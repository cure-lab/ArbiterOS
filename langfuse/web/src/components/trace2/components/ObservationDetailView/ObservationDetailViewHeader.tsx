/**
 * ObservationDetailViewHeader - Extracted header component for ObservationDetailView
 *
 * Contains:
 * - Title row with ItemBadge, observation name, CopyIdsPopover
 * - Action buttons (Dataset, Annotate, Queue, Playground, Comments)
 * - Metadata badges (timestamp, latency, environment, cost, usage, model, etc.)
 *
 * Memoized to prevent unnecessary re-renders when tab state changes.
 */

import { memo } from "react";
import { type ObservationType } from "@langfuse/shared";
import { type SelectionData } from "@/src/features/comments/contexts/InlineCommentSelectionContext";
import { type ObservationReturnTypeWithMetadata } from "@/src/server/api/routers/traces";
import { ItemBadge } from "@/src/components/ItemBadge";
import { LocalIsoDate } from "@/src/components/LocalIsoDate";
import { CopyIdsPopover } from "@/src/components/trace2/components/_shared/CopyIdsPopover";
import { CommentDrawerButton } from "@/src/features/comments/CommentDrawerButton";
import { ErrorAnalysisButton } from "@/src/features/error-analysis/components/ErrorAnalysisButton";
import { api } from "@/src/utils/api";
import {
  getGovernanceDisplayLevel,
  getRelevantInactivateErrorType,
} from "@/src/features/governance/utils/policyMetadata";
import {
  LatencyBadge,
  TimeToFirstTokenBadge,
  EnvironmentBadge,
  VersionBadge,
  LevelBadge,
  StatusMessageBadge,
  ErrorTypeBadge,
  PolicyNameBadges,
} from "./ObservationMetadataBadgesSimple";
import {
  SessionBadge,
  UserIdBadge,
} from "../TraceDetailView/TraceMetadataBadges";
import { CostBadge, UsageBadge } from "./ObservationMetadataBadgesTooltip";
import { ModelBadge } from "./ObservationMetadataBadgeModel";
import { ModelParametersBadges } from "./ObservationMetadataBadgeModelParameters";
import { formatParserNodeName } from "@/src/features/trace-graph-view/nodeNameUtils";
import { type AggregatedTraceMetrics } from "@/src/components/trace2/lib/trace-aggregation";
import type Decimal from "decimal.js";

export interface ObservationDetailViewHeaderProps {
  observation: ObservationReturnTypeWithMetadata;
  projectId: string;
  traceId: string;
  traceMetadata?: unknown;
  latencySeconds: number | null;
  commentCount: number | undefined;
  // Inline comment props
  pendingSelection?: SelectionData | null;
  onSelectionUsed?: () => void;
  isCommentDrawerOpen?: boolean;
  onCommentDrawerOpenChange?: (open: boolean) => void;
  subtreeMetrics?: AggregatedTraceMetrics | null;
  treeNodeTotalCost?: Decimal;
}

export const ObservationDetailViewHeader = memo(
  function ObservationDetailViewHeader({
    observation,
    projectId,
    traceId,
    traceMetadata,
    latencySeconds,
    commentCount,
    pendingSelection,
    onSelectionUsed,
    isCommentDrawerOpen,
    onCommentDrawerOpenChange,
    subtreeMetrics,
    treeNodeTotalCost,
  }: ObservationDetailViewHeaderProps) {
    // Format cost and usage values
    const totalCost = observation.totalCost;
    const totalUsage = observation.totalUsage;
    const inputUsage = observation.inputUsage;
    const outputUsage = observation.outputUsage;
    const rawObservationName = observation.name || observation.id;
    const readableObservationName =
      formatParserNodeName(rawObservationName, { multiline: false }) ??
      rawObservationName;
    const hasReadableAlias = readableObservationName !== rawObservationName;
    const inactivateErrorType = getRelevantInactivateErrorType({
      observationMetadata: observation.metadata,
      traceMetadata,
      observationName: observation.name,
      statusMessage: observation.statusMessage,
    });
    const effectiveLevel = getGovernanceDisplayLevel({
      level: observation.level,
      observationMetadata: observation.metadata,
      traceMetadata,
      observationName: observation.name,
      statusMessage: observation.statusMessage,
    });
    const shouldHideErrorAnalysis = Boolean(inactivateErrorType);

    const shouldFetchErrorType =
      !shouldHideErrorAnalysis &&
      (effectiveLevel === "ERROR" || effectiveLevel === "WARNING");
    const { data: errorTypeSummary } = api.errorAnalysis.getSummary.useQuery(
      {
        projectId,
        traceId,
        observationId: observation.id,
      },
      {
        enabled: shouldFetchErrorType,
        refetchOnWindowFocus: false,
      },
    );

    return (
      <div className="flex-shrink-0 space-y-2 border-b p-2 @container">
        {/* Title row with actions */}
        <div className="grid w-full grid-cols-1 items-start gap-2 @2xl:grid-cols-[auto,auto] @2xl:justify-between">
          <div className="flex w-full flex-row items-start gap-1">
            <div className="mt-1.5">
              <ItemBadge type={observation.type as ObservationType} isSmall />
            </div>
            <span
              className="mb-0 ml-1 line-clamp-2 min-w-0 break-all font-medium md:break-normal md:break-words"
              title={hasReadableAlias ? rawObservationName : undefined}
            >
              {readableObservationName}
            </span>
            <CopyIdsPopover
              idItems={[
                { id: traceId, name: "Trace ID" },
                { id: observation.id, name: "Observation ID" },
              ]}
            />
          </div>
          {/* Action buttons */}
          <div className="flex h-full flex-wrap content-start items-start justify-start gap-0.5 @2xl:mr-1 @2xl:justify-end">
            <CommentDrawerButton
              projectId={projectId}
              objectId={observation.id}
              objectType="OBSERVATION"
              count={commentCount}
              size="sm"
              pendingSelection={pendingSelection}
              onSelectionUsed={onSelectionUsed}
              isOpen={isCommentDrawerOpen}
              onOpenChange={onCommentDrawerOpenChange}
            />
          </div>
        </div>

        {/* Metadata badges */}
        <div className="flex flex-col gap-2">
          {/* Timestamp */}
          <div className="flex flex-wrap items-center gap-1">
            <LocalIsoDate
              date={observation.startTime}
              accuracy="millisecond"
              className="text-sm"
            />
          </div>

          {/* Other badges */}
          <div className="flex flex-wrap items-center gap-1">
            <LatencyBadge latencySeconds={latencySeconds} />
            <TimeToFirstTokenBadge
              timeToFirstToken={observation.timeToFirstToken}
            />
            <SessionBadge
              sessionId={observation.sessionId ?? null}
              projectId={projectId}
            />
            <UserIdBadge
              userId={observation.userId ?? null}
              projectId={projectId}
            />
            <EnvironmentBadge environment={observation.environment} />
            <CostBadge
              totalCost={
                subtreeMetrics
                  ? (treeNodeTotalCost?.toNumber() ?? subtreeMetrics.totalCost)
                  : totalCost
              }
              costDetails={
                subtreeMetrics?.costDetails ?? observation.costDetails
              }
            />
            {subtreeMetrics ? (
              subtreeMetrics.hasGenerationLike &&
              subtreeMetrics.usageDetails && (
                <UsageBadge
                  type="GENERATION"
                  inputUsage={subtreeMetrics.inputUsage}
                  outputUsage={subtreeMetrics.outputUsage}
                  totalUsage={subtreeMetrics.totalUsage}
                  usageDetails={subtreeMetrics.usageDetails}
                />
              )
            ) : (
              <UsageBadge
                type={observation.type}
                inputUsage={inputUsage}
                outputUsage={outputUsage}
                totalUsage={totalUsage}
                usageDetails={observation.usageDetails}
              />
            )}
            <VersionBadge version={observation.version} />
            <ModelBadge
              model={observation.model}
              internalModelId={observation.internalModelId}
              projectId={projectId}
              usageDetails={observation.usageDetails}
            />
            <ModelParametersBadges
              modelParameters={observation.modelParameters}
            />
            <LevelBadge level={effectiveLevel} />
            <PolicyNameBadges
              level={observation.level}
              metadata={observation.metadata}
            />
            {!shouldHideErrorAnalysis ? (
              <>
                <ErrorTypeBadge
                  errorType={errorTypeSummary?.errorType}
                  errorTypeDescription={errorTypeSummary?.errorTypeDescription}
                  errorTypeWhy={errorTypeSummary?.errorTypeWhy}
                  errorTypeConfidence={errorTypeSummary?.errorTypeConfidence}
                />
                <ErrorAnalysisButton
                  projectId={projectId}
                  traceId={traceId}
                  observationId={observation.id}
                  level={effectiveLevel}
                />
              </>
            ) : null}
            <StatusMessageBadge
              statusMessage={observation.statusMessage}
              level={effectiveLevel}
            />
          </div>
        </div>
      </div>
    );
  },
);
