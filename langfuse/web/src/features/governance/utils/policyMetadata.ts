const POLICY_METADATA_KEYS = [
  "policy_name",
  "policy_names",
  "policy_descriptions",
  "policy_sources",
  "policy_protected",
  "policy_violation",
  "policy_violation_tags",
  "policy_confirmation_state",
  "policy_confirmation_accepted",
  "policy_confirmation_rejected",
  "inactivate_error_type",
  "policy_has_block",
  "policy_authority_label",
  "policy_confidentiality",
  "policy_integrity",
  "policy_trustworthiness",
  "policy_confidence",
  "policy_reversible",
  "policy_confidentiality_label",
  "policy_rule_effect_counts",
] as const;

function parseTurnIndex(value: unknown): number | null {
  if (value == null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeComparableText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.replace(/\s+/g, " ").trim().toLowerCase();
  return normalized.length > 0 ? normalized : null;
}

function hasMeaningfulPolicyMetadata(record: Record<string, unknown>): boolean {
  return POLICY_METADATA_KEYS.some((key) => {
    const value = record[key];
    if (value == null) return false;
    if (typeof value === "string") return value.trim().length > 0;
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === "object") return Object.keys(value).length > 0;
    return true;
  });
}

function pickRelevantPolicyMetadata(
  record: Record<string, unknown>,
): Record<string, unknown> {
  return Object.fromEntries(
    POLICY_METADATA_KEYS.flatMap((key) => {
      const value = record[key];
      if (value == null) return [];
      if (typeof value === "string" && value.trim().length === 0) return [];
      if (
        Array.isArray(value) &&
        value.every((item) => typeof item === "string" && !item.trim())
      ) {
        return [];
      }
      if (
        typeof value === "object" &&
        !Array.isArray(value) &&
        Object.keys(value).length === 0
      ) {
        return [];
      }
      return [[key, value] as const];
    }),
  );
}

export function getMetadataRecord(metadata: unknown): Record<string, unknown> {
  if (metadata && typeof metadata === "object" && !Array.isArray(metadata)) {
    return metadata as Record<string, unknown>;
  }

  if (typeof metadata === "string") {
    try {
      const parsed = JSON.parse(metadata);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // Ignore invalid metadata payloads and fall back to an empty object.
    }
  }

  return {};
}

export function parseStringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter(
      (item): item is string =>
        typeof item === "string" && item.trim().length > 0,
    );
  }

  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) {
        return parsed.filter(
          (item): item is string =>
            typeof item === "string" && item.trim().length > 0,
        );
      }
    } catch {
      // no-op
    }

    if (value.includes(",")) {
      return value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
    }

    const trimmed = value.trim();
    return trimmed ? [trimmed] : [];
  }

  return [];
}

export function parseStringRecord(value: unknown): Record<string, string> {
  let input = value;
  if (typeof input === "string") {
    try {
      input = JSON.parse(input);
    } catch {
      return {};
    }
  }

  if (!input || typeof input !== "object" || Array.isArray(input)) {
    return {};
  }

  return Object.fromEntries(
    Object.entries(input as Record<string, unknown>).flatMap(([key, val]) =>
      typeof val === "string" && val.trim().length > 0
        ? [[key, val] as const]
        : [],
    ),
  );
}

export function getBooleanFlag(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value === 1;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    return normalized === "true" || normalized === "1";
  }
  return false;
}

export function getObservationTurnIndex(params: {
  metadata: unknown;
  observationName?: string | null;
}): number | null {
  const metadataRecord = getMetadataRecord(params.metadata);
  const metadataTurnIndex = parseTurnIndex(metadataRecord.turn_index);
  if (metadataTurnIndex != null) {
    return metadataTurnIndex;
  }

  const observationName = params.observationName ?? "";
  const match = /turn[_\.](\d+)/i.exec(observationName);
  if (!match?.[1]) return null;

  const parsed = Number.parseInt(match[1], 10);
  return Number.isFinite(parsed) ? parsed : null;
}

export function isSessionOutputTurnObservationName(
  observationName: string | null | undefined,
): boolean {
  return /^session\.output\.turn_\d+$/i.test(observationName ?? "");
}

export function mergeRelevantPolicyMetadata(params: {
  observationMetadata: unknown;
  traceMetadata?: unknown;
  observationName?: string | null;
  statusMessage?: string | null;
}): Record<string, unknown> {
  const observationRecord = getMetadataRecord(params.observationMetadata);
  const traceRecord = getMetadataRecord(params.traceMetadata);

  if (!hasMeaningfulPolicyMetadata(traceRecord)) {
    return observationRecord;
  }

  const observationTurnIndex = getObservationTurnIndex({
    metadata: observationRecord,
    observationName: params.observationName,
  });
  const traceTurnIndex = parseTurnIndex(traceRecord.turn_index);

  const matchesTurn =
    observationTurnIndex != null &&
    traceTurnIndex != null &&
    observationTurnIndex === traceTurnIndex;

  const normalizedStatus = normalizeComparableText(params.statusMessage);
  const traceCandidates = [
    traceRecord.raw_output_content,
    traceRecord.policy_protected,
    traceRecord.status_message,
  ]
    .map(normalizeComparableText)
    .filter((value): value is string => value != null);

  const matchesText =
    normalizedStatus != null &&
    traceCandidates.some(
      (candidate) =>
        candidate === normalizedStatus ||
        candidate.includes(normalizedStatus) ||
        normalizedStatus.includes(candidate),
    );

  if (!matchesTurn && !matchesText) {
    return observationRecord;
  }

  return {
    ...pickRelevantPolicyMetadata(traceRecord),
    ...observationRecord,
  };
}

export function derivePolicyNamesFromMetadata(metadata: unknown): string[] {
  const record = getMetadataRecord(metadata);
  const policyNames = new Set<string>(parseStringArray(record.policy_names));

  parseStringArray(record.policy_name).forEach((name) => policyNames.add(name));
  Object.keys(parseStringRecord(record.policy_descriptions)).forEach((name) =>
    policyNames.add(name),
  );
  Object.keys(parseStringRecord(record.policy_sources)).forEach((name) =>
    policyNames.add(name),
  );

  return Array.from(policyNames);
}

export function getInactivateErrorTypeFromMetadata(
  metadata: unknown,
): string | null {
  const record = getMetadataRecord(metadata);

  if (typeof record.inactivate_error_type === "string") {
    const trimmed = record.inactivate_error_type.trim();
    if (trimmed.length > 0) return trimmed;
  }

  const normalizedFromArray = parseStringArray(record.inactivate_error_type)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
  if (normalizedFromArray.length > 0) {
    return normalizedFromArray.join("\n");
  }

  return null;
}

export function getRelevantInactivateErrorType(params: {
  observationMetadata: unknown;
  traceMetadata?: unknown;
  observationName?: string | null;
  statusMessage?: string | null;
}): string | null {
  if (!isSessionOutputTurnObservationName(params.observationName)) {
    return null;
  }

  const observationRecord = getMetadataRecord(params.observationMetadata);
  const ownValue = getInactivateErrorTypeFromMetadata(observationRecord);
  if (ownValue) {
    return ownValue;
  }

  const traceRecord = getMetadataRecord(params.traceMetadata);
  const traceValue = getInactivateErrorTypeFromMetadata(traceRecord);
  if (!traceValue) {
    return null;
  }

  // Only inherit from trace metadata when the observation's statusMessage
  // matches the inactivate_error_type, confirming this observation actually
  // triggered the warning (prevents drift to later turns).
  const normalizedStatus = normalizeComparableText(params.statusMessage);
  const normalizedError = normalizeComparableText(traceValue);
  if (
    normalizedStatus != null &&
    normalizedError != null &&
    (normalizedStatus === normalizedError ||
      normalizedStatus.includes(normalizedError) ||
      normalizedError.includes(normalizedStatus))
  ) {
    return traceValue;
  }

  return null;
}

export function getInactivateErrorTypeDisplayLabel(
  inactivateErrorType: string | null | undefined,
): string | null {
  if (typeof inactivateErrorType !== "string") return null;
  return inactivateErrorType.trim().length > 0
    ? "Inactive Policy Warning"
    : null;
}

export function getGovernanceDisplayLevel(params: {
  level: string | null | undefined;
  observationMetadata: unknown;
  traceMetadata?: unknown;
  observationName?: string | null;
  statusMessage?: string | null;
}): string | null {
  if (params.level === "POLICY_VIOLATION") {
    return "POLICY_VIOLATION";
  }

  const inactivateErrorType = getRelevantInactivateErrorType({
    observationMetadata: params.observationMetadata,
    traceMetadata: params.traceMetadata,
    observationName: params.observationName,
    statusMessage: params.statusMessage,
  });
  if (inactivateErrorType) {
    return "WARNING";
  }

  return params.level ?? null;
}
