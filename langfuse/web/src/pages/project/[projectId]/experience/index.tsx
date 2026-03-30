import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/router";
import { ChevronDown, Settings } from "lucide-react";
import Page from "@/src/components/layouts/page";
import { Button } from "@/src/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/src/components/ui/tabs";
import { CodeView, JSONView } from "@/src/components/ui/CodeJsonViewer";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/src/components/ui/select";
import { Input } from "@/src/components/ui/input";
import { Textarea } from "@/src/components/ui/textarea";
import { Label } from "@/src/components/ui/label";
import { Badge } from "@/src/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/src/components/ui/card";
import {
  Collapsible,
  CollapsibleContent,
} from "@/src/components/ui/collapsible";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/src/components/ui/alert-dialog";
import { api } from "@/src/utils/api";
import { copyTextToClipboard } from "@/src/utils/clipboard";
import { cn } from "@/src/utils/tailwind";
import { toast } from "sonner";
import {
  ExperienceSummaryJsonSchema,
  ExperienceSummaryModelSchema,
  type ExperienceSummaryJson,
  type ExperienceSummaryModel,
} from "@/src/features/experience-summary/types";
import { ExperienceSummaryView } from "@/src/features/experience-summary/components/ExperienceSummaryView";
import { buildExperienceCopyAllText } from "@/src/features/experience-summary/utils";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";

function downloadJson(params: { data: unknown; filename: string }) {
  const blob = new Blob([JSON.stringify(params.data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = params.filename;
  a.click();
  URL.revokeObjectURL(url);
}

type SummaryView = "formatted" | "json" | "raw";
type EditEntryTarget =
  | {
      section: "prompt_pack";
      experienceKey?: string;
    }
  | {
      section: "experience";
      experienceKey?: string;
    };
type PendingUnsavedAction =
  | { type: "leave_editing" }
  | { type: "switch_view"; nextView: SummaryView }
  | { type: "route_change"; url: string };

function parseEditExperienceIndex(value: string): number | null {
  const match = /^experience-(\d+)$/.exec(value);
  if (!match) return null;
  const parsed = Number(match[1]);
  return Number.isInteger(parsed) ? parsed : null;
}

function toMultiline(values: string[] | null | undefined): string {
  return (values ?? []).join("\n");
}

function parseMultiline(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function toCsv(values: string[] | null | undefined): string {
  return (values ?? []).join(", ");
}

function parseCsv(value: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const token of value.split(",")) {
    const normalized = token.trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

export default function ExperienceSummaryPage() {
  const { t } = useLanguage();
  const router = useRouter();
  const utils = api.useUtils();
  const projectId = router.query.projectId as string | undefined;

  const models = useMemo<ExperienceSummaryModel[]>(
    () => [...ExperienceSummaryModelSchema.options] as ExperienceSummaryModel[],
    [],
  );
  const [model, setModel] = useState<ExperienceSummaryModel>(models[0]!);
  const [view, setView] = useState<SummaryView>("formatted");
  const [isEditing, setIsEditing] = useState(false);
  const [editDraft, setEditDraft] = useState<ExperienceSummaryJson | null>(
    null,
  );
  const [expandedEditExperienceValues, setExpandedEditExperienceValues] =
    useState<string[]>([]);
  const [expandedFormattedExperienceKeys, setExpandedFormattedExperienceKeys] =
    useState<string[]>([]);
  const [pendingScrollTargetId, setPendingScrollTargetId] = useState<
    string | null
  >(null);
  const [pendingFormattedScrollTargetId, setPendingFormattedScrollTargetId] =
    useState<string | null>(null);
  const [pendingUnsavedAction, setPendingUnsavedAction] =
    useState<PendingUnsavedAction | null>(null);
  const [isUnsavedPromptOpen, setIsUnsavedPromptOpen] = useState(false);
  const [isUnsavedPromptBusy, setIsUnsavedPromptBusy] = useState(false);
  const allowNextRouteChangeRef = useRef(false);

  const getQuery = api.experienceSummary.get.useQuery(
    { projectId: projectId ?? "" },
    { enabled: Boolean(projectId), refetchOnWindowFocus: false },
  );

  const incrementalStatusQuery =
    api.experienceSummary.getIncrementalUpdateStatus.useQuery(
      { projectId: projectId ?? "" },
      {
        enabled: Boolean(projectId),
        refetchOnWindowFocus: true,
      },
    );

  const generate = api.experienceSummary.generate.useMutation({
    onSuccess: async () => {
      toast.success(t("summary.updated"));
      await Promise.allSettled([
        utils.experienceSummary.get.invalidate({
          projectId: projectId ?? "",
        }),
        utils.experienceSummary.getIncrementalUpdateStatus.invalidate({
          projectId: projectId ?? "",
        }),
        // Refresh any open error-analysis dropdown status widgets.
        utils.errorAnalysis.getSummaryUpdateStatus.invalidate(),
      ]);
    },
    onError: (err) => {
      toast.error(err.message);
    },
  });

  const updateSummary = api.experienceSummary.update.useMutation({
    onSuccess: async (updatedRow) => {
      preserveFormattedContextFromEdit(updatedRow.summary);
      setEditDraft(updatedRow.summary);
      setExpandedEditExperienceValues([]);
      setPendingScrollTargetId(null);
      setIsEditing(false);
      toast.success(t("summary.saved"));
      await Promise.allSettled([
        utils.experienceSummary.get.invalidate({
          projectId: projectId ?? "",
        }),
        utils.experienceSummary.getIncrementalUpdateStatus.invalidate({
          projectId: projectId ?? "",
        }),
      ]);
    },
    onError: (err) => {
      toast.error(err.message);
    },
  });

  const writeMarkdown = api.experienceSummary.writeMarkdown.useMutation({
    onSuccess: ({ path }) => {
      toast.success(`${t("summary.writtenTo")} ${path}`);
    },
    onError: (err) => {
      toast.error(err.message);
    },
  });

  const row = getQuery.data;
  const summary = row?.summary ?? null;
  useEffect(() => {
    setEditDraft(summary);
    setExpandedEditExperienceValues([]);
    setPendingScrollTargetId(null);
  }, [row?.updatedAt, summary]);

  const canAct = Boolean(projectId) && !generate.isPending;
  const pendingAnalysesCount =
    incrementalStatusQuery.data?.pendingAnalysesCount;
  const hasIncrementalStatus = typeof pendingAnalysesCount === "number";
  const isIncrementalUpToDate =
    hasIncrementalStatus && pendingAnalysesCount === 0;
  const canRunIncremental =
    canAct && hasIncrementalStatus && !isIncrementalUpToDate;
  const hasUnsavedChanges = Boolean(
    summary &&
      editDraft &&
      JSON.stringify(summary) !== JSON.stringify(editDraft),
  );

  const updateDraft = (
    updater: (current: ExperienceSummaryJson) => ExperienceSummaryJson,
  ) => {
    setEditDraft((current) => (current ? updater(current) : current));
  };

  const saveEditedSummary = useCallback(async () => {
    if (!projectId || !editDraft) return false;
    const parsed = ExperienceSummaryJsonSchema.safeParse(editDraft);
    if (!parsed.success) {
      toast.error(
        parsed.error.issues[0]?.message ?? t("summary.invalidPayload"),
      );
      return false;
    }
    try {
      await updateSummary.mutateAsync({
        projectId,
        summary: parsed.data,
      });
      return true;
    } catch {
      return false;
    }
  }, [editDraft, projectId, updateSummary]);

  const addExperience = () => {
    updateDraft((current) => ({
      ...current,
      experiences: [
        ...current.experiences,
        {
          key: `experience_${current.experiences.length + 1}`,
          when: "",
          keywords: [],
          relatedErrorTypes: [],
          possibleProblems: [],
          avoidanceAndNotes: [],
          promptAdditions: [],
        },
      ],
    }));
  };

  const preserveFormattedContextFromEdit = useCallback(
    (sourceSummary: ExperienceSummaryJson | null) => {
      if (!sourceSummary) {
        setExpandedFormattedExperienceKeys([]);
        setPendingFormattedScrollTargetId(null);
        return;
      }

      const expandedIndexes = expandedEditExperienceValues
        .map(parseEditExperienceIndex)
        .filter((index): index is number => index !== null)
        .filter(
          (index) => index >= 0 && index < sourceSummary.experiences.length,
        );

      const expandedKeys = [
        ...new Set(
          expandedIndexes
            .map((index) => sourceSummary.experiences[index]?.key)
            .filter((value): value is string => Boolean(value)),
        ),
      ];
      const lastExpandedIndex = expandedIndexes[expandedIndexes.length - 1];
      const scrollKey =
        lastExpandedIndex != null
          ? sourceSummary.experiences[lastExpandedIndex]?.key
          : null;

      setExpandedFormattedExperienceKeys(expandedKeys);
      setPendingFormattedScrollTargetId(
        scrollKey ? `experience-pack-${scrollKey}` : null,
      );
    },
    [expandedEditExperienceValues],
  );

  useEffect(() => {
    if (!summary) {
      setExpandedFormattedExperienceKeys([]);
      return;
    }

    setExpandedFormattedExperienceKeys((current) =>
      current.filter((key) =>
        summary.experiences.some((experience) => experience.key === key),
      ),
    );
  }, [summary]);

  const enterEditing = useCallback(
    (params?: EditEntryTarget) => {
      if (!summary) return;

      if (
        params?.section === "experience" &&
        params.experienceKey &&
        editDraft
      ) {
        const selectedIndex = editDraft.experiences.findIndex(
          (item) => item.key === params.experienceKey,
        );
        if (selectedIndex >= 0) {
          const selectedValue = `experience-${selectedIndex}`;
          setExpandedEditExperienceValues((current) =>
            current.includes(selectedValue)
              ? current
              : [...current, selectedValue],
          );
          setPendingScrollTargetId(`edit-experience-${selectedValue}`);
        }
      }

      setIsEditing(true);
    },
    [editDraft, summary],
  );

  const discardEditedSummary = useCallback(() => {
    if (!isEditing) return;
    if (summary) {
      preserveFormattedContextFromEdit(summary);
      setEditDraft(summary);
    } else {
      preserveFormattedContextFromEdit(null);
    }
    setExpandedEditExperienceValues([]);
    setPendingScrollTargetId(null);
    setIsEditing(false);
  }, [isEditing, preserveFormattedContextFromEdit, summary]);

  const closeEditing = useCallback(
    (params: { discardChanges: boolean }) => {
      if (!isEditing) return;

      if (params.discardChanges) {
        if (summary) {
          preserveFormattedContextFromEdit(summary);
          setEditDraft(summary);
        } else {
          preserveFormattedContextFromEdit(null);
        }
      } else {
        preserveFormattedContextFromEdit(editDraft ?? summary);
      }

      setExpandedEditExperienceValues([]);
      setPendingScrollTargetId(null);
      setIsEditing(false);
    },
    [editDraft, isEditing, preserveFormattedContextFromEdit, summary],
  );

  const executePendingUnsavedAction = useCallback(
    (action: PendingUnsavedAction | null) => {
      if (!action) return;
      if (action.type === "switch_view") {
        setView(action.nextView);
        return;
      }
      if (action.type === "route_change") {
        allowNextRouteChangeRef.current = true;
        void router.push(action.url);
      }
    },
    [router],
  );

  const promptForUnsavedChanges = useCallback(
    (action: PendingUnsavedAction) => {
      setPendingUnsavedAction(action);
      setIsUnsavedPromptBusy(false);
      setIsUnsavedPromptOpen(true);
    },
    [],
  );

  const requestLeaveEditing = useCallback(
    (actionAfterLeave: PendingUnsavedAction) => {
      if (!isEditing) {
        executePendingUnsavedAction(actionAfterLeave);
        return true;
      }

      if (!hasUnsavedChanges) {
        closeEditing({ discardChanges: false });
        executePendingUnsavedAction(actionAfterLeave);
        return true;
      }

      promptForUnsavedChanges(actionAfterLeave);
      return false;
    },
    [
      closeEditing,
      executePendingUnsavedAction,
      hasUnsavedChanges,
      isEditing,
      promptForUnsavedChanges,
    ],
  );

  const handleUnsavedSaveAndContinue = useCallback(async () => {
    if (!pendingUnsavedAction) return;
    setIsUnsavedPromptBusy(true);
    const saveSucceeded = await saveEditedSummary();
    if (!saveSucceeded) {
      setIsUnsavedPromptBusy(false);
      return;
    }

    const action = pendingUnsavedAction;
    setPendingUnsavedAction(null);
    setIsUnsavedPromptOpen(false);
    setIsUnsavedPromptBusy(false);
    executePendingUnsavedAction(action);
  }, [executePendingUnsavedAction, pendingUnsavedAction, saveEditedSummary]);

  const handleUnsavedDiscardAndContinue = useCallback(() => {
    if (!pendingUnsavedAction) return;
    setIsUnsavedPromptBusy(true);
    closeEditing({ discardChanges: true });
    const action = pendingUnsavedAction;
    setPendingUnsavedAction(null);
    setIsUnsavedPromptOpen(false);
    setIsUnsavedPromptBusy(false);
    executePendingUnsavedAction(action);
  }, [closeEditing, executePendingUnsavedAction, pendingUnsavedAction]);

  const handleUnsavedCancel = useCallback(() => {
    setPendingUnsavedAction(null);
    setIsUnsavedPromptOpen(false);
    setIsUnsavedPromptBusy(false);
  }, []);

  useEffect(() => {
    if (!hasUnsavedChanges) return;

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };

    const handleRouteChangeStart = (url: string) => {
      if (allowNextRouteChangeRef.current) {
        allowNextRouteChangeRef.current = false;
        return;
      }

      const cancellationMessage = t("summary.routeChangeAborted");
      promptForUnsavedChanges({ type: "route_change", url });
      router.events.emit("routeChangeError", cancellationMessage, url, {
        shallow: false,
      });
      // Throw a non-Error sentinel so Next.js cancels the route without
      // surfacing the navigation abort as a runtime error in dev.
      // eslint-disable-next-line @typescript-eslint/no-throw-literal
      throw cancellationMessage;
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    router.events.on("routeChangeStart", handleRouteChangeStart);

    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      router.events.off("routeChangeStart", handleRouteChangeStart);
    };
  }, [hasUnsavedChanges, promptForUnsavedChanges, router.events, t]);

  useEffect(() => {
    if (!isEditing) return;

    const handleMouseDown = (event: MouseEvent) => {
      if (isUnsavedPromptOpen) return;
      const target = event.target as HTMLElement | null;
      if (!target) return;
      if (
        target.closest(
          "button, a, input, textarea, select, label, [role='button'], [role='tab'], [role='combobox'], [contenteditable='true']",
        )
      ) {
        return;
      }

      requestLeaveEditing({ type: "leave_editing" });
    };

    document.addEventListener("mousedown", handleMouseDown, true);

    return () => {
      document.removeEventListener("mousedown", handleMouseDown, true);
    };
  }, [isEditing, isUnsavedPromptOpen, requestLeaveEditing]);

  useEffect(() => {
    if (!isEditing || !pendingScrollTargetId) return;

    const timer = window.setTimeout(() => {
      const target = document.getElementById(pendingScrollTargetId);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      setPendingScrollTargetId(null);
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [isEditing, pendingScrollTargetId]);

  useEffect(() => {
    if (isEditing || !pendingFormattedScrollTargetId) return;

    const timer = window.setTimeout(() => {
      const target = document.getElementById(pendingFormattedScrollTargetId);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      setPendingFormattedScrollTargetId(null);
    }, 0);

    return () => {
      window.clearTimeout(timer);
    };
  }, [isEditing, pendingFormattedScrollTargetId]);

  const unsavedPromptDescription =
    pendingUnsavedAction?.type === "route_change"
      ? t("summary.unsavedChangesLeave")
      : t("summary.unsavedChangesLeaveEditing");

  return (
    <Page
      headerProps={{
        title: t("summary.pageTitle"),
      }}
      scrollable
      withPadding
    >
      <div className="flex min-h-0 flex-1 flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={model}
              onValueChange={(v) => {
                if (models.includes(v as ExperienceSummaryModel)) {
                  setModel(v as ExperienceSummaryModel);
                }
              }}
            >
              <SelectTrigger className="h-8 w-[180px]">
                <SelectValue placeholder={t("summary.selectModel")} />
              </SelectTrigger>
              <SelectContent>
                {models.map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Button
              variant="secondary"
              size="sm"
              loading={generate.isPending}
              disabled={!canAct}
              onClick={() => {
                if (!projectId) return;
                generate.mutate({
                  projectId,
                  mode: "full",
                  model,
                  maxItems: 50,
                });
              }}
            >
              {t("summary.generateFull")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              loading={generate.isPending}
              disabled={!canRunIncremental}
              title={
                !hasIncrementalStatus
                  ? t("summary.checkingIncrementalStatus")
                  : isIncrementalUpToDate
                    ? t("summary.alreadyUpToDate")
                    : undefined
              }
              onClick={() => {
                if (!projectId) return;
                generate.mutate({
                  projectId,
                  mode: "incremental",
                  model,
                  maxItems: 50,
                });
              }}
            >
              {t("summary.updateIncremental")}
            </Button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!summary || !projectId || writeMarkdown.isPending}
              loading={writeMarkdown.isPending}
              onClick={() => {
                if (!projectId) return;
                writeMarkdown.mutate({ projectId });
              }}
            >
              {t("summary.insertMarkdownPath")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!projectId}
              onClick={() => {
                if (!projectId) return;
                void router.push(
                  `/project/${projectId}/settings/error-analysis`,
                );
              }}
            >
              <Settings className="mr-1 h-3.5 w-3.5" />
              {t("summary.settings")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!summary}
              onClick={async () => {
                if (!summary) return;
                await copyTextToClipboard(buildExperienceCopyAllText(summary));
                toast.success(t("summary.copiedAllPromptLines"));
              }}
            >
              {t("summary.copyAll")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!summary}
              onClick={() => {
                if (!summary || !projectId) return;
                downloadJson({
                  data: summary,
                  filename: `experience-summary-${projectId}.json`,
                });
                toast.success(t("summary.downloadedJson"));
              }}
            >
              {t("summary.downloadJson")}
            </Button>
          </div>
        </div>

        {getQuery.isLoading ? (
          <div className="rounded-md border bg-background p-3 text-sm text-muted-foreground">
            {t("summary.loading")}
          </div>
        ) : getQuery.error ? (
          <div className="rounded-md border bg-background p-3 text-sm text-muted-foreground">
            {getQuery.error.message}
          </div>
        ) : summary ? (
          <div className="rounded-md border bg-background p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-sm font-medium">{t("summary.title")}</div>
              <div className="flex flex-wrap items-center gap-2">
                {isEditing ? (
                  <>
                    <Button
                      variant="secondary"
                      size="sm"
                      loading={updateSummary.isPending}
                      disabled={
                        !editDraft ||
                        !hasUnsavedChanges ||
                        updateSummary.isPending
                      }
                      onClick={() => {
                        void saveEditedSummary();
                      }}
                    >
                      {t("summary.save")}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!summary || updateSummary.isPending}
                      onClick={discardEditedSummary}
                    >
                      {t("summary.discard")}
                    </Button>
                  </>
                ) : null}
                <Tabs
                  value={view}
                  onValueChange={(v) => {
                    const nextView = v as SummaryView;
                    if (nextView === view) return;
                    if (isEditing) {
                      requestLeaveEditing({
                        type: "switch_view",
                        nextView,
                      });
                      return;
                    }
                    setView(nextView);
                  }}
                >
                  <TabsList className="h-fit p-0.5">
                    <TabsTrigger
                      value="formatted"
                      className="h-fit px-1 text-xs"
                    >
                      {t("summary.tabFormatted")}
                    </TabsTrigger>
                    <TabsTrigger value="json" className="h-fit px-1 text-xs">
                      {t("summary.tabJson")}
                    </TabsTrigger>
                    <TabsTrigger value="raw" className="h-fit px-1 text-xs">
                      {t("summary.tabRawJson")}
                    </TabsTrigger>
                  </TabsList>
                </Tabs>
              </div>
            </div>

            <div className="mt-3">
              {isEditing ? (
                editDraft ? (
                  <div className="space-y-4">
                    <Card>
                      <CardHeader>
                        <CardTitle className="text-base">
                          {t("summary.promptPack")}
                        </CardTitle>
                        <CardDescription>
                          {t("summary.promptPackDescription")}
                        </CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        <div className="space-y-1">
                          <Label htmlFor="prompt-pack-title">
                            {t("summary.promptPackTitle")}
                          </Label>
                          <Input
                            id="prompt-pack-title"
                            value={editDraft.promptPack.title}
                            onChange={(e) =>
                              updateDraft((current) => ({
                                ...current,
                                promptPack: {
                                  ...current.promptPack,
                                  title: e.target.value,
                                },
                              }))
                            }
                          />
                        </div>
                        <div className="space-y-1">
                          <Label htmlFor="prompt-pack-lines">
                            {t("summary.promptPackLines")}
                          </Label>
                          <Textarea
                            id="prompt-pack-lines"
                            rows={8}
                            value={toMultiline(editDraft.promptPack.lines)}
                            onChange={(e) =>
                              updateDraft((current) => ({
                                ...current,
                                promptPack: {
                                  ...current.promptPack,
                                  lines: parseMultiline(e.target.value),
                                },
                              }))
                            }
                          />
                        </div>
                      </CardContent>
                    </Card>

                    <div className="flex items-center justify-between gap-2">
                      <div className="text-sm font-medium">
                        {t("summary.experiences")}
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={addExperience}
                      >
                        {t("summary.addExperience")}
                      </Button>
                    </div>

                    {editDraft.experiences.length === 0 ? (
                      <div className="rounded-md border bg-background p-3 text-sm text-muted-foreground">
                        {t("summary.noExperiencesYet")}
                      </div>
                    ) : (
                      <div className="space-y-3">
                        {editDraft.experiences.map((experience, index) => {
                          const experienceValue = `experience-${index}`;
                          const isExpanded =
                            expandedEditExperienceValues.includes(
                              experienceValue,
                            );

                          return (
                            <Card
                              key={experienceValue}
                              id={`edit-experience-${experienceValue}`}
                              className="scroll-mt-20"
                            >
                              <CardHeader>
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                  <div className="flex flex-wrap items-center gap-2">
                                    <CardTitle className="text-base">
                                      {experience.key ||
                                        `${t("summary.experience")} ${index + 1}`}
                                    </CardTitle>
                                    {experience.relatedErrorTypes?.length ? (
                                      <div className="flex flex-wrap items-center gap-1">
                                        {experience.relatedErrorTypes
                                          .slice(0, 6)
                                          .map((t, idx) => (
                                            <Badge
                                              key={`${t}-${idx}`}
                                              variant="outline"
                                              className="border-red-200 bg-red-100 font-mono text-red-700 dark:border-red-900 dark:bg-red-900/40 dark:text-red-300"
                                            >
                                              {t}
                                            </Badge>
                                          ))}
                                        {experience.relatedErrorTypes.length >
                                        6 ? (
                                          <Badge
                                            variant="outline"
                                            className="border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-900/20 dark:text-red-300"
                                          >
                                            +
                                            {experience.relatedErrorTypes
                                              .length - 6}
                                          </Badge>
                                        ) : null}
                                      </div>
                                    ) : null}
                                  </div>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-7 px-2 text-xs"
                                    onClick={() =>
                                      setExpandedEditExperienceValues(
                                        (current) =>
                                          current.includes(experienceValue)
                                            ? current.filter(
                                                (v) => v !== experienceValue,
                                              )
                                            : [...current, experienceValue],
                                      )
                                    }
                                  >
                                    {isExpanded
                                      ? t("summary.collapse")
                                      : t("summary.expand")}
                                    <ChevronDown
                                      className={cn(
                                        "ml-1 h-3.5 w-3.5 transition-transform",
                                        isExpanded ? "rotate-180" : "rotate-0",
                                      )}
                                    />
                                  </Button>
                                </div>
                                <CardDescription
                                  className={cn(
                                    "whitespace-pre-wrap break-words",
                                    !isExpanded && "line-clamp-2",
                                  )}
                                >
                                  {experience.when || t("summary.noContextYet")}
                                </CardDescription>
                              </CardHeader>
                              <Collapsible
                                open={isExpanded}
                                onOpenChange={(open) =>
                                  setExpandedEditExperienceValues((current) =>
                                    open
                                      ? current.includes(experienceValue)
                                        ? current
                                        : [...current, experienceValue]
                                      : current.filter(
                                          (v) => v !== experienceValue,
                                        ),
                                  )
                                }
                              >
                                <CollapsibleContent>
                                  <CardContent className="space-y-4 text-sm">
                                    <div className="space-y-1">
                                      <Label>{t("summary.fieldKey")}</Label>
                                      <Input
                                        value={experience.key}
                                        onChange={(e) =>
                                          updateDraft((current) => ({
                                            ...current,
                                            experiences:
                                              current.experiences.map(
                                                (item, idx) =>
                                                  idx === index
                                                    ? {
                                                        ...item,
                                                        key: e.target.value,
                                                      }
                                                    : item,
                                              ),
                                          }))
                                        }
                                      />
                                    </div>
                                    <div className="space-y-1">
                                      <Label>{t("summary.fieldWhen")}</Label>
                                      <Textarea
                                        rows={3}
                                        value={experience.when}
                                        onChange={(e) =>
                                          updateDraft((current) => ({
                                            ...current,
                                            experiences:
                                              current.experiences.map(
                                                (item, idx) =>
                                                  idx === index
                                                    ? {
                                                        ...item,
                                                        when: e.target.value,
                                                      }
                                                    : item,
                                              ),
                                          }))
                                        }
                                      />
                                    </div>
                                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                                      <div className="space-y-1">
                                        <Label>
                                          {t("summary.fieldKeywords")}
                                        </Label>
                                        <Input
                                          value={toCsv(experience.keywords)}
                                          onChange={(e) =>
                                            updateDraft((current) => ({
                                              ...current,
                                              experiences:
                                                current.experiences.map(
                                                  (item, idx) => {
                                                    if (idx !== index)
                                                      return item;
                                                    const values = parseCsv(
                                                      e.target.value,
                                                    );
                                                    return {
                                                      ...item,
                                                      keywords: values.length
                                                        ? values
                                                        : null,
                                                    };
                                                  },
                                                ),
                                            }))
                                          }
                                        />
                                      </div>
                                      <div className="space-y-1">
                                        <Label>
                                          {t("summary.fieldRelatedErrorTypes")}
                                        </Label>
                                        <Input
                                          value={toCsv(
                                            experience.relatedErrorTypes,
                                          )}
                                          onChange={(e) =>
                                            updateDraft((current) => ({
                                              ...current,
                                              experiences:
                                                current.experiences.map(
                                                  (item, idx) => {
                                                    if (idx !== index)
                                                      return item;
                                                    const values = parseCsv(
                                                      e.target.value,
                                                    );
                                                    return {
                                                      ...item,
                                                      relatedErrorTypes:
                                                        values.length
                                                          ? values
                                                          : null,
                                                    };
                                                  },
                                                ),
                                            }))
                                          }
                                        />
                                      </div>
                                    </div>

                                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                                      <div>
                                        <div className="text-xs font-medium text-muted-foreground">
                                          {t("summary.possibleProblems")}
                                        </div>
                                        <Textarea
                                          className="mt-2"
                                          rows={5}
                                          value={toMultiline(
                                            experience.possibleProblems,
                                          )}
                                          onChange={(e) =>
                                            updateDraft((current) => ({
                                              ...current,
                                              experiences:
                                                current.experiences.map(
                                                  (item, idx) =>
                                                    idx === index
                                                      ? {
                                                          ...item,
                                                          possibleProblems:
                                                            parseMultiline(
                                                              e.target.value,
                                                            ),
                                                        }
                                                      : item,
                                                ),
                                            }))
                                          }
                                        />
                                      </div>
                                      <div>
                                        <div className="text-xs font-medium text-muted-foreground">
                                          {t("summary.avoidanceAndNotes")}
                                        </div>
                                        <Textarea
                                          className="mt-2"
                                          rows={5}
                                          value={toMultiline(
                                            experience.avoidanceAndNotes,
                                          )}
                                          onChange={(e) =>
                                            updateDraft((current) => ({
                                              ...current,
                                              experiences:
                                                current.experiences.map(
                                                  (item, idx) =>
                                                    idx === index
                                                      ? {
                                                          ...item,
                                                          avoidanceAndNotes:
                                                            parseMultiline(
                                                              e.target.value,
                                                            ),
                                                        }
                                                      : item,
                                                ),
                                            }))
                                          }
                                        />
                                      </div>
                                    </div>

                                    <div>
                                      <div className="text-xs font-medium text-muted-foreground">
                                        {t("summary.promptAdditions")}
                                      </div>
                                      <Textarea
                                        className="mt-2"
                                        rows={5}
                                        value={toMultiline(
                                          experience.promptAdditions,
                                        )}
                                        onChange={(e) =>
                                          updateDraft((current) => ({
                                            ...current,
                                            experiences:
                                              current.experiences.map(
                                                (item, idx) =>
                                                  idx === index
                                                    ? {
                                                        ...item,
                                                        promptAdditions:
                                                          parseMultiline(
                                                            e.target.value,
                                                          ),
                                                      }
                                                    : item,
                                              ),
                                          }))
                                        }
                                      />
                                    </div>

                                    <div className="flex justify-end">
                                      <Button
                                        variant="ghost"
                                        size="sm"
                                        onClick={() => {
                                          updateDraft((current) => ({
                                            ...current,
                                            experiences:
                                              current.experiences.filter(
                                                (_, idx) => idx !== index,
                                              ),
                                          }));
                                          setExpandedEditExperienceValues(
                                            (current) =>
                                              current.filter(
                                                (v) => v !== experienceValue,
                                              ),
                                          );
                                        }}
                                      >
                                        {t("summary.removeExperience")}
                                      </Button>
                                    </div>
                                  </CardContent>
                                </CollapsibleContent>
                              </Collapsible>
                              {!isExpanded ? (
                                <div className="px-6 pb-4 text-xs text-muted-foreground">
                                  {experience.possibleProblems.length}{" "}
                                  {t("summary.problems")},{" "}
                                  {experience.avoidanceAndNotes.length}{" "}
                                  {t("summary.notes")},{" "}
                                  {experience.promptAdditions.length}{" "}
                                  {t("summary.promptAdditionsCount")}
                                </div>
                              ) : null}
                            </Card>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="rounded-md border bg-background p-3 text-sm text-muted-foreground">
                    {t("summary.noEditablePayload")}
                  </div>
                )
              ) : view === "formatted" ? (
                <ExperienceSummaryView
                  summary={summary}
                  onRequestEdit={enterEditing}
                  expandedExperienceKeys={expandedFormattedExperienceKeys}
                  onExpandedExperienceKeysChange={
                    setExpandedFormattedExperienceKeys
                  }
                />
              ) : view === "json" ? (
                <JSONView json={summary} hideTitle scrollable borderless />
              ) : (
                <CodeView
                  content={JSON.stringify(summary, null, 2)}
                  scrollable
                />
              )}
            </div>
          </div>
        ) : (
          <div className="rounded-md border bg-background p-3 text-sm text-muted-foreground">
            {t("summary.noSummaryYet")}
          </div>
        )}
      </div>
      <AlertDialog
        open={isUnsavedPromptOpen}
        onOpenChange={(open) => {
          if (isUnsavedPromptBusy) return;
          if (!open) {
            handleUnsavedCancel();
          } else {
            setIsUnsavedPromptOpen(true);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("summary.unsavedChangesTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {unsavedPromptDescription}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button
              variant="outline"
              disabled={isUnsavedPromptBusy}
              onClick={handleUnsavedCancel}
            >
              {t("summary.cancel")}
            </Button>
            <Button
              variant="outline"
              disabled={isUnsavedPromptBusy}
              onClick={handleUnsavedDiscardAndContinue}
            >
              {t("summary.discard")}
            </Button>
            <Button
              variant="secondary"
              loading={isUnsavedPromptBusy && updateSummary.isPending}
              disabled={isUnsavedPromptBusy}
              onClick={() => {
                void handleUnsavedSaveAndContinue();
              }}
            >
              {t("summary.save")}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Page>
  );
}
