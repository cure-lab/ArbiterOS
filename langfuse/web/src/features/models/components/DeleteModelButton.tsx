import { useState } from "react";

import { Button } from "@/src/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/src/components/ui/popover";
import { type GetModelResult } from "@/src/features/models/validation";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { api } from "@/src/utils/api";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export const DeleteModelButton = ({
  modelData,
  projectId,
  onSuccess,
}: {
  modelData: GetModelResult;
  projectId: string;
  onSuccess?: () => void;
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const utils = api.useUtils();
  const capture = usePostHogClientCapture();
  const { language } = useLanguage();
  const mut = api.models.delete.useMutation({
    onSuccess: () => {
      void utils.models.invalidate();
      onSuccess?.();
    },
  });

  const hasAccess = useHasProjectAccess({
    projectId,
    scope: "models:CUD",
  });

  return (
    <Popover open={isOpen} onOpenChange={() => setIsOpen(!isOpen)}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          title={localize(language, "Delete model", "删除模型")}
          disabled={!hasAccess}
          className="flex items-center border-light-red"
        >
          <span className="text-dark-red">
            {localize(language, "Delete", "删除")}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent>
        <h2 className="text-md mb-3 font-semibold">
          {localize(language, "Please confirm", "请确认")}
        </h2>
        <p className="mb-3 text-sm">
          {localize(
            language,
            "This action permanently deletes this model definition.",
            "此操作会永久删除该模型定义。",
          )}
        </p>
        <div className="flex justify-end space-x-4">
          <Button
            type="button"
            variant="destructive"
            loading={mut.isPending}
            onClick={() => {
              capture("models:delete_button_click");
              mut.mutateAsync({
                projectId,
                modelId: modelData.id,
              });

              setIsOpen(false);
            }}
          >
            {localize(language, "Delete Model", "删除模型")}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
};
