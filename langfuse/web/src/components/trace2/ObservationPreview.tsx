import { PrettyJsonView } from "@/src/components/ui/PrettyJsonView";
import { type ScoreDomain, isGenerationLike } from "@langfuse/shared";
import { Badge } from "@/src/components/ui/badge";
import { type ObservationReturnType } from "@/src/server/api/routers/traces";
import { api } from "@/src/utils/api";
import { IOPreview } from "@/src/components/trace2/components/IOPreview/IOPreview";
import { formatIntervalSeconds } from "@/src/utils/dates";
import Link from "next/link";
import { usdFormatter, formatTokenCounts } from "@/src/utils/numbers";
import { withDefault, StringParam, useQueryParam } from "use-query-params";
import useLocalStorage from "@/src/components/useLocalStorage";
import { CommentDrawerButton } from "@/src/features/comments/CommentDrawerButton";
import { calculateDisplayTotalCost } from "@/src/components/trace2/lib/helpers";
import { Fragment, useMemo, useState } from "react";
import type Decimal from "decimal.js";
import { useIsAuthenticatedAndProjectMember } from "@/src/features/auth/hooks";
import {
  TabsBar,
  TabsBarList,
  TabsBarTrigger,
  TabsBarContent,
} from "@/src/components/ui/tabs-bar";
import {
  BreakdownTooltip,
  calculateAggregatedUsage,
} from "@/src/components/trace2/components/_shared/BreakdownToolTip";
import { ExternalLinkIcon, InfoIcon, PlusCircle } from "lucide-react";
import { UpsertModelFormDialog } from "@/src/features/models/components/UpsertModelFormDialog";
import { LocalIsoDate } from "@/src/components/LocalIsoDate";
import { ItemBadge } from "@/src/components/ItemBadge";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { Tabs, TabsList, TabsTrigger } from "@/src/components/ui/tabs";
import { Switch } from "@/src/components/ui/switch";
import { CopyIdsPopover } from "@/src/components/trace2/components/_shared/CopyIdsPopover";
import { useJsonExpansion } from "@/src/components/trace2/contexts/JsonExpansionContext";
import { type WithStringifiedMetadata } from "@/src/utils/clientSideDomainTypes";
import { useParsedObservation } from "@/src/hooks/useParsedObservation";
import { useJsonBetaToggle } from "@/src/components/trace2/hooks/useJsonBetaToggle";
import { getMostRecentCorrection } from "@/src/features/corrections/utils/getMostRecentCorrection";
import { buildKernelObservationIoSourceMap } from "@/src/components/trace2/lib/observationIoSource";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export const ObservationPreview = ({
  observations,
  projectId,
  serverScores: _scores,
  corrections,
  currentObservationId,
  traceId,
  commentCounts,
  viewType = "detailed",
  showCommentButton = false,
  precomputedCost,
}: {
  observations: Array<ObservationReturnType>;
  projectId: string;
  serverScores: WithStringifiedMetadata<ScoreDomain>[];
  corrections: ScoreDomain[];
  currentObservationId: string;
  traceId: string;
  commentCounts?: Map<string, number>;
  viewType?: "focused" | "detailed";
  showCommentButton?: boolean;
  precomputedCost: Decimal | undefined;
}) => {
  const { language } = useLanguage();
  const [, setSelectedTab] = useQueryParam(
    "view",
    withDefault(StringParam, "preview"),
  );
  const [currentView, setCurrentView] = useLocalStorage<
    "pretty" | "json" | "json-beta"
  >("jsonViewPreference", "pretty");
  const {
    jsonBetaEnabled,
    selectedViewTab,
    handleViewTabChange,
    handleBetaToggle,
  } = useJsonBetaToggle(currentView, setCurrentView);

  const capture = usePostHogClientCapture();
  const [isPrettyViewAvailable, setIsPrettyViewAvailable] = useState(false);

  const isAuthenticatedAndProjectMember =
    useIsAuthenticatedAndProjectMember(projectId);
  const {
    formattedExpansion,
    setFormattedFieldExpansion,
    jsonExpansion,
    setJsonFieldExpansion,
    advancedJsonExpansion,
    setAdvancedJsonExpansion,
  } = useJsonExpansion();

  const currentObservation = observations.find(
    (o) => o.id === currentObservationId,
  );
  const observationIoSourceById = useMemo(
    () => buildKernelObservationIoSourceMap(observations),
    [observations],
  );
  const ioSource = observationIoSourceById.get(currentObservationId);

  const currentObservationCorrections = corrections.filter(
    (c) => c.observationId === currentObservationId,
  );

  // Fetch and parse observation input/output in background (Web Worker)
  const {
    observation: observationWithIORaw,
    parsedInput,
    parsedOutput,
    parsedMetadata,
    isLoadingObservation,
    isWaitingForParsing,
  } = useParsedObservation({
    observationId: currentObservationId,
    traceId: traceId,
    projectId: projectId,
    startTime: currentObservation?.startTime,
    ioSourceObservationId: ioSource?.observationId,
    ioSourceStartTime: ioSource?.startTime,
    baseObservation: currentObservation,
  });

  // Type narrowing: when baseObservation is provided, result has full observation fields
  // (EventBatchIOOutput case only occurs when baseObservation is missing)
  const observationWithIO =
    observationWithIORaw && "type" in observationWithIORaw
      ? observationWithIORaw
      : undefined;

  const observationMedia = api.media.getByTraceOrObservationId.useQuery(
    {
      traceId: traceId,
      observationId: currentObservationId,
      projectId: projectId,
    },
    {
      enabled: isAuthenticatedAndProjectMember,
      refetchOnWindowFocus: false,
      refetchOnMount: false,
      refetchOnReconnect: false,
      staleTime: 50 * 60 * 1000, // 50 minutes
    },
  );

  const preloadedObservation = observations.find(
    (o) => o.id === currentObservationId,
  );

  const thisCost = preloadedObservation
    ? calculateDisplayTotalCost({
        allObservations: [preloadedObservation],
      })
    : undefined;

  const totalCost = precomputedCost;

  if (!preloadedObservation)
    return (
      <div className="flex-1">{localize(language, "Not found", "未找到")}</div>
    );

  return (
    <div className="col-span-2 flex h-full flex-1 flex-col overflow-hidden md:col-span-3">
      <div className="flex h-full flex-1 flex-col items-start gap-1 overflow-hidden @container">
        <div className="mt-2 grid w-full grid-cols-1 items-start gap-2 px-2 @2xl:grid-cols-[auto,auto] @2xl:justify-between">
          <div className="flex w-full flex-row items-start gap-1">
            <div className="mt-1.5">
              <ItemBadge type={preloadedObservation.type} isSmall />
            </div>
            <span className="mb-0 ml-1 line-clamp-2 min-w-0 break-all font-medium md:break-normal md:break-words">
              {preloadedObservation.name}
            </span>
            <CopyIdsPopover
              idItems={[
                {
                  id: preloadedObservation.traceId,
                  name: localize(language, "Trace ID", "Trace ID"),
                },
                {
                  id: preloadedObservation.id,
                  name: localize(language, "Observation ID", "Observation ID"),
                },
              ]}
            />
          </div>
          <div className="flex h-full flex-wrap content-start items-start justify-start gap-0.5 @2xl:mr-1 @2xl:justify-end">
            {viewType === "detailed" && (
              <>
                <CommentDrawerButton
                  projectId={preloadedObservation.projectId}
                  objectId={preloadedObservation.id}
                  objectType="OBSERVATION"
                  count={commentCounts?.get(preloadedObservation.id)}
                  size="sm"
                />
              </>
            )}
            {viewType === "focused" && showCommentButton && (
              <CommentDrawerButton
                projectId={preloadedObservation.projectId}
                objectId={preloadedObservation.id}
                objectType="OBSERVATION"
                count={commentCounts?.get(preloadedObservation.id)}
                size="sm"
              />
            )}
          </div>
        </div>
        <div className="grid w-full min-w-0 items-center justify-between px-2">
          <div className="flex min-w-0 max-w-full flex-shrink flex-col">
            <div className="mb-1 flex min-w-0 max-w-full flex-wrap items-center gap-1">
              <LocalIsoDate
                date={preloadedObservation.startTime}
                accuracy="millisecond"
                className="text-sm"
              />
            </div>
            <div className="flex min-w-0 max-w-full flex-wrap items-center gap-1">
              {viewType === "detailed" && (
                <Fragment>
                  {preloadedObservation.endTime ? (
                    <Badge variant="tertiary">
                      {localize(language, "Latency:", "延迟：")}{" "}
                      {formatIntervalSeconds(
                        (preloadedObservation.endTime.getTime() -
                          preloadedObservation.startTime.getTime()) /
                          1000,
                      )}
                    </Badge>
                  ) : null}

                  {preloadedObservation.timeToFirstToken ? (
                    <Badge variant="tertiary">
                      {localize(
                        language,
                        "Time to first token:",
                        "首词元时间：",
                      )}{" "}
                      {formatIntervalSeconds(
                        preloadedObservation.timeToFirstToken,
                      )}
                    </Badge>
                  ) : null}

                  {preloadedObservation.environment ? (
                    <Badge variant="tertiary">
                      {localize(language, "Env:", "环境：")}{" "}
                      {preloadedObservation.environment}
                    </Badge>
                  ) : null}

                  {thisCost ? (
                    <BreakdownTooltip
                      details={preloadedObservation.costDetails}
                      isCost={true}
                      pricingTierName={
                        preloadedObservation.usagePricingTierName ?? undefined
                      }
                    >
                      <Badge
                        variant="tertiary"
                        className="flex items-center gap-1"
                      >
                        <span>{usdFormatter(thisCost.toNumber())}</span>
                        <InfoIcon className="h-3 w-3" />
                      </Badge>
                    </BreakdownTooltip>
                  ) : undefined}
                  {totalCost && (!thisCost || !totalCost.equals(thisCost)) ? (
                    <Badge variant="tertiary">
                      ∑ {usdFormatter(totalCost.toNumber())}
                    </Badge>
                  ) : undefined}

                  {isGenerationLike(preloadedObservation.type) &&
                    (() => {
                      const aggregatedUsage = calculateAggregatedUsage(
                        preloadedObservation.usageDetails,
                      );

                      return (
                        <BreakdownTooltip
                          details={preloadedObservation.usageDetails}
                          isCost={false}
                          pricingTierName={
                            preloadedObservation.usagePricingTierName ??
                            undefined
                          }
                        >
                          <Badge
                            variant="tertiary"
                            className="flex items-center gap-1"
                          >
                            <span>
                              {formatTokenCounts(
                                aggregatedUsage.input,
                                aggregatedUsage.output,
                                aggregatedUsage.total,
                                true,
                              )}
                            </span>
                            <InfoIcon className="h-3 w-3" />
                          </Badge>
                        </BreakdownTooltip>
                      );
                    })()}
                  {preloadedObservation.version ? (
                    <Badge variant="tertiary">
                      {localize(language, "Version:", "版本：")}{" "}
                      {preloadedObservation.version}
                    </Badge>
                  ) : undefined}
                  {preloadedObservation.model ? (
                    preloadedObservation.internalModelId ? (
                      <Badge>
                        <Link
                          href={`/project/${preloadedObservation.projectId}/settings/models/${preloadedObservation.internalModelId}`}
                          className="flex items-center"
                          title={localize(
                            language,
                            "View model details",
                            "查看模型详情",
                          )}
                        >
                          <span className="truncate">
                            {preloadedObservation.model}
                          </span>
                          <ExternalLinkIcon className="ml-1 h-3 w-3" />
                        </Link>
                      </Badge>
                    ) : (
                      <UpsertModelFormDialog
                        action="create"
                        projectId={preloadedObservation.projectId}
                        prefilledModelData={{
                          modelName: preloadedObservation.model,
                          prices:
                            Object.keys(preloadedObservation.usageDetails)
                              .length > 0
                              ? Object.keys(preloadedObservation.usageDetails)
                                  .filter((key) => key != "total")
                                  .reduce(
                                    (acc, key) => {
                                      acc[key] = 0.000001;
                                      return acc;
                                    },
                                    {} as Record<string, number>,
                                  )
                              : undefined,
                        }}
                        className="cursor-pointer"
                      >
                        <Badge
                          variant="tertiary"
                          className="flex items-center gap-1"
                        >
                          <span>{preloadedObservation.model}</span>
                          <PlusCircle className="h-3 w-3" />
                        </Badge>
                      </UpsertModelFormDialog>
                    )
                  ) : null}

                  <Fragment>
                    {preloadedObservation.modelParameters &&
                    typeof preloadedObservation.modelParameters === "object"
                      ? Object.entries(preloadedObservation.modelParameters)
                          .filter(([_, value]) => value !== null)
                          .map(([key, value]) => {
                            const valueString =
                              Object.prototype.toString.call(value) ===
                              "[object Object]"
                                ? JSON.stringify(value)
                                : value?.toString();
                            return (
                              <Badge
                                variant="tertiary"
                                key={key}
                                className="h-6 max-w-md"
                              >
                                {/* CHILD: This span handles the text truncation */}
                                <span
                                  className="overflow-hidden text-ellipsis whitespace-nowrap"
                                  title={valueString}
                                >
                                  {key}: {valueString}
                                </span>
                              </Badge>
                            );
                          })
                      : null}
                  </Fragment>
                </Fragment>
              )}
            </div>
          </div>
        </div>

        <TabsBar
          value="preview"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
          onValueChange={() => setSelectedTab("preview")}
        >
          {viewType === "detailed" && (
            <TabsBarList>
              <TabsBarTrigger value="preview">
                {localize(language, "Preview", "预览")}
              </TabsBarTrigger>
              {isPrettyViewAvailable && (
                <>
                  <Tabs
                    className="ml-auto h-fit px-2 py-0.5"
                    value={selectedViewTab}
                    onValueChange={(value) => {
                      capture("trace_detail:io_mode_switch", { view: value });
                      handleViewTabChange(value);
                    }}
                  >
                    <TabsList className="h-fit py-0.5">
                      <TabsTrigger
                        value="pretty"
                        className="h-fit px-1 text-xs"
                      >
                        {localize(language, "Formatted", "格式化")}
                      </TabsTrigger>
                      <TabsTrigger value="json" className="h-fit px-1 text-xs">
                        JSON
                      </TabsTrigger>
                    </TabsList>
                  </Tabs>
                  {selectedViewTab === "json" && (
                    <div className="mr-1 flex items-center gap-1.5">
                      <Switch
                        size="sm"
                        checked={jsonBetaEnabled}
                        onCheckedChange={handleBetaToggle}
                      />
                      <span className="text-xs text-muted-foreground">
                        {localize(language, "Beta", "测试版")}
                      </span>
                    </div>
                  )}
                </>
              )}
            </TabsBarList>
          )}
          <TabsBarContent
            value="preview"
            className="mt-0 flex max-h-full min-h-0 w-full flex-1 pr-2"
          >
            <div
              className={`mb-2 flex max-h-full min-h-0 w-full flex-col gap-2 overflow-y-auto ${
                currentView === "json-beta" ? "" : "pb-4"
              }`}
            >
              <div>
                <IOPreview
                  key={preloadedObservation.id + "-input"}
                  observationName={preloadedObservation.name ?? undefined}
                  input={observationWithIO?.input ?? undefined}
                  output={observationWithIO?.output ?? undefined}
                  metadata={observationWithIO?.metadata ?? undefined}
                  parsedInput={parsedInput}
                  parsedOutput={parsedOutput}
                  parsedMetadata={parsedMetadata}
                  outputCorrection={getMostRecentCorrection(
                    currentObservationCorrections,
                  )}
                  observationId={currentObservationId}
                  isLoading={isLoadingObservation}
                  isParsing={isWaitingForParsing}
                  media={observationMedia.data}
                  currentView={currentView}
                  setIsPrettyViewAvailable={setIsPrettyViewAvailable}
                  inputExpansionState={formattedExpansion.input}
                  outputExpansionState={formattedExpansion.output}
                  onInputExpansionChange={(expansion) =>
                    setFormattedFieldExpansion(
                      "input",
                      expansion as Record<string, boolean>,
                    )
                  }
                  onOutputExpansionChange={(expansion) =>
                    setFormattedFieldExpansion(
                      "output",
                      expansion as Record<string, boolean>,
                    )
                  }
                  jsonInputExpanded={jsonExpansion.input}
                  jsonOutputExpanded={jsonExpansion.output}
                  onJsonInputExpandedChange={(expanded) =>
                    setJsonFieldExpansion("input", expanded)
                  }
                  onJsonOutputExpandedChange={(expanded) =>
                    setJsonFieldExpansion("output", expanded)
                  }
                  advancedJsonExpansionState={advancedJsonExpansion}
                  onAdvancedJsonExpansionChange={setAdvancedJsonExpansion}
                  projectId={projectId}
                  traceId={traceId}
                  environment={preloadedObservation.environment}
                />
              </div>
              <div>
                {preloadedObservation.statusMessage &&
                  preloadedObservation.level !== "POLICY_VIOLATION" && (
                    <PrettyJsonView
                      key={preloadedObservation.id + "-status"}
                      title={localize(language, "Status Message", "状态消息")}
                      json={preloadedObservation.statusMessage}
                      currentView={
                        currentView === "json-beta" ? "pretty" : currentView
                      }
                    />
                  )}
              </div>
              <div className="px-2">
                {observationWithIO?.metadata && (
                  <PrettyJsonView
                    key={observationWithIO.id + "-metadata"}
                    title={localize(language, "Metadata", "元数据")}
                    json={observationWithIO.metadata}
                    media={observationMedia.data?.filter(
                      (m) => m.field === "metadata",
                    )}
                    currentView={
                      currentView === "json-beta" ? "pretty" : currentView
                    }
                    externalExpansionState={formattedExpansion.metadata}
                    onExternalExpansionChange={(expansion) =>
                      setFormattedFieldExpansion(
                        "metadata",
                        expansion as Record<string, boolean>,
                      )
                    }
                  />
                )}
              </div>
            </div>
          </TabsBarContent>
        </TabsBar>
      </div>
    </div>
  );
};
