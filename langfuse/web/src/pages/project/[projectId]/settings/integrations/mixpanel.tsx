import { MixpanelLogo } from "@/src/components/MixpanelLogo";
import Header from "@/src/components/layouts/header";
import ContainerPage from "@/src/components/layouts/container-page";
import { StatusBadge } from "@/src/components/layouts/status-badge";
import { Button } from "@/src/components/ui/button";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/src/components/ui/form";
import { PasswordInput } from "@/src/components/ui/password-input";
import { Switch } from "@/src/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/src/components/ui/select";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/src/components/ui/tooltip";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import {
  mixpanelIntegrationFormSchema,
  MIXPANEL_REGIONS,
  type MixpanelRegion,
} from "@/src/features/mixpanel-integration/types";
import {
  AnalyticsIntegrationExportSource,
  EXPORT_SOURCE_OPTIONS,
} from "@langfuse/shared";
import { useV4Beta } from "@/src/features/events/hooks/useV4Beta";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { api } from "@/src/utils/api";
import { type RouterOutput } from "@/src/utils/types";
import { zodResolver } from "@hookform/resolvers/zod";
import { Card } from "@/src/components/ui/card";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { type z } from "zod/v4";
import { Info, ExternalLink } from "lucide-react";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export default function MixpanelIntegrationSettings() {
  const router = useRouter();
  const projectId = router.query.projectId as string;
  const { language } = useLanguage();

  const hasAccess = useHasProjectAccess({
    projectId,
    scope: "integrations:CRUD",
  });
  const state = api.mixpanelIntegration.get.useQuery(
    { projectId },
    {
      enabled: hasAccess,
    },
  );

  const status =
    state.isInitialLoading || !hasAccess
      ? undefined
      : state.data?.enabled
        ? "active"
        : "inactive";

  return (
    <ContainerPage
      headerProps={{
        title: localize(language, "Mixpanel Integration", "Mixpanel 集成"),
        breadcrumb: [
          {
            name: localize(language, "Settings", "设置"),
            href: `/project/${projectId}/settings`,
          },
        ],
        actionButtonsLeft: <>{status && <StatusBadge type={status} />}</>,
        actionButtonsRight: (
          <Button asChild variant="secondary">
            <Link href="https://langfuse.com/integrations/analytics/mixpanel">
              {localize(language, "Integration Docs ↗", "集成文档 ↗")}
            </Link>
          </Button>
        ),
      }}
    >
      <p className="mb-4 text-sm text-primary">
        {localize(language, "Integrate with", "集成")}{" "}
        <Link href="https://mixpanel.com" className="underline">
          Mixpanel
        </Link>{" "}
        {localize(
          language,
          "to sync your Langfuse traces, generations, and scores for advanced product analytics. Upon activation, all historical data from your project will be synced. After the initial sync, new data is automatically synced every hour to keep your Mixpanel dashboards up to date.",
          "以同步你的 Langfuse traces、generations 和 scores，用于高级产品分析。启用后，你项目中的全部历史数据都会被同步。首次同步完成后，新数据将每小时自动同步一次，以确保你的 Mixpanel 仪表板保持最新。",
        )}
      </p>
      {!hasAccess && (
        <p className="text-sm">
          {localize(
            language,
            "Your current role does not grant you access to these settings, please reach out to your project admin or owner.",
            "你当前的角色无权访问这些设置，请联系你的项目管理员或所有者。",
          )}
        </p>
      )}
      {hasAccess && (
        <>
          <Header title={localize(language, "Configuration", "配置")} />
          <Card className="p-3">
            <MixpanelLogo className="mb-4 w-20 text-foreground" />
            <MixpanelIntegrationSettingsForm
              state={state.data}
              projectId={projectId}
              isLoading={state.isLoading}
            />
          </Card>
        </>
      )}
      {state.data?.enabled && (
        <>
          <Header
            title={localize(language, "Status", "状态")}
            className="mt-8"
          />
          <p className="text-sm text-primary">
            {localize(language, "Data synced until:", "数据同步至：")}{" "}
            {state.data?.lastSyncAt
              ? new Date(state.data.lastSyncAt).toLocaleString()
              : localize(language, "Never (pending)", "从未（等待中）")}
          </p>
        </>
      )}
    </ContainerPage>
  );
}

const MixpanelIntegrationSettingsForm = ({
  state,
  projectId,
  isLoading,
}: {
  state?: RouterOutput["mixpanelIntegration"]["get"];
  projectId: string;
  isLoading: boolean;
}) => {
  const capture = usePostHogClientCapture();
  const { isBetaEnabled } = useV4Beta();
  const { language } = useLanguage();
  const getLocalizedRegionDescription = (region: MixpanelRegion) => {
    switch (region) {
      case "api":
        return localize(
          language,
          "US (api.mixpanel.com)",
          "美国（api.mixpanel.com）",
        );
      case "api-eu":
        return localize(
          language,
          "EU (api-eu.mixpanel.com)",
          "欧盟（api-eu.mixpanel.com）",
        );
      case "api-in":
        return localize(
          language,
          "India (api-in.mixpanel.com)",
          "印度（api-in.mixpanel.com）",
        );
      default:
        return region;
    }
  };
  const getLocalizedExportSourceLabel = (
    value: AnalyticsIntegrationExportSource,
  ) => {
    switch (value) {
      case AnalyticsIntegrationExportSource.TRACES_OBSERVATIONS:
        return localize(
          language,
          "Traces and observations (legacy)",
          "Traces 与 observations（旧版）",
        );
      case AnalyticsIntegrationExportSource.TRACES_OBSERVATIONS_EVENTS:
        return localize(
          language,
          "Traces and observations (legacy) and enriched observations",
          "Traces 与 observations（旧版）以及增强 observations",
        );
      case AnalyticsIntegrationExportSource.EVENTS:
        return localize(
          language,
          "Enriched observations (recommended)",
          "增强 observations（推荐）",
        );
      default:
        return value;
    }
  };
  const getLocalizedExportSourceDescription = (
    value: AnalyticsIntegrationExportSource,
  ) => {
    switch (value) {
      case AnalyticsIntegrationExportSource.TRACES_OBSERVATIONS:
        return localize(
          language,
          "Export traces, observations and scores. This is the legacy behavior prior to tracking traces and observations in separate tables. It is recommended to use the enriched observations option instead.",
          "导出 traces、observations 和 scores。这是将 traces 和 observations 分表跟踪之前的旧版行为。建议改用增强 observations 选项。",
        );
      case AnalyticsIntegrationExportSource.TRACES_OBSERVATIONS_EVENTS:
        return localize(
          language,
          "Export traces, observations, scores and enriched observations. This exports both the legacy data source (traces, observations) and the new one (enriched observations) and essentially exports duplicate data. Therefore, it should only be used to migrate existing integrations to the new recommended enriched observations and check validity of the data for downstream consumers of the export data.",
          "导出 traces、observations、scores 和增强 observations。此选项会同时导出旧数据源（traces、observations）和新数据源（增强 observations），本质上会导出重复数据。因此，它仅适用于将现有集成迁移到推荐的增强 observations，并校验下游使用方的数据有效性。",
        );
      case AnalyticsIntegrationExportSource.EVENTS:
        return localize(
          language,
          "Export enriched observations and scores. This is the recommended data source for integrations and will be the default for new integrations.",
          "导出增强 observations 和 scores。这是推荐用于集成的数据源，也将成为新集成的默认选项。",
        );
      default:
        return "";
    }
  };
  const mixpanelForm = useForm({
    resolver: zodResolver(mixpanelIntegrationFormSchema),
    defaultValues: {
      mixpanelRegion:
        (state?.mixpanelRegion as MixpanelRegion) ??
        MIXPANEL_REGIONS[0].subdomain,
      mixpanelProjectToken: state?.mixpanelProjectToken ?? "",
      enabled: state?.enabled ?? false,
      exportSource:
        state?.exportSource ??
        (isBetaEnabled
          ? AnalyticsIntegrationExportSource.EVENTS
          : AnalyticsIntegrationExportSource.TRACES_OBSERVATIONS),
    },
    disabled: isLoading,
  });

  useEffect(() => {
    mixpanelForm.reset({
      mixpanelRegion:
        (state?.mixpanelRegion as MixpanelRegion) ??
        MIXPANEL_REGIONS[0].subdomain,
      mixpanelProjectToken: state?.mixpanelProjectToken ?? "",
      enabled: state?.enabled ?? false,
      exportSource:
        state?.exportSource ??
        (isBetaEnabled
          ? AnalyticsIntegrationExportSource.EVENTS
          : AnalyticsIntegrationExportSource.TRACES_OBSERVATIONS),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  const utils = api.useUtils();
  const mut = api.mixpanelIntegration.update.useMutation({
    onSuccess: () => {
      utils.mixpanelIntegration.invalidate();
    },
  });
  const mutDelete = api.mixpanelIntegration.delete.useMutation({
    onSuccess: () => {
      utils.mixpanelIntegration.invalidate();
    },
  });

  async function onSubmit(
    values: z.infer<typeof mixpanelIntegrationFormSchema>,
  ) {
    capture("integrations:mixpanel_form_submitted");
    mut.mutate({
      projectId,
      ...values,
    });
  }

  return (
    <Form {...mixpanelForm}>
      <form
        className="space-y-3"
        onSubmit={mixpanelForm.handleSubmit(onSubmit)}
      >
        <FormField
          control={mixpanelForm.control}
          name="mixpanelRegion"
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                {localize(language, "Mixpanel Region", "Mixpanel 区域")}
              </FormLabel>
              <Select onValueChange={field.onChange} value={field.value}>
                <FormControl>
                  <SelectTrigger>
                    <SelectValue
                      placeholder={localize(
                        language,
                        "Select a region",
                        "选择区域",
                      )}
                    />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {MIXPANEL_REGIONS.map((region) => (
                    <SelectItem key={region.subdomain} value={region.subdomain}>
                      {getLocalizedRegionDescription(region.subdomain)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormDescription>
                {localize(
                  language,
                  "Select the Mixpanel region where your project is hosted",
                  "选择你的项目所在的 Mixpanel 区域",
                )}
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={mixpanelForm.control}
          name="mixpanelProjectToken"
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                {localize(
                  language,
                  "Mixpanel Project Token",
                  "Mixpanel 项目标识令牌",
                )}
              </FormLabel>
              <FormControl>
                <PasswordInput {...field} />
              </FormControl>
              <FormDescription>
                {localize(
                  language,
                  "You can find your Project Token in your Mixpanel project settings",
                  "你可以在 Mixpanel 项目设置中找到 Project Token",
                )}
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        {isBetaEnabled && (
          <FormField
            control={mixpanelForm.control}
            name="exportSource"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="flex items-center gap-1.5 pt-2">
                  {localize(language, "Export Source", "导出来源")}
                  <Tooltip>
                    <TooltipTrigger>
                      <Info className="h-3.5 w-3.5 text-muted-foreground" />
                    </TooltipTrigger>
                    <TooltipContent
                      side="bottom"
                      className="max-w-[350px] space-y-2 p-3"
                    >
                      {EXPORT_SOURCE_OPTIONS.map((option) => (
                        <div key={option.value} className="space-y-0.5">
                          <div className="font-medium">
                            {getLocalizedExportSourceLabel(option.value)}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {getLocalizedExportSourceDescription(option.value)}
                          </div>
                        </div>
                      ))}
                      <div className="border-t pt-2">
                        <a
                          href="https://langfuse.com/docs/integrations/export-sources"
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-primary hover:underline"
                        >
                          {localize(
                            language,
                            "For further information see",
                            "更多信息请参阅",
                          )}
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      </div>
                    </TooltipContent>
                  </Tooltip>
                </FormLabel>
                <Select onValueChange={field.onChange} value={field.value}>
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue
                        placeholder={localize(
                          language,
                          "Select data to export",
                          "选择要导出的数据",
                        )}
                      />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {EXPORT_SOURCE_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {getLocalizedExportSourceLabel(option.value)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormDescription>
                  {localize(
                    language,
                    "Choose which data sources to export to Mixpanel. Scores are always included.",
                    "选择要导出到 Mixpanel 的数据源。Scores 始终会包含在内。",
                  )}
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
        )}
        <FormField
          control={mixpanelForm.control}
          name="enabled"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{localize(language, "Enabled", "启用")}</FormLabel>
              <FormControl>
                <Switch
                  id="mixpanel-integration-enabled"
                  checked={field.value}
                  onCheckedChange={() => {
                    field.onChange(!field.value);
                  }}
                  className="ml-4 mt-1"
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </form>
      <div className="mt-8 flex gap-2">
        <Button
          loading={mut.isPending}
          onClick={mixpanelForm.handleSubmit(onSubmit)}
          disabled={isLoading}
        >
          {localize(language, "Save", "保存")}
        </Button>
        <Button
          variant="ghost"
          loading={mutDelete.isPending}
          disabled={isLoading || !!!state}
          onClick={() => {
            if (
              confirm(
                localize(
                  language,
                  "Are you sure you want to reset the Mixpanel integration for this project?",
                  "确定要重置此项目的 Mixpanel 集成吗？",
                ),
              )
            )
              mutDelete.mutate({ projectId });
          }}
        >
          {localize(language, "Reset", "重置")}
        </Button>
      </div>
    </Form>
  );
};
