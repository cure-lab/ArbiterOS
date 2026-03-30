import { DataTable } from "@/src/components/table/data-table";
import { DataTableToolbar } from "@/src/components/table/data-table-toolbar";
import { type LangfuseColumnDef } from "@/src/components/table/types";
import {
  Avatar,
  AvatarFallback,
  AvatarImage,
} from "@/src/components/ui/avatar";
import { useHasOrganizationAccess } from "@/src/features/rbac/utils/checkOrganizationAccess";
import { api } from "@/src/utils/api";
import { safeExtract } from "@/src/utils/map-utils";
import type { RouterOutput } from "@/src/utils/types";
import { Trash } from "lucide-react";
import { type Organization, type Role } from "@langfuse/shared";
import { useHasProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import Header from "@/src/components/layouts/header";
import useSessionStorage from "@/src/components/useSessionStorage";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";

export type tmp = Organization;
export type InvitesTableRow = {
  email: string;
  createdAt: Date;
  orgRole: Role;
  projectRole?: Role;
  invitedByUser: {
    name: string | null;
    image: string | null;
  } | null;
  meta: {
    inviteId: string;
  };
};

export function MembershipInvitesPage({
  orgId,
  projectId,
}: {
  orgId: string;
  projectId?: string;
}) {
  const { language } = useLanguage();
  const paginationKey = projectId
    ? `projectInvites_${projectId}_pagination`
    : `orgInvites_${orgId}_pagination`;

  const hasOrgViewAccess = useHasOrganizationAccess({
    organizationId: orgId,
    scope: "organizationMembers:read",
  });
  const hasProjectViewAccess =
    useHasProjectAccess({
      projectId,
      scope: "projectMembers:read",
    }) || hasOrgViewAccess;

  const [paginationState, setPaginationState] = useSessionStorage(
    paginationKey,
    {
      pageIndex: 0,
      pageSize: 10,
    },
  );

  const invites = projectId
    ? api.members.allInvitesFromProject.useQuery(
        {
          projectId,
          page: paginationState.pageIndex,
          limit: paginationState.pageSize,
        },
        {
          enabled: hasProjectViewAccess,
        },
      )
    : api.members.allInvitesFromOrg.useQuery(
        {
          orgId,
          page: paginationState.pageIndex,
          limit: paginationState.pageSize,
        },
        {
          enabled: hasOrgViewAccess,
        },
      );

  const totalCount = invites.data?.totalCount ?? null;

  const utils = api.useUtils();

  const mutDeleteInvite = api.members.deleteInvite.useMutation({
    onSuccess: () => utils.members.invalidate(),
  });

  const hasCudAccess = useHasOrganizationAccess({
    organizationId: orgId,
    scope: "organizationMembers:CUD",
  });

  const columns: LangfuseColumnDef<InvitesTableRow>[] = [
    {
      accessorKey: "email",
      id: "email",
      header: localize(language, "Email", "邮箱"),
    },
    {
      accessorKey: "orgRole",
      id: "orgRole",
      header: localize(language, "Organization Role", "组织角色"),
    },
    {
      accessorKey: "createdAt",
      id: "createdAt",
      header: localize(language, "Invited On", "邀请时间"),
      cell: ({ row }) => {
        const value = row.getValue("createdAt") as InvitesTableRow["createdAt"];
        return value ? new Date(value).toLocaleString() : undefined;
      },
    },
    ...(projectId
      ? [
          {
            accessorKey: "projectRole",
            id: "projectRole",
            header: localize(language, "Project Role", "项目角色"),
          },
        ]
      : []),
    {
      accessorKey: "invitedByUser",
      id: "invitedByUser",
      header: localize(language, "Invited By", "邀请人"),
      cell: ({ row }) => {
        const invitedByUser = row.getValue(
          "invitedByUser",
        ) as InvitesTableRow["invitedByUser"];
        const { name, image } = invitedByUser || {};
        return (
          <div className="flex items-center space-x-2">
            <Avatar className="h-7 w-7">
              <AvatarImage
                src={image ?? undefined}
                alt={name ?? localize(language, "User Avatar", "用户头像")}
              />
              <AvatarFallback>
                {name
                  ? name
                      .split(" ")
                      .map((word) => word[0])
                      .slice(0, 2)
                      .concat("")
                  : null}
              </AvatarFallback>
            </Avatar>
            <span>{name ?? "-"}</span>
          </div>
        );
      },
    },
    {
      accessorKey: "meta",
      id: "meta",
      header: localize(language, "Actions", "操作"),
      cell: ({ row }) => {
        const { inviteId } = row.getValue("meta") as InvitesTableRow["meta"];
        return hasCudAccess ? (
          <div className="flex space-x-2">
            <button
              onClick={() => {
                if (
                  confirm(
                    localize(
                      language,
                      "Are you sure you want to cancel this invitation?",
                      "确定要取消这条邀请吗？",
                    ),
                  )
                ) {
                  mutDeleteInvite.mutate({ inviteId, orgId });
                }
              }}
            >
              <Trash size={14} />
            </button>
          </div>
        ) : null;
      },
    },
  ];

  const convertToTableRow = (
    invite: RouterOutput["members"]["allInvitesFromOrg"]["invitations"][0],
  ): InvitesTableRow => {
    return {
      meta: {
        inviteId: invite.id,
      },
      email: invite.email,
      createdAt: invite.createdAt,
      orgRole: invite.orgRole,
      projectRole:
        invite.projectId === projectId
          ? (invite.projectRole ?? undefined)
          : undefined,
      invitedByUser: invite.invitedByUser,
    };
  };

  if (projectId ? !hasProjectViewAccess : !hasOrgViewAccess) {
    return null;
  }

  if (totalCount === 0) return null;

  return (
    <>
      {/* Header included in order to hide it when there are not invites yet */}
      <Header title={localize(language, "Membership Invites", "成员邀请")} />
      <DataTableToolbar columns={columns} />
      <DataTable
        tableName={"membershipInvites"}
        columns={columns}
        data={
          invites.isLoading
            ? { isLoading: true, isError: false }
            : invites.isError
              ? {
                  isLoading: false,
                  isError: true,
                  error: invites.error.message,
                }
              : {
                  isLoading: false,
                  isError: false,
                  data: safeExtract(invites.data, "invitations", []).map((i) =>
                    convertToTableRow(i),
                  ),
                }
        }
        pagination={{
          totalCount,
          onChange: setPaginationState,
          state: paginationState,
        }}
      />
    </>
  );
}
