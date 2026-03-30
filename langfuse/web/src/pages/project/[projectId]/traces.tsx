import React, { useEffect } from "react";
import { useRouter } from "next/router";
import { useQueryParams, StringParam } from "use-query-params";
import TracesTable from "@/src/components/table/use-cases/traces";
import Page from "@/src/components/layouts/page";
import { api } from "@/src/utils/api";
import { TracesOnboarding } from "@/src/components/onboarding/TracesOnboarding";
import { useV4Beta } from "@/src/features/events/hooks/useV4Beta";
import ObservationsEventsTable from "@/src/features/events/components/EventsTable";
import { useQueryProject } from "@/src/features/projects/hooks";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export default function Traces() {
  const router = useRouter();
  const projectId = router.query.projectId as string;
  const { isBetaEnabled } = useV4Beta();
  const [, setQueryParams] = useQueryParams({ viewMode: StringParam });
  const { project } = useQueryProject();
  const { language } = useLanguage();

  const tracingTitle = localize(language, "Tracing", "追踪");
  const docsLabel = localize(language, "docs", "文档");
  const tracingHelpPrefix = localize(
    language,
    "A trace represents a single function/api invocation. Traces contain observations. See ",
    "Trace 表示一次单独的函数/API 调用，其中包含 observations。查看",
  );
  const tracingHelpSuffix = localize(language, " to learn more.", "了解更多。");
  const tracingHelpText = localize(
    language,
    "A trace represents a single function/api invocation. Traces contain observations.",
    "Trace 表示一次单独的函数/API 调用，其中包含 observations。",
  );

  // Clear viewMode query when beta is turned off (e.g. from sidebar)
  useEffect(() => {
    if (!isBetaEnabled) {
      setQueryParams({ viewMode: undefined });
    }
  }, [isBetaEnabled, setQueryParams]);

  // Check if the user has tracing configured
  // Skip polling entirely if the project flag is already set in the session
  const { data: hasTracingConfigured, isLoading } =
    api.traces.hasTracingConfigured.useQuery(
      { projectId },
      {
        enabled: !!projectId,
        trpc: {
          context: {
            skipBatch: true,
          },
        },
        refetchInterval: project?.hasTraces ? false : 10_000,
        initialData: project?.hasTraces ? true : undefined,
        staleTime: project?.hasTraces ? Infinity : 0,
      },
    );

  const showOnboarding = !isLoading && !hasTracingConfigured;

  if (showOnboarding) {
    return (
      <Page
        headerProps={{
          title: tracingTitle,
          help: {
            description: `${tracingHelpText} ${localize(
              language,
              "See docs to learn more.",
              "查看文档了解更多。",
            )}`,
            href: "https://langfuse.com/docs/observability/data-model",
          },
        }}
        scrollable
      >
        <TracesOnboarding projectId={projectId} />
      </Page>
    );
  }

  return (
    <Page
      headerProps={{
        title: tracingTitle,
        help: {
          description: (
            <>
              {tracingHelpPrefix}
              <a
                href="https://langfuse.com/docs/observability/data-model"
                target="_blank"
                rel="noopener noreferrer"
                className="underline decoration-primary/30 hover:decoration-primary"
                onClick={(e) => e.stopPropagation()}
              >
                {docsLabel}
              </a>{" "}
              {tracingHelpSuffix}
            </>
          ),
          href: "https://langfuse.com/docs/observability/data-model",
        },
      }}
    >
      {isBetaEnabled ? (
        <ObservationsEventsTable projectId={projectId} />
      ) : (
        <TracesTable projectId={projectId} />
      )}
    </Page>
  );
}
