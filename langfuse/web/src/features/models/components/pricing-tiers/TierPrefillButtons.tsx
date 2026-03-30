import { Button } from "@/src/components/ui/button";
import { FormDescription } from "@/src/components/ui/form";
import type { UseFormReturn } from "react-hook-form";
import type { FormUpsertModel } from "../../validation";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

type TierPrefillButtonsProps = {
  tierIndex: number;
  form: UseFormReturn<FormUpsertModel>;
};

export type { TierPrefillButtonsProps };

export function TierPrefillButtons({
  tierIndex,
  form,
}: TierPrefillButtonsProps) {
  const prices = form.watch(`pricingTiers.${tierIndex}.prices`) || {};
  const { language } = useLanguage();

  return (
    <div className="space-y-2">
      <FormDescription>
        {localize(
          language,
          "Prefill usage types from template:",
          "从模板预填充用量类型：",
        )}
      </FormDescription>
      <div className="flex gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            form.setValue(`pricingTiers.${tierIndex}.prices`, {
              input: 0,
              output: 0,
              input_cached_tokens: 0,
              output_reasoning_tokens: 0,
              ...prices,
            });
          }}
        >
          OpenAI
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            form.setValue(`pricingTiers.${tierIndex}.prices`, {
              input: 0,
              input_tokens: 0,
              output: 0,
              output_tokens: 0,
              cache_creation_input_tokens: 0,
              cache_read_input_tokens: 0,
              ...prices,
            });
          }}
        >
          Anthropic
        </Button>
      </div>
    </div>
  );
}
