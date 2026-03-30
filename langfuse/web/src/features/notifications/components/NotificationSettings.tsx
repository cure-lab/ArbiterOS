import { useState } from "react";
import { useRouter } from "next/router";
import { api } from "@/src/utils/api";
import Header from "@/src/components/layouts/header";
import { Card, CardContent } from "@/src/components/ui/card";
import { Label } from "@/src/components/ui/label";
import { Switch } from "@/src/components/ui/switch";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export function NotificationSettings() {
  const router = useRouter();
  const projectId = router.query.projectId as string;
  const [isSaving, setIsSaving] = useState(false);
  const { language } = useLanguage();

  const hasAccess = useHasProjectAccess({
    projectId,
    scope: "project:read",
  });

  const {
    data: preferences,
    isLoading,
    refetch,
  } = api.notificationPreferences.getForProject.useQuery(
    { projectId },
    { enabled: Boolean(projectId) },
  );

  const updatePreference = api.notificationPreferences.update.useMutation({
    onSuccess: () => {
      refetch();
    },
  });

  const handleToggle = async (enabled: boolean) => {
    setIsSaving(true);
    await updatePreference.mutateAsync({
      projectId,
      channel: "EMAIL",
      type: "COMMENT_MENTION",
      enabled,
    });
    setIsSaving(false);
  };

  if (isLoading || !preferences) {
    return (
      <div>
        <Header
          title={localize(language, "Notification Settings", "通知设置")}
        />
        <Card className="mt-4">
          <CardContent className="p-6">
            <p className="text-sm text-muted-foreground">
              {localize(
                language,
                "Loading preferences...",
                "正在加载偏好设置...",
              )}
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const emailCommentMention = preferences.find(
    (p) => p.channel === "EMAIL" && p.type === "COMMENT_MENTION",
  );

  return (
    <div>
      <Header title={localize(language, "Notification Settings", "通知设置")} />
      <Card className="mt-4">
        <CardContent className="space-y-6 p-6">
          <div>
            <h3 className="text-lg font-medium">
              {localize(language, "Email Notifications", "邮件通知")}
            </h3>
            <p className="text-sm text-muted-foreground">
              {localize(
                language,
                "Manage your email notification preferences for this project.",
                "管理此项目的邮件通知偏好。",
              )}
            </p>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <Label htmlFor="comment-mention" className="text-base">
                  {localize(language, "Comment Mentions", "评论提及")}
                </Label>
                <p className="text-sm text-muted-foreground">
                  {localize(
                    language,
                    "Receive an email when someone mentions you in a comment",
                    "当有人在评论中提及你时接收邮件通知",
                  )}
                </p>
              </div>
              <Switch
                id="comment-mention"
                checked={emailCommentMention?.enabled ?? true}
                onCheckedChange={handleToggle}
                disabled={isSaving || !hasAccess}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      {updatePreference.isError && (
        <div className="mt-4 rounded-lg border border-destructive bg-destructive/10 p-4">
          <p className="text-sm text-destructive">
            {localize(
              language,
              "Failed to update notification preference. Please try again.",
              "更新通知偏好失败，请重试。",
            )}
          </p>
        </div>
      )}
    </div>
  );
}
