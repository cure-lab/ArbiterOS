import { z } from "zod/v4";
import { z as zodV3 } from "zod/v3";

export const ErrorAnalysisModelSchema = z.enum(["gpt-5.2", "gpt-4.1"]);
export type ErrorAnalysisModel = z.infer<typeof ErrorAnalysisModelSchema>;

export const ERROR_TYPE_KEYS = [
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

export type ErrorTypeKey = (typeof ERROR_TYPE_KEYS)[number];

export const UNCLASSIFIED_ERROR_TYPE_FILTER_VALUE = "pending_to_analysis";
export const UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL_EN = "unclassified";
export const UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL = "未分类";

export const ERROR_TYPE_CATALOG: Record<ErrorTypeKey, { description: string }> =
  {
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
      description:
        "The provider returned rate limiting / throttling (HTTP 429).",
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

export const ERROR_TYPE_CHOICES = [...ERROR_TYPE_KEYS, "OTHER"] as const;
export type ErrorTypeChoice = (typeof ERROR_TYPE_CHOICES)[number];

export const ErrorTypeChoiceSchema = z.enum(ERROR_TYPE_CHOICES);
export const ErrorTypeStructuredOutputSchema = zodV3.object({
  selectedType: zodV3
    .enum(ERROR_TYPE_CHOICES)
    .describe(
      "Select the closest type from the provided list. Use OTHER only if none match well.",
    ),
  otherTypeLabel: zodV3
    .string()
    .optional()
    .describe(
      "Short label for the OTHER type (required when selectedType=OTHER).",
    ),
  otherTypeDescription: zodV3
    .string()
    .optional()
    .describe(
      "Brief description for the OTHER type (required when selectedType=OTHER).",
    ),
  why: zodV3
    .string()
    .describe(
      "One sentence explaining why this error/warning happened (root reason, e.g. schema/tool mismatch).",
    ),
  confidence: zodV3
    .number()
    .min(0)
    .max(1)
    .describe("Confidence score from 0.0 (low) to 1.0 (high)."),
});

export const ErrorTypeClassificationResultSchema = z.object({
  selectedType: ErrorTypeChoiceSchema,
  otherTypeLabel: z.string().nullish(),
  otherTypeDescription: z.string().nullish(),
  why: z.string(),
  confidence: z.number().min(0).max(1),
});
export type ErrorTypeClassificationResult = z.infer<
  typeof ErrorTypeClassificationResultSchema
>;

/**
 * Input schema for the error analysis mutation.
 *
 * Note: `traceId`, `projectId`, `timestamp`, `fromTimestamp`, and `verbosity`
 * are also used by `protectedGetTraceProcedure` middleware for access control
 * and ClickHouse query efficiency.
 */
export const ErrorAnalysisAnalyzeInputSchema = z.object({
  // Required by protectedGetTraceProcedure
  traceId: z.string(),
  projectId: z.string(),
  timestamp: z.date().nullish(),
  fromTimestamp: z.date().nullish(),
  verbosity: z.enum(["compact", "truncated", "full"]).default("full"),

  // Feature-specific
  observationId: z.string(),
  model: ErrorAnalysisModelSchema,
  maxContextChars: z.number().int().positive().max(500_000).default(80_000),
});

export type ErrorAnalysisAnalyzeInput = z.infer<
  typeof ErrorAnalysisAnalyzeInputSchema
>;

/**
 * Raw LLM output (validated after the provider returns structured JSON).
 * Keep this focused on actionable prevention + root cause + confidence.
 */
export const ErrorAnalysisLLMResultSchema = z.object({
  rootCause: z.string(),
  resolveNow: z
    .array(z.string())
    .describe("How to resolve/mitigate this issue right now (may be empty)."),
  preventionNextCall: z.array(z.string()),
  relevantObservations: z.array(z.string()),
  contextSufficient: z
    .boolean()
    .describe(
      "Whether the provided context is sufficient to support the conclusion.",
    ),
  confidence: z.number().min(0).max(1),
});

export type ErrorAnalysisLLMResult = z.infer<
  typeof ErrorAnalysisLLMResultSchema
>;

/**
 * Rendered result shown in the UI (includes deterministic context fields).
 */
export const ErrorAnalysisRenderedResultSchema =
  ErrorAnalysisLLMResultSchema.extend({
    issue: z
      .string()
      .describe(
        "Concatenation of observation name, ERROR/WARNING tag, and error info.",
      ),
    errorType: z
      .string()
      .nullable()
      .describe("Short stable key identifying the error/warning type."),
    errorTypeDescription: z
      .string()
      .nullable()
      .describe("Short description of the error/warning type."),
    errorTypeWhy: z
      .string()
      .nullable()
      .describe("One-sentence explanation of why the issue happened."),
    errorTypeConfidence: z
      .number()
      .min(0)
      .max(1)
      .nullable()
      .describe("Confidence for the error/warning type classification (0-1)."),
    errorTypeFromList: z
      .boolean()
      .nullable()
      .describe("Whether the type was selected from the predefined catalog."),
  });

export type ErrorAnalysisRenderedResult = z.infer<
  typeof ErrorAnalysisRenderedResultSchema
>;

/**
 * API output for the analyze endpoint.
 * - `rendered`: enriched result used for the “Formatted” view
 * - `original`: validated LLM output used for the “JSON” view
 */
export const ErrorAnalysisAnalyzeOutputSchema = z.object({
  rendered: ErrorAnalysisRenderedResultSchema,
  original: ErrorAnalysisLLMResultSchema,
});

export type ErrorAnalysisAnalyzeOutput = z.infer<
  typeof ErrorAnalysisAnalyzeOutputSchema
>;

/**
 * Zod v3 schema used for OpenAI structured outputs.
 * We intentionally use Zod v3 due to provider quirks (see shared fetchLLMCompletion.ts).
 */
export const ErrorAnalysisStructuredOutputSchema = zodV3.object({
  rootCause: zodV3
    .string()
    .describe("Most likely root cause of the error/warning in this trace."),
  resolveNow: zodV3
    .array(zodV3.string())
    .describe(
      "How to resolve/mitigate this issue right now (may be empty if not applicable).",
    ),
  preventionNextCall: zodV3
    .array(zodV3.string())
    .describe(
      "Concrete changes the user can apply to prevent this error/warning in the next call (e.g., validation, guardrails, prompt changes, retries).",
    ),
  relevantObservations: zodV3
    .array(zodV3.string())
    .describe("Observation ids most relevant to the root cause."),
  contextSufficient: zodV3
    .boolean()
    .describe(
      "Whether the provided trace context is sufficient to support the conclusion.",
    ),
  confidence: zodV3
    .number()
    .min(0)
    .max(1)
    .describe("Confidence score from 0.0 (low) to 1.0 (high)."),
});

export function resolveDemoOpenAIModel(model: ErrorAnalysisModel): string {
  // Use a stable, concrete model name for gpt-5.2.
  return model === "gpt-5.2" ? "gpt-5.2-2025-12-11" : "gpt-4.1";
}
