import Header from "@/src/components/layouts/header";
import { ApiKeyList } from "@/src/features/public-api/components/ApiKeyList";
import { DeleteProjectButton } from "@/src/features/projects/components/DeleteProjectButton";
import { HostNameProject } from "@/src/features/projects/components/HostNameProject";
import RenameProject from "@/src/features/projects/components/RenameProject";
import { LlmApiKeyList } from "@/src/features/public-api/components/LLMApiKeyList";
import { PagedSettingsContainer } from "@/src/components/PagedSettingsContainer";
import { useQueryProject } from "@/src/features/projects/hooks";
import { MembershipInvitesPage } from "@/src/features/rbac/components/MembershipInvitesPage";
import { MembersTable } from "@/src/features/rbac/components/MembersTable";
import { JSONView } from "@/src/components/ui/CodeJsonViewer";
import { TransferProjectButton } from "@/src/features/projects/components/TransferProjectButton";
import { useHasEntitlement } from "@/src/features/entitlements/hooks";
import { useRouter } from "next/router";
import { SettingsDangerZone } from "@/src/components/SettingsDangerZone";
import { ModelsSettings } from "@/src/features/models/components/ModelSettings";
import ConfigureRetention from "@/src/features/projects/components/ConfigureRetention";
import ContainerPage from "@/src/components/layouts/container-page";
import { env } from "@/src/env.mjs";
import { ErrorAnalysisSettings } from "@/src/features/error-analysis/components/ErrorAnalysisSettings";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import type { TranslationKey } from "@/src/features/i18n/messages";

type ProjectSettingsPage = {
  title: string;
  slug: string;
  show?: boolean | (() => boolean);
  cmdKKeywords?: string[];
} & ({ content: React.ReactNode } | { href: string });

export function useProjectSettingsPages(): ProjectSettingsPage[] {
  const { t } = useLanguage();
  const router = useRouter();
  const { project, organization } = useQueryProject();
  const showBillingSettings = useHasEntitlement("cloud-billing");
  const showRetentionSettings = useHasEntitlement("data-retention");

  if (!project || !organization || !router.query.projectId) {
    return [];
  }

  return getProjectSettingsPages({
    project,
    organization,
    showBillingSettings,
    showRetentionSettings,
    showLLMConnectionsSettings: true,
    t,
  });
}

export const getProjectSettingsPages = ({
  project,
  organization,
  showBillingSettings,
  showRetentionSettings,
  showLLMConnectionsSettings,
  t,
}: {
  project: { id: string; name: string; metadata: Record<string, unknown> };
  organization: { id: string; name: string; metadata: Record<string, unknown> };
  showBillingSettings: boolean;
  showRetentionSettings: boolean;
  showLLMConnectionsSettings: boolean;
  t: (key: TranslationKey) => string;
}): ProjectSettingsPage[] => [
  {
    title: t("projectSettings.general"),
    slug: "index",
    cmdKKeywords: ["name", "id", "delete", "transfer", "ownership"],
    content: (
      <div className="flex flex-col gap-6">
        <HostNameProject />
        <RenameProject />
        {showRetentionSettings && <ConfigureRetention />}
        <div>
          <Header title={t("projectSettings.debugInformation")} />
          <JSONView
            title={t("projectSettings.metadata")}
            json={{
              project: {
                name: project.name,
                id: project.id,
                ...project.metadata,
              },
              org: {
                name: organization.name,
                id: organization.id,
                ...organization.metadata,
              },
              ...(env.NEXT_PUBLIC_LANGFUSE_CLOUD_REGION && {
                cloudRegion: env.NEXT_PUBLIC_LANGFUSE_CLOUD_REGION,
              }),
            }}
          />
        </div>
        <SettingsDangerZone
          items={[
            {
              title: t("projectSettings.transferOwnershipTitle"),
              description: t("projectSettings.transferOwnershipDescription"),
              button: <TransferProjectButton />,
            },
            {
              title: t("projectSettings.deleteProjectTitle"),
              description: t("projectSettings.deleteProjectDescription"),
              button: <DeleteProjectButton />,
            },
          ]}
        />
      </div>
    ),
  },
  {
    title: t("projectSettings.apiKeys"),
    slug: "api-keys",
    cmdKKeywords: ["auth", "public key", "secret key"],
    content: (
      <div className="flex flex-col gap-6">
        <ApiKeyList entityId={project.id} scope="project" />
      </div>
    ),
  },
  {
    title: t("projectSettings.llmConnections"),
    slug: "llm-connections",
    cmdKKeywords: [
      "llm",
      "provider",
      "openai",
      "anthropic",
      "azure",
      "playground",
      "evaluation",
      "endpoint",
      "api",
    ],
    content: (
      <div className="flex flex-col gap-6">
        <LlmApiKeyList projectId={project.id} />
      </div>
    ),
    show: showLLMConnectionsSettings,
  },
  {
    title: t("projectSettings.modelDefinitions"),
    slug: "models",
    cmdKKeywords: ["cost", "token"],
    content: <ModelsSettings projectId={project.id} />,
  },
  {
    title: t("projectSettings.errorAnalysis"),
    slug: "error-analysis",
    cmdKKeywords: ["analysis", "error", "warning", "auto"],
    content: <ErrorAnalysisSettings projectId={project.id} />,
  },
  {
    title: t("projectSettings.members"),
    slug: "members",
    cmdKKeywords: ["invite", "user"],
    content: (
      <div>
        <Header title={t("projectSettings.projectMembers")} />
        <MembersTable
          orgId={organization.id}
          project={{ id: project.id, name: project.name }}
          showSettingsCard
        />
        <div>
          <MembershipInvitesPage
            orgId={organization.id}
            projectId={project.id}
          />
        </div>
      </div>
    ),
  },
  {
    title: t("projectSettings.billing"),
    slug: "billing",
    href: `/organization/${organization.id}/settings/billing`,
    show: showBillingSettings,
  },
  {
    title: t("projectSettings.organizationSettings"),
    slug: "organization",
    href: `/organization/${organization.id}/settings`,
  },
];

export default function SettingsPage() {
  const { t } = useLanguage();
  const { project, organization } = useQueryProject();
  const router = useRouter();
  const pages = useProjectSettingsPages();

  if (!project || !organization) return null;

  return (
    <ContainerPage
      headerProps={{
        title: t("projectSettings.pageTitle"),
      }}
    >
      <PagedSettingsContainer
        activeSlug={router.query.page as string | undefined}
        pages={pages}
      />
    </ContainerPage>
  );
}
