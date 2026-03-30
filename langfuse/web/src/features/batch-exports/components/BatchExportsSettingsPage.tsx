import Header from "@/src/components/layouts/header";
import { Alert, AlertDescription, AlertTitle } from "@/src/components/ui/alert";
import { BatchExportsTable } from "@/src/features/batch-exports/components/BatchExportsTable";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { SettingsTableCard } from "@/src/components/layouts/settings-table-card";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export function BatchExportsSettingsPage(props: { projectId: string }) {
  const { language } = useLanguage();
  const hasAccess = useHasProjectAccess({
    projectId: props.projectId,
    scope: "batchExports:read",
  });

  return (
    <>
      <Header title={localize(language, "Exports", "导出")} />
      <p className="mb-4 text-sm">
        {localize(
          language,
          "Export large datasets in your preferred format via the export buttons across Langfuse. Exports are processed asynchronously and remain available for download for one hour. You will receive an email notification once your export is ready.",
          "通过 Langfuse 各处的导出按钮，以你偏好的格式导出大型数据集。导出会异步处理，并在一小时内可供下载。导出完成后，你将收到邮件通知。",
        )}
      </p>
      {hasAccess ? (
        <SettingsTableCard>
          <BatchExportsTable projectId={props.projectId} />
        </SettingsTableCard>
      ) : (
        <Alert>
          <AlertTitle>
            {localize(language, "Access Denied", "访问被拒绝")}
          </AlertTitle>
          <AlertDescription>
            {localize(
              language,
              "You do not have permission to view batch exports.",
              "你没有权限查看批量导出。",
            )}
          </AlertDescription>
        </Alert>
      )}
    </>
  );
}
