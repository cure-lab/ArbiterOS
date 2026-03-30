import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/src/components/ui/card";
import { Badge } from "@/src/components/ui/badge";
import { useMemo } from "react";
import { usePriceUnitMultiplier } from "@/src/features/models/hooks/usePriceUnitMultiplier";
import Decimal from "decimal.js";
import { getMaxDecimals } from "@/src/features/models/utils";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

type MatchedTierCardProps = {
  tier: {
    id: string;
    name: string;
    priority: number;
    isDefault: boolean;
    prices: Record<string, number>;
  };
};

export type { MatchedTierCardProps };

export function MatchedTierCard({ tier }: MatchedTierCardProps) {
  const { priceUnit, priceUnitMultiplier } = usePriceUnitMultiplier();
  const { language } = useLanguage();
  const isChinese = language === "zh-CN";
  const localizedPriceUnit =
    priceUnit === "per unit"
      ? localize(language, "per unit", "按单位")
      : priceUnit === "per 1K units"
        ? localize(language, "per 1K units", "每 1K 单位")
        : priceUnit === "per 1M units"
          ? localize(language, "per 1M units", "每 1M 单位")
          : priceUnit;

  const maxDecimals = useMemo(
    () =>
      Math.max(
        ...Object.values(tier.prices).map((price) =>
          getMaxDecimals(price, priceUnitMultiplier),
        ),
      ),
    [tier.prices, priceUnitMultiplier],
  );

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {localize(language, "Matched Pricing Tier", "匹配的定价层级")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2">
          <span className="text-base font-semibold">{tier.name}</span>
          {tier.isDefault && (
            <Badge variant="secondary" className="text-xs">
              {localize(language, "Default", "默认")}
            </Badge>
          )}
          <span className="text-xs text-muted-foreground">
            {localize(language, "Priority:", "优先级：")} {tier.priority}
          </span>
        </div>

        <div>
          <div className="mb-2 text-xs font-medium text-muted-foreground">
            {isChinese
              ? `${localize(language, "Prices", "价格")}（${localizedPriceUnit}）`
              : `${localize(language, "Prices", "价格")} (${localizedPriceUnit}):`}
          </div>
          <div className="space-y-1.5">
            {Object.entries(tier.prices).map(([usageType, price]) => (
              <div
                key={usageType}
                className="flex items-center justify-between rounded bg-muted/50 px-3 py-1.5"
              >
                <span className="font-mono text-xs text-muted-foreground">
                  {usageType}:
                </span>
                <span className="font-mono text-sm font-semibold">
                  $
                  {new Decimal(price)
                    .mul(priceUnitMultiplier)
                    .toFixed(maxDecimals)}
                </span>
              </div>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
