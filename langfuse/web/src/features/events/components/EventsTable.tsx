import { DataTable } from "@/src/components/table/data-table";
import { DataTableToolbar } from "@/src/components/table/data-table-toolbar";
import {
  DataTableControlsProvider,
  DataTableControls,
} from "@/src/components/table/data-table-controls";
import { ResizableFilterLayout } from "@/src/components/table/resizable-filter-layout";
import { useEffect, useMemo, useState, useRef, useCallback } from "react";
import { useQueryFilterState } from "@/src/features/filters/hooks/useFilterState";
import { usePaginationState } from "@/src/hooks/usePaginationState";
import { useSidebarFilterState } from "@/src/features/filters/hooks/useSidebarFilterState";
import {
  getEventsColumnName,
  observationEventsFilterConfig,
} from "../config/filter-config";
import { formatIntervalSeconds } from "@/src/utils/dates";
import { type LangfuseColumnDef } from "@/src/components/table/types";
import {
  type ObservationLevelType,
  type FilterState,
  BatchExportTableName,
  type ObservationType,
  TableViewPresetTableName,
  BatchActionType,
  ActionId,
  RESOURCE_LIMIT_ERROR_MESSAGE,
} from "@langfuse/shared";
import { cn } from "@/src/utils/tailwind";
import { LevelColors } from "@/src/components/level-colors";
import { numberFormatter, usdFormatter } from "@/src/utils/numbers";
import { useOrderByState } from "@/src/features/orderBy/hooks/useOrderByState";
import { useRowHeightLocalStorage } from "@/src/components/table/data-table-row-height-switch";
import { useTableDateRange } from "@/src/hooks/useTableDateRange";
import {
  toAbsoluteTimeRange,
  type TableDateRange,
} from "@/src/utils/date-range-utils";
import { type ScoreAggregate } from "@langfuse/shared";
import TagList from "@/src/features/tag/components/TagList";
import useColumnOrder from "@/src/features/column-visibility/hooks/useColumnOrder";
import { BatchExportTableButton } from "@/src/components/BatchExportTableButton";
import { BreakdownTooltip } from "@/src/components/trace2/components/_shared/BreakdownToolTip";
import {
  ArrowUpRight,
  Check,
  ChevronDown,
  InfoIcon,
  LightbulbIcon,
  PlusCircle,
} from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/src/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/src/components/ui/dropdown-menu";
import { UpsertModelFormDialog } from "@/src/features/models/components/UpsertModelFormDialog";
import { LocalIsoDate } from "@/src/components/LocalIsoDate";
import { Badge } from "@/src/components/ui/badge";
import { type Row, type RowSelectionState } from "@tanstack/react-table";
import TableIdOrName from "@/src/components/table/table-id";
import { ItemBadge } from "@/src/components/ItemBadge";
import { Skeleton } from "@/src/components/ui/skeleton";
import { PeekViewObservationDetail } from "@/src/components/table/peek/peek-observation-detail";
import { usePeekNavigation } from "@/src/components/table/peek/hooks/usePeekNavigation";
import { useDetailPageLists } from "@/src/features/navigate-detail-pages/context";
import { useTableViewManager } from "@/src/components/table/table-view-presets/hooks/useTableViewManager";
import { useRouter } from "next/router";
import { useFullTextSearch } from "@/src/components/table/use-cases/useFullTextSearch";
import { TableSelectionManager } from "@/src/features/table/components/TableSelectionManager";
import { useSelectAll } from "@/src/features/table/hooks/useSelectAll";
import { TableActionMenu } from "@/src/features/table/components/TableActionMenu";
import { type TableAction } from "@/src/features/table/types";
import {
  type DataTablePeekViewProps,
  TablePeekView,
} from "@/src/components/table/peek";
import { useScoreColumns } from "@/src/features/scores/hooks/useScoreColumns";
import { scoreFilters } from "@/src/features/scores/lib/scoreColumns";
import useColumnVisibility from "@/src/features/column-visibility/hooks/useColumnVisibility";
import { MemoizedIOTableCell } from "@/src/components/ui/IOTableCell";
import { useEventsTableData } from "@/src/features/events/hooks/useEventsTableData";
import { useEventsFilterOptions } from "@/src/features/events/hooks/useEventsFilterOptions";
import {
  useEventsViewMode,
  type EventsViewMode,
} from "@/src/features/events/hooks/useEventsViewMode";
import { EventsViewModeToggle } from "@/src/features/events/components/EventsViewModeToggle";
import { JsonSkeleton } from "@/src/components/ui/CodeJsonViewer";
import {
  type RefreshInterval,
  REFRESH_INTERVALS,
} from "@/src/components/table/data-table-refresh-button";
import useSessionStorage from "@/src/components/useSessionStorage";
import { api, directApi } from "@/src/utils/api";
import Link from "next/link";
import { Button } from "@/src/components/ui/button";
import { BulkErrorAnalysisButton } from "@/src/features/error-analysis/components/BulkErrorAnalysisButton";
import { RunEvaluationDialog } from "@/src/features/batch-actions/components/RunEvaluationDialog/index";
import { AddObservationsToDatasetDialog } from "@/src/features/batch-actions/components/AddObservationsToDatasetDialog/index";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";
import {
  UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL,
  UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL_EN,
  UNCLASSIFIED_ERROR_TYPE_FILTER_VALUE,
} from "@/src/features/error-analysis/types";
import { isSessionOutputTurnObservationName } from "@/src/features/governance/utils/policyMetadata";

export type EventsTableRow = {
  // Identity fields
  id: string;
  traceId?: string;
  spanId: string;
  parentSpanId?: string;

  // Time fields
  startTime: Date;
  endTime?: Date;
  completionStartTime?: Date;
  timestamp?: Date;

  // Core properties
  type: ObservationType;
  name?: string;
  environment?: string;
  version?: string;
  level?: ObservationLevelType;
  statusMessage?: string;

  // User context
  userId?: string;
  sessionId?: string;

  // Model fields
  providedModelName?: string;
  modelId?: string;
  modelParameters?: string;

  // Prompt fields
  promptId?: string;
  promptName?: string;
  promptVersion?: string;

  // Usage and cost
  usage: {
    inputUsage: number;
    outputUsage: number;
    totalUsage: number;
  };
  usageDetails: Record<string, number>;
  totalCost?: number;
  cost: {
    inputCost?: number;
    outputCost?: number;
  };
  costDetails: Record<string, number>;
  usagePricingTierName?: string | null;

  // Performance metrics
  latency?: number;
  timeToFirstToken?: number;

  // Tool fields
  toolDefinitions?: number;
  toolCalls?: number;

  input?: string;
  output?: string;
  metadata?: unknown;

  // Trace fields
  traceTags?: string[];
  traceName?: string;

  // Scores
  scores: ScoreAggregate;
};

function parsePolicyNamesFromMetadata(metadata: unknown): string[] {
  if (!metadata) return [];
  let metadataRecord: Record<string, unknown> | null = null;
  if (typeof metadata === "string") {
    try {
      const parsed = JSON.parse(metadata);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        metadataRecord = parsed as Record<string, unknown>;
      }
    } catch {
      return [];
    }
  } else if (typeof metadata === "object" && !Array.isArray(metadata)) {
    metadataRecord = metadata as Record<string, unknown>;
  }
  if (!metadataRecord) return [];

  const rawPolicyNames = metadataRecord.policy_names;
  if (Array.isArray(rawPolicyNames)) {
    return rawPolicyNames.filter(
      (item): item is string => typeof item === "string" && !!item.trim(),
    );
  }
  if (typeof rawPolicyNames === "string") {
    try {
      const parsed = JSON.parse(rawPolicyNames);
      if (Array.isArray(parsed)) {
        return parsed.filter(
          (item): item is string => typeof item === "string" && !!item.trim(),
        );
      }
    } catch {
      // no-op
    }
    const trimmed = rawPolicyNames.trim();
    return trimmed ? [trimmed] : [];
  }
  return [];
}

const UNCLASSIFIED_POLICY_TYPE = "unclassified";
const INACTIVE_POLICY_WARNING_TYPE = "inactive_policy_warning";

function isUnclassifiedErrorTypeValue(
  value: string | null | undefined,
): boolean {
  if (!value) return false;
  const normalized = value.trim().toLowerCase();
  return (
    normalized === UNCLASSIFIED_ERROR_TYPE_FILTER_VALUE.toLowerCase() ||
    normalized === UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL.toLowerCase() ||
    normalized === UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL_EN.toLowerCase() ||
    normalized === "unclassified"
  );
}

function normalizeErrorTypeFilterValue(value: string): string {
  return isUnclassifiedErrorTypeValue(value)
    ? UNCLASSIFIED_ERROR_TYPE_FILTER_VALUE
    : value.trim();
}

function getUnclassifiedErrorTypeLabel(
  language: Parameters<typeof localize>[0],
): string {
  return localize(
    language,
    UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL_EN,
    UNCLASSIFIED_ERROR_TYPE_FILTER_LABEL,
  );
}

function formatErrorTypeDisplayValue(params: {
  value: string | null | undefined;
  language: Parameters<typeof localize>[0];
}): string {
  const { value, language } = params;
  if (!value || isUnclassifiedErrorTypeValue(value)) {
    return getUnclassifiedErrorTypeLabel(language);
  }
  if (value === INACTIVE_POLICY_WARNING_TYPE) {
    return localize(language, "Inactive Policy Warning", "闲置策略警告");
  }
  return value;
}

function normalizePolicyTypeFilter(
  value: string | null | undefined,
): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

export type EventsTableProps = {
  projectId: string;
  userId?: string;
  hideControls?: boolean;
  // External control props for embedded preview tables
  externalFilterState?: FilterState;
  externalDateRange?: TableDateRange;
  limitRows?: number;
  sessionId?: string;
  forcedLevel?: ObservationLevelType;
  forcedLevels?: ObservationLevelType[];
  disableDefaultTypeFilter?: boolean;
  clearTypeFilter?: boolean;
  filterQueryParamKey?: string;
  filterStorageKey?: string;
  /**
   * Show a per-row button that navigates to the full trace (and highlights the node).
   */
  showOpenTraceButton?: boolean;
  /**
   * When set, the table will run in the provided view mode and hide the view-mode toggle.
   * This is useful for pages like Analysis, where "trace" mode (root-only) would omit
   * WARNING/ERROR nested inside spans.
   */
  forceViewMode?: EventsViewMode;
  /**
   * Show a batch "Analysis" action button in the toolbar.
   */
  showBulkAnalysisButton?: boolean;
  defaultSidebarCollapsed?: boolean;
  replaceLevelWithErrorType?: boolean;
  replaceLevelWithPolicyType?: boolean;
  initialPolicyTypeFilter?: string | null;
  omittedColumns?: string[];
};

export default function ObservationsEventsTable({
  projectId,
  userId,
  hideControls = false,
  externalFilterState,
  externalDateRange,
  limitRows,
  sessionId,
  forcedLevel,
  forcedLevels,
  disableDefaultTypeFilter = false,
  clearTypeFilter = false,
  filterQueryParamKey,
  filterStorageKey,
  showOpenTraceButton = false,
  forceViewMode,
  showBulkAnalysisButton = false,
  defaultSidebarCollapsed,
  replaceLevelWithErrorType = false,
  replaceLevelWithPolicyType = false,
  initialPolicyTypeFilter,
  omittedColumns,
}: EventsTableProps) {
  const { language } = useLanguage();
  const router = useRouter();
  const { viewId } = router.query;

  const { setDetailPageList } = useDetailPageLists();
  const [selectedRows, setSelectedRows] = useState<RowSelectionState>({});
  const [errorTypeByObservationId, setErrorTypeByObservationId] = useState<
    Record<string, string | null>
  >({});
  const [isErrorTypeLoading, setIsErrorTypeLoading] = useState(false);
  const accumulatedErrorTypesRef = useRef<
    Map<string, { value: string; label: string }>
  >(new Map());
  const accumulatedPolicyTypesRef = useRef<Set<string>>(new Set());
  const hasSeenUnclassifiedPolicyRef = useRef(false);
  const [selectedErrorType, setSelectedErrorType] = useState<string | null>(
    null,
  );
  const [selectedPolicyType, setSelectedPolicyType] = useState<string | null>(
    normalizePolicyTypeFilter(initialPolicyTypeFilter),
  );
  useEffect(() => {
    if (!replaceLevelWithPolicyType) {
      return;
    }
    const normalized = normalizePolicyTypeFilter(initialPolicyTypeFilter);
    if (normalized != null) {
      setSelectedPolicyType(normalized);
    }
  }, [initialPolicyTypeFilter, replaceLevelWithPolicyType]);
  const normalizedForcedLevels = useMemo(
    () =>
      Array.from(
        new Set(
          forcedLevels && forcedLevels.length > 0
            ? forcedLevels
            : forcedLevel
              ? [forcedLevel]
              : [],
        ),
      ),
    [forcedLevels, forcedLevel],
  );
  const { searchQuery, searchType, setSearchQuery, setSearchType } =
    useFullTextSearch();

  const { selectAll, setSelectAll } = useSelectAll(projectId, "observations");
  const [showRunEvaluationDialog, setShowRunEvaluationDialog] = useState(false);
  const [showAddToDatasetDialog, setShowAddToDatasetDialog] = useState(false);

  const [paginationState, setPaginationState] = usePaginationState(1, 50);

  const [rowHeight, setRowHeight] = useRowHeightLocalStorage(
    "observations",
    "s",
  );

  const [inputFilterState] = useQueryFilterState(
    // Default type filter - exclude SPAN and EVENT types
    !viewId && !disableDefaultTypeFilter
      ? [
          {
            column: "type",
            type: "stringOptions",
            operator: "any of",
            value: [
              "GENERATION",
              "AGENT",
              "TOOL",
              "CHAIN",
              "RETRIEVER",
              "EVALUATOR",
              "EMBEDDING",
              "GUARDRAIL",
            ],
          },
        ]
      : [],
    "generations", // Use "generations" table name for compatibility
    projectId,
    {
      queryParamKey: filterQueryParamKey,
      storageKey: filterStorageKey,
    },
  );

  const [orderByState, setOrderByState] = useOrderByState({
    column: "startTime",
    order: "DESC",
  });

  const { timeRange, setTimeRange } = useTableDateRange(projectId);

  // Disabled for now because perhaps confusing — replaced by "Is Root Observation"
  // boolean facet in the sidebar (see filter-config.ts).
  //
  // RE-ENABLING THE VIEW MODE TOGGLE:
  // To re-enable, uncomment the code below AND the viewModeFilter, viewModeToggle,
  // auto-switch logic, and imports further down. However, note that the sidebar now
  // has an "Is Root Observation" boolean facet that also controls `hasParentObservation`.
  // Having BOTH active would create duplicate/conflicting filters. Pick one:
  //   - Sidebar facet only (current): remove this commented code entirely
  //   - Toolbar toggle only: uncomment this code, remove the boolean facet from
  //     web/src/features/events/config/filter-config.ts, and re-add
  //     `hasParentObservation` param to the useEventsFilterOptions call below
  //   - Both: would need deduplication logic to prevent conflicting filters
  //
  // View mode toggle (Trace vs Observation)
  const { viewMode: storedViewMode, setViewMode: setViewModeRaw } =
    useEventsViewMode(projectId);
  const viewMode = forceViewMode ?? storedViewMode;
  const canChangeViewMode = !forceViewMode;

  // For filter options: trace mode filters to root items, observation mode shows all
  const hasParentObservation = viewMode === "observation" ? undefined : false;

  const setViewMode = useCallback(
    (mode: EventsViewMode) => {
      if (!canChangeViewMode) return;
      setViewModeRaw(mode);
      setPaginationState({ page: 1, limit: paginationState.limit });
    },
    [
      canChangeViewMode,
      paginationState.limit,
      setPaginationState,
      setViewModeRaw,
    ],
  );

  // for auto data refresh
  const utils = api.useUtils();
  const [rawRefreshInterval, setRawRefreshInterval] =
    useSessionStorage<RefreshInterval>(
      `tableRefreshInterval-events-${projectId}`,
      60_000,
    );

  // Validate session storage value against allowed intervals
  const allowedValues = REFRESH_INTERVALS.map((i) => i.value);
  const refreshInterval = allowedValues.includes(rawRefreshInterval)
    ? rawRefreshInterval
    : null;
  const setRefreshInterval = useCallback(
    (value: RefreshInterval) => {
      if (allowedValues.includes(value)) {
        setRawRefreshInterval(value);
      }
    },
    [allowedValues, setRawRefreshInterval],
  );

  const [refreshTick, setRefreshTick] = useState(0);

  // Auto-increment refresh tick to force date range recalculation
  useEffect(() => {
    if (!refreshInterval) return;
    const id = setInterval(() => {
      setRefreshTick((t) => t + 1);
    }, refreshInterval);
    return () => clearInterval(id);
  }, [refreshInterval]);

  const handleRefresh = useCallback(() => {
    setRefreshTick((t) => t + 1);
    void Promise.all([
      utils.events.all.invalidate(),
      utils.events.countAll.invalidate(),
      utils.events.filterOptions.invalidate(),
    ]);
  }, [utils]);

  // Convert timeRange to absolute date range for compatibility
  // Include refreshTick to force recalculation on refresh
  const tableDateRange = useMemo(() => {
    // refreshTick forces recalculation but isn't used in computation
    void refreshTick;
    return toAbsoluteTimeRange(timeRange) ?? undefined;
  }, [timeRange, refreshTick]);

  const dateRange = externalDateRange ?? tableDateRange;

  const dateRangeFilter: FilterState = dateRange
    ? [
        {
          column: "startTime",
          type: "datetime",
          operator: ">=",
          value: dateRange.from,
        },
        ...(dateRange.to
          ? [
              {
                column: "startTime",
                type: "datetime",
                operator: "<=",
                value: dateRange.to,
              } as const,
            ]
          : []),
      ]
    : [];

  const oldFilterState = inputFilterState.concat(dateRangeFilter);

  // Fetch filter options
  const { filterOptions, isFilterOptionsPending } = useEventsFilterOptions({
    projectId,
    oldFilterState,
    hasParentObservation,
  });

  const queryFilter = useSidebarFilterState(
    observationEventsFilterConfig,
    filterOptions,
    projectId,
    isFilterOptionsPending,
    hideControls, // Disable URL persistence for embedded preview tables
  );

  const errorTypeDropdownOptions = useMemo(() => {
    const unclassifiedLabel = getUnclassifiedErrorTypeLabel(language);
    const ref = accumulatedErrorTypesRef.current;

    Object.values(errorTypeByObservationId).forEach((value) => {
      if (value == null) {
        ref.set(UNCLASSIFIED_ERROR_TYPE_FILTER_VALUE, {
          value: UNCLASSIFIED_ERROR_TYPE_FILTER_VALUE,
          label: unclassifiedLabel,
        });
        return;
      }
      if (value === INACTIVE_POLICY_WARNING_TYPE) {
        ref.set(INACTIVE_POLICY_WARNING_TYPE, {
          value: INACTIVE_POLICY_WARNING_TYPE,
          label: formatErrorTypeDisplayValue({
            value: INACTIVE_POLICY_WARNING_TYPE,
            language,
          }),
        });
        return;
      }
      const normalizedValue = normalizeErrorTypeFilterValue(value);
      if (!ref.has(normalizedValue)) {
        ref.set(normalizedValue, {
          value: normalizedValue,
          label: isUnclassifiedErrorTypeValue(normalizedValue)
            ? unclassifiedLabel
            : normalizedValue,
        });
      }
    });

    if (selectedErrorType && !ref.has(selectedErrorType)) {
      ref.set(selectedErrorType, {
        value: selectedErrorType,
        label: formatErrorTypeDisplayValue({
          value: selectedErrorType,
          language,
        }),
      });
    }

    ref.forEach((opt, key) => {
      if (key === UNCLASSIFIED_ERROR_TYPE_FILTER_VALUE) {
        opt.label = unclassifiedLabel;
      } else if (key === INACTIVE_POLICY_WARNING_TYPE) {
        opt.label = formatErrorTypeDisplayValue({ value: key, language });
      }
    });

    return Array.from(ref.values());
  }, [errorTypeByObservationId, language, selectedErrorType]);

  const serializeSidebarFilterState = useCallback((state: FilterState) => {
    return JSON.stringify(state, (_k, v) =>
      v instanceof Date ? v.toISOString() : v,
    );
  }, []);

  const upsertAnalysisFilters = useCallback(
    (state: FilterState): FilterState => {
      let next = state;
      const normalizeColumn = (column: unknown) =>
        String(column).toLowerCase().replace(/[\s_]/g, "");

      // Always clear policy-type specific filters first. We only re-apply them
      // when the policy-type mode is active.
      next = next.filter((f) => {
        const normalizedColumn = normalizeColumn(f.column);
        return (
          normalizedColumn !== "policytype" && normalizedColumn !== "policyname"
        );
      });
      next = next.filter(
        (f) =>
          !(
            f.type === "stringObject" &&
            f.column === "metadata" &&
            f.key === "policy_names"
          ),
      );

      if (clearTypeFilter) {
        // Stored filters may use either column id ("type") or display name ("Type").
        // Always clear both to ensure Analysis can show EVENT/SPAN types as well.
        next = next.filter((f) => String(f.column).toLowerCase() !== "type");
      }

      // Analysis (forced levels) should always include nested observations.
      // Users may have an "Is Root Observation" facet persisted from other views (trace mode),
      // which maps to `hasParentObservation=false` and would otherwise hide most errors/warnings.
      if (normalizedForcedLevels.length > 0) {
        next = next.filter(
          (f) => normalizeColumn(f.column) !== "hasparentobservation",
        );
      }

      if (normalizedForcedLevels.length > 0) {
        next = [
          // Stored filters may use either column id ("level") or display name ("Level").
          ...next.filter((f) => String(f.column).toLowerCase() !== "level"),
          {
            // Use column ID to avoid infinite oscillation with URL normalization.
            // The sidebar filter hook normalizes display names ("Level") to IDs ("level")
            // after URL decoding, causing a mismatch that triggers re-renders in a loop.
            column: "level",
            type: "stringOptions",
            operator: "any of",
            value: normalizedForcedLevels,
          },
        ];
      }

      next = next.filter((f) => normalizeColumn(f.column) !== "errortype");
      if (replaceLevelWithErrorType && selectedErrorType) {
        if (selectedErrorType === INACTIVE_POLICY_WARNING_TYPE) {
          next = [
            ...next.filter((f) => String(f.column).toLowerCase() !== "level"),
            {
              column: "level",
              type: "stringOptions",
              operator: "any of",
              value: ["WARNING"],
            },
          ];
        } else {
          const normalizedSelectedErrorType =
            normalizeErrorTypeFilterValue(selectedErrorType);
          next = [
            ...next,
            {
              column: "errorType",
              type: "stringOptions",
              operator: "any of",
              value: [normalizedSelectedErrorType],
            },
          ];
        }
      }
      if (replaceLevelWithPolicyType) {
        if (selectedPolicyType) {
          next = [
            ...next,
            selectedPolicyType === UNCLASSIFIED_POLICY_TYPE
              ? {
                  column: "metadata",
                  type: "stringObject",
                  key: "policy_names",
                  operator: "does not contain",
                  value: '"',
                }
              : {
                  column: "metadata",
                  type: "stringObject",
                  key: "policy_names",
                  operator: "contains",
                  value: JSON.stringify(selectedPolicyType),
                },
          ];
        }
      }

      return next;
    },
    [
      clearTypeFilter,
      normalizedForcedLevels,
      replaceLevelWithErrorType,
      replaceLevelWithPolicyType,
      selectedErrorType,
      selectedPolicyType,
    ],
  );

  useEffect(() => {
    if (
      !clearTypeFilter &&
      normalizedForcedLevels.length === 0 &&
      !replaceLevelWithErrorType &&
      !replaceLevelWithPolicyType
    ) {
      return;
    }
    const next = upsertAnalysisFilters(queryFilter.filterState);
    if (
      serializeSidebarFilterState(next) !==
      serializeSidebarFilterState(queryFilter.filterState)
    ) {
      queryFilter.setFilterState(next);
    }
  }, [
    clearTypeFilter,
    normalizedForcedLevels,
    replaceLevelWithErrorType,
    replaceLevelWithPolicyType,
    queryFilter.filterState,
    queryFilter.setFilterState,
    serializeSidebarFilterState,
    upsertAnalysisFilters,
  ]);

  // Create ref-based wrapper to avoid stale closure when queryFilter updates
  const queryFilterRef = useRef(queryFilter);
  queryFilterRef.current = queryFilter;

  const setFiltersWrapper = useCallback(
    (filters: FilterState) => queryFilterRef.current?.setFilterState(filters),
    [],
  );

  const viewModeFilter: FilterState =
    viewMode === "trace"
      ? [
          {
            column: "hasParentObservation",
            type: "boolean",
            operator: "=",
            value: false,
          },
        ]
      : [];

  const sidebarFilterState = useMemo(() => {
    if (viewMode !== "trace") return queryFilter.filterState;
    const normalizeColumn = (column: unknown) =>
      String(column).toLowerCase().replace(/[\s_]/g, "");
    return queryFilter.filterState.filter(
      (f) => normalizeColumn(f.column) !== "hasparentobservation",
    );
  }, [queryFilter.filterState, viewMode]);

  // Create user ID filter if userId is provided
  const userIdFilter: FilterState = userId
    ? [
        {
          column: "User ID",
          type: "string",
          operator: "=",
          value: userId,
        },
      ]
    : [];

  const sessionIdFilter: FilterState = sessionId
    ? [
        {
          column: "Session ID",
          type: "string",
          operator: "=",
          value: sessionId,
        },
      ]
    : [];

  const combinedFilterState = sidebarFilterState
    .concat(dateRangeFilter)
    .concat(userIdFilter)
    .concat(sessionIdFilter)
    .concat(viewModeFilter);

  // Use external filter state if provided, otherwise use combined filter state
  const filterState = externalFilterState || combinedFilterState;

  // Use the custom hook for observations data fetching
  const {
    observations,
    totalCount,
    handleAddToAnnotationQueue,
    dataUpdatedAt,
    ioLoading,
    isSilencedError,
  } = useEventsTableData({
    projectId,
    filterState,
    paginationState: limitRows
      ? { page: 1, limit: limitRows }
      : paginationState,
    orderByState,
    searchQuery,
    searchType,
    selectedRows,
    selectAll,
    setSelectedRows,
  });

  const policyTypeDropdownOptions = useMemo(() => {
    const typesRef = accumulatedPolicyTypesRef.current;
    (observations.rows ?? []).forEach((observation) => {
      const policyNames = parsePolicyNamesFromMetadata(observation.metadata);
      if (policyNames.length === 0) {
        hasSeenUnclassifiedPolicyRef.current = true;
      } else {
        policyNames.forEach((policyName) => typesRef.add(policyName));
      }
    });
    if (selectedPolicyType && selectedPolicyType !== UNCLASSIFIED_POLICY_TYPE) {
      typesRef.add(selectedPolicyType);
    }
    const sortedOptions = Array.from(typesRef)
      .sort((a, b) => a.localeCompare(b))
      .map((value) => ({ value, label: value }));
    if (
      hasSeenUnclassifiedPolicyRef.current ||
      selectedPolicyType === UNCLASSIFIED_POLICY_TYPE
    ) {
      sortedOptions.push({
        value: UNCLASSIFIED_POLICY_TYPE,
        label: UNCLASSIFIED_POLICY_TYPE,
      });
    }
    return sortedOptions;
  }, [observations.rows, selectedPolicyType]);

  // Disabled for now because perhaps confusing
  // === Auto-switch to observation mode when trace view is empty ===
  // (commented out along with view mode toggle)

  useEffect(() => {
    if (observations.status === "success") {
      setDetailPageList(
        "observations",
        observations?.rows?.map((o) => ({
          id: o?.id,
          params: {
            traceId: o?.traceId || "",
            ...(o?.startTime ? { timestamp: o?.startTime.toISOString() } : {}),
          },
        })) ?? [],
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [observations.status, observations.rows]);

  const { scoreColumns, isLoading: isColumnLoading } =
    useScoreColumns<EventsTableRow>({
      scoreColumnKey: "scores",
      projectId,
      filter: scoreFilters.forObservations(),
      fromTimestamp: dateRange?.from,
    });

  const { selectActionColumn } = TableSelectionManager<EventsTableRow>({
    projectId,
    tableName: "observations",
    setSelectedRows,
  });

  const tableActions: TableAction[] = [
    {
      id: ActionId.ObservationAddToAnnotationQueue,
      type: BatchActionType.Create,
      label: localize(language, "Add to Annotation Queue", "加入标注队列"),
      description: localize(
        language,
        "Add selected observations to an annotation queue.",
        "将选中的 observations 加入标注队列。",
      ),
      targetLabel: localize(language, "Annotation Queue", "标注队列"),
      execute: handleAddToAnnotationQueue,
      accessCheck: {
        scope: "annotationQueues:CUD",
      },
    },
    {
      id: ActionId.ObservationAddToDataset,
      type: BatchActionType.Create,
      label: localize(language, "Add to Dataset", "加入数据集"),
      description: localize(
        language,
        "Add selected observations to a dataset",
        "将选中的 observations 加入数据集",
      ),
      customDialog: true,
      accessCheck: {
        scope: "datasets:CUD",
      },
    },
    {
      id: ActionId.ObservationBatchEvaluation,
      type: BatchActionType.Create,
      label: localize(language, "Evaluate", "评估"),
      description: localize(
        language,
        "Run evaluations on selected observations.",
        "对选中的 observations 运行评估。",
      ),
      customDialog: true,
      icon: <LightbulbIcon className="mr-2 h-4 w-4" />,
      accessCheck: {
        scope: "evalJob:CUD",
      },
    },
  ];

  const buildTraceHref = useCallback(
    (params: { traceId: string; observationId: string; timestamp?: Date }) => {
      const qp = new URLSearchParams();
      qp.set("observation", params.observationId);
      if (params.timestamp) {
        qp.set("timestamp", params.timestamp.toISOString());
      }
      return `/project/${projectId}/traces/${encodeURIComponent(params.traceId)}?${qp.toString()}`;
    },
    [projectId],
  );

  const enableSorting = !hideControls;
  const getLocalizedEventsColumnName = useCallback(
    (id: string) => {
      switch (id) {
        case "startTime":
          return localize(language, "Timestamp", "时间戳");
        case "type":
          return localize(language, "Type", "类型");
        case "name":
          return localize(language, "Name", "名称");
        case "traceName":
          return localize(language, "Trace Name", "Trace 名称");
        case "input":
          return localize(language, "Input", "输入");
        case "output":
          return localize(language, "Output", "输出");
        case "level":
          return localize(language, "Level", "级别");
        case "statusMessage":
          return localize(language, "Status Message", "状态消息");
        case "latency":
          return localize(language, "Latency", "延迟");
        case "totalCost":
          return localize(language, "Total Cost", "总成本");
        case "inputCost":
          return localize(language, "Input Cost", "输入成本");
        case "outputCost":
          return localize(language, "Output Cost", "输出成本");
        case "toolDefinitions":
          return localize(language, "Available Tools", "可用工具");
        case "toolCalls":
          return localize(language, "Tool Calls", "工具调用");
        case "timeToFirstToken":
          return localize(language, "Time to First Token", "首词元时间");
        case "inputTokens":
          return localize(language, "Input Tokens", "输入词元");
        case "outputTokens":
          return localize(language, "Output Tokens", "输出词元");
        case "totalTokens":
          return localize(language, "Total Tokens", "总词元");
        case "providedModelName":
          return localize(language, "Model", "模型");
        case "promptName":
          return localize(language, "Prompt", "Prompt");
        case "environment":
          return localize(language, "Environment", "环境");
        case "traceTags":
          return localize(language, "Trace Tags", "Trace 标签");
        case "endTime":
          return localize(language, "End Time", "结束时间");
        case "traceId":
          return localize(language, "Trace ID", "Trace ID");
        case "modelId":
          return localize(language, "Model ID", "模型 ID");
        case "version":
          return localize(language, "Version", "版本");
        case "userId":
          return localize(language, "User ID", "用户 ID");
        case "sessionId":
          return localize(language, "Session ID", "会话 ID");
        default:
          return getEventsColumnName(id);
      }
    },
    [language],
  );

  const columns: LangfuseColumnDef<EventsTableRow>[] = [
    ...(hideControls ? [] : [selectActionColumn]),
    ...(showOpenTraceButton
      ? ([
          {
            id: "openTrace",
            accessorKey: "openTrace",
            header: "",
            size: 44,
            enableHiding: false,
            enableSorting: false,
            cell: ({ row }) => {
              const traceId = row.original.traceId;
              if (!traceId) return null;

              const href = buildTraceHref({
                traceId,
                observationId: row.original.id,
                timestamp: row.original.timestamp ?? row.original.startTime,
              });

              return (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      asChild
                    >
                      <Link
                        href={href}
                        prefetch={false}
                        aria-label={localize(
                          language,
                          "Open full trace",
                          "打开完整 Trace",
                        )}
                        onClick={(e) => {
                          e.stopPropagation();
                        }}
                      >
                        <ArrowUpRight className="h-4 w-4" />
                      </Link>
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="top">
                    {localize(language, "Open full trace", "打开完整 Trace")}
                  </TooltipContent>
                </Tooltip>
              );
            },
          },
        ] as LangfuseColumnDef<EventsTableRow>[])
      : []),
    {
      accessorKey: "startTime",
      id: "startTime",
      header: getLocalizedEventsColumnName("startTime"),
      size: 150,
      enableHiding: true,
      enableSorting,
      cell: ({ row }) => {
        const value: Date = row.getValue("startTime");
        return <LocalIsoDate date={value} />;
      },
    },
    {
      accessorKey: "type",
      id: "type",
      header: getLocalizedEventsColumnName("type"),
      size: 50,
      enableSorting,
      cell: ({ row }) => {
        const value: ObservationType = row.getValue("type");
        return value ? (
          <div className="flex items-center gap-1">
            <ItemBadge type={value} />
          </div>
        ) : undefined;
      },
    },
    {
      accessorKey: "name",
      id: "name",
      header: getLocalizedEventsColumnName("name"),
      size: 150,
      enableSorting,
      cell: ({ row }) => {
        const value: EventsTableRow["name"] = row.getValue("name");
        return value ?? undefined;
      },
    },
    {
      accessorKey: "traceName",
      id: "traceName",
      header: getLocalizedEventsColumnName("traceName"),
      size: 150,
      enableSorting: true,
      cell: ({ row }) => {
        const value: string | undefined = row.getValue("traceName");
        return value ?? undefined;
      },
    },
    {
      accessorKey: "input",
      header: getLocalizedEventsColumnName("input"),
      id: "input",
      size: 220,
      cell: ({ row }) => {
        const value: string | undefined = row.getValue("input");
        if (ioLoading) {
          return (
            <JsonSkeleton
              borderless
              className="h-full w-full overflow-hidden px-2 py-1"
            />
          );
        }
        return value ? (
          <MemoizedIOTableCell
            isLoading={false}
            data={value}
            singleLine={rowHeight === "s"}
          />
        ) : null;
      },
      enableHiding: true,
    },
    {
      accessorKey: "output",
      id: "output",
      header: getLocalizedEventsColumnName("output"),
      size: 220,
      cell: ({ row }) => {
        const value: string | undefined = row.getValue("output");
        if (ioLoading) {
          return (
            <JsonSkeleton
              borderless
              className="h-full w-full overflow-hidden px-2 py-1"
            />
          );
        }
        return value ? (
          <MemoizedIOTableCell
            isLoading={false}
            data={value}
            className={cn("bg-accent-light-green")}
            singleLine={rowHeight === "s"}
          />
        ) : null;
      },
      enableHiding: true,
    },
    {
      accessorKey: "metadata",
      header: localize(language, "Metadata", "元数据"),
      size: 300,
      headerTooltip: {
        description: "Add metadata to traces to track additional information.",
        href: "https://langfuse.com/docs/observability/features/metadata",
      },
      cell: ({ row }) => {
        const value: string | undefined = row.getValue("metadata");
        if (ioLoading) {
          return (
            <JsonSkeleton
              borderless
              className="h-full w-full overflow-hidden px-2 py-1"
            />
          );
        }
        return value ? (
          <MemoizedIOTableCell
            isLoading={false}
            data={value}
            singleLine={rowHeight === "s"}
          />
        ) : null;
      },
      enableHiding: true,
    },
    {
      accessorKey: "level",
      id: "level",
      header: replaceLevelWithPolicyType
        ? () => (
            <div className="flex items-center gap-1">
              <span>Policy type</span>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-5 gap-1 px-1 text-[10px] font-normal"
                    aria-label="Filter by policy type"
                    title={selectedPolicyType ?? undefined}
                  >
                    <span>{selectedPolicyType ? "filtered" : "all"}</span>
                    <ChevronDown className="h-3 w-3 opacity-70" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="start"
                  className="max-h-64 overflow-y-auto"
                >
                  <DropdownMenuItem
                    onClick={() => setSelectedPolicyType(null)}
                    className="flex items-center justify-between gap-2"
                  >
                    <span>All</span>
                    {!selectedPolicyType ? <Check className="h-3 w-3" /> : null}
                  </DropdownMenuItem>
                  {policyTypeDropdownOptions.map((option) => (
                    <DropdownMenuItem
                      key={option.value}
                      onClick={() => setSelectedPolicyType(option.value)}
                      className="flex items-center justify-between gap-2"
                    >
                      <span>{option.label}</span>
                      {selectedPolicyType === option.value ? (
                        <Check className="h-3 w-3" />
                      ) : null}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          )
        : replaceLevelWithErrorType
          ? () => (
              <div className="flex items-center gap-1">
                <span>Type</span>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-5 gap-1 px-1 text-[10px] font-normal"
                      aria-label="Filter by error type"
                    >
                      <span>
                        {selectedErrorType
                          ? formatErrorTypeDisplayValue({
                              value: selectedErrorType,
                              language,
                            })
                          : localize(language, "all", "全部")}
                      </span>
                      <ChevronDown className="h-3 w-3 opacity-70" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    align="start"
                    className="max-h-64 overflow-y-auto"
                  >
                    <DropdownMenuItem
                      onClick={() => setSelectedErrorType(null)}
                      className="flex items-center justify-between gap-2"
                    >
                      <span>All</span>
                      {!selectedErrorType ? (
                        <Check className="h-3 w-3" />
                      ) : null}
                    </DropdownMenuItem>
                    {errorTypeDropdownOptions.map((option) => (
                      <DropdownMenuItem
                        key={option.value}
                        onClick={() => setSelectedErrorType(option.value)}
                        className="flex items-center justify-between gap-2"
                      >
                        <span>{option.label}</span>
                        {selectedErrorType === option.value ? (
                          <Check className="h-3 w-3" />
                        ) : null}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            )
          : getLocalizedEventsColumnName("level"),
      size: 320,
      minSize: 260,
      headerTooltip: replaceLevelWithPolicyType
        ? undefined
        : replaceLevelWithErrorType
          ? undefined
          : {
              description:
                "You can differentiate the importance of observations with the level attribute to control the verbosity of your traces and highlight errors and warnings.",
              href: "https://langfuse.com/docs/observability/features/log-levels",
            },
      enableHiding: true,
      cell: ({ row }) => {
        const value: ObservationLevelType | undefined = row.getValue("level");
        if (replaceLevelWithPolicyType) {
          if (value !== "POLICY_VIOLATION") {
            return undefined;
          }
          const policyNames = parsePolicyNamesFromMetadata(
            row.original.metadata,
          );
          const displayValues =
            policyNames.length > 0 ? policyNames : [UNCLASSIFIED_POLICY_TYPE];
          return (
            <div className="flex flex-wrap items-start gap-2 whitespace-normal py-0.5">
              {displayValues.map((displayValue, idx) => (
                <span
                  key={`${row.original.id}-${displayValue}-${idx}`}
                  className="inline-flex max-w-full whitespace-normal break-all rounded-md bg-amber-100 px-2 py-0.5 text-xs leading-normal text-amber-700 dark:bg-amber-900/40 dark:text-amber-300"
                >
                  {displayValue}
                </span>
              ))}
            </div>
          );
        }
        if (replaceLevelWithErrorType) {
          if (!value || (value !== "ERROR" && value !== "WARNING")) {
            return undefined;
          }

          const errorType = errorTypeByObservationId[row.original.id];

          const isInactivePolicy = errorType === INACTIVE_POLICY_WARNING_TYPE;

          const displayValue = isInactivePolicy
            ? formatErrorTypeDisplayValue({
                value: INACTIVE_POLICY_WARNING_TYPE,
                language,
              })
            : isErrorTypeLoading && errorType === undefined
              ? "..."
              : formatErrorTypeDisplayValue({
                  value: errorType,
                  language,
                });

          return (
            <div className="flex flex-wrap items-start gap-2 whitespace-normal py-0.5">
              <span
                className={cn(
                  "inline-flex max-w-full whitespace-normal break-all rounded-md px-2 py-0.5 text-xs leading-normal",
                  isInactivePolicy
                    ? "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300"
                    : "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
                )}
              >
                {displayValue}
              </span>
            </div>
          );
        }

        return value ? (
          <span
            className={cn(
              "rounded-sm p-0.5 text-xs",
              LevelColors[value].bg,
              LevelColors[value].text,
            )}
          >
            {value}
          </span>
        ) : undefined;
      },
      enableSorting:
        replaceLevelWithErrorType || replaceLevelWithPolicyType
          ? false
          : enableSorting,
    },
    {
      accessorKey: "statusMessage",
      header: getLocalizedEventsColumnName("statusMessage"),
      id: "statusMessage",
      size: 150,
      headerTooltip: {
        description:
          "Use a statusMessage to e.g. provide additional information on a status such as level=ERROR.",
        href: "https://langfuse.com/docs/observability/features/log-levels",
      },
      enableHiding: true,
      defaultHidden: true,
      cell: ({ row }) => {
        const value: string | undefined = row.getValue("statusMessage");
        return value ? (
          <MemoizedIOTableCell
            isLoading={false}
            data={value}
            singleLine={rowHeight === "s"}
          />
        ) : undefined;
      },
    },
    {
      accessorKey: "latency",
      id: "latency",
      header: getLocalizedEventsColumnName("latency"),
      size: 100,
      cell: ({ row }) => {
        const latency: number | undefined = row.getValue("latency");
        return latency !== undefined ? (
          <span>{formatIntervalSeconds(latency)}</span>
        ) : undefined;
      },
      enableHiding: true,
      enableSorting,
    },
    {
      accessorKey: "totalCost",
      header: getLocalizedEventsColumnName("totalCost"),
      id: "totalCost",
      size: 120,
      cell: ({ row }) => {
        const value: number | undefined = row.getValue("totalCost");

        return value !== undefined ? (
          <BreakdownTooltip
            details={row.original.costDetails}
            isCost
            pricingTierName={row.original.usagePricingTierName ?? undefined}
          >
            <div className="flex items-center gap-1">
              <span>{usdFormatter(value)}</span>
              <InfoIcon className="h-3 w-3" />
            </div>
          </BreakdownTooltip>
        ) : undefined;
      },
      enableHiding: true,
      enableSorting,
    },
    {
      accessorKey: "cost",
      header: "Cost",
      id: "cost",
      enableHiding: true,
      defaultHidden: true,
      cell: () => {
        return observations.status === "loading" ? (
          <Skeleton className="h-3 w-1/2" />
        ) : null;
      },
      columns: [
        {
          accessorKey: "inputCost",
          id: "inputCost",
          header: getLocalizedEventsColumnName("inputCost"),
          size: 120,
          cell: ({ row }: { row: Row<EventsTableRow> }) => {
            const value = row.getValue("cost") as {
              inputCost: number | undefined;
              outputCost: number | undefined;
            };

            return value.inputCost !== undefined ? (
              <span>{usdFormatter(value.inputCost)}</span>
            ) : undefined;
          },
          enableHiding: true,
          defaultHidden: true,
          enableSorting,
        },
        {
          accessorKey: "outputCost",
          id: "outputCost",
          header: getLocalizedEventsColumnName("outputCost"),
          size: 120,
          cell: ({ row }: { row: Row<EventsTableRow> }) => {
            const value = row.getValue("cost") as {
              inputCost: number | undefined;
              outputCost: number | undefined;
            };

            return value.outputCost !== undefined ? (
              <span>{usdFormatter(value.outputCost)}</span>
            ) : undefined;
          },
          enableHiding: true,
          defaultHidden: true,
          enableSorting,
        },
      ],
    },
    {
      accessorKey: "toolDefinitions",
      id: "toolDefinitions",
      header: getLocalizedEventsColumnName("toolDefinitions"),
      size: 120,
      enableHiding: true,
      enableSorting,
      defaultHidden: true,
      cell: ({ row }) => {
        const value: number | undefined = row.getValue("toolDefinitions");
        return value !== undefined ? (
          <span>{numberFormatter(value, 0)}</span>
        ) : undefined;
      },
    },
    {
      accessorKey: "toolCalls",
      id: "toolCalls",
      header: getLocalizedEventsColumnName("toolCalls"),
      size: 100,
      enableHiding: true,
      enableSorting,
      defaultHidden: true,
      cell: ({ row }) => {
        const value: number | undefined = row.getValue("toolCalls");
        return value !== undefined ? (
          <span>{numberFormatter(value, 0)}</span>
        ) : undefined;
      },
    },
    {
      accessorKey: "timeToFirstToken",
      id: "timeToFirstToken",
      header: getLocalizedEventsColumnName("timeToFirstToken"),
      size: 150,
      enableHiding: true,
      enableSorting,
      cell: ({ row }) => {
        const timeToFirstToken: number | undefined =
          row.getValue("timeToFirstToken");

        return (
          <span>
            {timeToFirstToken ? formatIntervalSeconds(timeToFirstToken) : "-"}
          </span>
        );
      },
    },
    {
      accessorKey: "usage",
      header: localize(language, "Usage", "用量"),
      id: "usage",
      enableHiding: true,
      defaultHidden: true,
      cell: () => {
        return observations.status === "loading" ? (
          <Skeleton className="h-3 w-1/2" />
        ) : null;
      },
      columns: [
        {
          accessorKey: "tokensPerSecond",
          id: "tokensPerSecond",
          header: localize(language, "Tokens per second", "每秒词元"),
          size: 200,
          cell: ({ row }: { row: Row<EventsTableRow> }) => {
            const latency: number | undefined = row.getValue("latency");
            const usage = row.getValue("usage") as {
              inputUsage: number;
              outputUsage: number;
              totalUsage: number;
            };
            return latency !== undefined &&
              (usage.outputUsage !== 0 || usage.totalUsage !== 0) ? (
              <span>
                {usage.outputUsage && latency
                  ? Number((usage.outputUsage / latency).toFixed(1))
                  : undefined}
              </span>
            ) : undefined;
          },
          defaultHidden: true,
          enableHiding: true,
          enableSorting,
        },
        {
          accessorKey: "inputTokens",
          id: "inputTokens",
          header: getLocalizedEventsColumnName("inputTokens"),
          size: 100,
          enableHiding: true,
          defaultHidden: true,
          enableSorting,
          cell: ({ row }: { row: Row<EventsTableRow> }) => {
            const value = row.getValue("usage") as {
              inputUsage: number;
              outputUsage: number;
              totalUsage: number;
            };
            return <span>{numberFormatter(value.inputUsage, 0)}</span>;
          },
        },
        {
          accessorKey: "outputTokens",
          id: "outputTokens",
          header: getLocalizedEventsColumnName("outputTokens"),
          size: 100,
          enableHiding: true,
          defaultHidden: true,
          enableSorting,
          cell: ({ row }: { row: Row<EventsTableRow> }) => {
            const value = row.getValue("usage") as {
              inputUsage: number;
              outputUsage: number;
              totalUsage: number;
            };
            return <span>{numberFormatter(value.outputUsage, 0)}</span>;
          },
        },
        {
          accessorKey: "totalTokens",
          id: "totalTokens",
          header: getLocalizedEventsColumnName("totalTokens"),
          size: 100,
          enableHiding: true,
          defaultHidden: true,
          enableSorting,
          cell: ({ row }: { row: Row<EventsTableRow> }) => {
            const value = row.getValue("usage") as {
              inputUsage: number;
              outputUsage: number;
              totalUsage: number;
            };
            return <span>{numberFormatter(value.totalUsage, 0)}</span>;
          },
        },
      ],
    },
    {
      accessorKey: "providedModelName",
      id: "providedModelName",
      header: getLocalizedEventsColumnName("providedModelName"),
      size: 150,
      enableHiding: true,
      enableSorting,
      cell: ({ row }) => {
        const model = row.getValue("providedModelName") as string;
        const modelId = row.getValue("modelId") as string | undefined;

        if (!model) return null;

        return modelId ? (
          <TableIdOrName value={model} />
        ) : (
          <UpsertModelFormDialog
            action="create"
            projectId={projectId}
            prefilledModelData={{
              modelName: model,
              prices:
                Object.keys(row.original.usageDetails).length > 0
                  ? Object.keys(row.original.usageDetails)
                      .filter((key) => key != "total")
                      .reduce(
                        (acc, key) => {
                          acc[key] = 0.000001;
                          return acc;
                        },
                        {} as Record<string, number>,
                      )
                  : undefined,
            }}
            className="cursor-pointer"
          >
            <span className="flex items-center gap-1">
              <span>{model}</span>
              <PlusCircle className="h-3 w-3" />
            </span>
          </UpsertModelFormDialog>
        );
      },
    },
    {
      accessorKey: "promptName",
      id: "promptName",
      header: getLocalizedEventsColumnName("promptName"),
      headerTooltip: {
        description: "Link to prompt version in Langfuse prompt management.",
        href: "https://langfuse.com/docs/prompt-management/get-started",
      },
      size: 200,
      enableHiding: true,
      enableSorting,
      cell: ({ row }) => {
        const promptName = row.original.promptName;
        const promptVersion = row.original.promptVersion;
        const value = `${promptName} (v${promptVersion})`;
        return promptName && promptVersion && <TableIdOrName value={value} />;
      },
    },
    {
      accessorKey: "environment",
      header: getLocalizedEventsColumnName("environment"),
      id: "environment",
      size: 150,
      enableHiding: true,
      cell: ({ row }) => {
        const value: EventsTableRow["environment"] =
          row.getValue("environment");
        return value ? (
          <Badge
            variant="secondary"
            className="max-w-fit truncate rounded-sm px-1 font-normal"
          >
            {value}
          </Badge>
        ) : null;
      },
    },
    {
      accessorKey: "traceTags",
      id: "traceTags",
      header: getLocalizedEventsColumnName("traceTags"),
      size: 250,
      enableHiding: true,
      cell: ({ row }) => {
        const traceTags: string[] | undefined = row.getValue("traceTags");
        return (
          traceTags && (
            <div
              className={cn(
                "flex gap-x-2 gap-y-1",
                rowHeight !== "s" && "flex-wrap",
              )}
            >
              <TagList selectedTags={traceTags} isLoading={false} />
            </div>
          )
        );
      },
    },
    {
      accessorKey: "scores",
      header: localize(language, "Scores", "评分"),
      id: "scores",
      enableHiding: true,
      defaultHidden: true,
      cell: () => {
        return isColumnLoading ? <Skeleton className="h-3 w-1/2" /> : null;
      },
      columns: scoreColumns,
    },
    {
      accessorKey: "endTime",
      id: "endTime",
      header: getLocalizedEventsColumnName("endTime"),
      size: 150,
      enableHiding: true,
      enableSorting,
      defaultHidden: true,
      cell: ({ row }) => {
        const value: Date | undefined = row.getValue("endTime");
        return value ? <LocalIsoDate date={value} /> : undefined;
      },
    },
    {
      accessorKey: "traceId",
      id: "traceId",
      header: getLocalizedEventsColumnName("traceId"),
      size: 100,
      cell: ({ row }) => {
        const value = row.getValue("traceId");
        return typeof value === "string" ? (
          <TableIdOrName value={value} />
        ) : undefined;
      },
      enableSorting,
      enableHiding: true,
      defaultHidden: true,
    },
    {
      accessorKey: "modelId",
      id: "modelId",
      header: getLocalizedEventsColumnName("modelId"),
      size: 100,
      enableHiding: true,
      defaultHidden: true,
    },
    {
      accessorKey: "version",
      id: "version",
      header: getLocalizedEventsColumnName("version"),
      size: 100,
      headerTooltip: {
        description: "Track changes via the version tag.",
        href: "https://langfuse.com/docs/experimentation",
      },
      enableHiding: true,
      enableSorting,
      defaultHidden: true,
    },
    {
      accessorKey: "userId",
      id: "userId",
      header: getLocalizedEventsColumnName("userId"),
      size: 150,
      enableHiding: true,
      defaultHidden: true,
    },
    {
      accessorKey: "sessionId",
      id: "sessionId",
      header: getLocalizedEventsColumnName("sessionId"),
      size: 150,
      enableHiding: true,
      defaultHidden: true,
    },
  ];

  const effectiveColumns = useMemo(() => {
    if (!omittedColumns?.length) {
      return columns;
    }

    const omittedColumnSet = new Set(omittedColumns);
    return columns.filter((column) => {
      const columnKey =
        (typeof column.id === "string" ? column.id : undefined) ??
        (typeof column.accessorKey === "string"
          ? column.accessorKey
          : undefined);
      return !columnKey || !omittedColumnSet.has(columnKey);
    });
  }, [columns, omittedColumns]);

  const [columnVisibility, setColumnVisibilityState] =
    useColumnVisibility<EventsTableRow>(
      `eventsColumnVisibility-${projectId}`,
      effectiveColumns,
    );

  const [columnOrder, setColumnOrder] = useColumnOrder<EventsTableRow>(
    `eventsColumnOrder-${projectId}`,
    effectiveColumns,
  );

  const peekNavigationProps = usePeekNavigation({
    queryParams: ["observation", "display", "timestamp", "traceId"],
    paramsToMirrorPeekValue: ["observation"],
    extractParamsValuesFromRow: (row: EventsTableRow) => ({
      traceId: row.traceId || "",
      timestamp: row.timestamp?.toISOString() || "",
    }),
    expandConfig: {
      basePath: `/project/${projectId}/traces`,
      pathParam: "traceId",
    },
  });

  const { isLoading: isViewLoading, ...viewControllers } = useTableViewManager({
    tableName: TableViewPresetTableName.Observations,
    projectId,
    stateUpdaters: {
      setOrderBy: setOrderByState,
      setFilters: setFiltersWrapper,
      setColumnOrder: setColumnOrder,
      setColumnVisibility: setColumnVisibilityState,
      setSearchQuery: setSearchQuery,
    },
    validationContext: {
      columns: effectiveColumns,
      filterColumnDefinition: observationEventsFilterConfig.columnDefinitions,
    },
    currentFilterState: queryFilter.filterState,
  });

  const peekConfig: DataTablePeekViewProps | undefined = useMemo(() => {
    if (hideControls) return undefined;
    return {
      itemType: "TRACE",
      customTitlePrefix: localize(
        language,
        "Observation ID:",
        "Observation ID：",
      ),
      detailNavigationKey: "observations",
      children: <PeekViewObservationDetail projectId={projectId} />,
      ...peekNavigationProps,
    };
  }, [language, projectId, peekNavigationProps, hideControls]);

  const rows: EventsTableRow[] = useMemo(() => {
    const result =
      observations.status === "success" && observations.rows
        ? observations.rows.map((observation) => {
            return {
              id: observation.id,
              traceId: observation.traceId ?? undefined,
              type: observation.type ?? undefined,
              spanId: observation.id, // span_id maps to id
              parentSpanId: observation.parentObservationId ?? undefined,
              startTime: observation.startTime,
              endTime: observation.endTime ?? undefined,
              timeToFirstToken: observation.timeToFirstToken ?? undefined,
              scores: {}, // TODO: scores not included in FullObservation type
              latency: observation.latency ?? undefined,
              totalCost: observation.totalCost ?? undefined,
              cost: {
                inputCost: observation.inputCost ?? undefined,
                outputCost: observation.outputCost ?? undefined,
              },
              name: observation.name ?? undefined,
              version: observation.version ?? "",
              providedModelName: observation.model ?? "",
              modelId: observation.internalModelId ?? undefined,
              level: observation.level,
              statusMessage: observation.statusMessage ?? undefined,
              usage: {
                inputUsage: observation.inputUsage,
                outputUsage: observation.outputUsage,
                totalUsage: observation.totalUsage,
              },
              promptId: observation.promptId ?? undefined,
              promptName: observation.promptName ?? undefined,
              promptVersion: observation.promptVersion?.toString() ?? undefined,
              traceTags: undefined, // TODO: traceTags not available in EventsObservation
              traceName: observation.traceName ?? undefined,
              timestamp: observation.startTime ?? undefined,
              usageDetails: observation.usageDetails ?? {},
              costDetails: observation.costDetails ?? {},
              usagePricingTierName:
                observation.usagePricingTierName ?? undefined,
              environment: observation.environment ?? undefined,
              // I/O data comes from joined data already
              input: observation.input
                ? typeof observation.input === "string"
                  ? observation.input
                  : JSON.stringify(observation.input)
                : undefined,
              output: observation.output
                ? typeof observation.output === "string"
                  ? observation.output
                  : JSON.stringify(observation.output)
                : undefined,
              metadata: observation.metadata,
              userId: observation.userId ?? undefined,
              sessionId: observation.sessionId ?? undefined,
              completionStartTime: observation.completionStartTime ?? undefined,
              toolDefinitions: observation.toolDefinitions
                ? Object.keys(observation.toolDefinitions).length
                : undefined,
              toolCalls: observation.toolCalls
                ? observation.toolCalls.length
                : undefined,
            };
          })
        : [];

    return result;
  }, [observations]);

  const errorTypeTargets = useMemo(
    () =>
      replaceLevelWithErrorType
        ? rows
            .filter(
              (row) =>
                (row.level === "ERROR" || row.level === "WARNING") &&
                !!row.traceId,
            )
            .map((row) => ({
              id: row.id,
              traceId: row.traceId as string,
              level: row.level,
              name: row.name,
            }))
        : [],
    [replaceLevelWithErrorType, rows],
  );

  const errorTypeTargetKey = useMemo(
    () =>
      errorTypeTargets
        .map((target) => `${target.id}:${target.traceId}`)
        .join("|"),
    [errorTypeTargets],
  );

  useEffect(() => {
    if (!replaceLevelWithErrorType) {
      setErrorTypeByObservationId((prev) =>
        Object.keys(prev).length === 0 ? prev : {},
      );
      setIsErrorTypeLoading(false);
      return;
    }

    if (errorTypeTargets.length === 0) {
      setErrorTypeByObservationId((prev) =>
        Object.keys(prev).length === 0 ? prev : {},
      );
      setIsErrorTypeLoading(false);
      return;
    }

    let cancelled = false;
    setIsErrorTypeLoading(true);

    void Promise.all(
      errorTypeTargets.map(async (target) => {
        try {
          const result = await directApi.errorAnalysis.getSummary.query({
            projectId,
            traceId: target.traceId,
            observationId: target.id,
          });

          let errorType = result?.errorType ?? null;
          if (
            errorType == null &&
            target.level === "WARNING" &&
            isSessionOutputTurnObservationName(target.name)
          ) {
            errorType = INACTIVE_POLICY_WARNING_TYPE;
          }

          return {
            observationId: target.id,
            errorType,
          };
        } catch {
          if (
            target.level === "WARNING" &&
            isSessionOutputTurnObservationName(target.name)
          ) {
            return {
              observationId: target.id,
              errorType: INACTIVE_POLICY_WARNING_TYPE,
            };
          }
          return {
            observationId: target.id,
            errorType: null,
          };
        }
      }),
    ).then((results) => {
      if (cancelled) return;

      const nextMap = Object.fromEntries(
        results.map((item) => [item.observationId, item.errorType]),
      ) as Record<string, string | null>;

      setErrorTypeByObservationId((prev) => {
        const prevKeys = Object.keys(prev);
        const nextKeys = Object.keys(nextMap);
        if (
          prevKeys.length === nextKeys.length &&
          nextKeys.every((key) => prev[key] === nextMap[key])
        ) {
          return prev;
        }
        return nextMap;
      });
      setIsErrorTypeLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [replaceLevelWithErrorType, errorTypeTargetKey, projectId]);

  const selectedObservationIds = useMemo(() => {
    const rowIds = new Set(observations.rows?.map((o) => o.id));
    return Object.keys(selectedRows).filter((id) => rowIds.has(id));
  }, [observations.rows, selectedRows]);

  const selectedObservationIdSet = useMemo(
    () => new Set(selectedObservationIds),
    [selectedObservationIds],
  );

  const selectedAnalysisTargets = useMemo(
    () =>
      rows
        .filter((row) => selectedObservationIdSet.has(row.id))
        .map((row) => ({
          observationId: row.id,
          traceId: row.traceId,
          level: row.level,
        })),
    [rows, selectedObservationIdSet],
  );

  const exampleObservation = useMemo(() => {
    const firstId = selectedObservationIds[0];
    const firstObs = observations.rows?.find((o) => o.id === firstId);
    return {
      id: firstObs?.id ?? "",
      traceId: firstObs?.traceId ?? "",
      startTime: firstObs?.startTime ?? undefined,
    };
  }, [selectedObservationIds, observations.rows]);

  return (
    <DataTableControlsProvider
      tableName={observationEventsFilterConfig.tableName}
      defaultSidebarCollapsed={defaultSidebarCollapsed}
    >
      <div className="flex h-full w-full flex-col">
        {/* Toolbar spanning full width */}
        {!hideControls && (
          <DataTableToolbar
            columns={effectiveColumns}
            filterState={queryFilter.filterState}
            searchConfig={{
              metadataSearchFields: [
                localize(language, "ID", "ID"),
                localize(language, "Name", "名称"),
                localize(language, "Trace Name", "Trace 名称"),
                localize(language, "Model", "模型"),
              ],
              updateQuery: setSearchQuery,
              currentQuery: searchQuery ?? undefined,
              searchType,
              setSearchType,
              tableAllowsFullTextSearch: true,
            }}
            viewConfig={{
              tableName: TableViewPresetTableName.Observations,
              projectId,
              controllers: viewControllers,
            }}
            preViewActionButtons={
              showBulkAnalysisButton ? (
                <BulkErrorAnalysisButton
                  key="bulk-error-analysis"
                  projectId={projectId}
                  targets={selectedAnalysisTargets}
                  onCompleted={() => {
                    setSelectedRows({});
                    setSelectAll(false);
                  }}
                />
              ) : null
            }
            columnsWithCustomSelect={[
              "providedModelName",
              "name",
              "promptName",
            ]}
            columnVisibility={columnVisibility}
            setColumnVisibility={setColumnVisibilityState}
            columnOrder={columnOrder}
            setColumnOrder={setColumnOrder}
            orderByState={orderByState}
            rowHeight={rowHeight}
            setRowHeight={setRowHeight}
            timeRange={timeRange}
            setTimeRange={setTimeRange}
            viewModeToggle={
              canChangeViewMode ? (
                <EventsViewModeToggle
                  viewMode={viewMode}
                  onViewModeChange={setViewMode}
                />
              ) : null
            }
            refreshConfig={{
              onRefresh: handleRefresh,
              isRefreshing: observations.status === "loading",
              interval: refreshInterval,
              setInterval: setRefreshInterval,
            }}
            actionButtons={[
              <BatchExportTableButton
                {...{
                  projectId,
                  filterState,
                  orderByState,
                  searchQuery,
                  searchType,
                }}
                tableName={BatchExportTableName.Events}
                key="batchExport"
              />,
              selectedObservationIds.length > 0 ? (
                <TableActionMenu
                  key="observations-multi-select-actions"
                  projectId={projectId}
                  actions={tableActions}
                  tableName={BatchExportTableName.Observations}
                  onCustomAction={(actionType) => {
                    if (actionType === ActionId.ObservationBatchEvaluation) {
                      setShowRunEvaluationDialog(true);
                    }
                    if (actionType === ActionId.ObservationAddToDataset) {
                      setShowAddToDatasetDialog(true);
                    }
                  }}
                />
              ) : null,
            ]}
            multiSelect={{
              selectAll,
              setSelectAll,
              selectedRowIds: selectedObservationIds,
              setRowSelection: setSelectedRows,
              totalCount,
              pageSize: paginationState.limit,
              pageIndex: paginationState.page - 1,
            }}
            filterWithAI
          />
        )}

        {/* Content area with sidebar and table */}
        <ResizableFilterLayout>
          {!hideControls && (
            <DataTableControls queryFilter={queryFilter} filterWithAI />
          )}

          <div className="flex flex-1 flex-col overflow-hidden">
            <DataTable
              key={`observations-table-${dataUpdatedAt}-${rows.length > 0 && rows[0]?.input ? "with-io" : "without-io"}`}
              tableName={"observations"}
              columns={effectiveColumns}
              peekView={peekConfig}
              data={
                observations.status === "loading" || isViewLoading
                  ? { isLoading: true, isError: false }
                  : observations.status === "error"
                    ? isSilencedError
                      ? {
                          isLoading: false,
                          isError: false,
                          data: [],
                        }
                      : {
                          isLoading: false,
                          isError: true,
                          error: "",
                        }
                    : {
                        isLoading: false,
                        isError: false,
                        data: rows,
                      }
              }
              noResultsMessage={
                isSilencedError ? (
                  <span className="text-muted-foreground">
                    {RESOURCE_LIMIT_ERROR_MESSAGE}
                  </span>
                ) : undefined
              }
              pagination={
                limitRows
                  ? undefined
                  : {
                      totalCount,
                      onChange: (updater) => {
                        const newState =
                          typeof updater === "function"
                            ? updater({
                                pageIndex: paginationState.page - 1,
                                pageSize: paginationState.limit,
                              })
                            : updater;
                        setPaginationState({
                          page: newState.pageIndex + 1,
                          limit: newState.pageSize,
                        });
                      },
                      state: {
                        pageIndex: paginationState.page - 1,
                        pageSize: paginationState.limit,
                      },
                    }
              }
              rowSelection={selectedRows}
              setRowSelection={setSelectedRows}
              setOrderBy={setOrderByState}
              orderBy={orderByState}
              columnOrder={columnOrder}
              onColumnOrderChange={setColumnOrder}
              columnVisibility={columnVisibility}
              onColumnVisibilityChange={setColumnVisibilityState}
              rowHeight={rowHeight}
              onRowClick={(row, event) => {
                // Handle Command/Ctrl+click to open observation in new tab
                if (event && (event.metaKey || event.ctrlKey)) {
                  // Prevent the default peek behavior
                  event.preventDefault();

                  // Construct the observation URL directly to avoid race conditions
                  const observationId = row.id;
                  const traceId = row.traceId;
                  const timestamp = row.timestamp;

                  if (traceId) {
                    let observationUrl = `/project/${projectId}/traces/${encodeURIComponent(traceId)}`;

                    const params = new URLSearchParams();
                    params.set("observation", observationId);
                    if (timestamp) {
                      params.set("timestamp", timestamp.toISOString());
                    }

                    observationUrl += `?${params.toString()}`;

                    const fullUrl = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}${observationUrl}`;
                    window.open(fullUrl, "_blank");
                  }
                }
                // For normal clicks, let the data-table handle opening the peek view
              }}
            />
          </div>
        </ResizableFilterLayout>
        {peekConfig && <TablePeekView peekView={peekConfig} />}
      </div>

      {showRunEvaluationDialog && (
        <RunEvaluationDialog
          projectId={projectId}
          selectedObservationIds={selectedObservationIds}
          query={{
            filter: filterState,
            orderBy: orderByState,
            searchQuery: searchQuery ?? undefined,
            searchType,
          }}
          selectAll={selectAll}
          totalCount={totalCount ?? 0}
          onClose={() => {
            setShowRunEvaluationDialog(false);
            setSelectedRows({});
            setSelectAll(false);
          }}
          exampleObservation={exampleObservation}
        />
      )}

      {showAddToDatasetDialog && (
        <AddObservationsToDatasetDialog
          projectId={projectId}
          selectedObservationIds={selectedObservationIds}
          query={{
            filter: filterState,
            orderBy: orderByState,
            searchQuery: searchQuery ?? undefined,
            searchType,
          }}
          selectAll={selectAll}
          totalCount={totalCount ?? 0}
          onClose={() => {
            setShowAddToDatasetDialog(false);
            setSelectedRows({});
            setSelectAll(false);
          }}
          exampleObservation={exampleObservation}
        />
      )}
    </DataTableControlsProvider>
  );
}
