import React, { useState } from "react";
import { Unlink, AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/src/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/src/components/ui/dialog";
import { showSuccessToast } from "@/src/features/notifications/showSuccessToast";
import { showErrorToast } from "@/src/features/notifications/showErrorToast";
import { api } from "@/src/utils/api";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

/**
 * Props for the SlackDisconnectButton component
 */
interface SlackDisconnectButtonProps {
  /** Project ID for the Slack integration */
  projectId: string;
  /** Whether the button is disabled */
  disabled?: boolean;
  /** Button variant */
  variant?:
    | "default"
    | "outline"
    | "secondary"
    | "destructive"
    | "ghost"
    | "link";
  /** Button size */
  size?: "default" | "sm" | "lg" | "icon";
  /** Custom button text */
  buttonText?: string;
  /** Callback when disconnection is successful */
  onSuccess?: () => void;
  /** Callback when disconnection fails */
  onError?: (error: Error) => void;
  /** Whether to show confirmation dialog */
  showConfirmation?: boolean;
  /** Whether to show the button text */
  showText?: boolean;
}

/**
 * A button component that handles disconnecting the Slack integration.
 *
 * This component handles:
 * - Showing a confirmation dialog before disconnecting
 * - Calling the disconnect API endpoint
 * - Providing loading states during the disconnection process
 * - Displaying appropriate success/error messages
 * - Calling success/error callbacks
 *
 * The component includes safety measures to prevent accidental disconnection:
 * - Confirmation dialog with clear warning about consequences
 * - Information about what happens when disconnecting
 * - Option to cancel the operation
 *
 * @param projectId - The project ID for the Slack integration
 * @param disabled - Whether the button should be disabled
 * @param variant - Button variant (default: "destructive")
 * @param size - Button size (default: "sm")
 * @param buttonText - Custom button text (default: "Disconnect")
 * @param onSuccess - Callback when disconnection is successful
 * @param onError - Callback when disconnection fails
 * @param showConfirmation - Whether to show confirmation dialog (default: true)
 * @param showText - Whether to show the button text (default: true)
 */
export const SlackDisconnectButton: React.FC<SlackDisconnectButtonProps> = ({
  projectId,
  disabled = false,
  variant = "destructive",
  size = "sm",
  buttonText = "Disconnect",
  onSuccess,
  onError,
  showConfirmation = true,
  showText = true,
}) => {
  const { language } = useLanguage();
  const [isDisconnecting, setIsDisconnecting] = useState(false);
  const [isDialogOpen, setIsDialogOpen] = useState(false);

  // Disconnect mutation
  const disconnectMutation = api.slack.disconnect.useMutation({
    onSuccess: () => {
      setIsDisconnecting(false);
      setIsDialogOpen(false);

      showSuccessToast({
        title: localize(language, "Slack Disconnected", "Slack 已断开连接"),
        description: localize(
          language,
          "Successfully disconnected from your Slack workspace.",
          "已成功断开与你的 Slack 工作区的连接。",
        ),
      });

      onSuccess?.();
    },
    onError: (error: any) => {
      setIsDisconnecting(false);

      const errorMessage =
        error.message ||
        localize(
          language,
          "Failed to disconnect from Slack",
          "断开 Slack 连接失败",
        );

      showErrorToast(
        localize(language, "Disconnection Failed", "断开连接失败"),
        errorMessage,
      );

      onError?.(new Error(errorMessage));
    },
  });

  // Handle disconnect action
  const handleDisconnect = async () => {
    if (isDisconnecting) return;

    setIsDisconnecting(true);

    try {
      await disconnectMutation.mutateAsync({ projectId });
    } catch (error) {
      // Error handling is done in the mutation callbacks
      console.error("Disconnect error:", error);
    }
  };

  // Handle button click
  const handleClick = () => {
    if (showConfirmation) {
      setIsDialogOpen(true);
    } else {
      handleDisconnect();
    }
  };

  const buttonContent = (
    <>
      {isDisconnecting ? (
        <Loader2
          className={
            showText ? "mr-2 h-4 w-4 animate-spin" : "h-4 w-4 animate-spin"
          }
        />
      ) : (
        <Unlink className={showText ? "mr-2 h-4 w-4" : "h-4 w-4"} />
      )}
      {showText &&
        (isDisconnecting
          ? localize(language, "Disconnecting...", "断开连接中...")
          : buttonText === "Disconnect"
            ? localize(language, "Disconnect", "断开连接")
            : buttonText)}
    </>
  );

  if (showConfirmation) {
    return (
      <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
        <DialogTrigger asChild>
          <Button
            variant={variant}
            size={size}
            onClick={handleClick}
            disabled={disabled || isDisconnecting}
          >
            {buttonContent}
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              {localize(
                language,
                "Disconnect Slack Integration",
                "断开 Slack 集成",
              )}
            </DialogTitle>
            <DialogDescription className="space-y-2">
              <p>
                {localize(
                  language,
                  "Are you sure you want to disconnect your Slack workspace from this project?",
                  "确定要将你的 Slack 工作区与此项目断开连接吗？",
                )}
              </p>
              <div className="space-y-2 rounded-md bg-muted p-3">
                <p className="text-sm font-medium">
                  {localize(language, "This will:", "这将会：")}
                </p>
                <ul className="ml-4 space-y-1 text-sm">
                  <li>
                    {localize(
                      language,
                      "• Remove the bot from your Slack workspace",
                      "• 从你的 Slack 工作区移除机器人",
                    )}
                  </li>
                  <li>
                    {localize(
                      language,
                      "• Disable all existing Slack automations",
                      "• 禁用所有现有 Slack 自动化",
                    )}
                  </li>
                  <li>
                    {localize(
                      language,
                      "• Stop all future Slack notifications",
                      "• 停止所有未来的 Slack 通知",
                    )}
                  </li>
                  <li>
                    {localize(
                      language,
                      "• Delete stored workspace credentials",
                      "• 删除已存储的工作区凭证",
                    )}
                  </li>
                </ul>
              </div>
              <p className="text-sm text-muted-foreground">
                {localize(
                  language,
                  "You can reconnect at any time, but you'll need to reconfigure your automations.",
                  "你可以随时重新连接，但需要重新配置你的自动化。",
                )}
              </p>
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setIsDialogOpen(false)}
              disabled={isDisconnecting}
            >
              {localize(language, "Cancel", "取消")}
            </Button>
            <Button
              variant="destructive"
              onClick={handleDisconnect}
              disabled={isDisconnecting}
            >
              {isDisconnecting ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {localize(language, "Disconnecting...", "断开连接中...")}
                </>
              ) : (
                <>
                  <Unlink className="mr-2 h-4 w-4" />
                  {localize(language, "Disconnect", "断开连接")}
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  return (
    <Button
      variant={variant}
      size={size}
      onClick={handleClick}
      disabled={disabled || isDisconnecting}
    >
      {buttonContent}
    </Button>
  );
};
