import { env } from "@/src/env.mjs";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";

export const CloudPrivacyNotice = ({
  action,
}: {
  action: "signingIn" | "creatingAccount";
}) => {
  const { t } = useLanguage();

  if (env.NEXT_PUBLIC_LANGFUSE_CLOUD_REGION === undefined) return null;

  return (
    <div className="mx-auto mt-10 max-w-lg text-center text-xs text-muted-foreground">
      {t("auth.privacy.prefix")}{" "}
      {action === "signingIn"
        ? t("auth.privacy.signingIn")
        : t("auth.privacy.creatingAccount")}{" "}
      <a
        href="https://langfuse.com/terms"
        target="_blank"
        rel="noopener noreferrer"
        className="italic"
      >
        {t("auth.privacy.terms")}
      </a>
      ,{" "}
      <a
        href="https://langfuse.com/privacy"
        rel="noopener noreferrer"
        className="italic"
      >
        {t("auth.privacy.privacy")}
      </a>
      , and{" "}
      <a
        href="https://langfuse.com/cookie-policy"
        rel="noopener noreferrer"
        className="italic"
      >
        {t("auth.privacy.cookie")}
      </a>
      . {t("auth.privacy.accuracy")}
    </div>
  );
};
