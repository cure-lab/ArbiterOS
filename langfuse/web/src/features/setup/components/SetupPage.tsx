import Header from "@/src/components/layouts/header";
import ContainerPage from "@/src/components/layouts/container-page";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/src/components/ui/breadcrumb";
import { Button } from "@/src/components/ui/button";
import { Card } from "@/src/components/ui/card";
import { NewOrganizationForm } from "@/src/features/organizations/components/NewOrganizationForm";
import { NewProjectForm } from "@/src/features/projects/components/NewProjectForm";
import { useQueryProjectOrOrganization } from "@/src/features/projects/hooks";
import { MembershipInvitesPage } from "@/src/features/rbac/components/MembershipInvitesPage";
import { MembersTable } from "@/src/features/rbac/components/MembersTable";
import {
  createProjectRoute,
  inviteMembersRoute,
} from "@/src/features/setup/setupRoutes";
import { cn } from "@/src/utils/tailwind";
import { Check } from "lucide-react";
import { useRouter } from "next/router";
import { StringParam, useQueryParam } from "use-query-params";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";

// Multi-step setup process
// 1. Create Organization: /setup
// 2. Invite Members: /organization/:orgId/setup
// 3. Create Project: /organization/:orgId/setup?step=create-project
export function SetupPage() {
  const { t } = useLanguage();
  const { project, organization } = useQueryProjectOrOrganization();
  const router = useRouter();
  const [orgStep] = useQueryParam("orgstep", StringParam); // "invite-members" | "create-project"

  // starts at 1 to align with breadcrumb
  const stepInt = !organization
    ? 1
    : project
      ? 3
      : orgStep === "create-project"
        ? 3
        : 2;

  return (
    <ContainerPage
      headerProps={{
        title: t("setup.pageTitle"),
        help: {
          description: t("setup.helpDescription"),
        },
        ...(stepInt === 1 && {
          breadcrumb: [
            {
              name: t("setup.breadcrumbOrganizations"),
              href: "/",
            },
          ],
        }),
      }}
    >
      <Breadcrumb className="mb-3">
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbPage
              className={cn(
                stepInt !== 1
                  ? "text-muted-foreground"
                  : "font-semibold text-foreground",
              )}
            >
              1. {t("setup.stepCreateOrganization")}
              {stepInt > 1 && <Check className="ml-1 inline-block h-3 w-3" />}
            </BreadcrumbPage>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage
              className={cn(
                stepInt !== 2
                  ? "text-muted-foreground"
                  : "font-semibold text-foreground",
              )}
            >
              2. {t("setup.stepInviteMembers")}
              {stepInt > 2 && <Check className="ml-1 inline-block h-3 w-3" />}
            </BreadcrumbPage>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage
              className={cn(
                stepInt !== 3
                  ? "text-muted-foreground"
                  : "font-semibold text-foreground",
              )}
            >
              3. {t("setup.stepCreateProject")}
            </BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      <Card className="p-3">
        {
          // 1. Create Org
          stepInt === 1 && (
            <div>
              <Header title={t("setup.newOrganizationTitle")} />
              <p className="mb-4 text-sm text-muted-foreground">
                {t("setup.newOrganizationDescription")}
              </p>
              <NewOrganizationForm
                onSuccess={(orgId) => {
                  router.push(inviteMembersRoute(orgId));
                }}
              />
            </div>
          )
        }
        {
          // 2. Invite Members
          stepInt === 2 && organization && (
            <div className="flex flex-col gap-10">
              <div>
                <Header title={t("setup.organizationMembersTitle")} />
                <p className="mb-4 text-sm text-muted-foreground">
                  {t("setup.organizationMembersDescription")}
                </p>
                <MembersTable orgId={organization.id} />
              </div>
              <div>
                <MembershipInvitesPage orgId={organization.id} />
              </div>
            </div>
          )
        }
        {
          // 3. Create Project
          stepInt === 3 && organization && (
            <div>
              <Header title={t("setup.newProjectTitle")} />
              <p className="mb-4 text-sm text-muted-foreground">
                {t("setup.newProjectDescription")}
              </p>
              <NewProjectForm
                orgId={organization.id}
                onSuccess={(projectId) =>
                  router.push(`/project/${projectId}/traces`)
                }
              />
            </div>
          )
        }
      </Card>

      {stepInt === 2 && organization && (
        <Button
          className="mt-4 self-start"
          data-testid="btn-skip-add-members"
          onClick={() => router.push(createProjectRoute(organization.id))}
        >
          {t("setup.nextButton")}
        </Button>
      )}
    </ContainerPage>
  );
}
