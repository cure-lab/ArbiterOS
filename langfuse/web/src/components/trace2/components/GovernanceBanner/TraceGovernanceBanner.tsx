import { useEffect, useMemo, useState } from "react";
import { api, directApi } from "@/src/utils/api";
import { useIsAuthenticatedAndProjectMember } from "@/src/features/auth/hooks";
import { useTraceData } from "@/src/components/trace2/contexts/TraceDataContext";
import { useSelection } from "@/src/components/trace2/contexts/SelectionContext";
import {
  derivePolicyNamesFromMetadata,
  getGovernanceDisplayLevel,
  getInactivateErrorTypeDisplayLabel,
  getMetadataRecord,
  getRelevantInactivateErrorType,
  isSessionOutputTurnObservationName,
  mergeRelevantPolicyMetadata,
} from "@/src/features/governance/utils/policyMetadata";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

const GOVERNANCE_REFRESH_INTERVAL_MS = 5_000;

export function TraceGovernanceBanner() {
  const { language } = useLanguage();
  const { trace, observations } = useTraceData();
  const { selectedNodeId, setSelectedNodeId } = useSelection();
  const hasProjectAccess = useIsAuthenticatedAndProjectMember(trace.projectId);
  const [errorTypeByObservationId, setErrorTypeByObservationId] = useState<
    Record<string, string | null>
  >({});
  const [isErrorTypeLoading, setIsErrorTypeLoading] = useState(false);
  const [expandedErrorType, setExpandedErrorType] = useState<string | null>(
    null,
  );
  const [expandedWarningType, setExpandedWarningType] = useState<string | null>(
    null,
  );
  const [expandedPolicyViolationType, setExpandedPolicyViolationType] =
    useState<string | null>(null);
  const [policyNamesByObservationId, setPolicyNamesByObservationId] = useState<
    Record<string, string[]>
  >({});
  const [isPolicyTypeLoading, setIsPolicyTypeLoading] = useState(false);

  const errorAnalysisSettingsQuery =
    api.projects.getErrorAnalysisSettings.useQuery(
      { projectId: trace.projectId },
      {
        enabled: hasProjectAccess,
        refetchOnWindowFocus: false,
        retry: false,
      },
    );

  const tracePolicyMetadata = useMemo(
    () => getMetadataRecord(trace.metadata),
    [trace.metadata],
  );

  const policyViolationObservations = useMemo(() => {
    return observations.filter((obs) => obs.level === "POLICY_VIOLATION");
  }, [observations]);

  const observationGovernanceState = useMemo(
    () =>
      Object.fromEntries(
        observations.map((obs) => [
          obs.id,
          {
            effectiveLevel: getGovernanceDisplayLevel({
              level: obs.level,
              observationMetadata: obs.metadata,
              traceMetadata: tracePolicyMetadata,
              observationName: obs.name,
              statusMessage: obs.statusMessage,
            }),
            inactivateErrorType: getRelevantInactivateErrorType({
              observationMetadata: obs.metadata,
              traceMetadata: tracePolicyMetadata,
              observationName: obs.name,
              statusMessage: obs.statusMessage,
            }),
          },
        ]),
      ),
    [observations, tracePolicyMetadata],
  );

  const {
    errorCount,
    warningCount,
    policyViolationCount,
    errorObservationIds,
    warningObservationIds,
    analysisObservationIds,
  } = useMemo(() => {
    const errorObservations = observations.filter(
      (obs) => observationGovernanceState[obs.id]?.effectiveLevel === "ERROR",
    );
    const errorCount = errorObservations.length;
    const warningObservations = observations.filter(
      (obs) => observationGovernanceState[obs.id]?.effectiveLevel === "WARNING",
    );
    const warningCount = warningObservations.length;
    const policyViolationCount = policyViolationObservations.length;
    const errorObservationIds = errorObservations.map((obs) => obs.id);
    const warningObservationIds = warningObservations.map((obs) => obs.id);

    return {
      errorCount,
      warningCount,
      policyViolationCount,
      errorObservationIds,
      warningObservationIds,
      analysisObservationIds: [
        ...new Set([...errorObservationIds, ...warningObservationIds]),
      ],
    };
  }, [observations, observationGovernanceState, policyViolationObservations]);

  useEffect(() => {
    if (!hasProjectAccess || analysisObservationIds.length === 0) {
      setErrorTypeByObservationId({});
      setIsErrorTypeLoading(false);
      return;
    }

    let cancelled = false;
    const fetchErrorTypes = async (isInitialFetch: boolean) => {
      if (isInitialFetch) {
        setIsErrorTypeLoading(true);
      }

      const results = await Promise.all(
        analysisObservationIds.map(async (observationId) => {
          try {
            const result = await directApi.errorAnalysis.getSummary.query({
              projectId: trace.projectId,
              traceId: trace.id,
              observationId,
            });

            return {
              observationId,
              errorType: result?.errorType ?? null,
            };
          } catch {
            return {
              observationId,
              errorType: null,
            };
          }
        }),
      );

      if (cancelled) return;

      setErrorTypeByObservationId(
        Object.fromEntries(
          results.map((item) => [item.observationId, item.errorType]),
        ),
      );
      if (isInitialFetch) {
        setIsErrorTypeLoading(false);
      }
    };

    void fetchErrorTypes(true);
    const intervalId = window.setInterval(() => {
      void fetchErrorTypes(false);
    }, GOVERNANCE_REFRESH_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [hasProjectAccess, analysisObservationIds, trace.projectId, trace.id]);

  useEffect(() => {
    if (!hasProjectAccess || policyViolationObservations.length === 0) {
      setPolicyNamesByObservationId({});
      setIsPolicyTypeLoading(false);
      return;
    }

    let cancelled = false;
    const fetchPolicyNames = async () => {
      setIsPolicyTypeLoading(true);
      const results = await Promise.all(
        policyViolationObservations.map(async (obs) => {
          try {
            const result = await directApi.observations.byId.query({
              projectId: trace.projectId,
              traceId: trace.id,
              observationId: obs.id,
              startTime: obs.startTime,
              verbosity: "compact",
            });
            return {
              observationId: obs.id,
              policyNames: derivePolicyNamesFromMetadata(
                mergeRelevantPolicyMetadata({
                  observationMetadata: result.metadata,
                  traceMetadata: tracePolicyMetadata,
                  observationName: obs.name,
                  statusMessage: obs.statusMessage,
                }),
              ),
            };
          } catch {
            return {
              observationId: obs.id,
              policyNames: [] as string[],
            };
          }
        }),
      );

      if (cancelled) return;

      setPolicyNamesByObservationId(
        Object.fromEntries(
          results.map((item) => [item.observationId, item.policyNames]),
        ),
      );
      setIsPolicyTypeLoading(false);
    };

    void fetchPolicyNames();

    return () => {
      cancelled = true;
    };
  }, [
    hasProjectAccess,
    policyViolationObservations,
    trace.projectId,
    trace.id,
    tracePolicyMetadata,
  ]);

  const errorGroups = useMemo(() => {
    const observationById = new Map(observations.map((obs) => [obs.id, obs]));
    const groups = new Map<
      string,
      {
        type: string;
        count: number;
        nodes: Array<{ id: string; label: string }>;
      }
    >();

    errorObservationIds.forEach((id) => {
      const type =
        observationGovernanceState[id]?.inactivateErrorType ??
        errorTypeByObservationId[id] ??
        "unclassified";
      const observation = observationById.get(id);
      const label = observation?.name?.trim() || id;
      const existing = groups.get(type);

      if (existing) {
        existing.count += 1;
        existing.nodes.push({ id, label });
      } else {
        groups.set(type, {
          type,
          count: 1,
          nodes: [{ id, label }],
        });
      }
    });

    return [...groups.values()].sort((a, b) => {
      if (b.count !== a.count) return b.count - a.count;
      return a.type.localeCompare(b.type);
    });
  }, [
    errorObservationIds,
    errorTypeByObservationId,
    observationGovernanceState,
    observations,
  ]);

  const warningGroups = useMemo(() => {
    const observationById = new Map(observations.map((obs) => [obs.id, obs]));
    const groups = new Map<
      string,
      {
        type: string;
        count: number;
        nodes: Array<{ id: string; label: string }>;
      }
    >();

    warningObservationIds.forEach((id) => {
      const obs = observationById.get(id);
      const inactivateType =
        observationGovernanceState[id]?.inactivateErrorType;

      let type: string;
      if (inactivateType) {
        type =
          getInactivateErrorTypeDisplayLabel(inactivateType) ?? "unclassified";
      } else if (
        obs &&
        isSessionOutputTurnObservationName(obs.name) &&
        obs.statusMessage?.trim()
      ) {
        type =
          getInactivateErrorTypeDisplayLabel(obs.statusMessage) ??
          "unclassified";
      } else {
        type = errorTypeByObservationId[id] ?? "unclassified";
      }

      const observation = obs;
      const label = observation?.name?.trim() || id;
      const existing = groups.get(type);

      if (existing) {
        existing.count += 1;
        existing.nodes.push({ id, label });
      } else {
        groups.set(type, {
          type,
          count: 1,
          nodes: [{ id, label }],
        });
      }
    });

    return [...groups.values()].sort((a, b) => {
      if (b.count !== a.count) return b.count - a.count;
      return a.type.localeCompare(b.type);
    });
  }, [
    errorTypeByObservationId,
    observationGovernanceState,
    observations,
    warningObservationIds,
  ]);

  const activeErrorGroup = useMemo(
    () => errorGroups.find((group) => group.type === expandedErrorType) ?? null,
    [errorGroups, expandedErrorType],
  );

  const activeWarningGroup = useMemo(
    () =>
      warningGroups.find((group) => group.type === expandedWarningType) ?? null,
    [warningGroups, expandedWarningType],
  );

  useEffect(() => {
    if (!expandedErrorType) return;
    const stillExists = errorGroups.some(
      (group) => group.type === expandedErrorType,
    );
    if (!stillExists) {
      setExpandedErrorType(null);
    }
  }, [errorGroups, expandedErrorType]);

  useEffect(() => {
    if (!expandedWarningType) return;
    const stillExists = warningGroups.some(
      (group) => group.type === expandedWarningType,
    );
    if (!stillExists) {
      setExpandedWarningType(null);
    }
  }, [expandedWarningType, warningGroups]);

  const policyViolationGroups = useMemo(() => {
    const groups = new Map<
      string,
      {
        type: string;
        count: number;
        nodes: Array<{ id: string; label: string }>;
      }
    >();

    policyViolationObservations.forEach((obs) => {
      const fetchedPolicyNames = policyNamesByObservationId[obs.id] ?? [];
      const metadataPolicyNames = derivePolicyNamesFromMetadata(
        mergeRelevantPolicyMetadata({
          observationMetadata: obs.metadata,
          traceMetadata: tracePolicyMetadata,
          observationName: obs.name,
          statusMessage: obs.statusMessage,
        }),
      );
      const policyNames = (
        fetchedPolicyNames.length > 0 ? fetchedPolicyNames : metadataPolicyNames
      ).filter(Boolean);
      const normalizedPolicyNames =
        policyNames.length > 0 ? policyNames : ["unclassified"];

      for (const policyName of normalizedPolicyNames) {
        const existing = groups.get(policyName);
        const node = {
          id: obs.id,
          label: obs.name?.trim() || obs.id,
        };
        if (existing) {
          existing.count += 1;
          existing.nodes.push(node);
        } else {
          groups.set(policyName, {
            type: policyName,
            count: 1,
            nodes: [node],
          });
        }
      }
    });

    return [...groups.values()].sort((a, b) => {
      if (b.count !== a.count) return b.count - a.count;
      return a.type.localeCompare(b.type);
    });
  }, [
    policyViolationObservations,
    policyNamesByObservationId,
    tracePolicyMetadata,
  ]);

  const activePolicyViolationGroup = useMemo(
    () =>
      policyViolationGroups.find(
        (group) => group.type === expandedPolicyViolationType,
      ) ?? null,
    [policyViolationGroups, expandedPolicyViolationType],
  );

  useEffect(() => {
    if (!expandedPolicyViolationType) return;
    const stillExists = policyViolationGroups.some(
      (group) => group.type === expandedPolicyViolationType,
    );
    if (!stillExists) {
      setExpandedPolicyViolationType(null);
    }
  }, [policyViolationGroups, expandedPolicyViolationType]);

  const enhancedGovernanceEnabled =
    errorAnalysisSettingsQuery.data?.enabled === true;
  return (
    <div className="rounded-md border bg-muted/30 px-3 py-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {enhancedGovernanceEnabled ? (
          <div className="flex items-center gap-1.5 text-xs font-medium text-foreground">
            <span>
              {localize(
                language,
                "Enhanced Governance Mode:",
                "增强治理模式：",
              )}
            </span>
            <span className="rounded-md border border-emerald-300 bg-emerald-50 px-2 py-0.5 font-medium text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">
              {localize(language, "Active", "已启用")}
            </span>
          </div>
        ) : null}
      </div>
      <div className="mt-1 text-sm text-muted-foreground">
        {localize(
          language,
          `Governance Summary: ${errorCount} errors, ${warningCount} warnings, ${policyViolationCount} policy violations across ${observations.length} nodes.`,
          `治理摘要：${errorCount} 个错误，${warningCount} 个警告，${policyViolationCount} 个策略违规，共涉及 ${observations.length} 个节点。`,
        )}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
        <span>{localize(language, "Error node types:", "错误节点类型：")}</span>
        {errorCount === 0 ? (
          <span className="font-bold text-foreground">
            {localize(language, "none", "无")}
          </span>
        ) : !hasProjectAccess ? (
          <span>
            {localize(
              language,
              "unavailable (project access required)",
              "不可用（需要项目访问权限）",
            )}
          </span>
        ) : isErrorTypeLoading && errorGroups.length === 0 ? (
          <span>{localize(language, "loading...", "加载中...")}</span>
        ) : (
          errorGroups.map((group) => {
            const isActive = expandedErrorType === group.type;
            return (
              <button
                key={group.type}
                type="button"
                className={`rounded-md border px-2 py-0.5 text-xs font-medium transition-colors ${
                  isActive
                    ? "border-red-500 bg-red-100 text-red-700 dark:border-red-700 dark:bg-red-900/40 dark:text-red-300"
                    : "border-red-300 bg-red-50 text-red-700 hover:bg-red-100 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300 dark:hover:bg-red-900/40"
                }`}
                onClick={() =>
                  setExpandedErrorType((prev) =>
                    prev === group.type ? null : group.type,
                  )
                }
              >
                {group.type}({group.count})
              </button>
            );
          })
        )}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
        <span>
          {localize(language, "Warning node types:", "警告节点类型：")}
        </span>
        {warningCount === 0 ? (
          <span className="font-bold text-foreground">
            {localize(language, "none", "无")}
          </span>
        ) : !hasProjectAccess ? (
          <span>
            {localize(
              language,
              "unavailable (project access required)",
              "不可用（需要项目访问权限）",
            )}
          </span>
        ) : isErrorTypeLoading && warningGroups.length === 0 ? (
          <span>{localize(language, "loading...", "加载中...")}</span>
        ) : (
          warningGroups.map((group) => {
            const isActive = expandedWarningType === group.type;
            return (
              <button
                key={group.type}
                type="button"
                className={`rounded-md border px-2 py-0.5 text-xs font-medium transition-colors ${
                  isActive
                    ? "border-amber-500 bg-amber-100 text-amber-700 dark:border-amber-700 dark:bg-amber-900/40 dark:text-amber-300"
                    : "border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300 dark:hover:bg-amber-900/40"
                }`}
                onClick={() =>
                  setExpandedWarningType((prev) =>
                    prev === group.type ? null : group.type,
                  )
                }
              >
                {group.type}({group.count})
              </button>
            );
          })
        )}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
        <span>
          {localize(
            language,
            "Policy violation node types:",
            "策略违规节点类型：",
          )}
        </span>
        {policyViolationCount === 0 ? (
          <span className="font-bold text-foreground">
            {localize(language, "none", "无")}
          </span>
        ) : isPolicyTypeLoading && policyViolationGroups.length === 0 ? (
          <span>{localize(language, "loading...", "加载中...")}</span>
        ) : (
          policyViolationGroups.map((group) => {
            const isActive = expandedPolicyViolationType === group.type;
            return (
              <button
                key={group.type}
                type="button"
                className={`rounded-md border px-2 py-0.5 text-xs font-medium transition-colors ${
                  isActive
                    ? "border-amber-500 bg-amber-100 text-amber-700 dark:border-amber-700 dark:bg-amber-900/40 dark:text-amber-300"
                    : "border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300 dark:hover:bg-amber-900/40"
                }`}
                onClick={() =>
                  setExpandedPolicyViolationType((prev) =>
                    prev === group.type ? null : group.type,
                  )
                }
              >
                {group.type}({group.count})
              </button>
            );
          })
        )}
      </div>
      {activePolicyViolationGroup ? (
        <div className="mt-2 rounded-md border border-amber-200 bg-amber-50/40 p-2 dark:border-amber-900 dark:bg-amber-950/20">
          <div className="mb-1 text-xs font-medium text-muted-foreground">
            {activePolicyViolationGroup.type} nodes (
            {activePolicyViolationGroup.count})
          </div>
          <div className="max-h-40 space-y-1 overflow-y-auto">
            {activePolicyViolationGroup.nodes.map((node) => {
              const isSelected = selectedNodeId === node.id;
              return (
                <button
                  key={node.id}
                  type="button"
                  className={`flex w-full flex-wrap items-center justify-between gap-2 rounded px-2 py-1 text-left text-xs transition-colors ${
                    isSelected
                      ? "bg-primary/10 text-primary"
                      : "text-foreground hover:bg-muted"
                  }`}
                  onClick={() => setSelectedNodeId(node.id)}
                >
                  <span className="line-clamp-1 break-all">{node.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
      {activeErrorGroup ? (
        <div className="mt-2 rounded-md border border-red-200 bg-red-50/40 p-2 dark:border-red-900 dark:bg-red-950/20">
          <div className="mb-1 text-xs font-medium text-muted-foreground">
            {activeErrorGroup.type} nodes ({activeErrorGroup.count})
          </div>
          <div className="max-h-40 space-y-1 overflow-y-auto">
            {activeErrorGroup.nodes.map((node) => {
              const isSelected = selectedNodeId === node.id;
              return (
                <button
                  key={node.id}
                  type="button"
                  className={`flex w-full items-start justify-between gap-2 rounded px-2 py-1 text-left text-xs transition-colors ${
                    isSelected
                      ? "bg-primary/10 text-primary"
                      : "text-foreground hover:bg-muted"
                  }`}
                  onClick={() => setSelectedNodeId(node.id)}
                >
                  <span className="line-clamp-1 break-all">{node.label}</span>
                  <span className="shrink-0 text-[10px] text-muted-foreground">
                    {node.id}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
      {activeWarningGroup ? (
        <div className="mt-2 rounded-md border border-amber-200 bg-amber-50/40 p-2 dark:border-amber-900 dark:bg-amber-950/20">
          <div className="mb-1 text-xs font-medium text-muted-foreground">
            {activeWarningGroup.type} nodes ({activeWarningGroup.count})
          </div>
          <div className="max-h-40 space-y-1 overflow-y-auto">
            {activeWarningGroup.nodes.map((node) => {
              const isSelected = selectedNodeId === node.id;
              return (
                <button
                  key={node.id}
                  type="button"
                  className={`flex w-full items-start justify-between gap-2 rounded px-2 py-1 text-left text-xs transition-colors ${
                    isSelected
                      ? "bg-primary/10 text-primary"
                      : "text-foreground hover:bg-muted"
                  }`}
                  onClick={() => setSelectedNodeId(node.id)}
                >
                  <span className="line-clamp-1 break-all">{node.label}</span>
                  <span className="shrink-0 text-[10px] text-muted-foreground">
                    {node.id}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
