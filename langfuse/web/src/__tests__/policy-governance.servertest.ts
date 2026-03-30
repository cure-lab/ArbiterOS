/** @jest-environment node */

import { mkdtemp, mkdir, rm, writeFile } from "fs/promises";
import { join } from "path";
import { tmpdir } from "os";
import {
  buildPolicyCards,
  getPolicySourceMetadata,
  parseProposalResult,
  resolvePolicyPaths,
} from "@/src/features/policy-governance/server/router";

async function writePolicyPair(baseDir: string) {
  const policyJsonPath = join(baseDir, "policy.json");
  const policyRegistryPath = join(baseDir, "policy_registry.json");
  await writeFile(
    policyJsonPath,
    JSON.stringify(
      {
        rate_limit: {
          max_consecutive_same_tool: 20,
          window_seconds: 10,
          max_calls_per_window: 30,
        },
        allow: { tools: ["read"] },
        deny: { tools: [] },
      },
      null,
      2,
    ),
    "utf8",
  );
  await writeFile(
    policyRegistryPath,
    JSON.stringify(
      [
        {
          name: "RateLimitPolicy",
          enabled: true,
          description: "Limits repeated calls.",
        },
        {
          name: "AllowDenyPolicy",
          enabled: true,
          description: "Allow/Deny list policy.",
        },
      ],
      null,
      2,
    ),
    "utf8",
  );
  return { policyJsonPath, policyRegistryPath };
}

describe("policy-governance helpers", () => {
  let sandboxDir = "";
  const originalPathPrefixMap = process.env.LANGFUSE_PATH_PREFIX_MAP;
  const originalLegacyPolicyPathPrefixMap =
    process.env.LANGFUSE_POLICY_PATH_PREFIX_MAP;

  beforeEach(async () => {
    sandboxDir = await mkdtemp(join(tmpdir(), "lf-policy-governance-"));
  });

  afterEach(async () => {
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

    if (sandboxDir) {
      await rm(sandboxDir, { recursive: true, force: true });
    }
  });

  it("resolves policy files from a direct policy.json path", async () => {
    const kernelDir = join(sandboxDir, "arbiteros_kernel");
    await mkdir(kernelDir, { recursive: true });
    const { policyJsonPath, policyRegistryPath } =
      await writePolicyPair(kernelDir);

    const resolved = await resolvePolicyPaths(policyJsonPath);

    expect(resolved.policyJsonPath).toBe(policyJsonPath);
    expect(resolved.policyRegistryPath).toBe(policyRegistryPath);
  });

  it("resolves policy files from a project root path with arbiteros_kernel", async () => {
    const kernelDir = join(sandboxDir, "arbiteros_kernel");
    await mkdir(kernelDir, { recursive: true });
    const { policyJsonPath, policyRegistryPath } =
      await writePolicyPair(kernelDir);

    const resolved = await resolvePolicyPaths(sandboxDir);

    expect(resolved.policyJsonPath).toBe(policyJsonPath);
    expect(resolved.policyRegistryPath).toBe(policyRegistryPath);
  });

  it("rewrites configured host prefixes to mounted container prefixes", async () => {
    const hostWorkspaceRoot = join(sandboxDir, "host-workspace");
    const mountedWorkspaceRoot = join(sandboxDir, "mounted-workspace");
    const hostLangfuseDir = join(hostWorkspaceRoot, "langfuse");
    const hostProjectRoot = join(hostWorkspaceRoot, "ArbiterOS-Kernel");
    const mountedProjectRoot = join(mountedWorkspaceRoot, "ArbiterOS-Kernel");
    const mountedKernelDir = join(mountedProjectRoot, "arbiteros_kernel");
    await mkdir(hostLangfuseDir, { recursive: true });
    await mkdir(mountedKernelDir, { recursive: true });
    const { policyJsonPath, policyRegistryPath } =
      await writePolicyPair(mountedKernelDir);

    process.env.LANGFUSE_PATH_PREFIX_MAP = `${join(hostLangfuseDir, "..")}=${mountedWorkspaceRoot}`;

    const resolved = await resolvePolicyPaths(
      join(hostProjectRoot, "arbiteros_kernel"),
    );

    expect(resolved.resolvedPathInput).toBe(mountedKernelDir);
    expect(resolved.policyJsonPath).toBe(policyJsonPath);
    expect(resolved.policyRegistryPath).toBe(policyRegistryPath);
  });

  it("builds policy cards with mapped settings sections", () => {
    const cards = buildPolicyCards({
      policyRegistryJson: [
        {
          name: "RateLimitPolicy",
          enabled: true,
          description: "Limits repeated calls.",
        },
        {
          name: "AllowDenyPolicy",
          enabled: false,
          description: "Allow/Deny list policy.",
        },
      ],
      policyJson: {
        rate_limit: {
          max_consecutive_same_tool: 20,
          window_seconds: 10,
          max_calls_per_window: 30,
        },
        allow: { tools: ["read"] },
        deny: { tools: [] },
      },
    });

    expect(cards).toHaveLength(2);
    expect(cards[0]?.settingSections).toEqual(["rate_limit"]);
    expect(cards[0]?.settingsBySection).toEqual({
      rate_limit: {
        max_consecutive_same_tool: 20,
        window_seconds: 10,
        max_calls_per_window: 30,
      },
    });
    expect(cards[1]?.settingSections).toEqual(["allow", "deny"]);
  });

  it("updates the source fingerprint when a policy file changes", async () => {
    const kernelDir = join(sandboxDir, "arbiteros_kernel");
    await mkdir(kernelDir, { recursive: true });
    const { policyJsonPath, policyRegistryPath } =
      await writePolicyPair(kernelDir);

    const firstMetadata = await getPolicySourceMetadata({
      policyJsonPath,
      policyRegistryPath,
    });

    await writeFile(
      policyJsonPath,
      JSON.stringify(
        {
          rate_limit: {
            max_consecutive_same_tool: 25,
            window_seconds: 10,
            max_calls_per_window: 30,
          },
          allow: { tools: ["read"] },
          deny: { tools: [] },
          delete_policy: { require_confirmation: true },
        },
        null,
        2,
      ),
      "utf8",
    );

    const secondMetadata = await getPolicySourceMetadata({
      policyJsonPath,
      policyRegistryPath,
    });

    expect(secondMetadata.policySourceFingerprint).not.toBe(
      firstMetadata.policySourceFingerprint,
    );
    expect(
      new Date(secondMetadata.sourceLastModifiedAt).getTime(),
    ).toBeGreaterThanOrEqual(
      new Date(firstMetadata.sourceLastModifiedAt).getTime(),
    );
  });

  it("rejects non-absolute path input", async () => {
    await expect(
      resolvePolicyPaths("relative/path/to/policy.json"),
    ).rejects.toThrow(/Policy path must be absolute/);
  });

  it("parses policy proposal JSON wrapped in extra text", () => {
    const parsed = parseProposalResult(`Here is the proposal:

\`\`\`json
{
  "summary": "Tighten the delete policy to reduce false positives.",
  "proposedPolicyJson": {
    "delete_policy": {
      "allowed_paths": ["/tmp"]
    }
  }
}
\`\`\`
`);

    expect(parsed).toEqual({
      summary: "Tighten the delete policy to reduce false positives.",
      proposedPolicyJson: {
        delete_policy: {
          allowed_paths: ["/tmp"],
        },
      },
    });
  });

  it("normalizes nested proposal wrapper shapes", () => {
    const parsed = parseProposalResult({
      proposal: {
        summary: "Narrow the delete scope to destructive actions only.",
        proposedPolicyJson: {
          delete_policy: {
            require_confirmation: true,
          },
        },
      },
    });

    expect(parsed).toEqual({
      summary: "Narrow the delete scope to destructive actions only.",
      proposedPolicyJson: {
        delete_policy: {
          require_confirmation: true,
        },
      },
    });
  });

  it("parses proposal objects where policy fields are JSON strings", () => {
    const parsed = parseProposalResult({
      summary: "Keep schema stable while updating one section.",
      proposedPolicyJson: JSON.stringify({
        delete_policy: {
          require_confirmation: true,
        },
      }),
    });

    expect(parsed).toEqual({
      summary: "Keep schema stable while updating one section.",
      proposedPolicyJson: {
        delete_policy: {
          require_confirmation: true,
        },
      },
    });
  });
});
