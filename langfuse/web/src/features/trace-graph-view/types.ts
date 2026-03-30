import { z } from "zod/v4";

export type TraceGraphMode = "execution" | "hierarchy";

export type GraphNodeMetadataSummary = {
  topic?: string | null;
  core?: string | null;
  category?: string | null;
  instructionType?: string | null;
  instructionCategory?: string | null;
  observationCount?: number;
  toolCount?: number;
  toolBreakdown?: Array<{
    toolName: string;
    count: number;
    instructionTypes?: string[] | null;
    hasBlock?: boolean | null;
  }>;
  errorCount?: number;
  warningCount?: number;
  policyViolationCount?: number;
  parserInconsistencyCount?: number;
  durationMs?: number | null;
  policy?: {
    authorityLabel?: string | null;
    confidentiality?: string | null;
    integrity?: string | null;
    trustworthiness?: string | null;
    confidence?: number | null;
    reversible?: boolean | null;
    confidentialityLabel?: boolean | null;
    hasBlock?: boolean | null;
    ruleEffectCounts?: Record<string, number> | null;
    inferredFromInstruction?: boolean | null;
  } | null;
};

export type GraphNodeData = {
  id: string;
  label: string;
  type: string;
  title?: string;
  level?: string | null;
  metadataSummary?: GraphNodeMetadataSummary;
};

export type GraphCanvasData = {
  nodes: GraphNodeData[];
  edges: { from: string; to: string }[];
};

export const LANGGRAPH_NODE_TAG = "langgraph_node";
export const LANGGRAPH_STEP_TAG = "langgraph_step";
export const LANGGRAPH_START_NODE_NAME = "__start__";
export const LANGGRAPH_END_NODE_NAME = "__end__";
export const LANGFUSE_START_NODE_NAME = "__start__";
export const LANGFUSE_END_NODE_NAME = "__end__";

export const LanggraphMetadataSchema = z.object({
  [LANGGRAPH_NODE_TAG]: z.string(),
  [LANGGRAPH_STEP_TAG]: z.number(),
});

const NullableBooleanFromMixedSchema = z
  .union([z.boolean(), z.string(), z.number()])
  .nullish()
  .transform((value) => {
    if (value === null || value === undefined) return null;
    if (typeof value === "boolean") return value;
    if (typeof value === "number") return value !== 0;
    const normalized = value.trim().toLowerCase();
    if (normalized === "true" || normalized === "1") return true;
    if (normalized === "false" || normalized === "0") return false;
    return null;
  });

export const AgentGraphDataSchema = z.object({
  id: z.string(),
  parent_observation_id: z.string().nullish(),
  type: z.string(),
  name: z.string(),
  start_time: z.string(),
  end_time: z.string().nullish(),
  level: z.string().nullish(),
  status_message: z.string().nullish(),
  node: z.string().nullish(),
  step: z.coerce.number().nullish(),
  category: z.string().nullish(),
  parser_stage: z.string().nullish(),
  turn_index: z.coerce.number().nullish(),
  tool_name: z.string().nullish(),
  instruction_count: z.coerce.number().nullish(),
  trace_id_consistent: NullableBooleanFromMixedSchema,
});

export type AgentGraphDataResponse = {
  id: string;
  node: string | null; // langgraph_node
  step: number | null;
  parentObservationId: string | null;
  name: string; // span name
  startTime: string;
  endTime?: string;
  observationType: string;
  level?: string | null;
  statusMessage?: string | null;
  category?: string | null;
  parserStage?: string | null;
  turnIndex?: number | null;
  toolName?: string | null;
  instructionCount?: number | null;
  traceIdConsistent?: boolean | null;
};
