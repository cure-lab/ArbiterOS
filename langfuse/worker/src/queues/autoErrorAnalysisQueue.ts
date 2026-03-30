import { randomUUID } from "crypto";
import { Job, Processor } from "bullmq";
import { z } from "zod/v4";
import { z as zodV3 } from "zod/v3";
import { LLMAdapter, type ChatMessage } from "@langfuse/shared";
import { prisma } from "@langfuse/shared/src/db";
import {
  ChatMessageRole,
  ChatMessageType,
  LLMApiKeySchema,
} from "@langfuse/shared";
import {
  fetchLLMCompletion,
  getObservationsForTrace,
  getQueue,
  logger,
  QueueJobs,
  QueueName,
  setEventErrorTypeTag,
  type TQueueJobTypes,
} from "@langfuse/shared/src/server";
import { env } from "../env";

const AutoErrorAnalysisModelSchema = z.enum(["gpt-5.2", "gpt-4.1"]);
type AutoErrorAnalysisModel = z.infer<typeof AutoErrorAnalysisModelSchema>;
const AutoErrorAnalysisSettingsSchema = z.object({
  enabled: z.boolean(),
  model: AutoErrorAnalysisModelSchema,
  minNewErrorNodesForSummary: z.number().int().min(1).nullable().default(null),
  summaryAppendMarkdownAbsolutePath: z
    .string()
    .trim()
    .min(1)
    .nullable()
    .default(null),
});

const DEFAULT_AUTO_ERROR_ANALYSIS_SETTINGS = {
  enabled: false,
  model: "gpt-5.2" as AutoErrorAnalysisModel,
  minNewErrorNodesForSummary: null as number | null,
  summaryAppendMarkdownAbsolutePath: null as string | null,
};

const DEFAULT_AUTO_EXPERIENCE_SUMMARY_MIN_NEW_ANALYSES = 1;
const SUMMARY_JOB_ACTIVE_STATES = new Set([
  "waiting",
  "active",
  "delayed",
  "prioritized",
  "waiting-children",
  "paused",
]);
const SUMMARY_JOB_SETTLE_TIMEOUT_MS = 90_000;
const SUMMARY_JOB_SETTLE_POLL_MS = 250;
const SUMMARY_SYNC_MAX_ATTEMPTS = 3;

const AutoErrorAnalysisResultSchema = z.object({
  rootCause: z.string(),
  resolveNow: z.array(z.string()),
  preventionNextCall: z.array(z.string()),
  relevantObservations: z.array(z.string()),
  contextSufficient: z.boolean(),
  confidence: z.number().min(0).max(1),
});

const AutoErrorAnalysisStructuredOutputSchema = zodV3.object({
  rootCause: zodV3.string(),
  resolveNow: zodV3.array(zodV3.string()),
  preventionNextCall: zodV3.array(zodV3.string()),
  relevantObservations: zodV3.array(zodV3.string()),
  contextSufficient: zodV3.boolean(),
  confidence: zodV3.number().min(0).max(1),
});

const ERROR_TYPE_KEYS = [
  "schema_mismatch",
  "tool_args_schema_error",
  "tool_execution_error",
  "json_parse_error",
  "context_length_exceeded",
  "rate_limit",
  "auth_error",
  "model_not_found",
  "timeout",
  "network_error",
  "provider_5xx",
  "unknown",
] as const;

type ErrorTypeKey = (typeof ERROR_TYPE_KEYS)[number];

const ERROR_TYPE_CATALOG: Record<ErrorTypeKey, { description: string }> = {
  schema_mismatch: {
    description:
      "The model output failed a required schema/format validation (e.g., JSON schema, structured output).",
  },
  tool_args_schema_error: {
    description:
      "A tool call was produced, but the tool arguments failed schema validation or parsing.",
  },
  tool_execution_error: {
    description:
      "A tool was called, but the tool execution failed (runtime error, exception, bad response).",
  },
  json_parse_error: {
    description: "A JSON parse/serialization error occurred in the pipeline.",
  },
  context_length_exceeded: {
    description:
      "The request exceeded context/token limits (prompt too long / too many tokens).",
  },
  rate_limit: {
    description: "The provider returned rate limiting / throttling (HTTP 429).",
  },
  auth_error: {
    description:
      "Authentication/authorization error talking to the provider or downstream service (HTTP 401/403).",
  },
  model_not_found: {
    description:
      "The requested model was not found / not available on the configured endpoint (HTTP 404).",
  },
  timeout: {
    description: "The request timed out or exceeded the configured timeout.",
  },
  network_error: {
    description:
      "A network/connection error occurred (DNS, TLS, connection reset, proxy issues).",
  },
  provider_5xx: {
    description: "The provider returned a server-side error (HTTP 5xx).",
  },
  unknown: {
    description: "Could not confidently map this issue to a known category.",
  },
};

const ERROR_TYPE_CHOICES = [...ERROR_TYPE_KEYS, "OTHER"] as const;

const ErrorTypeStructuredOutputSchema = zodV3.object({
  selectedType: zodV3.enum(ERROR_TYPE_CHOICES),
  otherTypeLabel: zodV3.string().optional(),
  otherTypeDescription: zodV3.string().optional(),
  why: zodV3.string(),
  confidence: zodV3.number().min(0).max(1),
});

const ErrorTypeClassificationResultSchema = z.object({
  selectedType: z.enum(ERROR_TYPE_CHOICES),
  otherTypeLabel: z.string().nullish(),
  otherTypeDescription: z.string().nullish(),
  why: z.string(),
  confidence: z.number().min(0).max(1),
});

function resolveModel(model: AutoErrorAnalysisModel): string {
  return model === "gpt-5.2" ? "gpt-5.2-2025-12-11" : "gpt-4.1";
}

function safeStringify(value: unknown): string {
  try {
    return typeof value === "string" ? value : JSON.stringify(value);
  } catch {
    return "[Unserializable value]";
  }
}

function truncateString(value: string, maxChars: number): string {
  if (value.length <= maxChars) return value;
  return value.slice(0, Math.max(0, maxChars - 24)) + "...[truncated]";
}

function slugifyErrorTypeKey(input: string): string {
  const lowered = input.trim().toLowerCase();
  const replaced = lowered.replace(/[^a-z0-9]+/g, "_");
  const collapsed = replaced.replace(/_+/g, "_").replace(/^_+|_+$/g, "");
  const safe = collapsed.length > 0 ? collapsed : "other";
  return safe.slice(0, 48);
}

function inferErrorTypeKeyFromText(text: string): ErrorTypeKey {
  const s = text.toLowerCase();

  // File/path I/O failures are usually tool execution failures (not "model_not_found").
  if (
    s.includes("file not found") ||
    s.includes("no such file or directory") ||
    s.includes("enoent") ||
    s.includes("permission denied") ||
    s.includes("eacces") ||
    s.includes("is a directory") ||
    s.includes("enotdir")
  ) {
    return "tool_execution_error";
  }

  if (
    s.includes("maximum context") ||
    s.includes("context length") ||
    s.includes("context_length") ||
    s.includes("too many tokens") ||
    s.includes("token limit") ||
    s.includes("prompt is too long") ||
    s.includes("request too large") ||
    s.includes("payload too large") ||
    s.includes("413")
  ) {
    return "context_length_exceeded";
  }

  if (
    s.includes("too many requests") ||
    s.includes("rate limit") ||
    s.includes("ratelimit") ||
    s.includes("throttl") ||
    s.includes("429")
  ) {
    return "rate_limit";
  }

  if (
    s.includes("unauthorized") ||
    s.includes("forbidden") ||
    s.includes("invalid api key") ||
    s.includes("authentication") ||
    s.includes("401") ||
    s.includes("403")
  ) {
    return "auth_error";
  }

  // Avoid mapping generic "not found" (e.g. "File not found") to model_not_found.
  // Only classify model_not_found when the missing entity is explicitly a model/deployment.
  const hasModelContext =
    s.includes("model") ||
    s.includes("deployment") ||
    s.includes("engine") ||
    s.includes("endpoint");
  if (
    s.includes("model not found") ||
    s.includes("unknown model") ||
    (hasModelContext &&
      (s.includes("does not exist") ||
        s.includes("not found") ||
        s.includes("404")))
  ) {
    return "model_not_found";
  }

  if (
    s.includes("timed out") ||
    s.includes("timeout") ||
    s.includes("etimedout")
  ) {
    return "timeout";
  }

  if (
    s.includes("econnreset") ||
    s.includes("enotfound") ||
    s.includes("eai_again") ||
    s.includes("socket hang up") ||
    s.includes("tls") ||
    s.includes("ssl") ||
    s.includes("networkerror") ||
    s.includes("fetch failed") ||
    s.includes("connection")
  ) {
    return "network_error";
  }

  if (
    s.includes("internal server error") ||
    s.includes("bad gateway") ||
    s.includes("service unavailable") ||
    s.includes("gateway timeout") ||
    s.includes("500") ||
    s.includes("502") ||
    s.includes("503") ||
    s.includes("504")
  ) {
    return "provider_5xx";
  }

  if (
    s.includes("unexpected token") ||
    s.includes("json parse") ||
    s.includes("json.parse") ||
    s.includes("unterminated string") ||
    s.includes("is not valid json")
  ) {
    return "json_parse_error";
  }

  if (
    s.includes("schema") ||
    s.includes("zod") ||
    s.includes("validation") ||
    s.includes("structured output") ||
    s.includes("invalid schema") ||
    s.includes("does not match the expected") ||
    s.includes("not matching the expected schema")
  ) {
    return "schema_mismatch";
  }

  if (s.includes("tool") && (s.includes("argument") || s.includes("schema"))) {
    return "tool_args_schema_error";
  }

  if (s.includes("tool") && (s.includes("failed") || s.includes("error"))) {
    return "tool_execution_error";
  }

  return "unknown";
}

function inferErrorTypeKeyFromObservation(params: {
  statusMessage: string | null | undefined;
  input: unknown;
  output: unknown;
  metadata: unknown;
  traceMetadata?: unknown;
}): ErrorTypeKey {
  const combined = [
    params.statusMessage ?? "",
    safeStringify(params.input),
    safeStringify(params.output),
    safeStringify(params.metadata),
    safeStringify(params.traceMetadata),
  ]
    .filter((v) => typeof v === "string" && v.trim().length > 0)
    .join("\n");

  if (!combined.trim()) return "unknown";
  return inferErrorTypeKeyFromText(combined);
}

function extractFirstJsonObject(input: string): string | null {
  const s = input.trim();
  let start = -1;
  let depth = 0;
  let inString = false;
  let escaped = false;

  for (let i = 0; i < s.length; i++) {
    const ch = s[i]!;
    if (inString) {
      if (escaped) {
        escaped = false;
        continue;
      }
      if (ch === "\\") {
        escaped = true;
        continue;
      }
      if (ch === '"') {
        inString = false;
      }
      continue;
    }

    if (ch === '"') {
      inString = true;
      continue;
    }

    if (ch === "{") {
      if (depth === 0) start = i;
      depth++;
      continue;
    }

    if (ch === "}") {
      if (depth === 0) continue;
      depth--;
      if (depth === 0 && start !== -1) {
        return s.slice(start, i + 1);
      }
    }
  }

  return null;
}

function parseJsonObjectFromCompletion(completion: string): unknown {
  const trimmed = completion.trim();
  const objectMatch = extractFirstJsonObject(trimmed);

  const candidates = [trimmed, objectMatch].filter((c): c is string =>
    Boolean(c),
  );

  for (const candidate of candidates) {
    try {
      return JSON.parse(candidate);
    } catch {
      // try next candidate
    }
  }

  throw new Error("Could not parse JSON object from LLM response.");
}

function coerceToString(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    for (const key of [
      "text",
      "content",
      "summary",
      "message",
      "reason",
      "rootCause",
      "root_cause",
    ]) {
      const v = obj[key];
      if (typeof v === "string" && v.trim().length > 0) return v;
    }
    try {
      return JSON.stringify(value);
    } catch {
      return "[Unserializable value]";
    }
  }
  return String(value);
}

function coerceToStringArray(value: unknown): string[] {
  if (value == null) return [];
  if (Array.isArray(value)) {
    return value
      .map(coerceToString)
      .map((s) => s.trim())
      .filter(Boolean);
  }
  if (typeof value === "string") {
    const s = value.trim();
    return s ? [s] : [];
  }
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    for (const key of [
      "items",
      "steps",
      "actions",
      "suggestions",
      "resolveNow",
      "resolve_now",
      "resolutionNow",
      "resolution_now",
      "preventionNextCall",
      "prevention_next_call",
    ]) {
      const v = obj[key];
      if (Array.isArray(v)) return coerceToStringArray(v);
      if (typeof v === "string") return coerceToStringArray(v);
    }
    return Object.values(obj).flatMap((v) => coerceToStringArray(v));
  }
  return [coerceToString(value)].map((s) => s.trim()).filter(Boolean);
}

function coerceConfidence(value: unknown): number {
  const n =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number.parseFloat(value)
        : typeof value === "object" && value && "value" in (value as any)
          ? Number.parseFloat(String((value as any).value))
          : Number.NaN;
  const safe = Number.isFinite(n) ? n : 0.5;
  return Math.max(0, Math.min(1, safe));
}

function coerceBoolean(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const s = value.trim().toLowerCase();
    if (["true", "yes", "y", "1"].includes(s)) return true;
    if (["false", "no", "n", "0"].includes(s)) return false;
  }
  return true;
}

function normalizeAndCoerceResult(raw: unknown): unknown {
  if (!raw || typeof raw !== "object") return raw;
  const obj = raw as Record<string, unknown>;
  const normalized = {
    ...obj,
    rootCause: obj.rootCause ?? obj.root_cause ?? obj["root_cause"],
    resolveNow:
      obj.resolveNow ??
      obj.resolve_now ??
      obj["resolve_now"] ??
      obj.resolutionNow ??
      obj.resolution_now ??
      obj["resolution_now"],
    preventionNextCall:
      obj.preventionNextCall ??
      obj.prevention_next_call ??
      obj["prevention_next_call"],
    relevantObservations:
      obj.relevantObservations ??
      obj.relevant_observations ??
      obj["relevant_observations"],
    contextSufficient:
      obj.contextSufficient ??
      obj.context_sufficient ??
      obj["context_sufficient"] ??
      obj.contextEnough ??
      obj.context_enough ??
      obj["context_enough"],
    confidence: obj.confidence ?? obj.confidenceScore ?? obj.confidence_score,
  } as Record<string, unknown>;

  return {
    ...normalized,
    rootCause: coerceToString(normalized.rootCause),
    resolveNow: coerceToStringArray(normalized.resolveNow),
    preventionNextCall: coerceToStringArray(normalized.preventionNextCall),
    relevantObservations: coerceToStringArray(normalized.relevantObservations),
    contextSufficient: coerceBoolean(normalized.contextSufficient),
    confidence: coerceConfidence(normalized.confidence),
  };
}

function normalizeAndCoerceTypeClassificationResult(raw: unknown): unknown {
  if (raw == null) return raw;
  let parsed: unknown = raw;
  if (typeof parsed === "string") {
    try {
      parsed = parseJsonObjectFromCompletion(parsed);
    } catch {
      // keep original string
    }
  }
  if (!parsed || typeof parsed !== "object") return parsed;
  const obj = parsed as Record<string, unknown>;

  const selectedType =
    obj.selectedType ??
    obj.selected_type ??
    obj.selected ??
    obj.type ??
    obj.errorType ??
    obj.error_type;

  const normalized: Record<string, unknown> = {
    ...obj,
    selectedType,
    otherTypeLabel:
      obj.otherTypeLabel ?? obj.other_type_label ?? obj.otherLabel,
    otherTypeDescription:
      obj.otherTypeDescription ??
      obj.other_type_description ??
      obj.otherDescription,
    why: obj.why ?? obj.reason ?? obj.rationale ?? obj.explanation,
    confidence: obj.confidence ?? obj.confidenceScore ?? obj.confidence_score,
  };

  return {
    ...normalized,
    selectedType: coerceToString(normalized.selectedType).trim(),
    otherTypeLabel: coerceToString(normalized.otherTypeLabel).trim() || null,
    otherTypeDescription:
      coerceToString(normalized.otherTypeDescription).trim() || null,
    why: coerceToString(normalized.why),
    confidence: coerceConfidence(normalized.confidence),
  };
}

function buildIssueLabel(params: {
  observationName: string;
  level: string;
  status?: string | null;
}) {
  const parts = [params.observationName, params.level];
  if (params.status) parts.push(params.status);
  return parts.join(" | ");
}

function buildNextNodeInputHint(params: {
  ordered: Awaited<ReturnType<typeof getObservationsForTrace>>;
  focusIndex: number;
}): string | null {
  const nextNodes = params.ordered.slice(
    params.focusIndex + 1,
    params.focusIndex + 4,
  );
  if (nextNodes.length === 0) return null;
  const snippets = nextNodes
    .map((node) => {
      const name = node.name ?? node.id;
      const level = node.level ?? "UNKNOWN";
      const status = node.statusMessage ?? "";
      const input = node.input
        ? truncateString(safeStringify(node.input), 1_500)
        : "";
      return [name, level, status, input].filter(Boolean).join("\n");
    })
    .filter((value) => value.trim().length > 0);
  if (snippets.length === 0) return null;
  return truncateString(snippets.join("\n\n"), 2_500);
}

function parseAutoErrorAnalysisSettings(metadata: unknown): {
  enabled: boolean;
  model: AutoErrorAnalysisModel;
  minNewErrorNodesForSummary: number | null;
  summaryAppendMarkdownAbsolutePath: string | null;
} {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return DEFAULT_AUTO_ERROR_ANALYSIS_SETTINGS;
  }

  const maybeSettings = (metadata as Record<string, unknown>).autoErrorAnalysis;
  const parsed = AutoErrorAnalysisSettingsSchema.safeParse(maybeSettings);
  if (!parsed.success) return DEFAULT_AUTO_ERROR_ANALYSIS_SETTINGS;
  return parsed.data;
}

function isSummaryJobInFlight(state: string): boolean {
  return SUMMARY_JOB_ACTIVE_STATES.has(state);
}

async function countPendingSummaryAnalyses(projectId: string): Promise<number> {
  const existingSummary = await prisma.experienceSummary.findUnique({
    where: { projectId },
    select: { cursorUpdatedAt: true },
  });
  const cursor = existingSummary?.cursorUpdatedAt ?? null;

  return prisma.errorAnalysis.count({
    where: {
      projectId,
      ...(cursor ? { updatedAt: { gt: cursor } } : {}),
    },
  });
}

async function waitForSummaryJobToSettle(params: {
  summaryQueue: {
    getJob: (jobId: string) => Promise<Job | undefined>;
  };
  summaryJobId: string;
  timeoutMs?: number;
}) {
  const timeoutMs = params.timeoutMs ?? SUMMARY_JOB_SETTLE_TIMEOUT_MS;
  const startedAt = Date.now();

  while (Date.now() - startedAt < timeoutMs) {
    const activeJob = await params.summaryQueue.getJob(params.summaryJobId);
    if (!activeJob) {
      return;
    }
    const state = await activeJob.getState();
    if (!isSummaryJobInFlight(state)) {
      return;
    }
    await new Promise<void>((resolve) =>
      setTimeout(resolve, SUMMARY_JOB_SETTLE_POLL_MS),
    );
  }
}

export const autoErrorAnalysisQueueProcessor: Processor = async (
  job: Job<TQueueJobTypes[QueueName.AutoErrorAnalysisQueue]>,
) => {
  const { projectId, traceId, observationId } = job.data.payload;
  const project = await prisma.project.findUnique({
    where: {
      id: projectId,
    },
    select: {
      metadata: true,
    },
  });
  const settings = parseAutoErrorAnalysisSettings(project?.metadata);
  if (!settings.enabled) {
    logger.debug("Skipping auto error analysis: project setting disabled", {
      projectId,
      traceId,
      observationId,
    });
    return;
  }

  const trace = await prisma.legacyPrismaTrace.findFirst({
    where: {
      id: traceId,
      projectId,
    },
    select: {
      metadata: true,
    },
  });

  const model = AutoErrorAnalysisModelSchema.catch("gpt-5.2").parse(
    job.data.payload.model ?? settings.model,
  );

  const llmApiKey = await prisma.llmApiKeys.findFirst({
    where: { projectId, adapter: LLMAdapter.OpenAI },
  });
  if (!llmApiKey) {
    logger.warn("Skipping auto error analysis: missing OpenAI LLM connection", {
      projectId,
      traceId,
      observationId,
    });
    return;
  }

  const parsedKey = LLMApiKeySchema.safeParse(llmApiKey);
  if (!parsedKey.success) {
    logger.warn("Skipping auto error analysis: invalid LLM connection", {
      projectId,
      traceId,
      observationId,
      error: parsedKey.error.message,
    });
    return;
  }

  const observations = await getObservationsForTrace({
    projectId,
    traceId,
    includeIO: true,
  });
  if (!observations.length) {
    // ClickHouse writes are asynchronous; the observation may not be queryable yet.
    // Throw to trigger BullMQ retry/backoff instead of completing without saving.
    throw new Error(
      `Auto error analysis: no observations found for trace yet (traceId=${traceId}). Retrying.`,
    );
  }

  const ordered = [...observations].sort(
    (a, b) => a.startTime.getTime() - b.startTime.getTime(),
  );
  const focusIndex = ordered.findIndex((o) => o.id === observationId);
  if (focusIndex === -1) {
    // Observation might not be visible in ClickHouse yet.
    throw new Error(
      `Auto error analysis: focus observation not found in trace context yet (observationId=${observationId}). Retrying.`,
    );
  }

  const current = ordered[focusIndex]!;
  if (current.level !== "ERROR" && current.level !== "WARNING") {
    // Should not happen (job is only enqueued for ERROR/WARNING), but avoid retries.
    logger.debug(
      "Skipping auto error analysis: observation level not eligible",
      {
        projectId,
        traceId,
        observationId,
        level: current.level,
      },
    );
    return;
  }

  const before = ordered.slice(Math.max(0, focusIndex - 8), focusIndex);
  const after = ordered.slice(focusIndex + 1, focusIndex + 5);
  const issue = buildIssueLabel({
    observationName: current.name ?? current.id,
    level: current.level,
    status: current.statusMessage,
  });

  const userPayload = {
    issue,
    traceMetadata:
      trace?.metadata == null
        ? null
        : truncateString(safeStringify(trace.metadata), 5000),
    currentObservation: {
      id: current.id,
      name: current.name,
      type: current.type,
      level: current.level,
      statusMessage: current.statusMessage,
      input: current.input
        ? truncateString(safeStringify(current.input), 5000)
        : null,
      output: current.output
        ? truncateString(safeStringify(current.output), 5000)
        : null,
      metadata: truncateString(safeStringify(current.metadata), 2500),
    },
    contextWindow: {
      before: before.map((o) => ({
        id: o.id,
        name: o.name,
        type: o.type,
        level: o.level,
        statusMessage: o.statusMessage,
      })),
      after: after.map((o) => ({
        id: o.id,
        name: o.name,
        type: o.type,
        level: o.level,
        statusMessage: o.statusMessage,
      })),
    },
    instruction:
      'Analyze the ERROR/WARNING and return ONLY JSON matching the schema (rootCause, resolveNow, preventionNextCall, relevantObservations, contextSufficient, confidence). Keep it concise: rootCause max 2 sentences; resolveNow max 3 items; preventionNextCall max 5 items. Treat policy constraints as required safety/privacy/compliance protections.\n\nPay close attention to metadata on both the trace and the observation. Explicit exception/error class names in metadata or status text (for example AttributeError, TypeError, KeyError, ValueError) are strong evidence for the root cause and should be reflected in the explanation.\n\nWhen the failure is access/blocked/forbidden/rate-limit related (e.g., HTTP 401/403/429 or similar), explicitly name what was blocked and by what: include the domain/URL/host (or best identifier available) and the tool/provider/adapter if present in issue/statusMessage/input/output/metadata. Do not invent missing identifiers; if not present, write "unknown".\n\nIn resolveNow and preventionNextCall, include only prompt-level actions that can be applied in the next LLM call (edits to system/developer/user prompt text, format constraints, tool-use instructions, or context selection). Avoid generic advice that omits identifiers when identifiers are available. Exclude implementation-heavy or non-prompt actions (code/config changes, retries/backoff/circuit-breaker logic, scheduler or long-running behavior changes, model/provider/account changes). Never suggest bypassing, weakening, or evading policy controls. If no valid prompt-only action exists, return an empty list for that field.',
  };

  const messages: ChatMessage[] = [
    {
      type: ChatMessageType.System,
      role: ChatMessageRole.System,
      content:
        "You are an expert at analyzing LLM pipeline ERROR/WARNING events. Policy gates are intentional safeguards for security, privacy, and compliance. Keep output concise and prevention-oriented.\n\nUse metadata from both the trace and the observation as first-class evidence. If the payload includes an explicit exception/error class name (for example AttributeError, TypeError, KeyError, ValueError), reflect it in the root cause rather than ignoring it.\n\nIf the error indicates blocked/forbidden/unauthorized/rate-limited access, explicitly identify (from the provided context) what was blocked (domain/URL/host) and which tool/provider/adapter was involved; do not fabricate identifiers.\n\nRecommend only prompt-level actions that are directly applicable in the next LLM call; reject implementation-heavy proposals such as retries/backoff/circuit breakers, system settings, infrastructure/config changes, or persistent behavior changes. Do not provide workaround or bypass suggestions. Return ONLY JSON matching the schema.",
    },
    {
      type: ChatMessageType.User,
      role: ChatMessageRole.User,
      content: safeStringify(userPayload),
    },
  ];

  const modelName =
    parsedKey.data.baseURL &&
    !parsedKey.data.baseURL.includes("api.openai.com") &&
    model === "gpt-5.2"
      ? "gpt-5.2"
      : resolveModel(model);

  let raw: unknown;
  try {
    raw = await fetchLLMCompletion({
      llmConnection: parsedKey.data,
      messages,
      modelParams: {
        provider: parsedKey.data.provider,
        adapter: LLMAdapter.OpenAI,
        model: modelName,
        temperature: 0.2,
        max_tokens: 800,
      },
      streaming: false,
      structuredOutputSchema: AutoErrorAnalysisStructuredOutputSchema,
    });
  } catch (error) {
    logger.warn(
      "Auto error analysis structured output failed, retrying plain completion",
      {
        projectId,
        traceId,
        observationId,
        error: error instanceof Error ? error.message : String(error),
      },
    );
    try {
      const fallbackCompletion = await fetchLLMCompletion({
        llmConnection: parsedKey.data,
        messages,
        modelParams: {
          provider: parsedKey.data.provider,
          adapter: LLMAdapter.OpenAI,
          model: modelName,
          temperature: 0.2,
          max_tokens: 800,
        },
        streaming: false,
      });
      raw =
        typeof fallbackCompletion === "string"
          ? parseJsonObjectFromCompletion(fallbackCompletion)
          : fallbackCompletion;
    } catch (fallbackError) {
      const msg =
        fallbackError instanceof Error
          ? fallbackError.message
          : String(fallbackError);
      logger.warn("Auto error analysis failed", {
        projectId,
        traceId,
        observationId,
        error: msg,
      });
      // Throw to trigger retries/backoff instead of completing silently.
      throw new Error(`Auto error analysis failed: ${msg}`);
    }
  }

  const validated = AutoErrorAnalysisResultSchema.safeParse(
    normalizeAndCoerceResult(raw),
  );
  if (!validated.success) {
    logger.warn("Auto error analysis returned invalid payload", {
      projectId,
      traceId,
      observationId,
      error: validated.error.message,
    });
    // Invalid payloads are often transient with provider quirks; retry a few times.
    throw new Error(
      `Auto error analysis returned invalid payload: ${validated.error.message}`,
    );
  }

  // Best-effort: classify error/warning type after analysis is available.
  let classificationFields: {
    errorType: string;
    errorTypeDescription: string | null;
    errorTypeWhy: string | null;
    errorTypeConfidence: number | null;
    errorTypeFromList: boolean | null;
  } | null = null;

  try {
    const typeCatalog = Object.fromEntries(
      Object.entries(ERROR_TYPE_CATALOG).map(([k, v]) => [k, v.description]),
    );

    const observationPreview = {
      id: current.id,
      type: current.type,
      name: current.name,
      level: current.level,
      statusMessage: current.statusMessage,
      input:
        current.input == null
          ? null
          : truncateString(safeStringify(current.input), 2_000),
      output:
        current.output == null
          ? null
          : truncateString(safeStringify(current.output), 2_000),
      metadata:
        current.metadata == null
          ? null
          : truncateString(safeStringify(current.metadata), 2_000),
    };

    const messagesForType: ChatMessage[] = [
      {
        type: ChatMessageType.System,
        role: ChatMessageRole.System,
        content:
          "You are an expert at classifying error/warning types in LLM traces. Return ONLY the structured JSON object that matches the provided schema.\n\nGuidance:\n- If the observation is a TOOL (or the name indicates a tool call) and it failed with file/path I/O errors (e.g. file not found, permission denied), classify as tool_execution_error.\n- Only use model_not_found when the missing thing is explicitly a model/deployment (e.g. provider message about an unavailable model, or HTTP 404 for a model/deployment endpoint). Do NOT treat generic 'not found' as model_not_found.\n- If metadata or status text contains an explicit exception/error class name (for example AttributeError, TypeError, KeyError, ValueError) and no catalog entry fits well, prefer selectedType=OTHER and use that exception class as otherTypeLabel.",
      },
      {
        type: ChatMessageType.User,
        role: ChatMessageRole.User,
        content: safeStringify({
          issue,
          rootCause: validated.data.rootCause,
          observation: observationPreview,
          traceMetadata:
            trace?.metadata == null
              ? null
              : truncateString(safeStringify(trace.metadata), 4_000),
          typeCatalog,
          instruction:
            "Classify the error/warning type. Choose from the catalog. If none match well, set selectedType=OTHER and propose a short label + description. If the payload includes an explicit exception/error class name (for example AttributeError, TypeError, KeyError, ValueError) and no catalog entry fits well, prefer selectedType=OTHER and use that exception class as otherTypeLabel with a concise description. Do not fall back to unknown when a concrete exception class is available. Be careful with 'not found': file/path not found -> tool_execution_error; only model/deployment not found -> model_not_found.",
        }),
      },
    ];

    let rawType: unknown;
    try {
      rawType = await fetchLLMCompletion({
        llmConnection: parsedKey.data,
        messages: messagesForType,
        modelParams: {
          provider: parsedKey.data.provider,
          adapter: LLMAdapter.OpenAI,
          model: modelName,
          temperature: 0,
          max_tokens: 250,
        },
        streaming: false,
        structuredOutputSchema: ErrorTypeStructuredOutputSchema,
      });
    } catch (error) {
      logger.warn(
        "Auto error analysis type classification structured output failed, retrying plain completion",
        {
          projectId,
          traceId,
          observationId,
          error: error instanceof Error ? error.message : String(error),
        },
      );
      const fallbackCompletion = await fetchLLMCompletion({
        llmConnection: parsedKey.data,
        messages: messagesForType,
        modelParams: {
          provider: parsedKey.data.provider,
          adapter: LLMAdapter.OpenAI,
          model: modelName,
          temperature: 0,
          max_tokens: 250,
        },
        streaming: false,
      });
      rawType =
        typeof fallbackCompletion === "string"
          ? parseJsonObjectFromCompletion(fallbackCompletion)
          : fallbackCompletion;
    }

    // LLM-first: normalize/coerce common variants before giving up to heuristics.
    let parsedType = ErrorTypeClassificationResultSchema.safeParse(
      normalizeAndCoerceTypeClassificationResult(rawType),
    );
    if (!parsedType.success) {
      // One more LLM attempt to correct shape if the model returned invalid keys/format.
      const repairMessages: ChatMessage[] = [
        {
          type: ChatMessageType.System,
          role: ChatMessageRole.System,
          content:
            "You will be given an object that is intended to match a JSON schema for error type classification but may be invalid. Rewrite it to EXACTLY match the schema keys and allowed values. Return ONLY JSON.",
        },
        {
          type: ChatMessageType.User,
          role: ChatMessageRole.User,
          content: safeStringify({
            original: rawType,
            schema: {
              selectedType: ERROR_TYPE_CHOICES,
              otherTypeLabel:
                "string (required only when selectedType=OTHER, else omit or null)",
              otherTypeDescription:
                "string (required only when selectedType=OTHER, else omit or null)",
              why: "string",
              confidence: "number 0..1",
            },
          }),
        },
      ];
      try {
        const repairCompletion = await fetchLLMCompletion({
          llmConnection: parsedKey.data,
          messages: repairMessages,
          modelParams: {
            provider: parsedKey.data.provider,
            adapter: LLMAdapter.OpenAI,
            model: modelName,
            temperature: 0,
            max_tokens: 220,
          },
          streaming: false,
        });
        const repaired =
          typeof repairCompletion === "string"
            ? parseJsonObjectFromCompletion(repairCompletion)
            : repairCompletion;
        parsedType = ErrorTypeClassificationResultSchema.safeParse(
          normalizeAndCoerceTypeClassificationResult(repaired),
        );
      } catch (repairError) {
        logger.debug("Auto error analysis type classification repair failed", {
          projectId,
          traceId,
          observationId,
          error:
            repairError instanceof Error
              ? repairError.message
              : String(repairError),
        });
      }
    }
    if (parsedType.success) {
      const v = parsedType.data;
      const why = v.why ?? null;
      const conf = v.confidence ?? null;

      if (v.selectedType === "OTHER") {
        const label = (v.otherTypeLabel ?? "").trim();
        const desc = (v.otherTypeDescription ?? "").trim();
        classificationFields =
          label && desc
            ? {
                errorType: `other_${slugifyErrorTypeKey(label)}`,
                errorTypeDescription: desc,
                errorTypeWhy: why,
                errorTypeConfidence: conf,
                errorTypeFromList: false,
              }
            : {
                // If the model chose OTHER but didn't provide the required fields,
                // still persist a useful, filterable type.
                errorType: "unknown",
                errorTypeDescription: ERROR_TYPE_CATALOG.unknown.description,
                errorTypeWhy: why,
                errorTypeConfidence: conf,
                errorTypeFromList: true,
              };
      } else {
        classificationFields = {
          errorType: v.selectedType,
          errorTypeDescription:
            (ERROR_TYPE_CATALOG as any)[v.selectedType]?.description ?? null,
          errorTypeWhy: why,
          errorTypeConfidence: conf,
          errorTypeFromList: true,
        };
      }
    } else {
      logger.warn(
        "Auto error analysis type classification returned invalid payload",
        {
          projectId,
          traceId,
          observationId,
          error: parsedType.error.message,
        },
      );
    }
  } catch (e) {
    logger.warn("Auto error analysis type classification failed", {
      projectId,
      traceId,
      observationId,
      error: e instanceof Error ? e.message : String(e),
    });
  }

  if (!classificationFields) {
    const inferred = inferErrorTypeKeyFromObservation({
      statusMessage: current.statusMessage,
      input: current.input,
      output: current.output,
      metadata: current.metadata,
      traceMetadata: trace?.metadata,
    });
    classificationFields = {
      errorType: inferred,
      errorTypeDescription:
        ERROR_TYPE_CATALOG[inferred]?.description ??
        ERROR_TYPE_CATALOG.unknown.description,
      errorTypeWhy: null,
      errorTypeConfidence: null,
      errorTypeFromList: true,
    };
  }

  await prisma.errorAnalysis.upsert({
    where: {
      projectId_observationId: {
        projectId,
        observationId,
      },
    },
    create: {
      projectId,
      traceId,
      observationId,
      model: modelName,
      rootCause: validated.data.rootCause,
      resolveNow: validated.data.resolveNow,
      preventionNextCall: validated.data.preventionNextCall,
      relevantObservations: validated.data.relevantObservations,
      contextSufficient: validated.data.contextSufficient,
      confidence: validated.data.confidence,
      ...(classificationFields ?? {}),
    },
    update: {
      model: modelName,
      rootCause: validated.data.rootCause,
      resolveNow: validated.data.resolveNow,
      preventionNextCall: validated.data.preventionNextCall,
      relevantObservations: validated.data.relevantObservations,
      contextSufficient: validated.data.contextSufficient,
      confidence: validated.data.confidence,
      ...(classificationFields ?? {}),
    },
  });

  if (classificationFields?.errorType) {
    // Best-effort: sync into ClickHouse events tags for filtering.
    try {
      await setEventErrorTypeTag({
        projectId,
        spanId: observationId,
        errorTypeKey: classificationFields.errorType,
      });
    } catch (e) {
      logger.warn("Failed to sync error type tag for auto error analysis", {
        projectId,
        traceId,
        observationId,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  }

  const summaryQueue = getQueue(QueueName.AutoExperienceSummaryQueue);
  if (!summaryQueue) return;

  const summaryJobId = `auto-summary:${projectId}`;
  try {
    const minNewAnalysesForSummary =
      settings.minNewErrorNodesForSummary ??
      DEFAULT_AUTO_EXPERIENCE_SUMMARY_MIN_NEW_ANALYSES;

    let pendingCount = await countPendingSummaryAnalyses(projectId);
    if (pendingCount < minNewAnalysesForSummary) {
      logger.debug(
        "Skipping auto experience summary enqueue: insufficient new analyses",
        {
          projectId,
          pendingCount,
          minNewAnalyses: minNewAnalysesForSummary,
        },
      );
      return;
    }

    for (let attempt = 0; attempt < SUMMARY_SYNC_MAX_ATTEMPTS; attempt++) {
      const existingJob = await summaryQueue.getJob(summaryJobId);
      if (existingJob) {
        const state = await existingJob.getState();
        if (isSummaryJobInFlight(state)) {
          // If another summary run is in flight, wait for it and re-check pending analyses.
          await waitForSummaryJobToSettle({ summaryQueue, summaryJobId });
          pendingCount = await countPendingSummaryAnalyses(projectId);
          if (pendingCount < minNewAnalysesForSummary) {
            return;
          }
          continue;
        }

        // Completed/failed jobs keep their jobId in Redis; remove so we can re-add with same ID.
        try {
          await existingJob.remove();
        } catch (e) {
          logger.warn("Failed to remove existing auto experience summary job", {
            projectId,
            summaryJobId,
            state,
            error: e instanceof Error ? e.message : String(e),
          });
          await waitForSummaryJobToSettle({ summaryQueue, summaryJobId });
          pendingCount = await countPendingSummaryAnalyses(projectId);
          if (pendingCount < minNewAnalysesForSummary) {
            return;
          }
          continue;
        }
      }

      await summaryQueue.add(
        QueueJobs.AutoExperienceSummaryJob,
        {
          id: randomUUID(),
          timestamp: new Date(),
          name: QueueJobs.AutoExperienceSummaryJob,
          payload: {
            projectId,
            mode: "incremental",
            model,
            maxItems: env.LANGFUSE_AUTO_ANALYSIS_MAX_ITEMS_PER_SUMMARY,
            nextNodeInputHint: buildNextNodeInputHint({
              ordered,
              focusIndex,
            }),
          },
        },
        {
          jobId: summaryJobId,
        },
      );
      await waitForSummaryJobToSettle({ summaryQueue, summaryJobId });
      pendingCount = await countPendingSummaryAnalyses(projectId);
      if (pendingCount < minNewAnalysesForSummary) {
        return;
      }
    }

    logger.warn("Auto experience summary remains behind after sync attempts", {
      projectId,
      summaryJobId,
      pendingCount,
      minNewAnalyses: minNewAnalysesForSummary,
    });
  } catch (e) {
    logger.warn("Failed to enqueue auto experience summary job", {
      projectId,
      summaryJobId,
      error: e instanceof Error ? e.message : String(e),
    });
  }
};
