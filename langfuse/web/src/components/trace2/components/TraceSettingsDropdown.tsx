/**
 * TraceSettingsDropdown - View preferences dropdown component
 *
 * Provides toggles for:
 * - Show Comments
 * - Show Scores
 * - Show Duration
 * - Show Cost/Tokens
 * - Color Code Metrics (dependent on duration or cost being enabled)
 * - Minimum Observation Level filter
 * - Show Graph (hidden when graph view not available)
 *
 * All preferences are managed via ViewPreferencesContext and persisted to localStorage.
 */

import { type ObservationLevelType, ObservationLevel } from "@langfuse/shared";
import { Settings2 } from "lucide-react";
import { Button } from "@/src/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
  DropdownMenuLabel,
} from "@/src/components/ui/dropdown-menu";
import { Switch } from "@/src/components/ui/switch";
import { cn } from "@/src/utils/tailwind";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { useViewPreferences } from "../contexts/ViewPreferencesContext";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export interface TraceSettingsDropdownProps {
  isGraphViewAvailable: boolean;
}

export function TraceSettingsDropdown({
  isGraphViewAvailable,
}: TraceSettingsDropdownProps) {
  const capture = usePostHogClientCapture();
  const { language } = useLanguage();

  // Get all preferences directly from context
  const {
    showGraph,
    setShowGraph,
    graphViewMode,
    setGraphViewMode,
    showComments,
    setShowComments,
    showScores,
    setShowScores,
    showDuration,
    setShowDuration,
    showCostTokens,
    setShowCostTokens,
    colorCodeMetrics,
    setColorCodeMetrics,
    minObservationLevel,
    setMinObservationLevel,
  } = useViewPreferences();

  // Color coding is only available when duration or cost metrics are shown
  const isColorCodeEnabled = showDuration || showCostTokens;
  const isHierarchyGraphMode = graphViewMode === "hierarchy";
  const localizeObservationLevel = (level: ObservationLevelType) => {
    switch (level) {
      case ObservationLevel.DEBUG:
        return localize(language, "DEBUG", "调试");
      case ObservationLevel.DEFAULT:
        return localize(language, "DEFAULT", "默认");
      case ObservationLevel.WARNING:
        return localize(language, "WARNING", "警告");
      case ObservationLevel.ERROR:
        return localize(language, "ERROR", "错误");
      case ObservationLevel.POLICY_VIOLATION:
        return localize(language, "POLICY_VIOLATION", "策略违规");
      default:
        return level;
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          title={localize(language, "View Options", "视图选项")}
          className="h-7 w-7"
        >
          <Settings2 className="h-3.5 w-3.5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-64 space-x-0 space-y-0 p-0 px-0"
      >
        <DropdownMenuLabel>
          {localize(language, "View Options", "视图选项")}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />

        <div className="space-y-0 p-0 py-1">
          {/* Show Graph Toggle (only when available) */}
          {isGraphViewAvailable && (
            <DropdownMenuItem
              asChild
              onSelect={(e) => e.preventDefault()}
              className="space-y-0 px-2 py-1"
            >
              <div className="flex w-full items-center justify-between">
                <span className="mr-2">
                  {localize(language, "Show Graph", "显示图表")}
                </span>
                <Switch
                  size="sm"
                  checked={showGraph}
                  onCheckedChange={setShowGraph}
                />
              </div>
            </DropdownMenuItem>
          )}
          {isGraphViewAvailable && (
            <DropdownMenuItem
              asChild
              onSelect={(e) => e.preventDefault()}
              className="space-y-0 px-2 py-1"
            >
              <div className="flex w-full items-center justify-between">
                <span className="mr-2">
                  {localize(language, "Hierarchy Graph", "层级图")}
                </span>
                <Switch
                  size="sm"
                  checked={isHierarchyGraphMode}
                  onCheckedChange={(checked) =>
                    setGraphViewMode(checked ? "hierarchy" : "execution")
                  }
                />
              </div>
            </DropdownMenuItem>
          )}

          {/* Show Comments Toggle */}
          <DropdownMenuItem
            asChild
            onSelect={(e) => e.preventDefault()}
            className="px-2 py-1"
          >
            <div className="flex w-full items-center justify-between">
              <span className="mr-2">
                {localize(language, "Show Comments", "显示评论")}
              </span>
              <Switch
                size="sm"
                checked={showComments}
                onCheckedChange={setShowComments}
              />
            </div>
          </DropdownMenuItem>

          {/* Show Scores Toggle */}
          <DropdownMenuItem
            asChild
            onSelect={(e) => e.preventDefault()}
            className="px-2 py-1"
          >
            <div className="flex w-full items-center justify-between">
              <span className="mr-2">
                {localize(language, "Show Scores", "显示评分")}
              </span>
              <Switch
                size="sm"
                checked={showScores}
                onCheckedChange={(checked) => {
                  capture("trace_detail:observation_tree_toggle_scores", {
                    show: checked,
                  });
                  setShowScores(checked);
                }}
              />
            </div>
          </DropdownMenuItem>

          {/* Show Duration Toggle */}
          <DropdownMenuItem
            asChild
            onSelect={(e) => e.preventDefault()}
            className="px-2 py-1"
          >
            <div className="flex w-full items-center justify-between">
              <span className="mr-2">
                {localize(language, "Show Duration", "显示耗时")}
              </span>
              <Switch
                size="sm"
                checked={showDuration}
                onCheckedChange={setShowDuration}
              />
            </div>
          </DropdownMenuItem>

          {/* Show Cost/Tokens Toggle */}
          <DropdownMenuItem
            asChild
            onSelect={(e) => e.preventDefault()}
            className="px-2 py-1"
          >
            <div className="flex w-full items-center justify-between">
              <span className="mr-2">
                {localize(language, "Show Cost/Tokens", "显示成本/词元")}
              </span>
              <Switch
                size="sm"
                checked={showCostTokens}
                onCheckedChange={setShowCostTokens}
              />
            </div>
          </DropdownMenuItem>

          {/* Color Code Metrics Toggle (disabled when no metrics shown) */}
          <DropdownMenuItem
            asChild
            onSelect={(e) => e.preventDefault()}
            disabled={!isColorCodeEnabled}
            className={cn([
              "px-2 py-1",
              isColorCodeEnabled ? "" : "cursor-not-allowed",
            ])}
          >
            <div
              className={cn(
                "flex w-full items-center justify-between",
                !isColorCodeEnabled && "cursor-not-allowed",
              )}
            >
              <span
                className={cn(
                  "mr-2",
                  !isColorCodeEnabled && "cursor-not-allowed",
                )}
              >
                {localize(
                  language,
                  "Show Color Code Metrics",
                  "显示颜色编码指标",
                )}
              </span>
              <Switch
                size="sm"
                checked={colorCodeMetrics}
                onCheckedChange={setColorCodeMetrics}
                disabled={!isColorCodeEnabled}
                className={cn(!isColorCodeEnabled && "cursor-not-allowed")}
              />
            </div>
          </DropdownMenuItem>
        </div>

        {/* Minimum Observation Level Submenu */}
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <span className="flex items-center">
              {localize(language, "Min Level", "最低级别")}:{" "}
              {localizeObservationLevel(minObservationLevel)}
            </span>
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DropdownMenuLabel className="font-semibold">
              {localize(language, "Minimum Level", "最低级别")}
            </DropdownMenuLabel>
            {Object.values(ObservationLevel).map((level) => (
              <DropdownMenuItem
                key={level}
                onSelect={(e) => {
                  e.preventDefault();
                  setMinObservationLevel(level);
                }}
              >
                {localizeObservationLevel(level)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuSubContent>
        </DropdownMenuSub>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
