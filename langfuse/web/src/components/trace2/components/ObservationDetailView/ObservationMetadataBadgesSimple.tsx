/**
 * Simple metadata badges for ObservationDetailView
 * Each badge handles its own null checks and returns null when data is unavailable
 */

import { Badge } from "@/src/components/ui/badge";
import { formatIntervalSeconds } from "@/src/utils/dates";

function getMetadataRecord(metadata: unknown): Record<string, unknown> {
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
      // no-op
    }
  }
  return {};
}

function parseStringArray(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter(
      (item): item is string => typeof item === "string" && !!item.trim(),
    );
  }
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) {
        return parsed.filter(
          (item): item is string => typeof item === "string" && !!item.trim(),
        );
      }
    } catch {
      // no-op
    }
    const trimmed = value.trim();
    return trimmed ? [trimmed] : [];
  }
  return [];
}

export function LatencyBadge({
  latencySeconds,
}: {
  latencySeconds: number | null;
}) {
  if (latencySeconds == null) return null;

  return (
    <Badge variant="tertiary">
      Latency: {formatIntervalSeconds(latencySeconds)}
    </Badge>
  );
}

export function TimeToFirstTokenBadge({
  timeToFirstToken,
}: {
  timeToFirstToken: number | null | undefined;
}) {
  if (timeToFirstToken == null) return null;

  return (
    <Badge variant="tertiary">
      Time to first token: {formatIntervalSeconds(timeToFirstToken)}
    </Badge>
  );
}

export function EnvironmentBadge({
  environment,
}: {
  environment: string | null | undefined;
}) {
  if (!environment) return null;

  return <Badge variant="tertiary">Env: {environment}</Badge>;
}

export function VersionBadge({
  version,
}: {
  version: string | null | undefined;
}) {
  if (!version) return null;

  return <Badge variant="tertiary">Version: {version}</Badge>;
}

export function LevelBadge({ level }: { level: string | null | undefined }) {
  if (!level || level === "DEFAULT") return null;

  return (
    <Badge
      variant={
        level === "ERROR"
          ? "destructive"
          : level === "WARNING" || level === "POLICY_VIOLATION"
            ? "warning"
            : "tertiary"
      }
    >
      {level}
    </Badge>
  );
}

export function StatusMessageBadge({
  statusMessage,
  level,
}: {
  statusMessage: string | null | undefined;
  level?: string | null | undefined;
}) {
  if (!statusMessage || level === "POLICY_VIOLATION") return null;

  return (
    <Badge
      variant="tertiary"
      className="max-w-full whitespace-normal break-all"
      title={statusMessage}
    >
      {statusMessage}
    </Badge>
  );
}

export function ErrorTypeBadge({
  errorType,
  errorTypeDescription,
  errorTypeWhy,
  errorTypeConfidence,
}: {
  errorType: string | null | undefined;
  errorTypeDescription?: string | null;
  errorTypeWhy?: string | null;
  errorTypeConfidence?: number | null;
}) {
  if (!errorType) return null;

  const title = [errorTypeDescription, errorTypeWhy].filter(Boolean).join("\n");

  return (
    <Badge
      variant="secondary"
      title={title || undefined}
      className="max-w-full whitespace-normal break-all"
    >
      Type: {errorType}
      {typeof errorTypeConfidence === "number"
        ? ` (${errorTypeConfidence.toFixed(2)})`
        : ""}
    </Badge>
  );
}

export function PolicyNameBadges({
  level,
  metadata,
}: {
  level: string | null | undefined;
  metadata: unknown;
}) {
  if (level !== "POLICY_VIOLATION") return null;
  const metadataRecord = getMetadataRecord(metadata);
  const policyNames = parseStringArray(metadataRecord.policy_names);
  if (policyNames.length === 0) return null;

  return (
    <>
      {policyNames.map((policyName) => (
        <Badge
          key={`policy-name-${policyName}`}
          variant="warning"
          className="max-w-full whitespace-normal break-all"
        >
          Policy: {policyName}
        </Badge>
      ))}
    </>
  );
}
