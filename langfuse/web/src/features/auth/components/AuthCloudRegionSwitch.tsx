import { env } from "@/src/env.mjs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/src/components/ui/select";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/src/components/ui/dialog";
import { useLangfuseCloudRegion } from "@/src/features/organizations/hooks";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";

const regions =
  env.NEXT_PUBLIC_LANGFUSE_CLOUD_REGION === "STAGING"
    ? [
        {
          name: "STAGING",
          hostname: "staging.langfuse.com",
          flag: "🇪🇺",
        },
      ]
    : env.NEXT_PUBLIC_LANGFUSE_CLOUD_REGION === "DEV"
      ? [
          {
            name: "DEV",
            hostname: null,
            flag: "🚧",
          },
        ]
      : [
          {
            name: "US",
            hostname: "us.cloud.langfuse.com",
            flag: "🇺🇸",
          },
          {
            name: "EU",
            hostname: "cloud.langfuse.com",
            flag: "🇪🇺",
          },
          {
            name: "HIPAA",
            hostname: "hipaa.cloud.langfuse.com",
            flag: "⚕️",
          },
        ];

export function CloudRegionSwitch({
  isSignUpPage,
}: {
  isSignUpPage?: boolean;
}) {
  const capture = usePostHogClientCapture();
  const { isLangfuseCloud, region: cloudRegion } = useLangfuseCloudRegion();
  const { t } = useLanguage();

  if (!isLangfuseCloud) return null;

  const currentRegion = regions.find((region) => region.name === cloudRegion);

  return (
    <div className="-mb-10 mt-8 rounded-lg bg-card px-6 py-6 text-sm sm:mx-auto sm:w-full sm:max-w-[480px] sm:rounded-lg sm:px-10">
      <div className="flex w-full flex-col gap-2">
        <div>
          <span className="text-sm font-medium leading-none">
            {t("auth.cloudRegion.dataRegion")}
            <DataRegionInfo />
          </span>
          {isSignUpPage && cloudRegion === "HIPAA" ? (
            <p className="text-xs text-muted-foreground">
              {t("auth.cloudRegion.demoUnavailableHipaa")}
            </p>
          ) : null}
        </div>
        <Select
          value={currentRegion?.name}
          onValueChange={(value) => {
            const region = regions.find((region) => region.name === value);
            if (!region) return;
            capture(
              "sign_in:cloud_region_switch",
              {
                region: region.name,
              },
              {
                send_instantly: true,
              },
            );
            if (region.hostname) {
              window.location.hostname = region.hostname;
            }
          }}
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {regions.map((region) => (
              <SelectItem key={region.name} value={region.name}>
                <span className="mr-2 text-xl leading-none">{region.flag}</span>
                {region.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {cloudRegion === "HIPAA" && (
          <div className="mt-2 rounded-md bg-muted/50 p-3 text-xs text-muted-foreground">
            <p>
              {t("auth.cloudRegion.hipaaNotice")}{" "}
              <a
                href="https://langfuse.com/security/hipaa"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary-accent underline hover:text-hover-primary-accent"
              >
                HIPAA →
              </a>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

const DataRegionInfo = () => (
  <Dialog>
    <DialogTrigger asChild>
      <a
        href="#"
        className="ml-1 text-xs text-primary-accent hover:text-hover-primary-accent"
        title="What is this?"
        tabIndex={-1}
      >
        <DataRegionInfoLabel />
      </a>
    </DialogTrigger>
    <DialogContent>
      <DialogHeader>
        <DialogTitle>
          <DataRegionInfoTitle />
        </DialogTitle>
      </DialogHeader>
      <DialogBody>
        <DialogDescription className="flex flex-col gap-2">
          <p>
            <DataRegionInfoIntro />
          </p>
          <ul className="list-disc pl-5">
            <li>
              <DataRegionInfoUs />
            </li>
            <li>
              <DataRegionInfoEu />
            </li>
            <li>
              <DataRegionInfoHipaa />
            </li>
          </ul>
          <p>
            <DataRegionInfoSeparation />
          </p>
          <p>
            <DataRegionInfoMultipleAccounts />
          </p>
          <p>
            <DataRegionInfoLearnMorePrefix />{" "}
            <a
              href="https://langfuse.com/security/data-regions"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary-accent underline"
            >
              <DataRegionInfoDataRegionsLink />
            </a>{" "}
            and{" "}
            <a
              href="https://langfuse.com/docs/data-security-privacy"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary-accent underline"
            >
              <DataRegionInfoSecurityLink />
            </a>
            .
          </p>
        </DialogDescription>
      </DialogBody>
    </DialogContent>
  </Dialog>
);

const DataRegionInfoLabel = () => {
  const { t } = useLanguage();
  return <>{t("auth.cloudRegion.whatIsThis")}</>;
};

const DataRegionInfoTitle = () => {
  const { t } = useLanguage();
  return <>{t("auth.cloudRegion.dataRegionsTitle")}</>;
};

const DataRegionInfoIntro = () => {
  const { t } = useLanguage();
  return <>{t("auth.cloudRegion.availableInThree")}</>;
};

const DataRegionInfoUs = () => {
  const { t } = useLanguage();
  return <>{t("auth.cloudRegion.us")}</>;
};

const DataRegionInfoEu = () => {
  const { t } = useLanguage();
  return <>{t("auth.cloudRegion.eu")}</>;
};

const DataRegionInfoHipaa = () => {
  const { t } = useLanguage();
  return <>{t("auth.cloudRegion.hipaa")}</>;
};

const DataRegionInfoSeparation = () => {
  const { t } = useLanguage();
  return <>{t("auth.cloudRegion.separation")}</>;
};

const DataRegionInfoMultipleAccounts = () => {
  const { t } = useLanguage();
  return <>{t("auth.cloudRegion.multipleAccounts")}</>;
};

const DataRegionInfoLearnMorePrefix = () => {
  const { t } = useLanguage();
  return <>{t("auth.cloudRegion.learnMore")}</>;
};

const DataRegionInfoDataRegionsLink = () => {
  const { t } = useLanguage();
  return <>{t("auth.cloudRegion.dataRegionsLink")}</>;
};

const DataRegionInfoSecurityLink = () => {
  const { t } = useLanguage();
  return <>{t("auth.cloudRegion.dataSecurityLink")}</>;
};
