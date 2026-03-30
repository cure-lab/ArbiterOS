import React from "react";
import { useRouter } from "next/router";
import SessionsTable from "@/src/components/table/use-cases/sessions";
import Page from "@/src/components/layouts/page";
import { SessionsOnboarding } from "@/src/components/onboarding/SessionsOnboarding";
import { api } from "@/src/utils/api";
import { useV4Beta } from "@/src/features/events/hooks/useV4Beta";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export default function Sessions() {
  const router = useRouter();
  const projectId = router.query.projectId as string;
  const { isBetaEnabled } = useV4Beta();
  const { language } = useLanguage();

  const sessionsTitle = localize(language, "Sessions", "会话");
  const docsLabel = localize(language, "docs", "文档");
  const helpPrefix = localize(
    language,
    "A session is a collection of related traces, such as a conversation or thread. To begin, add a sessionId to the trace. See ",
    "Session 是一组相关 traces 的集合，例如一次对话或一个线程。开始使用时，请在 trace 中添加 sessionId。查看",
  );
  const helpSuffix = localize(language, " to learn more.", "了解更多。");

  const { data: hasAnySession, isLoading } = api.sessions.hasAny.useQuery(
    { projectId },
    {
      enabled: !!projectId && !isBetaEnabled,
      trpc: {
        context: {
          skipBatch: true,
        },
      },
      refetchInterval: 10_000,
    },
  );

  const { data: hasAnySessionFromEvents, isLoading: isLoadingFromEvents } =
    api.sessions.hasAnyFromEvents.useQuery(
      { projectId },
      {
        enabled: !!projectId && isBetaEnabled,
        trpc: {
          context: {
            skipBatch: true,
          },
        },
        refetchInterval: 10_000,
      },
    );

  const hasSessions = isBetaEnabled ? hasAnySessionFromEvents : hasAnySession;
  const isLoadingSessions = isBetaEnabled ? isLoadingFromEvents : isLoading;
  const showOnboarding = !isLoadingSessions && !hasSessions;

  return (
    <Page
      headerProps={{
        title: sessionsTitle,
        help: {
          description: (
            <>
              {helpPrefix}
              <a
                href="https://langfuse.com/docs/observability/features/sessions"
                target="_blank"
                rel="noopener noreferrer"
                className="underline decoration-primary/30 hover:decoration-primary"
                onClick={(e) => e.stopPropagation()}
              >
                {docsLabel}
              </a>{" "}
              {helpSuffix}
            </>
          ),
          href: "https://langfuse.com/docs/observability/features/sessions",
        },
      }}
      scrollable={showOnboarding}
    >
      {/* Show onboarding screen if user has no sessions */}
      {showOnboarding ? (
        <SessionsOnboarding />
      ) : (
        <SessionsTable projectId={projectId} isBetaEnabled={isBetaEnabled} />
      )}
    </Page>
  );
}
