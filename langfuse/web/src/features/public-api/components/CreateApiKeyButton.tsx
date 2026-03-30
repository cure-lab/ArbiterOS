import { Button } from "@/src/components/ui/button";
import { api } from "@/src/utils/api";
import { useState } from "react";
import { PlusIcon } from "lucide-react";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/src/components/ui/dialog";
import { CodeView } from "@/src/components/ui/CodeJsonViewer";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { useHasOrganizationAccess } from "@/src/features/rbac/utils/checkOrganizationAccess";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { Input } from "@/src/components/ui/input";
import { useLangfuseEnvCode } from "@/src/features/public-api/hooks/useLangfuseEnvCode";
import { Label } from "@/src/components/ui/label";
import { cn } from "@/src/utils/tailwind";
import { SubHeader } from "@/src/components/layouts/header";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

type ApiKeyScope = "project" | "organization";

export function CreateApiKeyButton(props: {
  entityId: string;
  scope: ApiKeyScope;
}) {
  const utils = api.useUtils();
  const capture = usePostHogClientCapture();
  const { language } = useLanguage();

  const hasProjectAccess = useHasProjectAccess({
    projectId: props.entityId,
    scope: "apiKeys:CUD",
  });
  const hasOrganizationAccess = useHasOrganizationAccess({
    organizationId: props.entityId,
    scope: "organization:CRUD_apiKeys",
  });

  const hasAccess =
    props.scope === "project" ? hasProjectAccess : hasOrganizationAccess;

  const mutCreateProjectApiKey = api.projectApiKeys.create.useMutation({
    onSuccess: () => utils.projectApiKeys.invalidate(),
  });
  const mutCreateOrgApiKey = api.organizationApiKeys.create.useMutation({
    onSuccess: () => utils.organizationApiKeys.invalidate(),
  });

  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [generatedKeys, setGeneratedKeys] = useState<{
    secretKey: string;
    publicKey: string;
  } | null>(null);

  const handleOpenChange = (newOpen: boolean) => {
    setOpen(newOpen);
    if (!newOpen) {
      // Reset state when closing
      setGeneratedKeys(null);
      setNote("");
    }
  };

  const createApiKey = () => {
    if (props.scope === "project") {
      mutCreateProjectApiKey
        .mutateAsync({
          projectId: props.entityId,
          note: note || undefined,
        })
        .then(({ secretKey, publicKey }) => {
          setGeneratedKeys({
            secretKey,
            publicKey,
          });
          capture(`${props.scope}_settings:api_key_create`);
        })
        .catch((error) => {
          console.error(error);
        });
    } else {
      mutCreateOrgApiKey
        .mutateAsync({
          orgId: props.entityId,
          note: note || undefined,
        })
        .then(({ secretKey, publicKey }) => {
          setGeneratedKeys({
            secretKey,
            publicKey,
          });
          capture(`${props.scope}_settings:api_key_create`);
        })
        .catch((error) => {
          console.error(error);
        });
    }
  };

  if (!hasAccess) return null;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="secondary">
          <PlusIcon className="-ml-0.5 mr-1.5 h-5 w-5" aria-hidden="true" />
          {localize(language, "Create new API keys", "创建新的 API 密钥")}
        </Button>
      </DialogTrigger>
      <DialogContent onPointerDownOutside={(e) => e.preventDefault()}>
        <DialogHeader>
          <DialogTitle>
            {generatedKeys
              ? localize(language, "API Keys", "API 密钥")
              : localize(language, "Create API Keys", "创建 API 密钥")}
          </DialogTitle>
        </DialogHeader>
        <DialogBody>
          {generatedKeys ? (
            <ApiKeyRender scope={props.scope} generatedKeys={generatedKeys} />
          ) : (
            <div className="space-y-4">
              <div>
                <Label htmlFor="note">
                  {localize(language, "Note (optional)", "备注（可选）")}
                </Label>
                <Input
                  id="note"
                  placeholder={localize(
                    language,
                    "Production key",
                    "生产环境密钥",
                  )}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      createApiKey();
                    }
                  }}
                  className="mt-1.5"
                />
              </div>
            </div>
          )}
        </DialogBody>
        {!generatedKeys && (
          <DialogFooter>
            <Button
              onClick={createApiKey}
              loading={
                mutCreateProjectApiKey.isPending || mutCreateOrgApiKey.isPending
              }
            >
              {localize(language, "Create API keys", "创建 API 密钥")}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

export const ApiKeyRender = ({
  scope,
  generatedKeys,
  className,
}: {
  scope: ApiKeyScope;
  generatedKeys?: { secretKey: string; publicKey: string };
  className?: string;
}) => {
  const envCode = useLangfuseEnvCode(generatedKeys);
  const { language } = useLanguage();

  return (
    <div className={cn("space-y-6", className)}>
      <div>
        <SubHeader title={localize(language, "Secret Key", "密钥")} />
        <div className="text-sm text-muted-foreground">
          {localize(
            language,
            `This key can only be viewed once. You can always create new keys in the ${scope} settings.`,
            `此密钥只能查看一次。你始终可以在${scope === "project" ? "项目" : "组织"}设置中创建新的密钥。`,
          )}
        </div>
        <CodeView
          content={
            generatedKeys?.secretKey ??
            localize(language, "Loading ...", "加载中...")
          }
          className="mt-2"
        />
      </div>
      <div>
        <SubHeader title={localize(language, "Public Key", "公钥")} />
        <CodeView
          content={
            generatedKeys?.publicKey ??
            localize(language, "Loading ...", "加载中...")
          }
          className="mt-2"
        />
      </div>
      <div>
        <SubHeader title={localize(language, ".env", ".env")} />
        <CodeView content={envCode} className="mt-2" />
      </div>
    </div>
  );
};
