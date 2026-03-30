"use client";

import { useMemo, useState } from "react";
import { type ObservationLevelType } from "@langfuse/shared";
import { Button } from "@/src/components/ui/button";
import { showErrorToast } from "@/src/features/notifications/showErrorToast";
import { showSuccessToast } from "@/src/features/notifications/showSuccessToast";
import { api } from "@/src/utils/api";
import { cn } from "@/src/utils/tailwind";
import { type ErrorAnalysisModel } from "../types";

const BULK_ANALYSIS_MODEL: ErrorAnalysisModel = "gpt-5.2";

export type BulkErrorAnalysisTarget = {
  observationId: string;
  traceId?: string;
  level?: ObservationLevelType;
};

function isAnalyzableLevel(level: ObservationLevelType | undefined): boolean {
  return level === undefined || level === "ERROR" || level === "WARNING";
}

export function BulkErrorAnalysisButton(props: {
  projectId: string;
  targets: BulkErrorAnalysisTarget[];
  className?: string;
  onCompleted?: () => void;
}) {
  const { projectId, targets, className, onCompleted } = props;
  const [isRunning, setIsRunning] = useState(false);
  const analyze = api.errorAnalysis.analyze.useMutation();
  const utils = api.useUtils();

  const validTargets = useMemo(() => {
    const deduped = new Map<string, BulkErrorAnalysisTarget>();
    for (const target of targets) {
      if (!target.traceId) continue;
      if (!isAnalyzableLevel(target.level)) continue;
      deduped.set(target.observationId, target);
    }
    return [...deduped.values()];
  }, [targets]);

  const selectedCount = targets.length;
  const skippedCount = Math.max(0, selectedCount - validTargets.length);

  const handleBulkAnalyze = async () => {
    if (isRunning || validTargets.length === 0) return;

    setIsRunning(true);
    let successCount = 0;
    let failedCount = 0;
    let firstError: string | null = null;

    for (const target of validTargets) {
      try {
        await analyze.mutateAsync({
          projectId,
          traceId: target.traceId!,
          observationId: target.observationId,
          model: BULK_ANALYSIS_MODEL,
          maxContextChars: 80_000,
          timestamp: null,
          fromTimestamp: null,
          verbosity: "full",
        });
        successCount += 1;
      } catch (error) {
        failedCount += 1;
        if (!firstError) {
          firstError =
            error instanceof Error
              ? error.message
              : "Unknown error while running analysis.";
        }
      }
    }

    await Promise.allSettled([
      utils.errorAnalysis.get.invalidate(),
      utils.errorAnalysis.getSummary.invalidate(),
      utils.events.filterOptions.invalidate(),
      utils.generations.filterOptions.invalidate(),
      // Refresh visible tables so "pending_to_analysis" rows disappear immediately.
      utils.events.all.invalidate(),
      utils.events.countAll.invalidate(),
      utils.generations.all.invalidate(),
      utils.generations.countAll.invalidate(),
    ]);

    setIsRunning(false);
    onCompleted?.();

    if (successCount > 0) {
      showSuccessToast({
        title: "Analysis completed",
        description: `Generated ${successCount} error analysis report${successCount === 1 ? "" : "s"} and refreshed classifications.`,
      });
    }

    if (failedCount > 0) {
      showErrorToast(
        "Some analyses failed",
        `${failedCount} row${failedCount === 1 ? "" : "s"} could not be analyzed.${firstError ? ` First error: ${firstError}` : ""}`,
        "WARNING",
      );
    }

    if (skippedCount > 0) {
      showErrorToast(
        "Some rows were skipped",
        `${skippedCount} selected row${skippedCount === 1 ? "" : "s"} did not contain analyzable ERROR/WARNING observations.`,
        "WARNING",
      );
    }
  };

  return (
    <Button
      variant="outline"
      className={cn("w-[130px] justify-between", className)}
      loading={isRunning}
      disabled={validTargets.length === 0}
      onClick={() => void handleBulkAnalyze()}
      aria-label="Run batch error analysis for selected rows"
    >
      <span>Analysis</span>
      <div className="ml-1 rounded-sm bg-input px-1 text-xs">
        {selectedCount}
      </div>
    </Button>
  );
}
