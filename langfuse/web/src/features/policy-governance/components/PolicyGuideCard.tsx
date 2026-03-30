"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  ChevronDown,
  ExternalLink,
  FileWarning,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Button } from "@/src/components/ui/button";
import { Badge } from "@/src/components/ui/badge";
import { Card, CardContent } from "@/src/components/ui/card";
import { Label } from "@/src/components/ui/label";
import { Switch } from "@/src/components/ui/switch";
import { Textarea } from "@/src/components/ui/textarea";
import {
  Collapsible,
  CollapsibleContent,
} from "@/src/components/ui/collapsible";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/src/components/ui/accordion";
import { CodeMirrorEditor } from "@/src/components/editor/CodeMirrorEditor";
import { api } from "@/src/utils/api";
import { cn } from "@/src/utils/tailwind";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";

type PolicyGuideCase = {
  traceId: string;
  traceName: string | null;
  traceTimestamp: string | null;
  turnIndex: number | null;
  targetObservationId: string | null;
  blockedAction: string | null;
  examplePrompt: string | null;
  violationExample: {
    observationName: string | null;
    statusMessage: string | null;
    input: string | null;
    output: string | null;
    inactivateErrorType: string | null;
    policyNames: string[];
  } | null;
};

type PolicyGuideInsight = {
  policyName: string;
  recentViolationCount: number;
  exampleBlockedAction: string | null;
  examplePrompt: string | null;
  exampleViolation: {
    observationName: string | null;
    statusMessage: string | null;
    input: string | null;
    output: string | null;
    inactivateErrorType: string | null;
    policyNames: string[];
  } | null;
  similarCases: PolicyGuideCase[];
};

type PolicyStatsRow = {
  policyName: string;
  totalCount: number;
  acceptedCount: number;
  rejectedCount: number;
  acceptedRate: number;
  rejectedRate: number;
};

type PolicyRegistryEntry = {
  name: string;
  enabled: boolean;
  description: string;
};

const POLICY_PURPOSES: Record<string, string> = {
  PathBudgetPolicy:
    "Checks whether path-like tool inputs stay inside allowed boundaries and within a safe size budget.",
  AllowDenyPolicy:
    "Lets you explicitly allow or deny tools, instruction types, and categories before they run.",
  EfsmGatePolicy:
    "Uses workflow state rules to decide whether a read, write, or exec action should be allowed next.",
  TaintPolicy:
    "Prevents untrusted content from earlier tool outputs from flowing into risky write, exec, or send actions.",
  RateLimitPolicy:
    "Slows down repeated tool usage patterns that look abusive, accidental, or stuck in a loop.",
  OutputBudgetPolicy:
    "Keeps assistant output within a configured size cap so responses do not grow without bound.",
  SecurityLabelPolicy:
    "Applies security labels and confidence rules before sensitive tools or responses are allowed.",
  ExecCompositePolicy:
    "Treats multi-part exec commands conservatively so read-only combinations can pass while risky mixtures are blocked.",
  DeletePolicy:
    "Blocks delete-like operations so destructive actions require a stricter approval path.",
};

function formatList(values: unknown, emptyLabel = "none configured"): string {
  if (!Array.isArray(values) || values.length === 0) {
    return emptyLabel;
  }

  const items = values
    .filter((item): item is string | number | boolean =>
      ["string", "number", "boolean"].includes(typeof item),
    )
    .map((item) => String(item));
  if (items.length === 0) {
    return emptyLabel;
  }

  if (items.length <= 4) {
    return items.join(", ");
  }

  return `${items.slice(0, 4).join(", ")} +${items.length - 4} more`;
}

function sentenceFromKeyValue(key: string, value: unknown): string | null {
  if (value == null) return null;
  if (typeof value === "boolean") {
    return `${key.replace(/_/g, " ")} is ${value ? "enabled" : "disabled"}.`;
  }
  if (typeof value === "number" || typeof value === "string") {
    return `${key.replace(/_/g, " ")}: ${String(value)}.`;
  }
  if (Array.isArray(value)) {
    return `${key.replace(/_/g, " ")}: ${formatList(value)}.`;
  }
  return null;
}

function summarizeSection(section: string, value: unknown): string[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return [`${section.replace(/_/g, " ")} is not configured.`];
  }

  const record = value as Record<string, unknown>;
  switch (section) {
    case "paths":
      return [
        `Allowed prefixes: ${formatList(record.allow_prefixes)}.`,
        `Denied prefixes: ${formatList(record.deny_prefixes)}.`,
      ];
    case "input_budget":
      return [
        `Input strings longer than ${record.max_str_len ?? "the configured limit"} characters are blocked.`,
      ];
    case "output_budget":
      return [
        `Assistant output is capped at ${record.max_chars ?? "the configured limit"} characters.`,
      ];
    case "allow":
      return [
        `Allowed tools: ${formatList(record.tools)}.`,
        `Allowed instruction types: ${formatList(record.instruction_types)}.`,
        `Allowed categories: ${formatList(record.categories)}.`,
      ];
    case "deny":
      return [
        `Denied tools: ${formatList(record.tools)}.`,
        `Denied instruction types: ${formatList(record.instruction_types)}.`,
        `Denied categories: ${formatList(record.categories)}.`,
      ];
    case "rate_limit":
      return [
        `A tool can repeat up to ${record.max_consecutive_same_tool ?? "the configured"} consecutive times.`,
        `Within ${record.window_seconds ?? "the configured"} seconds, up to ${record.max_calls_per_window ?? "the configured"} calls are allowed.`,
      ];
    case "efsm":
      return [
        `State machine starts in ${String(record.initial ?? "the default")} and keeps plans for ${String(record.plan_ttl_seconds ?? "the configured")} seconds.`,
        `There are ${Array.isArray(record.transitions) ? record.transitions.length : 0} transition rules controlling allowed reads, writes, and exec calls.`,
      ];
    case "taint": {
      const taintPolicy =
        record.taint_policy &&
        typeof record.taint_policy === "object" &&
        !Array.isArray(record.taint_policy)
          ? (record.taint_policy as Record<string, unknown>)
          : {};
      const toolsByAction =
        taintPolicy.tools_by_action &&
        typeof taintPolicy.tools_by_action === "object" &&
        !Array.isArray(taintPolicy.tools_by_action)
          ? Object.keys(taintPolicy.tools_by_action).length
          : 0;

      return [
        `Taint tracking is ${record.enabled ? "enabled" : "disabled"}.`,
        `${Array.isArray(taintPolicy.input_tools) ? taintPolicy.input_tools.length : 0} tool types can introduce untrusted content and ${Array.isArray(taintPolicy.output_tools) ? taintPolicy.output_tools.length : 0} risky tool types are protected.`,
        `${toolsByAction} action-specific tool groups have custom input/output taint rules.`,
      ];
    }
    case "exec_composite_policy":
      return [
        `Multi-read-only exec chains are ${record.allow_multi_read_only ? "allowed" : "not allowed"}.`,
        `Any write in a composite exec is ${record.block_if_any_write ? "blocked" : "allowed"}, and any exec segment is ${record.block_if_any_exec ? "blocked" : "allowed"}.`,
      ];
    case "delete_policy":
      return [
        `Delete-like operations are ${record.enabled ? "blocked by this policy" : "not blocked by this policy"}.`,
      ];
    default: {
      const generic = Object.entries(record)
        .map(([key, sectionValue]) => sentenceFromKeyValue(key, sectionValue))
        .filter((item): item is string => Boolean(item));
      return generic.length > 0
        ? generic
        : [`${section.replace(/_/g, " ")} has custom JSON settings.`];
    }
  }
}

function buildPolicyChecksToday(settingsBySection: Record<string, unknown>) {
  const bullets = Object.entries(settingsBySection).flatMap(
    ([section, value]) => summarizeSection(section, value),
  );
  return bullets.slice(0, 5);
}

function formatSummaryPercent(value: number): string {
  const pct = value * 100;
  return Number.isInteger(pct) ? `${pct.toFixed(0)}%` : `${pct.toFixed(1)}%`;
}

function formatTimestamp(value: string | null) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function PolicyGuideCard(props: {
  projectId: string;
  entry: PolicyRegistryEntry;
  initialBeginnerSummary?: string | null;
  sections: string[];
  settingsBySection: Record<string, unknown>;
  highlightThresholdPct: number;
  confirmationStats?: PolicyStatsRow;
  lastConfirmationStatsResetAt?: string | null;
  guideInsight?: PolicyGuideInsight;
  hasUpdateAccess: boolean;
  sectionDrafts: Record<string, string>;
  sectionErrors: Record<string, string | null>;
  onOpenTracePeek: (policyCase: PolicyGuideCase) => void;
  onEnabledChange: (checked: boolean) => void;
  onDescriptionChange: (value: string) => void;
  onSectionChange: (section: string, value: string) => void;
  onGenerateProposal: () => void;
  isGeneratingProposal: boolean;
  onResetConfirmationStats: () => void;
  isResettingConfirmationStats: boolean;
  hasSectionErrors: boolean;
}) {
  const { t } = useLanguage();
  const {
    projectId,
    entry,
    initialBeginnerSummary,
    sections,
    settingsBySection,
    highlightThresholdPct,
    confirmationStats,
    lastConfirmationStatsResetAt,
    guideInsight,
    hasUpdateAccess,
    sectionDrafts,
    sectionErrors,
    onOpenTracePeek,
    onEnabledChange,
    onDescriptionChange,
    onSectionChange,
    onGenerateProposal,
    isGeneratingProposal,
    onResetConfirmationStats,
    isResettingConfirmationStats,
    hasSectionErrors,
  } = props;
  const [isAdvancedEditorOpen, setIsAdvancedEditorOpen] = useState(false);
  const [beginnerSummary, setBeginnerSummary] = useState<string | null>(
    initialBeginnerSummary ?? null,
  );
  const utils = api.useUtils();
  const beginnerSummaryMutation =
    api.policyGovernance.generatePolicyBeginnerSummary.useMutation({
      onSuccess: async (data) => {
        setBeginnerSummary(data.summary);
        await utils.projects.getPolicyGovernanceSettings.invalidate({
          projectId,
        });
      },
    });

  useEffect(() => {
    setBeginnerSummary(initialBeginnerSummary ?? null);
  }, [initialBeginnerSummary]);

  const checksToday = useMemo(
    () => buildPolicyChecksToday(settingsBySection),
    [settingsBySection],
  );
  const shouldHighlightByStats =
    (confirmationStats?.rejectedRate ?? 0) * 100 >= highlightThresholdPct;
  const hasConfirmationStats = (confirmationStats?.totalCount ?? 0) > 0;
  const viewViolationsHref = `/project/${projectId}/analysis?analysisLevel=policy_violation&policyType=${encodeURIComponent(entry.name)}`;
  const guideDescription =
    POLICY_PURPOSES[entry.name] ??
    entry.description ??
    t("policyCard.customPolicy");

  return (
    <Card
      className={cn(
        "overflow-hidden border-border/70",
        shouldHighlightByStats && "border-destructive/40 bg-destructive/5",
      )}
    >
      <CardContent className="space-y-5 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold text-foreground">
                {entry.name}
              </h2>
              <Badge variant={entry.enabled ? "success" : "secondary"}>
                {entry.enabled
                  ? t("policyCard.enabled")
                  : t("policyCard.disabled")}
              </Badge>
            </div>
            <p className="max-w-3xl text-sm text-muted-foreground">
              {entry.description}
            </p>
          </div>
        </div>

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(280px,0.9fr)]">
          <div className="space-y-5">
            <section className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("policyCard.whatPolicyDoes")}
              </div>
              <p className="text-sm text-foreground">{guideDescription}</p>
            </section>

            <section className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("policyCard.whatChecksToday")}
              </div>
              <ul className="space-y-1.5 text-sm text-foreground">
                {checksToday.length > 0 ? (
                  checksToday.map((item) => (
                    <li key={item} className="flex gap-2">
                      <span className="mt-[7px] h-1.5 w-1.5 rounded-full bg-primary/70" />
                      <span>{item}</span>
                    </li>
                  ))
                ) : (
                  <li className="text-muted-foreground">
                    {t("policyCard.noMappedRuntimeSettings")}
                  </li>
                )}
              </ul>
            </section>

            <section className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {t("policyCard.exampleBlockedAction")}
              </div>
              <div className="rounded-lg border bg-muted/30 px-3 py-2 text-sm text-foreground">
                {guideInsight?.exampleBlockedAction ??
                  guideInsight?.examplePrompt ??
                  t("policyCard.noRecentBlockedAction")}
              </div>
            </section>

            <Accordion
              type="single"
              collapsible
              className="rounded-lg border px-4"
            >
              <AccordionItem value="similar-cases" className="border-none">
                <AccordionTrigger className="py-3 text-sm">
                  {t("policyCard.similarPastCases")}
                </AccordionTrigger>
                <AccordionContent className="space-y-3 pt-1">
                  {guideInsight?.similarCases.length ? (
                    guideInsight.similarCases.map((policyCase) => (
                      <div
                        key={`${policyCase.traceId}-${policyCase.turnIndex ?? "null"}`}
                        className="rounded-lg border bg-muted/20 p-3"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="text-sm font-medium text-foreground">
                            {policyCase.traceName ?? policyCase.traceId}
                          </div>
                          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            {policyCase.turnIndex != null ? (
                              <span>
                                {t("policyCard.turn")} {policyCase.turnIndex}
                              </span>
                            ) : null}
                            {policyCase.traceTimestamp ? (
                              <span>
                                {formatTimestamp(policyCase.traceTimestamp)}
                              </span>
                            ) : null}
                            <button
                              type="button"
                              onClick={() => onOpenTracePeek(policyCase)}
                              disabled={!policyCase.targetObservationId}
                              className="inline-flex items-center gap-1 text-primary hover:underline disabled:cursor-not-allowed disabled:text-muted-foreground disabled:no-underline"
                            >
                              {t("policyCard.openTrace")}
                              <ArrowRight className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </div>
                        {policyCase.blockedAction ? (
                          <div className="mt-2 rounded-md border bg-background px-2.5 py-2 text-sm text-foreground">
                            {policyCase.blockedAction}
                          </div>
                        ) : null}
                        {policyCase.examplePrompt ? (
                          <div className="mt-2 text-xs text-muted-foreground">
                            {t("policyCard.promptSnippet")}{" "}
                            {policyCase.examplePrompt}
                          </div>
                        ) : null}
                      </div>
                    ))
                  ) : (
                    <div className="rounded-lg border border-dashed px-3 py-4 text-sm text-muted-foreground">
                      {t("policyCard.noMatchedCases")}
                    </div>
                  )}
                </AccordionContent>
              </AccordionItem>
            </Accordion>

            {beginnerSummary ? (
              <section className="rounded-lg border border-primary/20 bg-primary/5 p-3">
                <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-primary">
                  <Sparkles className="h-3.5 w-3.5" />
                  {t("policyCard.beginnerSummary")}
                </div>
                <div className="whitespace-pre-wrap text-sm text-foreground">
                  {beginnerSummary}
                </div>
              </section>
            ) : null}

            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="secondary"
                loading={beginnerSummaryMutation.isPending}
                onClick={() => {
                  beginnerSummaryMutation.mutate({
                    projectId,
                    policyName: entry.name,
                    description: entry.description,
                    enabled: entry.enabled,
                    settingSections: sections,
                    settingsBySection,
                    highlightThresholdPct,
                    suggestPolicyUpdate: shouldHighlightByStats,
                    confirmationSummary: confirmationStats
                      ? {
                          totalCount: confirmationStats.totalCount,
                          acceptedCount: confirmationStats.acceptedCount,
                          rejectedCount: confirmationStats.rejectedCount,
                          rejectedRate: confirmationStats.rejectedRate,
                        }
                      : null,
                    guideInsight: guideInsight ?? null,
                  });
                }}
              >
                <Sparkles className="mr-2 h-4 w-4" />
                {t("policyCard.generateBeginnerSummary")}
              </Button>
              <Button asChild type="button" variant="outline">
                <Link href={viewViolationsHref}>
                  {t("policyCard.viewViolations")}
                  <ExternalLink className="ml-2 h-4 w-4" />
                </Link>
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => setIsAdvancedEditorOpen((prev) => !prev)}
              >
                {t("policyCard.openAdvancedEditor")}
                <ChevronDown
                  className={cn(
                    "ml-2 h-4 w-4 transition-transform",
                    isAdvancedEditorOpen && "rotate-180",
                  )}
                />
              </Button>
            </div>
          </div>

          <div className="space-y-3">
            <div className="rounded-lg border bg-muted/20 p-3">
              <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                <ShieldCheck className="h-4 w-4 text-primary" />
                {t("policyCard.confirmationSignals")}
              </div>
              {confirmationStats ? (
                <div className="mt-3 space-y-2 text-sm text-muted-foreground">
                  <div className="flex items-center justify-between gap-3">
                    <span>{t("policyCard.totalConfirmations")}</span>
                    <span className="font-medium text-foreground">
                      {confirmationStats.totalCount}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span>{t("policyCard.accepted")}</span>
                    <span className="font-medium text-foreground">
                      {formatSummaryPercent(confirmationStats.acceptedRate)} (
                      {confirmationStats.acceptedCount})
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span>{t("policyCard.rejected")}</span>
                    <span
                      className={cn(
                        "font-medium",
                        shouldHighlightByStats
                          ? "text-destructive"
                          : "text-foreground",
                      )}
                    >
                      {formatSummaryPercent(confirmationStats.rejectedRate)} (
                      {confirmationStats.rejectedCount})
                    </span>
                  </div>
                  <div className="rounded-md border bg-background px-2.5 py-2 text-xs">
                    {t("policyCard.highlightThresholdPrefix")}{" "}
                    {highlightThresholdPct}%{" "}
                    {t("policyCard.highlightThresholdSuffix")}
                  </div>
                </div>
              ) : (
                <div className="mt-3 text-sm text-muted-foreground">
                  {t("policyCard.noConfirmationData")}
                </div>
              )}
              <div className="mt-3 space-y-2">
                <div className="rounded-md border bg-background px-2.5 py-2 text-xs text-muted-foreground">
                  {t("policyCard.lastStatsReset")}{" "}
                  {formatTimestamp(lastConfirmationStatsResetAt ?? null) ??
                    t("policyCard.noStatsReset")}
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  loading={isResettingConfirmationStats}
                  disabled={!hasUpdateAccess || !hasConfirmationStats}
                  onClick={onResetConfirmationStats}
                  className={cn(
                    "border-destructive/30 text-destructive transition-colors duration-150",
                    "hover:border-destructive hover:bg-destructive hover:text-destructive-foreground",
                    "focus-visible:ring-destructive/40",
                    "disabled:border-border disabled:text-muted-foreground disabled:hover:bg-transparent disabled:hover:text-muted-foreground",
                  )}
                >
                  {isResettingConfirmationStats
                    ? t("policyCard.resettingConfirmationStats")
                    : t("policyCard.resetConfirmationStats")}
                </Button>
              </div>
            </div>

            <div className="rounded-lg border bg-muted/20 p-3">
              <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                <FileWarning className="h-4 w-4 text-primary" />
                {t("policyCard.policyEvidence")}
              </div>
              <div className="mt-3 space-y-2 text-sm text-muted-foreground">
                <div className="flex items-center justify-between gap-3">
                  <span>{t("policyCard.recentViolations")}</span>
                  <span className="font-medium text-foreground">
                    {guideInsight?.recentViolationCount ?? 0}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span>{t("policyCard.matchedPastCases")}</span>
                  <span className="font-medium text-foreground">
                    {guideInsight?.similarCases.length ?? 0}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <Collapsible
          open={isAdvancedEditorOpen}
          onOpenChange={setIsAdvancedEditorOpen}
        >
          <CollapsibleContent className="space-y-4 border-t pt-5">
            <div className="flex items-center justify-between rounded-lg border bg-muted/20 px-4 py-3">
              <div>
                <p className="text-sm font-medium text-foreground">
                  {t("policyCard.enabled")}
                </p>
                <p className="text-xs text-muted-foreground">
                  {t("policyCard.toggleActivation")}
                </p>
              </div>
              <Switch
                checked={entry.enabled}
                disabled={!hasUpdateAccess}
                onCheckedChange={onEnabledChange}
              />
            </div>

            <div className="space-y-2">
              <Label>{t("policyCard.description")}</Label>
              <Textarea
                value={entry.description}
                disabled={!hasUpdateAccess}
                onChange={(event) => onDescriptionChange(event.target.value)}
              />
            </div>

            {sections.length > 0 ? (
              <div className="space-y-4">
                {sections.map((section) => (
                  <div key={`${entry.name}-${section}`} className="space-y-2">
                    <Label>{section}</Label>
                    <CodeMirrorEditor
                      mode="json"
                      value={sectionDrafts[section] ?? "{}"}
                      editable={hasUpdateAccess}
                      lineNumbers
                      minHeight={120}
                      maxHeight={360}
                      onChange={(next) => onSectionChange(section, next)}
                    />
                    {sectionErrors[section] ? (
                      <p className="text-xs text-destructive">
                        {t("policyCard.invalidJson")}: {sectionErrors[section]}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t("policyCard.noDedicatedRuntimeSettings")}
              </p>
            )}

            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="secondary"
                onClick={onGenerateProposal}
                disabled={
                  !hasUpdateAccess || isGeneratingProposal || hasSectionErrors
                }
              >
                {isGeneratingProposal
                  ? t("policyCard.generating")
                  : t("policyCard.llmSuggestUpdate")}
              </Button>
            </div>
          </CollapsibleContent>
        </Collapsible>
      </CardContent>
    </Card>
  );
}
