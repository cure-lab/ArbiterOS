import { Button } from "@/src/components/ui/button";
import { Switch } from "@/src/components/ui/switch";
import { api } from "@/src/utils/api";
import { useState } from "react";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/src/components/ui/dialog";
import Header from "@/src/components/layouts/header";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { useHasOrganizationAccess } from "@/src/features/rbac/utils/checkOrganizationAccess";
import {
  useLangfuseCloudRegion,
  useQueryOrganization,
} from "@/src/features/organizations/hooks";
import { Card } from "@/src/components/ui/card";
import { LockIcon, ExternalLink } from "lucide-react";
import { useSession } from "next-auth/react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod/v4";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

const aiFeaturesSchema = z.object({
  aiFeaturesEnabled: z.boolean(),
});

export default function AIFeatureSwitch() {
  const { update: updateSession } = useSession();
  const { isLangfuseCloud } = useLangfuseCloudRegion();
  const capture = usePostHogClientCapture();
  const organization = useQueryOrganization();
  const { language } = useLanguage();
  const [isAIFeatureSwitchEnabled, setIsAIFeatureSwitchEnabled] = useState(
    organization?.aiFeaturesEnabled ?? false,
  );
  const [confirmOpen, setConfirmOpen] = useState(false);
  const hasAccess = useHasOrganizationAccess({
    organizationId: organization?.id,
    scope: "organization:update",
  });

  const confirmForm = useForm<z.infer<typeof aiFeaturesSchema>>({
    resolver: zodResolver(aiFeaturesSchema),
    defaultValues: {
      aiFeaturesEnabled: isAIFeatureSwitchEnabled,
    },
  });

  const updateAIFeatures = api.organizations.update.useMutation({
    onSuccess: () => {
      void updateSession();
      setConfirmOpen(false);
    },
    onError: () => {
      setConfirmOpen(false);
    },
  });

  function handleSwitchChange(newValue: boolean) {
    if (!hasAccess) return;
    setIsAIFeatureSwitchEnabled(newValue);
    confirmForm.setValue("aiFeaturesEnabled", newValue);
    setConfirmOpen(true);
  }

  function handleCancel() {
    setIsAIFeatureSwitchEnabled(organization?.aiFeaturesEnabled ?? false);
    setConfirmOpen(false);
  }

  function handleConfirm() {
    if (!organization || !hasAccess) return;
    capture("organization_settings:ai_features_toggle");
    updateAIFeatures.mutate({
      orgId: organization.id,
      aiFeaturesEnabled: isAIFeatureSwitchEnabled,
    });
  }

  if (!isLangfuseCloud) return null;

  return (
    <div>
      <Header title={localize(language, "AI Features", "AI 功能")} />
      <Card className="mb-4 p-3">
        <div className="flex flex-row items-center justify-between">
          <div className="flex flex-col gap-1">
            <h4 className="font-semibold">
              {localize(
                language,
                "Enable AI powered features for your organization",
                "为你的组织启用 AI 功能",
              )}
            </h4>
            <p className="text-sm">
              {localize(
                language,
                "This setting applies to all users and projects. Any data ",
                "此设置适用于所有用户和项目。部分数据",
              )}
              <i>{localize(language, "can", "可能")}</i>
              {localize(
                language,
                " be sent to AWS Bedrock within the Langfuse data region. Traces are sent to Langfuse Cloud in your data region. Your data will not be used for training models. Applicable HIPAA, SOC2, GDPR, and ISO 27001 compliance remains intact. ",
                " 被发送到你的 Langfuse 数据区域内的 AWS Bedrock。Traces 会发送到你所在数据区域的 Langfuse Cloud。你的数据不会被用于训练模型。适用的 HIPAA、SOC2、GDPR 和 ISO 27001 合规性仍然保持有效。",
              )}
              <a
                href="https://langfuse.com/security/ai-features"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-primary hover:underline"
              >
                {localize(
                  language,
                  "More details in the docs here.",
                  "更多详情请查看文档。",
                )}
                <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          </div>
          <div className="relative">
            <Switch
              checked={isAIFeatureSwitchEnabled}
              onCheckedChange={handleSwitchChange}
              disabled={!hasAccess}
            />
            {!hasAccess && (
              <span title={localize(language, "No access", "无访问权限")}>
                <LockIcon className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 transform text-muted" />
              </span>
            )}
          </div>
        </div>
      </Card>

      <Dialog
        open={confirmOpen}
        onOpenChange={(isOpen) => {
          if (!isOpen && !updateAIFeatures.isPending) {
            handleCancel();
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {localize(
                language,
                "Confirm AI Features Change",
                "确认 AI 功能变更",
              )}
            </DialogTitle>
          </DialogHeader>
          <DialogBody>
            <span className="text-sm">
              {localize(language, "You are about to ", "你即将")}
              <strong>
                {isAIFeatureSwitchEnabled
                  ? localize(language, "enable ", "启用")
                  : localize(language, "disable ", "禁用")}
              </strong>{" "}
              {localize(
                language,
                "AI features for your organization. When enabled, any data ",
                "你组织的 AI 功能。启用后，部分数据",
              )}
              <i>{localize(language, "can", "可能")}</i>
              {localize(
                language,
                " be sent to AWS Bedrock in your data region for processing.",
                " 会被发送到你所在数据区域的 AWS Bedrock 进行处理。",
              )}
              <br />
              <br />{" "}
              <a
                href="https://langfuse.com/security/ai-features"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-primary hover:underline"
              >
                {localize(
                  language,
                  "Learn more in the docs.",
                  "在文档中了解更多。",
                )}
                <ExternalLink className="h-3 w-3" />
              </a>
            </span>
            <p className="mt-3 text-sm text-muted-foreground">
              {localize(
                language,
                "Are you sure you want to proceed?",
                "确定要继续吗？",
              )}
            </p>
          </DialogBody>
          <DialogFooter>
            <div className="flex justify-end space-x-2">
              <Button
                type="button"
                variant="outline"
                disabled={updateAIFeatures.isPending}
                onClick={handleCancel}
              >
                {localize(language, "Cancel", "取消")}
              </Button>
              <Button
                type="submit"
                onClick={handleConfirm}
                loading={updateAIFeatures.isPending}
              >
                {localize(language, "Confirm", "确认")}
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
