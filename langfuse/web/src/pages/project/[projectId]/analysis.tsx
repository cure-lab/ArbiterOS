import React, { useCallback, useMemo } from "react";
import { useRouter } from "next/router";
import type { ParsedUrlQueryInput } from "node:querystring";
import Page from "@/src/components/layouts/page";
import { Tabs, TabsList, TabsTrigger } from "@/src/components/ui/tabs";
import { api } from "@/src/utils/api";
import { TracesOnboarding } from "@/src/components/onboarding/TracesOnboarding";
import { useV4Beta } from "@/src/features/events/hooks/useV4Beta";
import ObservationsEventsTable from "@/src/features/events/components/EventsTable";
import ObservationsTable from "@/src/components/table/use-cases/observations";
import type { ObservationLevelType } from "@langfuse/shared";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";

type AnalysisTab = "error_warning" | "policy_violation";

function toLevels(tab: AnalysisTab): ObservationLevelType[] {
  return tab === "policy_violation"
    ? ["POLICY_VIOLATION"]
    : ["ERROR", "WARNING"];
}

function parseTab(value: unknown): AnalysisTab {
  if (value === "policy_violation") return "policy_violation";
  return "error_warning";
}

export default function AnalysisPage() {
  const { t } = useLanguage();
  const router = useRouter();
  const projectId = router.query.projectId as string;
  const tab = useMemo(
    () => parseTab(router.query.analysisLevel),
    [router.query.analysisLevel],
  );
  const initialPolicyType = useMemo(() => {
    const value = router.query.policyType;
    return typeof value === "string" && value.trim().length > 0 ? value : null;
  }, [router.query.policyType]);
  const forcedLevels = useMemo(() => toLevels(tab), [tab]);
  const { isBetaEnabled } = useV4Beta();
  const omittedAnalysisColumns = useMemo(
    () => [
      "latency",
      "totalCost",
      "timeToFirstToken",
      "tokens",
      "usage",
      "model",
      "providedModelName",
      "promptName",
      "environment",
      "traceTags",
      "metadata",
      "scores",
    ],
    [],
  );

  const { data: hasTracingConfigured, isLoading } =
    api.traces.hasTracingConfigured.useQuery(
      { projectId },
      {
        enabled: !!projectId,
        trpc: { context: { skipBatch: true } },
        refetchInterval: 10_000,
      },
    );

  const showOnboarding = !isLoading && !hasTracingConfigured;

  const setTab = useCallback(
    async (next: string) => {
      const nextTab = parseTab(next);
      const nextQuery: ParsedUrlQueryInput = {
        ...router.query,
        analysisLevel: nextTab,
        // Reset pagination when switching analysis tabs
        pageIndex: "0",
        page: "1",
      };
      await router.replace(
        {
          pathname: router.pathname,
          query: nextQuery,
        },
        undefined,
        { shallow: true },
      );
    },
    [router],
  );

  return (
    <Page
      headerProps={{
        title: t("analysis.pageTitle"),
      }}
      scrollable={showOnboarding}
    >
      {showOnboarding ? (
        <TracesOnboarding projectId={projectId} />
      ) : (
        <div className="flex h-full w-full flex-col">
          <div className="flex items-center justify-between px-3 pt-3">
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList>
                <TabsTrigger value="error_warning">
                  {t("analysis.tabErrorWarning")}
                </TabsTrigger>
                <TabsTrigger value="policy_violation">
                  {t("analysis.tabPolicyViolation")}
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          <div className="flex min-h-0 flex-1 flex-col">
            {isBetaEnabled ? (
              <ObservationsEventsTable
                projectId={projectId}
                forcedLevels={forcedLevels}
                disableDefaultTypeFilter
                clearTypeFilter
                defaultSidebarCollapsed
                replaceLevelWithErrorType={tab === "error_warning"}
                replaceLevelWithPolicyType={tab === "policy_violation"}
                initialPolicyTypeFilter={initialPolicyType}
                omittedColumns={omittedAnalysisColumns}
                forceViewMode="observation"
                showOpenTraceButton
                showBulkAnalysisButton={tab === "error_warning"}
              />
            ) : (
              <ObservationsTable
                projectId={projectId}
                forcedLevels={forcedLevels}
                disableDefaultTypeFilter
                clearTypeFilter
                defaultSidebarCollapsed
                replaceLevelWithErrorType={tab === "error_warning"}
                replaceLevelWithPolicyType={tab === "policy_violation"}
                initialPolicyTypeFilter={initialPolicyType}
                omittedColumns={omittedAnalysisColumns}
                showOpenTraceButton
                showBulkAnalysisButton={tab === "error_warning"}
              />
            )}
          </div>
        </div>
      )}
    </Page>
  );
}
