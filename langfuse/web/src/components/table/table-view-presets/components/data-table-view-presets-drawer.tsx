import { Button } from "@/src/components/ui/button";
import {
  X,
  Plus,
  ChevronDown,
  Link,
  MoreVertical,
  Pen,
  Lock,
} from "lucide-react";
import { Badge } from "@/src/components/ui/badge";
import { LangfuseIcon } from "@/src/components/LangfuseLogo";
import {
  DrawerTrigger,
  DrawerContent,
  DrawerHeader,
  DrawerTitle,
  Drawer,
  DrawerClose,
} from "@/src/components/ui/drawer";
import { Separator } from "@/src/components/ui/separator";
import { useViewData } from "@/src/components/table/table-view-presets/hooks/useViewData";
import {
  Command,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
} from "@/src/components/ui/command";
import { useViewMutations } from "@/src/components/table/table-view-presets/hooks/useViewMutations";
import { cn } from "@/src/utils/tailwind";
import {
  Avatar,
  AvatarFallback,
  AvatarImage,
} from "@/src/components/ui/avatar";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogBody,
} from "@/src/components/ui/dialog";
import { Input } from "@/src/components/ui/input";
import {
  type VisibilityState,
  type ColumnOrderState,
} from "@tanstack/react-table";
import {
  type OrderByState,
  type FilterState,
  type TableViewPresetTableName,
  type TableViewPresetDomain,
} from "@langfuse/shared";
import { useCallback, useMemo, useState } from "react";
import {
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/src/components/ui/dropdown-menu";
import { DropdownMenu } from "@/src/components/ui/dropdown-menu";
import { DropdownMenuContent } from "@/src/components/ui/dropdown-menu";
import { DeleteButton } from "@/src/components/deleteButton";
import { api } from "@/src/utils/api";
import { Popover, PopoverContent } from "@/src/components/ui/popover";
import { PopoverTrigger } from "@/src/components/ui/popover";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/src/components/ui/form";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod/v4";
import { showErrorToast } from "@/src/features/notifications/showErrorToast";
import { useUniqueNameValidation } from "@/src/hooks/useUniqueNameValidation";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import isEqual from "lodash/isEqual";
import { useDefaultViewMutations } from "../hooks/useDefaultViewMutations";
import { DropdownMenuSeparator } from "@/src/components/ui/dropdown-menu";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

/**
 * Prefix for system preset IDs. These are page-specific presets defined in code
 * (not stored in DB). Using this prefix prevents DB lookups and allows special handling.
 * Convention: `__langfuse_{preset_name}__`
 */
export const SYSTEM_PRESET_ID_PREFIX = "__langfuse_";

/** Check if a view ID is a system preset (defined in code, not stored in DB) */
export const isSystemPresetId = (id: string | undefined | null): boolean =>
  !!id?.startsWith(SYSTEM_PRESET_ID_PREFIX);

/** Recursively remove undefined values for consistent comparison */
function normalizeForComparison<T>(obj: T): T {
  if (Array.isArray(obj)) {
    return obj.map(normalizeForComparison) as T;
  }
  if (obj !== null && typeof obj === "object") {
    return Object.fromEntries(
      Object.entries(obj)
        .filter(([, v]) => v !== undefined)
        .map(([k, v]) => [k, normalizeForComparison(v)]),
    ) as T;
  }
  return obj;
}

interface SystemPreset {
  id: string;
  name: string;
  isSystem: true;
}

const SYSTEM_PRESETS: { DEFAULT: SystemPreset } = {
  DEFAULT: {
    id: "__langfuse_default__",
    name: "My view (default)",
    isSystem: true,
  },
};

export interface SystemFilterPreset {
  id: string;
  name: string;
  description?: string;
  filters: FilterState;
}

interface TableViewPresetsDrawerProps {
  viewConfig: {
    tableName: TableViewPresetTableName;
    projectId: string;
    controllers: {
      selectedViewId: string | null;
      handleSetViewId: (viewId: string | null) => void;
      applyViewState: (viewData: TableViewPresetDomain) => void;
    };
  };
  currentState: {
    orderBy: OrderByState;
    filters: FilterState;
    columnOrder: ColumnOrderState;
    columnVisibility: VisibilityState;
    searchQuery: string;
  };
  /** Page-specific system filter presets (e.g. "Last Generation in Trace") */
  systemFilterPresets?: SystemFilterPreset[];
}

function formatOrderBy(orderBy?: OrderByState) {
  return orderBy?.column ? `${orderBy.column} ${orderBy.order}` : "none";
}

export function TableViewPresetsDrawer({
  viewConfig,
  currentState,
  systemFilterPresets,
}: TableViewPresetsDrawerProps) {
  const { language } = useLanguage();
  const [searchQuery, setSearchQueryLocal] = useState("");
  const { tableName, projectId, controllers } = viewConfig;
  const { handleSetViewId, applyViewState, selectedViewId } = controllers;
  const { TableViewPresetsList } = useViewData({ tableName, projectId });
  const {
    createMutation,
    updateConfigMutation,
    updateNameMutation,
    deleteMutation,
    generatePermalinkMutation,
  } = useViewMutations({ handleSetViewId });
  const utils = api.useUtils();
  const capture = usePostHogClientCapture();

  const form = useForm({
    resolver: zodResolver(z.object({ name: z.string().min(1) })),
    defaultValues: {
      name: "",
    },
  });

  const hasWriteAccess = useHasProjectAccess({
    projectId,
    scope: "TableViewPresets:CUD",
  });

  const { data: currentDefault } = api.TableViewPresets.getDefault.useQuery(
    { projectId, viewName: tableName },
    { enabled: !!projectId },
  );

  const { setViewAsDefault, clearViewDefault, isSettingDefault } =
    useDefaultViewMutations({ tableName, projectId });

  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [isEditPopoverOpen, setIsEditPopoverOpen] = useState<boolean>(false);
  const [dropdownId, setDropdownId] = useState<string | null>(null);

  const selectedViewName = useMemo(() => {
    // Check system filter presets first
    const systemPreset = systemFilterPresets?.find(
      (p) => p.id === selectedViewId,
    );
    if (systemPreset) {
      // Normalize both to handle missing vs undefined property mismatch
      const normalizedCurrent = normalizeForComparison(currentState.filters);
      const normalizedPreset = normalizeForComparison(systemPreset.filters);
      // If filters have been modified from the preset, show "Saved Views" instead
      if (!isEqual(normalizedCurrent, normalizedPreset)) {
        return undefined;
      }
      return systemPreset.id === SYSTEM_PRESETS.DEFAULT.id
        ? localize(language, "My view (default)", "我的视图（默认）")
        : systemPreset.name;
    }
    // Then check user presets
    return TableViewPresetsList?.find((v) => v.id === selectedViewId)?.name;
  }, [
    language,
    selectedViewId,
    systemFilterPresets,
    TableViewPresetsList,
    currentState.filters,
  ]);

  const allViewNames = useMemo(
    () => TableViewPresetsList?.map((view) => ({ value: view.name })) ?? [],
    [TableViewPresetsList],
  );

  useUniqueNameValidation({
    currentName: form.watch("name"),
    allNames: allViewNames,
    form,
    errorMessage: localize(
      language,
      "View name already exists.",
      "视图名称已存在。",
    ),
  });

  const handleSelectView = async (viewId: string) => {
    // Handle system preset - just select it like any view
    if (viewId === SYSTEM_PRESETS.DEFAULT.id) {
      handleSetViewId(null);
      return;
    }

    capture("saved_views:view_selected", {
      tableName,
      viewId,
    });

    handleSetViewId(viewId);
    try {
      const fetchedViewData = await utils.TableViewPresets.getById.fetch({
        projectId,
        viewId,
      });

      if (fetchedViewData) {
        applyViewState(fetchedViewData);
      }
    } catch {
      showErrorToast(
        "Failed to apply view selection",
        "Please try again",
        "WARNING",
      );
    }
  };

  const handleSelectSystemFilterPreset = useCallback(
    (preset: SystemFilterPreset) => {
      capture("saved_views:system_preset_selected", {
        tableName,
        presetId: preset.id,
      });
      handleSetViewId(preset.id);
      applyViewState({
        id: preset.id,
        name: preset.name,
        filters: preset.filters,
        columnOrder: [],
        columnVisibility: {},
        orderBy: null,
        searchQuery: "",
        tableName,
        projectId,
        createdAt: new Date(),
        updatedAt: new Date(),
        createdBy: "",
        createdByUser: null,
      } as TableViewPresetDomain);
    },
    [capture, tableName, handleSetViewId, applyViewState, projectId],
  );

  const handleCreateView = (createdView: { name: string }) => {
    capture("saved_views:create", {
      tableName,
      name: createdView.name,
    });

    createMutation.mutate({
      name: createdView.name,
      tableName,
      projectId,
      orderBy: currentState.orderBy,
      filters: currentState.filters,
      columnOrder: currentState.columnOrder,
      columnVisibility: currentState.columnVisibility,
      searchQuery: currentState.searchQuery,
    });

    setIsCreateDialogOpen(false);
  };

  const handleUpdateViewConfig = (updatedView: { name: string }) => {
    if (!selectedViewId) return;

    capture("saved_views:update_config", {
      tableName,
      viewId: selectedViewId,
      name: updatedView.name,
    });

    updateConfigMutation.mutate({
      projectId,
      name: updatedView.name,
      id: selectedViewId,
      tableName,
      orderBy: currentState.orderBy,
      filters: currentState.filters,
      columnOrder: currentState.columnOrder,
      columnVisibility: currentState.columnVisibility,
      searchQuery: currentState.searchQuery,
    });
  };

  const handleUpdateViewName = (updatedView: { id: string; name: string }) => {
    capture("saved_views:update_name", {
      tableName,
      viewId: updatedView.id,
      name: updatedView.name,
    });

    updateNameMutation.mutate({
      id: updatedView.id,
      name: updatedView.name,
      tableName,
      projectId,
    });
  };

  const onSubmit = (id?: string) => (data: { name: string }) => {
    if (id) {
      handleUpdateViewName({ id, name: data.name });
      setIsEditPopoverOpen(false);
      setDropdownId(null);
    } else {
      handleCreateView({ name: data.name });
    }
  };

  const handleDeleteView = async (viewId: string) => {
    capture("saved_views:delete", {
      tableName,
      viewId,
    });

    await deleteMutation.mutateAsync({
      projectId,
      tableViewPresetsId: viewId,
    });
  };

  const handleGeneratePermalink = (viewId: string) => {
    capture("saved_views:permalink_generate", {
      tableName,
      viewId,
    });

    if (window.location.origin) {
      generatePermalinkMutation.mutate({
        viewId,
        projectId,
        tableName,
        baseUrl: window.location.origin,
      });
    } else {
      showErrorToast(
        localize(language, "Failed to generate permalink", "生成永久链接失败"),
        localize(
          language,
          "Please reach out to langfuse support and report this issue.",
          "请联系 langfuse 支持并报告此问题。",
        ),
        "WARNING",
      );
    }
  };

  return (
    <>
      <Drawer
        onOpenChange={(open) => {
          if (open) {
            capture("saved_views:drawer_open", { tableName });
          } else {
            capture("saved_views:drawer_close", { tableName });
          }
        }}
      >
        <DrawerTrigger asChild>
          <Button
            variant="outline"
            title={
              selectedViewName
                ? localize(
                    language,
                    `View: ${selectedViewName}`,
                    `视图：${selectedViewName}`,
                  )
                : localize(language, "Saved Views", "已保存视图")
            }
          >
            <span>
              {selectedViewName
                ? localize(
                    language,
                    `View: ${selectedViewName}`,
                    `视图：${selectedViewName}`,
                  )
                : localize(language, "Saved Views", "已保存视图")}
            </span>
            {selectedViewId ? (
              <ChevronDown className="ml-1 h-4 w-4" />
            ) : (
              <div className="ml-1 rounded-sm bg-input px-1 text-xs">
                {TableViewPresetsList?.length ?? 0}
              </div>
            )}
          </Button>
        </DrawerTrigger>
        <DrawerContent overlayClassName="bg-primary/10">
          <div className="mx-auto w-full">
            <DrawerHeader className="flex flex-row items-center justify-between rounded-sm bg-background px-3 py-1.5">
              <DrawerTitle className="flex flex-row items-center gap-1">
                {localize(language, "Saved Views", "已保存视图")}{" "}
                <a
                  href="https://github.com/orgs/langfuse/discussions/4657"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center"
                  title={localize(
                    language,
                    "Saving table view presets is currently in beta. Click here to provide feedback!",
                    "保存表格视图预设当前处于测试阶段。点击此处提供反馈！",
                  )}
                ></a>
              </DrawerTitle>
              <DrawerClose asChild>
                <Button variant="outline" size="icon">
                  <X className="h-4 w-4" />
                </Button>
              </DrawerClose>
            </DrawerHeader>
            <Separator />

            <Command className="h-fit rounded-none border-none pb-1 shadow-none">
              <CommandInput
                placeholder={localize(
                  language,
                  "Search saved views...",
                  "搜索已保存视图...",
                )}
                value={searchQuery}
                onValueChange={setSearchQueryLocal}
                className="h-9 border-none focus:ring-0"
              />
              <CommandList className="max-h-[calc(100vh-150px)]">
                <CommandEmpty>
                  {localize(
                    language,
                    "No saved views found",
                    "未找到已保存视图",
                  )}
                </CommandEmpty>
                <CommandGroup className="pb-0">
                  {/* System Preset: Langfuse Default - hidden when page-specific presets exist */}
                  {!systemFilterPresets?.length && (
                    <CommandItem
                      key={SYSTEM_PRESETS.DEFAULT.id}
                      onSelect={() =>
                        handleSelectView(SYSTEM_PRESETS.DEFAULT.id)
                      }
                      className={cn(
                        "group mt-1 flex cursor-pointer items-center justify-between rounded-md p-2 transition-colors hover:bg-muted/50",
                        selectedViewId === null && "bg-muted",
                      )}
                      title={localize(
                        language,
                        "Reflects your current table settings without applying any saved custom table views",
                        "反映当前表格设置，不会应用任何已保存的自定义视图",
                      )}
                    >
                      <div className="flex flex-col">
                        <span className="text-sm text-muted-foreground">
                          {localize(
                            language,
                            SYSTEM_PRESETS.DEFAULT.name,
                            "我的视图（默认）",
                          )}
                        </span>
                        <span className="w-fit pl-0 text-xs text-muted-foreground">
                          {localize(
                            language,
                            "Your working view",
                            "你当前的工作视图",
                          )}
                        </span>
                      </div>
                    </CommandItem>
                  )}

                  {/* Page-specific System Filter Presets */}
                  {systemFilterPresets?.map((preset) => (
                    <CommandItem
                      key={preset.id}
                      onSelect={() => handleSelectSystemFilterPreset(preset)}
                      className={cn(
                        "group mt-1 flex cursor-pointer items-center justify-between rounded-md p-2 transition-colors hover:bg-muted/50",
                        selectedViewId === preset.id &&
                          isEqual(
                            normalizeForComparison(currentState.filters),
                            normalizeForComparison(preset.filters),
                          ) &&
                          "bg-muted",
                      )}
                    >
                      <div className="flex flex-col">
                        <span className="flex items-center gap-1.5 text-sm">
                          <LangfuseIcon size={14} />
                          {preset.name}
                        </span>
                        {preset.description && (
                          <span className="w-fit pl-0 text-xs text-muted-foreground">
                            {preset.description}
                          </span>
                        )}
                      </div>
                    </CommandItem>
                  ))}

                  {/* Separator between system and user presets */}
                  {systemFilterPresets?.length &&
                  TableViewPresetsList?.length ? (
                    <Separator className="my-2" />
                  ) : null}

                  {/* User Presets */}
                  {TableViewPresetsList?.map((view) => {
                    const isUserDefault =
                      currentDefault?.viewId === view.id &&
                      currentDefault?.scope === "user";
                    const isProjectDefault =
                      currentDefault?.viewId === view.id &&
                      currentDefault?.scope === "project";

                    return (
                      <CommandItem
                        key={view.id}
                        onSelect={() => handleSelectView(view.id)}
                        className={cn(
                          "group mt-1 flex cursor-pointer items-center justify-between rounded-md p-2 transition-colors hover:bg-muted/50",
                          selectedViewId === view.id && "bg-muted",
                        )}
                      >
                        <div className="flex flex-col">
                          <div className="flex items-center gap-2">
                            <span className="text-sm">{view.name}</span>
                            {isUserDefault && (
                              <Badge variant="secondary" className="text-xs">
                                {localize(
                                  language,
                                  "Your default",
                                  "你的默认值",
                                )}
                              </Badge>
                            )}
                            {isProjectDefault && (
                              <Badge variant="outline" className="text-xs">
                                {localize(
                                  language,
                                  "Project default",
                                  "项目默认值",
                                )}
                              </Badge>
                            )}
                          </div>
                          {view.id === selectedViewId && (
                            <Button
                              variant="ghost"
                              size="xs"
                              className={cn(
                                "w-fit pl-0 text-xs",
                                hasWriteAccess
                                  ? "text-primary-accent"
                                  : "text-muted-foreground",
                              )}
                              onClick={(e) => {
                                e.stopPropagation();
                                handleUpdateViewConfig({
                                  name: view.name,
                                });
                              }}
                              disabled={!hasWriteAccess}
                            >
                              {localize(
                                language,
                                "Update view with current filters",
                                "用当前筛选条件更新视图",
                              )}
                            </Button>
                          )}
                        </div>
                        <div className="flex flex-row gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleGeneratePermalink(view.id);
                            }}
                            className="w-4 opacity-0 group-hover:opacity-100 peer-data-[state=open]:opacity-100"
                          >
                            <Link className="h-4 w-4" />
                          </Button>
                          <DropdownMenu
                            open={dropdownId === view.id}
                            onOpenChange={(open) => {
                              setDropdownId(open ? view.id : null);
                            }}
                          >
                            <DropdownMenuTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                onClick={(e) => {
                                  e.stopPropagation();
                                }}
                                className="opacity-0 group-hover:opacity-100 data-[state=open]:opacity-100"
                              >
                                <MoreVertical className="h-4 w-4" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent className="flex flex-col [&>*]:w-full [&>*]:justify-start">
                              <DropdownMenuItem asChild>
                                <Popover
                                  key={view.id + "-edit"}
                                  open={isEditPopoverOpen}
                                  onOpenChange={(open) => {
                                    setIsEditPopoverOpen(open);
                                    if (open) {
                                      form.reset({ name: view.name });
                                      capture("saved_views:update_form_open", {
                                        tableName,
                                        viewId: view.id,
                                      });
                                    } else {
                                      setDropdownId(null);
                                    }
                                  }}
                                >
                                  <PopoverTrigger asChild>
                                    <Button
                                      variant="ghost"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                      }}
                                      disabled={!hasWriteAccess}
                                    >
                                      {hasWriteAccess ? (
                                        <Pen className="mr-2 h-4 w-4" />
                                      ) : (
                                        <Lock className="mr-2 h-4 w-4" />
                                      )}
                                      {localize(language, "Rename", "重命名")}
                                    </Button>
                                  </PopoverTrigger>
                                  <PopoverContent
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    <h2 className="text-md mb-3 font-semibold">
                                      {localize(language, "Edit", "编辑")}
                                    </h2>
                                    <Form {...form}>
                                      <form
                                        onSubmit={form.handleSubmit(
                                          onSubmit(view.id),
                                        )}
                                        className="space-y-2"
                                      >
                                        <FormField
                                          control={form.control}
                                          name="name"
                                          render={({ field }) => (
                                            <FormItem>
                                              <FormLabel>
                                                {localize(
                                                  language,
                                                  "View name",
                                                  "视图名称",
                                                )}
                                              </FormLabel>
                                              <FormControl>
                                                <Input
                                                  defaultValue={view.name}
                                                  {...field}
                                                />
                                              </FormControl>
                                              <FormMessage />
                                            </FormItem>
                                          )}
                                        />

                                        <div className="flex w-full justify-end">
                                          <Button
                                            type="submit"
                                            loading={
                                              updateNameMutation.isPending
                                            }
                                            disabled={
                                              !!form.formState.errors.name
                                            }
                                          >
                                            {localize(language, "Save", "保存")}
                                          </Button>
                                        </div>
                                      </form>
                                    </Form>
                                  </PopoverContent>
                                </Popover>
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              {/* Set as my default */}
                              <DropdownMenuItem
                                onClick={(e) => {
                                  e.stopPropagation();
                                  if (isUserDefault) {
                                    clearViewDefault("user");
                                  } else {
                                    setViewAsDefault(view.id, "user");
                                  }
                                  setDropdownId(null);
                                }}
                                disabled={isSettingDefault}
                              >
                                {isUserDefault ? (
                                  <>
                                    {localize(
                                      language,
                                      "Remove as my default",
                                      "移除我的默认值",
                                    )}
                                  </>
                                ) : (
                                  <>
                                    {localize(
                                      language,
                                      "Set as my default",
                                      "设为我的默认值",
                                    )}
                                  </>
                                )}
                              </DropdownMenuItem>
                              {/* Set as project default - requires write access */}
                              <DropdownMenuItem
                                onClick={(e) => {
                                  e.stopPropagation();
                                  if (isProjectDefault) {
                                    clearViewDefault("project");
                                  } else {
                                    setViewAsDefault(view.id, "project");
                                  }
                                  setDropdownId(null);
                                }}
                                disabled={!hasWriteAccess || isSettingDefault}
                              >
                                {isProjectDefault ? (
                                  <>
                                    {localize(
                                      language,
                                      "Remove as project default",
                                      "移除项目默认值",
                                    )}
                                  </>
                                ) : (
                                  <>
                                    {localize(
                                      language,
                                      "Set as project default",
                                      "设为项目默认值",
                                    )}
                                  </>
                                )}
                                {!hasWriteAccess && (
                                  <Lock className="ml-auto h-4 w-4" />
                                )}
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem asChild>
                                <DeleteButton
                                  itemId={view.id}
                                  projectId={projectId}
                                  scope="TableViewPresets:CUD"
                                  entityToDeleteName={localize(
                                    language,
                                    "saved view",
                                    "已保存视图",
                                  )}
                                  executeDeleteMutation={async () => {
                                    await handleDeleteView(view.id);
                                  }}
                                  isDeleteMutationLoading={
                                    deleteMutation.isPending
                                  }
                                  invalidateFunc={() => {
                                    utils.TableViewPresets.invalidate();
                                  }}
                                  captureDeleteOpen={() =>
                                    capture("saved_views:delete_form_open", {
                                      tableName,
                                      viewId: view.id,
                                    })
                                  }
                                  captureDeleteSuccess={() => {}}
                                />
                              </DropdownMenuItem>
                            </DropdownMenuContent>
                          </DropdownMenu>
                          <div className="flex items-center text-xs text-muted-foreground">
                            <Avatar className="h-6 w-6">
                              <AvatarImage
                                src={view.createdByUser?.image ?? undefined}
                                alt={
                                  view.createdByUser?.name ??
                                  localize(language, "User Avatar", "用户头像")
                                }
                              />
                              <AvatarFallback className="bg-tertiary">
                                {view.createdByUser?.name
                                  ? view.createdByUser?.name
                                      .split(" ")
                                      .map((word) => word[0])
                                      .slice(0, 2)
                                      .concat("")
                                  : null}
                              </AvatarFallback>
                            </Avatar>
                          </div>
                        </div>
                      </CommandItem>
                    );
                  })}
                </CommandGroup>
              </CommandList>
            </Command>

            <Separator />

            <div className="p-2">
              <Button
                onClick={() => {
                  setIsCreateDialogOpen(true);
                  capture("saved_views:create_form_open", { tableName });
                }}
                variant="ghost"
                className="w-full justify-start px-1"
              >
                <Plus className="mr-2 h-4 w-4" />
                {localize(language, "Create Custom View", "创建自定义视图")}
              </Button>
            </div>
          </div>
        </DrawerContent>
      </Drawer>

      {/* Create View Dialog */}
      <Dialog
        open={isCreateDialogOpen}
        onOpenChange={(open) => {
          setIsCreateDialogOpen(open);
          if (!open) {
            form.reset({ name: "" });
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {localize(
                language,
                "Save Current Table View",
                "保存当前表格视图",
              )}
            </DialogTitle>
          </DialogHeader>
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(onSubmit())}
              className="space-y-4"
            >
              <DialogBody>
                <FormField
                  control={form.control}
                  name="name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>
                        {localize(language, "View name", "视图名称")}
                      </FormLabel>
                      <FormControl>
                        <Input {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <div className="mt-4 text-sm text-muted-foreground">
                  <p>
                    {localize(
                      language,
                      "This will save the current:",
                      "这将保存当前的：",
                    )}
                  </p>
                  <ul className="mt-2 list-disc pl-5">
                    <li>
                      {localize(
                        language,
                        `Column arrangement (${currentState.columnOrder.length} columns)`,
                        `列布局（${currentState.columnOrder.length} 列）`,
                      )}
                    </li>
                    <li>
                      {localize(
                        language,
                        `Filters (${currentState.filters.length} active)`,
                        `筛选条件（${currentState.filters.length} 个已激活）`,
                      )}
                    </li>
                    <li>
                      {localize(
                        language,
                        `Sort order (${formatOrderBy(currentState.orderBy)} criteria)`,
                        `排序方式（${formatOrderBy(currentState.orderBy)} 条规则）`,
                      )}
                    </li>
                    {currentState.searchQuery && (
                      <li>{localize(language, "Search term", "搜索词")}</li>
                    )}
                  </ul>
                </div>
              </DialogBody>

              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => setIsCreateDialogOpen(false)}
                >
                  {localize(language, "Cancel", "取消")}
                </Button>
                <Button
                  type="submit"
                  disabled={
                    createMutation.isPending ||
                    !!form.formState.errors.name ||
                    !hasWriteAccess
                  }
                >
                  {!hasWriteAccess && <Lock className="mr-2 h-4 w-4" />}
                  {createMutation.isPending
                    ? localize(language, "Saving...", "保存中...")
                    : localize(language, "Save View", "保存视图")}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>
    </>
  );
}
