"use client";

import { useMemo, useState, useEffect } from "react";
import Link from "next/link";
import { Button } from "@/src/components/ui/button";
import { JSONView } from "@/src/components/ui/CodeJsonViewer";
import { Tabs, TabsList, TabsTrigger } from "@/src/components/ui/tabs";
import { Badge } from "@/src/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/src/components/ui/select";
import { api } from "@/src/utils/api";
import {
  type ErrorAnalysisAnalyzeOutput,
  type ErrorAnalysisModel,
  ErrorAnalysisModelSchema,
} from "../types";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

function formatSummaryStatusTimestamp(value: Date | null | undefined): string {
  if (!value) return "n/a";
  return value.toLocaleString();
}

function truncateMessage(value: string, maxChars: number): string {
  if (value.length <= maxChars) return value;
  return `${value.slice(0, Math.max(0, maxChars - 3))}...`;
}

function FormattedAnalysisView(props: {
  rendered: ErrorAnalysisAnalyzeOutput["rendered"];
}) {
  const { rendered } = props;
  const { language } = useLanguage();

  return (
    <div className="flex flex-col gap-3 text-xs">
      {/* issue */}
      <div className="flex flex-col gap-1">
        <div className="font-mono font-medium text-muted-foreground">
          {localize(language, "issue", "问题")}
        </div>
        <div className="whitespace-pre-wrap break-words rounded-md border bg-background p-2">
          {rendered.issue}
        </div>
      </div>

      {/* errorType */}
      <div className="flex flex-col gap-1">
        <div className="font-mono font-medium text-muted-foreground">
          {localize(language, "errorType", "错误类型")}
        </div>
        {rendered.errorType ? (
          <div className="rounded-md border bg-background p-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary" className="font-mono">
                {rendered.errorType}
              </Badge>
              {rendered.errorTypeConfidence != null ? (
                <span className="font-mono text-xs text-muted-foreground">
                  {rendered.errorTypeConfidence.toFixed(2)}
                </span>
              ) : null}
              {rendered.errorTypeFromList != null ? (
                <span className="text-xs text-muted-foreground">
                  {rendered.errorTypeFromList
                    ? localize(language, "predefined", "预设")
                    : localize(language, "custom", "自定义")}
                </span>
              ) : null}
            </div>
            {rendered.errorTypeDescription ? (
              <div className="mt-2 whitespace-pre-wrap break-words text-muted-foreground">
                {rendered.errorTypeDescription}
              </div>
            ) : null}
            {rendered.errorTypeWhy ? (
              <div className="mt-2 whitespace-pre-wrap break-words text-muted-foreground">
                {rendered.errorTypeWhy}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="rounded-md border bg-background p-2 text-muted-foreground">
            {localize(
              language,
              "No error type classified yet. Click Analyze to generate one.",
              "尚未分类错误类型。点击“分析”生成。",
            )}
          </div>
        )}
      </div>

      {/* rootCause */}
      <div className="flex flex-col gap-1">
        <div className="font-mono font-medium text-muted-foreground">
          {localize(language, "rootCause", "根本原因")}
        </div>
        <div className="whitespace-pre-wrap break-words rounded-md border bg-background p-2">
          {rendered.rootCause}
        </div>
      </div>

      {/* resolveNow */}
      <div className="flex flex-col gap-1">
        <div className="font-mono font-medium text-muted-foreground">
          {localize(language, "resolveNow", "立即处理")}
        </div>
        <div className="flex flex-col gap-2">
          {rendered.resolveNow.length > 0 ? (
            rendered.resolveNow.map((item, idx) => {
              const effectiveSubtitle = `${idx + 1}`;
              const content = item.trim();

              return (
                <div
                  key={`${idx}-${effectiveSubtitle}`}
                  className="rounded-md border bg-background p-2"
                >
                  <div className="text-xs font-medium">{effectiveSubtitle}</div>
                  <div className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">
                    {content}
                  </div>
                </div>
              );
            })
          ) : (
            <div className="rounded-md border bg-background p-2 text-muted-foreground">
              {localize(
                language,
                "No immediate resolution steps returned.",
                "未返回立即处理步骤。",
              )}
            </div>
          )}
        </div>
      </div>

      {/* preventionNextCall */}
      <div className="flex flex-col gap-1">
        <div className="font-mono font-medium text-muted-foreground">
          {localize(language, "preventionNextCall", "下次调用预防")}
        </div>
        <div className="flex flex-col gap-2">
          {rendered.preventionNextCall.length > 0 ? (
            rendered.preventionNextCall.map((item, idx) => {
              const effectiveSubtitle = `${idx + 1}`;
              const content = item.trim();

              return (
                <div
                  key={`${idx}-${effectiveSubtitle}`}
                  className="rounded-md border bg-background p-2"
                >
                  <div className="text-xs font-medium">{effectiveSubtitle}</div>
                  <div className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">
                    {content}
                  </div>
                </div>
              );
            })
          ) : (
            <div className="rounded-md border bg-background p-2 text-muted-foreground">
              {localize(
                language,
                "No prevention steps returned.",
                "未返回预防步骤。",
              )}
            </div>
          )}
        </div>
      </div>

      {/* contextSufficient */}
      <div className="flex flex-col gap-1">
        <div className="font-mono font-medium text-muted-foreground">
          {localize(language, "contextSufficient", "上下文是否充足")}
        </div>
        <div className="rounded-md border bg-background p-2">
          <div className="font-mono text-xs">
            {rendered.contextSufficient
              ? localize(language, "true", "是")
              : localize(language, "false", "否")}
          </div>
        </div>
      </div>

      {/* confidence */}
      <div className="flex flex-col gap-1">
        <div className="font-mono font-medium text-muted-foreground">
          {localize(language, "confidence", "置信度")}
        </div>
        <div className="rounded-md border bg-background p-2">
          <div className="flex items-center gap-2">
            <div className="h-2 flex-1 overflow-hidden rounded bg-muted">
              <div
                className="h-2 bg-primary"
                style={{
                  width: `${Math.max(0, Math.min(1, rendered.confidence)) * 100}%`,
                }}
              />
            </div>
            <div className="font-mono text-xs">
              {rendered.confidence.toFixed(2)}
            </div>
          </div>
        </div>
      </div>

      {/* relevantObservations */}
      <div className="flex flex-col gap-1">
        <div className="font-mono font-medium text-muted-foreground">
          {localize(language, "relevantObservations", "相关 Observations")}
        </div>
        <div className="flex flex-col gap-2">
          {rendered.relevantObservations.length > 0 ? (
            rendered.relevantObservations.map((obsId, idx) => (
              <div
                key={`${idx}-${obsId}`}
                className="rounded-md border bg-background p-2"
              >
                <div className="text-xs font-medium">{`#${idx + 1}`}</div>
                <div className="mt-1 whitespace-pre-wrap break-words font-mono text-muted-foreground">
                  {obsId}
                </div>
              </div>
            ))
          ) : (
            <div className="rounded-md border bg-background p-2 text-muted-foreground">
              {localize(
                language,
                "No relevant observations returned.",
                "未返回相关 observations。",
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function ErrorAnalysisDropdown(props: {
  projectId: string;
  traceId: string;
  observationId: string;
  level: "ERROR" | "WARNING";
}) {
  const { language } = useLanguage();
  const models = useMemo<ErrorAnalysisModel[]>(
    () => [...ErrorAnalysisModelSchema.options] as ErrorAnalysisModel[],
    [],
  );
  const [model, setModel] = useState<ErrorAnalysisModel>(models[0]!);
  const [result, setResult] = useState<ErrorAnalysisAnalyzeOutput | null>(null);
  const [resultView, setResultView] = useState<"rendered" | "original">(
    "rendered",
  );
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [autoWaitStartedAt, setAutoWaitStartedAt] = useState<number | null>(
    null,
  );
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const [autoRetryError, setAutoRetryError] = useState<string | null>(null);

  const autoSettingsQuery = api.projects.getErrorAnalysisSettings.useQuery(
    { projectId: props.projectId },
    { enabled: Boolean(props.projectId), refetchOnWindowFocus: false },
  );
  const shouldPollForAutoAnalysis = Boolean(
    autoSettingsQuery.data?.enabled && !result,
  );

  const { data: savedAnalysis } = api.errorAnalysis.get.useQuery(
    {
      projectId: props.projectId,
      traceId: props.traceId,
      observationId: props.observationId,
    },
    {
      enabled: !result,
      refetchOnWindowFocus: false,
      // Auto-analysis is asynchronous in worker; poll briefly until result appears.
      refetchInterval: shouldPollForAutoAnalysis ? 2_000 : false,
      refetchIntervalInBackground: true,
    },
  );

  const autoGenerationStatusQuery =
    api.errorAnalysis.getAutoGenerationStatus.useQuery(
      {
        projectId: props.projectId,
        traceId: props.traceId,
        observationId: props.observationId,
      },
      {
        enabled: Boolean(autoSettingsQuery.data?.enabled && !result),
        refetchOnWindowFocus: false,
        refetchInterval: 2_000,
        refetchIntervalInBackground: true,
      },
    );

  const retryAutoGeneration = api.errorAnalysis.retryAutoGeneration.useMutation(
    {
      onSuccess: () => {
        setAutoRetryError(null);
        setAutoWaitStartedAt(Date.now());
      },
      onError: (err) => {
        setAutoRetryError(err.message);
      },
    },
  );

  const summaryUpdateStatusQuery =
    api.errorAnalysis.getSummaryUpdateStatus.useQuery(
      {
        projectId: props.projectId,
        traceId: props.traceId,
        observationId: props.observationId,
      },
      {
        enabled: Boolean(autoSettingsQuery.data?.enabled && result),
        refetchOnWindowFocus: false,
        refetchInterval: 2_000,
        refetchIntervalInBackground: true,
      },
    );

  const summaryUpdateStatusText = useMemo(() => {
    if (!autoSettingsQuery.data?.enabled) return null;
    if (summaryUpdateStatusQuery.isPending) {
      return localize(
        language,
        "Summary sync status: checking...",
        "摘要同步状态：检查中...",
      );
    }
    if (summaryUpdateStatusQuery.isError) {
      return localize(
        language,
        "Summary sync status: unavailable.",
        "摘要同步状态：不可用。",
      );
    }

    const status = summaryUpdateStatusQuery.data;
    if (!status?.analysisUpdatedAt) {
      return localize(
        language,
        "Summary sync status: waiting for analysis record...",
        "摘要同步状态：等待分析记录...",
      );
    }

    const batchSize = status.minNewAnalysesToUpdate ?? 1;
    const pendingCount = status.pendingAnalysesCount ?? 0;
    const pendingHint =
      pendingCount > 0 && pendingCount < batchSize
        ? ` (${pendingCount}/${batchSize} analyses pending)`
        : pendingCount >= batchSize
          ? ` (${pendingCount} analyses pending)`
          : "";

    if (status.synced) {
      return localize(
        language,
        `Summary sync status: synced (summary updated ${formatSummaryStatusTimestamp(
          status.summaryUpdatedAt ?? status.summaryCursorUpdatedAt,
        )}).`,
        `摘要同步状态：已同步（摘要更新时间 ${formatSummaryStatusTimestamp(
          status.summaryUpdatedAt ?? status.summaryCursorUpdatedAt,
        )}）。`,
      );
    }

    if (!status.summaryCursorUpdatedAt) {
      if (pendingCount > 0 && pendingCount < batchSize) {
        return localize(
          language,
          `Summary sync status: pending${pendingHint} (waiting to generate first summary).`,
          `摘要同步状态：待处理${pendingHint}（等待生成第一份摘要）。`,
        );
      }
      return localize(
        language,
        `Summary sync status: pending${pendingHint} (summary has not been generated yet).`,
        `摘要同步状态：待处理${pendingHint}（摘要尚未生成）。`,
      );
    }

    if (pendingCount > 0 && pendingCount < batchSize) {
      return localize(
        language,
        `Summary sync status: pending${pendingHint} (summary ${formatSummaryStatusTimestamp(
          status.summaryCursorUpdatedAt,
        )} is behind analysis ${formatSummaryStatusTimestamp(
          status.analysisUpdatedAt,
        )}).`,
        `摘要同步状态：待处理${pendingHint}（摘要 ${formatSummaryStatusTimestamp(
          status.summaryCursorUpdatedAt,
        )} 落后于分析 ${formatSummaryStatusTimestamp(
          status.analysisUpdatedAt,
        )}）。`,
      );
    }

    return localize(
      language,
      `Summary sync status: pending${pendingHint} (summary ${formatSummaryStatusTimestamp(
        status.summaryCursorUpdatedAt,
      )} is behind analysis ${formatSummaryStatusTimestamp(
        status.analysisUpdatedAt,
      )}).`,
      `摘要同步状态：待处理${pendingHint}（摘要 ${formatSummaryStatusTimestamp(
        status.summaryCursorUpdatedAt,
      )} 落后于分析 ${formatSummaryStatusTimestamp(
        status.analysisUpdatedAt,
      )}）。`,
    );
  }, [
    language,
    autoSettingsQuery.data?.enabled,
    summaryUpdateStatusQuery.data,
    summaryUpdateStatusQuery.isError,
    summaryUpdateStatusQuery.isPending,
  ]);

  useEffect(() => {
    if (savedAnalysis && !result) {
      setResult(savedAnalysis);
      setResultView("rendered");
    }
  }, [savedAnalysis, result]);

  useEffect(() => {
    if (!shouldPollForAutoAnalysis) {
      setAutoWaitStartedAt(null);
      return;
    }

    if (autoWaitStartedAt == null) {
      setAutoWaitStartedAt(Date.now());
    }

    const timer = window.setInterval(() => {
      setNowMs(Date.now());
    }, 1_000);

    return () => {
      window.clearInterval(timer);
    };
  }, [autoWaitStartedAt, shouldPollForAutoAnalysis]);

  const autoWaitMs =
    shouldPollForAutoAnalysis && autoWaitStartedAt != null
      ? Math.max(0, nowMs - autoWaitStartedAt)
      : 0;

  const pendingAutoMessage = useMemo(() => {
    if (!autoSettingsQuery.data?.enabled) {
      return {
        text: localize(
          language,
          "Click Analyze to generate a structured root cause analysis.",
          "点击“分析”以生成结构化根因分析。",
        ),
        showSettingsLink: false,
      };
    }

    if (autoWaitMs < 15_000) {
      return {
        text: localize(
          language,
          "Auto-generation is enabled. Waiting for the report to appear...",
          "已启用自动生成。正在等待报告出现...",
        ),
        showSettingsLink: false,
      };
    }

    if (autoGenerationStatusQuery.isPending) {
      return {
        text: localize(
          language,
          "Auto-generation is enabled. Still waiting; checking worker status...",
          "已启用自动生成。仍在等待；正在检查 worker 状态...",
        ),
        showSettingsLink: false,
      };
    }

    if (autoGenerationStatusQuery.isError) {
      return {
        text: "Auto-generation is enabled. Still waiting. If this persists, click Analyze to run immediately.",
        showSettingsLink: false,
      };
    }

    const status = autoGenerationStatusQuery.data;
    if (!status) {
      return {
        text: "Auto-generation is enabled. Still waiting. Click Analyze to run immediately.",
        showSettingsLink: false,
      };
    }

    switch (status.hint) {
      case "job_pending":
        return {
          text: `Auto-generation job is ${status.jobState ?? "pending"}${
            status.jobEnqueuedAt
              ? ` (queued ${formatSummaryStatusTimestamp(status.jobEnqueuedAt)})`
              : ""
          }.`,
          showSettingsLink: false,
        };
      case "job_completed_no_result":
        return {
          text: `Auto-generation job completed, but no analysis was saved. This can happen if the observation/context was not queryable yet when the job ran. Click Analyze to run now.`,
          showSettingsLink: false,
        };
      case "job_failed":
        return {
          text: `Auto-generation failed: ${truncateMessage(
            status.jobFailedReason ?? "unknown reason",
            180,
          )}. Click Analyze to retry now.`,
          showSettingsLink: false,
        };
      case "missing_llm_connection":
        return {
          text: "Auto-generation cannot run because no OpenAI LLM connection is configured.",
          showSettingsLink: true,
        };
      case "job_not_found":
        return {
          text: "No auto-generation job found for this observation. Auto-generation only runs for newly ingested ERROR/WARNING items after enabling. Click Analyze to run now.",
          showSettingsLink: false,
        };
      case "disabled":
        return {
          text: "Auto-generation is currently disabled for this project.",
          showSettingsLink: false,
        };
      default:
        return {
          text: `Auto-generation is enabled. Still waiting${
            status.jobState ? ` (job state: ${status.jobState})` : ""
          }. Click Analyze to run immediately.`,
          showSettingsLink: false,
        };
    }
  }, [
    language,
    autoGenerationStatusQuery.data,
    autoGenerationStatusQuery.isError,
    autoGenerationStatusQuery.isPending,
    autoSettingsQuery.data?.enabled,
    autoWaitMs,
  ]);

  const showRetryAutoButton = Boolean(
    autoSettingsQuery.data?.enabled &&
      autoWaitMs >= 15_000 &&
      autoGenerationStatusQuery.data &&
      ["job_completed_no_result", "job_not_found", "job_failed"].includes(
        autoGenerationStatusQuery.data.hint,
      ),
  );

  const runAnalysis = (params?: { clearExisting?: boolean }) => {
    setErrorMessage(null);
    setResultView("rendered");
    if (params?.clearExisting ?? true) {
      setResult(null);
    }
    analyze.mutate({
      traceId: props.traceId,
      projectId: props.projectId,
      observationId: props.observationId,
      model,
      maxContextChars: 80_000,
      timestamp: null,
      fromTimestamp: null,
      verbosity: "full",
    });
  };

  const analyze = api.errorAnalysis.analyze.useMutation({
    onSuccess: (data) => {
      setResult(data);
      setResultView("rendered");
      setErrorMessage(null);
    },
    onError: (err) => {
      setResult(null);
      setErrorMessage(err.message);
    },
  });

  const settingsHref = `/project/${props.projectId}/settings/llm-connections`;
  const showSettingsHint =
    errorMessage?.toLowerCase().includes("llm connections") ||
    errorMessage?.toLowerCase().includes("openai api key") ||
    false;

  return (
    <div className="flex w-[420px] flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-col">
          <div className="text-sm font-medium">
            {localize(language, "LLM Debug Analysis", "LLM 调试分析")}
          </div>
          <div className="text-xs text-muted-foreground">
            {localize(
              language,
              `Analyzes this ${props.level.toLowerCase()} using trace context.`,
              `使用 trace 上下文分析该 ${props.level.toLowerCase()}。`,
            )}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <div className="flex-1">
          <Select
            value={model}
            onValueChange={(v) => {
              if (models.includes(v as ErrorAnalysisModel)) {
                setModel(v as ErrorAnalysisModel);
              }
            }}
          >
            <SelectTrigger className="h-8">
              <SelectValue
                placeholder={localize(language, "Select model", "选择模型")}
              />
            </SelectTrigger>
            <SelectContent>
              {models.map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          variant="secondary"
          size="sm"
          loading={analyze.isPending}
          onClick={() => {
            runAnalysis({ clearExisting: true });
          }}
        >
          {localize(language, "Analyze", "分析")}
        </Button>
        {result ? (
          <Button
            variant="outline"
            size="sm"
            loading={analyze.isPending}
            onClick={() => {
              runAnalysis({ clearExisting: false });
            }}
          >
            {localize(language, "Regenerate", "重新生成")}
          </Button>
        ) : null}
      </div>

      {errorMessage ? (
        <div className="rounded-md border bg-background p-2 text-xs">
          <div className="font-medium text-destructive">
            {localize(language, "Analysis failed", "分析失败")}
          </div>
          <div className="mt-1 whitespace-pre-wrap text-muted-foreground">
            {errorMessage}
          </div>
          {showSettingsHint ? (
            <div className="mt-2">
              <Link className="text-primary underline" href={settingsHref}>
                {localize(
                  language,
                  "Go to Settings → LLM Connections",
                  "前往 设置 → LLM Connections",
                )}
              </Link>
            </div>
          ) : null}
        </div>
      ) : null}

      {result ? (
        <div className="relative rounded-md border bg-background p-2">
          <div className="absolute right-2 top-2">
            <Tabs
              className="h-fit py-0.5"
              value={resultView}
              onValueChange={(value) =>
                setResultView(value as "rendered" | "original")
              }
            >
              <TabsList className="h-fit p-0.5">
                <TabsTrigger value="rendered" className="h-fit px-1 text-xs">
                  {localize(language, "Formatted", "格式化")}
                </TabsTrigger>
                <TabsTrigger value="original" className="h-fit px-1 text-xs">
                  JSON
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          <div className="pt-7">
            {resultView === "rendered" ? (
              <FormattedAnalysisView rendered={result.rendered} />
            ) : (
              <JSONView
                json={result.original}
                hideTitle
                scrollable
                borderless
              />
            )}
          </div>
          {summaryUpdateStatusText ? (
            <div className="mt-3 border-t pt-2 text-[11px] text-muted-foreground">
              {summaryUpdateStatusText}
            </div>
          ) : null}
        </div>
      ) : (
        <div className="rounded-md border bg-background p-2 text-xs text-muted-foreground">
          <div>{pendingAutoMessage.text}</div>
          {pendingAutoMessage.showSettingsLink ? (
            <div className="mt-1">
              <Link className="text-primary underline" href={settingsHref}>
                {localize(
                  language,
                  "Go to Settings → LLM Connections",
                  "前往 设置 → LLM Connections",
                )}
              </Link>
            </div>
          ) : null}
          {autoRetryError ? (
            <div className="mt-2 text-xs text-destructive">
              {autoRetryError}
            </div>
          ) : null}
          {showRetryAutoButton ? (
            <div className="mt-2 flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                loading={retryAutoGeneration.isPending}
                onClick={() => {
                  retryAutoGeneration.mutate({
                    projectId: props.projectId,
                    traceId: props.traceId,
                    observationId: props.observationId,
                  });
                }}
              >
                {localize(language, "Retry auto-generation", "重试自动生成")}
              </Button>
              <div className="text-[11px] text-muted-foreground">
                {localize(
                  language,
                  "Re-queues the worker job.",
                  "重新将 worker 任务加入队列。",
                )}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
