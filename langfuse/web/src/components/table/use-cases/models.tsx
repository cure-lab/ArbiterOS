import { DataTable } from "@/src/components/table/data-table";
import { type LangfuseColumnDef } from "@/src/components/table/types";
import useColumnVisibility from "@/src/features/column-visibility/hooks/useColumnVisibility";
import { api } from "@/src/utils/api";
import { safeExtract } from "@/src/utils/map-utils";
import { type Prisma } from "@langfuse/shared/src/db";
import { useQueryParams, withDefault, StringParam } from "use-query-params";
import { usePaginationState } from "@/src/hooks/usePaginationState";
import { IOTableCell } from "../../ui/IOTableCell";
import { useRowHeightLocalStorage } from "@/src/components/table/data-table-row-height-switch";
import { DataTableToolbar } from "@/src/components/table/data-table-toolbar";
import useColumnOrder from "@/src/features/column-visibility/hooks/useColumnOrder";
import { type GetModelResult } from "@/src/features/models/validation";
import { DeleteModelButton } from "@/src/features/models/components/DeleteModelButton";
import { EditModelButton } from "@/src/features/models/components/EditModelButton";
import { CloneModelButton } from "@/src/features/models/components/CloneModelButton";
import { PriceBreakdownTooltip } from "@/src/features/models/components/PriceBreakdownTooltip";
import { UserCircle2Icon, PlusIcon } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/src/components/ui/tooltip";
import { Skeleton } from "@/src/components/ui/skeleton";
import { LangfuseIcon } from "@/src/components/LangfuseLogo";
import { useRouter } from "next/router";
import { PriceUnitSelector } from "@/src/features/models/components/PriceUnitSelector";
import { usePriceUnitMultiplier } from "@/src/features/models/hooks/usePriceUnitMultiplier";
import { UpsertModelFormDialog } from "@/src/features/models/components/UpsertModelFormDialog";
import { TestModelMatchButton } from "@/src/features/models/components/test-match/TestModelMatchButton";
import { ActionButton } from "@/src/components/ActionButton";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { SettingsTableCard } from "@/src/components/layouts/settings-table-card";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";
export type ModelTableRow = {
  modelId: string;
  maintainer: string;
  modelName: string;
  matchPattern: string;
  prices?: Record<string, number>;
  tokenizerId?: string;
  config?: Prisma.JsonValue;
  serverResponse: GetModelResult;
};

export default function ModelTable({ projectId }: { projectId: string }) {
  const router = useRouter();
  const capture = usePostHogClientCapture();
  const { language } = useLanguage();
  const [paginationState, setPaginationState] = usePaginationState(0, 50, {
    page: "pageIndex",
    limit: "pageSize",
  });
  const [queryParams, setQueryParams] = useQueryParams({
    search: withDefault(StringParam, ""),
  });
  const searchString = queryParams.search;
  const models = api.models.getAll.useQuery(
    {
      page: paginationState.pageIndex,
      limit: paginationState.pageSize,
      projectId,
      searchString,
    },
    {
      refetchOnWindowFocus: false,
      refetchOnMount: true,
      refetchOnReconnect: false,
      staleTime: 1000 * 60 * 10,
    },
  );
  const totalCount = models.data?.totalCount ?? null;

  const modelIds = models.data?.models.map((m) => m.id) ?? [];
  const lastUsed = api.models.lastUsedByModelIds.useQuery(
    { projectId, modelIds },
    {
      enabled: models.isSuccess && modelIds.length > 0,
      refetchOnWindowFocus: false,
      refetchOnMount: true,
      refetchOnReconnect: false,
      staleTime: 1000 * 60 * 10,
    },
  );
  const { priceUnit } = usePriceUnitMultiplier();
  const [rowHeight, setRowHeight] = useRowHeightLocalStorage("models", "m");

  const hasWriteAccess = useHasProjectAccess({
    projectId,
    scope: "models:CUD",
  });
  const localizedPriceUnit = (() => {
    switch (priceUnit) {
      case "per unit":
        return localize(language, "per unit", "按单位");
      case "per 1K units":
        return localize(language, "per 1K units", "每 1K 单位");
      case "per 1M units":
        return localize(language, "per 1M units", "每 1M 单位");
      default:
        return priceUnit;
    }
  })();
  const modelConfigDescriptions = {
    modelName: localize(
      language,
      "Standardized model name. Generations are assigned to this model name if they match the `matchPattern` upon ingestion.",
      "标准化模型名称。摄取时如果 generation 匹配 `matchPattern`，则会被归入该模型名称。",
    ),
    matchPattern: localize(
      language,
      "Regex pattern to match `model` parameter of generations to model pricing",
      "用于将 generation 的 `model` 参数匹配到模型定价的正则表达式",
    ),
    startDate: localize(
      language,
      "Date to start pricing model. If not set, model is active unless a more recent version exists.",
      "模型定价开始生效的日期。若未设置，则除非存在更新版本，否则模型始终有效。",
    ),
    prices: localize(language, "Prices per usage type", "按用量类型计价"),
    tokenizerId: localize(
      language,
      "Tokenizer used for this model to calculate token counts if none are ingested. Pick from list of supported tokenizers.",
      "当未摄取 token 计数时，用于计算该模型 token 数量的 tokenizer。请从支持的 tokenizer 列表中选择。",
    ),
    config: localize(
      language,
      "Some tokenizers require additional configuration (e.g. openai tiktoken). See docs for details.",
      "部分 tokenizer 需要额外配置（例如 OpenAI tiktoken）。详情请参阅文档。",
    ),
    maintainer: localize(
      language,
      "Maintainer of the model. Langfuse managed models can be cloned, user managed models can be edited and deleted. To supersede a Langfuse managed model, set the custom model name to the Langfuse model name.",
      "模型维护者。Langfuse 维护的模型可被克隆，用户维护的模型可被编辑和删除。若要覆盖 Langfuse 维护的模型，请将自定义模型名称设置为对应的 Langfuse 模型名称。",
    ),
    lastUsed: localize(
      language,
      "Start time of the latest generation using this model",
      "最近一次使用该模型的 generation 开始时间",
    ),
  } as const;

  const columns: LangfuseColumnDef<ModelTableRow>[] = [
    {
      accessorKey: "modelName",
      id: "modelName",
      header: localize(language, "Model Name", "模型名称"),
      headerTooltip: {
        description: modelConfigDescriptions.modelName,
      },
      cell: ({ row }) => {
        return (
          <span className="truncate font-mono text-xs font-semibold">
            {row.original.modelName}
          </span>
        );
      },
      size: 120,
    },
    {
      accessorKey: "maintainer",
      id: "maintainer",
      header: localize(language, "Maintainer", "维护者"),
      headerTooltip: {
        description: modelConfigDescriptions.maintainer,
      },
      size: 60,
      cell: ({ row }) => {
        const isLangfuse = row.original.maintainer === "Langfuse";
        return (
          <div className="flex justify-center">
            <Tooltip>
              <TooltipTrigger>
                {isLangfuse ? (
                  <LangfuseIcon size={16} />
                ) : (
                  <UserCircle2Icon className="h-4 w-4" />
                )}
              </TooltipTrigger>
              <TooltipContent>
                {isLangfuse
                  ? localize(language, "Langfuse maintained", "Langfuse 维护")
                  : localize(language, "User maintained", "用户维护")}
              </TooltipContent>
            </Tooltip>
          </div>
        );
      },
    },
    {
      accessorKey: "matchPattern",
      id: "matchPattern",
      headerTooltip: {
        description: modelConfigDescriptions.matchPattern,
      },
      header: localize(language, "Match Pattern", "匹配模式"),
      size: 200,
      cell: ({ row }) => {
        const value: string = row.getValue("matchPattern");

        return value ? (
          <span className="truncate font-mono text-xs">{value}</span>
        ) : null;
      },
    },
    {
      accessorKey: "prices",
      id: "prices",
      header: () => {
        return (
          <div className="flex items-center gap-2">
            <span>
              {localize(language, "Prices", "价格")} {localizedPriceUnit}
            </span>
            <PriceUnitSelector />
          </div>
        );
      },
      size: 120,
      cell: ({ row }) => {
        const prices: Record<string, number> | undefined =
          row.getValue("prices");

        return (
          <PriceBreakdownTooltip
            modelName={row.original.modelName}
            prices={prices}
            priceUnit={priceUnit}
            rowHeight={rowHeight}
          />
        );
      },
      enableHiding: true,
    },
    {
      accessorKey: "tokenizerId",
      id: "tokenizerId",
      header: localize(language, "Tokenizer", "Tokenizer"),
      headerTooltip: {
        description: modelConfigDescriptions.tokenizerId,
      },
      enableHiding: true,
      size: 120,
    },
    {
      accessorKey: "config",
      id: "config",
      header: localize(language, "Tokenizer Configuration", "Tokenizer 配置"),
      headerTooltip: {
        description: modelConfigDescriptions.config,
      },
      enableHiding: true,
      size: 120,
      cell: ({ row }) => {
        const value: Prisma.JsonValue | undefined = row.getValue("config");

        return value ? (
          <IOTableCell data={value} singleLine={rowHeight === "s"} />
        ) : null;
      },
    },
    {
      accessorKey: "lastUsed",
      id: "lastUsed",
      header: localize(language, "Last used", "最近使用"),
      headerTooltip: {
        description: modelConfigDescriptions.lastUsed,
      },
      enableHiding: true,
      size: 120,
      cell: ({ row }) => {
        if (!lastUsed.data) return <Skeleton className="h-4 w-20" />;
        const value = lastUsed.data[row.original.modelId];
        return value?.toLocaleString() ?? "";
      },
    },
    {
      accessorKey: "actions",
      header: localize(language, "Actions", "操作"),
      size: 120,
      cell: ({ row }) => {
        return row.original.maintainer !== "Langfuse" ? (
          <div
            className="flex items-center gap-2"
            onClick={(e) => e.stopPropagation()}
          >
            <EditModelButton
              projectId={projectId}
              modelData={row.original.serverResponse}
            />
            <DeleteModelButton
              projectId={projectId}
              modelData={row.original.serverResponse}
            />
          </div>
        ) : (
          <div onClick={(e) => e.stopPropagation()}>
            <CloneModelButton
              projectId={projectId}
              modelData={row.original.serverResponse}
            />
          </div>
        );
      },
    },
  ];

  const [columnVisibility, setColumnVisibility] =
    useColumnVisibility<ModelTableRow>("modelsColumnVisibility", columns);

  const [columnOrder, setColumnOrder] = useColumnOrder<ModelTableRow>(
    "modelsColumnOrder",
    columns,
  );

  const convertToTableRow = (model: GetModelResult): ModelTableRow => {
    // Get default tier prices for backward compatibility
    const defaultTier = model.pricingTiers.find((t) => t.isDefault);
    const prices = defaultTier?.prices;

    return {
      modelId: model.id,
      maintainer: model.projectId ? "User" : "Langfuse",
      modelName: model.modelName,
      matchPattern: model.matchPattern,
      prices,
      tokenizerId: model.tokenizerId ?? undefined,
      config: model.tokenizerConfig,
      serverResponse: model,
    };
  };

  return (
    <>
      <DataTableToolbar
        columns={columns}
        columnVisibility={columnVisibility}
        setColumnVisibility={setColumnVisibility}
        columnOrder={columnOrder}
        setColumnOrder={setColumnOrder}
        rowHeight={rowHeight}
        setRowHeight={setRowHeight}
        searchConfig={{
          updateQuery: (event: string) => {
            setQueryParams({ search: event });
          },
          tableAllowsFullTextSearch: true,
          currentQuery: searchString,
        }}
        actionButtons={
          <>
            <TestModelMatchButton projectId={projectId} />
            <UpsertModelFormDialog {...{ projectId, action: "create" }}>
              <ActionButton
                variant="secondary"
                icon={<PlusIcon className="h-4 w-4" />}
                hasAccess={hasWriteAccess}
                onClick={() => capture("models:new_form_open")}
              >
                {localize(language, "Add Model Definition", "添加模型定义")}
              </ActionButton>
            </UpsertModelFormDialog>
          </>
        }
        className="px-0"
      />
      <SettingsTableCard className="max-h-[75dvh]">
        <DataTable
          tableName={"models"}
          columns={columns}
          data={
            models.isPending
              ? { isLoading: true, isError: false }
              : models.isError
                ? {
                    isLoading: false,
                    isError: true,
                    error: models.error.message,
                  }
                : {
                    isLoading: false,
                    isError: false,
                    data: safeExtract(models.data, "models", []).map((t) =>
                      convertToTableRow(t),
                    ),
                  }
          }
          pagination={{
            totalCount,
            onChange: setPaginationState,
            state: paginationState,
          }}
          columnVisibility={columnVisibility}
          onColumnVisibilityChange={setColumnVisibility}
          columnOrder={columnOrder}
          onColumnOrderChange={setColumnOrder}
          rowHeight={rowHeight}
          onRowClick={(row) => {
            router.push(`/project/${projectId}/settings/models/${row.modelId}`);
          }}
        />
      </SettingsTableCard>
    </>
  );
}
