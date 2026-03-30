import { getMetadataRecord } from "@/src/features/governance/utils/policyMetadata";

export type PolicyConfirmationState = "ask" | "accepted" | "rejected";
export type HumanPolicyConfirmationState = Exclude<
  PolicyConfirmationState,
  "ask"
>;

const POLICY_CONFIRMATION_PROMPT_RE =
  /do you want to apply the protection\?\s*please reply yes\/no\.?$/i;
const NESTED_REPLY_KEYS = ["content", "raw_content", "parsed_content"] as const;
const METADATA_REPLY_KEYS = [
  "text_preview",
  "input_preview",
  "latest_user_preview",
] as const;

function parseNestedJson(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function isPolicyConfirmationPrompt(value: string): boolean {
  return POLICY_CONFIRMATION_PROMPT_RE.test(value.trim());
}

function hasReplyPayload(value: unknown, seen = new Set<unknown>()): boolean {
  if (value == null || seen.has(value)) {
    return false;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) {
      return false;
    }

    const nested = parseNestedJson(trimmed);
    if (nested != null) {
      return hasReplyPayload(nested, seen);
    }

    return !isPolicyConfirmationPrompt(trimmed);
  }
  if (typeof value !== "object") {
    return false;
  }

  seen.add(value);

  if (Array.isArray(value)) {
    return value.some((item) => hasReplyPayload(item, seen));
  }

  return NESTED_REPLY_KEYS.some((key) =>
    hasReplyPayload((value as Record<string, unknown>)[key], seen),
  );
}

export function getPolicyConfirmationState(
  metadata: unknown,
): PolicyConfirmationState | null {
  const record = getMetadataRecord(metadata);
  const rawState = record.policy_confirmation_state;
  if (typeof rawState !== "string") {
    return null;
  }

  const normalizedState = rawState.trim().toLowerCase();
  if (
    normalizedState === "ask" ||
    normalizedState === "accepted" ||
    normalizedState === "rejected"
  ) {
    return normalizedState;
  }

  return null;
}

export function hasHumanPolicyConfirmationReply(params: {
  observationInput?: unknown;
  traceInput?: unknown;
  observationMetadata?: unknown;
  traceMetadata?: unknown;
}): boolean {
  const observationMetadataRecord = getMetadataRecord(
    params.observationMetadata,
  );
  const traceMetadataRecord = getMetadataRecord(params.traceMetadata);
  const metadataCandidates = [
    ...METADATA_REPLY_KEYS.flatMap((key) => [
      observationMetadataRecord[key],
      traceMetadataRecord[key],
    ]),
  ];

  return [
    params.observationInput,
    params.traceInput,
    ...metadataCandidates,
  ].some((value) => hasReplyPayload(value));
}

export function getHumanPolicyConfirmationState(params: {
  metadata: unknown;
  observationInput?: unknown;
  traceInput?: unknown;
  traceMetadata?: unknown;
}): HumanPolicyConfirmationState | null {
  const state = getPolicyConfirmationState(params.metadata);
  if (state !== "accepted" && state !== "rejected") {
    return null;
  }

  return hasHumanPolicyConfirmationReply({
    observationInput: params.observationInput,
    traceInput: params.traceInput,
    observationMetadata: params.metadata,
    traceMetadata: params.traceMetadata,
  })
    ? state
    : null;
}
