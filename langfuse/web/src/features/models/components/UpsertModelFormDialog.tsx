import Link from "next/link";
import { useEffect, useState, useMemo } from "react";
import { useForm, useFieldArray } from "react-hook-form";

import { CodeMirrorEditor } from "@/src/components/editor";
import { Button } from "@/src/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogBody,
} from "@/src/components/ui/dialog";
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
import {
  type FormUpsertModel,
  FormUpsertModelSchema,
  type GetModelResult,
} from "@/src/features/models/validation";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { api } from "@/src/utils/api";
import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/router";

import { showSuccessToast } from "@/src/features/notifications/showSuccessToast";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";
import { PricingSection } from "./pricing-tiers/PricingSection";

type UpsertModelDialogProps =
  | {
      action: "create";
      children: React.ReactNode;
      projectId: string;
      prefilledModelData?: {
        modelName?: string;
        prices?: Record<string, number>;
      };
      className?: string;
    }
  | {
      action: "edit" | "clone";
      children: React.ReactNode;
      projectId: string;
      modelData: GetModelResult;
      className?: string;
    };

export const UpsertModelFormDialog = (({
  children,
  ...props
}: UpsertModelDialogProps) => {
  const capture = usePostHogClientCapture();
  const router = useRouter();
  const [formError, setFormError] = useState<string | null>(null);
  const utils = api.useUtils();
  const [open, setOpen] = useState(false);
  const { language } = useLanguage();

  // Initialize form default values
  const defaultValues: FormUpsertModel = useMemo(() => {
    if (props.action !== "create") {
      // EDIT or CLONE: Load all tiers
      const loadedTiers = props.modelData.pricingTiers.map((tier) => ({
        id: tier.id,
        name: tier.name,
        isDefault: tier.isDefault,
        priority: tier.priority,
        conditions: tier.conditions,
        prices: tier.prices,
      }));

      return {
        modelName: props.modelData.modelName,
        matchPattern: props.modelData.matchPattern,
        tokenizerId: props.modelData.tokenizerId,
        tokenizerConfig: JSON.stringify(props.modelData.tokenizerConfig ?? {}),
        pricingTiers: loadedTiers,
      };
    } else {
      // CREATE: Start with 1 default tier
      return {
        modelName: props.prefilledModelData?.modelName ?? "",
        matchPattern: props.prefilledModelData?.modelName
          ? `(?i)^(${props.prefilledModelData?.modelName})$`
          : "",
        tokenizerId: null,
        tokenizerConfig: null,
        pricingTiers: [
          {
            name: "Standard",
            isDefault: true,
            priority: 0,
            conditions: [],
            prices: props.prefilledModelData?.prices ?? {
              input: 0.000001,
              output: 0.000002,
            },
          },
        ],
      };
    }
  }, [props]);

  const form = useForm({
    resolver: zodResolver(FormUpsertModelSchema),
    defaultValues,
  });

  const modelName = form.watch("modelName");
  const matchPattern = form.watch("matchPattern");
  const tokenizerId = form.watch("tokenizerId");

  const { fields, append, remove } = useFieldArray({
    control: form.control,
    name: "pricingTiers",
  });

  // Watch default tier prices for syncing
  const defaultTierIndex = fields.findIndex((f) => f.isDefault);
  const defaultTierPrices =
    defaultTierIndex !== -1
      ? form.watch(`pricingTiers.${defaultTierIndex}.prices`)
      : undefined;

  // Compute keys signature - memoized to prevent unnecessary updates
  const defaultKeysSignature = useMemo(() => {
    if (!defaultTierPrices) return "";
    return Object.keys(defaultTierPrices).sort().join(",");
  }, [defaultTierPrices]);

  // Auto-assign priorities based on order
  useEffect(() => {
    fields.forEach((field, index) => {
      const tier = form.getValues(`pricingTiers.${index}`);
      const expectedPriority = tier.isDefault ? 0 : index;
      if (tier.priority !== expectedPriority) {
        form.setValue(`pricingTiers.${index}.priority`, expectedPriority);
      }
    });
  }, [fields, form]);

  // Sync usage keys from default tier to all non-default tiers
  useEffect(() => {
    if (!defaultTierPrices || defaultTierIndex === -1 || !defaultKeysSignature)
      return;

    const defaultKeys = defaultKeysSignature.split(",");

    fields.forEach((field, index) => {
      if (field.isDefault) return;

      const currentPrices = form.getValues(`pricingTiers.${index}.prices`);
      const currentKeys = Object.keys(currentPrices).sort();

      // Only update if keys don't match
      const keysMatch =
        defaultKeys.length === currentKeys.length &&
        defaultKeys.every((key, i) => key === currentKeys[i]);

      if (!keysMatch) {
        const newPrices: Record<string, number> = {};
        defaultKeys.forEach((key) => {
          newPrices[key] = currentPrices[key] ?? 0;
        });
        form.setValue(`pricingTiers.${index}.prices`, newPrices);
      }
    });
  }, [defaultKeysSignature, defaultTierPrices, defaultTierIndex, fields, form]);

  // prefill match pattern if model name changes
  useEffect(() => {
    const getRegexString = (modelName: string) => `(?i)^(${modelName})$`;

    if (
      modelName &&
      (!matchPattern ||
        matchPattern === `(?i)^(${modelName.slice(0, -1)})$` ||
        matchPattern === `(?i)^(${modelName})$`)
    ) {
      form.setValue("matchPattern", getRegexString(modelName));
    }
  }, [modelName, matchPattern, form]);

  const upsertModelMutation = api.models.upsert.useMutation({
    onSuccess: (upsertedModel) => {
      utils.models.invalidate();
      form.reset();
      setOpen(false);
      showSuccessToast({
        title:
          props.action === "edit"
            ? localize(language, "Model updated", "模型已更新")
            : localize(language, "Model created", "模型已创建"),
        description:
          props.action === "edit"
            ? localize(
                language,
                `The model '${upsertedModel.modelName}' has been successfully updated. New generations will use these model prices.`,
                `模型“${upsertedModel.modelName}”已成功更新。新的 generations 将使用这些模型价格。`,
              )
            : localize(
                language,
                `The model '${upsertedModel.modelName}' has been successfully created. New generations will use these model prices.`,
                `模型“${upsertedModel.modelName}”已成功创建。新的 generations 将使用这些模型价格。`,
              ),
      });
      router.push(
        `/project/${props.projectId}/settings/models/${upsertedModel.id}`,
      );
    },
    onError: (error) => setFormError(error.message),
  });

  const onSubmit = async (values: FormUpsertModel) => {
    capture("models:new_form_submit");

    // Transform FormPricingTier[] -> PricingTierInput[] (remove id field and filter prices)
    const pricingTiers = values.pricingTiers.map(({ id: _id, ...tier }) => ({
      ...tier,
      prices: Object.fromEntries(
        Object.entries(tier.prices).filter(([_, value]) => value != null),
      ) as Record<string, number>,
    }));

    await upsertModelMutation
      .mutateAsync({
        modelId: props.action === "edit" ? props.modelData.id : null,
        projectId: props.projectId,
        modelName:
          props.action === "edit"
            ? props.modelData.modelName
            : values.modelName,
        matchPattern: values.matchPattern,
        pricingTiers,
        tokenizerId: values.tokenizerId,
        tokenizerConfig:
          values.tokenizerConfig &&
          typeof JSON.parse(values.tokenizerConfig) === "object"
            ? (JSON.parse(values.tokenizerConfig) as Record<string, number>)
            : undefined,
      })
      .catch((error) => {
        setFormError(error.message);
      });
  };

  const addTier = () => {
    const defaultTier = fields.find((f) => f.isDefault);
    if (!defaultTier) return;

    append({
      name: `Custom Tier ${fields.length}`,
      isDefault: false,
      priority: fields.length,
      conditions: [
        {
          usageDetailPattern: "^input",
          operator: "gt",
          value: 0,
          caseSensitive: false,
        },
      ],
      prices: { ...defaultTier.prices }, // Copy default tier prices
    });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(newOpen) => {
        if (!newOpen) {
          form.reset();
          setFormError(null);
        }
        setOpen(newOpen);
      }}
    >
      <DialogTrigger
        asChild
        onClick={() => setOpen(true)}
        className={props.className}
        title={
          props.action === "create"
            ? localize(language, "Create model definition", "创建模型定义")
            : localize(language, "Edit model definition", "编辑模型定义")
        }
      >
        {children}
      </DialogTrigger>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>
            {props.action === "create"
              ? localize(language, "Create Model", "创建模型")
              : props.action === "clone"
                ? localize(language, "Clone Model", "克隆模型")
                : localize(language, "Edit Model", "编辑模型")}
          </DialogTitle>
          {props.action === "edit" && (
            <DialogDescription>{props.modelData.modelName}</DialogDescription>
          )}
          {props.action === "create" && (
            <DialogDescription>
              {localize(
                language,
                "Create a new model configuration to track generation costs.",
                "创建新的模型配置以跟踪 generation 成本。",
              )}
            </DialogDescription>
          )}
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex flex-1 flex-col overflow-hidden"
          >
            <DialogBody className="space-y-6">
              <FormField
                control={form.control}
                name="modelName"
                disabled={props.action === "edit"}
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {localize(language, "Model Name", "模型名称")}
                    </FormLabel>
                    <FormDescription>
                      {localize(
                        language,
                        "The name of the model. This will be used to reference the model in the API. You can track price changes of models by using the same name and match pattern.",
                        "模型名称。该名称将在 API 中用于引用模型。你可以通过使用相同的名称和匹配模式来追踪模型价格变化。",
                      )}
                    </FormDescription>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="matchPattern"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {localize(language, "Match pattern", "匹配模式")}
                    </FormLabel>
                    <FormDescription>
                      {localize(
                        language,
                        "Regular expression (Postgres syntax) to match ingested generations (model attribute) to this model definition. For an exact, case-insensitive match to a model name, use the expression: (?i)^(modelname)$",
                        "用于将摄取的 generations（model 属性）匹配到此模型定义的正则表达式（Postgres 语法）。若要对模型名称进行精确且不区分大小写的匹配，请使用表达式：(?i)^(modelname)$",
                      )}
                    </FormDescription>
                    <FormControl>
                      <Input {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* PRICING SECTION */}
              <PricingSection
                fields={fields}
                form={form}
                remove={remove}
                addTier={addTier}
              />

              <FormField
                control={form.control}
                name="tokenizerId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {localize(language, "Tokenizer", "Tokenizer")}
                    </FormLabel>
                    <Select
                      onValueChange={(tokenizerId) => {
                        field.onChange(tokenizerId);
                        if (tokenizerId === "None") {
                          form.setValue("tokenizerConfig", "{}");
                        }
                      }}
                      defaultValue={field.value ?? "None"}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue
                            placeholder={localize(
                              language,
                              "Select a unit",
                              "选择单位",
                            )}
                          />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {["openai", "claude", "None"].map((unit) => (
                          <SelectItem value={unit} key={unit}>
                            {unit}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormDescription>
                      {localize(
                        language,
                        "Optionally, Langfuse can tokenize the input and output of a generation if no unit counts are ingested. This is useful for e.g. streamed OpenAI completions. For details on the supported tokenizers, see the",
                        "可选地，当未摄取单位计数时，Langfuse 可以对 generation 的输入和输出进行分词。这对于流式 OpenAI completions 等场景很有帮助。有关支持的 tokenizer 详情，请参阅",
                      )}{" "}
                      <Link
                        href="https://langfuse.com/docs/model-usage-and-cost"
                        className="underline"
                        target="_blank"
                      >
                        {localize(language, "docs", "文档")}
                      </Link>
                      {localize(language, ".", "。")}
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              {tokenizerId && tokenizerId !== "None" && (
                <FormField
                  control={form.control}
                  name="tokenizerConfig"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>
                        {localize(
                          language,
                          "Tokenizer Config",
                          "Tokenizer 配置",
                        )}
                      </FormLabel>
                      <CodeMirrorEditor
                        mode="json"
                        value={field.value ?? "{}"}
                        onChange={field.onChange}
                      />
                      <FormDescription>
                        {localize(
                          language,
                          "The config for the tokenizer. Required for openai. See the",
                          "Tokenizer 的配置。openai 为必填。请参阅",
                        )}{" "}
                        <Link
                          href="https://langfuse.com/docs/model-usage-and-cost"
                          className="underline"
                          target="_blank"
                        >
                          {localize(language, "docs", "文档")}
                        </Link>{" "}
                        {localize(language, "for details.", "了解详情。")}
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
            </DialogBody>

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setOpen(false)}
              >
                {localize(language, "Cancel", "取消")}
              </Button>

              <Button type="submit" loading={upsertModelMutation.isPending}>
                {localize(language, "Submit", "提交")}
              </Button>
            </DialogFooter>
          </form>
          {formError ? (
            <p className="my-2 text-center text-sm font-medium text-destructive">
              <span className="font-semibold">
                {localize(language, "Error:", "错误：")}
              </span>{" "}
              {formError}
            </p>
          ) : null}
        </Form>
      </DialogContent>
    </Dialog>
  );
}) as React.FC<UpsertModelDialogProps>;
