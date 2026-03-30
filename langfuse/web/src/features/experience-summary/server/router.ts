import { TRPCError } from "@trpc/server";
import { z } from "zod/v4";
import { mkdir, readFile, writeFile } from "fs/promises";
import { dirname, isAbsolute } from "path";
import {
  protectedProjectProcedure,
  createTRPCRouter,
} from "@/src/server/api/trpc";
import { throwIfNoProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import {
  ChatMessageRole,
  ChatMessageType,
  LLMAdapter,
  LLMApiKeySchema,
  type ChatMessage,
} from "@langfuse/shared";
import { fetchLLMCompletion, logger } from "@langfuse/shared/src/server";
import {
  buildExperienceSummaryHintSectionContent,
  type ExperienceSummaryMarkdownOutputMode,
  upsertProjectHintSectionInMarkdown,
} from "@langfuse/shared/src/utils/experienceSummaryHintMarkdown";
import {
  ExperienceSummaryJsonSchema,
  ExperienceSummaryMarkdownOutputModeSchema,
  ExperienceSummaryModeSchema,
  ExperienceSummaryModelSchema,
  ExperienceSummaryStructuredOutputSchema,
  type ExperienceSummaryJson,
} from "../types";
import { rewriteAbsolutePathFromPrefixMappings } from "@/src/features/file-paths/server/absolutePathPrefixMap";
import {
  applyLanguageInstructionToMessages,
  getLanguageFromCookieHeader,
} from "@/src/features/i18n/server";

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
      // Limit what we send to the model; we merge deterministically server-side.
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
    schemaVersion: 1,
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

function resolveDemoOpenAIModel(
  model: z.infer<typeof ExperienceSummaryModelSchema>,
) {
  return model === "gpt-5.2" ? "gpt-5.2-2025-12-11" : "gpt-4.1";
}

const AutoErrorAnalysisSummarySettingsSchema = z
  .object({
    enabled: z.boolean().default(false),
    summaryAppendMarkdownAbsolutePath: z
      .string()
      .trim()
      .min(1)
      .nullable()
      .default(null),
    summaryMarkdownOutputMode:
      ExperienceSummaryMarkdownOutputModeSchema.default("prompt_pack_only"),
  })
  .passthrough();

const ExperienceSummaryGetInputSchema = z.object({
  projectId: z.string(),
});

const ExperienceSummaryIncrementalUpdateStatusSchema = z.object({
  cursorUpdatedAt: z.date().nullable(),
  pendingAnalysesCount: z.number().int().min(0),
});

const ExperienceSummaryRowSchema = z.object({
  projectId: z.string(),
  model: z.string(),
  schemaVersion: z.number().int().positive(),
  summary: ExperienceSummaryJsonSchema,
  cursorUpdatedAt: z.date().nullable(),
  updatedAt: z.date(),
});

const ExperienceSummaryGenerateInputSchema = z.object({
  projectId: z.string(),
  mode: ExperienceSummaryModeSchema.default("incremental"),
  model: ExperienceSummaryModelSchema.default("gpt-5.2"),
  maxItems: z.number().int().positive().max(500).default(50),
});

const ExperienceSummaryGenerateOutputSchema = z.object({
  updated: z.boolean(),
  row: ExperienceSummaryRowSchema.nullable(),
});

const ExperienceSummaryUpdateInputSchema = z.object({
  projectId: z.string(),
  summary: ExperienceSummaryJsonSchema,
});

const ExperienceSummaryWriteMarkdownInputSchema = z.object({
  projectId: z.string(),
});

const ExperienceSummaryWriteMarkdownOutputSchema = z.object({
  written: z.boolean(),
  path: z.string(),
});

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

function resolveSummaryMarkdownConfig(
  metadata: unknown,
  options?: { requireEnabled?: boolean },
): {
  absolutePath: string | null;
  outputMode: ExperienceSummaryMarkdownOutputMode;
} {
  const requireEnabled = options?.requireEnabled ?? true;
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return {
      absolutePath: null,
      outputMode: "prompt_pack_only",
    };
  }

  const parsed = AutoErrorAnalysisSummarySettingsSchema.safeParse(
    (metadata as Record<string, unknown>).autoErrorAnalysis,
  );
  if (!parsed.success) {
    return {
      absolutePath: null,
      outputMode: "prompt_pack_only",
    };
  }

  const outputMode = parsed.data.summaryMarkdownOutputMode;

  if (requireEnabled && parsed.data.enabled !== true) {
    return {
      absolutePath: null,
      outputMode,
    };
  }

  const pathFromSettings = parsed.data.summaryAppendMarkdownAbsolutePath;
  if (
    !pathFromSettings ||
    !isAbsolute(pathFromSettings) ||
    !pathFromSettings.toLowerCase().endsWith(".md")
  ) {
    return {
      absolutePath: null,
      outputMode,
    };
  }

  return {
    absolutePath: rewriteAbsolutePathFromPrefixMappings(pathFromSettings),
    outputMode,
  };
}

async function replaceSummaryHintSectionInMarkdown(params: {
  projectId: string;
  absolutePath: string;
  summary: z.infer<typeof ExperienceSummaryJsonSchema>;
  outputMode: ExperienceSummaryMarkdownOutputMode;
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

function compactErrorAnalysisRow(row: any): ErrorAnalysisCompact {
  return {
    observationId: String(row.observationId),
    traceId: String(row.traceId),
    updatedAt: row.updatedAt as Date,
    errorType: (row.errorType ?? null) as string | null,
    errorTypeWhy: (row.errorTypeWhy ?? null) as string | null,
    rootCause: truncateString(String(row.rootCause ?? ""), 1_200),
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

function buildUserPayload(params: {
  previousSummary: unknown | null;
  newAnalyses: ErrorAnalysisCompact[];
  mode: z.infer<typeof ExperienceSummaryModeSchema>;
}) {
  const previousParsed = parseExperienceSummary(params.previousSummary);
  const isIncremental = params.mode === "incremental" && previousParsed;

  return {
    ...(isIncremental
      ? buildExistingSummaryInfo(previousParsed)
      : { previousSummary: params.previousSummary }),
    newAnalyses: params.newAnalyses,
    instruction: [
      "You are creating an 'experience summary' to reduce recurrence of LLM pipeline errors/warnings.",
      "You MUST output ONLY the JSON object that matches the provided schema.",
      ...(isIncremental
        ? [
            "You are NOT given the full previous summary; only existingSummaryKeys and existingPromptPack are provided.",
            "Return a DELTA summary: include only new or updated experience items inferred from newAnalyses, and any new promptPack lines.",
            "It is OK to omit unchanged existing experiences; the server will merge your output with the stored summary.",
            "Keep keys stable and snake_case; dedupe by key.",
            "Use existingPromptPack.title as the promptPack.title.",
          ]
        : [
            "Merge with previousSummary when present. Keep keys stable and snake_case; dedupe by key.",
          ]),
      "Each experience item should be written as: when -> possibleProblems -> avoidanceAndNotes -> promptAdditions.",
      "When possible, add concise `keywords` (2-8 tokens) for each experience item to help retrieval/ranking.",
      "Keep entries concise and directly useful for preventing recurrence.",
      "promptAdditions should be directly pasteable lines for a user's prompt (guardrails/checklist), and should be generic/reusable.",
      "Ensure promptPack.lines reflects the highest-signal reusable guardrails implied by the included experiences, deduplicated and ordered by priority.",
      "Prefer actionable, specific, and generalizable advice over vague statements.",
      "Exclude non-prompt or implementation-heavy suggestions: code/config/system changes, retries/backoff/circuit breakers, scheduler or long-running behavior changes, model/provider/account changes.",
      "Avoid hardcoded operational playbooks unless explicitly required by repeated evidence in newAnalyses.",
      "For blocked/forbidden/unauthorized/rate-limit cases, include the identifiable target (domain/URL/host) and tool/provider/adapter from newAnalyses when present; do not fabricate missing identifiers (use unknown).",
    ].join("\n"),
  };
}

export const experienceSummaryRouter = createTRPCRouter({
  get: protectedProjectProcedure
    .input(ExperienceSummaryGetInputSchema)
    .output(ExperienceSummaryRowSchema.nullable())
    .query(async ({ input, ctx }) => {
      const delegate = (ctx.prisma as any).experienceSummary as
        | typeof ctx.prisma.experienceSummary
        | undefined;
      if (!delegate) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "Server is missing the ExperienceSummary Prisma model. Please restart the dev server after running prisma generate/migrate.",
        });
      }

      const row = await ctx.prisma.experienceSummary.findUnique({
        where: { projectId: input.projectId },
      });
      if (!row) return null;

      const parsed = ExperienceSummaryJsonSchema.safeParse(row.summary);
      if (!parsed.success) {
        logger.warn("Stored experience summary failed schema validation", {
          projectId: input.projectId,
          error: parsed.error.message,
        });
        throw new TRPCError({
          code: "INTERNAL_SERVER_ERROR",
          message: "Stored experience summary is invalid (schema mismatch).",
        });
      }

      return {
        projectId: row.projectId,
        model: row.model,
        schemaVersion: row.schemaVersion,
        summary: parsed.data,
        cursorUpdatedAt: row.cursorUpdatedAt,
        updatedAt: row.updatedAt,
      };
    }),

  getIncrementalUpdateStatus: protectedProjectProcedure
    .input(ExperienceSummaryGetInputSchema)
    .output(ExperienceSummaryIncrementalUpdateStatusSchema)
    .query(async ({ input, ctx }) => {
      const existing = await ctx.prisma.experienceSummary.findUnique({
        where: { projectId: input.projectId },
        select: { cursorUpdatedAt: true },
      });

      const cursorUpdatedAt = existing?.cursorUpdatedAt ?? null;
      const pendingAnalysesCount = await ctx.prisma.errorAnalysis.count({
        where: {
          projectId: input.projectId,
          ...(cursorUpdatedAt ? { updatedAt: { gt: cursorUpdatedAt } } : {}),
        },
      });

      return {
        cursorUpdatedAt,
        pendingAnalysesCount,
      };
    }),

  generate: protectedProjectProcedure
    .input(ExperienceSummaryGenerateInputSchema)
    .output(ExperienceSummaryGenerateOutputSchema)
    .mutation(async ({ input, ctx }) => {
      const language = getLanguageFromCookieHeader(ctx.headers.cookie);
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "llmApiKeys:read",
        forbiddenErrorMessage:
          "User does not have access to run LLM analysis (missing llmApiKeys:read).",
      });

      const delegate = (ctx.prisma as any).experienceSummary as
        | typeof ctx.prisma.experienceSummary
        | undefined;
      if (!delegate) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "Server is missing the ExperienceSummary Prisma model. Please restart the dev server after running prisma generate/migrate.",
        });
      }

      const existing = await ctx.prisma.experienceSummary.findUnique({
        where: { projectId: input.projectId },
      });

      const llmApiKey = await ctx.prisma.llmApiKeys.findFirst({
        where: { projectId: input.projectId, adapter: LLMAdapter.OpenAI },
      });
      if (!llmApiKey) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "No OpenAI-adapter LLM connection configured. Please add one in Settings → LLM Connections.",
        });
      }

      const parsedKey = LLMApiKeySchema.safeParse(llmApiKey);
      if (!parsedKey.success) {
        throw new TRPCError({
          code: "INTERNAL_SERVER_ERROR",
          message: "Could not parse LLM connection configuration.",
        });
      }

      const cursor =
        input.mode === "incremental"
          ? (existing?.cursorUpdatedAt ?? null)
          : null;

      const newRows = await ctx.prisma.errorAnalysis.findMany({
        where: {
          projectId: input.projectId,
          ...(cursor ? { updatedAt: { gt: cursor } } : {}),
        },
        orderBy: { updatedAt: "asc" },
        take: input.maxItems,
      });

      if (input.mode === "incremental" && existing && newRows.length === 0) {
        const parsed = ExperienceSummaryJsonSchema.safeParse(existing.summary);
        if (!parsed.success) {
          throw new TRPCError({
            code: "INTERNAL_SERVER_ERROR",
            message: "Stored experience summary is invalid (schema mismatch).",
          });
        }
        return {
          updated: false,
          row: {
            projectId: existing.projectId,
            model: existing.model,
            schemaVersion: existing.schemaVersion,
            summary: parsed.data,
            cursorUpdatedAt: existing.cursorUpdatedAt,
            updatedAt: existing.updatedAt,
          },
        };
      }

      if (!existing && newRows.length === 0) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "No ErrorAnalysis records found yet. Run Analyze on some ERROR/WARNING observations first.",
        });
      }

      const previousSummary = existing?.summary ?? null;
      const newAnalyses = compactAnalysisPayloadRows(
        newRows.map(compactErrorAnalysisRow),
      );

      const messages: ChatMessage[] = [
        {
          type: ChatMessageType.System,
          role: ChatMessageRole.System,
          content:
            "You are an expert at preventing recurring LLM pipeline errors. Keep output concise and focused on what can be changed in prompts for future LLM calls. Exclude implementation-heavy proposals (code/config/system changes, retries/backoff/circuit breakers, scheduler/long-running behavior changes, model/provider/account changes). Return ONLY the structured JSON object that matches the provided schema.",
        },
        {
          type: ChatMessageType.User,
          role: ChatMessageRole.User,
          content: safeStringify(
            buildUserPayload({
              previousSummary,
              newAnalyses,
              mode: input.mode,
            }),
          ),
        },
      ];

      const modelName =
        parsedKey.data.baseURL &&
        !parsedKey.data.baseURL.includes("api.openai.com") &&
        input.model === "gpt-5.2"
          ? "gpt-5.2"
          : resolveDemoOpenAIModel(input.model);

      let raw: unknown;
      try {
        raw = await fetchLLMCompletion({
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
            max_tokens: 3000,
          },
          streaming: false,
          structuredOutputSchema: ExperienceSummaryStructuredOutputSchema,
        });
      } catch (e) {
        logger.warn("Experience summary LLM request failed", {
          projectId: input.projectId,
          message: e instanceof Error ? e.message : String(e),
        });
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "LLM request failed while generating experience summary. Check Settings → LLM Connections and retry.",
        });
      }

      const validated = ExperienceSummaryJsonSchema.safeParse(raw);
      if (!validated.success) {
        logger.warn(
          "LLM returned invalid structured output for experience summary",
          {
            projectId: input.projectId,
            error: validated.error.message,
          },
        );
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "LLM returned an invalid summary payload (not matching expected schema). " +
            validated.error.message,
        });
      }

      const previousParsed =
        input.mode === "incremental"
          ? parseExperienceSummary(existing?.summary ?? null)
          : null;
      const merged =
        input.mode === "incremental" && previousParsed
          ? mergeExperienceSummaries({
              previous: previousParsed,
              delta: validated.data,
            })
          : validated.data;
      const normalizedMerged = ensurePromptPackCoverage(merged);

      const maxUpdatedAt =
        newRows.length > 0
          ? newRows.reduce<Date>((acc, r) => {
              const t = r.updatedAt;
              return t > acc ? t : acc;
            }, newRows[0]!.updatedAt)
          : (existing?.cursorUpdatedAt ?? null);

      const saved = await ctx.prisma.experienceSummary.upsert({
        where: { projectId: input.projectId },
        create: {
          projectId: input.projectId,
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

      const projectForSettings = await ctx.prisma.project.findUnique({
        where: { id: input.projectId },
        select: { metadata: true },
      });
      const summaryMarkdownConfig = resolveSummaryMarkdownConfig(
        projectForSettings?.metadata,
      );
      if (summaryMarkdownConfig.absolutePath) {
        try {
          await replaceSummaryHintSectionInMarkdown({
            projectId: input.projectId,
            absolutePath: summaryMarkdownConfig.absolutePath,
            summary: normalizedMerged,
            outputMode: summaryMarkdownConfig.outputMode,
          });
        } catch (error) {
          logger.warn(
            "Failed to replace summary hint section in markdown file",
            {
              projectId: input.projectId,
              path: summaryMarkdownConfig.absolutePath,
              error: error instanceof Error ? error.message : String(error),
            },
          );
        }
      }

      return {
        updated: true,
        row: {
          projectId: saved.projectId,
          model: saved.model,
          schemaVersion: saved.schemaVersion,
          summary: normalizedMerged,
          cursorUpdatedAt: saved.cursorUpdatedAt,
          updatedAt: saved.updatedAt,
        },
      };
    }),

  update: protectedProjectProcedure
    .input(ExperienceSummaryUpdateInputSchema)
    .output(ExperienceSummaryRowSchema)
    .mutation(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:update",
      });

      const delegate = (ctx.prisma as any).experienceSummary as
        | typeof ctx.prisma.experienceSummary
        | undefined;
      if (!delegate) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "Server is missing the ExperienceSummary Prisma model. Please restart the dev server after running prisma generate/migrate.",
        });
      }

      const existing = await ctx.prisma.experienceSummary.findUnique({
        where: { projectId: input.projectId },
        select: {
          model: true,
          cursorUpdatedAt: true,
        },
      });

      const normalizedSummary = ensurePromptPackCoverage(input.summary);
      const saved = await ctx.prisma.experienceSummary.upsert({
        where: { projectId: input.projectId },
        create: {
          projectId: input.projectId,
          model: existing?.model ?? "manual",
          schemaVersion: normalizedSummary.schemaVersion,
          summary: normalizedSummary as any,
          cursorUpdatedAt: existing?.cursorUpdatedAt ?? null,
        },
        update: {
          schemaVersion: normalizedSummary.schemaVersion,
          summary: normalizedSummary as any,
        },
      });

      const projectForSettings = await ctx.prisma.project.findUnique({
        where: { id: input.projectId },
        select: { metadata: true },
      });
      const summaryMarkdownConfig = resolveSummaryMarkdownConfig(
        projectForSettings?.metadata,
      );
      if (summaryMarkdownConfig.absolutePath) {
        try {
          await replaceSummaryHintSectionInMarkdown({
            projectId: input.projectId,
            absolutePath: summaryMarkdownConfig.absolutePath,
            summary: normalizedSummary,
            outputMode: summaryMarkdownConfig.outputMode,
          });
        } catch (error) {
          logger.warn(
            "Failed to replace summary hint section in markdown file after manual summary update",
            {
              projectId: input.projectId,
              path: summaryMarkdownConfig.absolutePath,
              error: error instanceof Error ? error.message : String(error),
            },
          );
        }
      }

      return {
        projectId: saved.projectId,
        model: saved.model,
        schemaVersion: saved.schemaVersion,
        summary: normalizedSummary,
        cursorUpdatedAt: saved.cursorUpdatedAt,
        updatedAt: saved.updatedAt,
      };
    }),

  writeMarkdown: protectedProjectProcedure
    .input(ExperienceSummaryWriteMarkdownInputSchema)
    .output(ExperienceSummaryWriteMarkdownOutputSchema)
    .mutation(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:update",
      });

      const delegate = (ctx.prisma as any).experienceSummary as
        | typeof ctx.prisma.experienceSummary
        | undefined;
      if (!delegate) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "Server is missing the ExperienceSummary Prisma model. Please restart the dev server after running prisma generate/migrate.",
        });
      }

      const projectForSettings = await ctx.prisma.project.findUnique({
        where: { id: input.projectId },
        select: { metadata: true },
      });
      const summaryMarkdownConfig = resolveSummaryMarkdownConfig(
        projectForSettings?.metadata,
        { requireEnabled: false },
      );

      if (!summaryMarkdownConfig.absolutePath) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "No markdown path configured. Set it in Settings → Error Analysis first.",
        });
      }

      const row = await ctx.prisma.experienceSummary.findUnique({
        where: { projectId: input.projectId },
        select: { summary: true },
      });
      const parsed = ExperienceSummaryJsonSchema.safeParse(row?.summary);
      if (!parsed.success) {
        throw new TRPCError({
          code: "PRECONDITION_FAILED",
          message:
            "No valid experience summary found. Generate or save a summary first.",
        });
      }

      try {
        await replaceSummaryHintSectionInMarkdown({
          projectId: input.projectId,
          absolutePath: summaryMarkdownConfig.absolutePath,
          summary: parsed.data,
          outputMode: summaryMarkdownConfig.outputMode,
        });
      } catch (error) {
        logger.warn("Failed to write summary hint section to markdown file", {
          projectId: input.projectId,
          path: summaryMarkdownConfig.absolutePath,
          error: error instanceof Error ? error.message : String(error),
        });
        throw new TRPCError({
          code: "INTERNAL_SERVER_ERROR",
          message:
            "Failed to write summary markdown. Check file permissions and path.",
        });
      }

      return {
        written: true,
        path: summaryMarkdownConfig.absolutePath,
      };
    }),
});
