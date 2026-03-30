import { type Flag } from "@/src/features/feature-flags/types";
import { type ProjectScope } from "@/src/features/rbac/constants/projectAccessRights";
import {
  LayoutDashboard,
  ListTree,
  type LucideIcon,
  Settings,
  UsersIcon,
  BookOpen,
  Grid2X2,
  Search,
  Home,
  Clock,
  Shield,
} from "lucide-react";
import { type ReactNode, useMemo } from "react";
import { type Entitlement } from "@/src/features/entitlements/constants/entitlements";
import { type User } from "next-auth";
import { type OrganizationScope } from "@/src/features/rbac/constants/organizationAccessRights";
import { BookACallButton } from "@/src/components/nav/book-a-call-button";
import { V4BetaSidebarToggle } from "@/src/features/events/components/V4BetaSidebarToggle";
import { SidebarMenuButton } from "@/src/components/ui/sidebar";
import { useCommandMenu } from "@/src/features/command-k-menu/CommandMenuProvider";
import { usePostHogClientCapture } from "@/src/features/posthog-analytics/usePostHogClientCapture";
import { CloudStatusMenu } from "@/src/features/cloud-status-notification/components/CloudStatusMenu";
import { type ProductModule } from "@/src/ee/features/ui-customization/productModuleSchema";
import { AnalysisIcon } from "@/src/components/icons/AnalysisIcon";
import { LanguageSwitcher } from "@/src/features/i18n/LanguageSwitcher";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { messages, type TranslationKey } from "@/src/features/i18n/messages";

export enum RouteSection {
  Main = "main",
  Secondary = "secondary",
}

export enum RouteGroup {
  Observability = "Observability",
  Governance = "Governance",
}

export type Route = {
  title: string;
  menuNode?: ReactNode;
  featureFlag?: Flag;
  label?: string | ReactNode;
  projectRbacScopes?: ProjectScope[]; // array treated as OR
  organizationRbacScope?: OrganizationScope;
  icon?: LucideIcon; // ignored for nested routes
  pathname: string; // link
  items?: Array<Route>; // folder
  section?: RouteSection; // which section of the sidebar (top/main/bottom)
  newTab?: boolean; // open in new tab
  entitlements?: Entitlement[]; // entitlements required, array treated as OR
  productModule?: ProductModule; // Product module this route belongs to. Used to show/hide modules via ui customization.
  show?: (p: {
    organization: User["organizations"][number] | undefined;
  }) => boolean;
  group?: RouteGroup; // group this route belongs to (within a section)
};

type TranslateFn = (key: TranslationKey) => string;

function buildRoutes(
  t: TranslateFn,
  options?: { includeLanguageMenu?: boolean },
): Route[] {
  return [
    {
      title: t("nav.goTo"),
      pathname: "", // Empty pathname since this is a dropdown
      icon: Search,
      menuNode: <CommandMenuTrigger />,
      section: RouteSection.Main,
    },
    {
      title: t("nav.organizations"),
      pathname: "/",
      icon: Grid2X2,
      show: ({ organization }) => organization === undefined,
      section: RouteSection.Main,
    },
    {
      title: t("nav.projects"),
      pathname: "/organization/[organizationId]",
      icon: Grid2X2,
      section: RouteSection.Main,
    },
    {
      title: t("nav.home"),
      pathname: `/project/[projectId]`,
      icon: Home,
      section: RouteSection.Main,
    },
    {
      title: t("nav.dashboards"),
      pathname: `/project/[projectId]/dashboards`,
      icon: LayoutDashboard,
      productModule: "dashboards",
      section: RouteSection.Main,
    },
    {
      title: t("nav.tracing"),
      icon: ListTree,
      productModule: "tracing",
      group: RouteGroup.Observability,
      section: RouteSection.Main,
      pathname: `/project/[projectId]/traces`,
    },
    {
      title: t("nav.sessions"),
      icon: Clock,
      productModule: "tracing",
      group: RouteGroup.Observability,
      section: RouteSection.Main,
      pathname: `/project/[projectId]/sessions`,
    },
    {
      title: t("nav.users"),
      pathname: `/project/[projectId]/users`,
      icon: UsersIcon,
      productModule: "tracing",
      group: RouteGroup.Observability,
      section: RouteSection.Main,
    },
    {
      title: t("nav.analysis"),
      pathname: `/project/[projectId]/analysis`,
      group: RouteGroup.Governance,
      section: RouteSection.Main,
      icon: AnalysisIcon,
    },
    {
      title: t("nav.summary"),
      pathname: `/project/[projectId]/experience`,
      group: RouteGroup.Governance,
      section: RouteSection.Main,
      icon: BookOpen,
    },
    {
      title: t("nav.policy"),
      pathname: `/project/[projectId]/policy`,
      group: RouteGroup.Governance,
      section: RouteSection.Main,
      icon: Shield,
    },
    {
      title: t("nav.cloudStatus"),
      section: RouteSection.Secondary,
      pathname: "",
      menuNode: <CloudStatusMenu />,
    },
    {
      title: t("nav.v4Beta"),
      pathname: "",
      section: RouteSection.Secondary,
      featureFlag: "v4BetaToggleVisible",
      menuNode: <V4BetaSidebarToggle />,
    },
    {
      title: t("nav.language"),
      pathname: "",
      section: RouteSection.Secondary,
      menuNode: options?.includeLanguageMenu ? (
        <LanguageSwitcher variant="sidebar" />
      ) : undefined,
    },
    {
      title: t("nav.settings"),
      pathname: "/project/[projectId]/settings",
      icon: Settings,
      section: RouteSection.Secondary,
    },
    {
      title: t("nav.settings"),
      pathname: "/organization/[organizationId]/settings",
      icon: Settings,
      section: RouteSection.Secondary,
    },
    {
      title: t("nav.bookACall"),
      section: RouteSection.Secondary,
      pathname: "",
      menuNode: <BookACallButton />,
    },
  ];
}

export const ROUTES: Route[] = buildRoutes((key) => messages.en[key]);

export function useRoutes() {
  const { t } = useLanguage();
  return useMemo(() => buildRoutes(t, { includeLanguageMenu: true }), [t]);
}

function CommandMenuTrigger() {
  const { t } = useLanguage();
  const { setOpen } = useCommandMenu();
  const capture = usePostHogClientCapture();

  return (
    <SidebarMenuButton
      onClick={() => {
        capture("cmd_k_menu:opened", {
          source: "main_navigation",
        });
        setOpen(true);
      }}
      className="whitespace-nowrap"
    >
      <Search className="h-4 w-4" />
      {t("nav.goTo")}
      <kbd className="pointer-events-none ml-auto inline-flex h-5 select-none items-center gap-1 rounded-md border px-1.5 font-mono text-[10px]">
        {navigator.userAgent.includes("Mac") ? (
          <span className="text-[12px]">⌘</span>
        ) : (
          <span>Ctrl</span>
        )}
        <span>K</span>
      </kbd>
    </SidebarMenuButton>
  );
}
