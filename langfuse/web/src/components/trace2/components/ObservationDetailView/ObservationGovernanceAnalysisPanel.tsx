import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/src/components/ui/badge";
import { Button } from "@/src/components/ui/button";
import { api } from "@/src/utils/api";
import { ChevronDown, ChevronUp } from "lucide-react";
import {
  getGovernanceDisplayLevel,
  getRelevantInactivateErrorType,
  getMetadataRecord,
  mergeRelevantPolicyMetadata,
  parseStringArray,
  parseStringRecord,
} from "@/src/features/governance/utils/policyMetadata";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

type PolicyAction = {
  tool: string;
  reason: string;
};

function derivePolicyActions(policyReason: string | null): PolicyAction[] {
  if (!policyReason) return [];

  const pattern =
    /POLICY_BLOCK\s+tool=(\S+)\s+reason=([\s\S]*?)(?=\s+POLICY_BLOCK\s+tool=|$)/g;
  const results: PolicyAction[] = [];
  const seen = new Set<string>();
  let match: RegExpExecArray | null = pattern.exec(policyReason);
  while (match) {
    const tool = match[1]?.trim();
    const reason = match[2]?.replace(/\s+/g, " ").trim();
    if (tool && reason) {
      const key = `${tool}::${reason}`;
      if (!seen.has(key)) {
        seen.add(key);
        results.push({ tool, reason });
      }
    }
    match = pattern.exec(policyReason);
  }
  return results;
}

export function ObservationGovernanceAnalysisPanel(props: {
  projectId: string;
  traceId: string;
  observationId: string;
  observationName?: string | null;
  level: string | null | undefined;
  statusMessage: string | null | undefined;
  metadata: unknown;
  traceMetadata?: unknown;
  hasProjectAccess: boolean;
}) {
  const { language } = useLanguage();
  const statusMessage = props.statusMessage?.trim();
  const [isOutputExpanded, setIsOutputExpanded] = useState(false);
  const observationPolicyMetadata = useMemo(
    () => getMetadataRecord(props.metadata),
    [props.metadata],
  );
  const effectiveLevel = useMemo(
    () =>
      getGovernanceDisplayLevel({
        level: props.level,
        observationMetadata: observationPolicyMetadata,
        traceMetadata: props.traceMetadata,
        observationName: props.observationName,
        statusMessage,
      }),
    [
      observationPolicyMetadata,
      props.level,
      props.observationName,
      props.traceMetadata,
      statusMessage,
    ],
  );
  const isPolicyViolation = effectiveLevel === "POLICY_VIOLATION";
  const isGovernanceLevel =
    effectiveLevel === "ERROR" ||
    effectiveLevel === "WARNING" ||
    isPolicyViolation;
  const policyMetadata = useMemo(
    () =>
      mergeRelevantPolicyMetadata({
        observationMetadata: observationPolicyMetadata,
        traceMetadata: props.traceMetadata,
        observationName: props.observationName,
        statusMessage,
      }),
    [
      observationPolicyMetadata,
      props.traceMetadata,
      props.observationName,
      statusMessage,
    ],
  );
  const policyProtectedReason = useMemo(() => {
    const value = policyMetadata.policy_protected;
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
    if (statusMessage?.includes("POLICY_BLOCK")) {
      return statusMessage;
    }
    return null;
  }, [policyMetadata.policy_protected, statusMessage]);
  const policyNames = useMemo(
    () => [
      ...new Set([
        ...parseStringArray(policyMetadata.policy_names),
        ...parseStringArray(policyMetadata.policy_name),
      ]),
    ],
    [policyMetadata.policy_name, policyMetadata.policy_names],
  );
  const policyDescriptions = useMemo(
    () => parseStringRecord(policyMetadata.policy_descriptions),
    [policyMetadata.policy_descriptions],
  );
  const policySources = useMemo(
    () => parseStringRecord(policyMetadata.policy_sources),
    [policyMetadata.policy_sources],
  );
  const inactivateErrorType = useMemo(() => {
    return getRelevantInactivateErrorType({
      observationMetadata: observationPolicyMetadata,
      traceMetadata: props.traceMetadata,
      observationName: props.observationName,
      statusMessage,
    });
  }, [
    observationPolicyMetadata,
    props.observationName,
    props.traceMetadata,
    statusMessage,
  ]);
  const policyNameList = useMemo(() => {
    const names = new Set(policyNames);
    Object.keys(policyDescriptions).forEach((name) => names.add(name));
    Object.keys(policySources).forEach((name) => names.add(name));
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [policyDescriptions, policyNames, policySources]);

  const panelTitle = isPolicyViolation
    ? localize(language, "Policy Enforcement", "策略执行")
    : inactivateErrorType
      ? localize(language, "Governance Warning", "治理警告")
      : localize(
          language,
          "Governance Analysis & Suggestion",
          "治理分析与建议",
        );
  const panelSubtitle = isPolicyViolation
    ? localize(
        language,
        "This node was blocked by policy. Action shows what was prevented.",
        "该节点被策略阻止。操作会显示被阻止的内容。",
      )
    : inactivateErrorType
      ? localize(
          language,
          "This node triggered a non-blocking policy warning.",
          "该节点触发了一个非阻断策略警告。",
        )
      : localize(
          language,
          "Node-level diagnostics and mitigation guidance for this failure.",
          "针对该失败的节点级诊断与缓解建议。",
        );
  const shouldShowInactivePolicyWarning =
    Boolean(inactivateErrorType) && !isPolicyViolation;
  const canQueryGovernance =
    props.hasProjectAccess &&
    isGovernanceLevel &&
    !shouldShowInactivePolicyWarning;
  const errorAnalysisQuery = api.errorAnalysis.get.useQuery(
    {
      projectId: props.projectId,
      traceId: props.traceId,
      observationId: props.observationId,
    },
    {
      enabled: canQueryGovernance,
      refetchOnWindowFocus: false,
    },
  );

  const rawOutputContent = statusMessage ?? null;
  const policyActions = useMemo(
    () => derivePolicyActions(policyProtectedReason),
    [policyProtectedReason],
  );
  const policyActionFallback = useMemo(() => {
    if (policyActions.length > 0 || !policyProtectedReason) return null;
    const compact = policyProtectedReason
      .replace(/^POLICY_[A-Z_]+\s+/i, "")
      .replace(/\s+/g, " ")
      .trim();
    return compact || null;
  }, [policyActions, policyProtectedReason]);
  const normalizedPolicyProtectedLines = useMemo(() => {
    if (policyActions.length > 0) {
      return policyActions.map(
        (item) => `POLICY_BLOCK tool=${item.tool} reason=${item.reason}`,
      );
    }
    if (!policyProtectedReason) return [];
    const normalized = policyProtectedReason
      .replace(/\r\n/g, "\n")
      .replace(/\s*POLICY_BLOCK\s+tool=/g, "\nPOLICY_BLOCK tool=")
      .trim();
    if (!normalized) return [];
    return Array.from(
      new Set(
        normalized
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
      ),
    );
  }, [policyActions, policyProtectedReason]);
  const canExpandOutput = useMemo(() => {
    if (!rawOutputContent) return false;
    if (rawOutputContent.length > 180) return true;
    const lines = rawOutputContent.split("\n").length;
    return lines > 6;
  }, [rawOutputContent]);

  useEffect(() => {
    setIsOutputExpanded(false);
  }, [props.observationId]);

  if (!isGovernanceLevel) {
    return null;
  }

  return (
    <div className="h-full min-h-0 min-w-0 p-2">
      <div className="h-full min-h-0 min-w-0 overflow-y-auto overflow-x-hidden rounded-md border bg-muted/20 p-3">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="text-sm font-medium">{panelTitle}</div>
            <div className="text-xs text-muted-foreground">{panelSubtitle}</div>
          </div>
          <Badge
            variant={
              effectiveLevel === "ERROR"
                ? "destructive"
                : effectiveLevel === "WARNING" || isPolicyViolation
                  ? "warning"
                  : "secondary"
            }
          >
            {effectiveLevel ?? props.level}
          </Badge>
        </div>

        <div className="space-y-3">
          {shouldShowInactivePolicyWarning ? (
            <div>
              <div className="mb-1 min-w-0 text-xs font-medium text-amber-700 dark:text-amber-300">
                {localize(
                  language,
                  "Inactive Policy Warning",
                  "未激活策略警告",
                )}
              </div>
              <div className="min-w-0 whitespace-pre-wrap break-words rounded-md border border-amber-200 bg-amber-50/40 p-2 font-mono text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
                {inactivateErrorType}
              </div>
            </div>
          ) : null}
          {isPolicyViolation ? (
            <div>
              <div className="mb-1 min-w-0 text-xs font-medium text-muted-foreground">
                {localize(language, "Action", "操作")}
              </div>
              <div className="min-w-0 rounded-md border bg-background p-2 text-sm">
                {policyActions.length > 0 ? (
                  <div className="space-y-2">
                    {policyActions.map((action, idx) => (
                      <div
                        key={`policy-action-${idx}`}
                        className="flex flex-wrap items-center gap-2 rounded border border-amber-200 bg-amber-50/50 px-2 py-1 text-xs dark:border-amber-900 dark:bg-amber-950/20"
                      >
                        <Badge
                          variant="warning"
                          className="font-mono text-[10px]"
                        >
                          {action.tool}
                        </Badge>
                        <span className="break-words text-muted-foreground">
                          {action.reason}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : policyActionFallback ? (
                  <div className="font-mono text-xs text-muted-foreground">
                    {policyActionFallback}
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground">
                    {localize(language, "Not available", "不可用")}
                  </div>
                )}
              </div>
            </div>
          ) : null}

          {isPolicyViolation || !shouldShowInactivePolicyWarning ? (
            <div>
              <div className="mb-1 min-w-0 text-xs font-medium text-muted-foreground">
                {isPolicyViolation
                  ? localize(language, "Policy Details", "策略详情")
                  : localize(language, "Output", "输出")}
              </div>
              {isPolicyViolation ? (
                <div className="divide-y rounded-md border bg-background text-xs">
                  <div className="space-y-2 p-3">
                    <div className="text-sm font-semibold text-foreground">
                      {localize(language, "Policy Protected", "策略保护")}
                    </div>
                    {normalizedPolicyProtectedLines.length > 0 ? (
                      <div className="mt-1 space-y-1">
                        {normalizedPolicyProtectedLines.map((line, idx) => (
                          <div
                            key={`policy-protected-line-${idx}`}
                            className="whitespace-pre-wrap break-words font-mono text-[11px] text-muted-foreground"
                          >
                            {line}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="mt-1 text-muted-foreground">
                        {localize(language, "Not available", "不可用")}
                      </div>
                    )}
                  </div>
                  <div className="space-y-2 p-3">
                    <div className="text-sm font-semibold text-foreground">
                      {localize(language, "Policy Names", "策略名称")}
                    </div>
                    {policyNameList.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {policyNameList.map((policyName) => (
                          <Badge
                            key={`policy-name-${policyName}`}
                            variant="warning"
                          >
                            {policyName}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      <div className="mt-1 text-muted-foreground">
                        {localize(language, "Not available", "不可用")}
                      </div>
                    )}
                  </div>
                  <div className="space-y-2 p-3">
                    <div className="text-sm font-semibold text-foreground">
                      {localize(language, "Policy Descriptions", "策略说明")}
                    </div>
                    <div className="space-y-1 text-muted-foreground">
                      {policyNameList.length > 0 ? (
                        policyNameList.map((policyName) => (
                          <div key={`policy-description-${policyName}`}>
                            <span className="font-medium text-foreground">
                              {policyName}:
                            </span>{" "}
                            {policyDescriptions[policyName] ??
                              localize(language, "Not available", "不可用")}
                          </div>
                        ))
                      ) : (
                        <div>
                          {localize(language, "Not available", "不可用")}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="space-y-2 p-3">
                    <div className="text-sm font-semibold text-foreground">
                      {localize(language, "Policy Sources", "策略来源")}
                    </div>
                    <div className="space-y-2 text-muted-foreground">
                      {policyNameList.length > 0 ? (
                        policyNameList.map((policyName) => (
                          <div
                            key={`policy-source-${policyName}`}
                            className="rounded border bg-muted/20 p-2"
                          >
                            <div className="font-medium text-foreground">
                              {policyName}
                            </div>
                            <div className="mt-1 whitespace-pre-wrap break-all font-mono text-[11px] text-muted-foreground">
                              {policySources[policyName] ??
                                localize(language, "Not available", "不可用")}
                            </div>
                          </div>
                        ))
                      ) : (
                        <div>
                          {localize(language, "Not available", "不可用")}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ) : null}
              {!isPolicyViolation && rawOutputContent ? (
                <div className="rounded-md border bg-background">
                  <div
                    className={`whitespace-pre-wrap break-words px-3 py-2 font-mono text-xs text-muted-foreground ${
                      isOutputExpanded ? "block" : "line-clamp-6"
                    }`}
                    onClick={() => {
                      if (!isOutputExpanded && canExpandOutput) {
                        setIsOutputExpanded(true);
                      }
                    }}
                  >
                    {rawOutputContent}
                  </div>
                  {canExpandOutput ? (
                    <div className="flex justify-center px-2 pb-2">
                      <Button
                        variant="secondary"
                        size="icon-xs"
                        onClick={() => setIsOutputExpanded((prev) => !prev)}
                        title={
                          isOutputExpanded
                            ? localize(language, "Collapse", "收起")
                            : localize(language, "Expand", "展开")
                        }
                      >
                        {isOutputExpanded ? (
                          <ChevronUp className="h-3 w-3" />
                        ) : (
                          <ChevronDown className="h-3 w-3" />
                        )}
                      </Button>
                    </div>
                  ) : null}
                </div>
              ) : !isPolicyViolation ? (
                <div className="min-w-0 rounded-md border bg-background p-2 text-xs text-muted-foreground">
                  {localize(
                    language,
                    "No status message available.",
                    "没有可用的状态消息。",
                  )}
                </div>
              ) : null}
            </div>
          ) : null}

          {!shouldShowInactivePolicyWarning ? (
            !canQueryGovernance ? (
              <div className="min-w-0 rounded-md border bg-background p-2 text-xs text-muted-foreground">
                {localize(
                  language,
                  "Governance analysis is available for project members.",
                  "治理分析仅对项目成员可用。",
                )}
              </div>
            ) : errorAnalysisQuery.isLoading ? (
              <div className="min-w-0 rounded-md border bg-background p-2 text-xs text-muted-foreground">
                {localize(
                  language,
                  "Loading governance analysis...",
                  "正在加载治理分析...",
                )}
              </div>
            ) : errorAnalysisQuery.data ? (
              <div className="min-w-0">
                <div className="mb-1 text-xs font-medium text-muted-foreground">
                  {localize(language, "Analysis", "分析")}
                </div>
                <div className="min-w-0 rounded-md border bg-background p-2 text-sm">
                  <div className="font-medium">
                    {localize(language, "Root cause", "根本原因")}
                  </div>
                  <div className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">
                    {errorAnalysisQuery.data.rendered.rootCause}
                  </div>

                  {errorAnalysisQuery.data.rendered.resolveNow.length > 0 && (
                    <div className="mt-3">
                      <div className="text-xs font-medium">
                        {localize(language, "Resolve now", "立即处理")}
                      </div>
                      <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                        {errorAnalysisQuery.data.rendered.resolveNow.map(
                          (item, idx) => (
                            <li
                              key={`resolve-now-${idx}`}
                              className="break-words"
                            >
                              {item}
                            </li>
                          ),
                        )}
                      </ul>
                    </div>
                  )}

                  {errorAnalysisQuery.data.rendered.preventionNextCall.length >
                    0 && (
                    <div className="mt-3">
                      <div className="text-xs font-medium">
                        {localize(
                          language,
                          "Prevention next call",
                          "下次调用预防措施",
                        )}
                      </div>
                      <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                        {errorAnalysisQuery.data.rendered.preventionNextCall.map(
                          (item, idx) => (
                            <li
                              key={`prevention-next-call-${idx}`}
                              className="break-words"
                            >
                              {item}
                            </li>
                          ),
                        )}
                      </ul>
                    </div>
                  )}
                </div>
              </div>
            ) : null
          ) : null}
        </div>
      </div>
    </div>
  );
}
