import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/src/components/ui/form";
import { RadioGroup, RadioGroupItem } from "@/src/components/ui/radio-group";
import { Textarea } from "@/src/components/ui/textarea";
import { Input } from "@/src/components/ui/input";
import { Label } from "@/src/components/ui/label";
import type { Control, Path } from "react-hook-form";
import type { SurveyQuestion, SurveyFormData } from "../lib/surveyTypes";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

const AUTO_ADVANCE_DELAY = 300;

interface SurveyStepProps {
  question: SurveyQuestion;
  control: Control<SurveyFormData>;
  onAutoAdvance?: (selectedValue?: string) => void;
  isLast?: boolean;
}

export function SurveyStep({
  question,
  control,
  onAutoAdvance,
  isLast = false,
}: SurveyStepProps) {
  const fieldName = question.id as keyof SurveyFormData;
  const { language } = useLanguage();

  const localizedQuestion =
    question.id === "role"
      ? localize(
          language,
          "What describes you best?",
          "以下哪项最符合你的身份？",
        )
      : question.id === "signupReason"
        ? localize(language, "Why are you signing up?", "你为什么注册？")
        : question.id === "referralSource"
          ? localize(
              language,
              "Where did you hear about us?",
              "你是从哪里了解到我们的？",
            )
          : question.question;

  const localizedPlaceholder =
    question.id === "referralSource"
      ? localize(
          language,
          "GitHub, X, Reddit, colleague etc.",
          "GitHub、X、Reddit、同事推荐等",
        )
      : "placeholder" in question
        ? question.placeholder
        : undefined;

  const getLocalizedOption = (option: string) => {
    switch (option) {
      case "Software Engineer":
        return localize(language, "Software Engineer", "软件工程师");
      case "ML Engineer / Data Scientist":
        return localize(
          language,
          "ML Engineer / Data Scientist",
          "机器学习工程师 / 数据科学家",
        );
      case "Product Manager":
        return localize(language, "Product Manager", "产品经理");
      case "Domain Expert":
        return localize(language, "Domain Expert", "领域专家");
      case "Executive or Manager":
        return localize(language, "Executive or Manager", "高管或经理");
      case "Other":
        return localize(language, "Other", "其他");
      case "Invited by team":
        return localize(language, "Invited by team", "受团队邀请");
      case "Just looking around":
        return localize(language, "Just looking around", "随便看看");
      case "Evaluating / Testing Langfuse":
        return localize(
          language,
          "Evaluating / Testing Langfuse",
          "评估 / 测试 Langfuse",
        );
      case "Start using Langfuse":
        return localize(language, "Start using Langfuse", "开始使用 Langfuse");
      case "Migrating from other solution":
        return localize(
          language,
          "Migrating from other solution",
          "从其他方案迁移",
        );
      case "Migrating from self-hosted":
        return localize(
          language,
          "Migrating from self-hosted",
          "从自托管版本迁移",
        );
      default:
        return option;
    }
  };

  const handleAutoAdvanceWithTimeout = (selectedValue?: string) => {
    if (onAutoAdvance) {
      // For signupReason question, ignore isLast and let the hook decide
      // For other questions, respect the isLast prop
      const shouldAutoAdvance = question.id === "signupReason" || !isLast;

      if (shouldAutoAdvance) {
        setTimeout(() => {
          onAutoAdvance(selectedValue);
        }, AUTO_ADVANCE_DELAY);
      }
    }
  };

  if (question.type === "radio") {
    return (
      <FormField
        key={fieldName}
        control={control}
        name={fieldName as Path<SurveyFormData>}
        render={({ field }) => (
          <FormItem className="flex flex-col gap-2">
            <FormLabel className="text-xl font-semibold">
              {localizedQuestion}
            </FormLabel>
            <FormControl>
              <RadioGroup
                name={field.name}
                ref={field.ref}
                onValueChange={(value) => {
                  field.onChange(value);
                  setTimeout(() => {
                    handleAutoAdvanceWithTimeout(value);
                  }, 0);
                }}
                value={field.value as string}
                className="grid gap-3"
              >
                {question.options.map((option) => (
                  <Label
                    key={option}
                    htmlFor={option}
                    className="flex flex-1 cursor-pointer items-center gap-3 rounded-lg border border-border p-3 text-sm font-medium leading-none transition-colors hover:bg-muted/50 peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
                  >
                    <RadioGroupItem value={option} id={option} />
                    <span className="flex-1">{getLocalizedOption(option)}</span>
                  </Label>
                ))}
              </RadioGroup>
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    );
  }

  if (question.type === "text") {
    return (
      <FormField
        key={fieldName}
        control={control}
        name={fieldName as Path<SurveyFormData>}
        render={({ field }) => (
          <FormItem className="flex flex-col gap-2">
            <FormLabel className="text-xl font-semibold">
              {localizedQuestion}
            </FormLabel>
            <FormControl>
              {question.id === "referralSource" ? (
                <Input placeholder={localizedPlaceholder} {...field} />
              ) : (
                <Textarea
                  placeholder={localizedPlaceholder}
                  className="min-h-[170px] resize-none"
                  {...field}
                />
              )}
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    );
  }

  return null;
}
