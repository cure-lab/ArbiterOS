import { useEffect, useMemo, useState } from "react";
import { api } from "@/src/utils/api";
import { type FilterState } from "@langfuse/shared";
import { type ViewVersion } from "@/src/features/query";
import { usePeekData } from "@/src/components/table/peek/hooks/usePeekData";
import { Trace } from "@/src/components/trace2/Trace";
import { Badge } from "@/src/components/ui/badge";
import { NoDataOrLoading } from "@/src/components/NoDataOrLoading";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/src/components/ui/sheet";
import { cn } from "@/src/utils/tailwind";
import { useV4Beta } from "@/src/features/events/hooks/useV4Beta";
import { StringParam, useQueryParam } from "use-query-params";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";

type PolicyConfirmationState = "accepted" | "rejected";

type PolicyConfirmationDetail = {
  traceId: string;
  traceName: string | null;
  turnIndex: number | null;
};

type TurnNode = {
  id: string;
  label: string;
};

function detailKey(detail: PolicyConfirmationDetail) {
  return `${detail.traceId}::${detail.turnIndex ?? "null"}`;
}

function isSessionTurnNodeLabel(label: string) {
  return /^session\.turn\.\d+$/i.test(label.trim());
}

export function PolicyConfirmationDetailsSheet(props: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  policyName: string;
  state: PolicyConfirmationState;
  globalFilterState: FilterState;
  fromTimestamp: Date;
  toTimestamp: Date;
  metricsVersion?: ViewVersion;
}) {
  const {
    open,
    onOpenChange,
    projectId,
    policyName,
    state,
    globalFilterState,
    fromTimestamp,
    toTimestamp,
    metricsVersion,
  } = props;
  const { t } = useLanguage();
  const [selectedDetailKey, setSelectedDetailKey] = useState<string | null>(
    null,
  );
  const [selectedObservationId, setSelectedObservationId] = useQueryParam(
    "observation",
    StringParam,
  );
  const { isBetaEnabled } = useV4Beta();

  const detailsQuery = api.dashboard.policyConfirmationDetails.useQuery(
    {
      projectId,
      policyName,
      state,
      globalFilterState,
      fromTimestamp,
      toTimestamp,
      version: metricsVersion,
    },
    {
      enabled: open,
      trpc: {
        context: {
          skipBatch: true,
        },
      },
    },
  );

  const details = useMemo(
    () => (detailsQuery.data as PolicyConfirmationDetail[] | undefined) ?? [],
    [detailsQuery.data],
  );

  useEffect(() => {
    if (!open) {
      setSelectedDetailKey(null);
      return;
    }

    if (details.length === 0) {
      setSelectedDetailKey(null);
      return;
    }

    setSelectedDetailKey((current) => {
      if (current && details.some((detail) => detailKey(detail) === current)) {
        return current;
      }

      return detailKey(details[0]!);
    });
  }, [details, open]);

  const selectedDetail = useMemo(
    () =>
      details.find((detail) => detailKey(detail) === selectedDetailKey) ?? null,
    [details, selectedDetailKey],
  );

  const selectedTrace = usePeekData({
    projectId,
    traceId: selectedDetail?.traceId,
  });

  const graphTimeRange = useMemo(() => {
    const observations = selectedTrace.data?.observations ?? [];
    if (observations.length === 0) {
      return { minStartTime: null, maxStartTime: null };
    }

    let minTime = Infinity;
    let maxTime = 0;
    for (const observation of observations) {
      const timestamp = observation.startTime.getTime();
      if (timestamp < minTime) minTime = timestamp;
      if (timestamp > maxTime) maxTime = timestamp;
    }

    return {
      minStartTime: new Date(minTime).toISOString(),
      maxStartTime: new Date(maxTime).toISOString(),
    };
  }, [selectedTrace.data]);

  const graphQueryInput = {
    projectId,
    traceId: selectedDetail?.traceId ?? "",
    minStartTime: graphTimeRange.minStartTime ?? "",
    maxStartTime: graphTimeRange.maxStartTime ?? "",
  };

  const graphQueryEnabled =
    !!selectedDetail?.traceId &&
    graphTimeRange.minStartTime !== null &&
    graphTimeRange.maxStartTime !== null;

  const tracesGraphQuery = api.traces.getAgentGraphData.useQuery(
    graphQueryInput,
    {
      enabled: graphQueryEnabled && !isBetaEnabled,
      refetchOnWindowFocus: false,
      refetchOnMount: false,
      refetchOnReconnect: false,
      staleTime: 50 * 60 * 1000,
    },
  );

  const eventsGraphQuery = api.events.getAgentGraphData.useQuery(
    graphQueryInput,
    {
      enabled: graphQueryEnabled && isBetaEnabled,
      refetchOnWindowFocus: false,
      refetchOnMount: false,
      refetchOnReconnect: false,
      staleTime: 50 * 60 * 1000,
    },
  );

  const selectedGraphData = isBetaEnabled
    ? (eventsGraphQuery.data ?? [])
    : (tracesGraphQuery.data ?? []);
  const isGraphLoading = isBetaEnabled
    ? eventsGraphQuery.isLoading
    : tracesGraphQuery.isLoading;

  const selectedTurnNodes = useMemo<TurnNode[]>(() => {
    if (!selectedDetail) return [];

    const seen = new Set<string>();
    const nodes: TurnNode[] = [];

    for (const node of selectedGraphData) {
      if (node.turnIndex !== selectedDetail.turnIndex) {
        continue;
      }

      const label = (
        node.node ??
        node.name ??
        t("dashboard.policyConfirmationDetails.unnamedNode")
      ).trim();
      if (!isSessionTurnNodeLabel(label)) {
        continue;
      }

      if (seen.has(node.id)) {
        continue;
      }

      seen.add(node.id);
      nodes.push({
        id: node.id,
        label,
      });
    }

    return nodes;
  }, [selectedDetail, selectedGraphData]);

  useEffect(() => {
    if (selectedTurnNodes.length === 0) {
      setSelectedObservationId(null);
      return;
    }

    setSelectedObservationId((current) => {
      if (current && selectedTurnNodes.some((node) => node.id === current)) {
        return current;
      }

      return selectedTurnNodes[0]!.id;
    });
  }, [selectedTurnNodes, setSelectedObservationId]);

  const stateLabel =
    state === "accepted"
      ? t("dashboard.policyConfirmationDetails.stateAccepted")
      : t("dashboard.policyConfirmationDetails.stateRejected");

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex min-h-0 min-w-[92vw] max-w-none flex-col gap-0 overflow-hidden p-0">
        <SheetHeader className="border-b px-6 py-4">
          <SheetTitle>
            {stateLabel} {t("dashboard.policyConfirmationDetails.turnsFor")}{" "}
            {policyName}
          </SheetTitle>
          <SheetDescription>
            {t("dashboard.policyConfirmationDetails.selectTurnInspect")}
          </SheetDescription>
        </SheetHeader>

        <div className="flex min-h-0 flex-1 overflow-hidden">
          <div className="flex w-[22rem] min-w-[22rem] flex-col border-r">
            <div className="border-b px-4 py-3 text-sm font-medium">
              {t("dashboard.policyConfirmationDetails.matchingTurns")} (
              {details.length})
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {details.length > 0 ? (
                <div className="flex flex-col gap-2">
                  {details.map((detail) => {
                    const isSelected = detailKey(detail) === selectedDetailKey;

                    return (
                      <button
                        key={detailKey(detail)}
                        type="button"
                        onClick={() => setSelectedDetailKey(detailKey(detail))}
                        className={cn(
                          "rounded-md border p-3 text-left transition-colors",
                          isSelected
                            ? "border-primary bg-primary/5"
                            : "hover:bg-muted/50",
                        )}
                      >
                        <div className="truncate text-sm font-semibold text-foreground">
                          {detail.traceName || detail.traceId}
                        </div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {t("dashboard.policyConfirmationDetails.traceId")}:{" "}
                          {detail.traceId}
                        </div>
                        <div className="mt-2">
                          <Badge variant="secondary">
                            {t("dashboard.policyConfirmationDetails.turn")}{" "}
                            {detail.turnIndex ?? "-"}
                          </Badge>
                        </div>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <NoDataOrLoading
                  isLoading={detailsQuery.isLoading}
                  description={t(
                    "dashboard.policyConfirmationDetails.noMatchingTurns",
                  )}
                />
              )}
            </div>
          </div>

          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="border-b px-4 py-3">
              <div className="text-sm font-medium text-foreground">
                {selectedDetail
                  ? `${t("dashboard.policyConfirmationDetails.nodesInTurn")} ${selectedDetail.turnIndex ?? "-"}`
                  : t("dashboard.policyConfirmationDetails.turnNodes")}
              </div>
              <div className="mt-2 flex flex-wrap gap-2">
                {selectedDetail ? (
                  selectedTrace.isLoading || isGraphLoading ? (
                    <span className="text-xs text-muted-foreground">
                      {t("dashboard.policyConfirmationDetails.loadingNodes")}
                    </span>
                  ) : selectedTurnNodes.length > 0 ? (
                    selectedTurnNodes.map((node) => {
                      const isSelected = selectedObservationId === node.id;

                      return (
                        <button
                          key={node.id}
                          type="button"
                          onClick={() => setSelectedObservationId(node.id)}
                          className={cn(
                            "rounded-md border px-2 py-1 text-xs font-medium transition-colors",
                            isSelected
                              ? "border-primary bg-primary/10 text-primary"
                              : "border-border hover:bg-muted",
                          )}
                        >
                          {node.label}
                        </button>
                      );
                    })
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      {t("dashboard.policyConfirmationDetails.noNodesForTurn")}
                    </span>
                  )
                ) : (
                  <span className="text-xs text-muted-foreground">
                    {t(
                      "dashboard.policyConfirmationDetails.selectTurnFromList",
                    )}
                  </span>
                )}
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-hidden">
              {selectedDetail ? (
                selectedTrace.data ? (
                  <Trace
                    key={selectedTrace.data.id}
                    trace={selectedTrace.data}
                    scores={selectedTrace.data.scores}
                    corrections={selectedTrace.data.corrections}
                    projectId={selectedTrace.data.projectId}
                    policyConfirmationTurnIndexes={
                      "policyConfirmationTurnIndexes" in selectedTrace.data
                        ? selectedTrace.data.policyConfirmationTurnIndexes
                        : undefined
                    }
                    observations={selectedTrace.data.observations}
                    context="peek"
                  />
                ) : (
                  <div className="flex h-full items-center justify-center p-4">
                    <NoDataOrLoading
                      isLoading={selectedTrace.isLoading}
                      description={t(
                        "dashboard.policyConfirmationDetails.unableToLoadTrace",
                      )}
                    />
                  </div>
                )
              ) : (
                <div className="flex h-full items-center justify-center p-4">
                  <NoDataOrLoading
                    isLoading={detailsQuery.isLoading}
                    description={t(
                      "dashboard.policyConfirmationDetails.selectTurnInspectTrace",
                    )}
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
