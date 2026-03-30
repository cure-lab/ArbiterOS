import { useRouter } from "next/router";
import { api } from "@/src/utils/api";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/src/components/ui/card";
import { DeleteModelButton } from "@/src/features/models/components/DeleteModelButton";
import { EditModelButton } from "@/src/features/models/components/EditModelButton";
import { CloneModelButton } from "@/src/features/models/components/CloneModelButton";
import { TestModelMatchButton } from "@/src/features/models/components/test-match/TestModelMatchButton";
import { JSONView } from "@/src/components/ui/CodeJsonViewer";
import Link from "next/link";
import { Button } from "@/src/components/ui/button";
import { getMaxDecimals } from "@/src/features/models/utils";
import Decimal from "decimal.js";
import { PriceUnitSelector } from "@/src/features/models/components/PriceUnitSelector";
import { useMemo, useState } from "react";
import { usePriceUnitMultiplier } from "@/src/features/models/hooks/usePriceUnitMultiplier";
import Generations from "@/src/components/table/use-cases/observations";
import Page from "@/src/components/layouts/page";
import { SquareArrowOutUpRight, Info as InfoIcon } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/src/components/ui/select";
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/src/components/ui/hover-card";
import { CodeMirrorEditor } from "@/src/components/editor";
import { useEffect } from "react";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export default function ModelDetailPage() {
  const router = useRouter();
  const { priceUnit, priceUnitMultiplier } = usePriceUnitMultiplier();
  const { language } = useLanguage();
  const projectId = router.query.projectId as string;
  const modelId = router.query.modelId as string;
  const pricingTierParam = router.query.pricingTier as string | undefined;
  const hasWriteAccess = useHasProjectAccess({
    projectId,
    scope: "models:CUD",
  });

  const { data: model, isLoading } = api.models.getById.useQuery(
    { projectId, modelId },
    { enabled: !!projectId && !!modelId },
  );

  // Get default tier or first tier by priority
  const defaultTier = useMemo(() => {
    if (!model?.pricingTiers || model.pricingTiers.length === 0) return null;
    return model.pricingTiers.find((t) => t.isDefault) || model.pricingTiers[0];
  }, [model?.pricingTiers]);

  // State for selected pricing tier - initialize from URL param
  const [selectedTierId, setSelectedTierId] = useState<string | null>(
    pricingTierParam ?? null,
  );

  // Sync with URL parameter when it changes
  useEffect(() => {
    if (pricingTierParam && model?.pricingTiers) {
      const tierExists = model.pricingTiers.some(
        (t) => t.id === pricingTierParam,
      );
      if (tierExists) {
        setSelectedTierId(pricingTierParam);
      }
    }
  }, [pricingTierParam, model?.pricingTiers]);

  // Get the active tier (selected or default)
  const activeTier = useMemo(() => {
    if (!model?.pricingTiers) return null;
    if (selectedTierId) {
      return model.pricingTiers.find((t) => t.id === selectedTierId) || null;
    }
    return defaultTier;
  }, [model?.pricingTiers, selectedTierId, defaultTier]);

  const maxDecimals = useMemo(
    () =>
      Math.max(
        ...Object.values(activeTier?.prices ?? {}).map((price) =>
          getMaxDecimals(price, priceUnitMultiplier),
        ),
      ),
    [activeTier?.prices, priceUnitMultiplier],
  );
  const localizedPriceUnit =
    priceUnit === "per unit"
      ? localize(language, "per unit", "按单位")
      : priceUnit === "per 1K units"
        ? localize(language, "per 1K units", "每 1K 单位")
        : priceUnit === "per 1M units"
          ? localize(language, "per 1M units", "每 1M 单位")
          : priceUnit;

  // If not found, redirect to models page
  if (!isLoading && !model) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center">
        <div className="mb-4 text-xl font-medium">
          {localize(language, "Model not found", "未找到模型")}
        </div>
        <Button variant="outline" asChild>
          <Link href={`/project/${projectId}/settings/models`}>
            {localize(language, "Return to Models page", "返回模型页面")}
          </Link>
        </Button>
      </div>
    );
  }

  const isLangfuseModel = !Boolean(model?.projectId);

  if (isLoading || !model) {
    return (
      <div className="p-3">{localize(language, "Loading...", "加载中...")}</div>
    );
  }

  return (
    <Page
      scrollable
      headerProps={{
        title: model.modelName,
        help: {
          description: localize(
            language,
            "Model configuration and pricing details",
            "模型配置和定价详情",
          ),
          href: "https://langfuse.com/docs/model-usage-and-cost",
        },
        breadcrumb: [
          {
            name: localize(language, "Settings", "设置"),
            href: `/project/${router.query.projectId as string}/settings`,
          },
          {
            name: localize(language, "Models", "模型"),
            href: `/project/${router.query.projectId as string}/settings/models`,
          },
          { name: model.modelName },
        ],
        actionButtonsRight: (
          <div className="flex gap-2">
            {hasWriteAccess && (
              <>
                <TestModelMatchButton projectId={projectId} variant="outline" />
                {!isLangfuseModel ? (
                  <>
                    <EditModelButton projectId={projectId} modelData={model} />
                    <DeleteModelButton
                      projectId={projectId}
                      modelData={model}
                      onSuccess={() => {
                        void router.push(
                          `/project/${projectId}/settings/models`,
                        );
                      }}
                    />
                  </>
                ) : (
                  <CloneModelButton projectId={projectId} modelData={model} />
                )}
              </>
            )}
          </div>
        ),
      }}
    >
      <div className="grid grid-cols-2 gap-6 p-2">
        <Card>
          <CardHeader>
            <CardTitle>
              {localize(language, "Model configuration", "模型配置")}
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div>
              <div className="text-sm font-medium text-muted-foreground">
                {localize(language, "Match Pattern", "匹配模式")}
              </div>
              <div className="mt-1 font-mono text-sm">{model.matchPattern}</div>
            </div>

            <div>
              <div className="text-sm font-medium text-muted-foreground">
                {localize(language, "Maintained by", "维护者")}
              </div>
              <div className="mt-1 text-sm">
                {isLangfuseModel
                  ? "Langfuse"
                  : localize(language, "User", "用户")}
              </div>
            </div>

            <div>
              <div className="text-sm font-medium text-muted-foreground">
                {localize(language, "Tokenizer", "Tokenizer")}
              </div>
              <div className="mt-1 text-sm">
                {model.tokenizerId || localize(language, "None", "无")}
              </div>
            </div>

            {model.tokenizerId && (
              <div>
                <div className="text-sm font-medium text-muted-foreground">
                  {localize(language, "Tokenizer Config", "Tokenizer 配置")}
                </div>
                <pre className="mt-1 rounded bg-muted p-2 text-sm">
                  <JSONView json={model.tokenizerConfig} />
                </pre>
              </div>
            )}
          </CardContent>
        </Card>

        <Card id="pricing-section">
          <CardHeader>
            <div className="flex flex-col gap-2">
              <CardTitle>{localize(language, "Pricing", "定价")}</CardTitle>
              {model.pricingTiers.length > 1 && (
                <div className="flex items-center gap-4">
                  <label className="text-sm font-medium text-muted-foreground">
                    {localize(language, "Pricing Tier", "定价层级")}
                  </label>
                  <Select
                    value={activeTier?.id ?? ""}
                    onValueChange={setSelectedTierId}
                  >
                    <SelectTrigger className="w-[200px]">
                      <SelectValue
                        placeholder={localize(
                          language,
                          "Select tier",
                          "选择层级",
                        )}
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {model.pricingTiers.map((tier) => (
                        <SelectItem key={tier.id} value={tier.id}>
                          {tier.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {activeTier && !activeTier.isDefault && (
                    <HoverCard openDelay={200} closeDelay={100}>
                      <HoverCardTrigger asChild>
                        <Button
                          variant="ghost"
                          className="inline-flex h-auto items-center gap-1.5 p-0 text-xs text-muted-foreground hover:bg-transparent hover:text-accent-foreground"
                          size="sm"
                        >
                          <InfoIcon className="h-3 w-3" />
                          <span>
                            {localize(language, "Conditions", "条件")}
                          </span>
                        </Button>
                      </HoverCardTrigger>
                      <HoverCardContent
                        className="max-h-[80vh] w-[400px] overflow-auto"
                        collisionPadding={20}
                      >
                        <p className="text-sm font-medium">
                          {localize(
                            language,
                            "Pricing Tier Conditions",
                            "定价层级条件",
                          )}
                        </p>
                        <p className="pt-2 text-sm text-muted-foreground">
                          {localize(
                            language,
                            "This tier is applied when the following conditions are met:",
                            "满足以下条件时将应用此层级：",
                          )}
                        </p>
                        <div className="mt-2">
                          <CodeMirrorEditor
                            mode="json"
                            value={JSON.stringify(
                              activeTier.conditions,
                              null,
                              2,
                            )}
                            onChange={() => {}} // Read-only
                            className="max-h-[250px] overflow-y-auto"
                            editable={false}
                          />
                        </div>
                      </HoverCardContent>
                    </HoverCard>
                  )}
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col gap-2">
              <div className="grid grid-cols-2 gap-2 border-b border-border text-sm font-medium text-muted-foreground">
                <span>{localize(language, "Usage Type", "用量类型")}</span>
                <span className="flex items-center gap-2">
                  <span>
                    {localize(language, "Price", "价格")} {localizedPriceUnit}
                  </span>
                  <PriceUnitSelector />
                </span>
              </div>
              {activeTier &&
                Object.entries(activeTier.prices)
                  // Sort by price ascending
                  .sort((a, b) => a[1] - b[1])
                  .map(([usageType, price]) => (
                    <div
                      key={usageType}
                      className="grid grid-cols-2 gap-2 rounded px-1 py-0.5 text-sm"
                    >
                      <span className="break-all">{usageType}</span>
                      <span className="text-left font-mono">
                        $
                        {new Decimal(price)
                          .mul(priceUnitMultiplier)
                          .toFixed(maxDecimals)}
                      </span>
                    </div>
                  ))}
            </div>
          </CardContent>
        </Card>

        <Card className="col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>
                {localize(language, "Model observations", "模型观测")}
              </span>
              <Button variant="ghost" asChild>
                <Link
                  href={`/project/${projectId}/observations`}
                  className="flex items-center gap-1"
                >
                  <span className="text-sm">
                    {localize(language, "View all", "查看全部")}
                  </span>
                  <SquareArrowOutUpRight className="h-4 w-4" />
                </Link>
              </Button>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex max-h-[calc(100vh-20rem)] flex-col">
              <Generations
                projectId={projectId}
                omittedFilter={["Model"]}
                modelId={model.id}
              />
            </div>
          </CardContent>
        </Card>
      </div>
    </Page>
  );
}
