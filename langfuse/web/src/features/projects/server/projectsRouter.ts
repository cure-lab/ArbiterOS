import {
  createTRPCRouter,
  protectedOrganizationProcedure,
  protectedProjectProcedure,
} from "@/src/server/api/trpc";
import * as z from "zod/v4";
import { throwIfNoProjectAccess } from "@/src/features/rbac/utils/checkProjectAccess";
import { TRPCError } from "@trpc/server";
import { projectNameSchema } from "@/src/features/auth/lib/projectNameSchema";
import { auditLog } from "@/src/features/audit-logs/auditLog";
import { throwIfNoOrganizationAccess } from "@/src/features/rbac/utils/checkOrganizationAccess";
import { ApiAuthService } from "@/src/features/public-api/server/apiAuth";
import {
  ExperienceSummaryJsonSchema,
  ExperienceSummaryMarkdownOutputModeSchema,
} from "@/src/features/experience-summary/types";
import { mkdir, readFile, writeFile } from "fs/promises";
import {
  QueueJobs,
  redis,
  ProjectDeleteQueue,
  getEnvironmentsForProject,
  logger,
} from "@langfuse/shared/src/server";
import { StringNoHTMLNonEmpty } from "@langfuse/shared";
import {
  buildExperienceSummaryHintSectionContent,
  removeProjectHintSectionFromMarkdown,
  upsertProjectHintSectionInMarkdown,
} from "@langfuse/shared/src/utils/experienceSummaryHintMarkdown";
import { rewriteAbsolutePathFromPrefixMappings } from "@/src/features/file-paths/server/absolutePathPrefixMap";
import { randomUUID } from "crypto";
import { dirname, isAbsolute } from "path";

const AutoErrorAnalysisModelSchema = z.enum(["gpt-5.2", "gpt-4.1"]);
const ProjectAutoErrorAnalysisSettingsSchema = z.object({
  enabled: z.boolean(),
  model: AutoErrorAnalysisModelSchema,
  minNewErrorNodesForSummary: z.number().int().min(1).nullable().default(null),
  policyRejectHighlightThresholdPct: z
    .number()
    .int()
    .min(1)
    .max(100)
    .default(70),
  summaryAppendMarkdownAbsolutePath: z
    .string()
    .trim()
    .min(1)
    .nullable()
    .default(null),
  summaryMarkdownOutputMode:
    ExperienceSummaryMarkdownOutputModeSchema.default("prompt_pack_only"),
});
type ProjectAutoErrorAnalysisSettings = z.infer<
  typeof ProjectAutoErrorAnalysisSettingsSchema
>;

const ProjectPolicyGovernanceSettingsSchema = z.object({
  kernelPolicyPathAbsolute: z.string().trim().min(1).nullable().default(null),
  lastPolicyUpdatedAt: z.string().trim().min(1).nullable().default(null),
  beginnerSummaries: z.record(z.string(), z.string()).default({}),
  policyConfirmationResetTimestamps: z
    .record(z.string(), z.string().trim().min(1))
    .default({}),
});
type ProjectPolicyGovernanceSettings = z.infer<
  typeof ProjectPolicyGovernanceSettingsSchema
>;

const DEFAULT_AUTO_ERROR_ANALYSIS_SETTINGS: ProjectAutoErrorAnalysisSettings = {
  enabled: false,
  model: "gpt-5.2",
  minNewErrorNodesForSummary: null,
  policyRejectHighlightThresholdPct: 70,
  summaryAppendMarkdownAbsolutePath: null,
  summaryMarkdownOutputMode: "prompt_pack_only",
};

const DEFAULT_POLICY_GOVERNANCE_SETTINGS: ProjectPolicyGovernanceSettings = {
  kernelPolicyPathAbsolute: null,
  lastPolicyUpdatedAt: null,
  beginnerSummaries: {},
  policyConfirmationResetTimestamps: {},
};

function parseAutoErrorAnalysisSettings(
  metadata: unknown,
): ProjectAutoErrorAnalysisSettings {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return DEFAULT_AUTO_ERROR_ANALYSIS_SETTINGS;
  }

  const maybeConfig = (metadata as Record<string, unknown>).autoErrorAnalysis;
  const parsed = ProjectAutoErrorAnalysisSettingsSchema.safeParse(maybeConfig);
  if (!parsed.success) return DEFAULT_AUTO_ERROR_ANALYSIS_SETTINGS;
  return parsed.data;
}

function mergeAutoErrorAnalysisSettingsIntoMetadata(params: {
  metadata: unknown;
  settings: ProjectAutoErrorAnalysisSettings;
}) {
  const metadata =
    params.metadata &&
    typeof params.metadata === "object" &&
    !Array.isArray(params.metadata)
      ? (params.metadata as Record<string, unknown>)
      : {};

  return {
    ...metadata,
    autoErrorAnalysis: params.settings,
  };
}

function parsePolicyGovernanceSettings(
  metadata: unknown,
): ProjectPolicyGovernanceSettings {
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return DEFAULT_POLICY_GOVERNANCE_SETTINGS;
  }

  const maybeConfig = (metadata as Record<string, unknown>).policyGovernance;
  const parsed = ProjectPolicyGovernanceSettingsSchema.safeParse(maybeConfig);
  if (!parsed.success) return DEFAULT_POLICY_GOVERNANCE_SETTINGS;
  return parsed.data;
}

function mergePolicyGovernanceSettingsIntoMetadata(params: {
  metadata: unknown;
  settings: ProjectPolicyGovernanceSettings;
}) {
  const metadata =
    params.metadata &&
    typeof params.metadata === "object" &&
    !Array.isArray(params.metadata)
      ? (params.metadata as Record<string, unknown>)
      : {};

  return {
    ...metadata,
    policyGovernance: params.settings,
  };
}

export const projectsRouter = createTRPCRouter({
  create: protectedOrganizationProcedure
    .input(
      z.object({
        name: StringNoHTMLNonEmpty,
        orgId: z.string(),
      }),
    )
    .mutation(async ({ input, ctx }) => {
      throwIfNoOrganizationAccess({
        session: ctx.session,
        organizationId: input.orgId,
        scope: "projects:create",
      });

      const existingProject = await ctx.prisma.project.findFirst({
        where: {
          name: input.name,
          orgId: input.orgId,
          deletedAt: null,
        },
      });

      if (existingProject) {
        throw new TRPCError({
          code: "CONFLICT",
          message:
            "A project with this name already exists in your organization",
        });
      }

      const project = await ctx.prisma.project.create({
        data: {
          name: input.name,
          orgId: input.orgId,
        },
      });
      await auditLog({
        session: ctx.session,
        resourceType: "project",
        resourceId: project.id,
        action: "create",
        after: project,
      });

      return {
        id: project.id,
        name: project.name,
        role: "OWNER",
      };
    }),

  update: protectedProjectProcedure
    .input(
      z.object({
        projectId: z.string(),
        newName: projectNameSchema.shape.name,
      }),
    )
    .mutation(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:update",
      });

      // check if the project name is already taken by another project
      const otherProjectWithSameName = await ctx.prisma.project.findFirst({
        where: {
          name: input.newName,
          orgId: ctx.session.orgId,
          deletedAt: null,
          id: {
            not: input.projectId,
          },
        },
      });
      if (otherProjectWithSameName) {
        throw new TRPCError({
          code: "CONFLICT",
          message:
            "A project with this name already exists in your organization",
        });
      }

      const project = await ctx.prisma.project.update({
        where: {
          id: input.projectId,
          orgId: ctx.session.orgId,
        },
        data: {
          name: input.newName,
        },
      });
      await auditLog({
        session: ctx.session,
        resourceType: "project",
        resourceId: input.projectId,
        action: "update",
        after: project,
      });
      return true;
    }),

  setRetention: protectedProjectProcedure
    .input(
      z.object({
        projectId: z.string(),
        retention: z.number().int().gte(3).nullable(),
      }),
    )
    .mutation(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:update",
      });

      const project = await ctx.prisma.project.update({
        where: {
          id: input.projectId,
          orgId: ctx.session.orgId,
        },
        data: {
          retentionDays: input.retention,
        },
      });
      await auditLog({
        session: ctx.session,
        resourceType: "project",
        resourceId: input.projectId,
        action: "update",
        after: project,
      });
      return true;
    }),

  getPolicyGovernanceSettings: protectedProjectProcedure
    .input(
      z.object({
        projectId: z.string(),
      }),
    )
    .output(ProjectPolicyGovernanceSettingsSchema)
    .query(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:read",
      });

      const project = await ctx.prisma.project.findUnique({
        where: {
          id: input.projectId,
          orgId: ctx.session.orgId,
        },
        select: {
          metadata: true,
        },
      });

      if (!project) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Project not found",
        });
      }

      return parsePolicyGovernanceSettings(project.metadata);
    }),

  setPolicyGovernanceSettings: protectedProjectProcedure
    .input(
      z.object({
        projectId: z.string(),
        kernelPolicyPathAbsolute: z
          .string()
          .trim()
          .min(1)
          .nullable()
          .optional()
          .default(null),
      }),
    )
    .output(ProjectPolicyGovernanceSettingsSchema)
    .mutation(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:update",
      });

      const existingProject = await ctx.prisma.project.findUnique({
        where: {
          id: input.projectId,
          orgId: ctx.session.orgId,
        },
        select: {
          metadata: true,
        },
      });

      if (!existingProject) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Project not found",
        });
      }

      if (
        input.kernelPolicyPathAbsolute !== null &&
        !isAbsolute(input.kernelPolicyPathAbsolute)
      ) {
        throw new TRPCError({
          code: "BAD_REQUEST",
          message: "Kernel policy path must be an absolute path.",
        });
      }

      const settings: ProjectPolicyGovernanceSettings = {
        kernelPolicyPathAbsolute: input.kernelPolicyPathAbsolute ?? null,
        lastPolicyUpdatedAt: parsePolicyGovernanceSettings(
          existingProject.metadata,
        ).lastPolicyUpdatedAt,
        beginnerSummaries: parsePolicyGovernanceSettings(
          existingProject.metadata,
        ).beginnerSummaries,
        policyConfirmationResetTimestamps: parsePolicyGovernanceSettings(
          existingProject.metadata,
        ).policyConfirmationResetTimestamps,
      };
      const mergedMetadata = mergePolicyGovernanceSettingsIntoMetadata({
        metadata: existingProject.metadata,
        settings,
      });

      await ctx.prisma.project.update({
        where: {
          id: input.projectId,
          orgId: ctx.session.orgId,
        },
        data: {
          metadata: mergedMetadata as any,
        },
      });

      await auditLog({
        session: ctx.session,
        resourceType: "project",
        resourceId: input.projectId,
        action: "update",
        after: {
          policyGovernance: settings,
        },
      });

      return settings;
    }),

  resetPolicyConfirmationStats: protectedProjectProcedure
    .input(
      z.object({
        projectId: z.string(),
        policyNames: z.array(z.string().trim().min(1)).min(1),
      }),
    )
    .output(ProjectPolicyGovernanceSettingsSchema)
    .mutation(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:update",
      });

      const existingProject = await ctx.prisma.project.findUnique({
        where: {
          id: input.projectId,
          orgId: ctx.session.orgId,
        },
        select: {
          metadata: true,
        },
      });

      if (!existingProject) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Project not found",
        });
      }

      const existingSettings = parsePolicyGovernanceSettings(
        existingProject.metadata,
      );
      const resetAt = new Date().toISOString();
      const policyNames = Array.from(
        new Set(input.policyNames.map((name) => name.trim()).filter(Boolean)),
      );

      const settings: ProjectPolicyGovernanceSettings = {
        ...existingSettings,
        policyConfirmationResetTimestamps: {
          ...existingSettings.policyConfirmationResetTimestamps,
          ...Object.fromEntries(
            policyNames.map((policyName) => [policyName, resetAt] as const),
          ),
        },
      };
      const mergedMetadata = mergePolicyGovernanceSettingsIntoMetadata({
        metadata: existingProject.metadata,
        settings,
      });

      await ctx.prisma.project.update({
        where: {
          id: input.projectId,
          orgId: ctx.session.orgId,
        },
        data: {
          metadata: mergedMetadata as any,
        },
      });

      await auditLog({
        session: ctx.session,
        resourceType: "project",
        resourceId: input.projectId,
        action: "update",
        after: {
          policyGovernance: settings,
          resetPolicyConfirmationStats: {
            policyNames,
            resetAt,
          },
        },
      });

      return settings;
    }),

  getErrorAnalysisSettings: protectedProjectProcedure
    .input(
      z.object({
        projectId: z.string(),
      }),
    )
    .output(ProjectAutoErrorAnalysisSettingsSchema)
    .query(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:read",
      });

      const project = await ctx.prisma.project.findUnique({
        where: {
          id: input.projectId,
          orgId: ctx.session.orgId,
        },
        select: {
          metadata: true,
        },
      });

      if (!project) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Project not found",
        });
      }

      return parseAutoErrorAnalysisSettings(project.metadata);
    }),

  setErrorAnalysisSettings: protectedProjectProcedure
    .input(
      z.object({
        projectId: z.string(),
        enabled: z.boolean(),
        model: AutoErrorAnalysisModelSchema,
        minNewErrorNodesForSummary: z
          .number()
          .int()
          .min(1)
          .nullable()
          .optional()
          .default(null),
        policyRejectHighlightThresholdPct: z
          .number()
          .int()
          .min(1)
          .max(100)
          .optional()
          .default(70),
        summaryAppendMarkdownAbsolutePath: z
          .string()
          .trim()
          .min(1)
          .nullable()
          .optional()
          .default(null),
        summaryMarkdownOutputMode:
          ExperienceSummaryMarkdownOutputModeSchema.optional().default(
            "prompt_pack_only",
          ),
      }),
    )
    .output(ProjectAutoErrorAnalysisSettingsSchema)
    .mutation(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: input.projectId,
        scope: "project:update",
      });

      const existingProject = await ctx.prisma.project.findUnique({
        where: {
          id: input.projectId,
          orgId: ctx.session.orgId,
        },
        select: {
          metadata: true,
        },
      });

      if (!existingProject) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Project not found",
        });
      }

      if (
        input.summaryAppendMarkdownAbsolutePath !== null &&
        !isAbsolute(input.summaryAppendMarkdownAbsolutePath)
      ) {
        throw new TRPCError({
          code: "BAD_REQUEST",
          message: "Summary markdown path must be an absolute path.",
        });
      }

      if (
        input.summaryAppendMarkdownAbsolutePath !== null &&
        !input.summaryAppendMarkdownAbsolutePath.toLowerCase().endsWith(".md")
      ) {
        throw new TRPCError({
          code: "BAD_REQUEST",
          message: "Summary markdown path must point to a .md file.",
        });
      }

      const previousSettings = parseAutoErrorAnalysisSettings(
        existingProject.metadata,
      );

      const settings: ProjectAutoErrorAnalysisSettings = {
        enabled: input.enabled,
        model: input.model,
        minNewErrorNodesForSummary: input.minNewErrorNodesForSummary ?? null,
        policyRejectHighlightThresholdPct:
          input.policyRejectHighlightThresholdPct,
        summaryAppendMarkdownAbsolutePath:
          input.summaryAppendMarkdownAbsolutePath ?? null,
        summaryMarkdownOutputMode: input.summaryMarkdownOutputMode,
      };

      const mergedMetadata = mergeAutoErrorAnalysisSettingsIntoMetadata({
        metadata: existingProject.metadata,
        settings,
      });

      const project = await ctx.prisma.project.update({
        where: {
          id: input.projectId,
          orgId: ctx.session.orgId,
        },
        data: {
          metadata: mergedMetadata as any,
        },
      });

      await auditLog({
        session: ctx.session,
        resourceType: "project",
        resourceId: input.projectId,
        action: "update",
        after: {
          autoErrorAnalysis: settings,
        },
      });

      // Best-effort: keep the configured markdown hint section in sync with the
      // project's tracing/auto-analysis toggle.
      const maybePreviousPath =
        previousSettings.summaryAppendMarkdownAbsolutePath;
      const maybeNextPath = settings.summaryAppendMarkdownAbsolutePath;
      const nextEnabled = settings.enabled === true;
      const pathChanged = maybePreviousPath !== maybeNextPath;

      const removeHintSectionInFile = async (absolutePath: string) => {
        const resolvedAbsolutePath =
          rewriteAbsolutePathFromPrefixMappings(absolutePath);
        let existingContent = "";
        try {
          existingContent = await readFile(resolvedAbsolutePath, "utf8");
        } catch (error) {
          const code = (error as NodeJS.ErrnoException).code;
          if (code === "ENOENT") return;
          throw error;
        }

        const removed = removeProjectHintSectionFromMarkdown({
          markdown: existingContent,
          projectId: input.projectId,
        });
        if (!removed.removed) return;

        await mkdir(dirname(resolvedAbsolutePath), { recursive: true });
        await writeFile(
          resolvedAbsolutePath,
          `${removed.markdown.replace(/\s*$/, "")}\n`,
          "utf8",
        );
      };

      const upsertHintSectionInFile = async (params: {
        absolutePath: string;
        summary: z.infer<typeof ExperienceSummaryJsonSchema>;
      }) => {
        const resolvedAbsolutePath = rewriteAbsolutePathFromPrefixMappings(
          params.absolutePath,
        );
        await mkdir(dirname(resolvedAbsolutePath), { recursive: true });

        let existingContent = "";
        try {
          existingContent = await readFile(resolvedAbsolutePath, "utf8");
        } catch (error) {
          const code = (error as NodeJS.ErrnoException).code;
          if (code !== "ENOENT") throw error;
        }

        const replacementContent = buildExperienceSummaryHintSectionContent({
          summary: params.summary,
          outputMode: settings.summaryMarkdownOutputMode,
          sectionHeadingHashCount: 4,
        });
        const updatedMarkdown = upsertProjectHintSectionInMarkdown({
          markdown: existingContent,
          projectId: input.projectId,
          replacementContent,
          defaultHeadingHashCount: 2,
        });
        await writeFile(
          resolvedAbsolutePath,
          `${updatedMarkdown.replace(/\s*$/, "")}\n`,
          "utf8",
        );
      };

      try {
        // If disabled, ensure no hint/summary is left behind in the configured markdown file(s).
        if (!nextEnabled) {
          for (const p of [maybePreviousPath, maybeNextPath]) {
            if (p) await removeHintSectionInFile(p);
          }
        } else {
          // If the target file changed, remove the old hint section to avoid stale content.
          if (pathChanged && maybePreviousPath) {
            await removeHintSectionInFile(maybePreviousPath);
          }

          if (maybeNextPath) {
            const row = await ctx.prisma.experienceSummary.findUnique({
              where: { projectId: input.projectId },
              select: { summary: true },
            });
            const parsed = ExperienceSummaryJsonSchema.safeParse(
              row?.summary ?? null,
            );
            if (parsed.success) {
              await upsertHintSectionInFile({
                absolutePath: maybeNextPath,
                summary: parsed.data,
              });
            } else {
              // No valid summary yet: clear existing hint section if present (keeps markdown clean).
              await removeHintSectionInFile(maybeNextPath);
              if (row?.summary != null) {
                logger.warn(
                  "Project experience summary failed schema validation; skipping markdown insert",
                  {
                    projectId: input.projectId,
                    path: maybeNextPath,
                    error: parsed.error.message,
                  },
                );
              }
            }
          }
        }
      } catch (error) {
        logger.warn("Failed to sync hint section in markdown file", {
          projectId: input.projectId,
          previousPath: maybePreviousPath,
          nextPath: maybeNextPath,
          error: error instanceof Error ? error.message : String(error),
        });
      }

      return parseAutoErrorAnalysisSettings(project.metadata);
    }),

  delete: protectedProjectProcedure
    .input(
      z.object({
        projectId: z.string(),
      }),
    )
    .mutation(async ({ input, ctx }) => {
      throwIfNoProjectAccess({
        session: ctx.session,
        projectId: ctx.session.projectId,
        scope: "project:delete",
      });

      // API keys need to be deleted from cache. Otherwise, they will still be valid.
      await new ApiAuthService(
        ctx.prisma,
        redis,
      ).invalidateCachedProjectApiKeys(input.projectId);

      // Delete API keys from DB
      await ctx.prisma.apiKey.deleteMany({
        where: {
          projectId: input.projectId,
          scope: "PROJECT",
        },
      });

      const project = await ctx.prisma.project.update({
        where: {
          id: input.projectId,
          orgId: ctx.session.orgId,
        },
        data: {
          deletedAt: new Date(),
        },
      });

      await auditLog({
        session: ctx.session,
        resourceType: "project",
        resourceId: input.projectId,
        before: project,
        action: "delete",
      });

      const projectDeleteQueue = ProjectDeleteQueue.getInstance();
      if (!projectDeleteQueue) {
        throw new TRPCError({
          code: "INTERNAL_SERVER_ERROR",
          message:
            "ProjectDeleteQueue is not available. Please try again later.",
        });
      }

      await projectDeleteQueue.add(QueueJobs.ProjectDelete, {
        timestamp: new Date(),
        id: randomUUID(),
        payload: {
          projectId: input.projectId,
          orgId: ctx.session.orgId,
        },
        name: QueueJobs.ProjectDelete,
      });

      return true;
    }),

  transfer: protectedProjectProcedure
    .input(
      z.object({
        projectId: z.string(),
        targetOrgId: z.string(),
      }),
    )
    .mutation(async ({ input, ctx }) => {
      // source org
      throwIfNoOrganizationAccess({
        session: ctx.session,
        organizationId: ctx.session.orgId,
        scope: "projects:transfer_org",
      });
      // destination org
      throwIfNoOrganizationAccess({
        session: ctx.session,
        organizationId: input.targetOrgId,
        scope: "projects:transfer_org",
      });

      const project = await ctx.prisma.project.findUnique({
        where: {
          id: input.projectId,
          deletedAt: null,
        },
      });
      if (!project) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Project not found",
        });
      }

      await auditLog({
        session: ctx.session,
        resourceType: "project",
        resourceId: input.projectId,
        action: "transfer",
        before: { orgId: ctx.session.orgId },
        after: { orgId: input.targetOrgId },
      });

      await ctx.prisma.$transaction([
        ctx.prisma.projectMembership.deleteMany({
          where: {
            projectId: input.projectId,
          },
        }),
        ctx.prisma.project.update({
          where: {
            id: input.projectId,
            orgId: ctx.session.orgId,
          },
          data: {
            orgId: input.targetOrgId,
          },
        }),
      ]);

      // API keys need to be deleted from cache. Otherwise, they will still be valid.
      // It has to be called after the db is done to prevent new API keys from being cached.
      await new ApiAuthService(
        ctx.prisma,
        redis,
      ).invalidateCachedProjectApiKeys(input.projectId);
    }),

  environmentFilterOptions: protectedProjectProcedure
    .input(
      z.object({ projectId: z.string(), fromTimestamp: z.date().optional() }),
    )
    .query(async ({ input }) => getEnvironmentsForProject(input)),
});
