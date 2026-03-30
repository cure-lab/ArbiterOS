import { Button } from "@/src/components/ui/button";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { UpsertModelFormDialog } from "@/src/features/models/components/UpsertModelFormDialog";
import { type GetModelResult } from "@/src/features/models/validation";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export const CloneModelButton = ({
  modelData,
  projectId,
}: {
  modelData: GetModelResult;
  projectId: string;
}) => {
  const hasAccess = useHasProjectAccess({
    projectId,
    scope: "models:CUD",
  });
  const { language } = useLanguage();

  return (
    <UpsertModelFormDialog {...{ modelData, projectId, action: "clone" }}>
      <Button
        variant="outline"
        disabled={!hasAccess}
        title={localize(language, "Clone model", "克隆模型")}
        className="flex items-center"
      >
        <span>{localize(language, "Clone", "克隆")}</span>
      </Button>
    </UpsertModelFormDialog>
  );
};
