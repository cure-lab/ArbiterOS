import { Card } from "@/src/components/ui/card";
import { CodeView } from "@/src/components/ui/CodeJsonViewer";
import Header from "@/src/components/layouts/header";
import { useUiCustomization } from "@/src/ee/features/ui-customization/useUiCustomization";
import { env } from "@/src/env.mjs";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export function HostNameProject() {
  const uiCustomization = useUiCustomization();
  const { language } = useLanguage();
  return (
    <div>
      <Header title={localize(language, "Host Name", "主机名")} />
      <Card className="mb-4 p-3">
        <div className="">
          <div className="mb-2 text-sm">
            {localize(
              language,
              "When connecting to Langfuse, use this hostname / baseurl.",
              "连接到 Langfuse 时，请使用此主机名 / baseurl。",
            )}
          </div>
          <CodeView
            content={`${uiCustomization?.hostname ?? window.origin}${env.NEXT_PUBLIC_BASE_PATH ?? ""}`}
          />
        </div>
      </Card>
    </div>
  );
}
