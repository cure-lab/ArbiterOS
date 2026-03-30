import Header from "@/src/components/layouts/header";
import { Alert, AlertDescription, AlertTitle } from "@/src/components/ui/alert";
import { SettingsTableCard } from "@/src/components/layouts/settings-table-card";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { BatchActionsTable } from "./BatchActionsTable";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export function BatchActionsSettingsPage(props: { projectId: string }) {
  const { language } = useLanguage();
  const hasAccess = useHasProjectAccess({
    projectId: props.projectId,
    scope: "datasets:CUD",
  });

  return (
    <>
      <Header title={localize(language, "Batch Actions", "批量操作")} />
      <p className="mb-4 text-sm">
        {localize(
          language,
          "Track the status of bulk operations performed on tables, such as adding observations to datasets, deleting traces, and adding items to annotation queues. Actions are processed asynchronously in the background.",
          "跟踪在表格上执行的批量操作状态，例如将 observations 添加到数据集、删除 traces，以及将条目添加到标注队列。这些操作会在后台异步处理。",
        )}
      </p>
      {hasAccess ? (
        <SettingsTableCard>
          <BatchActionsTable projectId={props.projectId} />
        </SettingsTableCard>
      ) : (
        <Alert>
          <AlertTitle>
            {localize(language, "Access Denied", "访问被拒绝")}
          </AlertTitle>
          <AlertDescription>
            {localize(
              language,
              "You do not have permission to view batch actions.",
              "你没有权限查看批量操作。",
            )}
          </AlertDescription>
        </Alert>
      )}
    </>
  );
}
