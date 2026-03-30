import Link from "next/link";
import { LeftAlignedCell } from "@/src/features/dashboard/components/LeftAlignedCell";
import { RightAlignedCell } from "@/src/features/dashboard/components/RightAlignedCell";
import { DashboardCard } from "@/src/features/dashboard/components/cards/DashboardCard";
import { NoDataOrLoading } from "@/src/components/NoDataOrLoading";
import { type FilterState } from "@langfuse/shared";
import { api } from "@/src/utils/api";
import { cn } from "@/src/utils/tailwind";
import { useEffect, useRef, useState } from "react";
import {
  type QueryType,
  type ViewVersion,
  mapLegacyUiTableFilterToView,
} from "@/src/features/query";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";

export const PolicyViolationTable = ({
  className,
  projectId,
  globalFilterState,
  fromTimestamp,
  toTimestamp,
  isLoading = false,
  metricsVersion,
}: {
  className: string;
  projectId: string;
  globalFilterState: FilterState;
  fromTimestamp: Date;
  toTimestamp: Date;
  isLoading?: boolean;
  metricsVersion?: ViewVersion;
}) => {
  const { t } = useLanguage();
  const basePolicyViolationFilters = [
    ...mapLegacyUiTableFilterToView("observations", globalFilterState),
    {
      column: "level",
      operator: "any of" as const,
      value: ["POLICY_VIOLATION"],
      type: "stringOptions" as const,
    },
  ];

  const policyViolationQuery: QueryType = {
    view: "observations",
    dimensions: [{ field: "policyName" }, { field: "policyDescription" }],
    metrics: [{ measure: "count", aggregation: "count" }],
    filters: basePolicyViolationFilters,
    timeDimension: null,
    fromTimestamp: fromTimestamp.toISOString(),
    toTimestamp: toTimestamp.toISOString(),
    orderBy: [{ field: "count_count", direction: "desc" }],
    chartConfig: { type: "table", row_limit: 50 },
  };
  const unclassifiedMissingPolicyNamesQuery: QueryType = {
    view: "observations",
    dimensions: [],
    metrics: [{ measure: "count", aggregation: "count" }],
    filters: [
      ...basePolicyViolationFilters,
      {
        column: "metadata",
        key: "policy_names",
        operator: "=",
        value: "",
        type: "stringObject",
      },
    ],
    timeDimension: null,
    fromTimestamp: fromTimestamp.toISOString(),
    toTimestamp: toTimestamp.toISOString(),
    orderBy: null,
    chartConfig: { type: "table", row_limit: 1 },
  };
  const unclassifiedEmptyArrayPolicyNamesQuery: QueryType = {
    view: "observations",
    dimensions: [],
    metrics: [{ measure: "count", aggregation: "count" }],
    filters: [
      ...basePolicyViolationFilters,
      {
        column: "metadata",
        key: "policy_names",
        operator: "=",
        value: "[]",
        type: "stringObject",
      },
    ],
    timeDimension: null,
    fromTimestamp: fromTimestamp.toISOString(),
    toTimestamp: toTimestamp.toISOString(),
    orderBy: null,
    chartConfig: { type: "table", row_limit: 1 },
  };

  const policyMetrics = api.dashboard.executeQuery.useQuery(
    {
      projectId,
      query: policyViolationQuery,
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
  const unclassifiedMissingPolicyNamesMetrics =
    api.dashboard.executeQuery.useQuery(
      {
        projectId,
        query: unclassifiedMissingPolicyNamesQuery,
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
  const unclassifiedEmptyArrayPolicyNamesMetrics =
    api.dashboard.executeQuery.useQuery(
      {
        projectId,
        query: unclassifiedEmptyArrayPolicyNamesQuery,
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

  const unclassifiedCount =
    Number(unclassifiedMissingPolicyNamesMetrics.data?.[0]?.count_count ?? 0) +
    Number(
      unclassifiedEmptyArrayPolicyNamesMetrics.data?.[0]?.count_count ?? 0,
    );

  const classifiedRows = (policyMetrics.data ?? [])
    .filter(
      (row) =>
        typeof row.policyName === "string" &&
        row.policyName.trim().length > 0 &&
        Number(row.count_count ?? 0) > 0,
    )
    .map((row) => ({
      policyName: row.policyName as string,
      policyDescription: (row.policyDescription as string | null) ?? "-",
      count: Number(row.count_count ?? 0),
    }));
  const rows = [
    ...classifiedRows,
    {
      policyName: t("dashboard.policyViolation.unclassified"),
      policyDescription: t("dashboard.policyViolation.unclassifiedDescription"),
      count: unclassifiedCount,
    },
  ].filter((row) => row.count > 0);

  const getPolicyAnalysisHref = (policyName: string) => {
    const params = new URLSearchParams({
      analysisLevel: "policy_violation",
      policyType: policyName,
    });

    return `/project/${projectId}/analysis?${params.toString()}`;
  };

  return (
    <DashboardCard
      className={className}
      title={
        <div className="flex items-center gap-2">
          <Link
            href={`/project/${projectId}/analysis?analysisLevel=policy_violation`}
            className="hover:underline"
          >
            {t("dashboard.policyViolation.title")}
          </Link>
          <span className="text-xs font-normal text-muted-foreground">
            {t("dashboard.policyViolation.clickToViewDetails")}
          </span>
        </div>
      }
      isLoading={
        isLoading ||
        policyMetrics.isLoading ||
        unclassifiedMissingPolicyNamesMetrics.isLoading ||
        unclassifiedEmptyArrayPolicyNamesMetrics.isLoading
      }
      cardContentClassName="flex min-h-0 flex-1 flex-col"
    >
      {rows.length > 0 ? (
        <div className="mt-4 flex min-h-0 flex-1 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto">
            <table className="w-full table-fixed divide-y divide-border">
              <colgroup>
                <col style={{ width: "24%" }} />
                <col style={{ width: "62%" }} />
                <col style={{ width: "14%" }} />
              </colgroup>
              <thead className="sticky top-0 z-10 bg-background">
                <tr>
                  <th
                    scope="col"
                    className="py-3.5 pl-4 pr-3 text-center text-xs font-semibold text-primary sm:pl-0"
                  >
                    {t("dashboard.policyViolation.policyName")}
                  </th>
                  <th
                    scope="col"
                    className="py-3.5 pl-4 pr-3 text-center text-xs font-semibold text-primary sm:pl-0"
                  >
                    {t("dashboard.policyViolation.description")}
                  </th>
                  <th
                    scope="col"
                    className="py-3.5 pl-4 pr-3 text-center text-xs font-semibold text-primary sm:pl-0"
                  >
                    <RightAlignedCell className="text-center">
                      {t("dashboard.policyViolation.count")}
                    </RightAlignedCell>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-accent bg-background">
                {rows.map((row, i) => (
                  <tr key={`${row.policyName}-${i}`}>
                    <td className="py-2 pl-3 pr-2 text-center align-top text-xs text-muted-foreground sm:pl-0">
                      <LeftAlignedCell
                        className="whitespace-normal break-words text-center font-semibold text-foreground"
                        title={row.policyName}
                      >
                        {row.policyName}
                      </LeftAlignedCell>
                    </td>
                    <td className="py-2 pl-3 pr-2 text-center align-top text-xs text-muted-foreground sm:pl-0">
                      <ExpandableDescriptionCell text={row.policyDescription} />
                    </td>
                    <td className="py-2 pl-3 pr-2 text-center align-top text-xs text-muted-foreground sm:pl-0">
                      <RightAlignedCell className="whitespace-nowrap text-center">
                        {row.count > 0 ? (
                          <Link
                            href={getPolicyAnalysisHref(row.policyName)}
                            className="font-semibold text-foreground underline hover:text-foreground"
                          >
                            {row.count}
                          </Link>
                        ) : (
                          <span className="font-semibold text-foreground underline">
                            {row.count}
                          </span>
                        )}
                      </RightAlignedCell>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <NoDataOrLoading
          isLoading={
            isLoading ||
            policyMetrics.isLoading ||
            unclassifiedMissingPolicyNamesMetrics.isLoading ||
            unclassifiedEmptyArrayPolicyNamesMetrics.isLoading
          }
        />
      )}
    </DashboardCard>
  );
};

const ExpandableDescriptionCell = ({ text }: { text: string }) => {
  const { t } = useLanguage();
  const [isExpanded, setExpanded] = useState(false);
  const [isOverflowing, setOverflowing] = useState(false);
  const textRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = textRef.current;
    if (!element || isExpanded) {
      return;
    }

    const checkOverflow = () => {
      setOverflowing(element.scrollHeight > element.clientHeight + 1);
    };

    checkOverflow();

    if (typeof ResizeObserver === "undefined") {
      return;
    }

    const resizeObserver = new ResizeObserver(checkOverflow);
    resizeObserver.observe(element);

    return () => {
      resizeObserver.disconnect();
    };
  }, [text, isExpanded]);

  return (
    <div className="text-center text-foreground">
      <div
        ref={textRef}
        className={cn(
          "whitespace-normal break-words text-foreground",
          !isExpanded && "line-clamp-2",
        )}
        title={text === "-" ? undefined : text}
      >
        {text}
      </div>
      {isOverflowing ? (
        <button
          type="button"
          className="mt-1 text-xs font-medium text-primary hover:underline"
          onClick={() => setExpanded((prev) => !prev)}
        >
          {isExpanded
            ? t("dashboard.policyViolation.collapse")
            : t("dashboard.policyViolation.expand")}
        </button>
      ) : null}
    </div>
  );
};
