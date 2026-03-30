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
import { Input } from "@/src/components/ui/input";
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
  blobStorageIntegrationFormSchema,
  type BlobStorageIntegrationFormSchema,
} from "@/src/features/blobstorage-integration/types";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { api } from "@/src/utils/api";
import { zodResolver } from "@hookform/resolvers/zod";
import { Card } from "@/src/components/ui/card";
import Link from "next/link";
import { useRouter } from "next/router";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { showSuccessToast } from "@/src/features/notifications/showSuccessToast";
import { showErrorToast } from "@/src/features/notifications/showErrorToast";
import {
  BlobStorageIntegrationType,
  BlobStorageIntegrationFileType,
  BlobStorageExportMode,
  AnalyticsIntegrationExportSource,
  type BlobStorageIntegration,
  EXPORT_SOURCE_OPTIONS,
} from "@langfuse/shared";
import { useLangfuseCloudRegion } from "@/src/features/organizations/hooks";
import { useV4Beta } from "@/src/features/events/hooks/useV4Beta";
import { Info, ExternalLink } from "lucide-react";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export default function BlobStorageIntegrationSettings() {
  const router = useRouter();
  const projectId = router.query.projectId as string;
  const { language } = useLanguage();
  const hasAccess = useHasProjectAccess({
    projectId,
    scope: "integrations:CRUD",
  });
  const state = api.blobStorageIntegration.get.useQuery(
    { projectId },
    {
      enabled: hasAccess,
      refetchOnMount: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      staleTime: 50 * 60 * 1000, // 50 minutes
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
        title: localize(
          language,
          "Blob Storage Integration",
          "Blob Storage 集成",
        ),
        breadcrumb: [
          {
            name: localize(language, "Settings", "设置"),
            href: `/project/${projectId}/settings`,
          },
        ],
        actionButtonsLeft: <>{status && <StatusBadge type={status} />}</>,
        actionButtonsRight: (
          <Button asChild variant="secondary">
            <Link
              href="https://langfuse.com/docs/api-and-data-platform/features/export-to-blob-storage"
              target="_blank"
            >
              {localize(language, "Integration Docs ↗", "集成文档 ↗")}
            </Link>
          </Button>
        ),
      }}
    >
      <p className="mb-4 text-sm text-primary">
        {localize(
          language,
          'Configure scheduled exports of your trace data to AWS S3, S3-compatible storages, or Azure Blob Storage. Set up a hourly, daily, or weekly export to your own storage for data analysis or backup purposes. Use the "Validate" button to test your configuration by uploading a small test file, and the "Run Now" button to trigger an immediate export.',
          "将你的 trace 数据按计划导出到 AWS S3、S3 兼容存储或 Azure Blob Storage。你可以配置每小时、每天或每周导出到自己的存储中，用于数据分析或备份。使用“验证”按钮可通过上传一个小测试文件来测试配置，使用“立即运行”按钮可触发一次即时导出。",
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
            <BlobStorageIntegrationSettingsForm
              state={state.data || undefined}
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
          <div className="space-y-2">
            <p className="text-sm text-primary">
              {localize(language, "Data last exported:", "最近导出时间：")}{" "}
              {state.data?.lastSyncAt
                ? new Date(state.data.lastSyncAt).toLocaleString()
                : localize(language, "Never (pending)", "从未（等待中）")}
            </p>
            <p className="text-sm text-primary">
              {localize(language, "Export mode:", "导出模式：")}{" "}
              {state.data?.exportMode === BlobStorageExportMode.FULL_HISTORY
                ? localize(language, "Full history", "完整历史")
                : state.data?.exportMode === BlobStorageExportMode.FROM_TODAY
                  ? localize(language, "From setup date", "从设置日期开始")
                  : state.data?.exportMode ===
                      BlobStorageExportMode.FROM_CUSTOM_DATE
                    ? localize(language, "From custom date", "从自定义日期开始")
                    : localize(language, "Unknown", "未知")}
            </p>
            {(state.data?.exportMode ===
              BlobStorageExportMode.FROM_CUSTOM_DATE ||
              state.data?.exportMode === BlobStorageExportMode.FROM_TODAY) &&
              state.data?.exportStartDate && (
                <p className="text-sm text-primary">
                  {localize(language, "Export start date:", "导出开始日期：")}{" "}
                  {new Date(state.data.exportStartDate).toLocaleDateString()}
                </p>
              )}
          </div>
        </>
      )}
    </ContainerPage>
  );
}

const BlobStorageIntegrationSettingsForm = ({
  state,
  projectId,
  isLoading,
}: {
  state?: Partial<BlobStorageIntegration>;
  projectId: string;
  isLoading: boolean;
}) => {
  const capture = usePostHogClientCapture();
  const { isLangfuseCloud } = useLangfuseCloudRegion();
  const { isBetaEnabled } = useV4Beta();
  const { language } = useLanguage();
  const [integrationType, setIntegrationType] =
    useState<BlobStorageIntegrationType>(BlobStorageIntegrationType.S3);
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

  // Check if this is a self-hosted instance (no cloud region set)
  const isSelfHosted = !isLangfuseCloud;

  const blobStorageForm = useForm({
    resolver: zodResolver(blobStorageIntegrationFormSchema),
    defaultValues: {
      type: state?.type || BlobStorageIntegrationType.S3,
      bucketName: state?.bucketName || "",
      endpoint: state?.endpoint || null,
      region: state?.region || "",
      accessKeyId: state?.accessKeyId || "",
      secretAccessKey: state?.secretAccessKey || null,
      prefix: state?.prefix || "",
      exportFrequency: (state?.exportFrequency || "daily") as
        | "daily"
        | "weekly"
        | "hourly",
      enabled: state?.enabled || false,
      forcePathStyle: state?.forcePathStyle || false,
      fileType: state?.fileType || BlobStorageIntegrationFileType.JSONL,
      exportMode: state?.exportMode || BlobStorageExportMode.FULL_HISTORY,
      exportStartDate: state?.exportStartDate || null,
      exportSource:
        state?.exportSource ||
        (isBetaEnabled
          ? AnalyticsIntegrationExportSource.EVENTS
          : AnalyticsIntegrationExportSource.TRACES_OBSERVATIONS),
    },
    disabled: isLoading,
  });

  useEffect(() => {
    setIntegrationType(state?.type || BlobStorageIntegrationType.S3);
    blobStorageForm.reset({
      type: state?.type || BlobStorageIntegrationType.S3,
      bucketName: state?.bucketName || "",
      endpoint: state?.endpoint || null,
      region: state?.region || "auto",
      accessKeyId: state?.accessKeyId || "",
      secretAccessKey: state?.secretAccessKey || null,
      prefix: state?.prefix || "",
      exportFrequency: (state?.exportFrequency || "daily") as
        | "daily"
        | "weekly"
        | "hourly",
      enabled: state?.enabled || false,
      forcePathStyle: state?.forcePathStyle || false,
      fileType: state?.fileType || BlobStorageIntegrationFileType.JSONL,
      exportMode: state?.exportMode || BlobStorageExportMode.FULL_HISTORY,
      exportStartDate: state?.exportStartDate || null,
      exportSource:
        state?.exportSource ||
        (isBetaEnabled
          ? AnalyticsIntegrationExportSource.EVENTS
          : AnalyticsIntegrationExportSource.TRACES_OBSERVATIONS),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  const utils = api.useUtils();
  const mut = api.blobStorageIntegration.update.useMutation({
    onSuccess: () => {
      utils.blobStorageIntegration.invalidate();
    },
  });
  const mutDelete = api.blobStorageIntegration.delete.useMutation({
    onSuccess: () => {
      utils.blobStorageIntegration.invalidate();
    },
  });
  const mutRunNow = api.blobStorageIntegration.runNow.useMutation({
    onSuccess: () => {
      utils.blobStorageIntegration.invalidate();
    },
  });
  const mutValidate = api.blobStorageIntegration.validate.useMutation({
    onSuccess: (data) => {
      showSuccessToast({
        title: data.message,
        description: localize(
          language,
          `Test file: ${data.testFileName}`,
          `测试文件：${data.testFileName}`,
        ),
      });
    },
    onError: (error) => {
      showErrorToast(
        localize(language, "Validation failed", "验证失败"),
        error.message,
      );
    },
  });

  async function onSubmit(values: BlobStorageIntegrationFormSchema) {
    capture("integrations:blob_storage_form_submitted");
    mut.mutate({
      projectId,
      ...values,
    });
  }

  const handleIntegrationTypeChange = (value: BlobStorageIntegrationType) => {
    setIntegrationType(value);
    blobStorageForm.setValue("type", value);
  };

  return (
    <Form {...blobStorageForm}>
      <form
        className="space-y-3"
        onSubmit={blobStorageForm.handleSubmit(onSubmit)}
      >
        <FormField
          control={blobStorageForm.control}
          name="type"
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                {localize(language, "Storage Provider", "存储提供商")}
              </FormLabel>
              <FormControl>
                <Select
                  value={field.value}
                  onValueChange={(value) =>
                    handleIntegrationTypeChange(
                      value as BlobStorageIntegrationType,
                    )
                  }
                >
                  <SelectTrigger>
                    <SelectValue
                      placeholder={localize(
                        language,
                        "Select provider",
                        "选择提供商",
                      )}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="S3">AWS S3</SelectItem>
                    <SelectItem value="S3_COMPATIBLE">
                      {localize(
                        language,
                        "S3 Compatible Storage",
                        "S3 兼容存储",
                      )}
                    </SelectItem>
                    <SelectItem value="AZURE_BLOB_STORAGE">
                      {localize(
                        language,
                        "Azure Blob Storage",
                        "Azure Blob Storage",
                      )}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </FormControl>
              <FormDescription>
                {localize(
                  language,
                  "Choose your cloud storage provider",
                  "选择你的云存储提供商",
                )}
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={blobStorageForm.control}
          name="bucketName"
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                {integrationType === "AZURE_BLOB_STORAGE"
                  ? localize(language, "Container Name", "容器名称")
                  : localize(language, "Bucket Name", "Bucket 名称")}
              </FormLabel>
              <FormControl>
                <Input {...field} />
              </FormControl>
              <FormDescription>
                {integrationType === "AZURE_BLOB_STORAGE"
                  ? localize(
                      language,
                      "The Azure storage container name",
                      "Azure 存储容器名称",
                    )
                  : localize(language, "The S3 bucket name", "S3 bucket 名称")}
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        {/* Endpoint URL field - Only shown for S3-compatible and Azure */}
        {integrationType !== "S3" && (
          <FormField
            control={blobStorageForm.control}
            name="endpoint"
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  {localize(language, "Endpoint URL", "端点 URL")}
                </FormLabel>
                <FormControl>
                  <Input {...field} value={field.value || ""} />
                </FormControl>
                <FormDescription>
                  {integrationType === "AZURE_BLOB_STORAGE"
                    ? localize(
                        language,
                        "Azure Blob Storage endpoint URL (e.g., https://accountname.blob.core.windows.net)",
                        "Azure Blob Storage 端点 URL（例如：https://accountname.blob.core.windows.net）",
                      )
                    : localize(
                        language,
                        "S3 compatible endpoint URL (e.g., https://play.min.io)",
                        "S3 兼容端点 URL（例如：https://play.min.io）",
                      )}
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
        )}

        {/* Region field - Only shown for AWS S3 or compatible storage */}
        {integrationType !== "AZURE_BLOB_STORAGE" && (
          <FormField
            control={blobStorageForm.control}
            name="region"
            render={({ field }) => (
              <FormItem>
                <FormLabel>{localize(language, "Region", "区域")}</FormLabel>
                <FormControl>
                  <Input {...field} />
                </FormControl>
                <FormDescription>
                  {integrationType === "S3"
                    ? localize(
                        language,
                        "AWS region (e.g., us-east-1)",
                        "AWS 区域（例如：us-east-1）",
                      )
                    : localize(
                        language,
                        "S3 compatible storage region",
                        "S3 兼容存储区域",
                      )}
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
        )}

        {/* Force Path Style switch - Only shown for S3-compatible */}
        {integrationType === "S3_COMPATIBLE" && (
          <FormField
            control={blobStorageForm.control}
            name="forcePathStyle"
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  {localize(language, "Force Path Style", "强制路径样式")}
                </FormLabel>
                <FormControl>
                  <Switch
                    checked={field.value}
                    onCheckedChange={field.onChange}
                    className="ml-4 mt-1"
                  />
                </FormControl>
                <FormDescription>
                  {localize(
                    language,
                    "Enable for MinIO and some other S3 compatible providers",
                    "为 MinIO 和部分其他 S3 兼容提供商启用",
                  )}
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
        )}

        <FormField
          control={blobStorageForm.control}
          name="accessKeyId"
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                {integrationType === "AZURE_BLOB_STORAGE"
                  ? localize(language, "Storage Account Name", "存储账户名称")
                  : integrationType === "S3"
                    ? localize(
                        language,
                        "AWS Access Key ID",
                        "AWS Access Key ID",
                      )
                    : localize(language, "Access Key ID", "Access Key ID")}
                {/* Show optional indicator for S3 types on self-hosted instances with entitlement */}
                {isSelfHosted && integrationType === "S3" && (
                  <span className="text-muted-foreground">
                    {" "}
                    {localize(language, "(optional)", "（可选）")}
                  </span>
                )}
              </FormLabel>
              <FormControl>
                <Input {...field} />
              </FormControl>
              <FormDescription>
                {integrationType === "AZURE_BLOB_STORAGE"
                  ? localize(
                      language,
                      "Your Azure storage account name",
                      "你的 Azure 存储账户名称",
                    )
                  : integrationType === "S3"
                    ? isSelfHosted
                      ? localize(
                          language,
                          "Your AWS IAM user access key ID. Leave empty to use host credentials (IAM roles, instance profiles, etc.)",
                          "你的 AWS IAM 用户 Access Key ID。留空则使用宿主环境凭证（IAM 角色、实例配置文件等）。",
                        )
                      : localize(
                          language,
                          "Your AWS IAM user access key ID",
                          "你的 AWS IAM 用户 Access Key ID",
                        )
                    : localize(
                        language,
                        "Access key for your S3-compatible storage",
                        "你的 S3 兼容存储 Access Key",
                      )}
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={blobStorageForm.control}
          name="secretAccessKey"
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                {integrationType === "AZURE_BLOB_STORAGE"
                  ? localize(language, "Storage Account Key", "存储账户密钥")
                  : integrationType === "S3"
                    ? localize(
                        language,
                        "AWS Secret Access Key",
                        "AWS Secret Access Key",
                      )
                    : localize(
                        language,
                        "Secret Access Key",
                        "Secret Access Key",
                      )}
                {/* Show optional indicator for S3 types on self-hosted instances with entitlement */}
                {isSelfHosted && integrationType === "S3" && (
                  <span className="text-muted-foreground">
                    {" "}
                    {localize(language, "(optional)", "（可选）")}
                  </span>
                )}
              </FormLabel>
              <FormControl>
                <PasswordInput
                  placeholder={localize(
                    language,
                    "********************",
                    "********************",
                  )}
                  {...field}
                  value={field.value || ""}
                />
              </FormControl>
              <FormDescription>
                {integrationType === "AZURE_BLOB_STORAGE"
                  ? localize(
                      language,
                      "Your Azure storage account access key",
                      "你的 Azure 存储账户访问密钥",
                    )
                  : integrationType === "S3"
                    ? isSelfHosted
                      ? localize(
                          language,
                          "Your AWS IAM user secret access key. Leave empty to use host credentials (IAM roles, instance profiles, etc.)",
                          "你的 AWS IAM 用户 Secret Access Key。留空则使用宿主环境凭证（IAM 角色、实例配置文件等）。",
                        )
                      : localize(
                          language,
                          "Your AWS IAM user secret access key",
                          "你的 AWS IAM 用户 Secret Access Key",
                        )
                    : localize(
                        language,
                        "Secret key for your S3-compatible storage",
                        "你的 S3 兼容存储 Secret Key",
                      )}
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={blobStorageForm.control}
          name="prefix"
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                {localize(language, "Export Prefix", "导出前缀")}
              </FormLabel>
              <FormControl>
                <Input {...field} />
              </FormControl>
              <FormDescription>
                {integrationType === "AZURE_BLOB_STORAGE"
                  ? localize(
                      language,
                      'Optional prefix path for exported files in your Azure container (e.g., "langfuse-exports/")',
                      'Azure 容器中导出文件的可选前缀路径（例如："langfuse-exports/"）',
                    )
                  : integrationType === "S3"
                    ? localize(
                        language,
                        'Optional prefix path for exported files in your S3 bucket (e.g., "langfuse-exports/")',
                        'S3 bucket 中导出文件的可选前缀路径（例如："langfuse-exports/"）',
                      )
                    : localize(
                        language,
                        'Optional prefix path for exported files (e.g., "langfuse-exports/")',
                        '导出文件的可选前缀路径（例如："langfuse-exports/"）',
                      )}
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={blobStorageForm.control}
          name="exportFrequency"
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                {localize(language, "Export Frequency", "导出频率")}
              </FormLabel>
              <FormControl>
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger>
                    <SelectValue
                      placeholder={localize(
                        language,
                        "Select frequency",
                        "选择频率",
                      )}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="hourly">
                      {localize(language, "Hourly", "每小时")}
                    </SelectItem>
                    <SelectItem value="daily">
                      {localize(language, "Daily", "每天")}
                    </SelectItem>
                    <SelectItem value="weekly">
                      {localize(language, "Weekly", "每周")}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </FormControl>
              <FormDescription>
                {localize(
                  language,
                  "How often the data should be exported. Changes are taken into consideration from the next run onwards.",
                  "设置数据导出的频率。更改将从下一次运行开始生效。",
                )}
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={blobStorageForm.control}
          name="fileType"
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                {localize(language, "File Type", "文件类型")}
              </FormLabel>
              <FormControl>
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger>
                    <SelectValue
                      placeholder={localize(
                        language,
                        "Select file type",
                        "选择文件类型",
                      )}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="JSONL">JSONL</SelectItem>
                    <SelectItem value="CSV">CSV</SelectItem>
                    <SelectItem value="JSON">JSON</SelectItem>
                  </SelectContent>
                </Select>
              </FormControl>
              <FormDescription>
                {localize(
                  language,
                  "The file format for exported data.",
                  "导出数据的文件格式。",
                )}
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={blobStorageForm.control}
          name="exportMode"
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                {localize(language, "Export Mode", "导出模式")}
              </FormLabel>
              <FormControl>
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger>
                    <SelectValue
                      placeholder={localize(
                        language,
                        "Select export mode",
                        "选择导出模式",
                      )}
                    />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={BlobStorageExportMode.FULL_HISTORY}>
                      {localize(language, "Full history", "完整历史")}
                    </SelectItem>
                    <SelectItem value={BlobStorageExportMode.FROM_TODAY}>
                      {localize(language, "Today", "今天")}
                    </SelectItem>
                    <SelectItem value={BlobStorageExportMode.FROM_CUSTOM_DATE}>
                      {localize(language, "Custom date", "自定义日期")}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </FormControl>
              <FormDescription>
                {localize(
                  language,
                  'Choose when to start exporting data. "Today" and "Custom date" modes will not include historical data before the specified date.',
                  "选择从何时开始导出数据。“今天”和“自定义日期”模式不会包含指定日期之前的历史数据。",
                )}
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        {isBetaEnabled && (
          <FormField
            control={blobStorageForm.control}
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
                    "Choose which data sources to export to blob storage. Scores are always included.",
                    "选择要导出到 Blob Storage 的数据源。Scores 始终会包含在内。",
                  )}
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
        )}

        {blobStorageForm.watch("exportMode") ===
          BlobStorageExportMode.FROM_CUSTOM_DATE && (
          <FormField
            control={blobStorageForm.control}
            name="exportStartDate"
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  {localize(language, "Export Start Date", "导出开始日期")}
                </FormLabel>
                <FormControl>
                  <Input
                    type="date"
                    value={
                      field.value instanceof Date
                        ? field.value.toISOString().split("T")[0]
                        : ""
                    }
                    onChange={(e) => {
                      const date = e.target.value
                        ? new Date(e.target.value)
                        : null;
                      field.onChange(date);
                    }}
                    placeholder={localize(
                      language,
                      "Select start date",
                      "选择开始日期",
                    )}
                  />
                </FormControl>
                <FormDescription>
                  {localize(
                    language,
                    "Data before this date will not be included in exports",
                    "此日期之前的数据不会包含在导出中",
                  )}
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
        )}

        <FormField
          control={blobStorageForm.control}
          name="enabled"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{localize(language, "Enabled", "启用")}</FormLabel>
              <FormControl>
                <Switch
                  checked={field.value}
                  onCheckedChange={field.onChange}
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
          onClick={blobStorageForm.handleSubmit(onSubmit)}
          disabled={isLoading}
        >
          {localize(language, "Save", "保存")}
        </Button>
        <Button
          variant="secondary"
          loading={mutValidate.isPending}
          disabled={isLoading || !state}
          title={localize(
            language,
            "Test your saved configuration by uploading a small test file to your storage",
            "通过上传一个小型测试文件到你的存储中来测试已保存的配置",
          )}
          onClick={() => {
            mutValidate.mutate({ projectId });
          }}
        >
          {localize(language, "Validate", "验证")}
        </Button>
        <Button
          variant="secondary"
          loading={mutRunNow.isPending}
          disabled={isLoading || !state?.enabled}
          title={localize(
            language,
            "Trigger an immediate export of all data since the last sync",
            "立即导出自上次同步以来的所有数据",
          )}
          onClick={() => {
            if (
              confirm(
                localize(
                  language,
                  "Are you sure you want to run the blob storage export now? This will export all data since the last sync.",
                  "确定要立即运行 Blob Storage 导出吗？这将导出自上次同步以来的所有数据。",
                ),
              )
            )
              mutRunNow.mutate({ projectId });
          }}
        >
          {localize(language, "Run Now", "立即运行")}
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
                  "Are you sure you want to reset the Blob Storage integration for this project?",
                  "确定要重置此项目的 Blob Storage 集成吗？",
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
