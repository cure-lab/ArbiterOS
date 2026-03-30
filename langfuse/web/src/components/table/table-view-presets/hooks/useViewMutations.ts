import { showSuccessToast } from "@/src/features/notifications/showSuccessToast";
import { api } from "@/src/utils/api";
import { copyTextToClipboard } from "@/src/utils/clipboard";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

type UseViewMutationsProps = {
  handleSetViewId: (viewId: string | null) => void;
};

export const useViewMutations = ({
  handleSetViewId,
}: UseViewMutationsProps) => {
  const { language } = useLanguage();
  const utils = api.useUtils();

  const createMutation = api.TableViewPresets.create.useMutation({
    onSuccess: (data) => {
      utils.TableViewPresets.getByTableName.invalidate();
      handleSetViewId(data.view.id);
    },
  });

  const updateConfigMutation = api.TableViewPresets.update.useMutation({
    onSuccess: (data) => {
      utils.TableViewPresets.getById.invalidate({
        viewId: data.view.id,
      });
      utils.TableViewPresets.getByTableName.invalidate();
      showSuccessToast({
        title: localize(language, "View updated", "视图已更新"),
        description: localize(
          language,
          `${data.view.name} has been updated to reflect your current table state`,
          `${data.view.name} 已更新为当前表格状态`,
        ),
      });
    },
  });

  const updateNameMutation = api.TableViewPresets.updateName.useMutation({
    onSuccess: () => {
      utils.TableViewPresets.getByTableName.invalidate();
    },
  });

  const deleteMutation = api.TableViewPresets.delete.useMutation({
    onSuccess: () => {
      utils.TableViewPresets.getByTableName.invalidate();
      handleSetViewId(null);
    },
  });

  const generatePermalinkMutation =
    api.TableViewPresets.generatePermalink.useMutation({
      onSuccess: (data) => {
        copyTextToClipboard(data);
        showSuccessToast({
          title: localize(
            language,
            "Permalink copied to clipboard",
            "永久链接已复制到剪贴板",
          ),
          description: localize(
            language,
            "You can now share the permalink with others",
            "现在可以将该永久链接分享给其他人",
          ),
        });
      },
    });

  return {
    createMutation,
    updateConfigMutation,
    updateNameMutation,
    deleteMutation,
    generatePermalinkMutation,
  };
};
