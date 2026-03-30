import { PlusCircle, Trash2 } from "lucide-react";
import { useFieldArray } from "react-hook-form";
import { Button } from "@/src/components/ui/button";
import { Input } from "@/src/components/ui/input";
import { Checkbox } from "@/src/components/ui/checkbox";
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/src/components/ui/form";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/src/components/ui/select";
import type { UseFormReturn } from "react-hook-form";
import type { FormUpsertModel } from "../../validation";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

type TierConditionsEditorProps = {
  tierIndex: number;
  form: UseFormReturn<FormUpsertModel>;
};

export type { TierConditionsEditorProps };

export function TierConditionsEditor({
  tierIndex,
  form,
}: TierConditionsEditorProps) {
  const { fields, append, remove } = useFieldArray({
    control: form.control,
    name: `pricingTiers.${tierIndex}.conditions`,
  });
  const { language } = useLanguage();

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <FormLabel>{localize(language, "Conditions", "条件")}</FormLabel>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() =>
            append({
              usageDetailPattern: "",
              operator: "gt",
              value: 0,
              caseSensitive: false,
            })
          }
        >
          <PlusCircle className="mr-1 h-4 w-4" />
          {localize(language, "Add Condition", "添加条件")}
        </Button>
      </div>

      {fields.length === 0 && (
        <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          <strong>{localize(language, "Warning:", "警告：")}</strong>{" "}
          {localize(
            language,
            "Non-default tiers require at least one condition. This tier will fail validation.",
            "非默认层级至少需要一个条件，否则该层级将无法通过校验。",
          )}
        </div>
      )}

      {fields.map((condition, conditionIndex) => (
        <div key={condition.id} className="space-y-3 rounded-lg border p-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">
              {localize(
                language,
                `Condition ${conditionIndex + 1}`,
                `条件 ${conditionIndex + 1}`,
              )}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => remove(conditionIndex)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>

          {/* Pattern */}
          <FormField
            control={form.control}
            name={`pricingTiers.${tierIndex}.conditions.${conditionIndex}.usageDetailPattern`}
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  {localize(
                    language,
                    "Usage Detail Pattern (Regex)",
                    "用量详情模式（正则）",
                  )}
                </FormLabel>
                <FormControl>
                  <Input {...field} placeholder="^input" />
                </FormControl>
                <FormDescription>
                  {localize(
                    language,
                    "Match usage type keys (e.g., ^input, .*cache.*, output_tokens)",
                    "匹配用量类型键（例如：^input、.*cache.*、output_tokens）",
                  )}
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />

          {/* Operator + Value */}
          <div className="grid grid-cols-2 gap-2">
            <FormField
              control={form.control}
              name={`pricingTiers.${tierIndex}.conditions.${conditionIndex}.operator`}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {localize(language, "Operator", "操作符")}
                  </FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="gt">
                        {localize(language, "> (greater than)", ">（大于）")}
                      </SelectItem>
                      <SelectItem value="gte">
                        {localize(
                          language,
                          ">= (greater or equal)",
                          ">=（大于等于）",
                        )}
                      </SelectItem>
                      <SelectItem value="lt">
                        {localize(language, "< (less than)", "<（小于）")}
                      </SelectItem>
                      <SelectItem value="lte">
                        {localize(
                          language,
                          "<= (less or equal)",
                          "<=（小于等于）",
                        )}
                      </SelectItem>
                      <SelectItem value="eq">
                        {localize(language, "= (equals)", "=（等于）")}
                      </SelectItem>
                      <SelectItem value="neq">
                        {localize(language, "!= (not equals)", "!=（不等于）")}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name={`pricingTiers.${tierIndex}.conditions.${conditionIndex}.value`}
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{localize(language, "Value", "数值")}</FormLabel>
                  <FormControl>
                    <Input
                      type="number"
                      {...field}
                      onChange={(e) =>
                        field.onChange(parseFloat(e.target.value))
                      }
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>

          {/* Case Sensitive */}
          <FormField
            control={form.control}
            name={`pricingTiers.${tierIndex}.conditions.${conditionIndex}.caseSensitive`}
            render={({ field }) => (
              <FormItem className="flex items-center gap-2">
                <FormControl>
                  <Checkbox
                    checked={field.value}
                    onCheckedChange={field.onChange}
                  />
                </FormControl>
                <FormLabel className="!mt-0">
                  {localize(language, "Case sensitive", "区分大小写")}
                </FormLabel>
              </FormItem>
            )}
          />
        </div>
      ))}
    </div>
  );
}
