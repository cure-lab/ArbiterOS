import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/router";
import { LLMAdapter, type FilterState } from "@langfuse/shared";
import Page from "@/src/components/layouts/page";
import { api } from "@/src/utils/api";
import { toast } from "sonner";
import { Input } from "@/src/components/ui/input";
import { Label } from "@/src/components/ui/label";
import { Button } from "@/src/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/src/components/ui/alert";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/src/components/ui/card";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import DiffViewer from "@/src/components/DiffViewer";
import { useV4Beta } from "@/src/features/events/hooks/useV4Beta";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/src/components/ui/alert-dialog";
import { PolicyGuideCard } from "@/src/features/policy-governance/components/PolicyGuideCard";
import { PeekViewObservationDetail } from "@/src/components/table/peek/peek-observation-detail";
import {
  TablePeekView,
  type DataTablePeekViewProps,
} from "@/src/components/table/peek";
import { usePeekNavigation } from "@/src/components/table/peek/hooks/usePeekNavigation";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";

type PolicyRegistryEntry = {
  name: string;
  enabled: boolean;
  description: string;
};

type LoadedPolicyData = {
  configuredPath: string | null;
  resolvedPathInput: string;
  policyJsonPath: string;
  policyRegistryPath: string;
  policySourceFingerprint: string;
  sourceLastModifiedAt: string;
  policyJson: Record<string, unknown>;
  policyRegistryJson: PolicyRegistryEntry[];
  policyCards: Array<{
    name: string;
    description: string;
    enabled: boolean;
    settingSections: string[];
    settingsBySection: Record<string, unknown>;
  }>;
  policySectionMap: Record<string, string[]>;
};

type PendingProposal = {
  policyName: string;
  summary: string;
  proposedPolicyJson: Record<string, unknown>;
  proposedPolicyRegistryJson: PolicyRegistryEntry[];
};

type PolicyStatsRow = {
  policyName: string;
  totalCount: number;
  acceptedCount: number;
  rejectedCount: number;
  acceptedRate: number;
  rejectedRate: number;
};

type PolicyGuideInsight = {
  policyName: string;
  recentViolationCount: number;
  exampleBlockedAction: string | null;
  examplePrompt: string | null;
  exampleViolation: {
    observationName: string | null;
    statusMessage: string | null;
    input: string | null;
    output: string | null;
    inactivateErrorType: string | null;
    policyNames: string[];
  } | null;
  similarCases: Array<{
    traceId: string;
    traceName: string | null;
    traceTimestamp: string | null;
    turnIndex: number | null;
    targetObservationId: string | null;
    blockedAction: string | null;
    examplePrompt: string | null;
    violationExample: {
      observationName: string | null;
      statusMessage: string | null;
      input: string | null;
      output: string | null;
      inactivateErrorType: string | null;
      policyNames: string[];
    } | null;
  }>;
};

type PendingUnsavedAction = {
  type: "route_change";
  url: string;
};

const AUTO_REFRESH_INTERVAL_MS = 5000;

function stableStringify(value: unknown) {
  return JSON.stringify(value, null, 2);
}

function getPathnameFromUrl(url: string) {
  if (typeof window === "undefined") {
    return url.split("?")[0] ?? url;
  }

  return new URL(url, window.location.origin).pathname;
}

function buildSectionDrafts(params: {
  policyRegistryJson: PolicyRegistryEntry[];
  policyJson: Record<string, unknown>;
  policySectionMap: Record<string, string[]>;
}) {
  const { policyRegistryJson, policyJson, policySectionMap } = params;
  return Object.fromEntries(
    policyRegistryJson.map((entry) => [
      entry.name,
      Object.fromEntries(
        (policySectionMap[entry.name] ?? []).map((section) => [
          section,
          stableStringify(policyJson[section] ?? null),
        ]),
      ),
    ]),
  ) as Record<string, Record<string, string>>;
}

function buildPolicySectionPreview(params: {
  policyJson: Record<string, unknown>;
  policyName: string;
  policySectionMap: Record<string, string[]>;
}) {
  const sections = params.policySectionMap[params.policyName] ?? [];
  return {
    sections,
    preview: Object.fromEntries(
      sections.map((section) => [section, params.policyJson[section] ?? null]),
    ) as Record<string, unknown>,
  };
}

export default function PolicyGovernancePage() {
  const { t } = useLanguage();
  const router = useRouter();
  const projectId = router.query.projectId as string | undefined;
  const { isBetaEnabled } = useV4Beta();
  const metricsVersion = isBetaEnabled ? "v2" : "v1";
  const utils = api.useUtils();
  const [pathInput, setPathInput] = useState("");
  const [isPathInputHydrated, setIsPathInputHydrated] = useState(false);
  const [loaded, setLoaded] = useState<LoadedPolicyData | null>(null);
  const [policyJsonDraft, setPolicyJsonDraft] = useState<
    Record<string, unknown>
  >({});
  const [policyRegistryDraft, setPolicyRegistryDraft] = useState<
    PolicyRegistryEntry[]
  >([]);
  const [sectionDrafts, setSectionDrafts] = useState<
    Record<string, Record<string, string>>
  >({});
  const [sectionErrors, setSectionErrors] = useState<
    Record<string, Record<string, string | null>>
  >({});
  const [pendingProposal, setPendingProposal] =
    useState<PendingProposal | null>(null);
  const [proposalBasePolicyJson, setProposalBasePolicyJson] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [proposalPolicyNameLoading, setProposalPolicyNameLoading] = useState<
    string | null
  >(null);
  const [pendingUnsavedAction, setPendingUnsavedAction] =
    useState<PendingUnsavedAction | null>(null);
  const [isUnsavedPromptOpen, setIsUnsavedPromptOpen] = useState(false);
  const [isUnsavedPromptBusy, setIsUnsavedPromptBusy] = useState(false);
  const [sourceChangedWhileEditing, setSourceChangedWhileEditing] =
    useState(false);
  const allowNextRouteChangeRef = useRef(false);
  const autoLoadedPathRef = useRef<string | null>(null);
  const hasUpdateAccess = useHasProjectAccess({
    projectId: projectId ?? "",
    scope: "project:update",
  });
  const hasLLMConnectionAccess = useHasProjectAccess({
    projectId: projectId ?? "",
    scope: "llmApiKeys:read",
  });
  const statsTimeRange = useMemo(() => {
    const toTimestamp = new Date();
    const fromTimestamp = new Date(
      toTimestamp.getTime() - 30 * 24 * 60 * 60 * 1000,
    );
    return { fromTimestamp, toTimestamp };
  }, []);

  const policySettingsQuery = api.projects.getPolicyGovernanceSettings.useQuery(
    { projectId: projectId ?? "" },
    {
      enabled: Boolean(projectId),
      refetchOnWindowFocus: false,
    },
  );
  const errorAnalysisSettingsQuery =
    api.projects.getErrorAnalysisSettings.useQuery(
      { projectId: projectId ?? "" },
      {
        enabled: Boolean(projectId),
        refetchOnWindowFocus: false,
      },
    );
  const policyConfirmationStatsQuery =
    api.dashboard.policyConfirmationStats.useQuery(
      {
        projectId: projectId ?? "",
        globalFilterState: [] as FilterState,
        fromTimestamp: statsTimeRange.fromTimestamp,
        toTimestamp: statsTimeRange.toTimestamp,
        version: metricsVersion,
      },
      {
        enabled: Boolean(projectId),
        trpc: {
          context: {
            skipBatch: true,
          },
        },
      },
    );
  const llmConnectionsQuery = api.llmApiKey.all.useQuery(
    { projectId: projectId ?? "" },
    {
      enabled: Boolean(projectId) && hasLLMConnectionAccess,
      refetchOnWindowFocus: false,
    },
  );

  useEffect(() => {
    if (!policySettingsQuery.data) return;

    setPathInput(policySettingsQuery.data.kernelPolicyPathAbsolute ?? "");
    setIsPathInputHydrated(true);
  }, [policySettingsQuery.data]);

  const savePolicySettingsMutation =
    api.projects.setPolicyGovernanceSettings.useMutation({
      onSuccess: async (saved) => {
        setPathInput(saved.kernelPolicyPathAbsolute ?? "");
        if (projectId && saved.kernelPolicyPathAbsolute) {
          const savedPath = saved.kernelPolicyPathAbsolute.trim();
          if (savedPath.length > 0) {
            autoLoadedPathRef.current = savedPath;
            loadPolicyFilesMutation.mutate({
              projectId,
              pathOverride: savedPath,
            });
          }
        }
        await utils.projects.getPolicyGovernanceSettings.invalidate({
          projectId: projectId ?? "",
        });
        toast.success(t("policy.pathSaved"));
      },
      onError: (error) => toast.error(error.message),
    });

  const applyLoadedPolicyData = useCallback((data: LoadedPolicyData) => {
    setLoaded(data);
    setPolicyJsonDraft(data.policyJson);
    setPolicyRegistryDraft(data.policyRegistryJson);
    setSectionDrafts(
      buildSectionDrafts({
        policyRegistryJson: data.policyRegistryJson,
        policyJson: data.policyJson,
        policySectionMap: data.policySectionMap,
      }),
    );
    setSectionErrors({});
    setSourceChangedWhileEditing(false);
  }, []);

  const loadPolicyFilesMutation =
    api.policyGovernance.loadPolicyFiles.useMutation({
      onSuccess: (data) => {
        applyLoadedPolicyData(data as LoadedPolicyData);
      },
      onError: (error) => toast.error(error.message),
    });
  const loadPolicyFiles = loadPolicyFilesMutation.mutate;
  const isLoadPolicyFilesPending = loadPolicyFilesMutation.isPending;

  const savePolicyFilesMutation =
    api.policyGovernance.savePolicyFiles.useMutation({
      onSuccess: async (data) => {
        applyLoadedPolicyData(data as LoadedPolicyData);
        await utils.projects.getPolicyGovernanceSettings.invalidate({
          projectId: projectId ?? "",
        });
        toast.success(t("policy.filesSaved"));
      },
      onError: (error) => toast.error(error.message),
    });
  const resetPolicyConfirmationStatsMutation =
    api.projects.resetPolicyConfirmationStats.useMutation({
      onSuccess: async () => {
        await Promise.all([
          utils.projects.getPolicyGovernanceSettings.invalidate({
            projectId: projectId ?? "",
          }),
          utils.dashboard.policyConfirmationStats.invalidate(),
          utils.dashboard.policyConfirmationDetails.invalidate(),
        ]);
        toast.success(t("policy.confirmationStatsReset"));
      },
      onError: (error) => toast.error(error.message),
    });

  useEffect(() => {
    if (!projectId) return;
    if (!isPathInputHydrated) return;
    const savedPath =
      policySettingsQuery.data?.kernelPolicyPathAbsolute?.trim() ?? "";
    if (!savedPath) return;
    if (autoLoadedPathRef.current === savedPath) return;
    if (isLoadPolicyFilesPending) return;

    autoLoadedPathRef.current = savedPath;
    loadPolicyFiles({
      projectId,
      pathOverride: savedPath,
    });
  }, [
    isPathInputHydrated,
    isLoadPolicyFilesPending,
    loadPolicyFiles,
    policySettingsQuery.data?.kernelPolicyPathAbsolute,
    projectId,
  ]);

  const reloadLoadedPolicyFiles = useCallback(() => {
    if (!projectId || !loaded) return;
    loadPolicyFilesMutation.mutate({
      projectId,
      pathOverride: loaded.resolvedPathInput,
    });
  }, [loadPolicyFilesMutation, loaded, projectId]);

  const suggestionMutation = api.policySuggestions.generate.useMutation();
  const proposalMutation =
    api.policyGovernance.generatePolicyUpdateProposal.useMutation();

  const hasSectionErrors = useMemo(
    () =>
      Object.values(sectionErrors).some((policyErrors) =>
        Object.values(policyErrors).some((error) => Boolean(error)),
      ),
    [sectionErrors],
  );

  const sectionMap = loaded?.policySectionMap ?? {};
  const highlightThresholdPct =
    errorAnalysisSettingsQuery.data?.policyRejectHighlightThresholdPct ?? 70;
  const policyStatsMap = useMemo(
    () =>
      new Map(
        (
          ((policyConfirmationStatsQuery.data as
            | PolicyStatsRow[]
            | undefined) ?? []) as PolicyStatsRow[]
        ).map((row) => [row.policyName, row]),
      ),
    [policyConfirmationStatsQuery.data],
  );
  const policyGuideInsightsQuery =
    api.policyGovernance.getPolicyGuideInsights.useQuery(
      {
        projectId: projectId ?? "",
        policyNames: policyRegistryDraft.map((entry) => entry.name),
        globalFilterState: [] as FilterState,
        fromTimestamp: statsTimeRange.fromTimestamp,
        toTimestamp: statsTimeRange.toTimestamp,
        version: metricsVersion,
      },
      {
        enabled: Boolean(projectId) && policyRegistryDraft.length > 0,
        trpc: {
          context: {
            skipBatch: true,
          },
        },
      },
    );
  const policyGuideInsightsMap = useMemo(
    () =>
      new Map(
        (
          ((policyGuideInsightsQuery.data as
            | PolicyGuideInsight[]
            | undefined) ?? []) as PolicyGuideInsight[]
        ).map((row) => [row.policyName, row]),
      ),
    [policyGuideInsightsQuery.data],
  );
  const policyFilesStatusQuery =
    api.policyGovernance.getPolicyFilesStatus.useQuery(
      {
        projectId: projectId ?? "",
        pathOverride: loaded?.resolvedPathInput,
      },
      {
        enabled: Boolean(projectId) && Boolean(loaded),
        refetchInterval: AUTO_REFRESH_INTERVAL_MS,
        refetchOnWindowFocus: false,
        trpc: {
          context: {
            skipBatch: true,
          },
        },
      },
    );

  const peekNavigationProps = usePeekNavigation({
    queryParams: ["observation", "display", "timestamp", "traceId"],
    paramsToMirrorPeekValue: ["observation"],
    extractParamsValuesFromRow: (row: {
      traceId: string;
      timestamp?: string | null;
    }) => ({
      traceId: row.traceId,
      ...(row.timestamp ? { timestamp: row.timestamp } : {}),
    }),
    expandConfig: {
      basePath: `/project/${projectId ?? ""}/traces`,
      pathParam: "traceId",
    },
  });
  const peekConfig: DataTablePeekViewProps | undefined = useMemo(
    () =>
      projectId
        ? {
            itemType: "TRACE",
            customTitlePrefix: t("policy.observationIdPrefix"),
            children: <PeekViewObservationDetail projectId={projectId} />,
            ...peekNavigationProps,
          }
        : undefined,
    [peekNavigationProps, projectId, t],
  );
  const activeOpenAiConnection = useMemo(
    () =>
      llmConnectionsQuery.data?.data.find(
        (connection) => connection.adapter === LLMAdapter.OpenAI,
      ) ?? null,
    [llmConnectionsQuery.data],
  );
  const canSaveDraft =
    Boolean(loaded) &&
    hasUpdateAccess &&
    !hasSectionErrors &&
    !savePolicyFilesMutation.isPending;
  const hasUnsavedDraftChanges = useMemo(() => {
    if (!loaded) return false;
    return (
      stableStringify(policyJsonDraft) !== stableStringify(loaded.policyJson) ||
      stableStringify(policyRegistryDraft) !==
        stableStringify(loaded.policyRegistryJson)
    );
  }, [loaded, policyJsonDraft, policyRegistryDraft]);
  const hasUnsavedPathChanges =
    isPathInputHydrated &&
    pathInput.trim() !==
      (policySettingsQuery.data?.kernelPolicyPathAbsolute ?? "").trim();
  const hasUnsavedChanges = hasUnsavedDraftChanges || hasUnsavedPathChanges;

  useEffect(() => {
    if (!loaded || !policyFilesStatusQuery.data) return;

    if (
      policyFilesStatusQuery.data.policySourceFingerprint ===
      loaded.policySourceFingerprint
    ) {
      setSourceChangedWhileEditing(false);
      return;
    }

    if (
      loadPolicyFilesMutation.isPending ||
      savePolicyFilesMutation.isPending
    ) {
      return;
    }

    if (hasUnsavedChanges) {
      setSourceChangedWhileEditing(true);
      return;
    }

    setSourceChangedWhileEditing(false);
    reloadLoadedPolicyFiles();
  }, [
    hasUnsavedChanges,
    loadPolicyFilesMutation.isPending,
    loaded,
    policyFilesStatusQuery.data,
    reloadLoadedPolicyFiles,
    savePolicyFilesMutation.isPending,
  ]);

  const loadPathHint = t("policy.pathHint");

  const onLoadPolicyFiles = () => {
    if (!projectId) return;
    const trimmed = pathInput.trim();
    loadPolicyFilesMutation.mutate({
      projectId,
      pathOverride: trimmed.length > 0 ? trimmed : undefined,
    });
  };

  const onSavePolicyFiles = () => {
    if (!projectId || !loaded) return;
    const trimmed = pathInput.trim();
    savePolicyFilesMutation.mutate({
      projectId,
      pathOverride: trimmed.length > 0 ? trimmed : undefined,
      policyJson: policyJsonDraft,
      policyRegistryJson: policyRegistryDraft,
    });
  };

  const promptForUnsavedChanges = useCallback(
    (action: PendingUnsavedAction) => {
      setPendingUnsavedAction(action);
      setIsUnsavedPromptOpen(true);
    },
    [],
  );

  const executePendingUnsavedAction = useCallback(
    (action: PendingUnsavedAction) => {
      if (action.type === "route_change") {
        allowNextRouteChangeRef.current = true;
        void router.push(action.url).catch(() => {
          allowNextRouteChangeRef.current = false;
        });
      }
    },
    [router],
  );

  const updateRegistryEntry = (
    policyName: string,
    updater: (entry: PolicyRegistryEntry) => PolicyRegistryEntry,
  ) => {
    setPolicyRegistryDraft((prev) =>
      prev.map((entry) => (entry.name === policyName ? updater(entry) : entry)),
    );
  };

  const updateSectionDraft = (params: {
    policyName: string;
    section: string;
    value: string;
  }) => {
    setSectionDrafts((prev) => ({
      ...prev,
      [params.policyName]: {
        ...(prev[params.policyName] ?? {}),
        [params.section]: params.value,
      },
    }));

    try {
      const parsed = JSON.parse(params.value);
      setPolicyJsonDraft((prev) => ({
        ...prev,
        [params.section]: parsed,
      }));
      setSectionErrors((prev) => ({
        ...prev,
        [params.policyName]: {
          ...(prev[params.policyName] ?? {}),
          [params.section]: null,
        },
      }));
    } catch (error) {
      setSectionErrors((prev) => ({
        ...prev,
        [params.policyName]: {
          ...(prev[params.policyName] ?? {}),
          [params.section]:
            error instanceof Error ? error.message : t("policy.invalidJson"),
        },
      }));
    }
  };

  const onGenerateProposal = async (policyName: string) => {
    if (!projectId || !loaded) return;
    if (hasSectionErrors) {
      toast.error(t("policy.fixInvalidBeforeGenerate"));
      return;
    }

    setProposalPolicyNameLoading(policyName);
    try {
      const now = new Date();
      const from = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
      const suggestion = await suggestionMutation.mutateAsync({
        projectId,
        policyName,
        globalFilterState: [] as FilterState,
        fromTimestamp: from,
        toTimestamp: now,
        version: metricsVersion,
      });
      const proposal = await proposalMutation.mutateAsync({
        projectId,
        policyName,
        policyJson: policyJsonDraft,
        policyRegistryJson: policyRegistryDraft,
        suggestion: suggestion.suggestion,
      });
      setProposalBasePolicyJson(policyJsonDraft);
      setPendingProposal({
        policyName: proposal.policyName,
        summary: proposal.summary,
        proposedPolicyJson: proposal.proposedPolicyJson,
        proposedPolicyRegistryJson: proposal.proposedPolicyRegistryJson,
      });
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : t("policy.generateFailed"),
      );
    } finally {
      setProposalPolicyNameLoading(null);
    }
  };

  const applyProposalToDraft = () => {
    if (!pendingProposal || !loaded) return;
    const nextRegistry = pendingProposal.proposedPolicyRegistryJson;
    const nextPolicy = pendingProposal.proposedPolicyJson;
    setPolicyRegistryDraft(nextRegistry);
    setPolicyJsonDraft(nextPolicy);
    setSectionDrafts(
      buildSectionDrafts({
        policyRegistryJson: nextRegistry,
        policyJson: nextPolicy,
        policySectionMap: loaded.policySectionMap,
      }),
    );
    setSectionErrors({});
    setPendingProposal(null);
    setProposalBasePolicyJson(null);
    toast.success(t("policy.proposalAppliedToDraft"));
  };

  const proposalDiffPreview = useMemo(() => {
    if (!pendingProposal || !proposalBasePolicyJson || !loaded) return null;

    const currentPreview = buildPolicySectionPreview({
      policyJson: proposalBasePolicyJson,
      policyName: pendingProposal.policyName,
      policySectionMap: loaded.policySectionMap,
    });
    const proposedPreview = buildPolicySectionPreview({
      policyJson: pendingProposal.proposedPolicyJson,
      policyName: pendingProposal.policyName,
      policySectionMap: loaded.policySectionMap,
    });

    return {
      sections: currentPreview.sections,
      currentPolicyJson: currentPreview.preview,
      proposedPolicyJson: proposedPreview.preview,
    };
  }, [loaded, pendingProposal, proposalBasePolicyJson]);
  const proposalHasChanges = useMemo(() => {
    if (!proposalDiffPreview) return false;

    return (
      stableStringify(proposalDiffPreview.currentPolicyJson) !==
      stableStringify(proposalDiffPreview.proposedPolicyJson)
    );
  }, [proposalDiffPreview]);

  const handleUnsavedSaveAndContinue = useCallback(async () => {
    if (!pendingUnsavedAction) return;
    setIsUnsavedPromptBusy(true);
    try {
      if (hasUnsavedPathChanges && projectId) {
        await savePolicySettingsMutation.mutateAsync({
          projectId,
          kernelPolicyPathAbsolute:
            pathInput.trim().length > 0 ? pathInput.trim() : null,
        });
      }

      if (hasUnsavedDraftChanges) {
        if (hasSectionErrors) {
          toast.error(t("policy.fixInvalidBeforeSave"));
          setIsUnsavedPromptBusy(false);
          return;
        }
        if (!projectId || !loaded) {
          setIsUnsavedPromptBusy(false);
          return;
        }
        await savePolicyFilesMutation.mutateAsync({
          projectId,
          pathOverride:
            pathInput.trim().length > 0 ? pathInput.trim() : undefined,
          policyJson: policyJsonDraft,
          policyRegistryJson: policyRegistryDraft,
        });
      }
    } catch {
      setIsUnsavedPromptBusy(false);
      return;
    }

    const action = pendingUnsavedAction;
    setPendingUnsavedAction(null);
    setIsUnsavedPromptOpen(false);
    setIsUnsavedPromptBusy(false);
    executePendingUnsavedAction(action);
  }, [
    pendingUnsavedAction,
    hasUnsavedPathChanges,
    projectId,
    savePolicySettingsMutation,
    pathInput,
    hasUnsavedDraftChanges,
    hasSectionErrors,
    loaded,
    savePolicyFilesMutation,
    policyJsonDraft,
    policyRegistryDraft,
    executePendingUnsavedAction,
    t,
  ]);

  const handleUnsavedDiscardAndContinue = useCallback(() => {
    if (!pendingUnsavedAction) return;
    setIsUnsavedPromptBusy(true);
    const action = pendingUnsavedAction;
    setPendingUnsavedAction(null);
    setIsUnsavedPromptOpen(false);
    setIsUnsavedPromptBusy(false);
    executePendingUnsavedAction(action);
  }, [executePendingUnsavedAction, pendingUnsavedAction]);

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
      if (getPathnameFromUrl(url) === getPathnameFromUrl(router.asPath)) {
        return;
      }

      const cancellationMessage = t("policy.routeChangeAborted");
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

  const llmConnectionLabel = activeOpenAiConnection
    ? activeOpenAiConnection.baseURL
      ? `${activeOpenAiConnection.provider} via ${activeOpenAiConnection.baseURL}`
      : `${activeOpenAiConnection.provider} via default endpoint`
    : null;
  const configuredModelLabel = errorAnalysisSettingsQuery.data?.model ?? null;
  const beginnerSummaries = policySettingsQuery.data?.beginnerSummaries ?? {};
  const policyConfirmationResetTimestamps =
    policySettingsQuery.data?.policyConfirmationResetTimestamps ?? {};
  const sourceLastModifiedLabel =
    policyFilesStatusQuery.data?.sourceLastModifiedAt ??
    loaded?.sourceLastModifiedAt ??
    null;

  return (
    <Page
      headerProps={{
        title: t("policy.pageTitle"),
      }}
      scrollable
    >
      <div className="space-y-4 p-3">
        <Card>
          <CardHeader>
            <CardTitle>{t("policy.sourceAndContextTitle")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="kernel-policy-path">
                {t("policy.pathLabel")}
              </Label>
              <Input
                id="kernel-policy-path"
                placeholder={loadPathHint}
                value={pathInput}
                onChange={(e) => setPathInput(e.target.value)}
                disabled={
                  !hasUpdateAccess || savePolicySettingsMutation.isPending
                }
              />
              <p className="text-xs text-muted-foreground">
                {t("policy.pathHintDescription")}
              </p>
              <p className="text-xs text-muted-foreground">
                {t("policy.productionHint")}
              </p>
              {policySettingsQuery.data?.kernelPolicyPathAbsolute ? (
                <p className="text-xs text-muted-foreground">
                  {t("policy.savedPathDetected")}
                </p>
              ) : null}
              {loaded ? (
                <p className="text-xs text-muted-foreground">
                  {t("policy.autoRefreshInfo")}
                </p>
              ) : null}
            </div>
            <div className="grid gap-3 md:grid-cols-4">
              <div className="rounded-lg border bg-muted/20 p-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("policy.llmConnection")}
                </div>
                <div className="mt-1 text-sm text-foreground">
                  {hasLLMConnectionAccess
                    ? (llmConnectionLabel ?? t("policy.noLlmConnection"))
                    : t("policy.requiresLlmReadAccess")}
                </div>
              </div>
              <div className="rounded-lg border bg-muted/20 p-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("policy.errorAnalysisModel")}
                </div>
                <div className="mt-1 text-sm text-foreground">
                  {configuredModelLabel ?? t("policy.notConfigured")}
                </div>
              </div>
              <div className="rounded-lg border bg-muted/20 p-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("policy.lastPolicyUpdate")}
                </div>
                <div className="mt-1 text-sm text-foreground">
                  {policySettingsQuery.data?.lastPolicyUpdatedAt
                    ? new Date(
                        policySettingsQuery.data.lastPolicyUpdatedAt,
                      ).toLocaleString()
                    : t("policy.noSavedUpdateTimestamp")}
                </div>
              </div>
              <div className="rounded-lg border bg-muted/20 p-3">
                <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {t("policy.kernelSourceUpdated")}
                </div>
                <div className="mt-1 text-sm text-foreground">
                  {sourceLastModifiedLabel
                    ? new Date(sourceLastModifiedLabel).toLocaleString()
                    : t("policy.loadPolicyFilesToInspect")}
                </div>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                onClick={() => {
                  if (!projectId) return;
                  savePolicySettingsMutation.mutate({
                    projectId,
                    kernelPolicyPathAbsolute:
                      pathInput.trim().length > 0 ? pathInput.trim() : null,
                  });
                }}
                disabled={
                  !hasUpdateAccess || savePolicySettingsMutation.isPending
                }
              >
                {t("policy.savePath")}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={onLoadPolicyFiles}
                disabled={!projectId || isLoadPolicyFilesPending}
              >
                {isLoadPolicyFilesPending
                  ? t("policy.loading")
                  : t("policy.loadPolicyFiles")}
              </Button>
              <Button
                type="button"
                variant="secondary"
                onClick={onSavePolicyFiles}
                disabled={!canSaveDraft}
              >
                {savePolicyFilesMutation.isPending
                  ? t("policy.saving")
                  : t("policy.savePolicyFiles")}
              </Button>
            </div>
            {loaded ? (
              <div className="rounded border bg-muted/30 p-2 text-xs text-muted-foreground">
                <div>
                  {t("policy.resolvedInput")}: {loaded.resolvedPathInput}
                </div>
                <div>policy.json: {loaded.policyJsonPath}</div>
                <div>policy_registry.json: {loaded.policyRegistryPath}</div>
              </div>
            ) : null}
          </CardContent>
        </Card>

        {loaded ? (
          <div className="space-y-4">
            {sourceChangedWhileEditing ? (
              <Alert>
                <AlertTitle>{t("policy.kernelFilesChangedTitle")}</AlertTitle>
                <AlertDescription className="space-y-3">
                  <p>{t("policy.kernelFilesChangedDescription")}</p>
                  <p>{t("policy.reloadingReplacesDraft")}</p>
                  <div>
                    <Button
                      type="button"
                      variant="secondary"
                      onClick={reloadLoadedPolicyFiles}
                      disabled={isLoadPolicyFilesPending}
                    >
                      {isLoadPolicyFilesPending
                        ? t("policy.reloading")
                        : t("policy.reloadFromKernel")}
                    </Button>
                  </div>
                </AlertDescription>
              </Alert>
            ) : null}
            {policyRegistryDraft.map((entry) => {
              const sections = sectionMap[entry.name] ?? [];
              const settingsBySection = Object.fromEntries(
                sections.map((section) => [
                  section,
                  policyJsonDraft[section] ?? null,
                ]),
              );

              return (
                <PolicyGuideCard
                  key={entry.name}
                  projectId={projectId ?? ""}
                  entry={entry}
                  initialBeginnerSummary={beginnerSummaries[entry.name] ?? null}
                  sections={sections}
                  settingsBySection={settingsBySection}
                  highlightThresholdPct={highlightThresholdPct}
                  confirmationStats={policyStatsMap.get(entry.name)}
                  lastConfirmationStatsResetAt={
                    policyConfirmationResetTimestamps[entry.name] ?? null
                  }
                  guideInsight={policyGuideInsightsMap.get(entry.name)}
                  hasUpdateAccess={hasUpdateAccess}
                  sectionDrafts={sectionDrafts[entry.name] ?? {}}
                  sectionErrors={sectionErrors[entry.name] ?? {}}
                  onOpenTracePeek={(policyCase) => {
                    if (!policyCase.targetObservationId) return;
                    peekNavigationProps.openPeek(
                      policyCase.targetObservationId,
                      {
                        traceId: policyCase.traceId,
                        timestamp: policyCase.traceTimestamp,
                      },
                    );
                  }}
                  onEnabledChange={(checked) =>
                    updateRegistryEntry(entry.name, (prev) => ({
                      ...prev,
                      enabled: checked,
                    }))
                  }
                  onDescriptionChange={(value) =>
                    updateRegistryEntry(entry.name, (prev) => ({
                      ...prev,
                      description: value,
                    }))
                  }
                  onSectionChange={(section, value) =>
                    updateSectionDraft({
                      policyName: entry.name,
                      section,
                      value,
                    })
                  }
                  onGenerateProposal={() => void onGenerateProposal(entry.name)}
                  isGeneratingProposal={
                    proposalPolicyNameLoading === entry.name
                  }
                  onResetConfirmationStats={() => {
                    if (!projectId) return;
                    resetPolicyConfirmationStatsMutation.mutate({
                      projectId,
                      policyNames: [entry.name],
                    });
                  }}
                  isResettingConfirmationStats={
                    resetPolicyConfirmationStatsMutation.isPending &&
                    resetPolicyConfirmationStatsMutation.variables?.policyNames.includes(
                      entry.name,
                    ) === true
                  }
                  hasSectionErrors={hasSectionErrors}
                />
              );
            })}
            <div className="flex items-center justify-end gap-2">
              <Button
                type="button"
                onClick={onSavePolicyFiles}
                disabled={!canSaveDraft}
              >
                {savePolicyFilesMutation.isPending
                  ? t("policy.saving")
                  : t("policy.savePolicyFiles")}
              </Button>
            </div>
          </div>
        ) : (
          <Card>
            <CardContent className="p-6 text-sm text-muted-foreground">
              {t("policy.emptyState")}
            </CardContent>
          </Card>
        )}
      </div>
      {peekConfig ? <TablePeekView peekView={peekConfig} /> : null}

      <AlertDialog
        open={Boolean(pendingProposal)}
        onOpenChange={(open) => {
          if (!open) {
            setPendingProposal(null);
            setProposalBasePolicyJson(null);
          }
        }}
      >
        <AlertDialogContent className="max-w-6xl">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("policy.proposalDialogTitle")} {pendingProposal?.policyName}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingProposal?.summary}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {proposalDiffPreview && proposalHasChanges ? (
            <div className="max-h-[70vh] space-y-3 overflow-y-auto">
              <p className="text-sm text-muted-foreground">
                {t("policy.showingSectionsFor")} {pendingProposal?.policyName}
                {proposalDiffPreview.sections.length > 0
                  ? `: ${proposalDiffPreview.sections.join(", ")}`
                  : "."}
              </p>
              <DiffViewer
                oldLabel={t("policy.diffCurrent")}
                newLabel={t("policy.diffProposal")}
                oldSubLabel={pendingProposal?.policyName}
                newSubLabel={pendingProposal?.policyName}
                oldString={stableStringify(
                  proposalDiffPreview.currentPolicyJson,
                )}
                newString={stableStringify(
                  proposalDiffPreview.proposedPolicyJson,
                )}
              />
            </div>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel>
              {proposalHasChanges ? t("policy.reject") : t("policy.cancel")}
            </AlertDialogCancel>
            {proposalHasChanges ? (
              <Button type="button" onClick={applyProposalToDraft}>
                {t("policy.applyToDraft")}
              </Button>
            ) : null}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={isUnsavedPromptOpen}
        onOpenChange={(open) => {
          if (!open) {
            handleUnsavedCancel();
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t("policy.unsavedChangesTitle")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t("policy.unsavedChangesLeave")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel
              onClick={handleUnsavedCancel}
              disabled={isUnsavedPromptBusy}
            >
              {t("policy.stay")}
            </AlertDialogCancel>
            <Button
              type="button"
              variant="destructive"
              onClick={handleUnsavedDiscardAndContinue}
              disabled={isUnsavedPromptBusy}
            >
              {t("policy.discardAndLeave")}
            </Button>
            <Button
              type="button"
              onClick={() => void handleUnsavedSaveAndContinue()}
              disabled={isUnsavedPromptBusy}
            >
              {t("policy.saveAndLeave")}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Page>
  );
}
