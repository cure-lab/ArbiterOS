/** @jest-environment node */

import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const mockFetchLLMCompletion = jest.fn();

jest.mock("@langfuse/shared/src/server", () => {
  const originalModule = jest.requireActual("@langfuse/shared/src/server");
  return {
    ...originalModule,
    fetchLLMCompletion: (...args: unknown[]) => mockFetchLLMCompletion(...args),
    logger: {
      ...originalModule.logger,
      info: jest.fn(),
      warn: jest.fn(),
      error: jest.fn(),
      debug: jest.fn(),
    },
  };
});

import type { Session } from "next-auth";
import { pruneDatabase } from "@/src/__tests__/test-utils";
import { prisma } from "@langfuse/shared/src/db";
import { appRouter } from "@/src/server/api/root";
import { createInnerTRPCContext } from "@/src/server/api/trpc";
import { LLMAdapter } from "@langfuse/shared";

describe("experienceSummary.generate RPC", () => {
  const projectId = "7a88fb47-b4e2-43b8-a06c-a5ce950dc53a";
  const originalPathPrefixMap = process.env.LANGFUSE_PATH_PREFIX_MAP;
  const originalLegacyPolicyPathPrefixMap =
    process.env.LANGFUSE_POLICY_PATH_PREFIX_MAP;

  const session: Session = {
    expires: "1",
    user: {
      id: "user-1",
      name: "Demo User",
      canCreateOrganizations: true,
      organizations: [
        {
          id: "seed-org-id",
          role: "OWNER",
          plan: "cloud:hobby",
          cloudConfig: undefined,
          name: "Test Organization",
          metadata: {},
          projects: [
            {
              id: projectId,
              role: "ADMIN",
              name: "Test Project",
              deletedAt: null,
              retentionDays: null,
              metadata: {},
            },
          ],
        },
      ],
      featureFlags: {
        templateFlag: true,
        excludeClickhouseRead: false,
      },
      admin: true,
    },
    environment: {} as any,
  };

  const ctx = createInnerTRPCContext({ session, headers: {} });
  const caller = appRouter.createCaller({ ...ctx, prisma });

  beforeEach(async () => {
    mockFetchLLMCompletion.mockReset();
    delete process.env.LANGFUSE_PATH_PREFIX_MAP;
    delete process.env.LANGFUSE_POLICY_PATH_PREFIX_MAP;
    await pruneDatabase();
    await prisma.errorAnalysis.deleteMany();
    await prisma.experienceSummary.deleteMany();
  });

  afterEach(() => {
    if (originalPathPrefixMap === undefined) {
      delete process.env.LANGFUSE_PATH_PREFIX_MAP;
    } else {
      process.env.LANGFUSE_PATH_PREFIX_MAP = originalPathPrefixMap;
    }

    if (originalLegacyPolicyPathPrefixMap === undefined) {
      delete process.env.LANGFUSE_POLICY_PATH_PREFIX_MAP;
    } else {
      process.env.LANGFUSE_POLICY_PATH_PREFIX_MAP =
        originalLegacyPolicyPathPrefixMap;
    }
  });

  it("should reject when no OpenAI LLM connection exists", async () => {
    await prisma.errorAnalysis.create({
      data: {
        projectId,
        traceId: "trace-1",
        observationId: "obs-1",
        updatedAt: new Date(Date.now() - 60_000),
        model: "gpt-5.2",
        rootCause: "root cause",
        resolveNow: ["step 1"],
        preventionNextCall: ["add a schema validator"],
        relevantObservations: ["obs-1"],
        contextSufficient: true,
        confidence: 0.9,
        errorType: "schema_mismatch",
        errorTypeDescription: "schema mismatch",
        errorTypeWhy: "output did not match schema",
        errorTypeConfidence: 0.8,
        errorTypeFromList: true,
      },
    });

    await expect(
      caller.experienceSummary.generate({
        projectId,
        mode: "full",
        model: "gpt-5.2",
        maxItems: 50,
      }),
    ).rejects.toThrow(/No OpenAI-adapter LLM connection configured/i);
  });

  it("should no-op for incremental update when there are no new ErrorAnalysis rows", async () => {
    await prisma.llmApiKeys.create({
      data: {
        projectId,
        provider: "openai",
        adapter: LLMAdapter.OpenAI,
        displaySecretKey: "...test",
        secretKey: "test-secret",
        baseURL: null,
        customModels: [],
        withDefaultModels: true,
        extraHeaders: null,
        extraHeaderKeys: [],
        config: null,
      },
    });

    await prisma.errorAnalysis.create({
      data: {
        projectId,
        traceId: "trace-1",
        observationId: "obs-1",
        model: "gpt-5.2",
        rootCause: "root cause",
        resolveNow: ["step 1"],
        preventionNextCall: ["add a schema validator"],
        relevantObservations: ["obs-1"],
        contextSufficient: true,
        confidence: 0.9,
        errorType: "schema_mismatch",
        errorTypeDescription: "schema mismatch",
        errorTypeWhy: "output did not match schema",
        errorTypeConfidence: 0.8,
        errorTypeFromList: true,
      },
    });

    mockFetchLLMCompletion.mockResolvedValueOnce({
      schemaVersion: 1,
      experiences: [
        {
          key: "schema_mismatch",
          when: "When structured output fails schema validation.",
          possibleProblems: ["The pipeline rejects invalid JSON/shape."],
          avoidanceAndNotes: [
            "Use strict schemas and validate before execution.",
          ],
          promptAdditions: ["Return ONLY valid JSON that matches the schema."],
          relatedErrorTypes: ["schema_mismatch"],
        },
      ],
      promptPack: {
        title: "Experience guardrails",
        lines: ["Return ONLY valid JSON matching the schema."],
      },
    });

    const first = await caller.experienceSummary.generate({
      projectId,
      mode: "full",
      model: "gpt-5.2",
      maxItems: 50,
    });

    expect(first.updated).toBe(true);
    expect(first.row?.summary.schemaVersion).toBe(1);

    const callsAfterFirst = mockFetchLLMCompletion.mock.calls.length;

    const second = await caller.experienceSummary.generate({
      projectId,
      mode: "incremental",
      model: "gpt-5.2",
      maxItems: 50,
    });

    expect(second.updated).toBe(false);
    expect(second.row?.summary.schemaVersion).toBe(1);
    expect(mockFetchLLMCompletion.mock.calls.length).toBe(callsAfterFirst);
  });

  it("should return new auto error analysis setting defaults", async () => {
    const settings = await caller.projects.getErrorAnalysisSettings({
      projectId,
    });
    expect(settings.minNewErrorNodesForSummary).toBe(1);
    expect(settings.summaryMarkdownOutputMode).toBe("prompt_pack_only");
  });

  it("should send compact input for incremental updates and merge with existing summary", async () => {
    await prisma.llmApiKeys.create({
      data: {
        projectId,
        provider: "openai",
        adapter: LLMAdapter.OpenAI,
        displaySecretKey: "...test",
        secretKey: "test-secret",
        baseURL: null,
        customModels: [],
        withDefaultModels: true,
        extraHeaders: null,
        extraHeaderKeys: [],
        config: null,
      },
    });

    await prisma.errorAnalysis.create({
      data: {
        projectId,
        traceId: "trace-1",
        observationId: "obs-1",
        model: "gpt-5.2",
        rootCause: "root cause",
        resolveNow: ["step 1"],
        preventionNextCall: ["add a schema validator"],
        relevantObservations: ["obs-1"],
        contextSufficient: true,
        confidence: 0.9,
        errorType: "schema_mismatch",
        errorTypeDescription: "schema mismatch",
        errorTypeWhy: "output did not match schema",
        errorTypeConfidence: 0.8,
        errorTypeFromList: true,
      },
    });

    mockFetchLLMCompletion.mockResolvedValueOnce({
      schemaVersion: 1,
      experiences: [
        {
          key: "schema_mismatch",
          when: "When structured output fails schema validation.",
          possibleProblems: ["The pipeline rejects invalid JSON/shape."],
          avoidanceAndNotes: ["Validate and tighten constraints."],
          promptAdditions: ["Return ONLY valid JSON that matches the schema."],
          relatedErrorTypes: ["schema_mismatch"],
        },
      ],
      promptPack: {
        title: "Experience guardrails",
        lines: ["Return ONLY valid JSON matching the schema."],
      },
    });

    const first = await caller.experienceSummary.generate({
      projectId,
      mode: "full",
      model: "gpt-5.2",
      maxItems: 50,
    });
    expect(first.updated).toBe(true);

    await prisma.errorAnalysis.create({
      data: {
        projectId,
        traceId: "trace-2",
        observationId: "obs-2",
        updatedAt: new Date(Date.now() + 60_000),
        model: "gpt-5.2",
        rootCause: "another cause",
        resolveNow: ["step a"],
        preventionNextCall: ["add a timeout guardrail"],
        relevantObservations: ["obs-2"],
        contextSufficient: true,
        confidence: 0.7,
        errorType: "rate_limit",
        errorTypeDescription: "rate limit",
        errorTypeWhy: "too many requests",
        errorTypeConfidence: 0.7,
        errorTypeFromList: true,
      },
    });

    // DELTA: only provide the new item; server should merge with stored summary.
    mockFetchLLMCompletion.mockResolvedValueOnce({
      schemaVersion: 1,
      experiences: [
        {
          key: "rate_limit",
          when: "When the provider rate-limits requests.",
          possibleProblems: ["Calls fail with rate limit errors."],
          avoidanceAndNotes: ["Reduce request volume per call."],
          promptAdditions: ["Be concise to reduce token usage."],
          relatedErrorTypes: ["rate_limit"],
        },
      ],
      promptPack: {
        title: "Experience guardrails",
        lines: ["Be concise to reduce token usage."],
      },
    });

    const second = await caller.experienceSummary.generate({
      projectId,
      mode: "incremental",
      model: "gpt-5.2",
      maxItems: 50,
    });

    expect(second.updated).toBe(true);
    const keys = (second.row?.summary.experiences ?? []).map((e) => e.key);
    expect(keys).toContain("schema_mismatch");
    expect(keys).toContain("rate_limit");

    const secondCall = mockFetchLLMCompletion.mock.calls[1]?.[0] as any;
    const userContent = secondCall?.messages?.[1]?.content as string;
    const payload = JSON.parse(userContent);
    expect(payload.previousSummary).toBeUndefined();
    expect(payload.existingSummaryKeys).toContain("schema_mismatch");
    expect(payload.existingPromptPack?.title).toBe("Experience guardrails");
  });

  it("should fail when LLM returns invalid structured output", async () => {
    await prisma.llmApiKeys.create({
      data: {
        projectId,
        provider: "openai",
        adapter: LLMAdapter.OpenAI,
        displaySecretKey: "...test",
        secretKey: "test-secret",
        baseURL: null,
        customModels: [],
        withDefaultModels: true,
        extraHeaders: null,
        extraHeaderKeys: [],
        config: null,
      },
    });

    await prisma.errorAnalysis.create({
      data: {
        projectId,
        traceId: "trace-1",
        observationId: "obs-1",
        model: "gpt-5.2",
        rootCause: "root cause",
        resolveNow: ["step 1"],
        preventionNextCall: ["add a schema validator"],
        relevantObservations: ["obs-1"],
        contextSufficient: true,
        confidence: 0.9,
      },
    });

    // Missing required keys -> should be rejected by schema validation
    mockFetchLLMCompletion.mockResolvedValueOnce({
      schemaVersion: 1,
      experiences: [],
    });

    await expect(
      caller.experienceSummary.generate({
        projectId,
        mode: "full",
        model: "gpt-5.2",
        maxItems: 50,
      }),
    ).rejects.toThrow(/invalid summary payload/i);
  });

  it("should replace (not append) the # HINT section in the configured markdown file", async () => {
    await prisma.llmApiKeys.create({
      data: {
        projectId,
        provider: "openai",
        adapter: LLMAdapter.OpenAI,
        displaySecretKey: "...test",
        secretKey: "test-secret",
        baseURL: null,
        customModels: [],
        withDefaultModels: true,
        extraHeaders: null,
        extraHeaderKeys: [],
        config: null,
      },
    });

    await prisma.errorAnalysis.create({
      data: {
        projectId,
        traceId: "trace-1",
        observationId: "obs-1",
        model: "gpt-5.2",
        rootCause: "root cause",
        resolveNow: ["step 1"],
        preventionNextCall: ["add a schema validator"],
        relevantObservations: ["obs-1"],
        contextSufficient: true,
        confidence: 0.9,
      },
    });

    const dir = await mkdtemp(join(tmpdir(), "langfuse-experience-summary-"));
    const summaryPath = join(dir, "summary.md");
    await writeFile(
      summaryPath,
      [
        "# Intro",
        "",
        "Some content",
        "",
        "# HINT",
        "",
        "old hint 1",
        "",
        "# HINT",
        "",
        "old hint 2",
        "",
        "# HINT",
        "",
        "old hint 3",
        "",
        "# After",
        "",
        "Keep me",
        "",
      ].join("\n"),
      "utf8",
    );

    await prisma.project.update({
      where: { id: projectId },
      data: {
        metadata: {
          autoErrorAnalysis: {
            enabled: true,
            model: "gpt-5.2",
            minNewErrorNodesForSummary: 1,
            summaryAppendMarkdownAbsolutePath: summaryPath,
          },
        } as any,
      },
    });

    mockFetchLLMCompletion.mockResolvedValueOnce({
      schemaVersion: 1,
      experiences: [],
      promptPack: {
        title: "Pack v1",
        lines: ["Line v1"],
      },
    });

    await caller.experienceSummary.generate({
      projectId,
      mode: "full",
      model: "gpt-5.2",
      maxItems: 50,
    });

    const firstMarkdown = await readFile(summaryPath, "utf8");
    expect((firstMarkdown.match(/^#\s*HINT\s*$/gim) ?? []).length).toBe(1);
    expect(firstMarkdown).toContain("Pack v1");
    expect(firstMarkdown).not.toContain("old hint 1");
    expect(firstMarkdown).not.toContain("old hint 2");
    expect(firstMarkdown).not.toContain("old hint 3");
    expect(firstMarkdown).toContain("# After");
    expect(firstMarkdown).toContain("Keep me");

    mockFetchLLMCompletion.mockResolvedValueOnce({
      schemaVersion: 1,
      experiences: [],
      promptPack: {
        title: "Pack v2",
        lines: ["Line v2"],
      },
    });

    await caller.experienceSummary.generate({
      projectId,
      mode: "full",
      model: "gpt-5.2",
      maxItems: 50,
    });

    const secondMarkdown = await readFile(summaryPath, "utf8");
    expect((secondMarkdown.match(/^#\s*HINT\s*$/gim) ?? []).length).toBe(1);
    expect(secondMarkdown).toContain("Pack v2");
    expect(secondMarkdown).not.toContain("Pack v1");
  });

  it("should remove hint section on disable and insert project hint on enable (## hint compatible)", async () => {
    const dir = await mkdtemp(join(tmpdir(), "langfuse-experience-summary-"));
    const summaryPath = join(dir, "summary.md");

    await writeFile(
      summaryPath,
      [
        "# Intro",
        "",
        "Some content",
        "",
        "## hint",
        "",
        "stale hint",
        "",
        "# After",
        "",
        "Keep me",
        "",
      ].join("\n"),
      "utf8",
    );

    await prisma.project.update({
      where: { id: projectId },
      data: {
        metadata: {
          autoErrorAnalysis: {
            enabled: true,
            model: "gpt-5.2",
            minNewErrorNodesForSummary: 1,
            summaryAppendMarkdownAbsolutePath: summaryPath,
          },
        } as any,
      },
    });

    await caller.projects.setErrorAnalysisSettings({
      projectId,
      enabled: false,
      model: "gpt-5.2",
      minNewErrorNodesForSummary: 1,
      summaryAppendMarkdownAbsolutePath: summaryPath,
    });

    const afterDisable = await readFile(summaryPath, "utf8");
    expect(afterDisable).not.toMatch(/^\s*##\s*HINT\s*$/gim);
    expect(afterDisable).not.toContain("stale hint");
    expect(afterDisable).toContain("# After");
    expect(afterDisable).toContain("Keep me");

    // Re-add a hint block; enabling should replace it with this project's saved summary.
    await writeFile(
      summaryPath,
      [
        "# Intro",
        "",
        "Some content",
        "",
        "## hint",
        "",
        "other project's hint",
        "",
        "# After",
        "",
        "Keep me",
        "",
      ].join("\n"),
      "utf8",
    );

    await prisma.experienceSummary.create({
      data: {
        projectId,
        model: "gpt-5.2",
        summary: {
          schemaVersion: 1,
          experiences: [],
          promptPack: {
            title: "Pack from DB",
            lines: ["Line from DB"],
          },
        } as any,
        cursorUpdatedAt: null,
      },
    });

    await caller.projects.setErrorAnalysisSettings({
      projectId,
      enabled: true,
      model: "gpt-5.2",
      minNewErrorNodesForSummary: 1,
      summaryAppendMarkdownAbsolutePath: summaryPath,
    });

    const afterEnable = await readFile(summaryPath, "utf8");
    expect((afterEnable.match(/^\s*##\s*HINT\s*$/gim) ?? []).length).toBe(1);
    expect(afterEnable).toContain("Pack from DB");
    expect(afterEnable).not.toContain("other project's hint");
    expect(afterEnable).toContain("# After");
    expect(afterEnable).toContain("Keep me");
  });

  it("should keep markdown hint sections isolated per project in the same file", async () => {
    const secondProject = await prisma.project.create({
      data: {
        orgId: "seed-org-id",
        name: "Second Summary Project",
      },
    });
    const firstOrg = session.user.organizations[0]!;
    if (!firstOrg.projects.some((p) => p.id === secondProject.id)) {
      firstOrg.projects.push({
        id: secondProject.id,
        role: "ADMIN",
        name: secondProject.name,
        deletedAt: null,
        retentionDays: null,
        metadata: {},
      });
    }

    await prisma.llmApiKeys.createMany({
      data: [
        {
          projectId,
          provider: "openai",
          adapter: LLMAdapter.OpenAI,
          displaySecretKey: "...test",
          secretKey: "test-secret",
          baseURL: null,
          customModels: [],
          withDefaultModels: true,
          extraHeaders: null,
          extraHeaderKeys: [],
          config: null,
        },
        {
          projectId: secondProject.id,
          provider: "openai",
          adapter: LLMAdapter.OpenAI,
          displaySecretKey: "...test-2",
          secretKey: "test-secret-2",
          baseURL: null,
          customModels: [],
          withDefaultModels: true,
          extraHeaders: null,
          extraHeaderKeys: [],
          config: null,
        },
      ],
    });

    await prisma.errorAnalysis.createMany({
      data: [
        {
          projectId,
          traceId: "trace-1",
          observationId: "obs-1",
          model: "gpt-5.2",
          rootCause: "first cause",
          resolveNow: ["first step"],
          preventionNextCall: ["first prevention"],
          relevantObservations: ["obs-1"],
          contextSufficient: true,
          confidence: 0.9,
        },
        {
          projectId: secondProject.id,
          traceId: "trace-2",
          observationId: "obs-2",
          model: "gpt-5.2",
          rootCause: "second cause",
          resolveNow: ["second step"],
          preventionNextCall: ["second prevention"],
          relevantObservations: ["obs-2"],
          contextSufficient: true,
          confidence: 0.8,
        },
      ],
    });

    const dir = await mkdtemp(join(tmpdir(), "langfuse-experience-summary-"));
    const summaryPath = join(dir, "shared-summary.md");
    await writeFile(summaryPath, "# Intro\n\nExisting\n", "utf8");

    await prisma.project.updateMany({
      where: {
        id: {
          in: [projectId, secondProject.id],
        },
      },
      data: {
        metadata: {
          autoErrorAnalysis: {
            enabled: true,
            model: "gpt-5.2",
            minNewErrorNodesForSummary: 1,
            summaryAppendMarkdownAbsolutePath: summaryPath,
            summaryMarkdownOutputMode: "full",
          },
        } as any,
      },
    });

    mockFetchLLMCompletion
      .mockResolvedValueOnce({
        schemaVersion: 1,
        experiences: [],
        promptPack: {
          title: "Pack First",
          lines: ["line-first"],
        },
      })
      .mockResolvedValueOnce({
        schemaVersion: 1,
        experiences: [],
        promptPack: {
          title: "Pack Second",
          lines: ["line-second"],
        },
      });

    await caller.experienceSummary.generate({
      projectId,
      mode: "full",
      model: "gpt-5.2",
      maxItems: 50,
    });
    await caller.experienceSummary.generate({
      projectId: secondProject.id,
      mode: "full",
      model: "gpt-5.2",
      maxItems: 50,
    });

    const markdown = await readFile(summaryPath, "utf8");
    expect((markdown.match(/^\s*##\s*HINT\s*$/gim) ?? []).length).toBe(1);
    expect(markdown).toContain(`Project ${projectId}`);
    expect(markdown).toContain(`Project ${secondProject.id}`);
    expect(markdown).toContain("Pack First");
    expect(markdown).toContain("Pack Second");
  });

  it("should write prompt-pack-only markdown while keeping full summary in DB", async () => {
    await prisma.llmApiKeys.create({
      data: {
        projectId,
        provider: "openai",
        adapter: LLMAdapter.OpenAI,
        displaySecretKey: "...test",
        secretKey: "test-secret",
        baseURL: null,
        customModels: [],
        withDefaultModels: true,
        extraHeaders: null,
        extraHeaderKeys: [],
        config: null,
      },
    });

    await prisma.errorAnalysis.create({
      data: {
        projectId,
        traceId: "trace-1",
        observationId: "obs-1",
        model: "gpt-5.2",
        rootCause: "root cause",
        resolveNow: ["step 1"],
        preventionNextCall: ["prevent 1"],
        relevantObservations: ["obs-1"],
        contextSufficient: true,
        confidence: 0.9,
      },
    });

    const dir = await mkdtemp(join(tmpdir(), "langfuse-experience-summary-"));
    const summaryPath = join(dir, "prompt-only.md");
    await prisma.project.update({
      where: { id: projectId },
      data: {
        metadata: {
          autoErrorAnalysis: {
            enabled: true,
            model: "gpt-5.2",
            minNewErrorNodesForSummary: 1,
            summaryAppendMarkdownAbsolutePath: summaryPath,
            summaryMarkdownOutputMode: "prompt_pack_only",
          },
        } as any,
      },
    });

    mockFetchLLMCompletion.mockResolvedValueOnce({
      schemaVersion: 1,
      experiences: [
        {
          key: "schema_mismatch",
          when: "When output shape drifts",
          keywords: ["schema", "json"],
          possibleProblems: ["Validation errors"],
          avoidanceAndNotes: ["Use explicit schemas"],
          promptAdditions: ["Return strict JSON only."],
          relatedErrorTypes: ["schema_mismatch"],
        },
      ],
      promptPack: {
        title: "Prompt pack",
        lines: ["Return strict JSON only."],
      },
    });

    await caller.experienceSummary.generate({
      projectId,
      mode: "full",
      model: "gpt-5.2",
      maxItems: 50,
    });

    const markdown = await readFile(summaryPath, "utf8");
    expect(markdown).toContain("## Prompt pack");
    expect(markdown).not.toContain("## Experiences");

    const summaryRow = await prisma.experienceSummary.findUnique({
      where: { projectId },
      select: { summary: true },
    });
    const summary = summaryRow?.summary as any;
    expect(Array.isArray(summary?.experiences)).toBe(true);
    expect(summary?.experiences?.length).toBeGreaterThan(0);
  });

  it("should write summary markdown through configured path prefix mappings", async () => {
    await prisma.llmApiKeys.create({
      data: {
        projectId,
        provider: "openai",
        adapter: LLMAdapter.OpenAI,
        displaySecretKey: "...test",
        secretKey: "test-secret",
        baseURL: null,
        customModels: [],
        withDefaultModels: true,
        extraHeaders: null,
        extraHeaderKeys: [],
        config: null,
      },
    });

    await prisma.errorAnalysis.create({
      data: {
        projectId,
        traceId: "trace-mapped",
        observationId: "obs-mapped",
        model: "gpt-5.2",
        rootCause: "mapped root cause",
        resolveNow: ["mapped step"],
        preventionNextCall: ["mapped prevention"],
        relevantObservations: ["obs-mapped"],
        contextSufficient: true,
        confidence: 0.9,
      },
    });

    const dir = await mkdtemp(join(tmpdir(), "langfuse-experience-summary-"));
    const hostRoot = join(dir, "host");
    const mountedRoot = join(dir, "mounted");
    const hostSummaryPath = join(hostRoot, "summary.md");
    const mountedSummaryPath = join(mountedRoot, "summary.md");
    await mkdir(mountedRoot, { recursive: true });
    process.env.LANGFUSE_PATH_PREFIX_MAP = `${hostRoot}=${mountedRoot}`;

    await prisma.project.update({
      where: { id: projectId },
      data: {
        metadata: {
          autoErrorAnalysis: {
            enabled: true,
            model: "gpt-5.2",
            minNewErrorNodesForSummary: 1,
            summaryAppendMarkdownAbsolutePath: hostSummaryPath,
            summaryMarkdownOutputMode: "prompt_pack_only",
          },
        } as any,
      },
    });

    mockFetchLLMCompletion.mockResolvedValueOnce({
      schemaVersion: 1,
      experiences: [],
      promptPack: {
        title: "Mapped prompt pack",
        lines: ["Mapped guardrail"],
      },
    });

    await caller.experienceSummary.generate({
      projectId,
      mode: "full",
      model: "gpt-5.2",
      maxItems: 50,
    });

    const markdown = await readFile(mountedSummaryPath, "utf8");
    expect(markdown).toContain("Mapped prompt pack");
    await expect(readFile(hostSummaryPath, "utf8")).rejects.toThrow();
  });

  it("should backfill prompt pack lines from experience prompt additions", async () => {
    await prisma.llmApiKeys.create({
      data: {
        projectId,
        provider: "openai",
        adapter: LLMAdapter.OpenAI,
        displaySecretKey: "...test",
        secretKey: "test-secret",
        baseURL: null,
        customModels: [],
        withDefaultModels: true,
        extraHeaders: null,
        extraHeaderKeys: [],
        config: null,
      },
    });

    await prisma.errorAnalysis.create({
      data: {
        projectId,
        traceId: "trace-coverage",
        observationId: "obs-coverage",
        model: "gpt-5.2",
        rootCause: "coverage cause",
        resolveNow: ["step 1"],
        preventionNextCall: ["step 2"],
        relevantObservations: ["obs-coverage"],
        contextSufficient: true,
        confidence: 0.9,
      },
    });

    mockFetchLLMCompletion.mockResolvedValueOnce({
      schemaVersion: 1,
      experiences: [
        {
          key: "coverage_case",
          when: "When retrieval falls back to snippets",
          keywords: ["retrieval", "fallback"],
          possibleProblems: ["Important details may be missing from snippets"],
          avoidanceAndNotes: [
            "Prefer reachable alternate sources when possible",
          ],
          promptAdditions: ["Always cite reachable alternate sources."],
          relatedErrorTypes: ["connection_failed"],
        },
      ],
      promptPack: {
        title: "Prompt pack",
        lines: [],
      },
    });

    const result = await caller.experienceSummary.generate({
      projectId,
      mode: "full",
      model: "gpt-5.2",
      maxItems: 50,
    });

    expect(result.updated).toBe(true);
    expect(result.row?.summary.promptPack.lines).toContain(
      "Always cite reachable alternate sources.",
    );
  });
});
