import { useState } from "react";
import { ActionButton } from "@/src/components/ActionButton";
import { TestModelMatchDialog } from "./TestModelMatchDialog";
import { FlaskConical } from "lucide-react";
import { type ButtonProps } from "@/src/components/ui/button";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

type TestModelMatchButtonProps = {
  projectId: string;
  variant?: ButtonProps["variant"];
};

export type { TestModelMatchButtonProps };

export function TestModelMatchButton({
  projectId,
  variant,
}: TestModelMatchButtonProps) {
  const [open, setOpen] = useState(false);
  const { language } = useLanguage();

  return (
    <>
      <ActionButton
        variant={variant ?? "secondary"}
        icon={<FlaskConical className="h-4 w-4" />}
        onClick={() => setOpen(true)}
        data-testid="test-model-match-button"
      >
        {localize(language, "Test Model Match", "测试模型匹配")}
      </ActionButton>

      <TestModelMatchDialog
        projectId={projectId}
        open={open}
        onOpenChange={setOpen}
      />
    </>
  );
}
