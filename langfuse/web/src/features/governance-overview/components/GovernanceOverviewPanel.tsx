import { useMemo, useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/src/components/ui/card";
import { NoDataOrLoading } from "@/src/components/NoDataOrLoading";
import { DashboardCard } from "@/src/features/dashboard/components/cards/DashboardCard";
import { Chart } from "@/src/features/widgets/chart-library/Chart";
import { api } from "@/src/utils/api";
import { compactNumberFormatter } from "@/src/utils/numbers";
import {
  dashboardDateRangeAggregationSettings,
  type DashboardDateRangeAggregationOption,
} from "@/src/utils/date-range-utils";
import {
  type QueryType,
  type ViewVersion,
  mapLegacyUiTableFilterToView,
} from "@/src/features/query";
import { type FilterState } from "@langfuse/shared";
import { type DataPoint } from "@/src/features/widgets/chart-library/chart-props";
import { Button } from "@/src/components/ui/button";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";

function getSafeIsoTime(value: unknown): string {
  const asString = String(value);
  const parsed = new Date(asString);
  if (Number.isNaN(parsed.getTime())) return asString;
  return parsed.toISOString();
}

export function GovernanceOverviewPanel(props: {
  projectId: string;
  globalFilterState: FilterState;
  fromTimestamp: Date;
  toTimestamp: Date;
  agg: DashboardDateRangeAggregationOption;
  isLoading?: boolean;
  metricsVersion?: ViewVersion;
}) {
  const { t } = useLanguage();
  const [trendMode, setTrendMode] = useState<
    "error_warning" | "policy_violation"
  >("error_warning");
  const {
    projectId,
    globalFilterState,
    fromTimestamp,
    toTimestamp,
    agg,
    isLoading = false,
    metricsVersion,
  } = props;

  const observationsQuery: QueryType = {
    view: "observations",
    dimensions: [{ field: "level" }],
    metrics: [{ measure: "count", aggregation: "count" }],
    filters: mapLegacyUiTableFilterToView("observations", globalFilterState),
    timeDimension: {
      granularity:
        dashboardDateRangeAggregationSettings[agg].dateTrunc ?? "day",
    },
    fromTimestamp: fromTimestamp.toISOString(),
    toTimestamp: toTimestamp.toISOString(),
    orderBy: null,
  };

  const observations = api.dashboard.executeQuery.useQuery(
    {
      projectId,
      query: observationsQuery,
      version: metricsVersion,
    },
    {
      trpc: {
        context: {
          skipBatch: true,
        },
      },
      enabled: !isLoading,
    },
  );

  const settingsQuery = api.projects.getErrorAnalysisSettings.useQuery(
    { projectId },
    {
      enabled: Boolean(projectId),
      refetchOnWindowFocus: false,
    },
  );

  const summaryQuery = api.experienceSummary.get.useQuery(
    { projectId },
    {
      enabled: Boolean(projectId),
      refetchOnWindowFocus: false,
    },
  );

  const {
    errorCount,
    warningCount,
    policyViolationCount,
    governedCount,
    errorWarningTrendData,
    policyViolationTrendData,
    latestErrors,
    latestWarnings,
    latestPolicyViolations,
  } = useMemo(() => {
    const rows = observations.data ?? [];
    let errorCount = 0;
    let warningCount = 0;
    let policyViolationCount = 0;

    const groupedByTime = new Map<
      string,
      { ERROR: number; WARNING: number; POLICY_VIOLATION: number }
    >();
    for (const row of rows) {
      const level = String(row.level ?? "").toUpperCase();
      if (
        level !== "ERROR" &&
        level !== "WARNING" &&
        level !== "POLICY_VIOLATION"
      ) {
        continue;
      }

      const value = Number(row.count_count ?? 0);
      const timeKey = getSafeIsoTime(row.time_dimension);
      const current = groupedByTime.get(timeKey) ?? {
        ERROR: 0,
        WARNING: 0,
        POLICY_VIOLATION: 0,
      };
      current[level as "ERROR" | "WARNING" | "POLICY_VIOLATION"] += value;
      groupedByTime.set(timeKey, current);

      if (level === "ERROR") errorCount += value;
      if (level === "WARNING") warningCount += value;
      if (level === "POLICY_VIOLATION") policyViolationCount += value;
    }

    const sortedTimes = [...groupedByTime.keys()].sort(
      (a, b) => new Date(a).getTime() - new Date(b).getTime(),
    );
    const errorWarningTrendData: DataPoint[] = [];
    const policyViolationTrendData: DataPoint[] = [];
    for (const time of sortedTimes) {
      const group = groupedByTime.get(time)!;
      errorWarningTrendData.push({
        time_dimension: time,
        dimension: t("dashboard.governance.errors"),
        metric: group.ERROR,
      });
      errorWarningTrendData.push({
        time_dimension: time,
        dimension: t("dashboard.governance.warnings"),
        metric: group.WARNING,
      });
      policyViolationTrendData.push({
        time_dimension: time,
        dimension: t("dashboard.governance.policyViolations"),
        metric: group.POLICY_VIOLATION,
      });
    }

    const latestTime =
      sortedTimes.length > 0 ? sortedTimes[sortedTimes.length - 1] : null;
    const latest = latestTime ? groupedByTime.get(latestTime) : null;

    return {
      errorCount,
      warningCount,
      policyViolationCount,
      governedCount: errorCount + warningCount + policyViolationCount,
      errorWarningTrendData,
      policyViolationTrendData,
      latestErrors: latest?.ERROR ?? 0,
      latestWarnings: latest?.WARNING ?? 0,
      latestPolicyViolations: latest?.POLICY_VIOLATION ?? 0,
    };
  }, [observations.data, t]);

  const experiencePackCount =
    summaryQuery.data?.summary.experiences.length ?? 0;
  const promptPackLineCount =
    summaryQuery.data?.summary.promptPack.lines.length ?? 0;
  const isPanelLoading =
    isLoading ||
    observations.isPending ||
    settingsQuery.isPending ||
    summaryQuery.isPending;

  const trendData =
    trendMode === "policy_violation"
      ? policyViolationTrendData
      : errorWarningTrendData;
  const hasTrendData = trendData.length > 0;

  return (
    <div className="mb-3 space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {t("dashboard.governance.governedSignals")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold">
              {compactNumberFormatter(governedCount)}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {t("dashboard.governance.governedSignalsDesc")}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {t("dashboard.governance.errors")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold text-destructive">
              {compactNumberFormatter(errorCount)}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {t("dashboard.governance.latestBucket")}:{" "}
              {compactNumberFormatter(latestErrors)}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {t("dashboard.governance.warnings")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold text-yellow-600 dark:text-yellow-400">
              {compactNumberFormatter(warningCount)}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {t("dashboard.governance.latestBucket")}:{" "}
              {compactNumberFormatter(latestWarnings)}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {t("dashboard.governance.policyViolations")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold text-emerald-500 dark:text-emerald-200">
              {compactNumberFormatter(policyViolationCount)}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {t("dashboard.governance.latestBucket")}:{" "}
              {compactNumberFormatter(latestPolicyViolations)}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {t("dashboard.governance.assets")}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold">
              {compactNumberFormatter(experiencePackCount)}
            </div>
            <div className="mt-1 text-xs text-muted-foreground">
              {t("dashboard.governance.experiencePacks")} ·{" "}
              {compactNumberFormatter(promptPackLineCount)}{" "}
              {t("dashboard.governance.promptLines")}
            </div>
          </CardContent>
        </Card>
      </div>

      <DashboardCard
        title={t("dashboard.governance.trend")}
        description={
          trendMode === "policy_violation"
            ? t("dashboard.governance.trendPolicyDescription")
            : t("dashboard.governance.trendErrorDescription")
        }
        isLoading={isPanelLoading}
      >
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Button
            variant={trendMode === "error_warning" ? "default" : "outline"}
            size="sm"
            onClick={() => setTrendMode("error_warning")}
          >
            {t("dashboard.governance.errorsAndWarnings")}
          </Button>
          <Button
            variant={trendMode === "policy_violation" ? "default" : "outline"}
            size="sm"
            onClick={() => setTrendMode("policy_violation")}
          >
            {t("dashboard.governance.policyViolationsTab")}
          </Button>
        </div>
        {hasTrendData ? (
          <div className="h-72 w-full">
            <Chart
              chartType="LINE_TIME_SERIES"
              data={trendData}
              rowLimit={240}
              chartConfig={{
                type: "LINE_TIME_SERIES",
                show_data_point_dots: false,
              }}
              legendPosition="above"
            />
          </div>
        ) : (
          <NoDataOrLoading
            isLoading={isPanelLoading}
            description={
              trendMode === "policy_violation"
                ? t("dashboard.governance.noPolicyViolations")
                : t("dashboard.governance.noErrorsWarnings")
            }
          />
        )}
      </DashboardCard>
    </div>
  );
}
