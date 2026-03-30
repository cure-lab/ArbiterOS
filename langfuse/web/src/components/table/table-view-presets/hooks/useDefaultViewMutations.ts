import { api } from "@/src/utils/api";
import { showSuccessToast } from "@/src/features/notifications/showSuccessToast";
import { showErrorToast } from "@/src/features/notifications/showErrorToast";
import { type DefaultViewScope } from "@langfuse/shared/src/server";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

interface UseDefaultViewMutationsProps {
  tableName: string;
  projectId: string;
}

export function useDefaultViewMutations({
  tableName,
  projectId,
}: UseDefaultViewMutationsProps) {
  const { language } = useLanguage();
  const utils = api.useUtils();

  const setAsDefault = api.TableViewPresets.setAsDefault.useMutation({
    onSuccess: (_, variables) => {
      utils.TableViewPresets.getDefault.invalidate({
        projectId,
        viewName: tableName,
      });
      const scopeLabel = variables.scope === "user" ? "your" : "project";
      showSuccessToast({
        title: localize(language, "Default view set", "默认视图已设置"),
        description: localize(
          language,
          `Set as ${scopeLabel} default`,
          variables.scope === "user"
            ? "已设为你的默认视图"
            : "已设为项目默认视图",
        ),
      });
    },
    onError: (error) => {
      showErrorToast(
        localize(language, "Failed to set default", "设置默认值失败"),
        error.message,
      );
    },
  });

  const clearDefault = api.TableViewPresets.clearDefault.useMutation({
    onSuccess: (_, variables) => {
      utils.TableViewPresets.getDefault.invalidate({
        projectId,
        viewName: tableName,
      });
      const scopeLabel = variables.scope === "user" ? "Your" : "Project";
      showSuccessToast({
        title: localize(language, "Default cleared", "默认值已清除"),
        description: localize(
          language,
          `${scopeLabel} default view cleared`,
          variables.scope === "user"
            ? "你的默认视图已清除"
            : "项目默认视图已清除",
        ),
      });
    },
    onError: (error) => {
      showErrorToast(
        localize(language, "Failed to clear default", "清除默认值失败"),
        error.message,
      );
    },
  });

  const setViewAsDefault = (viewId: string, scope: DefaultViewScope) => {
    setAsDefault.mutate({
      projectId,
      viewId,
      viewName: tableName,
      scope,
    });
  };

  const clearViewDefault = (scope: DefaultViewScope) => {
    clearDefault.mutate({
      projectId,
      viewName: tableName,
      scope,
    });
  };

  return {
    setViewAsDefault,
    clearViewDefault,
    isSettingDefault: setAsDefault.isPending,
    isClearingDefault: clearDefault.isPending,
  };
}
