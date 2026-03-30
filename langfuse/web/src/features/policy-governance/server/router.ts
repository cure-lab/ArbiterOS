import { TRPCError } from "@trpc/server";
import { z } from "zod/v4";
import { z as zodV3 } from "zod/v3";
import {
  ChatMessageRole,
  ChatMessageType,
  LLMAdapter,
  LLMApiKeySchema,
  singleFilter,
  type ChatMessage,
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
  createTRPCRouter,
  protectedProjectProcedure,
} from "@/src/server/api/trpc";
import {
  rewriteAbsolutePathFromPrefixMappings,
  trimTrailingPathSeparators,
} from "@/src/features/file-paths/server/absolutePathPrefixMap";
import { throwIfNoProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { projectRoleAccessRights } from "@/src/features/rbac/constants/projectAccessRights";
import { resolveDemoOpenAIModel } from "@/src/features/error-analysis/types";
import {
  buildSampledTurnContext,
  getRejectedTurnDetails,
} from "@/src/features/policy-suggestions/server/router";
import { type QueryType, viewVersions } from "@/src/features/query/types";
import { mapLegacyUiTableFilterToView } from "@/src/features/query/dashboardUiTableToViewMapping";
import { executeQuery } from "@/src/features/query/server/queryExecutor";
import { readFile, stat, writeFile } from "fs/promises";
import { basename, dirname, isAbsolute, join } from "path";
import {
  applyLanguageInstructionToMessages,
  getLanguageFromCookieHeader,
} from "@/src/features/i18n/server";

const PolicyRegistryEntrySchema = z.object({
  name: z.string().trim().min(1),
  enabled: z.boolean(),
  description: z.string().default(""),
});
const PolicyRegistrySchema = z.array(PolicyRegistryEntrySchema);
const JsonObjectSchema = z.record(z.string(), z.unknown());

const PolicyGovernanceSettingsSchema = z.object({
  kernelPolicyPathAbsolute: z.string().trim().min(1).nullable().default(null),
  lastPolicyUpdatedAt: z.string().trim().min(1).nullable().default(null),
  beginnerSummaries: z.record(z.string(), z.string()).default({}),
  policyConfirmationResetTimestamps: z
    .record(z.string(), z.string().trim().min(1))
    .default({}),
});

export const POLICY_SECTION_MAP: Record<string, string[]> = {
  PathBudgetPolicy: ["paths", "input_budget"],
  AllowDenyPolicy: ["allow", "deny"],
  EfsmGatePolicy: ["efsm"],
  TaintPolicy: ["taint"],
  RateLimitPolicy: ["rate_limit"],
  OutputBudgetPolicy: ["output_budget"],
  SecurityLabelPolicy: ["security"],
  ExecCompositePolicy: ["exec_composite_policy"],
  DeletePolicy: ["delete_policy"],
};

const PolicyCardSchema = z.object({
  name: z.string(),
  description: z.string(),
  enabled: z.boolean(),
  settingSections: z.array(z.string()),
  settingsBySection: z.record(z.string(), z.unknown()),
});

const LoadPolicyFilesOutputSchema = z.object({
  configuredPath: z.string().nullable(),
  resolvedPathInput: z.string(),
  policyJsonPath: z.string(),
  policyRegistryPath: z.string(),
  policySourceFingerprint: z.string(),
  sourceLastModifiedAt: z.string(),
  policyJson: JsonObjectSchema,
  policyRegistryJson: PolicyRegistrySchema,
  policyCards: z.array(PolicyCardSchema),
  policySectionMap: z.record(z.string(), z.array(z.string())),
});

const PolicyFilesStatusOutputSchema = z.object({
  configuredPath: z.string().nullable(),
  resolvedPathInput: z.string(),
  policyJsonPath: z.string(),
  policyRegistryPath: z.string(),
  policySourceFingerprint: z.string(),
  sourceLastModifiedAt: z.string(),
});

const SavePolicyFilesInputSchema = z.object({
  projectId: z.string(),
  pathOverride: z.string().trim().min(1).optional(),
  policyJson: JsonObjectSchema,
  policyRegistryJson: PolicyRegistrySchema,
});

const GeneratePolicyUpdateProposalInputSchema = z.object({
  projectId: z.string(),
  policyName: z.string().trim().min(1),
  policyJson: JsonObjectSchema,
  policyRegistryJson: PolicyRegistrySchema,
  suggestion: z.object({
    suggestion: z.string().trim().min(1),
    reason: z.string().trim().min(1),
    supportingSignals: z.array(z.string()).default([]),
  }),
});

const GeneratePolicyUpdateProposalOutputSchema = z.object({
  policyName: z.string(),
  summary: z.string(),
  appliedSections: z.array(z.string()),
  proposedPolicyJson: JsonObjectSchema,
  proposedPolicyRegistryJson: PolicyRegistrySchema,
});

const PolicyGuideViolationExampleSchema = z.object({
  observationName: z.string().nullable(),
  statusMessage: z.string().nullable(),
  input: z.string().nullable(),
  output: z.string().nullable(),
  inactivateErrorType: z.string().nullable(),
  policyNames: z.array(z.string()),
});

const PolicyGuideCaseSchema = z.object({
  traceId: z.string(),
  traceName: z.string().nullable(),
  traceTimestamp: z.string().nullable(),
  turnIndex: z.number().nullable(),
  targetObservationId: z.string().nullable(),
  blockedAction: z.string().nullable(),
  examplePrompt: z.string().nullable(),
  violationExample: PolicyGuideViolationExampleSchema.nullable(),
});

const PolicyGuideInsightSchema = z.object({
  policyName: z.string(),
  recentViolationCount: z.number().int().nonnegative(),
  exampleBlockedAction: z.string().nullable(),
  examplePrompt: z.string().nullable(),
  exampleViolation: PolicyGuideViolationExampleSchema.nullable(),
  similarCases: z.array(PolicyGuideCaseSchema),
});

const PolicyGuideInsightsInputSchema = z.object({
  projectId: z.string(),
  policyNames: z.array(z.string().trim().min(1)).default([]),
  globalFilterState: z.array(singleFilter).default([]),
  fromTimestamp: z.date(),
  toTimestamp: z.date(),
  version: viewVersions.optional().default("v1"),
});

const PolicyGuideInsightsOutputSchema = z.array(PolicyGuideInsightSchema);

const PolicyConfirmationSummarySchema = z.object({
  totalCount: z.number().int().nonnegative(),
  acceptedCount: z.number().int().nonnegative(),
  rejectedCount: z.number().int().nonnegative(),
  rejectedRate: z.number().min(0).max(1),
});

const GeneratePolicyBeginnerSummaryInputSchema = z.object({
  projectId: z.string(),
  policyName: z.string().trim().min(1),
  description: z.string().default(""),
  enabled: z.boolean(),
  settingSections: z.array(z.string()).default([]),
  settingsBySection: z.record(z.string(), z.unknown()).default({}),
  highlightThresholdPct: z.number().min(0).max(100).default(70),
  suggestPolicyUpdate: z.boolean().default(false),
  confirmationSummary: PolicyConfirmationSummarySchema.nullable().default(null),
  guideInsight: PolicyGuideInsightSchema.nullable().default(null),
});

const GeneratePolicyBeginnerSummaryOutputSchema = z.object({
  summary: z.string(),
});

const PolicyUpdateProposalResultSchema = z.object({
  summary: z.string().trim().min(1),
  proposedPolicyJson: JsonObjectSchema,
});

const PolicyUpdateProposalStructuredOutputSchema = zodV3.object({
  summary: zodV3
    .string()
    .describe(
      "1-2 concise lines describing the proposed policy change and why it helps.",
    ),
  proposedPolicyJson: zodV3
    .record(zodV3.any())
    .describe(
      "Complete updated policy.json object. Preserve unrelated sections exactly as provided.",
    ),
});
const POLICY_UPDATE_PROPOSAL_MAX_TOKENS = 3200;

const PolicyBeginnerSummaryStructuredOutputSchema = zodV3.object({
  summary: zodV3
    .string()
    .describe(
      "A plain-language explanation of the policy in 4-8 short lines for someone without a programming background. Explain what it protects, what the current settings mean in everyday terms, and when available include concrete recent blocked or rejected examples with simple explanations.",
    ),
});

function extractFirstJsonObject(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  let inString = false;
  let escaped = false;
  let depth = 0;
  let start = -1;

  for (let i = 0; i < trimmed.length; i++) {
    const char = trimmed[i]!;

    if (inString) {
      if (escaped) {
        escaped = false;
        continue;
      }
      if (char === "\\") {
        escaped = true;
        continue;
      }
      if (char === '"') {
        inString = false;
      }
      continue;
    }

    if (char === '"') {
      inString = true;
      continue;
    }

    if (char === "{") {
      if (depth === 0) start = i;
      depth++;
      continue;
    }

    if (char === "}") {
      if (depth === 0) continue;
      depth--;
      if (depth === 0 && start !== -1) {
        return trimmed.slice(start, i + 1);
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
      // Try the next candidate.
    }
  }

  throw new Error("Could not parse JSON object from LLM response.");
}

function unwrapProposalResult(rawResult: unknown): unknown {
  if (!rawResult || typeof rawResult !== "object" || Array.isArray(rawResult)) {
    return rawResult;
  }

  const obj = rawResult as Record<string, unknown>;
  const nestedCandidate = [
    obj.proposal,
    obj.result,
    obj.output,
    obj.response,
  ].find(
    (value) => value && typeof value === "object" && !Array.isArray(value),
  );

  const source =
    (nestedCandidate as Record<string, unknown> | undefined) ??
    (rawResult as Record<string, unknown>);

  const summary = source.summary ?? source.reason ?? source.message;
  const rawPolicyJson = source.proposedPolicyJson ?? source.policyJson;

  const maybeParseJson = (value: unknown): unknown => {
    if (typeof value !== "string") return value;
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  };

  const normalized: Record<string, unknown> = {
    ...(summary !== undefined ? { summary } : {}),
    ...(rawPolicyJson !== undefined
      ? { proposedPolicyJson: maybeParseJson(rawPolicyJson) }
      : {}),
  };

  return Object.keys(normalized).length > 0
    ? { ...source, ...normalized }
    : source;
}

function parsePolicyGovernanceSettings(metadata: unknown) {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return {
      kernelPolicyPathAbsolute: null as string | null,
      lastPolicyUpdatedAt: null as string | null,
      beginnerSummaries: {} as Record<string, string>,
      policyConfirmationResetTimestamps: {} as Record<string, string>,
    };
  }

  const maybe = (metadata as Record<string, unknown>).policyGovernance;
  const parsed = PolicyGovernanceSettingsSchema.safeParse(maybe);
  if (!parsed.success)
    return {
      kernelPolicyPathAbsolute: null as string | null,
      lastPolicyUpdatedAt: null as string | null,
      beginnerSummaries: {} as Record<string, string>,
      policyConfirmationResetTimestamps: {} as Record<string, string>,
    };
  return parsed.data;
}

async function pathExists(path: string) {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
}

async function isDirectoryPath(path: string) {
  try {
    return (await stat(path)).isDirectory();
  } catch {
    return false;
  }
}

export function buildPolicyCards(params: {
  policyRegistryJson: z.infer<typeof PolicyRegistrySchema>;
  policyJson: z.infer<typeof JsonObjectSchema>;
}) {
  const { policyRegistryJson, policyJson } = params;
  return policyRegistryJson.map((entry) => {
    const settingSections = POLICY_SECTION_MAP[entry.name] ?? [];
    const settingsBySection = Object.fromEntries(
      settingSections.map((section) => [section, policyJson[section] ?? null]),
    );
    return {
      name: entry.name,
      description: entry.description ?? "",
      enabled: entry.enabled,
      settingSections,
      settingsBySection,
    };
  });
}

export async function resolvePolicyPaths(pathInput: string) {
  const trimmed = pathInput.trim();
  if (!trimmed) {
    throw new TRPCError({
      code: "BAD_REQUEST",
      message: "Policy path is required.",
    });
  }
  if (!isAbsolute(trimmed)) {
    throw new TRPCError({
      code: "BAD_REQUEST",
      message: "Policy path must be absolute.",
    });
  }

  const candidateInputs = Array.from(
    new Set([
      trimTrailingPathSeparators(trimmed),
      rewriteAbsolutePathFromPrefixMappings(trimmed),
    ]),
  );

  for (const candidateInput of candidateInputs) {
    const candidates: Array<{
      policyJsonPath: string;
      policyRegistryPath: string;
    }> = [];

    const base = basename(candidateInput);
    if (base === "policy.json") {
      candidates.push({
        policyJsonPath: candidateInput,
        policyRegistryPath: join(
          dirname(candidateInput),
          "policy_registry.json",
        ),
      });
    } else if (base === "policy_registry.json") {
      candidates.push({
        policyJsonPath: join(dirname(candidateInput), "policy.json"),
        policyRegistryPath: candidateInput,
      });
    } else if (await isDirectoryPath(candidateInput)) {
      candidates.push({
        policyJsonPath: join(candidateInput, "policy.json"),
        policyRegistryPath: join(candidateInput, "policy_registry.json"),
      });
      candidates.push({
        policyJsonPath: join(candidateInput, "arbiteros_kernel", "policy.json"),
        policyRegistryPath: join(
          candidateInput,
          "arbiteros_kernel",
          "policy_registry.json",
        ),
      });
    }

    for (const candidate of candidates) {
      const hasPolicy = await pathExists(candidate.policyJsonPath);
      const hasRegistry = await pathExists(candidate.policyRegistryPath);
      if (hasPolicy && hasRegistry) {
        return {
          resolvedPathInput: candidateInput,
          policyJsonPath: candidate.policyJsonPath,
          policyRegistryPath: candidate.policyRegistryPath,
        };
      }
    }
  }

  throw new TRPCError({
    code: "NOT_FOUND",
    message:
      "Could not resolve policy.json and policy_registry.json from the provided path. Provide either the arbiteros_kernel folder or a direct path to one of the files. If Langfuse runs in Docker or production, mount the policy directory into the web container and configure LANGFUSE_PATH_PREFIX_MAP when the host path differs from the container path.",
  });
}

async function readPolicyDocuments(paths: {
  policyJsonPath: string;
  policyRegistryPath: string;
}) {
  let rawPolicy = "";
  let rawRegistry = "";
  try {
    rawPolicy = await readFile(paths.policyJsonPath, "utf8");
    rawRegistry = await readFile(paths.policyRegistryPath, "utf8");
  } catch (error) {
    throw new TRPCError({
      code: "PRECONDITION_FAILED",
      message: `Failed to read policy files: ${error instanceof Error ? error.message : String(error)}`,
    });
  }

  let parsedPolicy: unknown;
  let parsedRegistry: unknown;
  try {
    parsedPolicy = JSON.parse(rawPolicy);
    parsedRegistry = JSON.parse(rawRegistry);
  } catch (error) {
    throw new TRPCError({
      code: "PRECONDITION_FAILED",
      message: `Failed to parse policy JSON files: ${error instanceof Error ? error.message : String(error)}`,
    });
  }

  const policyJson = JsonObjectSchema.safeParse(parsedPolicy);
  if (!policyJson.success) {
    throw new TRPCError({
      code: "PRECONDITION_FAILED",
      message: "policy.json is not a valid JSON object.",
    });
  }

  const policyRegistryJson = PolicyRegistrySchema.safeParse(parsedRegistry);
  if (!policyRegistryJson.success) {
    throw new TRPCError({
      code: "PRECONDITION_FAILED",
      message: "policy_registry.json is not a valid policy registry array.",
    });
  }

  return {
    policyJson: policyJson.data,
    policyRegistryJson: policyRegistryJson.data,
  };
}

export async function getPolicySourceMetadata(paths: {
  policyJsonPath: string;
  policyRegistryPath: string;
}) {
  try {
    const [policyStat, registryStat] = await Promise.all([
      stat(paths.policyJsonPath),
      stat(paths.policyRegistryPath),
    ]);
    const newestMtimeMs = Math.max(policyStat.mtimeMs, registryStat.mtimeMs);

    return {
      policySourceFingerprint: [
        `${policyStat.mtimeMs}:${policyStat.size}`,
        `${registryStat.mtimeMs}:${registryStat.size}`,
      ].join("|"),
      sourceLastModifiedAt: new Date(newestMtimeMs).toISOString(),
    };
  } catch (error) {
    throw new TRPCError({
      code: "PRECONDITION_FAILED",
      message: `Failed to inspect policy files: ${error instanceof Error ? error.message : String(error)}`,
    });
  }
}

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
  return rawModel === "gpt-4.1" ? ("gpt-4.1" as const) : ("gpt-5.2" as const);
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

export function parseProposalResult(rawResult: unknown) {
  const unwrappedDirect = unwrapProposalResult(rawResult);
  const direct = PolicyUpdateProposalResultSchema.safeParse(unwrappedDirect);
  if (direct.success) return direct.data;

  if (typeof rawResult === "string") {
    try {
      const parsed = parseJsonObjectFromCompletion(rawResult);
      return PolicyUpdateProposalResultSchema.parse(
        unwrapProposalResult(parsed),
      );
    } catch {
      // handled below
    }
  }

  logger.warn("Policy update proposal payload did not match schema", {
    rawResult:
      typeof rawResult === "string"
        ? rawResult.slice(0, 1500)
        : JSON.stringify(rawResult).slice(0, 1500),
    unwrappedResult:
      typeof unwrappedDirect === "string"
        ? unwrappedDirect.slice(0, 1500)
        : JSON.stringify(unwrappedDirect).slice(0, 1500),
  });

  throw new TRPCError({
    code: "PRECONDITION_FAILED",
    message:
      "LLM returned an invalid policy update proposal payload (schema mismatch).",
  });
}

function safeStringify(value: unknown): string {
  try {
    return typeof value === "string" ? value : JSON.stringify(value);
  } catch {
    return "[Unserializable value]";
  }
}

async function getPolicyViolationCounts(params: {
  projectId: string;
  policyNames: string[];
  globalFilterState: z.infer<
    typeof PolicyGuideInsightsInputSchema
  >["globalFilterState"];
  fromTimestamp: Date;
  toTimestamp: Date;
  version: z.infer<typeof viewVersions>;
}): Promise<Map<string, number>> {
  if (params.policyNames.length === 0) {
    return new Map();
  }

  const query: QueryType = {
    view: "observations",
    dimensions: [{ field: "policyName" }],
    metrics: [{ measure: "count", aggregation: "count" }],
    filters: [
      ...mapLegacyUiTableFilterToView("observations", params.globalFilterState),
      {
        column: "level",
        operator: "any of",
        value: ["POLICY_VIOLATION"],
        type: "stringOptions",
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
    params.version === "v2",
  );

  const counts = new Map<string, number>();
  const allowedPolicyNames = new Set(params.policyNames);
  for (const row of rows) {
    const policyName =
      typeof row.policyName === "string" ? row.policyName.trim() : "";
    if (!policyName || !allowedPolicyNames.has(policyName)) continue;
    counts.set(policyName, Number(row.count_count ?? 0));
  }

  return counts;
}

function getTurnBlockedAction(
  turn: ReturnType<typeof buildSampledTurnContext>,
) {
  if (!turn) return null;
  return (
    turn.policyProtected ??
    turn.relatedTurns.find((relatedTurn) => relatedTurn.policyProtected)
      ?.policyProtected ??
    turn.nodes.find((node) => node.statusMessage)?.statusMessage ??
    null
  );
}

function getTurnExamplePrompt(
  turn: ReturnType<typeof buildSampledTurnContext>,
) {
  if (!turn) return null;
  return (
    turn.examplePrompt ??
    turn.relatedTurns.find((relatedTurn) => relatedTurn.examplePrompt)
      ?.examplePrompt ??
    null
  );
}

function getTurnViolationExample(
  turn: ReturnType<typeof buildSampledTurnContext>,
  policyName: string,
) {
  if (!turn) return null;

  const relevantNode = turn.nodes.find((node) => {
    const statusMessage = node.statusMessage ?? "";
    return (
      node.level === "POLICY_VIOLATION" ||
      node.policyNames.includes(policyName) ||
      statusMessage.includes("POLICY_BLOCK") ||
      statusMessage.includes("blocked by policy")
    );
  });

  if (!relevantNode) return null;

  return {
    observationName: relevantNode.name ?? null,
    statusMessage: relevantNode.statusMessage,
    input: relevantNode.input,
    output: relevantNode.output,
    inactivateErrorType: relevantNode.inactivateErrorType,
    policyNames: relevantNode.policyNames,
  };
}

function getTurnTargetObservationId(
  turn: ReturnType<typeof buildSampledTurnContext>,
  policyName: string,
) {
  if (!turn) return null;

  const relatedTurnTarget = turn.relatedTurns
    .flatMap((relatedTurn) => relatedTurn.nodes)
    .find(
      (node) =>
        node.level === "POLICY_VIOLATION" ||
        node.policyNames.includes(policyName),
    );

  if (relatedTurnTarget) {
    return relatedTurnTarget.id;
  }

  return (
    turn.nodes.find(
      (node) =>
        node.level === "POLICY_VIOLATION" ||
        node.policyNames.includes(policyName),
    )?.id ??
    turn.nodes[0]?.id ??
    null
  );
}

function parsePolicyBeginnerSummaryResult(rawResult: unknown) {
  const direct = GeneratePolicyBeginnerSummaryOutputSchema.safeParse(rawResult);
  if (direct.success) return direct.data;

  if (typeof rawResult === "string") {
    try {
      const parsed = parseJsonObjectFromCompletion(rawResult);
      return GeneratePolicyBeginnerSummaryOutputSchema.parse(parsed);
    } catch {
      return {
        summary: rawResult.trim(),
      };
    }
  }

  throw new TRPCError({
    code: "PRECONDITION_FAILED",
    message:
      "LLM returned an invalid beginner summary payload (schema mismatch).",
  });
}

export const policyGovernanceRouter = createTRPCRouter({
  loadPolicyFiles: protectedProjectProcedure
    .input(
      z.object({
        projectId: z.string(),
        pathOverride: z.string().trim().min(1).optional(),
      }),
    )
    .output(LoadPolicyFilesOutputSchema)
    .mutation(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:read",
      });

      const project = await ctx.prisma.project.findUnique({
        where: { id: input.projectId, orgId: ctx.session.orgId },
        select: { metadata: true },
      });
      if (!project) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Project not found",
        });
      }

      const existingSettings = parsePolicyGovernanceSettings(project.metadata);
      const configuredPath = existingSettings.kernelPolicyPathAbsolute;
      const selectedPath = input.pathOverride ?? configuredPath;
      if (!selectedPath) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "No policy path configured. Set a kernel policy path first in this page.",
        });
      }

      const resolved = await resolvePolicyPaths(selectedPath);
      const docs = await readPolicyDocuments(resolved);
      const sourceMetadata = await getPolicySourceMetadata(resolved);
      const policyCards = buildPolicyCards({
        policyRegistryJson: docs.policyRegistryJson,
        policyJson: docs.policyJson,
      });

      return {
        configuredPath,
        ...resolved,
        ...sourceMetadata,
        policyJson: docs.policyJson,
        policyRegistryJson: docs.policyRegistryJson,
        policyCards,
        policySectionMap: POLICY_SECTION_MAP,
      };
    }),

  getPolicyFilesStatus: protectedProjectProcedure
    .input(
      z.object({
        projectId: z.string(),
        pathOverride: z.string().trim().min(1).optional(),
      }),
    )
    .output(PolicyFilesStatusOutputSchema)
    .query(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:read",
      });

      const project = await ctx.prisma.project.findUnique({
        where: { id: input.projectId, orgId: ctx.session.orgId },
        select: { metadata: true },
      });
      if (!project) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Project not found",
        });
      }

      const existingSettings = parsePolicyGovernanceSettings(project.metadata);
      const configuredPath = existingSettings.kernelPolicyPathAbsolute;
      const selectedPath = input.pathOverride ?? configuredPath;
      if (!selectedPath) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "No policy path configured. Set a kernel policy path first in this page.",
        });
      }

      const resolved = await resolvePolicyPaths(selectedPath);
      const sourceMetadata = await getPolicySourceMetadata(resolved);

      return {
        configuredPath,
        ...resolved,
        ...sourceMetadata,
      };
    }),

  savePolicyFiles: protectedProjectProcedure
    .input(SavePolicyFilesInputSchema)
    .output(LoadPolicyFilesOutputSchema)
    .mutation(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:update",
      });

      const project = await ctx.prisma.project.findUnique({
        where: { id: input.projectId, orgId: ctx.session.orgId },
        select: { metadata: true },
      });
      if (!project) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Project not found",
        });
      }

      const existingSettings = parsePolicyGovernanceSettings(project.metadata);
      const configuredPath = existingSettings.kernelPolicyPathAbsolute;
      const selectedPath = input.pathOverride ?? configuredPath;
      if (!selectedPath) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "No policy path configured. Set a kernel policy path first in this page.",
        });
      }

      const resolved = await resolvePolicyPaths(selectedPath);

      await writeFile(
        resolved.policyJsonPath,
        `${JSON.stringify(input.policyJson, null, 2)}\n`,
        "utf8",
      );
      await writeFile(
        resolved.policyRegistryPath,
        `${JSON.stringify(input.policyRegistryJson, null, 2)}\n`,
        "utf8",
      );

      const updatedPolicyGovernance = {
        ...existingSettings,
        kernelPolicyPathAbsolute: configuredPath,
        lastPolicyUpdatedAt: new Date().toISOString(),
      };
      const projectMetadata =
        project.metadata &&
        typeof project.metadata === "object" &&
        !Array.isArray(project.metadata)
          ? (project.metadata as Record<string, unknown>)
          : {};
      await ctx.prisma.project.update({
        where: { id: input.projectId, orgId: ctx.session.orgId },
        data: {
          metadata: {
            ...projectMetadata,
            policyGovernance: updatedPolicyGovernance,
          } as any,
        },
      });

      const sourceMetadata = await getPolicySourceMetadata(resolved);
      const policyCards = buildPolicyCards({
        policyRegistryJson: input.policyRegistryJson,
        policyJson: input.policyJson,
      });

      return {
        configuredPath,
        ...resolved,
        ...sourceMetadata,
        policyJson: input.policyJson,
        policyRegistryJson: input.policyRegistryJson,
        policyCards,
        policySectionMap: POLICY_SECTION_MAP,
      };
    }),

  getPolicyGuideInsights: protectedProjectProcedure
    .input(PolicyGuideInsightsInputSchema)
    .output(PolicyGuideInsightsOutputSchema)
    .query(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:read",
      });

      if (input.fromTimestamp > input.toTimestamp) {
        return input.policyNames.map((policyName) => ({
          policyName,
          recentViolationCount: 0,
          exampleBlockedAction: null,
          examplePrompt: null,
          exampleViolation: null,
          similarCases: [],
        }));
      }

      const recentViolationCounts = await getPolicyViolationCounts({
        projectId: input.projectId,
        policyNames: input.policyNames,
        globalFilterState: input.globalFilterState,
        fromTimestamp: input.fromTimestamp,
        toTimestamp: input.toTimestamp,
        version: input.version,
      });

      return Promise.all(
        input.policyNames.map(async (policyName) => {
          const rejectedTurnDetails = await getRejectedTurnDetails({
            projectId: input.projectId,
            policyName,
            globalFilterState: input.globalFilterState,
            fromTimestamp: input.fromTimestamp,
            toTimestamp: input.toTimestamp,
            version: input.version,
          });

          const sampledTurns = (
            await Promise.all(
              rejectedTurnDetails.slice(0, 3).map(async (detail) => {
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
                  policyName,
                  detail,
                  trace,
                  observations: observations as Observation[],
                });
              }),
            )
          ).filter(
            (
              turn,
            ): turn is NonNullable<
              ReturnType<typeof buildSampledTurnContext>
            > => turn !== null,
          );

          return {
            policyName,
            recentViolationCount: recentViolationCounts.get(policyName) ?? 0,
            exampleBlockedAction: sampledTurns[0]
              ? getTurnBlockedAction(sampledTurns[0])
              : null,
            examplePrompt: sampledTurns[0]
              ? getTurnExamplePrompt(sampledTurns[0])
              : null,
            exampleViolation: sampledTurns[0]
              ? getTurnViolationExample(sampledTurns[0], policyName)
              : null,
            similarCases: sampledTurns.map((turn) => ({
              traceId: turn.traceId,
              traceName: turn.traceName,
              traceTimestamp: turn.traceTimestamp,
              turnIndex: turn.turnIndex,
              targetObservationId: getTurnTargetObservationId(turn, policyName),
              blockedAction: getTurnBlockedAction(turn),
              examplePrompt: getTurnExamplePrompt(turn),
              violationExample: getTurnViolationExample(turn, policyName),
            })),
          };
        }),
      );
    }),

  generatePolicyBeginnerSummary: protectedProjectProcedure
    .input(GeneratePolicyBeginnerSummaryInputSchema)
    .output(GeneratePolicyBeginnerSummaryOutputSchema)
    .mutation(async ({ input, ctx }) => {
      const language = getLanguageFromCookieHeader(ctx.headers.cookie);
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:read",
      });

      const user = ctx.session?.user;
      if (!user) {
        throw new TRPCError({
          code: "UNAUTHORIZED",
          message: "Please sign in to generate beginner summaries.",
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
            message: "User does not have access to run LLM policy summaries.",
          });
        }
      }

      const project = await ctx.prisma.project.findUnique({
        where: { id: input.projectId, orgId: ctx.session.orgId },
        select: { metadata: true },
      });
      if (!project) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Project not found",
        });
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

      const parsedKey = LLMApiKeySchema.safeParse(llmApiKey);
      if (!parsedKey.success) {
        logger.warn("Failed to parse LLM API key for policy beginner summary", {
          projectId: input.projectId,
          policyName: input.policyName,
          error: parsedKey.error.message,
        });
        throw new TRPCError({
          code: "INTERNAL_SERVER_ERROR",
          message: "Could not parse LLM connection configuration.",
        });
      }

      const configuredModel = resolveErrorAnalysisModelFromMetadata(
        project.metadata,
      );
      const modelName =
        parsedKey.data.baseURL &&
        !parsedKey.data.baseURL.includes("api.openai.com") &&
        configuredModel === "gpt-5.2"
          ? "gpt-5.2"
          : resolveDemoOpenAIModel(configuredModel);
      const hasRecentPolicySignals =
        (input.guideInsight?.recentViolationCount ?? 0) > 0 ||
        (input.confirmationSummary?.rejectedCount ?? 0) > 0 ||
        Boolean(input.guideInsight?.exampleBlockedAction) ||
        (input.guideInsight?.similarCases.length ?? 0) > 0;

      const payload = {
        policyName: input.policyName,
        enabled: input.enabled,
        description: input.description,
        settingSections: input.settingSections,
        settingsBySection: input.settingsBySection,
        highlightThresholdPct: input.highlightThresholdPct,
        suggestPolicyUpdate: input.suggestPolicyUpdate,
        confirmationSummary: input.confirmationSummary,
        guideInsight: input.guideInsight,
        hasRecentPolicySignals,
        modelSource: {
          llmConnectionProvider: parsedKey.data.provider,
          llmConnectionBaseUrl: parsedKey.data.baseURL ?? null,
          selectedModel: modelName,
        },
      };

      const messages: ChatMessage[] = [
        {
          type: ChatMessageType.System,
          role: ChatMessageRole.System,
          content:
            "You are writing a friendly policy guide for a beginner with no programming background. Explain one policy in plain, everyday language that a non-technical person can understand. Avoid code terms, product jargon, and policy-system jargon when possible. If you must use a technical term from the payload, immediately explain it in simple words. Keep the tone concrete and practical. Mention what the policy is trying to protect, what the current settings mean today, and the practical effect in everyday use. Prefer short sentences and simple wording such as 'this rule helps prevent...', 'right now the system will...', or 'a user may need to...'. Avoid template lead-ins such as 'For a normal user, this means', 'What this means for a normal user', or similar phrasing; write direct statements instead.\n\nWhen `hasRecentPolicySignals` is true, do not rely on counts alone. Prefer 1-2 concrete recent examples from `guideInsight.exampleViolation` or `guideInsight.similarCases[].violationExample`. These raw violation examples are the best evidence because they may include the actual observation message, input, output, error type, and `policyNames`. If a violation example contains a file path, command, or request, quote that exact detail and explain in simple terms why it was rejected or blocked. For example, explain it like 'Reading `/path/to/file` was rejected because...' instead of only saying there were 2 blocked attempts. Use `guideInsight.exampleBlockedAction` or `guideInsight.examplePrompt` only as backup context when the violation example is missing details. Counts and reject rates may be mentioned as supporting context, but never as the main point when concrete examples exist.\n\nImportant: do not assume the currently selected `policyName` caused every recent example. If a concrete recent example's `policyNames` points more directly to another policy, explicitly say that the example appears to be enforced by that other policy and name it. In that case, tell the user to review that policy instead of implying they should change the current one. For example, if the example is really about workflow permissioning, say to review `EfsmGatePolicy`; if it is really about allow/deny rules, say to review `AllowDenyPolicy`. Only recommend changing the current policy when the evidence actually matches it.\n\nDo not invent examples, reasons, paths, settings, or policy names that are not present in the payload. If the payload does not contain the exact reason, say that the example was blocked under the current rule set and explain the likely reason only by referring back to the current settings shown in the payload. Do not tell the user to reply, ask a follow-up question, share more text, or continue the conversation. In particular, avoid assistant-style advice such as 'share only the specific text you need help with.' This summary is informational only.\n\nOnly mention recent signals when `hasRecentPolicySignals` is true and the payload includes actual violations, rejected confirmations, blocked actions, or similar cases. When `hasRecentPolicySignals` is false, do not add a 'recent signals' sentence, do not mention zero counts, and do not speculate about what would happen if the policy started triggering in the future.\n\nOnly if `suggestPolicyUpdate` is true, and the recent evidence suggests the policy may be too strict or mismatched to user intent, you may add one short final sentence recommending `LLM Suggest Update` in the advanced editor. If you do, explicitly remind the user to double-check carefully because policy changes are high-risk. Do not mention `LLM Suggest Update` when `suggestPolicyUpdate` is false.\n\nDo not mention internal prompt engineering, tokens, schemas, JSON structure, or implementation details unless the policy truly depends on them. Return ONLY valid JSON with a single `summary` string.",
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
            max_tokens: 500,
          },
          streaming: false,
          structuredOutputSchema: PolicyBeginnerSummaryStructuredOutputSchema,
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
              max_tokens: 500,
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

      const parsedSummary = parsePolicyBeginnerSummaryResult(rawResult);
      const existingSettings = parsePolicyGovernanceSettings(project.metadata);
      const projectMetadata =
        project.metadata &&
        typeof project.metadata === "object" &&
        !Array.isArray(project.metadata)
          ? (project.metadata as Record<string, unknown>)
          : {};

      await ctx.prisma.project.update({
        where: { id: input.projectId, orgId: ctx.session.orgId },
        data: {
          metadata: {
            ...projectMetadata,
            policyGovernance: {
              ...existingSettings,
              beginnerSummaries: {
                ...existingSettings.beginnerSummaries,
                [input.policyName]: parsedSummary.summary,
              },
            },
          } as any,
        },
      });

      return parsedSummary;
    }),

  generatePolicyUpdateProposal: protectedProjectProcedure
    .input(GeneratePolicyUpdateProposalInputSchema)
    .output(GeneratePolicyUpdateProposalOutputSchema)
    .mutation(async ({ input, ctx }) => {
      const language = getLanguageFromCookieHeader(ctx.headers.cookie);
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:update",
      });

      const user = ctx.session?.user;
      if (!user) {
        throw new TRPCError({
          code: "UNAUTHORIZED",
          message: "Please sign in to generate policy update suggestions.",
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
            message:
              "User does not have access to run LLM policy update suggestions.",
          });
        }
      }

      const project = await ctx.prisma.project.findUnique({
        where: { id: input.projectId, orgId: ctx.session.orgId },
        select: { metadata: true },
      });
      if (!project) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Project not found",
        });
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

      const parsedKey = LLMApiKeySchema.safeParse(llmApiKey);
      if (!parsedKey.success) {
        logger.warn(
          "Failed to parse LLM API key for policy governance proposal",
          {
            projectId: input.projectId,
            policyName: input.policyName,
            error: parsedKey.error.message,
          },
        );
        throw new TRPCError({
          code: "INTERNAL_SERVER_ERROR",
          message: "Could not parse LLM connection configuration.",
        });
      }

      const targetEntry = input.policyRegistryJson.find(
        (entry) => entry.name === input.policyName,
      );
      if (!targetEntry) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: `Policy "${input.policyName}" is not present in policy_registry.json.`,
        });
      }

      const editableSections = POLICY_SECTION_MAP[input.policyName] ?? [];
      const currentSections = Object.fromEntries(
        editableSections.map((section) => [
          section,
          input.policyJson[section] ?? null,
        ]),
      );

      const payload = {
        policyName: input.policyName,
        currentPolicyJson: input.policyJson,
        currentPolicyRegistryJson: input.policyRegistryJson,
        currentRegistryEntry: targetEntry,
        editableSections,
        currentSectionValues: currentSections,
        suggestion: input.suggestion,
        constraints: {
          mustStayFocusedOnTargetPolicy: true,
          preserveOtherPolicies: true,
          preserveUnrelatedConfig: true,
        },
      };

      const configuredModel = resolveErrorAnalysisModelFromMetadata(
        project.metadata,
      );
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
            "You are a policy refactoring assistant. Propose a safe, minimal update for exactly one policy. The goal is to implement a change that better matches the user's demonstrated preferences from past rejected confirmations and reduces future reject rate for the same intent, while preserving core safety constraints. Return ONLY valid JSON matching the schema with summary and proposedPolicyJson (the full updated policy.json object). Do not propose any changes to policy_registry.json. Keep changes scoped to the selected policy's listed editable config sections only, and preserve all unrelated keys exactly.",
        },
        {
          type: ChatMessageType.User,
          role: ChatMessageRole.User,
          content: JSON.stringify(payload),
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
            max_tokens: POLICY_UPDATE_PROPOSAL_MAX_TOKENS,
          },
          streaming: false,
          structuredOutputSchema: PolicyUpdateProposalStructuredOutputSchema,
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
              max_tokens: POLICY_UPDATE_PROPOSAL_MAX_TOKENS,
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

      const proposal = parseProposalResult(rawResult);
      const proposedPolicyJson = {
        ...input.policyJson,
      } as Record<string, unknown>;
      for (const section of editableSections) {
        if (
          Object.prototype.hasOwnProperty.call(
            proposal.proposedPolicyJson,
            section,
          )
        ) {
          proposedPolicyJson[section] = proposal.proposedPolicyJson[section];
        }
      }
      const proposedPolicyRegistryJson = input.policyRegistryJson;
      const appliedSections = editableSections.filter(
        (section) =>
          JSON.stringify(proposedPolicyJson[section] ?? null) !==
          JSON.stringify(input.policyJson[section] ?? null),
      );

      return {
        policyName: input.policyName,
        summary: proposal.summary,
        appliedSections,
        proposedPolicyJson,
        proposedPolicyRegistryJson,
      };
    }),
});
