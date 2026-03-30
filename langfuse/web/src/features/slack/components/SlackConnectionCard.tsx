import React from "react";
import { CheckCircle, AlertCircle } from "lucide-react";
import { Badge } from "@/src/components/ui/badge";
import { Button } from "@/src/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/src/components/ui/card";
import { Alert, AlertDescription } from "@/src/components/ui/alert";
import { api } from "@/src/utils/api";
import { SlackConnectButton } from "@/src/features/slack/components/SlackConnectButton";
import { SlackDisconnectButton } from "@/src/features/slack/components/SlackDisconnectButton";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

/**
 * Props for the SlackConnectionCard component
 */
interface SlackConnectionCardProps {
  /** Project ID for the Slack integration */
  projectId: string;
  /** Whether the component is disabled */
  disabled?: boolean;
  /** Optional callback when connection status changes */
  onConnectionChange?: (connected: boolean) => void;
  /** Whether to show the connect button in the card */
  showConnectButton?: boolean;
}

/**
 * A reusable card component that displays Slack connection status and management controls.
 *
 * This component handles:
 * - Displaying current connection status
 * - Showing team information when connected
 * - Providing connection and disconnection actions
 * - Handling error states with appropriate messaging
 *
 * The component automatically fetches the integration status and updates when the connection changes.
 *
 * @param projectId - The project ID for the Slack integration
 * @param disabled - Whether the component should be disabled
 * @param onConnectionChange - Optional callback when connection status changes
 * @param showConnectButton - Whether to show the connect button in the card (default: true)
 */
export const SlackConnectionCard: React.FC<SlackConnectionCardProps> = ({
  projectId,
  disabled = false,
  onConnectionChange,
  showConnectButton = true,
}) => {
  const { language } = useLanguage();
  // Get Slack integration status
  const {
    data: integrationStatus,
    isLoading,
    refetch: refetchStatus,
    error: statusError,
  } = api.slack.getIntegrationStatus.useQuery(
    { projectId },
    {
      enabled: !!projectId,
      // Refetch every 30 seconds to keep status up to date
      refetchInterval: 30000,
    },
  );

  // Handle connection status change
  const handleConnectionChange = (connected: boolean) => {
    refetchStatus();
    onConnectionChange?.(connected);
  };

  // Handle loading state
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {localize(language, "Slack Connection", "Slack 连接")}
          </CardTitle>
          <CardDescription>
            {localize(
              language,
              "Checking connection status...",
              "正在检查连接状态...",
            )}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-muted-foreground">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
            <span>{localize(language, "Loading...", "加载中...")}</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  // Handle error state
  if (statusError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {localize(language, "Slack Connection", "Slack 连接")}
          </CardTitle>
          <CardDescription>
            {localize(
              language,
              "Error loading connection status",
              "加载连接状态时出错",
            )}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              {localize(
                language,
                "Failed to load Slack integration status. Please try again.",
                "加载 Slack 集成状态失败，请重试。",
              )}
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    );
  }

  // Handle not connected state
  if (!integrationStatus?.isConnected) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {localize(language, "Slack Connection", "Slack 连接")}
          </CardTitle>
          <CardDescription>
            {localize(
              language,
              "Connect your Slack workspace to send notifications",
              "连接你的 Slack 工作区以发送通知",
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {integrationStatus?.error && (
            <Alert>
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{integrationStatus.error}</AlertDescription>
            </Alert>
          )}

          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              {localize(
                language,
                "Connect your Slack workspace to enable real-time notifications for your automations.",
                "连接你的 Slack 工作区，以为自动化启用实时通知。",
              )}
            </p>

            {showConnectButton && (
              <SlackConnectButton
                projectId={projectId}
                disabled={disabled}
                onSuccess={() => handleConnectionChange(true)}
                onError={(error: Error) => {
                  console.error("Slack connection error:", error);
                }}
              />
            )}
          </div>
        </CardContent>
      </Card>
    );
  }

  // Handle connected state
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {localize(language, "Slack Connection", "Slack 连接")}
        </CardTitle>
        <CardDescription>
          {localize(
            language,
            "Connected to your Slack workspace",
            "已连接到你的 Slack 工作区",
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Connection Status */}
        <div className="flex items-center gap-2">
          <CheckCircle className="h-4 w-4 text-green-500" />
          <span className="text-sm font-medium">
            {localize(language, "Connected", "已连接")}
          </span>
        </div>

        {/* Team Information */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">
              {localize(language, "Workspace:", "工作区：")}
            </span>
            <Badge variant="secondary" className="text-xs">
              {integrationStatus.teamName}
            </Badge>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">
              {localize(language, "Team ID:", "团队 ID：")}
            </span>
            <Badge variant="outline" className="font-mono text-xs">
              {integrationStatus.teamId}
            </Badge>
          </div>

          {integrationStatus.botUserId && (
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium">
                {localize(language, "Bot User:", "机器人用户：")}
              </span>
              <Badge variant="outline" className="font-mono text-xs">
                {integrationStatus.botUserId}
              </Badge>
            </div>
          )}
        </div>

        {/* Management Actions */}
        <div className="flex gap-2 pt-2">
          <SlackDisconnectButton
            projectId={projectId}
            disabled={disabled}
            onSuccess={() => handleConnectionChange(false)}
            onError={(error: Error) => {
              console.error("Slack disconnection error:", error);
            }}
          />

          <Button
            variant="outline"
            size="sm"
            onClick={() => refetchStatus()}
            disabled={disabled}
          >
            {localize(language, "Refresh Status", "刷新状态")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
};
