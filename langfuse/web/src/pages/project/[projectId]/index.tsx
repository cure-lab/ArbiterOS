import { useRouter } from "next/router";
import { GenerationLatencyChart } from "@/src/features/dashboard/components/LatencyChart";
import { PolicyViolationTable } from "@/src/features/dashboard/components/PolicyViolationTable";
import { PolicyConfirmationStatsCard } from "@/src/features/dashboard/components/PolicyConfirmationStatsCard";
import { ModelUsageChart } from "@/src/features/dashboard/components/ModelUsageChart";
import { TracesAndObservationsTimeSeriesChart } from "@/src/features/dashboard/components/TracesTimeSeriesChart";
import { UserChart } from "@/src/features/dashboard/components/UserChart";
import { TimeRangePicker } from "@/src/components/date-picker";
import { api } from "@/src/utils/api";
import { PopoverFilterBuilder } from "@/src/features/filters/components/filter-builder";
import { type FilterState } from "@langfuse/shared";
import { type ColumnDefinition } from "@langfuse/shared";
import { useQueryFilterState } from "@/src/features/filters/hooks/useFilterState";
import { LatencyTables } from "@/src/features/dashboard/components/LatencyTables";
import { useMemo } from "react";
import {
  findClosestDashboardInterval,
  DASHBOARD_AGGREGATION_OPTIONS,
  toAbsoluteTimeRange,
  type DashboardDateRangeAggregationOption,
} from "@/src/utils/date-range-utils";
import { useDashboardDateRange } from "@/src/hooks/useDashboardDateRange";
import { useDebounce } from "@/src/hooks/useDebounce";
import SetupTracingButton from "@/src/features/setup/components/SetupTracingButton";
import { useEntitlementLimit } from "@/src/features/entitlements/hooks";
import Page from "@/src/components/layouts/page";
import { MultiSelect } from "@/src/features/filters/components/multi-select";
import {
  convertSelectedEnvironmentsToFilter,
  useEnvironmentFilter,
} from "@/src/hooks/use-environment-filter";
import { useV4Beta } from "@/src/features/events/hooks/useV4Beta";
import { type ViewVersion } from "@/src/features/query";
import { GovernanceOverviewPanel } from "@/src/features/governance-overview/components/GovernanceOverviewPanel";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";

export default function Dashboard() {
  const { t } = useLanguage();
  const router = useRouter();
  const projectId = router.query.projectId as string;
  const { timeRange, setTimeRange } = useDashboardDateRange();
  const { isBetaEnabled } = useV4Beta();
  const metricsVersion: ViewVersion = isBetaEnabled ? "v2" : "v1";

  const absoluteTimeRange = useMemo(
    () => toAbsoluteTimeRange(timeRange),
    [timeRange],
  );

  const lookbackLimit = useEntitlementLimit("data-access-days");

  const [userFilterState, setUserFilterState] = useQueryFilterState(
    [],
    "dashboard",
    projectId,
  );

  const traceFilterOptions = api.traces.filterOptions.useQuery(
    {
      projectId,
    },
    {
      trpc: {
        context: {
          skipBatch: true,
        },
      },
      refetchOnMount: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      staleTime: Infinity,
    },
  );

  const environmentFilterOptions =
    api.projects.environmentFilterOptions.useQuery(
      {
        projectId,
        fromTimestamp: absoluteTimeRange?.from,
      },
      {
        trpc: {
          context: {
            skipBatch: true,
          },
        },
        refetchOnMount: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        staleTime: Infinity,
      },
    );
  const environmentOptions: string[] =
    environmentFilterOptions.data?.map((value) => value.environment) || [];

  // Add effect to update filter state when environments change
  const { selectedEnvironments, setSelectedEnvironments } =
    useEnvironmentFilter(environmentOptions, projectId);

  const nameOptions =
    traceFilterOptions.data?.name?.map((n) => ({
      value: n.value,
      count: Number(n.count),
    })) || [];
  const tagsOptions = traceFilterOptions.data?.tags || [];

  const filterColumns: ColumnDefinition[] = [
    {
      name: t("dashboard.home.filterTraceName"),
      id: "traceName",
      type: "stringOptions",
      options: nameOptions,
      internal: "internalValue",
    },
    {
      name: t("dashboard.home.filterTags"),
      id: "tags",
      type: "arrayOptions",
      options: tagsOptions,
      internal: "internalValue",
    },
    {
      name: t("dashboard.home.filterUser"),
      id: "user",
      type: "string",
      internal: "internalValue",
    },
    {
      name: t("dashboard.home.filterRelease"),
      id: "release",
      type: "string",
      internal: "internalValue",
    },
    {
      name: t("dashboard.home.filterVersion"),
      id: "version",
      type: "string",
      internal: "internalValue",
    },
  ];

  const dashboardTimeRangePresets = DASHBOARD_AGGREGATION_OPTIONS;

  const agg = useMemo(() => {
    if ("range" in timeRange) {
      return timeRange.range as DashboardDateRangeAggregationOption;
    }

    return findClosestDashboardInterval(timeRange) ?? "last7Days";
  }, [timeRange]);

  const fromTimestamp = absoluteTimeRange?.from
    ? absoluteTimeRange.from
    : new Date(new Date().getTime() - 1000);
  const toTimestamp = absoluteTimeRange?.to ? absoluteTimeRange.to : new Date();
  const timeFilter = [
    {
      type: "datetime" as const,
      column: "startTime",
      operator: ">" as const,
      value: fromTimestamp,
    },
    {
      type: "datetime" as const,
      column: "startTime",
      operator: "<" as const,
      value: toTimestamp,
    },
  ];

  const environmentFilter = convertSelectedEnvironmentsToFilter(
    ["environment"],
    selectedEnvironments,
  );

  const mergedFilterState: FilterState = [
    ...userFilterState,
    ...timeFilter,
    ...environmentFilter,
  ];

  return (
    <Page
      withPadding
      scrollable
      headerProps={{
        title: t("dashboard.home.title"),
        actionButtonsLeft: (
          <>
            <TimeRangePicker
              timeRange={timeRange}
              onTimeRangeChange={setTimeRange}
              timeRangePresets={dashboardTimeRangePresets}
              className="my-0 max-w-full overflow-x-auto"
              disabled={
                lookbackLimit
                  ? {
                      before: new Date(
                        new Date().getTime() -
                          lookbackLimit * 24 * 60 * 60 * 1000,
                      ),
                    }
                  : undefined
              }
            />
            <MultiSelect
              title={t("dashboard.home.environment")}
              label={t("dashboard.home.envShort")}
              values={selectedEnvironments}
              onValueChange={useDebounce(setSelectedEnvironments)}
              options={environmentOptions.map((env) => ({
                value: env,
              }))}
              className="my-0 w-auto overflow-hidden"
            />
            <PopoverFilterBuilder
              columns={filterColumns}
              filterState={userFilterState}
              onChange={useDebounce(setUserFilterState)}
            />
          </>
        ),
        actionButtonsRight: (
          <>
            <SetupTracingButton />
          </>
        ),
      }}
    >
      <GovernanceOverviewPanel
        projectId={projectId}
        globalFilterState={[...userFilterState, ...environmentFilter]}
        fromTimestamp={fromTimestamp}
        toTimestamp={toTimestamp}
        agg={agg}
        isLoading={environmentFilterOptions.isPending}
        metricsVersion={metricsVersion}
      />
      <div className="grid w-full grid-cols-1 gap-3 overflow-hidden lg:grid-cols-2 xl:grid-cols-6">
        <PolicyConfirmationStatsCard
          className="col-span-1 xl:col-span-3"
          projectId={projectId}
          globalFilterState={[...userFilterState, ...environmentFilter]}
          fromTimestamp={fromTimestamp}
          toTimestamp={toTimestamp}
          isLoading={environmentFilterOptions.isPending}
          metricsVersion={metricsVersion}
        />
        <PolicyViolationTable
          className="col-span-1 xl:col-span-3"
          projectId={projectId}
          globalFilterState={[...userFilterState, ...environmentFilter]}
          fromTimestamp={fromTimestamp}
          toTimestamp={toTimestamp}
          isLoading={environmentFilterOptions.isPending}
          metricsVersion={metricsVersion}
        />
        <TracesAndObservationsTimeSeriesChart
          className="col-span-1 xl:col-span-3"
          projectId={projectId}
          globalFilterState={[...userFilterState, ...environmentFilter]}
          fromTimestamp={fromTimestamp}
          toTimestamp={toTimestamp}
          agg={agg}
          isLoading={environmentFilterOptions.isPending}
          metricsVersion={metricsVersion}
        />
        <ModelUsageChart
          className="col-span-1 min-h-24 xl:col-span-3"
          projectId={projectId}
          globalFilterState={mergedFilterState}
          fromTimestamp={fromTimestamp}
          toTimestamp={toTimestamp}
          userAndEnvFilterState={[...userFilterState, ...environmentFilter]}
          agg={agg}
          isLoading={environmentFilterOptions.isPending}
          metricsVersion={metricsVersion}
        />
        <UserChart
          className="col-span-1 xl:col-span-6"
          projectId={projectId}
          globalFilterState={[...userFilterState, ...environmentFilter]}
          fromTimestamp={fromTimestamp}
          toTimestamp={toTimestamp}
          isLoading={environmentFilterOptions.isPending}
          metricsVersion={metricsVersion}
        />
        <LatencyTables
          projectId={projectId}
          globalFilterState={[...userFilterState, ...environmentFilter]}
          fromTimestamp={fromTimestamp}
          toTimestamp={toTimestamp}
          isLoading={environmentFilterOptions.isPending}
          metricsVersion={metricsVersion}
        />
        <GenerationLatencyChart
          className="col-span-1 flex-auto justify-between lg:col-span-full"
          projectId={projectId}
          agg={agg}
          globalFilterState={[...userFilterState, ...environmentFilter]}
          fromTimestamp={fromTimestamp}
          toTimestamp={toTimestamp}
          isLoading={environmentFilterOptions.isPending}
          metricsVersion={metricsVersion}
        />
      </div>
    </Page>
  );
}
