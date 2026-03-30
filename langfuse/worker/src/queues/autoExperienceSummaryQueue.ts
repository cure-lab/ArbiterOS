import { Job, Processor } from "bullmq";
import { z } from "zod/v4";
import { z as zodV3 } from "zod/v3";
import { mkdir, readFile, writeFile } from "fs/promises";
import { dirname, isAbsolute } from "path";
import {
  ChatMessageRole,
  ChatMessageType,
  LLMAdapter,
  LLMApiKeySchema,
  type ChatMessage,
} from "@langfuse/shared";
import { prisma } from "@langfuse/shared/src/db";
import {
  fetchLLMCompletion,
  logger,
  QueueName,
  type TQueueJobTypes,
} from "@langfuse/shared/src/server";
import {
  buildExperienceSummaryHintSectionContent,
  upsertProjectHintSectionInMarkdown,
} from "@langfuse/shared/src/utils/experienceSummaryHintMarkdown";

const ExperienceSummarySchemaVersion = 1 as const;
const AutoSummaryModelSchema = z.enum(["gpt-5.2", "gpt-4.1"]);
type AutoSummaryModel = z.infer<typeof AutoSummaryModelSchema>;

const DEFAULT_AUTO_EXPERIENCE_SUMMARY_MIN_NEW_ANALYSES = 1;
const AutoErrorAnalysisSummarySettingsSchema = z
  .object({
    enabled: z.boolean().default(false),
    minNewErrorNodesForSummary: z
      .number()
      .int()
      .min(1)
      .nullable()
      .default(null),
    summaryAppendMarkdownAbsolutePath: z
      .string()
      .trim()
      .min(1)
      .nullable()
      .default(null),
    summaryMarkdownOutputMode: z
      .enum(["prompt_pack_only", "full"])
      .default("prompt_pack_only"),
  })
  .passthrough();

const ExperienceSummaryJsonSchema = z
  .object({
    schemaVersion: z.literal(ExperienceSummarySchemaVersion),
    experiences: z
      .array(
        z
          .object({
            key: z.string().min(1).max(64),
            when: z.string().min(1).max(800),
            keywords: z.array(z.string().min(1).max(64)).max(20).nullish(),
            possibleProblems: z.array(z.string().min(1).max(400)).max(30),
            avoidanceAndNotes: z.array(z.string().min(1).max(500)).max(40),
            promptAdditions: z.array(z.string().min(1).max(300)).max(40),
            relatedErrorTypes: z
              .array(z.string().min(1).max(64))
              .max(20)
              .nullish(),
          })
          .strict(),
      )
      .max(100),
    promptPack: z
      .object({
        title: z.string().min(1).max(120),
        lines: z.array(z.string().min(1).max(400)).max(200),
      })
      .strict(),
  })
  .strict();

const ExperienceSummaryStructuredOutputSchema = zodV3
  .object({
    schemaVersion: zodV3.literal(ExperienceSummarySchemaVersion),
    experiences: zodV3.array(
      zodV3
        .object({
          key: zodV3.string(),
          when: zodV3.string(),
          keywords: zodV3.array(zodV3.string()).nullable(),
          possibleProblems: zodV3.array(zodV3.string()),
          avoidanceAndNotes: zodV3.array(zodV3.string()),
          promptAdditions: zodV3.array(zodV3.string()),
          relatedErrorTypes: zodV3.array(zodV3.string()).nullable(),
        })
        .strict(),
    ),
    promptPack: zodV3
      .object({
        title: zodV3.string(),
        lines: zodV3.array(zodV3.string()),
      })
      .strict(),
  })
  .strict();

function resolveModel(model: AutoSummaryModel): string {
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
  return value.slice(0, Math.max(0, maxChars - 30)) + "\n...[truncated]";
}

function tokenizeForNgram(value: string): Set<string> {
  const normalized = value.toLowerCase().replace(/[^a-z0-9_\s-]/g, " ");
  const words = normalized
    .split(/\s+/)
    .map((word) => word.trim())
    .filter((word) => word.length > 1);
  const tokens = new Set<string>();
  for (const word of words) tokens.add(word);
  for (let i = 0; i < words.length - 1; i++) {
    tokens.add(`${words[i]}_${words[i + 1]}`);
  }
  return tokens;
}

function lexicalSimilarity(params: {
  queryTokens: Set<string>;
  documentTokens: Set<string>;
}) {
  if (params.queryTokens.size === 0 || params.documentTokens.size === 0)
    return 0;
  let overlap = 0;
  for (const token of params.queryTokens) {
    if (params.documentTokens.has(token)) overlap++;
  }
  if (overlap === 0) return 0;
  return (
    overlap / Math.sqrt(params.queryTokens.size * params.documentTokens.size)
  );
}

function uniqueStableLines(lines: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of lines) {
    const normalized = line.trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

type ExperienceSummaryJson = z.infer<typeof ExperienceSummaryJsonSchema>;

function parseExperienceSummary(
  summary: unknown | null,
): ExperienceSummaryJson | null {
  if (!summary) return null;
  const parsed = ExperienceSummaryJsonSchema.safeParse(summary);
  return parsed.success ? parsed.data : null;
}

function buildExistingSummaryInfo(previous: ExperienceSummaryJson): {
  existingSummaryKeys: string[];
  existingPromptPack: { title: string; lines: string[] };
} {
  return {
    existingSummaryKeys: previous.experiences.map((e) => e.key),
    existingPromptPack: {
      title: previous.promptPack.title,
      // Keep the prompt small; we merge deterministically server-side.
      lines: previous.promptPack.lines.slice(0, 50),
    },
  };
}

function mergeExperienceSummaries(params: {
  previous: ExperienceSummaryJson;
  delta: ExperienceSummaryJson;
}): ExperienceSummaryJson {
  const deltaByKey = new Map(params.delta.experiences.map((e) => [e.key, e]));
  const previousOnly = params.previous.experiences.filter(
    (e) => !deltaByKey.has(e.key),
  );

  const mergedExperiences = [
    ...params.delta.experiences,
    ...previousOnly,
  ].slice(0, 100);

  const mergedPromptPackLines = uniqueStableLines([
    ...params.previous.promptPack.lines,
    ...params.delta.promptPack.lines,
    ...mergedExperiences.flatMap(
      (experience) => experience.promptAdditions ?? [],
    ),
  ]).slice(0, 200);

  return {
    schemaVersion: ExperienceSummarySchemaVersion,
    experiences: mergedExperiences,
    promptPack: {
      title: params.previous.promptPack.title || params.delta.promptPack.title,
      lines: mergedPromptPackLines,
    },
  };
}

function ensurePromptPackCoverage(
  summary: ExperienceSummaryJson,
): ExperienceSummaryJson {
  return {
    ...summary,
    promptPack: {
      title: summary.promptPack.title,
      lines: uniqueStableLines([
        ...summary.promptPack.lines,
        ...summary.experiences.flatMap(
          (experience) => experience.promptAdditions ?? [],
        ),
      ]).slice(0, 200),
    },
  };
}

type ErrorAnalysisCompact = {
  observationId: string;
  traceId: string;
  updatedAt: Date;
  errorType: string | null;
  errorTypeWhy: string | null;
  rootCause: string;
  resolveNow: string[];
  preventionNextCall: string[];
  relevantObservations: string[];
  contextSufficient: boolean;
  confidence: number;
};

const MAX_ANALYSIS_PAYLOAD_CHARS = 28_000;
const MAX_MARKDOWN_EXPERIENCES = 12;
const MAX_MARKDOWN_PROMPT_LINES = 80;

type SummarySettings = {
  minNewErrorNodesForSummary: number;
  summaryAppendMarkdownAbsolutePath: string | null;
  summaryMarkdownOutputMode: "prompt_pack_only" | "full";
};

function resolveSummarySettings(metadata: unknown): SummarySettings {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return {
      minNewErrorNodesForSummary:
        DEFAULT_AUTO_EXPERIENCE_SUMMARY_MIN_NEW_ANALYSES,
      summaryAppendMarkdownAbsolutePath: null,
      summaryMarkdownOutputMode: "prompt_pack_only",
    };
  }

  const parsed = AutoErrorAnalysisSummarySettingsSchema.safeParse(
    (metadata as Record<string, unknown>).autoErrorAnalysis,
  );
  if (!parsed.success) {
    return {
      minNewErrorNodesForSummary:
        DEFAULT_AUTO_EXPERIENCE_SUMMARY_MIN_NEW_ANALYSES,
      summaryAppendMarkdownAbsolutePath: null,
      summaryMarkdownOutputMode: "prompt_pack_only",
    };
  }

  const minNewErrorNodesForSummary =
    parsed.data.minNewErrorNodesForSummary ??
    DEFAULT_AUTO_EXPERIENCE_SUMMARY_MIN_NEW_ANALYSES;
  const enabled = parsed.data.enabled === true;
  const pathFromSettings = enabled
    ? parsed.data.summaryAppendMarkdownAbsolutePath
    : null;
  const validPath =
    pathFromSettings &&
    isAbsolute(pathFromSettings) &&
    pathFromSettings.toLowerCase().endsWith(".md")
      ? pathFromSettings
      : null;

  return {
    minNewErrorNodesForSummary,
    summaryAppendMarkdownAbsolutePath: validPath,
    summaryMarkdownOutputMode: parsed.data.summaryMarkdownOutputMode,
  };
}

async function replaceSummaryHintSectionInMarkdown(params: {
  projectId: string;
  absolutePath: string;
  summary: z.infer<typeof ExperienceSummaryJsonSchema>;
  outputMode: "prompt_pack_only" | "full";
}) {
  await mkdir(dirname(params.absolutePath), { recursive: true });

  let existingContent = "";
  try {
    existingContent = await readFile(params.absolutePath, "utf8");
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code !== "ENOENT") throw error;
  }

  const replacementContent = buildExperienceSummaryHintSectionContent({
    summary: params.summary,
    outputMode: params.outputMode,
    sectionHeadingHashCount: 4,
  });
  const updatedMarkdown = upsertProjectHintSectionInMarkdown({
    markdown: existingContent,
    projectId: params.projectId,
    replacementContent,
    defaultHeadingHashCount: 2,
  });
  await writeFile(
    params.absolutePath,
    `${updatedMarkdown.replace(/\s*$/, "")}\n`,
    "utf8",
  );
}

function compactAnalysisPayloadRows(
  rows: ErrorAnalysisCompact[],
): ErrorAnalysisCompact[] {
  let totalChars = 0;
  const out: ErrorAnalysisCompact[] = [];
  for (const row of rows) {
    const rowChars = JSON.stringify(row).length;
    if (out.length > 0 && totalChars + rowChars > MAX_ANALYSIS_PAYLOAD_CHARS) {
      break;
    }
    out.push(row);
    totalChars += rowChars;
  }
  return out;
}

function buildExperienceSearchText(
  experience: ExperienceSummaryJson["experiences"][number],
) {
  return [
    experience.key,
    experience.when,
    (experience.keywords ?? []).join(" "),
    (experience.relatedErrorTypes ?? []).join(" "),
    ...(experience.possibleProblems ?? []),
    ...(experience.avoidanceAndNotes ?? []),
    ...(experience.promptAdditions ?? []),
  ]
    .filter(Boolean)
    .join("\n");
}

function buildFallbackHintFromAnalyses(rows: ErrorAnalysisCompact[]): string {
  const latest = rows[rows.length - 1];
  if (!latest) return "";
  return [
    latest.errorType ?? "",
    latest.errorTypeWhy ?? "",
    latest.rootCause ?? "",
    ...(latest.preventionNextCall ?? []),
  ]
    .filter((value) => value && value.trim().length > 0)
    .join("\n");
}

async function selectSummaryForMarkdown(params: {
  projectId: string;
  summary: ExperienceSummaryJson;
  outputMode: "prompt_pack_only" | "full";
  nextNodeInputHint: string | null;
  analysesForFallback: ErrorAnalysisCompact[];
}): Promise<ExperienceSummaryJson> {
  const experiences = params.summary.experiences ?? [];
  if (experiences.length === 0) {
    return {
      ...params.summary,
      experiences: [],
      promptPack: {
        title: params.summary.promptPack.title,
        lines: uniqueStableLines(params.summary.promptPack.lines).slice(
          0,
          MAX_MARKDOWN_PROMPT_LINES,
        ),
      },
    };
  }

  const queryText = (
    params.nextNodeInputHint?.trim() ||
    buildFallbackHintFromAnalyses(params.analysesForFallback)
  ).trim();
  if (!queryText) {
    return {
      ...params.summary,
      experiences:
        params.outputMode === "full"
          ? experiences.slice(0, MAX_MARKDOWN_EXPERIENCES)
          : [],
      promptPack: {
        title: params.summary.promptPack.title,
        lines: uniqueStableLines(params.summary.promptPack.lines).slice(
          0,
          MAX_MARKDOWN_PROMPT_LINES,
        ),
      },
    };
  }

  const queryTokens = tokenizeForNgram(queryText);
  const rows = experiences.map((experience) => {
    const text = buildExperienceSearchText(experience);
    const lexicalScore = lexicalSimilarity({
      queryTokens,
      documentTokens: tokenizeForNgram(text),
    });
    return { experience, lexicalScore };
  });

  const ranked = [...rows].sort((a, b) => b.lexicalScore - a.lexicalScore);

  const selectedExperiences = ranked
    .slice(0, MAX_MARKDOWN_EXPERIENCES)
    .map((row) => row.experience);

  const selectedPromptAdditions = ranked
    .slice(0, MAX_MARKDOWN_EXPERIENCES)
    .flatMap((row) => row.experience.promptAdditions ?? []);

  const selectedPromptPackLines = uniqueStableLines([
    ...(params.summary.promptPack.lines ?? []),
    ...selectedPromptAdditions,
  ]).slice(0, MAX_MARKDOWN_PROMPT_LINES);

  return {
    ...params.summary,
    experiences: params.outputMode === "full" ? selectedExperiences : [],
    promptPack: {
      title: params.summary.promptPack.title,
      lines: selectedPromptPackLines,
    },
  };
}

function compactErrorAnalysisRow(row: any): ErrorAnalysisCompact {
  return {
    observationId: String(row.observationId),
    traceId: String(row.traceId),
    updatedAt: row.updatedAt as Date,
    errorType: (row.errorType ?? null) as string | null,
    errorTypeWhy: (row.errorTypeWhy ?? null) as string | null,
    rootCause: truncateString(String(row.rootCause ?? ""), 1200),
    resolveNow: Array.isArray(row.resolveNow)
      ? (row.resolveNow as string[])
          .slice(0, 8)
          .map((s) => truncateString(String(s), 260))
      : [],
    preventionNextCall: Array.isArray(row.preventionNextCall)
      ? (row.preventionNextCall as string[])
          .slice(0, 8)
          .map((s) => truncateString(String(s), 320))
      : [],
    relevantObservations: Array.isArray(row.relevantObservations)
      ? (row.relevantObservations as string[])
          .slice(0, 12)
          .map((s) => String(s))
      : [],
    contextSufficient: Boolean(row.contextSufficient ?? true),
    confidence: Number(row.confidence ?? 0.5),
  };
}

export const autoExperienceSummaryQueueProcessor: Processor = async (
  job: Job<TQueueJobTypes[QueueName.AutoExperienceSummaryQueue]>,
) => {
  const { projectId, nextNodeInputHint } = job.data.payload;
  const project = await prisma.project.findUnique({
    where: { id: projectId },
    select: { metadata: true },
  });
  const summarySettings = resolveSummarySettings(project?.metadata);

  const model = AutoSummaryModelSchema.catch("gpt-5.2").parse(
    job.data.payload.model ?? "gpt-5.2",
  );
  const requestedMaxItems = Math.max(
    1,
    Math.min(500, job.data.payload.maxItems ?? 50),
  );
  const maxItems = Math.max(
    summarySettings.minNewErrorNodesForSummary,
    requestedMaxItems,
  );

  const existing = await prisma.experienceSummary.findUnique({
    where: { projectId },
  });
  const cursor = existing?.cursorUpdatedAt ?? null;

  const newRows = await prisma.errorAnalysis.findMany({
    where: {
      projectId,
      ...(cursor ? { updatedAt: { gt: cursor } } : {}),
    },
    orderBy: { updatedAt: "asc" },
    take: maxItems,
  });
  if (newRows.length < summarySettings.minNewErrorNodesForSummary) return;

  const llmApiKey = await prisma.llmApiKeys.findFirst({
    where: { projectId, adapter: LLMAdapter.OpenAI },
  });
  if (!llmApiKey) {
    logger.warn(
      "Skipping auto experience summary: missing OpenAI LLM connection",
      {
        projectId,
      },
    );
    return;
  }

  const parsedKey = LLMApiKeySchema.safeParse(llmApiKey);
  if (!parsedKey.success) {
    logger.warn("Skipping auto experience summary: invalid LLM connection", {
      projectId,
      error: parsedKey.error.message,
    });
    return;
  }

  const previousSummary = existing?.summary ?? null;
  const newAnalyses = compactAnalysisPayloadRows(
    newRows.map(compactErrorAnalysisRow),
  );
  const previousParsed = parseExperienceSummary(previousSummary);

  const messages: ChatMessage[] = [
    {
      type: ChatMessageType.System,
      role: ChatMessageRole.System,
      content:
        "You are an expert at preventing recurring LLM pipeline errors. Keep output concise, practical, and focused on what can be changed in prompts for future LLM calls. Exclude implementation-heavy proposals (code/config/system changes, retries/backoff/circuit breakers, scheduler/long-running behavior changes, model/provider/account changes).\n\nWhen summarizing blocked/forbidden/unauthorized/rate-limit issues, include the specific identifiers available from newAnalyses (domain/URL/host and tool/provider/adapter) and do not invent missing details.\n\nReturn ONLY the structured JSON object that matches the provided schema.",
    },
    {
      type: ChatMessageType.User,
      role: ChatMessageRole.User,
      content: safeStringify({
        ...(previousParsed
          ? buildExistingSummaryInfo(previousParsed)
          : { previousSummary }),
        newAnalyses,
        instruction: [
          ...(previousParsed
            ? [
                "You are NOT given the full previous summary; only existingSummaryKeys and existingPromptPack are provided.",
                "Return a DELTA summary: include only new or updated experience items inferred from newAnalyses, and any new promptPack lines.",
                "It is OK to omit unchanged existing experiences; the server will merge your output with the stored summary.",
                "Use existingPromptPack.title as the promptPack.title.",
              ]
            : ["Merge with previousSummary when present."]),
          "Keep keys stable and snake_case; dedupe by key.",
          "Each experience item should be written as when -> possibleProblems -> avoidanceAndNotes -> promptAdditions.",
          "When possible, add concise `keywords` (2-8 tokens) for each experience item to help retrieval/ranking.",
          "Make entries concise and directly useful for preventing recurrence.",
          "promptAdditions must be copy-pasteable prompt lines for the next LLM call and must be generic/reusable.",
          "Ensure promptPack.lines reflects the highest-signal reusable guardrails implied by the included experiences, deduplicated and ordered by priority.",
          "Do not include hardcoded operational playbooks (e.g., fixed retry/backoff sequences, source-specific runbooks) unless the same concrete requirement is explicitly present in the analyzed failures.",
          "Do not propose actions that require code changes, infrastructure changes, or long-running behavior controls.",
          "Avoid generic blocked/forbidden advice when newAnalyses contains identifiable targets; include the domain/URL/host and tool/provider/adapter from newAnalyses when present, otherwise say unknown.",
        ].join("\n"),
      }),
    },
  ];

  const modelName =
    parsedKey.data.baseURL &&
    !parsedKey.data.baseURL.includes("api.openai.com") &&
    model === "gpt-5.2"
      ? "gpt-5.2"
      : resolveModel(model);

  const raw = await fetchLLMCompletion({
    llmConnection: parsedKey.data,
    messages,
    modelParams: {
      provider: parsedKey.data.provider,
      adapter: LLMAdapter.OpenAI,
      model: modelName,
      temperature: 0.2,
      max_tokens: 3000,
    },
    streaming: false,
    structuredOutputSchema: ExperienceSummaryStructuredOutputSchema,
  });

  const validated = ExperienceSummaryJsonSchema.safeParse(raw);
  if (!validated.success) {
    logger.warn("Auto experience summary returned invalid payload", {
      projectId,
      error: validated.error.message,
    });
    return;
  }

  const merged =
    previousParsed != null
      ? mergeExperienceSummaries({
          previous: previousParsed,
          delta: validated.data,
        })
      : validated.data;
  const normalizedMerged = ensurePromptPackCoverage(merged);

  const markdownSummary = await selectSummaryForMarkdown({
    projectId,
    summary: normalizedMerged,
    outputMode: summarySettings.summaryMarkdownOutputMode,
    nextNodeInputHint: nextNodeInputHint ?? null,
    analysesForFallback: newAnalyses,
  });

  const maxUpdatedAt = newRows.reduce<Date>((acc, r) => {
    return r.updatedAt > acc ? r.updatedAt : acc;
  }, newRows[0]!.updatedAt);

  await prisma.experienceSummary.upsert({
    where: { projectId },
    create: {
      projectId,
      model: modelName,
      schemaVersion: normalizedMerged.schemaVersion,
      summary: normalizedMerged as any,
      cursorUpdatedAt: maxUpdatedAt,
    },
    update: {
      model: modelName,
      schemaVersion: normalizedMerged.schemaVersion,
      summary: normalizedMerged as any,
      cursorUpdatedAt: maxUpdatedAt,
    },
  });

  if (summarySettings.summaryAppendMarkdownAbsolutePath) {
    try {
      await replaceSummaryHintSectionInMarkdown({
        projectId,
        absolutePath: summarySettings.summaryAppendMarkdownAbsolutePath,
        summary: markdownSummary,
        outputMode: summarySettings.summaryMarkdownOutputMode,
      });
    } catch (error) {
      logger.warn("Failed to replace summary hint section in markdown file", {
        projectId,
        path: summarySettings.summaryAppendMarkdownAbsolutePath,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
};
