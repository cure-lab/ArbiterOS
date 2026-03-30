import ContainerPage from "@/src/components/layouts/container-page";
import { StatusBadge } from "@/src/components/layouts/status-badge";
import { AutomationButton } from "@/src/features/automations/components/AutomationButton";
import { SlackConnectionCard } from "@/src/features/slack/components/SlackConnectionCard";
import {
  ChannelSelector,
  type SlackChannel,
} from "@/src/features/slack/components/ChannelSelector";
import { SlackTestMessageButton } from "@/src/features/slack/components/SlackTestMessageButton";
import { api } from "@/src/utils/api";
import { useRouter } from "next/router";
import { useState, useEffect } from "react";
import { Badge } from "@/src/components/ui/badge";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/src/components/ui/card";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export default function SlackIntegrationSettings() {
  const router = useRouter();
  const projectId = router.query.projectId as string;
  const { language } = useLanguage();

  // Handle popup OAuth completion
  useEffect(() => {
    // Check if this page is opened in a popup window
    const isPopup = window.opener && window.opener !== window;

    if (isPopup) {
      // Check for OAuth completion parameters
      const urlParams = new URLSearchParams(window.location.search);
      const success = urlParams.get("success");
      const error = urlParams.get("error");
      const teamName = urlParams.get("team_name");

      if (success === "true") {
        // Send success message to parent window
        window.opener.postMessage(
          {
            type: "slack-oauth-success",
            teamName: teamName || "your Slack workspace",
          },
          window.location.origin,
        );

        // Close popup
        window.close();
      } else if (error) {
        // Send error message to parent window
        window.opener.postMessage(
          {
            type: "slack-oauth-error",
            error: error,
          },
          window.location.origin,
        );

        // Close popup
        window.close();
      }
    }
  }, [router.query]);

  const { data: integrationStatus, isInitialLoading } =
    api.slack.getIntegrationStatus.useQuery(
      { projectId },
      { enabled: !!projectId },
    );

  const status = isInitialLoading
    ? undefined
    : integrationStatus?.isConnected
      ? "active"
      : "inactive";

  const [selectedChannel, setSelectedChannel] = useState<SlackChannel | null>(
    null,
  );

  // Check user permissions
  const hasAccess = useHasProjectAccess({
    projectId,
    scope: "automations:CUD",
  });

  return (
    <ContainerPage
      headerProps={{
        title: localize(language, "Slack Integration", "Slack 集成"),
        breadcrumb: [
          {
            name: localize(language, "Settings", "设置"),
            href: `/project/${projectId}/settings`,
          },
        ],
        actionButtonsLeft: <>{status && <StatusBadge type={status} />}</>,
        actionButtonsRight: <AutomationButton projectId={projectId} />,
      }}
    >
      <div className="space-y-6">
        {/* Connection Configuration */}
        <SlackConnectionCard projectId={projectId} showConnectButton={true} />

        {/* Test Channel Section - Only show when connected */}
        {integrationStatus?.isConnected && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                {localize(language, "Test Integration", "测试集成")}
              </CardTitle>
              <CardDescription>
                {localize(
                  language,
                  "Test your Slack integration by sending a message to a channel.",
                  "向某个频道发送一条消息，以测试你的 Slack 集成。",
                )}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <h4 className="mb-2 text-sm font-medium">
                  {localize(language, "Select Test Channel", "选择测试频道")}
                </h4>
                <div className="max-w-md">
                  <ChannelSelector
                    projectId={projectId}
                    selectedChannelId={selectedChannel?.id}
                    onChannelSelect={setSelectedChannel}
                    placeholder={localize(
                      language,
                      "Choose a channel to test",
                      "选择要测试的频道",
                    )}
                    showRefreshButton={true}
                  />
                </div>
              </div>

              {selectedChannel && (
                <div className="space-y-4 border-t pt-4">
                  <div>
                    <h4 className="mb-3 text-sm font-medium">
                      {localize(language, "Channel Information", "频道信息")}
                    </h4>
                    <div className="grid gap-4 md:grid-cols-2">
                      <div>
                        <p className="text-sm font-medium">
                          {localize(language, "Channel Name", "频道名称")}
                        </p>
                        <p className="text-sm text-muted-foreground">
                          #{selectedChannel.name}
                        </p>
                      </div>
                      <div>
                        <p className="text-sm font-medium">
                          {localize(language, "Channel Type", "频道类型")}
                        </p>
                        <Badge variant="outline" className="text-xs">
                          {selectedChannel.isPrivate
                            ? localize(language, "Private", "私有")
                            : localize(language, "Public", "公开")}
                        </Badge>
                      </div>
                      <div>
                        <p className="text-sm font-medium">
                          {localize(language, "Channel ID", "频道 ID")}
                        </p>
                        <p className="font-mono text-sm text-muted-foreground">
                          {selectedChannel.id}
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-3">
                    <SlackTestMessageButton
                      projectId={projectId}
                      selectedChannel={selectedChannel}
                      hasAccess={hasAccess}
                      disabled={false}
                    />
                  </div>
                </div>
              )}

              {!selectedChannel && (
                <div className="text-sm text-muted-foreground">
                  {localize(
                    language,
                    "Select a channel above to view its details and test message delivery.",
                    "在上方选择一个频道以查看其详情并测试消息发送。",
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </ContainerPage>
  );
}
