"use client";

import { useEffect, useMemo, useState } from "react";
import Header from "@/src/components/layouts/header";
import { Card, CardContent } from "@/src/components/ui/card";
import { Label } from "@/src/components/ui/label";
import { Switch } from "@/src/components/ui/switch";
import { Button } from "@/src/components/ui/button";
import { Input } from "@/src/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/src/components/ui/select";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { api } from "@/src/utils/api";
import { toast } from "sonner";
import {
  ErrorAnalysisModelSchema,
  type ErrorAnalysisModel,
} from "@/src/features/error-analysis/types";
import {
  ExperienceSummaryMarkdownOutputModeSchema,
  type ExperienceSummaryMarkdownOutputMode,
} from "@/src/features/experience-summary/types";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

function parseNullablePositiveInt(
  value: string,
): number | null | "invalid_format" | "invalid_range" {
  const trimmed = value.trim();
  if (trimmed.length === 0) return null;
  if (!/^\d+$/.test(trimmed)) return "invalid_format";

  const parsed = Number.parseInt(trimmed, 10);
  if (!Number.isSafeInteger(parsed)) return "invalid_format";
  if (parsed < 1) return "invalid_range";
  return parsed;
}

function parseNullablePercentageInt(
  value: string,
): number | null | "invalid_format" | "invalid_range" {
  const trimmed = value.trim();
  if (trimmed.length === 0) return null;
  if (!/^\d+$/.test(trimmed)) return "invalid_format";

  const parsed = Number.parseInt(trimmed, 10);
  if (!Number.isSafeInteger(parsed)) return "invalid_format";
  if (parsed < 1 || parsed > 100) return "invalid_range";
  return parsed;
}

function normalizeOptionalPath(value: string): string | null {
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function isAbsolutePath(value: string): boolean {
  return value.startsWith("/") || /^[a-zA-Z]:[\\/]/.test(value);
}

export function ErrorAnalysisSettings(props: { projectId: string }) {
  const { projectId } = props;
  const { language } = useLanguage();
  const utils = api.useUtils();
  const hasAccess = useHasProjectAccess({
    projectId,
    scope: "project:update",
  });

  const models = useMemo<ErrorAnalysisModel[]>(
    () => [...ErrorAnalysisModelSchema.options] as ErrorAnalysisModel[],
    [],
  );
  const [enabled, setEnabled] = useState(false);
  const [model, setModel] = useState<ErrorAnalysisModel>(models[0]!);
  const [minNewErrorNodesInput, setMinNewErrorNodesInput] = useState("");
  const [
    policyRejectHighlightThresholdInput,
    setPolicyRejectHighlightThresholdInput,
  ] = useState("");
  const [summaryMarkdownPathInput, setSummaryMarkdownPathInput] = useState("");
  const [summaryMarkdownOutputMode, setSummaryMarkdownOutputMode] =
    useState<ExperienceSummaryMarkdownOutputMode>("prompt_pack_only");

  const settingsQuery = api.projects.getErrorAnalysisSettings.useQuery(
    { projectId },
    {
      enabled: Boolean(projectId),
      refetchOnWindowFocus: false,
    },
  );
  useEffect(() => {
    if (!settingsQuery.data) return;
    setEnabled(settingsQuery.data.enabled);
    setModel(settingsQuery.data.model);
    setMinNewErrorNodesInput(
      settingsQuery.data.minNewErrorNodesForSummary == null
        ? ""
        : String(settingsQuery.data.minNewErrorNodesForSummary),
    );
    setPolicyRejectHighlightThresholdInput(
      String(settingsQuery.data.policyRejectHighlightThresholdPct ?? 70),
    );
    setSummaryMarkdownPathInput(
      settingsQuery.data.summaryAppendMarkdownAbsolutePath ?? "",
    );
    setSummaryMarkdownOutputMode(settingsQuery.data.summaryMarkdownOutputMode);
  }, [settingsQuery.data]);

  const saveMutation = api.projects.setErrorAnalysisSettings.useMutation({
    onSuccess: async (saved) => {
      setEnabled(saved.enabled);
      setModel(saved.model);
      setMinNewErrorNodesInput(
        saved.minNewErrorNodesForSummary == null
          ? ""
          : String(saved.minNewErrorNodesForSummary),
      );
      setPolicyRejectHighlightThresholdInput(
        String(saved.policyRejectHighlightThresholdPct),
      );
      setSummaryMarkdownPathInput(
        saved.summaryAppendMarkdownAbsolutePath ?? "",
      );
      setSummaryMarkdownOutputMode(saved.summaryMarkdownOutputMode);
      await utils.projects.getErrorAnalysisSettings.invalidate({ projectId });
      toast.success(
        localize(
          language,
          "Error analysis settings saved",
          "错误分析设置已保存",
        ),
      );
    },
    onError: (error) => {
      toast.error(error.message);
    },
  });

  const parsedMinNewErrorNodes = parseNullablePositiveInt(
    minNewErrorNodesInput,
  );
  const parsedPolicyRejectHighlightThreshold = parseNullablePercentageInt(
    policyRejectHighlightThresholdInput,
  );
  const normalizedSummaryMarkdownPath = normalizeOptionalPath(
    summaryMarkdownPathInput,
  );
  const summaryPathHasInvalidAbsoluteFormat = Boolean(
    normalizedSummaryMarkdownPath &&
      !isAbsolutePath(normalizedSummaryMarkdownPath),
  );
  const summaryPathHasInvalidExtension = Boolean(
    normalizedSummaryMarkdownPath &&
      !normalizedSummaryMarkdownPath.toLowerCase().endsWith(".md"),
  );
  const hasValidationErrors =
    parsedMinNewErrorNodes === "invalid_format" ||
    parsedMinNewErrorNodes === "invalid_range" ||
    parsedPolicyRejectHighlightThreshold === "invalid_format" ||
    parsedPolicyRejectHighlightThreshold === "invalid_range" ||
    summaryPathHasInvalidAbsoluteFormat ||
    summaryPathHasInvalidExtension;
  const hasInvalidMinNewErrorNodes =
    parsedMinNewErrorNodes === "invalid_format" ||
    parsedMinNewErrorNodes === "invalid_range";
  const hasInvalidPolicyRejectHighlightThreshold =
    parsedPolicyRejectHighlightThreshold === "invalid_format" ||
    parsedPolicyRejectHighlightThreshold === "invalid_range";
  const minNewErrorNodesForSave: number | null = hasInvalidMinNewErrorNodes
    ? null
    : parsedMinNewErrorNodes;
  const policyRejectHighlightThresholdForSave: number =
    hasInvalidPolicyRejectHighlightThreshold
      ? 70
      : (parsedPolicyRejectHighlightThreshold ?? 70);

  const hasUnsavedChanges =
    settingsQuery.data != null &&
    (enabled !== settingsQuery.data.enabled ||
      model !== settingsQuery.data.model ||
      (hasInvalidMinNewErrorNodes
        ? minNewErrorNodesInput.trim().length > 0
        : minNewErrorNodesForSave !==
          settingsQuery.data.minNewErrorNodesForSummary) ||
      (hasInvalidPolicyRejectHighlightThreshold
        ? policyRejectHighlightThresholdInput.trim().length > 0
        : policyRejectHighlightThresholdForSave !==
          settingsQuery.data.policyRejectHighlightThresholdPct) ||
      normalizedSummaryMarkdownPath !==
        settingsQuery.data.summaryAppendMarkdownAbsolutePath ||
      summaryMarkdownOutputMode !==
        settingsQuery.data.summaryMarkdownOutputMode);

  return (
    <div>
      <Header title={localize(language, "Error Analysis", "错误分析")} />
      <Card className="mt-4">
        <CardContent className="space-y-6 p-6">
          <div>
            <h3 className="text-lg font-medium">
              {localize(language, "Automatic Error Analysis", "自动错误分析")}
            </h3>
            <p className="text-sm text-muted-foreground">
              {localize(
                language,
                "Automatically run LLM analysis when an observation is ingested with level ERROR or WARNING.",
                "当 observation 以 ERROR 或 WARNING 级别写入时，自动运行 LLM 分析。",
              )}
            </p>
          </div>

          {settingsQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">
              {localize(language, "Loading settings...", "正在加载设置...")}
            </p>
          ) : settingsQuery.error ? (
            <p className="text-sm text-destructive">
              {settingsQuery.error.message}
            </p>
          ) : (
            <>
              <div className="flex items-center justify-between rounded-lg border p-4">
                <div className="space-y-0.5">
                  <Label htmlFor="auto-error-analysis" className="text-base">
                    {localize(
                      language,
                      "Auto-generate error analysis",
                      "自动生成错误分析",
                    )}
                  </Label>
                  <p className="text-sm text-muted-foreground">
                    {localize(
                      language,
                      "Run analysis automatically for newly ingested errors/warnings.",
                      "为新写入的错误/警告自动运行分析。",
                    )}
                  </p>
                </div>
                <Switch
                  id="auto-error-analysis"
                  checked={enabled}
                  onCheckedChange={setEnabled}
                  disabled={!hasAccess || saveMutation.isPending}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="auto-error-analysis-model">
                  {localize(language, "Model", "模型")}
                </Label>
                <Select
                  value={model}
                  onValueChange={(value) => {
                    if (models.includes(value as ErrorAnalysisModel)) {
                      setModel(value as ErrorAnalysisModel);
                    }
                  }}
                  disabled={!enabled || !hasAccess || saveMutation.isPending}
                >
                  <SelectTrigger
                    id="auto-error-analysis-model"
                    className="max-w-[240px]"
                  >
                    <SelectValue
                      placeholder={localize(
                        language,
                        "Select model",
                        "选择模型",
                      )}
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
                {!enabled ? (
                  <p className="text-xs text-muted-foreground">
                    {localize(
                      language,
                      "Enable auto-generation to select a model.",
                      "启用自动生成功能后才能选择模型。",
                    )}
                  </p>
                ) : null}
              </div>

              <div className="space-y-2">
                <Label htmlFor="auto-summary-threshold">
                  {localize(
                    language,
                    "New error nodes before auto summary update",
                    "触发自动摘要更新前的新错误节点数",
                  )}
                </Label>
                <Input
                  id="auto-summary-threshold"
                  inputMode="numeric"
                  value={minNewErrorNodesInput}
                  onChange={(e) => setMinNewErrorNodesInput(e.target.value)}
                  placeholder={localize(language, "5 (default)", "5（默认）")}
                  disabled={!hasAccess || saveMutation.isPending}
                  className="max-w-[240px]"
                />
                <p className="text-xs text-muted-foreground">
                  {localize(
                    language,
                    "Leave empty to use the default threshold: 1.",
                    "留空则使用默认阈值：1。",
                  )}
                </p>
                {parsedMinNewErrorNodes === "invalid_format" ? (
                  <p className="text-xs text-destructive">
                    {localize(
                      language,
                      "Please enter a whole number or leave it empty.",
                      "请输入整数，或留空。",
                    )}
                  </p>
                ) : null}
                {parsedMinNewErrorNodes === "invalid_range" ? (
                  <p className="text-xs text-destructive">
                    {localize(
                      language,
                      "Threshold must be at least 1.",
                      "阈值必须至少为 1。",
                    )}
                  </p>
                ) : null}
              </div>

              <div className="space-y-2">
                <Label htmlFor="policy-reject-highlight-threshold">
                  {localize(
                    language,
                    "Policy reject-rate highlight threshold (%)",
                    "策略拒绝率高亮阈值（%）",
                  )}
                </Label>
                <Input
                  id="policy-reject-highlight-threshold"
                  inputMode="numeric"
                  value={policyRejectHighlightThresholdInput}
                  onChange={(e) =>
                    setPolicyRejectHighlightThresholdInput(e.target.value)
                  }
                  placeholder={localize(language, "70 (default)", "70（默认）")}
                  disabled={!hasAccess || saveMutation.isPending}
                  className="max-w-[240px]"
                />
                <p className="text-xs text-muted-foreground">
                  {localize(
                    language,
                    "Rows are highlighted on Home when reject rate is at or above this threshold. Leave empty to use 70.",
                    "当拒绝率达到或超过该阈值时，首页中的行会被高亮。留空则使用 70。",
                  )}
                </p>
                {parsedPolicyRejectHighlightThreshold === "invalid_format" ? (
                  <p className="text-xs text-destructive">
                    {localize(
                      language,
                      "Please enter a whole number from 1 to 100, or leave it empty.",
                      "请输入 1 到 100 之间的整数，或留空。",
                    )}
                  </p>
                ) : null}
                {parsedPolicyRejectHighlightThreshold === "invalid_range" ? (
                  <p className="text-xs text-destructive">
                    {localize(
                      language,
                      "Threshold must be between 1 and 100.",
                      "阈值必须在 1 到 100 之间。",
                    )}
                  </p>
                ) : null}
              </div>

              <div className="space-y-2">
                <Label htmlFor="summary-markdown-mode">
                  {localize(
                    language,
                    "Markdown summary output mode",
                    "Markdown 摘要输出模式",
                  )}
                </Label>
                <Select
                  value={summaryMarkdownOutputMode}
                  onValueChange={(value) => {
                    if (
                      ExperienceSummaryMarkdownOutputModeSchema.options.includes(
                        value as ExperienceSummaryMarkdownOutputMode,
                      )
                    ) {
                      setSummaryMarkdownOutputMode(
                        value as ExperienceSummaryMarkdownOutputMode,
                      );
                    }
                  }}
                  disabled={!hasAccess || saveMutation.isPending}
                >
                  <SelectTrigger
                    id="summary-markdown-mode"
                    className="max-w-[260px]"
                  >
                    <SelectValue
                      placeholder={localize(
                        language,
                        "Select output mode",
                        "选择输出模式",
                      )}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="prompt_pack_only">
                      {localize(
                        language,
                        "Prompt pack only (recommended)",
                        "仅 Prompt pack（推荐）",
                      )}
                    </SelectItem>
                    <SelectItem value="full">
                      {localize(
                        language,
                        "Prompt pack + experiences",
                        "Prompt pack + experiences",
                      )}
                    </SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  {localize(
                    language,
                    "Controls what gets written to markdown. Database summary still keeps full structured experiences.",
                    "控制写入 markdown 的内容。数据库中的摘要仍会保留完整的结构化 experiences。",
                  )}
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="summary-md-path">
                  {localize(
                    language,
                    "Append summary prevention note to markdown path (optional)",
                    "将摘要预防建议追加到 markdown 路径（可选）",
                  )}
                </Label>
                <Input
                  id="summary-md-path"
                  value={summaryMarkdownPathInput}
                  onChange={(e) => setSummaryMarkdownPathInput(e.target.value)}
                  placeholder={localize(
                    language,
                    "/absolute/path/to/error-summary.md",
                    "/absolute/path/to/error-summary.md",
                  )}
                  disabled={!hasAccess || saveMutation.isPending}
                />
                <p className="text-xs text-muted-foreground">
                  {localize(
                    language,
                    "Use an absolute `.md` path. If the file does not exist, it will be created automatically. The `## HINT` (or `# HINT`) section is replaced on each summary update with the latest complete summary.",
                    "请使用绝对路径的 `.md` 文件。如果文件不存在，会自动创建。每次摘要更新时，`## HINT`（或 `# HINT`）部分都会被最新的完整摘要替换。",
                  )}
                </p>
                <p className="text-xs text-muted-foreground">
                  {localize(
                    language,
                    "Production: the bundled Docker compose mounts your home directory at the same absolute path, so local paths under home usually work directly. Use `LANGFUSE_PATH_PREFIX_MAP` only for custom mount layouts or paths outside your home directory.",
                    "生产环境：内置的 Docker compose 会将你的 home 目录挂载到相同的绝对路径，因此 home 目录下的本地路径通常可以直接使用。仅在自定义挂载布局或 home 目录外的路径场景下使用 `LANGFUSE_PATH_PREFIX_MAP`。",
                  )}
                </p>
                {summaryPathHasInvalidAbsoluteFormat ? (
                  <p className="text-xs text-destructive">
                    {localize(
                      language,
                      "Path must be absolute.",
                      "路径必须是绝对路径。",
                    )}
                  </p>
                ) : null}
                {summaryPathHasInvalidExtension ? (
                  <p className="text-xs text-destructive">
                    {localize(
                      language,
                      "Path must end with `.md`.",
                      "路径必须以 `.md` 结尾。",
                    )}
                  </p>
                ) : null}
              </div>

              <Button
                variant="secondary"
                size="sm"
                loading={saveMutation.isPending}
                disabled={
                  !hasAccess || !hasUnsavedChanges || hasValidationErrors
                }
                onClick={() => {
                  if (
                    parsedMinNewErrorNodes === "invalid_format" ||
                    parsedMinNewErrorNodes === "invalid_range"
                  ) {
                    toast.error(
                      localize(
                        language,
                        "Invalid threshold. Enter a whole number or leave empty.",
                        "阈值无效。请输入整数，或留空。",
                      ),
                    );
                    return;
                  }
                  if (
                    parsedPolicyRejectHighlightThreshold === "invalid_format" ||
                    parsedPolicyRejectHighlightThreshold === "invalid_range"
                  ) {
                    toast.error(
                      localize(
                        language,
                        "Invalid policy highlight threshold. Enter a whole number between 1 and 100, or leave empty.",
                        "策略高亮阈值无效。请输入 1 到 100 之间的整数，或留空。",
                      ),
                    );
                    return;
                  }
                  if (summaryPathHasInvalidAbsoluteFormat) {
                    toast.error(
                      localize(
                        language,
                        "Summary markdown path must be absolute.",
                        "摘要 markdown 路径必须是绝对路径。",
                      ),
                    );
                    return;
                  }
                  if (summaryPathHasInvalidExtension) {
                    toast.error(
                      localize(
                        language,
                        "Summary markdown path must end with .md.",
                        "摘要 markdown 路径必须以 .md 结尾。",
                      ),
                    );
                    return;
                  }

                  saveMutation.mutate({
                    projectId,
                    enabled,
                    model,
                    minNewErrorNodesForSummary: minNewErrorNodesForSave,
                    policyRejectHighlightThresholdPct:
                      policyRejectHighlightThresholdForSave,
                    summaryAppendMarkdownAbsolutePath:
                      normalizedSummaryMarkdownPath,
                    summaryMarkdownOutputMode,
                  });
                }}
              >
                {localize(language, "Save", "保存")}
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
