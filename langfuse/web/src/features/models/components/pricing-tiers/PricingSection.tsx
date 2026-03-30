import { PlusCircle } from "lucide-react";
import { Button } from "@/src/components/ui/button";
import { FormDescription, FormLabel } from "@/src/components/ui/form";
import { Accordion } from "@/src/components/ui/accordion";
import { TierAccordionItem } from "./TierAccordionItem";
import { TierPriceEditor } from "./TierPriceEditor";
import { TierPrefillButtons } from "./TierPrefillButtons";
import type { UseFormReturn, UseFieldArrayReturn } from "react-hook-form";
import type { FormUpsertModel } from "../../validation";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

type PricingSectionProps = {
  fields: UseFieldArrayReturn<FormUpsertModel, "pricingTiers">["fields"];
  form: UseFormReturn<FormUpsertModel>;
  remove: UseFieldArrayReturn<FormUpsertModel, "pricingTiers">["remove"];
  addTier: () => void;
};

export type { PricingSectionProps };

export function PricingSection({
  fields,
  form,
  remove,
  addTier,
}: PricingSectionProps) {
  const hasMultipleTiers = fields.length > 1;
  const defaultTierIndex = fields.findIndex((f) => f.isDefault);
  const { language } = useLanguage();

  if (!hasMultipleTiers) {
    // SIMPLE VIEW: Just show prices for the single default tier
    return (
      <div className="space-y-4">
        <div>
          <FormLabel>{localize(language, "Prices", "价格")}</FormLabel>
          <FormDescription>
            {localize(
              language,
              "Set prices per usage type for this model. Usage types must exactly match the keys of the ingested usage details.",
              "为该模型设置各用量类型的价格。用量类型必须与摄取的 usage details 中的键完全一致。",
            )}
          </FormDescription>
        </div>

        <TierPrefillButtons tierIndex={defaultTierIndex} form={form} />
        <TierPriceEditor
          tierIndex={defaultTierIndex}
          form={form}
          isDefault={true}
        />

        <Button type="button" variant="ghost" onClick={addTier}>
          <PlusCircle className="mr-2 h-4 w-4" />
          {localize(language, "Add Custom Pricing Tier", "添加自定义定价层级")}
        </Button>
      </div>
    );
  }

  // ACCORDION VIEW: Multiple tiers
  return (
    <div className="space-y-4">
      <div>
        <FormLabel>{localize(language, "Pricing Tiers", "定价层级")}</FormLabel>
        <FormDescription>
          {localize(
            language,
            "Define pricing rules evaluated in priority order. Tiers are checked from top to bottom until conditions match.",
            "定义按优先级顺序评估的定价规则。系统会从上到下检查各层级，直到条件匹配为止。",
          )}
        </FormDescription>
      </div>

      <Accordion
        type="multiple"
        defaultValue={fields.map((_, i) => `tier-${i}`)} // All expanded
        className="space-y-2"
      >
        {fields.map((field, index) => (
          <TierAccordionItem
            key={field.id}
            tier={field}
            index={index}
            form={form}
            remove={remove}
            isDefault={field.isDefault}
          />
        ))}
      </Accordion>

      <Button type="button" variant="outline" onClick={addTier}>
        <PlusCircle className="mr-2 h-4 w-4" />
        {localize(language, "Add Custom Tier", "添加自定义层级")}
      </Button>
    </div>
  );
}
