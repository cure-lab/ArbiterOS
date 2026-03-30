import { TRPCError } from "@trpc/server";
import { z } from "zod/v4";
import {
  ChatMessageRole,
  ChatMessageType,
  LLMAdapter,
  LLMApiKeySchema,
  singleFilter,
  type ChatMessage,
  type FilterState,
  type Observation,
} from "@langfuse/shared";
import {
  fetchLLMCompletion,
  getObservationsForTrace,
  getTraceById,
  isLLMCompletionError,
  logger,
} from "@langfuse/shared/src/server";
import {
  applyLanguageInstructionToMessages,
  getLanguageFromCookieHeader,
} from "@/src/features/i18n/server";
import { projectRoleAccessRights } from "@/src/features/rbac/constants/projectAccessRights";
import {
  createTRPCRouter,
  protectedProjectProcedure,
} from "@/src/server/api/trpc";
import { type QueryType, viewVersions } from "@/src/features/query/types";
import { mapLegacyUiTableFilterToView } from "@/src/features/query/dashboardUiTableToViewMapping";
import { executeQuery } from "@/src/features/query/server/queryExecutor";
import {
  derivePolicyNamesFromMetadata,
  getBooleanFlag,
  getMetadataRecord,
  getObservationTurnIndex,
  mergeRelevantPolicyMetadata,
  parseStringArray,
  parseStringRecord,
} from "@/src/features/governance/utils/policyMetadata";
import { getHumanPolicyConfirmationState } from "@/src/features/governance/utils/policyConfirmation";
import {
  PolicySuggestionGenerateOutputSchema,
  PolicySuggestionModelSchema,
  PolicySuggestionResultSchema,
  PolicySuggestionStructuredOutputSchema,
} from "@/src/features/policy-suggestions/types";
import { resolveDemoOpenAIModel } from "@/src/features/error-analysis/types";

export { getMetadataRecord, parseStringArray, parseStringRecord };

const MAX_REJECTED_TURNS = 4;
const MAX_OBSERVATIONS_PER_TURN = 6;
const MAX_IO_CHARS = 2000;

type RejectedTurnDetail = {
  traceId: string;
  traceName: string | null;
  turnIndex: number | null;
  nodeCount: number;
};

type SampledTurnContext = {
  traceId: string;
  traceName: string | null;
  traceTimestamp: string | null;
  turnIndex: number | null;
  policyTurnIndices: number[];
  examplePrompt: string | null;
  policyProtected: string | null;
  inactivateErrorType: string | null;
  policyDescription: string | null;
  policySource: string | null;
  nodes: Array<{
    id: string;
    name: string;
    level: string | null;
    statusMessage: string | null;
    input: string | null;
    output: string | null;
    policyNames: string[];
    inactivateErrorType: string | null;
  }>;
  relatedTurns: Array<{
    turnIndex: number | null;
    examplePrompt: string | null;
    policyProtected: string | null;
    nodes: Array<{
      id: string;
      name: string;
      level: string | null;
      statusMessage: string | null;
      input: string | null;
      output: string | null;
      policyNames: string[];
      inactivateErrorType: string | null;
    }>;
  }>;
};

type SampledTurnNode = SampledTurnContext["nodes"][number];
type RelatedTurnContext = SampledTurnContext["relatedTurns"][number];

const GeneratePolicySuggestionInputSchema = z.object({
  projectId: z.string(),
  policyName: z.string().trim().min(1),
  globalFilterState: z.array(singleFilter).default([]),
  fromTimestamp: z.date(),
  toTimestamp: z.date(),
  version: viewVersions.optional().default("v1"),
});

function resolveErrorAnalysisModelFromMetadata(metadata: unknown) {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return "gpt-5.2" as const;
  }

  const autoErrorAnalysis = (metadata as Record<string, unknown>)
    .autoErrorAnalysis;
  if (
    !autoErrorAnalysis ||
    typeof autoErrorAnalysis !== "object" ||
    Array.isArray(autoErrorAnalysis)
  ) {
    return "gpt-5.2" as const;
  }

  const rawModel = (autoErrorAnalysis as Record<string, unknown>).model;
  const parsedModel = PolicySuggestionModelSchema.safeParse(rawModel);
  return parsedModel.success ? parsedModel.data : ("gpt-5.2" as const);
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
  return `${value.slice(0, Math.max(0, maxChars - 30))}\n...[truncated]`;
}

function getString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function parseTurnIndex(value: unknown): number | null {
  if (value == null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function extractObservationPrompt(observation: Observation): string | null {
  const rawObservation = observation as unknown as Record<string, unknown>;
  const input = rawObservation.input;
  if (input == null) return null;
  const raw = safeStringify(input).trim();
  return raw ? truncateString(raw, MAX_IO_CHARS) : null;
}

function getObservationHumanConfirmationState(params: {
  observation: Observation;
  traceInput?: unknown;
  traceMetadata?: unknown;
}) {
  const metadata = mergeRelevantPolicyMetadata({
    observationMetadata: params.observation.metadata,
    traceMetadata: params.traceMetadata,
    observationName: params.observation.name,
    statusMessage: params.observation.statusMessage,
  });

  return getHumanPolicyConfirmationState({
    metadata,
    observationInput: params.observation.input,
    traceInput: params.traceInput,
    traceMetadata: params.traceMetadata,
  });
}

function isPolicyViolationObservation(params: {
  observation: Observation;
  policyName: string;
  confirmationTurnIndex: number | null;
  traceMetadata?: unknown;
}): boolean {
  const { observation, policyName, confirmationTurnIndex, traceMetadata } =
    params;
  const metadata = mergeRelevantPolicyMetadata({
    observationMetadata: observation.metadata,
    traceMetadata,
    observationName: observation.name,
    statusMessage: observation.statusMessage,
  });
  const observationTurnIndex = getObservationTurnIndex({
    metadata: observation.metadata,
    observationName: observation.name,
  });
  if (
    confirmationTurnIndex != null &&
    observationTurnIndex != null &&
    observationTurnIndex > confirmationTurnIndex
  ) {
    return false;
  }

  const observationPolicyNames = derivePolicyNamesFromMetadata(metadata);
  const hasMatchingPolicyName =
    observationPolicyNames.length === 0 ||
    observationPolicyNames.includes(policyName);
  const statusMessage = observation.statusMessage ?? "";
  const hasPolicySignal =
    observation.level === "POLICY_VIOLATION" ||
    getBooleanFlag(metadata.policy_violation) ||
    Boolean(getString(metadata.policy_protected)) ||
    statusMessage.includes("POLICY_BLOCK") ||
    statusMessage.includes("blocked by policy");

  return hasPolicySignal && hasMatchingPolicyName;
}

function getObservationDisplayInput(observation: Observation): string | null {
  const input =
    observation.input == null
      ? null
      : truncateString(safeStringify(observation.input), MAX_IO_CHARS);
  return input && input.trim().length > 0 ? input : null;
}

function getObservationDisplayOutput(observation: Observation): string | null {
  const output =
    observation.output == null
      ? null
      : truncateString(safeStringify(observation.output), MAX_IO_CHARS);
  return output && output.trim().length > 0 ? output : null;
}

function getPolicyProtectedFromObservation(
  observation: Observation,
  traceMetadata?: unknown,
): string | null {
  const metadata = mergeRelevantPolicyMetadata({
    observationMetadata: observation.metadata,
    traceMetadata,
    observationName: observation.name,
    statusMessage: observation.statusMessage,
  });
  const protectedReason = getString(metadata.policy_protected);
  if (protectedReason) return protectedReason;
  const statusMessage = observation.statusMessage?.trim() ?? "";
  if (
    statusMessage.includes("POLICY_BLOCK") ||
    statusMessage.includes("blocked by policy")
  ) {
    return statusMessage;
  }
  return null;
}

function getInactivateErrorTypeFromObservation(
  observation: Observation,
  traceMetadata?: unknown,
): string | null {
  const metadata = mergeRelevantPolicyMetadata({
    observationMetadata: observation.metadata,
    traceMetadata,
    observationName: observation.name,
    statusMessage: observation.statusMessage,
  });

  const direct = getString(metadata.inactivate_error_type);
  if (direct) return direct;

  const fromArray = parseStringArray(metadata.inactivate_error_type)
    .map((value) => value.trim())
    .filter(Boolean);
  if (fromArray.length > 0) {
    return fromArray.join("\n");
  }

  return null;
}

function buildTurnNodes(
  observations: Observation[],
  traceMetadata?: unknown,
): SampledTurnNode[] {
  return observations.map((observation) => {
    const metadata = mergeRelevantPolicyMetadata({
      observationMetadata: observation.metadata,
      traceMetadata,
      observationName: observation.name,
      statusMessage: observation.statusMessage,
    });
    return {
      id: observation.id,
      name: observation.name || observation.id,
      level: observation.level ?? null,
      statusMessage: observation.statusMessage ?? null,
      input: getObservationDisplayInput(observation),
      output: getObservationDisplayOutput(observation),
      policyNames: derivePolicyNamesFromMetadata(metadata),
      inactivateErrorType: getInactivateErrorTypeFromObservation(
        observation,
        traceMetadata,
      ),
    };
  });
}

function parseSuggestedOutput(rawResult: unknown) {
  const direct = PolicySuggestionResultSchema.safeParse(rawResult);
  if (direct.success) return direct.data;

  if (typeof rawResult === "string") {
    try {
      const parsed = JSON.parse(rawResult);
      return PolicySuggestionResultSchema.parse(parsed);
    } catch {
      // handled below
    }
  }

  throw new TRPCError({
    code: "PRECONDITION_FAILED",
    message:
      "LLM returned an invalid policy suggestion payload (schema mismatch).",
  });
}

function mapLLMCompletionErrorToTRPCError(e: unknown): TRPCError | null {
  if (!isLLMCompletionError(e)) return null;
  const status = e.responseStatusCode ?? 500;
  const baseMessage = `LLM request failed (HTTP ${status}). ${e.message}`;
  const normalizedMessage = e.message.toLowerCase();

  if (
    normalizedMessage.includes("unexpected token '<'") ||
    normalizedMessage.includes("<!doctype") ||
    normalizedMessage.includes("<html")
  ) {
    return new TRPCError({
      code: "PRECONDITION_FAILED",
      message:
        `LLM request failed (HTTP ${status}). The configured OpenAI-compatible endpoint returned HTML instead of JSON.` +
        " Check Settings -> LLM Connections and verify the base URL points to the API endpoint (for example `https://api.openai.com/v1`) rather than a web page, login page, or proxy root.",
    });
  }

  if (status === 401 || status === 403) {
    return new TRPCError({
      code: "PRECONDITION_FAILED",
      message:
        baseMessage +
        " Check Settings -> LLM Connections (API key / permissions / base URL).",
    });
  }

  if (status === 404) {
    return new TRPCError({
      code: "PRECONDITION_FAILED",
      message:
        baseMessage +
        " The selected model may not exist on your configured endpoint.",
    });
  }

  if (status === 429) {
    return new TRPCError({
      code: "TOO_MANY_REQUESTS",
      message: `${baseMessage} Please retry shortly.`,
    });
  }

  return new TRPCError({
    code: "PRECONDITION_FAILED",
    message: baseMessage,
  });
}

export async function getRejectedTurnDetails(params: {
  projectId: string;
  policyName: string;
  globalFilterState: FilterState;
  fromTimestamp: Date;
  toTimestamp: Date;
  version: z.infer<typeof viewVersions>;
}): Promise<RejectedTurnDetail[]> {
  const query: QueryType = {
    view: "observations",
    dimensions: [
      { field: "policyName" },
      { field: "traceId" },
      { field: "traceName" },
      { field: "policyConfirmationTurnIndex" },
    ],
    metrics: [{ measure: "count", aggregation: "count" }],
    filters: [
      ...mapLegacyUiTableFilterToView("observations", params.globalFilterState),
      {
        column: "humanPolicyConfirmationState",
        operator: "=",
        value: "rejected",
        type: "string",
      },
    ],
    timeDimension: null,
    fromTimestamp: params.fromTimestamp.toISOString(),
    toTimestamp: params.toTimestamp.toISOString(),
    orderBy: [{ field: "count_count", direction: "desc" }],
    chartConfig: { type: "table", row_limit: 500 },
  };

  const rows = await executeQuery(
    params.projectId,
    query,
    params.version,
    false,
  );

  const deduped = new Map<string, RejectedTurnDetail>();
  for (const row of rows) {
    const policyName =
      typeof row.policyName === "string" ? row.policyName.trim() : "";
    if (policyName !== params.policyName) continue;

    const traceId = typeof row.traceId === "string" ? row.traceId.trim() : "";
    if (!traceId) continue;

    const turnIndex = parseTurnIndex(row.policyConfirmationTurnIndex);
    const key = `${traceId}::${turnIndex ?? "null"}`;
    if (deduped.has(key)) continue;

    deduped.set(key, {
      traceId,
      traceName:
        typeof row.traceName === "string" && row.traceName.trim().length > 0
          ? row.traceName.trim()
          : null,
      turnIndex,
      nodeCount:
        typeof row.count_count === "number"
          ? row.count_count
          : Number(row.count_count ?? 0),
    });
  }

  return [...deduped.values()].sort((a, b) => b.nodeCount - a.nodeCount);
}

export function buildSampledTurnContext(params: {
  policyName: string;
  detail: RejectedTurnDetail;
  trace: unknown;
  observations: Observation[];
}): SampledTurnContext | null {
  const { policyName, detail, trace, observations } = params;
  if (!trace) return null;
  const rawTrace = trace as unknown as Record<string, unknown>;
  const traceMetadata = getMetadataRecord(rawTrace.metadata);
  const acceptedConfirmationTurnIndices = new Set(
    observations
      .flatMap((observation) => {
        const humanConfirmationState = getObservationHumanConfirmationState({
          observation,
          traceInput: rawTrace.input,
          traceMetadata,
        });
        if (humanConfirmationState !== "accepted") {
          return [];
        }

        const turnIndex = getObservationTurnIndex({
          metadata: mergeRelevantPolicyMetadata({
            observationMetadata: observation.metadata,
            traceMetadata,
            observationName: observation.name,
            statusMessage: observation.statusMessage,
          }),
          observationName: observation.name,
        });
        return turnIndex != null ? [turnIndex] : [];
      })
      .filter((turnIndex): turnIndex is number => turnIndex != null),
  );

  const policyViolationTurnIndices = Array.from(
    new Set(
      observations
        .filter((observation) =>
          isPolicyViolationObservation({
            observation,
            policyName,
            confirmationTurnIndex: detail.turnIndex,
            traceMetadata,
          }),
        )
        .map((observation) =>
          getObservationTurnIndex({
            metadata: observation.metadata,
            observationName: observation.name,
          }),
        )
        .filter(
          (turnIndex): turnIndex is number =>
            turnIndex != null &&
            !acceptedConfirmationTurnIndices.has(turnIndex),
        ),
    ),
  ).sort((a, b) => a - b);

  const selectedTurnIndices =
    policyViolationTurnIndices.length > 0
      ? policyViolationTurnIndices
      : detail.turnIndex != null
        ? [detail.turnIndex]
        : [];

  const relatedTurns: RelatedTurnContext[] = [];
  for (const turnIndex of selectedTurnIndices) {
    const turnObservations = observations
      .filter(
        (observation) =>
          getObservationTurnIndex({
            metadata: observation.metadata,
            observationName: observation.name,
          }) === turnIndex,
      )
      .sort((a, b) => a.startTime.getTime() - b.startTime.getTime())
      .slice(0, MAX_OBSERVATIONS_PER_TURN);

    if (turnObservations.length === 0) continue;

    let turnPolicyProtected: string | null = null;
    let turnExamplePrompt: string | null = null;
    for (const observation of turnObservations) {
      if (!turnPolicyProtected) {
        turnPolicyProtected = getPolicyProtectedFromObservation(
          observation,
          traceMetadata,
        );
      }
      if (!turnExamplePrompt) {
        turnExamplePrompt =
          getObservationDisplayInput(observation) ??
          extractObservationPrompt(observation);
      }
    }

    relatedTurns.push({
      turnIndex,
      examplePrompt: turnExamplePrompt,
      policyProtected: turnPolicyProtected,
      nodes: buildTurnNodes(turnObservations, traceMetadata),
    });
  }

  const nodes = relatedTurns.flatMap((turn) => turn.nodes);
  if (nodes.length === 0) return null;

  let policyProtected: string | null = null;
  let inactivateErrorType: string | null = null;
  let policyDescription: string | null = null;
  let policySource: string | null = null;
  let examplePrompt: string | null = null;

  for (const observation of observations) {
    if (!inactivateErrorType) {
      inactivateErrorType = getInactivateErrorTypeFromObservation(
        observation,
        traceMetadata,
      );
    }

    const metadata = mergeRelevantPolicyMetadata({
      observationMetadata: observation.metadata,
      traceMetadata,
      observationName: observation.name,
      statusMessage: observation.statusMessage,
    });
    if (
      !policyDescription ||
      !policySource ||
      !policyProtected ||
      !examplePrompt
    ) {
      const turnIndex = getObservationTurnIndex({
        metadata: observation.metadata,
        observationName: observation.name,
      });
      if (
        selectedTurnIndices.length > 0 &&
        turnIndex != null &&
        !selectedTurnIndices.includes(turnIndex)
      ) {
        continue;
      }
    }

    if (!policyProtected) {
      policyProtected = getPolicyProtectedFromObservation(
        observation,
        traceMetadata,
      );
    }

    const descriptions = parseStringRecord(metadata.policy_descriptions);
    if (!policyDescription && descriptions[policyName]) {
      policyDescription = descriptions[policyName]!;
    }

    const sources = parseStringRecord(metadata.policy_sources);
    if (!policySource && sources[policyName]) {
      policySource = sources[policyName]!;
    }

    if (!examplePrompt) {
      examplePrompt =
        getObservationDisplayInput(observation) ??
        extractObservationPrompt(observation);
    }
  }

  if (!examplePrompt) {
    const traceInput = rawTrace.input;
    if (traceInput != null) {
      const serializedTraceInput = safeStringify(traceInput).trim();
      if (serializedTraceInput) {
        examplePrompt = truncateString(serializedTraceInput, MAX_IO_CHARS);
      }
    }
  }

  return {
    traceId: detail.traceId,
    traceName: detail.traceName,
    traceTimestamp:
      rawTrace.timestamp instanceof Date
        ? rawTrace.timestamp.toISOString()
        : null,
    turnIndex: detail.turnIndex,
    policyTurnIndices: selectedTurnIndices,
    examplePrompt,
    policyProtected,
    inactivateErrorType,
    policyDescription,
    policySource,
    nodes,
    relatedTurns,
  };
}

export const policySuggestionRouter = createTRPCRouter({
  generate: protectedProjectProcedure
    .input(GeneratePolicySuggestionInputSchema)
    .output(PolicySuggestionGenerateOutputSchema)
    .mutation(async ({ input, ctx }) => {
      const language = getLanguageFromCookieHeader(ctx.headers.cookie);
      if (input.fromTimestamp > input.toTimestamp) {
        throw new TRPCError({
          code: "BAD_REQUEST",
          message: "fromTimestamp must be before toTimestamp.",
        });
      }

      const user = ctx.session?.user;
      if (!user) {
        throw new TRPCError({
          code: "UNAUTHORIZED",
          message: "Please sign in to generate policy suggestions.",
        });
      }

      if (!user.admin) {
        const projectRole = user.organizations
          .flatMap((org) => org.projects)
          .find((project) => project.id === input.projectId)?.role;

        if (
          !projectRole ||
          !projectRoleAccessRights[projectRole].includes("llmApiKeys:read")
        ) {
          throw new TRPCError({
            code: "FORBIDDEN",
            message: "User does not have access to run LLM policy suggestions.",
          });
        }
      }

      const llmApiKey = await ctx.prisma.llmApiKeys.findFirst({
        where: {
          projectId: input.projectId,
          adapter: LLMAdapter.OpenAI,
        },
      });
      if (!llmApiKey) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "No OpenAI-adapter LLM connection configured. Please add one in Settings -> LLM Connections.",
        });
      }

      const projectSettings = await ctx.prisma.project.findUnique({
        where: {
          id: input.projectId,
        },
        select: {
          metadata: true,
        },
      });
      const configuredModel = resolveErrorAnalysisModelFromMetadata(
        projectSettings?.metadata,
      );

      const parsedKey = LLMApiKeySchema.safeParse(llmApiKey);
      if (!parsedKey.success) {
        logger.warn("Failed to parse LLM API key for policy suggestions", {
          projectId: input.projectId,
          policyName: input.policyName,
          error: parsedKey.error.message,
        });
        throw new TRPCError({
          code: "INTERNAL_SERVER_ERROR",
          message: "Could not parse LLM connection configuration.",
        });
      }

      const rejectedTurnDetails = await getRejectedTurnDetails({
        projectId: input.projectId,
        policyName: input.policyName,
        globalFilterState: input.globalFilterState,
        fromTimestamp: input.fromTimestamp,
        toTimestamp: input.toTimestamp,
        version: input.version,
      });

      if (rejectedTurnDetails.length === 0) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message:
            "No rejected turns found for this policy in the selected range.",
        });
      }

      const sampledDetails = rejectedTurnDetails.slice(0, MAX_REJECTED_TURNS);
      const sampledTurns = await Promise.all(
        sampledDetails.map(async (detail) => {
          const [trace, observations] = await Promise.all([
            getTraceById({
              traceId: detail.traceId,
              projectId: input.projectId,
            }),
            getObservationsForTrace({
              traceId: detail.traceId,
              projectId: input.projectId,
              includeIO: true,
            }),
          ]);

          return buildSampledTurnContext({
            policyName: input.policyName,
            detail,
            trace,
            observations: observations as Observation[],
          });
        }),
      );

      const cleanSampledTurns = sampledTurns.filter(
        (turn): turn is SampledTurnContext => turn !== null,
      );

      if (cleanSampledTurns.length === 0) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message:
            "Unable to build policy context from rejected turns in the selected range.",
        });
      }

      const payload = {
        policyName: input.policyName,
        timeRange: {
          from: input.fromTimestamp.toISOString(),
          to: input.toTimestamp.toISOString(),
        },
        summary: {
          rejectedTurnCount: rejectedTurnDetails.length,
          sampledRejectedTurns: cleanSampledTurns.length,
          sampledPolicyViolationTurns: cleanSampledTurns.reduce(
            (sum, turn) => sum + turn.policyTurnIndices.length,
            0,
          ),
          sampledInactivateSignals: cleanSampledTurns.filter(
            (turn) => typeof turn.inactivateErrorType === "string",
          ).length,
        },
        examples: cleanSampledTurns,
        instructions: {
          objective:
            "Recommend a policy-level change that improves the policy itself while preserving core safety constraints. The primary goal is to better align the policy with the user's demonstrated preferences from past rejected confirmations and thereby reduce future reject rate for the same intent. Focus on the rule, scope, matching logic, allowlist/denylist criteria, path/tool boundaries, thresholds, or policy wording.",
          format:
            "suggestion should be 1-2 lines; reason should be 2-3 lines and grounded in the provided examples. When the evidence includes exact concrete values such as paths, tools, prefixes, thresholds, or schema names, keep those exact literals in the suggestion instead of paraphrasing them.",
          constraints:
            "Do not propose bypassing policy/safety checks. Reduce reject rate by better matching the user's confirmed preferences only when the evidence supports that the current policy is overly broad, ambiguous, or incorrectly scoped.",
          grounding:
            "Prioritize evidence from policy-violation turns, node inputs/outputs, and explicit policy-block messages. If present, treat inactivate_error_type as a secondary signal for over-triggering policy matches that were intentionally left non-blocking. Treat downstream non-policy execution failures such as 'File not found' as separate operational issues unless policy evidence clearly supports a policy-level cause. If an example contains a concrete blocked path or prefix, cite that exact value and suggest a concrete config edit such as adding it to allow_prefixes when appropriate.",
          exclusions:
            "Do not suggest changing the wording of the violation message, confirmation prompt, response phrasing, UI flow, or other communication/UX details unless the underlying policy text itself is part of the problem. The recommendation must be about modifying the policy itself.",
        },
      };

      const modelName =
        parsedKey.data.baseURL &&
        !parsedKey.data.baseURL.includes("api.openai.com") &&
        configuredModel === "gpt-5.2"
          ? "gpt-5.2"
          : resolveDemoOpenAIModel(configuredModel);

      const messages: ChatMessage[] = [
        {
          type: ChatMessageType.System,
          role: ChatMessageRole.System,
          content:
            'You are a policy-quality assistant. Based on policy enforcement evidence and rejected-turn examples, propose a concise policy modification suggestion. The goal is to make the policy better match the user\'s demonstrated preferences from past rejected confirmations and reduce future reject rate for the same intent, while keeping the policy safe and not bypassing core constraints. Ground your answer primarily in policy-violation turns, node inputs/outputs, and explicit policy-block evidence; use inactivate_error_type as an auxiliary signal when present. Do not blame later tool execution errors unless the policy evidence clearly supports that conclusion. Recommend changes to the policy itself only: policy rule, scope, thresholds, allowlist/denylist logic, or policy wording. When the evidence includes exact concrete literals such as blocked file paths, prefixes, tool names, or thresholds, preserve those exact values in the suggestion and reason instead of paraphrasing them. Prefer direct configuration wording like add "/exact/path" to allow_prefixes when the evidence supports that edit. Do not recommend UI-copy, confirmation-prompt, or response-message changes unless the policy text itself must change. Return only JSON matching the schema.',
        },
        {
          type: ChatMessageType.User,
          role: ChatMessageRole.User,
          content: safeStringify(payload),
        },
      ];

      let rawResult: unknown;
      try {
        rawResult = await fetchLLMCompletion({
          llmConnection: parsedKey.data,
          messages: applyLanguageInstructionToMessages({
            messages,
            language,
            mode: "structured",
          }),
          modelParams: {
            provider: parsedKey.data.provider,
            adapter: LLMAdapter.OpenAI,
            model: modelName,
            temperature: 0.2,
            max_tokens: 400,
          },
          streaming: false,
          structuredOutputSchema: PolicySuggestionStructuredOutputSchema,
        });
      } catch (e) {
        try {
          rawResult = await fetchLLMCompletion({
            llmConnection: parsedKey.data,
            messages: applyLanguageInstructionToMessages({
              messages,
              language,
              mode: "structured",
            }),
            modelParams: {
              provider: parsedKey.data.provider,
              adapter: LLMAdapter.OpenAI,
              model: modelName,
              temperature: 0.2,
              max_tokens: 400,
            },
            streaming: false,
          });
        } catch (fallbackError) {
          const mappedFallback =
            mapLLMCompletionErrorToTRPCError(fallbackError);
          if (mappedFallback) throw mappedFallback;

          const mappedOriginal = mapLLMCompletionErrorToTRPCError(e);
          if (mappedOriginal) throw mappedOriginal;
          throw fallbackError;
        }
      }

      const suggestion = parseSuggestedOutput(rawResult);

      return {
        policyName: input.policyName,
        suggestion,
        sampledRejectedTurns: cleanSampledTurns.length,
      };
    }),
});
