import { Button } from "@/src/components/ui/button";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { UpsertModelFormDialog } from "@/src/features/models/components/UpsertModelFormDialog";
import { type GetModelResult } from "@/src/features/models/validation";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export const EditModelButton = ({
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
    <UpsertModelFormDialog {...{ modelData, projectId, action: "edit" }}>
      <Button
        variant="outline"
        disabled={!hasAccess}
        title={localize(language, "Edit model", "编辑模型")}
        className="flex items-center"
      >
        <span>{localize(language, "Edit", "编辑")}</span>
      </Button>
    </UpsertModelFormDialog>
  );
};
