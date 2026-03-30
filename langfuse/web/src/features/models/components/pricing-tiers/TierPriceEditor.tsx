import { MinusCircle, PlusCircle } from "lucide-react";
import { Button } from "@/src/components/ui/button";
import { Input } from "@/src/components/ui/input";
import { FormLabel } from "@/src/components/ui/form";
import { PricePreview } from "../PricePreview";
import type { UseFormReturn } from "react-hook-form";
import type { FormUpsertModel } from "../../validation";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

type TierPriceEditorProps = {
  tierIndex: number;
  form: UseFormReturn<FormUpsertModel>;
  isDefault: boolean;
};

export type { TierPriceEditorProps };

export function TierPriceEditor({
  tierIndex,
  form,
  isDefault,
}: TierPriceEditorProps) {
  const prices = form.watch(`pricingTiers.${tierIndex}.prices`) || {};
  const { language } = useLanguage();

  return (
    <div className="space-y-3">
      <FormLabel>{localize(language, "Prices", "价格")}</FormLabel>
      <div className="grid grid-cols-2 gap-1 text-sm text-muted-foreground">
        <span>{localize(language, "Usage type", "用量类型")}</span>
        <span>{localize(language, "Price", "价格")}</span>
      </div>
      {Object.entries(prices).map(([key, value]) => (
        <div key={key} className="grid grid-cols-2 gap-1">
          <Input
            placeholder={localize(
              language,
              "Key (e.g. input, output)",
              "键（例如：input、output）",
            )}
            value={key}
            disabled={!isDefault}
            onChange={(e) => {
              const newKey = e.target.value;

              // Prevent overwriting existing keys (unless it's the same key)
              if (newKey !== key && prices[newKey] !== undefined) {
                return; // Don't allow the change
              }

              const newPrices = { ...prices };
              const oldValue = newPrices[key];
              delete newPrices[key];
              newPrices[newKey] = oldValue;
              form.setValue(`pricingTiers.${tierIndex}.prices`, newPrices);
            }}
            className={!isDefault ? "cursor-not-allowed bg-muted" : ""}
          />
          <div className="flex gap-1">
            <Input
              type="number"
              placeholder={localize(language, "Price per unit", "每单位价格")}
              value={value as number}
              step="0.000001"
              onChange={(e) => {
                form.setValue(`pricingTiers.${tierIndex}.prices`, {
                  ...prices,
                  [key]: parseFloat(e.target.value),
                });
              }}
            />
            {isDefault && (
              <Button
                type="button"
                variant="outline"
                title={localize(language, "Remove price", "移除价格")}
                size="icon"
                onClick={() => {
                  const newPrices = { ...prices };
                  delete newPrices[key];
                  form.setValue(`pricingTiers.${tierIndex}.prices`, newPrices);
                }}
              >
                <MinusCircle className="h-4 w-4" />
              </Button>
            )}
          </div>
        </div>
      ))}
      {isDefault && (
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            // Generate unique key name
            let counter = 1;
            let newKey = "new_usage_type";
            while (prices[newKey] !== undefined) {
              newKey = `new_usage_type_${counter}`;
              counter++;
            }
            form.setValue(`pricingTiers.${tierIndex}.prices`, {
              ...prices,
              [newKey]: 0.000001,
            });
          }}
          className="flex items-center gap-1"
        >
          <PlusCircle className="h-4 w-4" />
          <span>{localize(language, "Add Price", "添加价格")}</span>
        </Button>
      )}
      <PricePreview prices={prices} />
    </div>
  );
}
