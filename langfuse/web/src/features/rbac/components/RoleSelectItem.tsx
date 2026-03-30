import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/src/components/ui/hover-card";
import { SelectItem } from "@/src/components/ui/select";
import { Role } from "@langfuse/shared";
import { HoverCardPortal } from "@radix-ui/react-hover-card";
import {
  organizationRoleAccessRights,
  orgNoneRoleComment,
} from "@/src/features/rbac/constants/organizationAccessRights";
import {
  projectNoneRoleComment,
  projectRoleAccessRights,
} from "@/src/features/rbac/constants/projectAccessRights";
import { orderedRoles } from "@/src/features/rbac/constants/orderedRoles";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export const RoleSelectItem = ({
  role,
  isProjectRole,
}: {
  role: Role;
  isProjectRole?: boolean;
}) => {
  const isProjectNoneRole = role === Role.NONE && isProjectRole;
  const isOrgNoneRole = role === Role.NONE && !isProjectRole;
  const { language } = useLanguage();
  const orgScopes = reduceScopesToListItems(
    organizationRoleAccessRights,
    role,
    language,
  );
  const projectScopes = reduceScopesToListItems(
    projectRoleAccessRights,
    role,
    language,
  );

  return (
    <HoverCard openDelay={0} closeDelay={0}>
      <HoverCardTrigger asChild>
        <SelectItem value={role} className="max-w-56">
          <span>
            {formatRole(role, language)}
            {isProjectNoneRole
              ? localize(language, " (keep default role)", "（保留默认角色）")
              : ""}
          </span>
        </SelectItem>
      </HoverCardTrigger>
      <HoverCardPortal>
        <HoverCardContent hideWhenDetached={true} align="center" side="right">
          {isProjectNoneRole ? (
            <div className="text-xs">
              {localize(
                language,
                projectNoneRoleComment,
                "默认继承组织角色。只有在需要覆盖当前项目默认角色时才设置项目角色。",
              )}
            </div>
          ) : isOrgNoneRole ? (
            <div className="text-xs">
              {localize(
                language,
                orgNoneRoleComment,
                "默认不具备组织级资源权限。用户需要通过项目角色获得项目访问权限。",
              )}
            </div>
          ) : (
            <>
              <div className="font-bold">
                {localize(language, "Role", "角色")}:{" "}
                {formatRole(role, language)}
              </div>
              <p className="mt-2 text-xs font-semibold">
                {localize(language, "Organization Scopes", "组织权限范围")}
              </p>
              <ul className="list-inside list-disc text-xs">{orgScopes}</ul>
              <p className="mt-2 text-xs font-semibold">
                {localize(language, "Project Scopes", "项目权限范围")}
              </p>
              <ul className="list-inside list-disc text-xs">{projectScopes}</ul>
              <p className="mt-2 border-t pt-2 text-xs">
                {localize(language, "Note", "说明")}:{" "}
                <span className="text-muted-foreground">
                  {localize(language, "Muted scopes", "浅色显示的权限")}
                </span>{" "}
                {localize(
                  language,
                  "are inherited from lower role.",
                  "表示从更低角色继承而来。",
                )}
              </p>
            </>
          )}
        </HoverCardContent>
      </HoverCardPortal>
    </HoverCard>
  );
};

const reduceScopesToListItems = (
  accessRights: Record<string, string[]>,
  role: Role,
  language: ReturnType<typeof useLanguage>["language"],
) => {
  const currentRoleLevel = orderedRoles[role];
  const lowerRole = Object.entries(orderedRoles).find(
    ([_role, level]) => level === currentRoleLevel - 1,
  )?.[0] as Role | undefined;
  const inheritedScopes = lowerRole ? accessRights[lowerRole] : [];

  return accessRights[role].length > 0 ? (
    <>
      {Object.entries(
        accessRights[role].reduce(
          (acc, scope) => {
            const [resource, action] = scope.split(":");
            if (!acc[resource]) {
              acc[resource] = [];
            }
            acc[resource].push(action);
            return acc;
          },
          {} as Record<string, string[]>,
        ),
      ).map(([resource, actions]) => {
        const inheritedActions = actions.filter((action) =>
          inheritedScopes.includes(`${resource}:${action}`),
        );
        const newActions = actions.filter(
          (action) => !inheritedScopes.includes(`${resource}:${action}`),
        );

        return (
          <li key={resource}>
            <span>{resource}: </span>
            <span className="text-muted-foreground">
              {inheritedActions.length > 0 ? inheritedActions.join(", ") : ""}
              {newActions.length > 0 && inheritedActions.length > 0 ? ", " : ""}
            </span>
            <span className="font-semibold">
              {newActions.length > 0 ? newActions.join(", ") : ""}
            </span>
          </li>
        );
      })}
    </>
  ) : (
    <li>{localize(language, "None", "无")}</li>
  );
};

const formatRole = (
  role: Role,
  language: ReturnType<typeof useLanguage>["language"],
) =>
  localize(
    language,
    role.charAt(0).toUpperCase() + role.slice(1).toLowerCase(),
    role === Role.OWNER
      ? "所有者"
      : role === Role.ADMIN
        ? "管理员"
        : role === Role.MEMBER
          ? "成员"
          : role === Role.VIEWER
            ? "查看者"
            : "无",
  );
