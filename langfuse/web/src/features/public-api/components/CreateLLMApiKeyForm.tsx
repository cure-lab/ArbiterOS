import { useFieldArray, useForm } from "react-hook-form";
import { useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  type BedrockConfig,
  type BedrockCredential,
  type VertexAIConfig,
  LLMAdapter,
  type LlmApiKeys,
  BEDROCK_USE_DEFAULT_CREDENTIALS,
  VERTEXAI_USE_DEFAULT_CREDENTIALS,
} from "@langfuse/shared";
import { ChevronDown, PlusIcon, TrashIcon } from "lucide-react";
import { z } from "zod/v4";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/src/components/ui/select";
import { Switch } from "@/src/components/ui/switch";
import { api } from "@/src/utils/api";
import { cn } from "@/src/utils/tailwind";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { type useUiCustomization } from "@/src/ee/features/ui-customization/useUiCustomization";
import { DialogFooter } from "@/src/components/ui/dialog";
import { DialogBody } from "@/src/components/ui/dialog";
import { env } from "@/src/env.mjs";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";
import { type AppLanguage } from "@/src/features/i18n/constants";

const isLangfuseCloud = Boolean(env.NEXT_PUBLIC_LANGFUSE_CLOUD_REGION);

const isCustomModelsRequired = (adapter: LLMAdapter) =>
  adapter === LLMAdapter.Azure || adapter === LLMAdapter.Bedrock;

const createFormSchema = (mode: "create" | "update", language: AppLanguage) =>
  z
    .object({
      secretKey: z.string().optional(),
      provider: z
        .string()
        .min(
          1,
          localize(
            language,
            "Please add a provider name that identifies this connection.",
            "请添加一个用于标识此连接的提供商名称。",
          ),
        )
        .regex(
          /^[^:]+$/,
          localize(
            language,
            "Provider name cannot contain colons. Use a format like 'OpenRouter_Mistral' instead.",
            "提供商名称不能包含冒号。请改用类似“OpenRouter_Mistral”的格式。",
          ),
        ),
      adapter: z.nativeEnum(LLMAdapter),
      baseURL: z.union([z.literal(""), z.url()]),
      withDefaultModels: z.boolean(),
      customModels: z.array(z.object({ value: z.string().min(1) })),
      awsAccessKeyId: z.string().optional(),
      awsSecretAccessKey: z.string().optional(),
      awsRegion: z.string().optional(),
      vertexAILocation: z.string().optional(),
      extraHeaders: z.array(
        z.object({
          key: z.string().min(1),
          value: mode === "create" ? z.string().min(1) : z.string().optional(),
        }),
      ),
    })
    // 1) Bedrock validation - credentials required in create mode
    .refine(
      (data) => {
        if (data.adapter !== LLMAdapter.Bedrock) return true;

        // In update mode, credentials are optional (existing ones are preserved)
        if (mode === "update") {
          // Only validate region is present
          return data.awsRegion;
        }

        // In create mode, validate credentials
        // For cloud deployments, AWS credentials are required
        if (isLangfuseCloud) {
          return (
            data.awsAccessKeyId && data.awsSecretAccessKey && data.awsRegion
          );
        }

        // For self-hosted deployments, only region is required
        return data.awsRegion;
      },
      {
        message:
          mode === "update"
            ? localize(
                language,
                "AWS region is required.",
                "AWS 区域为必填项。",
              )
            : isLangfuseCloud
              ? localize(
                  language,
                  "AWS credentials are required for Bedrock",
                  "Bedrock 需要 AWS 凭证。",
                )
              : localize(
                  language,
                  "AWS region is required.",
                  "AWS 区域为必填项。",
                ),
        path: ["adapter"],
      },
    )
    .refine(
      (data) => {
        if (isCustomModelsRequired(data.adapter)) {
          return data.customModels.length > 0;
        }
        return true;
      },
      {
        message: localize(
          language,
          "At least one custom model is required for this adapter.",
          "此适配器至少需要一个自定义模型。",
        ),
        path: ["customModels"],
      },
    )
    // 2) For adapters that support defaults, require default models or at least one custom model
    .refine(
      (data) => {
        if (isCustomModelsRequired(data.adapter)) {
          return true;
        }
        return data.withDefaultModels || data.customModels.length > 0;
      },
      {
        message: localize(
          language,
          "At least one custom model name is required when default models are disabled.",
          "禁用默认模型时，至少需要一个自定义模型名称。",
        ),
        path: ["withDefaultModels"],
      },
    )
    // Vertex AI validation - service account key or ADC sentinel value required
    .refine(
      (data) => {
        if (data.adapter !== LLMAdapter.VertexAI) return true;

        // In update mode, credentials are optional (existing ones are preserved)
        if (mode === "update") return true;

        // secretKey is required (either JSON key or VERTEXAI_USE_DEFAULT_CREDENTIALS sentinel)
        return !!data.secretKey;
      },
      {
        message: isLangfuseCloud
          ? localize(
              language,
              "GCP service account JSON key is required for Vertex AI",
              "Vertex AI 需要 GCP 服务账号 JSON 密钥。",
            )
          : localize(
              language,
              "GCP service account JSON key or Application Default Credentials is required.",
              "需要提供 GCP 服务账号 JSON 密钥或应用默认凭证。",
            ),
        path: ["secretKey"],
      },
    )
    .refine(
      (data) =>
        data.adapter === LLMAdapter.Bedrock ||
        data.adapter === LLMAdapter.VertexAI ||
        mode === "update" ||
        data.secretKey,
      {
        message: localize(
          language,
          "Secret key is required.",
          "密钥为必填项。",
        ),
        path: ["secretKey"],
      },
    )
    .refine(
      (data) => {
        if (data.adapter !== LLMAdapter.Azure) return true;
        return data.baseURL && data.baseURL.trim() !== "";
      },
      {
        message: localize(
          language,
          "API Base URL is required for Azure connections.",
          "Azure 连接需要 API Base URL。",
        ),
        path: ["baseURL"],
      },
    );

interface CreateLLMApiKeyFormProps {
  projectId?: string;
  onSuccess: () => void;
  customization: ReturnType<typeof useUiCustomization>;
  mode?: "create" | "update";
  existingKey?: LlmApiKeys;
}

export function CreateLLMApiKeyForm({
  projectId,
  onSuccess,
  customization,
  mode = "create",
  existingKey,
}: CreateLLMApiKeyFormProps) {
  const [showAdvancedSettings, setShowAdvancedSettings] = useState(false);
  const utils = api.useUtils();
  const capture = usePostHogClientCapture();
  const { language } = useLanguage();

  const existingKeys = api.llmApiKey.all.useQuery(
    {
      projectId: projectId as string,
    },
    { enabled: Boolean(projectId) },
  );

  const mutCreateLlmApiKey = api.llmApiKey.create.useMutation({
    onSuccess: () => utils.llmApiKey.invalidate(),
  });

  const mutUpdateLlmApiKey = api.llmApiKey.update.useMutation({
    onSuccess: () => utils.llmApiKey.invalidate(),
  });

  const mutTestLLMApiKey = api.llmApiKey.test.useMutation();
  const mutTestUpdateLLMApiKey = api.llmApiKey.testUpdate.useMutation();

  const defaultAdapter: LLMAdapter = customization?.defaultModelAdapter
    ? LLMAdapter[customization.defaultModelAdapter]
    : LLMAdapter.OpenAI;

  const getCustomizedBaseURL = (adapter: LLMAdapter) => {
    switch (adapter) {
      case LLMAdapter.OpenAI:
        return customization?.defaultBaseUrlOpenAI ?? "";
      case LLMAdapter.Azure:
        return customization?.defaultBaseUrlAzure ?? "";
      case LLMAdapter.Anthropic:
        return customization?.defaultBaseUrlAnthropic ?? "";
      default:
        return "";
    }
  };

  const formSchema = createFormSchema(mode, language);

  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues:
      mode === "update" && existingKey
        ? {
            adapter: existingKey.adapter as LLMAdapter,
            provider: existingKey.provider,
            secretKey:
              existingKey.adapter === LLMAdapter.VertexAI &&
              existingKey.displaySecretKey === "Default GCP credentials (ADC)"
                ? VERTEXAI_USE_DEFAULT_CREDENTIALS
                : "",
            baseURL:
              existingKey.baseURL ??
              getCustomizedBaseURL(existingKey.adapter as LLMAdapter),
            withDefaultModels: existingKey.withDefaultModels,
            customModels: existingKey.customModels.map((value) => ({ value })),
            extraHeaders:
              existingKey.extraHeaderKeys?.map((key) => ({ key, value: "" })) ??
              [],
            vertexAILocation:
              existingKey.adapter === LLMAdapter.VertexAI && existingKey.config
                ? ((existingKey.config as VertexAIConfig).location ?? "")
                : "",
            awsRegion:
              existingKey.adapter === LLMAdapter.Bedrock && existingKey.config
                ? ((existingKey.config as BedrockConfig).region ?? "")
                : "",
            awsAccessKeyId: "",
            awsSecretAccessKey: "",
          }
        : {
            adapter: defaultAdapter,
            provider: "",
            secretKey: "",
            baseURL: getCustomizedBaseURL(defaultAdapter),
            withDefaultModels: true,
            customModels: [],
            extraHeaders: [],
            vertexAILocation: "global",
            awsRegion: "",
            awsAccessKeyId: "",
            awsSecretAccessKey: "",
          },
  });

  const currentAdapter = form.watch("adapter");

  const hasAdvancedSettings = (adapter: LLMAdapter) =>
    adapter === LLMAdapter.OpenAI ||
    adapter === LLMAdapter.Anthropic ||
    adapter === LLMAdapter.VertexAI ||
    adapter === LLMAdapter.GoogleAIStudio;

  const { fields, append, remove } = useFieldArray({
    control: form.control,
    name: "customModels",
  });

  const {
    fields: headerFields,
    append: appendHeader,
    remove: removeHeader,
  } = useFieldArray({
    control: form.control,
    name: "extraHeaders",
  });

  const renderCustomModelsField = () => (
    <FormField
      control={form.control}
      name="customModels"
      render={() => (
        <FormItem>
          <FormLabel>
            {localize(language, "Custom models", "自定义模型")}
          </FormLabel>
          <FormDescription>
            {localize(
              language,
              "Custom model names accepted by given endpoint.",
              "当前端点接受的自定义模型名称。",
            )}
          </FormDescription>
          {currentAdapter === LLMAdapter.Azure && (
            <FormDescription className="text-dark-yellow">
              {localize(
                language,
                "For Azure, the model name should be the same as the deployment name in Azure. For evals, choose a model with function calling capabilities.",
                "对于 Azure，模型名称应与 Azure 中的部署名称一致。用于评估时，请选择支持函数调用的模型。",
              )}
            </FormDescription>
          )}

          {currentAdapter === LLMAdapter.Bedrock && (
            <FormDescription className="text-dark-yellow">
              {localize(
                language,
                "For Bedrock, the model name is the Bedrock Inference Profile ID, e.g. 'eu.anthropic.claude-3-5-sonnet-20240620-v1:0'",
                "对于 Bedrock，模型名称是 Bedrock Inference Profile ID，例如“eu.anthropic.claude-3-5-sonnet-20240620-v1:0”。",
              )}
            </FormDescription>
          )}

          {fields.map((customModel, index) => (
            <span key={customModel.id} className="flex flex-row space-x-2">
              <Input
                {...form.register(`customModels.${index}.value`)}
                placeholder={localize(
                  language,
                  `Custom model name ${index + 1}`,
                  `自定义模型名称 ${index + 1}`,
                )}
              />
              <Button
                type="button"
                variant="ghost"
                onClick={() => remove(index)}
              >
                <TrashIcon className="h-4 w-4" />
              </Button>
            </span>
          ))}
          <Button
            type="button"
            variant="ghost"
            onClick={() => append({ value: "" })}
            className="w-full"
          >
            <PlusIcon className="-ml-0.5 mr-1.5 h-5 w-5" aria-hidden="true" />
            {localize(language, "Add custom model name", "添加自定义模型名称")}
          </Button>
        </FormItem>
      )}
    />
  );

  const renderExtraHeadersField = () => (
    <FormField
      control={form.control}
      name="extraHeaders"
      render={() => (
        <FormItem>
          <FormLabel>
            {localize(language, "Extra Headers", "额外请求头")}
          </FormLabel>
          <FormDescription>
            {localize(
              language,
              "Optional additional HTTP headers to include with requests towards LLM provider. All header values stored encrypted",
              "可选的附加 HTTP 请求头，会随发往 LLM 提供商的请求一起发送。所有请求头值都会加密存储",
            )}{" "}
            {isLangfuseCloud
              ? localize(language, "on our servers", "在我们的服务器上")
              : localize(language, "in your database", "在你的数据库中")}
            .
          </FormDescription>

          {headerFields.map((header, index) => (
            <div key={header.id} className="flex flex-row space-x-2">
              <Input
                {...form.register(`extraHeaders.${index}.key`)}
                placeholder={localize(language, "Header name", "请求头名称")}
              />
              <Input
                {...form.register(`extraHeaders.${index}.value`)}
                placeholder={
                  mode === "update" &&
                  existingKey?.extraHeaderKeys &&
                  existingKey.extraHeaderKeys[index]
                    ? "***"
                    : localize(language, "Header value", "请求头值")
                }
              />
              <Button
                type="button"
                variant="ghost"
                onClick={() => removeHeader(index)}
              >
                <TrashIcon className="h-4 w-4" />
              </Button>
            </div>
          ))}

          <Button
            type="button"
            variant="ghost"
            onClick={() => appendHeader({ key: "", value: "" })}
            className="w-full"
          >
            <PlusIcon className="-ml-0.5 mr-1.5 h-5 w-5" aria-hidden="true" />
            {localize(language, "Add Header", "添加请求头")}
          </Button>
        </FormItem>
      )}
    />
  );

  // Disable provider and adapter fields in update mode
  const isFieldDisabled = (fieldName: string) => {
    if (mode !== "update") return false;
    return ["provider", "adapter"].includes(fieldName);
  };

  async function onSubmit(values: z.infer<typeof formSchema>) {
    if (!projectId) return console.error("No project ID found.");

    if (mode === "create") {
      if (
        existingKeys?.data?.data
          .map((k) => k.provider)
          .includes(values.provider)
      ) {
        form.setError("provider", {
          type: "manual",
          message: localize(
            language,
            "There already exists an API key for this provider.",
            "该提供商已存在 API 密钥。",
          ),
        });
        return;
      }
      capture("project_settings:llm_api_key_create", {
        provider: values.provider,
      });
    } else {
      capture("project_settings:llm_api_key_update", {
        provider: values.provider,
      });
    }

    let secretKey = values.secretKey;
    let config: BedrockConfig | VertexAIConfig | undefined;

    if (currentAdapter === LLMAdapter.Bedrock) {
      // In update mode, only update credentials if provided
      if (mode === "update") {
        // Only update secretKey if both credentials are provided
        if (values.awsAccessKeyId && values.awsSecretAccessKey) {
          const credentials: BedrockCredential = {
            accessKeyId: values.awsAccessKeyId,
            secretAccessKey: values.awsSecretAccessKey,
          };
          secretKey = JSON.stringify(credentials);
        } else {
          // Keep existing credentials by not setting secretKey
          secretKey = undefined;
        }
      } else {
        // In create mode, handle as before
        if (
          !isLangfuseCloud &&
          (!values.awsAccessKeyId || !values.awsSecretAccessKey)
        ) {
          secretKey = BEDROCK_USE_DEFAULT_CREDENTIALS;
        } else {
          const credentials: BedrockCredential = {
            accessKeyId: values.awsAccessKeyId ?? "",
            secretAccessKey: values.awsSecretAccessKey ?? "",
          };
          secretKey = JSON.stringify(credentials);
        }
      }

      config = {
        region: values.awsRegion ?? "",
      };
    } else if (currentAdapter === LLMAdapter.VertexAI) {
      // Handle Vertex AI credentials
      // secretKey already contains either JSON key or VERTEXAI_USE_DEFAULT_CREDENTIALS sentinel
      if (mode === "update") {
        // In update mode, only update secretKey if a new one is provided
        if (values.secretKey) {
          secretKey = values.secretKey;
        } else {
          // Keep existing credentials by not setting secretKey
          secretKey = undefined;
        }
      }
      // In create mode, secretKey is already set from values.secretKey

      // Build config with location only (projectId removed for security - ADC auto-detects)
      config = {};
      if (values.vertexAILocation?.trim()) {
        config.location = values.vertexAILocation.trim();
      }
      // If config is empty, set to undefined
      if (Object.keys(config).length === 0) {
        config = undefined;
      }
    }

    const extraHeaders =
      values.extraHeaders.length > 0
        ? values.extraHeaders.reduce(
            (acc, header) => {
              acc[header.key] = header.value ?? "";
              return acc;
            },
            {} as Record<string, string>,
          )
        : undefined;

    const newLlmApiKey = {
      id: existingKey?.id ?? "",
      projectId,
      secretKey: secretKey ?? "",
      provider: values.provider,
      adapter: values.adapter,
      baseURL: values.baseURL || undefined,
      withDefaultModels: isCustomModelsRequired(currentAdapter)
        ? false
        : values.withDefaultModels,
      config,
      customModels: values.customModels
        .map((m) => m.value.trim())
        .filter(Boolean),
      extraHeaders,
    };

    try {
      const testResult =
        mode === "create"
          ? await mutTestLLMApiKey.mutateAsync(newLlmApiKey)
          : await mutTestUpdateLLMApiKey.mutateAsync(newLlmApiKey);

      if (!testResult.success) throw new Error(testResult.error);
    } catch (error) {
      form.setError("root", {
        type: "manual",
        message:
          error instanceof Error
            ? error.message
            : localize(
                language,
                "Could not verify the API key.",
                "无法验证该 API 密钥。",
              ),
      });

      return;
    }

    return (mode === "create" ? mutCreateLlmApiKey : mutUpdateLlmApiKey)
      .mutateAsync(newLlmApiKey)
      .then(() => {
        form.reset();
        onSuccess();
      })
      .catch((error) => {
        console.error(error);
      });
  }

  return (
    <Form {...form}>
      <form
        className={cn("flex flex-col gap-4 overflow-auto")}
        onSubmit={(e) => {
          e.stopPropagation(); // Prevent event bubbling to parent forms
          form.handleSubmit(onSubmit)(e);
        }}
      >
        <DialogBody>
          {/* LLM adapter */}
          <FormField
            control={form.control}
            name="adapter"
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  {localize(language, "LLM adapter", "LLM 适配器")}
                </FormLabel>
                <FormDescription>
                  {localize(
                    language,
                    "Schema that is accepted at that provider endpoint.",
                    "该提供商端点所接受的协议/格式。",
                  )}
                </FormDescription>
                <Select
                  defaultValue={field.value}
                  onValueChange={(value) => {
                    field.onChange(value as LLMAdapter);
                    form.setValue(
                      "baseURL",
                      getCustomizedBaseURL(value as LLMAdapter),
                    );
                  }}
                  disabled={isFieldDisabled("adapter")}
                >
                  <FormControl>
                    <SelectTrigger>
                      <SelectValue
                        placeholder={localize(
                          language,
                          "Select a LLM provider",
                          "选择 LLM 提供商",
                        )}
                      />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {Object.values(LLMAdapter).map((provider) => (
                      <SelectItem value={provider} key={provider}>
                        {provider}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
          {/* Provider name */}
          <FormField
            control={form.control}
            name="provider"
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  {localize(language, "Provider name", "提供商名称")}
                </FormLabel>
                <FormDescription>
                  {localize(
                    language,
                    "Key to identify the connection within Langfuse. Cannot contain colons.",
                    "用于在 Langfuse 中标识该连接的键。不能包含冒号。",
                  )}
                </FormDescription>
                <FormControl>
                  <Input
                    {...field}
                    placeholder={localize(
                      language,
                      `e.g. ${currentAdapter}`,
                      `例如：${currentAdapter}`,
                    )}
                    disabled={isFieldDisabled("provider")}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          {/* API Key or AWS Credentials or Vertex AI Credentials */}
          {currentAdapter === LLMAdapter.Bedrock ? (
            <>
              <FormField
                control={form.control}
                name="awsRegion"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {localize(language, "AWS Region", "AWS 区域")}
                    </FormLabel>
                    <FormDescription>
                      {mode === "update" &&
                        existingKey?.config &&
                        (existingKey.config as BedrockConfig).region && (
                          <span className="text-sm">
                            {localize(language, "Current:", "当前：")}{" "}
                            <code className="rounded bg-muted px-1 py-0.5">
                              {(existingKey.config as BedrockConfig).region}
                            </code>
                          </span>
                        )}
                    </FormDescription>
                    <FormControl>
                      <Input
                        {...field}
                        placeholder={
                          mode === "update" && existingKey?.config
                            ? ((existingKey.config as BedrockConfig).region ??
                              "")
                            : localize(
                                language,
                                "e.g., us-east-1",
                                "例如：us-east-1",
                              )
                        }
                        data-1p-ignore
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="awsAccessKeyId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {localize(
                        language,
                        "AWS Access Key ID",
                        "AWS Access Key ID",
                      )}
                      {!isLangfuseCloud && (
                        <span className="font-normal text-muted-foreground">
                          {" "}
                          {localize(language, "(optional)", "（可选）")}
                        </span>
                      )}
                    </FormLabel>
                    <FormDescription>
                      {mode === "update"
                        ? localize(
                            language,
                            "Leave empty to keep existing credentials. To update, provide both Access Key ID and Secret Access Key.",
                            "留空可保留现有凭证。如需更新，请同时提供 Access Key ID 和 Secret Access Key。",
                          )
                        : isLangfuseCloud
                          ? localize(
                              language,
                              "These should be long-lived credentials for an AWS user with `bedrock:InvokeModel` permission.",
                              "这里应填写具有 `bedrock:InvokeModel` 权限的 AWS 用户长期凭证。",
                            )
                          : localize(
                              language,
                              "For self-hosted deployments, AWS credentials are optional. When omitted, authentication will use the AWS SDK default credential provider chain.",
                              "对于自托管部署，AWS 凭证为可选项。留空时，认证将使用 AWS SDK 默认凭证提供链。",
                            )}
                    </FormDescription>
                    <FormControl>
                      <Input
                        {...field}
                        placeholder={
                          mode === "update"
                            ? existingKey?.displaySecretKey ===
                              "Default AWS credentials"
                              ? localize(
                                  language,
                                  "Using default AWS credentials",
                                  "使用默认 AWS 凭证",
                                )
                              : localize(
                                  language,
                                  "•••••••• (existing credentials preserved if empty)",
                                  "••••••••（留空则保留现有凭证）",
                                )
                            : undefined
                        }
                        autoComplete="off"
                        data-1p-ignore
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="awsSecretAccessKey"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {localize(
                        language,
                        "AWS Secret Access Key",
                        "AWS Secret Access Key",
                      )}
                      {!isLangfuseCloud && (
                        <span className="font-normal text-muted-foreground">
                          {" "}
                          {localize(language, "(optional)", "（可选）")}
                        </span>
                      )}
                    </FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        type="password"
                        placeholder={
                          mode === "update"
                            ? existingKey?.displaySecretKey ===
                              "Default AWS credentials"
                              ? localize(
                                  language,
                                  "Using default AWS credentials",
                                  "使用默认 AWS 凭证",
                                )
                              : existingKey?.displaySecretKey
                                ? localize(
                                    language,
                                    `${existingKey.displaySecretKey} (preserved if empty)`,
                                    `${existingKey.displaySecretKey}（留空则保留）`,
                                  )
                                : localize(
                                    language,
                                    "•••••••• (existing credentials preserved if empty)",
                                    "••••••••（留空则保留现有凭证）",
                                  )
                            : undefined
                        }
                        autoComplete="new-password"
                        data-1p-ignore
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {!isLangfuseCloud && (
                <div className="space-y-2 border-l-2 border-blue-200 pl-4 text-sm text-muted-foreground">
                  <p>
                    <strong>
                      {localize(
                        language,
                        "Default credential provider chain:",
                        "默认凭证提供链：",
                      )}
                    </strong>{" "}
                    {localize(
                      language,
                      "When AWS credentials are omitted, the system will automatically check for credentials in this order:",
                      "当未提供 AWS 凭证时，系统会按以下顺序自动查找凭证：",
                    )}
                  </p>
                  <ul className="ml-2 list-inside list-disc space-y-1">
                    <li>
                      {localize(
                        language,
                        "Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)",
                        "环境变量（AWS_ACCESS_KEY_ID、AWS_SECRET_ACCESS_KEY）",
                      )}
                    </li>
                    <li>
                      {localize(
                        language,
                        "AWS credentials file (~/.aws/credentials)",
                        "AWS 凭证文件（~/.aws/credentials）",
                      )}
                    </li>
                    <li>
                      {localize(
                        language,
                        "IAM roles for EC2 instances",
                        "EC2 实例的 IAM 角色",
                      )}
                    </li>
                    <li>
                      {localize(
                        language,
                        "IAM roles for ECS tasks",
                        "ECS 任务的 IAM 角色",
                      )}
                    </li>
                  </ul>
                  <p>
                    <a
                      href="https://docs.aws.amazon.com/sdk-for-javascript/v3/developer-guide/setting-credentials-node.html"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 underline hover:text-blue-800"
                    >
                      {localize(
                        language,
                        "Learn more about AWS credential providers →",
                        "了解更多 AWS 凭证提供方式 →",
                      )}
                    </a>
                  </p>
                </div>
              )}
            </>
          ) : currentAdapter === LLMAdapter.VertexAI ? (
            <>
              {/* Vertex AI ADC option for self-hosted only, create mode only */}
              {!isLangfuseCloud && mode === "create" && (
                <FormItem>
                  <span className="row flex">
                    <span className="flex-1">
                      <FormLabel>
                        {localize(
                          language,
                          "Use Application Default Credentials (ADC)",
                          "使用应用默认凭证（ADC）",
                        )}
                      </FormLabel>
                      <FormDescription>
                        {localize(
                          language,
                          "When enabled, authentication uses the GCP environment's default credentials instead of a service account key.",
                          "启用后，认证将使用 GCP 环境中的默认凭证，而不是服务账号密钥。",
                        )}
                      </FormDescription>
                    </span>
                    <FormControl>
                      <Switch
                        checked={
                          form.watch("secretKey") ===
                          VERTEXAI_USE_DEFAULT_CREDENTIALS
                        }
                        onCheckedChange={(checked) => {
                          if (checked) {
                            form.setValue(
                              "secretKey",
                              VERTEXAI_USE_DEFAULT_CREDENTIALS,
                            );
                          } else {
                            form.setValue("secretKey", "");
                          }
                        }}
                      />
                    </FormControl>
                  </span>
                </FormItem>
              )}

              {/* Service Account Key - hidden when ADC is enabled */}
              {(isLangfuseCloud ||
                form.watch("secretKey") !==
                  VERTEXAI_USE_DEFAULT_CREDENTIALS) && (
                <FormField
                  control={form.control}
                  name="secretKey"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>
                        {localize(
                          language,
                          "GCP Service Account Key (JSON)",
                          "GCP 服务账号密钥（JSON）",
                        )}
                      </FormLabel>
                      <FormDescription>
                        {isLangfuseCloud
                          ? localize(
                              language,
                              "Your API keys are stored encrypted on our servers.",
                              "你的 API 密钥会以加密形式存储在我们的服务器上。",
                            )
                          : localize(
                              language,
                              "Your API keys are stored encrypted in your database.",
                              "你的 API 密钥会以加密形式存储在你的数据库中。",
                            )}
                      </FormDescription>
                      <FormDescription className="text-dark-yellow">
                        {localize(
                          language,
                          "Paste your GCP service account JSON key here. The service account must have `Vertex AI User` role permissions. Example JSON:",
                          "请在此粘贴你的 GCP 服务账号 JSON 密钥。该服务账号必须具有 `Vertex AI User` 角色权限。示例 JSON：",
                        )}
                        <pre className="text-xs">
                          {`{
  "type": "service_account",
  "project_id": "<project_id>",
  "private_key_id": "<private_key_id>",
  "private_key": "<private_key>",
  "client_email": "<client_email>",
  "client_id": "<client_id>",
  "auth_uri": "<auth_uri>",
  "token_uri": "<token_uri>",
  "auth_provider_x509_cert_url": "<auth_provider_x509_cert_url>",
  "client_x509_cert_url": "<client_x509_cert_url>",
}`}
                        </pre>
                      </FormDescription>
                      <FormControl>
                        <Input
                          {...field}
                          placeholder={
                            mode === "update"
                              ? existingKey?.displaySecretKey
                              : localize(
                                  language,
                                  '{"type": "service_account", ...}',
                                  '{"type": "service_account", ...}',
                                )
                          }
                          autoComplete="off"
                          spellCheck="false"
                          autoCapitalize="off"
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              {/* ADC info box for self-hosted */}
              {!isLangfuseCloud &&
                form.watch("secretKey") ===
                  VERTEXAI_USE_DEFAULT_CREDENTIALS && (
                  <div className="space-y-2 border-l-2 border-blue-200 pl-4 text-sm text-muted-foreground">
                    <p>
                      <strong>
                        {localize(
                          language,
                          "Application Default Credentials (ADC):",
                          "应用默认凭证（ADC）：",
                        )}
                      </strong>{" "}
                      {localize(
                        language,
                        "When enabled, the system will automatically check for credentials in this order:",
                        "启用后，系统会按以下顺序自动查找凭证：",
                      )}
                    </p>
                    <ul className="ml-2 list-inside list-disc space-y-1">
                      <li>
                        {localize(
                          language,
                          "Environment variable (GOOGLE_APPLICATION_CREDENTIALS)",
                          "环境变量（GOOGLE_APPLICATION_CREDENTIALS）",
                        )}
                      </li>
                      <li>
                        {localize(
                          language,
                          "gcloud CLI credentials (gcloud auth application-default login)",
                          "gcloud CLI 凭证（gcloud auth application-default login）",
                        )}
                      </li>
                      <li>
                        {localize(
                          language,
                          "GKE Workload Identity",
                          "GKE Workload Identity",
                        )}
                      </li>
                      <li>
                        {localize(
                          language,
                          "Cloud Run service account",
                          "Cloud Run 服务账号",
                        )}
                      </li>
                      <li>
                        {localize(
                          language,
                          "GCE instance service account (metadata service)",
                          "GCE 实例服务账号（元数据服务）",
                        )}
                      </li>
                    </ul>
                    <p>
                      <a
                        href="https://cloud.google.com/docs/authentication/application-default-credentials"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 underline hover:text-blue-800"
                      >
                        {localize(
                          language,
                          "Learn more about GCP Application Default Credentials →",
                          "了解更多 GCP 应用默认凭证 →",
                        )}
                      </a>
                    </p>
                  </div>
                )}
            </>
          ) : (
            <FormField
              control={form.control}
              name="secretKey"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {localize(language, "API Key", "API 密钥")}
                  </FormLabel>
                  <FormDescription>
                    {isLangfuseCloud
                      ? localize(
                          language,
                          "Your API keys are stored encrypted on our servers.",
                          "你的 API 密钥会以加密形式存储在我们的服务器上。",
                        )
                      : localize(
                          language,
                          "Your API keys are stored encrypted in your database.",
                          "你的 API 密钥会以加密形式存储在你的数据库中。",
                        )}
                  </FormDescription>
                  <FormControl>
                    <Input
                      {...field}
                      placeholder={
                        mode === "update"
                          ? existingKey?.displaySecretKey
                          : undefined
                      }
                      autoComplete="off"
                      spellCheck="false"
                      autoCapitalize="off"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          )}

          {/* Azure Base URL - Always required for Azure */}
          {currentAdapter === LLMAdapter.Azure && (
            <FormField
              control={form.control}
              name="baseURL"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {localize(language, "API Base URL", "API Base URL")}
                  </FormLabel>
                  <FormDescription>
                    {localize(
                      language,
                      "Please add the base URL in the following format (or compatible API):",
                      "请按以下格式填写 Base URL（或兼容 API）：",
                    )}
                    https://&#123;instanceName&#125;.openai.azure.com/openai/deployments
                  </FormDescription>
                  <FormControl>
                    <Input
                      {...field}
                      placeholder={localize(
                        language,
                        "https://your-instance.openai.azure.com/openai/deployments",
                        "https://your-instance.openai.azure.com/openai/deployments",
                      )}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          )}

          {/* Custom models: top-level for Azure/Bedrock */}
          {isCustomModelsRequired(currentAdapter) && renderCustomModelsField()}

          {/* Extra headers - show for Azure in main section (Azure has no advanced settings) */}
          {currentAdapter === LLMAdapter.Azure && renderExtraHeadersField()}

          {hasAdvancedSettings(currentAdapter) && (
            <div className="flex items-center">
              <Button
                type="button"
                variant="link"
                size="sm"
                className="flex items-center pl-0"
                onClick={() => setShowAdvancedSettings(!showAdvancedSettings)}
              >
                <span>
                  {showAdvancedSettings
                    ? localize(
                        language,
                        "Hide advanced settings",
                        "隐藏高级设置",
                      )
                    : localize(
                        language,
                        "Show advanced settings",
                        "显示高级设置",
                      )}
                </span>
                <ChevronDown
                  className={`ml-1 h-4 w-4 transition-transform ${showAdvancedSettings ? "rotate-180" : "rotate-0"}`}
                />
              </Button>
            </div>
          )}

          {hasAdvancedSettings(currentAdapter) && showAdvancedSettings && (
            <div className="space-y-4 border-t pt-4">
              {/* baseURL */}
              <FormField
                control={form.control}
                name="baseURL"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {localize(language, "API Base URL", "API Base URL")}
                    </FormLabel>
                    <FormDescription>
                      {localize(
                        language,
                        "Leave blank to use the default base URL for the given LLM adapter.",
                        "留空则使用该 LLM 适配器的默认 Base URL。",
                      )}{" "}
                      {currentAdapter === LLMAdapter.OpenAI && (
                        <span>
                          {localize(
                            language,
                            "OpenAI default: https://api.openai.com/v1",
                            "OpenAI 默认值：https://api.openai.com/v1",
                          )}
                        </span>
                      )}
                      {currentAdapter === LLMAdapter.Anthropic && (
                        <span>
                          {localize(
                            language,
                            "Anthropic default: https://api.anthropic.com (excluding /v1/messages)",
                            "Anthropic 默认值：https://api.anthropic.com（不包含 /v1/messages）",
                          )}
                        </span>
                      )}
                    </FormDescription>

                    <FormControl>
                      <Input
                        {...field}
                        placeholder={localize(language, "default", "默认")}
                      />
                    </FormControl>

                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* VertexAI Location */}
              {currentAdapter === LLMAdapter.VertexAI && (
                <FormField
                  control={form.control}
                  name="vertexAILocation"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>
                        {localize(
                          language,
                          "Location (Optional)",
                          "位置（可选）",
                        )}
                      </FormLabel>
                      <FormDescription>
                        {localize(
                          language,
                          "Google Cloud region (e.g., global, us-central1, europe-west4). Defaults to",
                          "Google Cloud 区域（例如：global、us-central1、europe-west4）。默认值为",
                        )}{" "}
                        <span className="font-medium">
                          {localize(language, "global", "global")}
                        </span>{" "}
                        {localize(
                          language,
                          "as required for Gemini 3 models.",
                          "，这是 Gemini 3 模型所需的配置。",
                        )}
                      </FormDescription>
                      <FormControl>
                        <Input
                          {...field}
                          placeholder={localize(language, "global", "global")}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              {/* Extra Headers */}
              {currentAdapter === LLMAdapter.OpenAI &&
                renderExtraHeadersField()}

              {/* With default models */}
              <FormField
                control={form.control}
                name="withDefaultModels"
                render={({ field }) => (
                  <FormItem>
                    <span className="row flex">
                      <span className="flex-1">
                        <FormLabel>
                          {localize(
                            language,
                            "Enable default models",
                            "启用默认模型",
                          )}
                        </FormLabel>
                        <FormDescription>
                          {localize(
                            language,
                            "Default models for the selected adapter will be available in Langfuse features.",
                            "所选适配器的默认模型将在 Langfuse 功能中可用。",
                          )}
                        </FormDescription>
                      </span>

                      <FormControl>
                        <Switch
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                    </span>

                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Custom model names */}
              {!isCustomModelsRequired(currentAdapter) &&
                renderCustomModelsField()}
            </div>
          )}
        </DialogBody>

        <DialogFooter>
          <div className="flex flex-col gap-4">
            <Button
              type="submit"
              className="w-full"
              loading={form.formState.isSubmitting}
            >
              {mode === "create"
                ? localize(language, "Create connection", "创建连接")
                : localize(language, "Save changes", "保存更改")}
            </Button>
            {form.formState.errors.root && (
              <FormMessage>{form.formState.errors.root.message}</FormMessage>
            )}
          </div>
        </DialogFooter>
      </form>
    </Form>
  );
}
