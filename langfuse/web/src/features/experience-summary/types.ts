import { z } from "zod/v4";
import { z as zodV3 } from "zod/v3";

export const ExperienceSummaryModelSchema = z.enum(["gpt-5.2", "gpt-4.1"]);
export type ExperienceSummaryModel = z.infer<
  typeof ExperienceSummaryModelSchema
>;

export const ExperienceSummaryModeSchema = z.enum(["full", "incremental"]);
export type ExperienceSummaryMode = z.infer<typeof ExperienceSummaryModeSchema>;
export const ExperienceSummaryMarkdownOutputModeSchema = z.enum([
  "prompt_pack_only",
  "full",
]);
export type ExperienceSummaryMarkdownOutputMode = z.infer<
  typeof ExperienceSummaryMarkdownOutputModeSchema
>;

const ExperienceItemKeySchema = z
  .string()
  .min(1)
  .max(64)
  .regex(/^[a-z0-9_]+$/, "key must be snake_case (a-z, 0-9, _)"); // stable key for merge/dedupe

export const ExperienceItemSchema = z
  .object({
    key: ExperienceItemKeySchema,
    when: z.string().min(1).max(800),
    keywords: z.array(z.string().min(1).max(64)).max(20).nullish(),
    possibleProblems: z.array(z.string().min(1).max(400)).max(30),
    avoidanceAndNotes: z.array(z.string().min(1).max(500)).max(40),
    promptAdditions: z.array(z.string().min(1).max(300)).max(40),
    relatedErrorTypes: z.array(z.string().min(1).max(64)).max(20).nullish(),
  })
  .strict();

export type ExperienceItem = z.infer<typeof ExperienceItemSchema>;

export const ExperiencePromptPackSchema = z
  .object({
    title: z.string().min(1).max(120),
    lines: z.array(z.string().min(1).max(400)).max(200),
  })
  .strict();

export type ExperiencePromptPack = z.infer<typeof ExperiencePromptPackSchema>;

export const ExperienceSummarySchemaVersion = 1 as const;

export const ExperienceSummaryJsonSchema = z
  .object({
    schemaVersion: z.literal(ExperienceSummarySchemaVersion),
    experiences: z.array(ExperienceItemSchema).max(100),
    promptPack: ExperiencePromptPackSchema,
  })
  .strict();

export type ExperienceSummaryJson = z.infer<typeof ExperienceSummaryJsonSchema>;

/**
 * Zod v3 schema used for OpenAI structured outputs.
 * We intentionally use Zod v3 because our LLM structured output implementation is
 * standardized on Zod v3 for provider compatibility (see shared fetchLLMCompletion).
 */
export const ExperienceSummaryStructuredOutputSchema = zodV3
  .object({
    schemaVersion: zodV3
      .literal(ExperienceSummarySchemaVersion)
      .describe("Schema version. Must be 1."),
    experiences: zodV3
      .array(
        zodV3
          .object({
            key: zodV3
              .string()
              .describe(
                "Stable snake_case key for this experience item (used to merge/dedupe across updates).",
              ),
            when: zodV3
              .string()
              .describe("When this issue happens (or similar situations)."),
            keywords: zodV3
              .array(zodV3.string())
              .nullable()
              .describe(
                "Optional concise keywords for retrieval/ranking when selecting targeted experiences.",
              ),
            possibleProblems: zodV3
              .array(zodV3.string())
              .describe("Possible problems you may encounter."),
            avoidanceAndNotes: zodV3
              .array(zodV3.string())
              .describe("How to avoid it + important notes."),
            promptAdditions: zodV3
              .array(zodV3.string())
              .describe(
                "Prompt-ready constraints/checklist lines to reduce recurrence.",
              ),
            relatedErrorTypes: zodV3
              .array(zodV3.string())
              .nullable()
              .describe("Related errorType keys, if available."),
          })
          .strict(),
      )
      .describe("List of experience items."),
    promptPack: zodV3
      .object({
        title: zodV3
          .string()
          .describe("Title for the prompt pack users can paste into prompts."),
        lines: zodV3
          .array(zodV3.string())
          .describe("Prompt-ready lines (one per bullet/constraint)."),
      })
      .strict()
      .describe("Compact prompt pack for copy/paste."),
  })
  .strict();
