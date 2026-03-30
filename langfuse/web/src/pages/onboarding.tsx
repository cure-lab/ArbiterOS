// This page is currently only shown to Langfuse cloud users.
// It might be expanded to everyone in the future when it does not only ask for the referral source.

import Head from "next/head";
import { OnboardingSurvey } from "@/src/features/onboarding/components/OnboardingSurvey";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export default function OnboardingPage() {
  const { language } = useLanguage();

  return (
    <>
      <Head>
        <title>
          {localize(language, "Onboarding | Langfuse", "引导设置 | Langfuse")}
        </title>
      </Head>
      <OnboardingSurvey />
    </>
  );
}
