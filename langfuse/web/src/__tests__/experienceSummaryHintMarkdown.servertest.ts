/** @jest-environment node */

import {
  buildExperienceSummaryHintSectionContent,
  removeProjectHintSectionFromMarkdown,
  upsertProjectHintSectionInMarkdown,
} from "@langfuse/shared/src/utils/experienceSummaryHintMarkdown";

describe("experienceSummaryHintMarkdown utility", () => {
  const projectId = "cmlgcv2zs0007w26wzt7dvf0e";

  it("should replace malformed duplicated project sections with one clean section", () => {
    const markdown = [
      "# Agent Instructions",
      "",
      "## HINT",
      "",
      `<!-- LF_PROJECT_HINT_START:${projectId} -->`,
      "",
      `<!-- LF_PROJECT_HINT_START:${projectId} -->`,
      `### Project ${projectId}`,
      "",
      "_Last updated: 2026-02-24T15:19:55.607Z_",
      "",
      "#### Prompt pack",
      "- title: Stale title",
      "- lines:",
      "  - stale line one",
      `<!-- LF_PROJECT_HINT_END:${projectId} -->`,
      "",
      "#### Prompt pack",
      "- title: Stale duplicate title",
      "- lines:",
      "  - stale line two",
      `<!-- LF_PROJECT_HINT_END:${projectId} -->`,
      "",
      "#### Prompt pack",
      "- title: Stale trailing title",
      "- lines:",
      "  - stale line three",
      "",
      "## After",
      "",
      "Keep me",
      "",
    ].join("\n");

    const replacementContent = buildExperienceSummaryHintSectionContent({
      summary: {
        schemaVersion: 1,
        experiences: [],
        promptPack: {
          title: "Web fetch fallback",
          lines: ["Use alternate reachable domains when fetch fails."],
        },
      },
      outputMode: "prompt_pack_only",
      now: new Date("2026-02-25T00:00:00.000Z"),
      sectionHeadingHashCount: 4,
    });

    const updated = upsertProjectHintSectionInMarkdown({
      markdown,
      projectId,
      replacementContent,
      defaultHeadingHashCount: 2,
    });

    expect(
      (
        updated.match(new RegExp(`LF_PROJECT_HINT_START:${projectId}`, "g")) ??
        []
      ).length,
    ).toBe(1);
    expect(
      (updated.match(new RegExp(`LF_PROJECT_HINT_END:${projectId}`, "g")) ?? [])
        .length,
    ).toBe(1);
    expect(updated).toContain(
      "Use alternate reachable domains when fetch fails.",
    );
    expect(updated).not.toContain("stale line one");
    expect(updated).not.toContain("stale line two");
    expect(updated).not.toContain("stale line three");
    expect(updated).toContain("## After");
    expect(updated).toContain("Keep me");
  });

  it("should remove malformed target project hint while keeping other projects", () => {
    const otherProjectId = "project-other";
    const markdown = [
      "# Agent Instructions",
      "",
      "## HINT",
      "",
      `<!-- LF_PROJECT_HINT_START:${projectId} -->`,
      `### Project ${projectId}`,
      "",
      "#### Prompt pack",
      "- title: Old one",
      "- lines:",
      "  - old line one",
      `<!-- LF_PROJECT_HINT_END:${projectId} -->`,
      "",
      `<!-- LF_PROJECT_HINT_START:${projectId} -->`,
      `### Project ${projectId}`,
      "",
      "#### Prompt pack",
      "- title: Old two",
      "- lines:",
      "  - old line two",
      `<!-- LF_PROJECT_HINT_END:${projectId} -->`,
      "",
      `<!-- LF_PROJECT_HINT_START:${otherProjectId} -->`,
      `### Project ${otherProjectId}`,
      "",
      "#### Prompt pack",
      "- title: Keep me",
      "- lines:",
      "  - keep me line",
      `<!-- LF_PROJECT_HINT_END:${otherProjectId} -->`,
      "",
      "## After",
      "",
      "Keep this section",
      "",
    ].join("\n");

    const removed = removeProjectHintSectionFromMarkdown({
      markdown,
      projectId,
    });

    expect(removed.removed).toBe(true);
    expect(removed.markdown).not.toContain(`Project ${projectId}`);
    expect(removed.markdown).not.toContain("old line one");
    expect(removed.markdown).not.toContain("old line two");
    expect(removed.markdown).toContain(`Project ${otherProjectId}`);
    expect(removed.markdown).toContain("keep me line");
    expect(removed.markdown).toContain("## After");
    expect(removed.markdown).toContain("Keep this section");
  });

  it("should deduplicate normalized prompt-pack lines in markdown output", () => {
    const content = buildExperienceSummaryHintSectionContent({
      summary: {
        schemaVersion: 1,
        experiences: [],
        promptPack: {
          title: "Prompt pack",
          lines: [
            "Use HTTPS sources first.",
            "  Use HTTPS   sources first.  ",
            "Use web_search when web_fetch fails.",
          ],
        },
      },
      outputMode: "prompt_pack_only",
      now: new Date("2026-02-25T00:00:00.000Z"),
      sectionHeadingHashCount: 4,
    });

    expect((content.match(/Use HTTPS sources first\./g) ?? []).length).toBe(1);
    expect(content).toContain("Use web_search when web_fetch fails.");
  });
});
