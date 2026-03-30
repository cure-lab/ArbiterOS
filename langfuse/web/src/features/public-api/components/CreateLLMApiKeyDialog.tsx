import { PlusIcon } from "lucide-react";
import { Button } from "@/src/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/src/components/ui/dialog";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import useProjectIdFromURL from "@/src/hooks/useProjectIdFromURL";
import { useUiCustomization } from "@/src/ee/features/ui-customization/useUiCustomization";
import { CreateLLMApiKeyForm } from "@/src/features/public-api/components/CreateLLMApiKeyForm";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export function CreateLLMApiKeyDialog({
  open,
  setOpen,
}: {
  open: boolean;
  setOpen: (open: boolean) => void;
}) {
  const projectId = useProjectIdFromURL();
  const hasAccess = useHasProjectAccess({
    projectId,
    scope: "llmApiKeys:create",
  });
  const uiCustomization = useUiCustomization();
  const { language } = useLanguage();

  if (!hasAccess) return null;

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        setOpen(isOpen);
      }}
    >
      <DialogTrigger asChild>
        <Button variant="secondary">
          <PlusIcon className="-ml-0.5 mr-1.5 h-5 w-5" aria-hidden="true" />
          {localize(language, "Add LLM Connection", "添加 LLM 连接")}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90%] min-w-[40vw] overflow-auto">
        <DialogHeader>
          <DialogTitle>
            {localize(language, "New LLM Connection", "新建 LLM 连接")}
          </DialogTitle>
        </DialogHeader>
        {open && (
          <CreateLLMApiKeyForm
            projectId={projectId}
            onSuccess={() => setOpen(false)}
            customization={uiCustomization}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
