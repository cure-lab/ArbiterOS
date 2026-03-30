import { type ObservationLevelType } from "@langfuse/shared";

export const LevelColors = {
  DEFAULT: { text: "", bg: "" },
  DEBUG: { text: "text-muted-foreground", bg: "bg-primary-foreground" },
  WARNING: { text: "text-dark-yellow", bg: "bg-light-yellow" },
  ERROR: { text: "text-dark-red", bg: "bg-light-red" },
  POLICY_VIOLATION: { text: "text-dark-red", bg: "bg-light-red" },
};

export const LevelSymbols = {
  DEFAULT: "ℹ️",
  DEBUG: "🔍",
  WARNING: "⚠️",
  ERROR: "🚨",
  POLICY_VIOLATION: "⚔️",
};

export const formatAsLabel = (countLabel: string) => {
  const normalized = countLabel.replace(/Count$/, "");
  if (normalized === "policyViolation") {
    return "POLICY_VIOLATION";
  }
  return normalized.toUpperCase() as ObservationLevelType;
};
