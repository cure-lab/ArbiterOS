type ExperienceSummaryLike = {
  promptPack: { title: string; lines: string[] };
  experiences: Array<{
    key: string;
    when: string;
    keywords?: string[] | null;
    relatedErrorTypes?: string[] | null;
    possibleProblems: string[];
    avoidanceAndNotes: string[];
    promptAdditions: string[];
  }>;
};

export type ExperienceSummaryMarkdownOutputMode = "prompt_pack_only" | "full";

function normalizeMarkdownLine(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

function uniqueNormalizedLines(values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const value of values) {
    const normalized = normalizeMarkdownLine(value);
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

export function buildExperienceSummaryHintSectionContent(params: {
  summary: ExperienceSummaryLike;
  now?: Date;
  outputMode?: ExperienceSummaryMarkdownOutputMode;
  sectionHeadingHashCount?: number;
}): string {
  const now = params.now ?? new Date();
  const outputMode = params.outputMode ?? "full";
  const sectionHeadingHashCount = Math.max(
    1,
    Math.min(6, params.sectionHeadingHashCount ?? 2),
  );
  const lines: string[] = [];
  lines.push(`_Last updated: ${now.toISOString()}_`);
  lines.push("");
  lines.push(`${"#".repeat(sectionHeadingHashCount)} Prompt pack`);
  lines.push(
    `- title: ${normalizeMarkdownLine(params.summary.promptPack.title)}`,
  );
  const normalizedPromptPackLines = uniqueNormalizedLines(
    params.summary.promptPack.lines ?? [],
  );
  if (normalizedPromptPackLines.length === 0) {
    lines.push("- lines:");
    lines.push("  - (none)");
  } else {
    lines.push("- lines:");
    for (const line of normalizedPromptPackLines) {
      lines.push(`  - ${line}`);
    }
  }
  lines.push("");
  if (outputMode === "full") {
    lines.push(`${"#".repeat(sectionHeadingHashCount)} Experiences`);
    if ((params.summary.experiences ?? []).length === 0) {
      lines.push("- (none)");
    } else {
      for (const experience of params.summary.experiences ?? []) {
        lines.push(`### ${experience.key}`);
        lines.push(`- when: ${normalizeMarkdownLine(experience.when)}`);
        lines.push(
          `- relatedErrorTypes: ${
            experience.relatedErrorTypes?.length
              ? experience.relatedErrorTypes.join(", ")
              : "n/a"
          }`,
        );
        lines.push(
          `- keywords: ${
            experience.keywords?.length ? experience.keywords.join(", ") : "n/a"
          }`,
        );
        lines.push("- possibleProblems:");
        if ((experience.possibleProblems ?? []).length === 0) {
          lines.push("  - (none)");
        } else {
          for (const problem of experience.possibleProblems ?? []) {
            lines.push(`  - ${normalizeMarkdownLine(problem)}`);
          }
        }
        lines.push("- avoidanceAndNotes:");
        if ((experience.avoidanceAndNotes ?? []).length === 0) {
          lines.push("  - (none)");
        } else {
          for (const note of experience.avoidanceAndNotes ?? []) {
            lines.push(`  - ${normalizeMarkdownLine(note)}`);
          }
        }
        lines.push("- promptAdditions:");
        if ((experience.promptAdditions ?? []).length === 0) {
          lines.push("  - (none)");
        } else {
          for (const addition of experience.promptAdditions ?? []) {
            lines.push(`  - ${normalizeMarkdownLine(addition)}`);
          }
        }
        lines.push("");
      }
    }
  }

  return lines.join("\n").trimEnd();
}

const HINT_HEADING_REGEX = /^[ \t]*(#{1,6})[ \t]*hint[ \t]*$/im;
const ANY_HEADING_REGEX = /^[ \t]*(#{1,6})[ \t]+.+$/gm;

function isHintHeadingLine(line: string): boolean {
  return /^\s*#{1,6}\s*hint\s*$/i.test(line);
}

function buildHintHeadingLine(params: { hashCount: number }): string {
  const hashCount = Math.max(1, Math.min(6, params.hashCount));
  return `${"#".repeat(hashCount)} HINT`;
}

function buildProjectHintSubsectionHeading(params: {
  hintHashCount: number;
  projectId: string;
}): string {
  const hashCount = Math.max(1, Math.min(6, params.hintHashCount + 1));
  return `${"#".repeat(hashCount)} Project ${params.projectId}`;
}

function buildProjectSectionStartMarker(projectId: string): string {
  return `<!-- LF_PROJECT_HINT_START:${projectId} -->`;
}

function buildProjectSectionEndMarker(projectId: string): string {
  return `<!-- LF_PROJECT_HINT_END:${projectId} -->`;
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function resolveHintHeadingMatch(markdown: string): {
  matchIndex: number;
  hashCount: number;
} | null {
  const m = HINT_HEADING_REGEX.exec(markdown);
  if (!m || m.index == null) return null;
  const hashes = m[1] ?? "#";
  return { matchIndex: m.index, hashCount: hashes.length };
}

function resolveHintSectionEndIndex(params: {
  markdown: string;
  sectionContentStart: number;
  hintHashCount: number;
}): number {
  const { markdown, sectionContentStart, hintHashCount } = params;
  const rest = markdown.slice(sectionContentStart);
  const headingRegex = new RegExp(ANY_HEADING_REGEX.source, "gm");
  let next: RegExpExecArray | null;
  while ((next = headingRegex.exec(rest)) != null) {
    const hashes = next[1] ?? "";
    const count = hashes.length;
    const line = next[0] ?? "";
    if (count <= hintHashCount) {
      if (isHintHeadingLine(line)) continue;
      return sectionContentStart + next.index;
    }
  }
  return markdown.length;
}

function resolveProjectSubsectionMatch(params: {
  content: string;
  hintHashCount: number;
  projectId: string;
}): {
  start: number;
  end: number;
} | null {
  const startMarker = buildProjectSectionStartMarker(params.projectId);
  const endMarker = buildProjectSectionEndMarker(params.projectId);
  const projectHashCount = Math.max(1, Math.min(6, params.hintHashCount + 1));

  const markerRegex = /<!--\s*LF_PROJECT_HINT_START:([^>]+)-->/g;
  const markerStartsForProject: number[] = [];
  const otherProjectBoundaries: number[] = [];
  let markerMatch: RegExpExecArray | null;
  while ((markerMatch = markerRegex.exec(params.content)) != null) {
    const matchedProjectId = (markerMatch[1] ?? "").trim();
    if (matchedProjectId === params.projectId) {
      markerStartsForProject.push(markerMatch.index);
    } else {
      otherProjectBoundaries.push(markerMatch.index);
    }
  }

  const projectHeadingRegex = new RegExp(
    `^\\s*${"#".repeat(projectHashCount)}\\s+Project\\s+(.+?)\\s*$`,
    "gm",
  );
  const headingStartsForProject: number[] = [];
  let headingMatch: RegExpExecArray | null;
  while ((headingMatch = projectHeadingRegex.exec(params.content)) != null) {
    const headingProjectId = (headingMatch[1] ?? "").trim();
    if (headingProjectId === params.projectId) {
      headingStartsForProject.push(headingMatch.index);
    } else {
      otherProjectBoundaries.push(headingMatch.index);
    }
  }

  const resolveNextBoundary = (start: number) => {
    const nextBoundary = otherProjectBoundaries
      .filter((index) => index > start)
      .sort((a, b) => a - b)[0];
    return nextBoundary == null ? params.content.length : nextBoundary;
  };

  if (markerStartsForProject.length > 1) {
    const start = [...markerStartsForProject].sort((a, b) => a - b)[0]!;
    return {
      start,
      end: resolveNextBoundary(start),
    };
  }

  if (markerStartsForProject.length === 1) {
    const start = markerStartsForProject[0]!;
    const markerEndIndex = params.content.indexOf(
      endMarker,
      start + startMarker.length,
    );
    if (markerEndIndex !== -1) {
      return {
        start,
        end: markerEndIndex + endMarker.length,
      };
    }
    return {
      start,
      end: resolveNextBoundary(start),
    };
  }

  if (headingStartsForProject.length === 0) return null;
  const start = [...headingStartsForProject].sort((a, b) => a - b)[0]!;
  return {
    start,
    end: resolveNextBoundary(start),
  };
}

function removeProjectScaffoldLines(params: {
  content: string;
  hintHashCount: number;
  projectId: string;
}): string {
  const escapedProjectId = escapeRegex(params.projectId);
  const projectHashCount = Math.max(1, Math.min(6, params.hintHashCount + 1));
  const markerLineRegex = new RegExp(
    `^\\s*<!--\\s*LF_PROJECT_HINT_(?:START|END):${escapedProjectId}\\s*-->\\s*$`,
    "im",
  );
  const headingLineRegex = new RegExp(
    `^\\s*${"#".repeat(projectHashCount)}\\s+Project\\s+${escapedProjectId}\\s*$`,
    "im",
  );

  return params.content
    .split("\n")
    .filter(
      (line) => !markerLineRegex.test(line) && !headingLineRegex.test(line),
    )
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function hasAnyProjectSubsection(params: {
  content: string;
  hintHashCount: number;
}): boolean {
  if (
    /<!--\s*LF_PROJECT_HINT_START:[^>]+-->/i.test(params.content) &&
    /<!--\s*LF_PROJECT_HINT_END:[^>]+-->/i.test(params.content)
  ) {
    return true;
  }
  const projectHashCount = Math.max(1, Math.min(6, params.hintHashCount + 1));
  const headingRegex = new RegExp(
    `^\\s*${"#".repeat(projectHashCount)}\\s+Project\\s+.+$`,
    "im",
  );
  return headingRegex.test(params.content);
}

function rebuildHintSection(params: {
  markdown: string;
  hintMatch: { matchIndex: number; hashCount: number };
  replacementContent: string;
}) {
  const afterHeadingIndex = params.markdown.indexOf(
    "\n",
    params.hintMatch.matchIndex,
  );
  const sectionContentStart =
    afterHeadingIndex === -1 ? params.markdown.length : afterHeadingIndex + 1;
  const sectionEnd = resolveHintSectionEndIndex({
    markdown: params.markdown,
    sectionContentStart,
    hintHashCount: params.hintMatch.hashCount,
  });
  const replacementHeading = buildHintHeadingLine({
    hashCount: params.hintMatch.hashCount,
  });
  const replacementSection = `${replacementHeading}\n\n${params.replacementContent.trim()}\n`;
  const before = params.markdown
    .slice(0, params.hintMatch.matchIndex)
    .replace(/\s*$/, "");
  const after = params.markdown.slice(sectionEnd).replace(/^\s*/, "");

  return [before, replacementSection.trimEnd(), after]
    .filter((part) => part.length > 0)
    .join("\n\n");
}

export function upsertHintSectionInMarkdown(params: {
  markdown: string;
  replacementContent: string;
  defaultHeadingHashCount?: number;
}): string {
  const defaultHeadingHashCount = params.defaultHeadingHashCount ?? 2;
  const match = resolveHintHeadingMatch(params.markdown);
  const replacementHeading = buildHintHeadingLine({
    hashCount: match?.hashCount ?? defaultHeadingHashCount,
  });
  const replacementSection = `${replacementHeading}\n\n${params.replacementContent}\n`;

  if (!match) {
    if (params.markdown.trim().length === 0) return replacementSection;
    return `${params.markdown.replace(/\s*$/, "")}\n\n${replacementSection}`;
  }

  const afterHeadingIndex = params.markdown.indexOf("\n", match.matchIndex);
  const sectionContentStart =
    afterHeadingIndex === -1 ? params.markdown.length : afterHeadingIndex + 1;
  const sectionEnd = resolveHintSectionEndIndex({
    markdown: params.markdown,
    sectionContentStart,
    hintHashCount: match.hashCount,
  });

  const before = params.markdown.slice(0, match.matchIndex).replace(/\s*$/, "");
  const after = params.markdown.slice(sectionEnd).replace(/^\s*/, "");
  return [before, replacementSection.trimEnd(), after]
    .filter((part) => part.length > 0)
    .join("\n\n");
}

export function upsertProjectHintSectionInMarkdown(params: {
  markdown: string;
  projectId: string;
  replacementContent: string;
  defaultHeadingHashCount?: number;
}): string {
  const defaultHeadingHashCount = params.defaultHeadingHashCount ?? 2;
  const match = resolveHintHeadingMatch(params.markdown);
  const normalizedReplacement = params.replacementContent.trim();

  if (!match) {
    const hintHeading = buildHintHeadingLine({
      hashCount: defaultHeadingHashCount,
    });
    const projectHeading = buildProjectHintSubsectionHeading({
      hintHashCount: defaultHeadingHashCount,
      projectId: params.projectId,
    });
    const section = `${hintHeading}\n\n${projectHeading}\n\n${normalizedReplacement}\n`;
    if (params.markdown.trim().length === 0) return section;
    return `${params.markdown.replace(/\s*$/, "")}\n\n${section}`;
  }

  const afterHeadingIndex = params.markdown.indexOf("\n", match.matchIndex);
  const sectionContentStart =
    afterHeadingIndex === -1 ? params.markdown.length : afterHeadingIndex + 1;
  const sectionEnd = resolveHintSectionEndIndex({
    markdown: params.markdown,
    sectionContentStart,
    hintHashCount: match.hashCount,
  });
  const hintContent = params.markdown
    .slice(sectionContentStart, sectionEnd)
    .trim();
  const projectHeading = buildProjectHintSubsectionHeading({
    hintHashCount: match.hashCount,
    projectId: params.projectId,
  });
  const nextProjectSection = [
    buildProjectSectionStartMarker(params.projectId),
    projectHeading,
    "",
    normalizedReplacement,
    buildProjectSectionEndMarker(params.projectId),
  ]
    .join("\n")
    .trim();
  const subsectionMatch = resolveProjectSubsectionMatch({
    content: hintContent,
    hintHashCount: match.hashCount,
    projectId: params.projectId,
  });

  const containsProjectSubsections = hasAnyProjectSubsection({
    content: hintContent,
    hintHashCount: match.hashCount,
  });

  let sanitizedHintContent = hintContent;
  let removedAnyExistingProjectSection = false;
  let currentMatch = subsectionMatch;
  while (currentMatch) {
    removedAnyExistingProjectSection = true;
    const before = sanitizedHintContent
      .slice(0, currentMatch.start)
      .replace(/\s*$/, "");
    const after = sanitizedHintContent
      .slice(currentMatch.end)
      .replace(/^\s*/, "");
    sanitizedHintContent = [before, after]
      .filter((part) => part.length > 0)
      .join("\n\n");
    currentMatch = resolveProjectSubsectionMatch({
      content: sanitizedHintContent,
      hintHashCount: match.hashCount,
      projectId: params.projectId,
    });
  }

  const scaffoldCleanedHintContent = removeProjectScaffoldLines({
    content: sanitizedHintContent,
    hintHashCount: match.hashCount,
    projectId: params.projectId,
  });
  const replacementContent =
    !removedAnyExistingProjectSection && !containsProjectSubsections
      ? nextProjectSection
      : scaffoldCleanedHintContent.length === 0
        ? nextProjectSection
        : `${scaffoldCleanedHintContent.replace(/\s*$/, "")}\n\n${nextProjectSection}`;

  return rebuildHintSection({
    markdown: params.markdown,
    hintMatch: match,
    replacementContent,
  });
}

export function removeHintSectionFromMarkdown(params: { markdown: string }): {
  markdown: string;
  removed: boolean;
} {
  const match = resolveHintHeadingMatch(params.markdown);
  if (!match) return { markdown: params.markdown, removed: false };

  const afterHeadingIndex = params.markdown.indexOf("\n", match.matchIndex);
  const sectionContentStart =
    afterHeadingIndex === -1 ? params.markdown.length : afterHeadingIndex + 1;
  const sectionEnd = resolveHintSectionEndIndex({
    markdown: params.markdown,
    sectionContentStart,
    hintHashCount: match.hashCount,
  });

  const before = params.markdown.slice(0, match.matchIndex).replace(/\s*$/, "");
  const after = params.markdown.slice(sectionEnd).replace(/^\s*/, "");
  const updated = [before, after]
    .filter((part) => part.length > 0)
    .join("\n\n");
  return { markdown: updated, removed: true };
}

export function removeProjectHintSectionFromMarkdown(params: {
  markdown: string;
  projectId: string;
}): {
  markdown: string;
  removed: boolean;
} {
  const match = resolveHintHeadingMatch(params.markdown);
  if (!match) return { markdown: params.markdown, removed: false };

  const afterHeadingIndex = params.markdown.indexOf("\n", match.matchIndex);
  const sectionContentStart =
    afterHeadingIndex === -1 ? params.markdown.length : afterHeadingIndex + 1;
  const sectionEnd = resolveHintSectionEndIndex({
    markdown: params.markdown,
    sectionContentStart,
    hintHashCount: match.hashCount,
  });
  const hintContent = params.markdown
    .slice(sectionContentStart, sectionEnd)
    .trim();

  let remainingContent = hintContent;
  let removedAny = false;
  let subsectionMatch = resolveProjectSubsectionMatch({
    content: remainingContent,
    hintHashCount: match.hashCount,
    projectId: params.projectId,
  });
  while (subsectionMatch) {
    removedAny = true;
    const before = remainingContent
      .slice(0, subsectionMatch.start)
      .replace(/\s*$/, "");
    const after = remainingContent
      .slice(subsectionMatch.end)
      .replace(/^\s*/, "");
    remainingContent = [before, after]
      .filter((part) => part.length > 0)
      .join("\n\n");
    subsectionMatch = resolveProjectSubsectionMatch({
      content: remainingContent,
      hintHashCount: match.hashCount,
      projectId: params.projectId,
    });
  }

  const scaffoldCleaned = removeProjectScaffoldLines({
    content: remainingContent,
    hintHashCount: match.hashCount,
    projectId: params.projectId,
  });
  if (scaffoldCleaned !== remainingContent) {
    removedAny = true;
  }

  if (scaffoldCleaned.length === 0) {
    return removeHintSectionFromMarkdown({ markdown: params.markdown });
  }

  if (!removedAny) {
    if (
      !hasAnyProjectSubsection({
        content: hintContent,
        hintHashCount: match.hashCount,
      })
    ) {
      return removeHintSectionFromMarkdown({ markdown: params.markdown });
    }
    return { markdown: params.markdown, removed: false };
  }

  return {
    markdown: rebuildHintSection({
      markdown: params.markdown,
      hintMatch: match,
      replacementContent: scaffoldCleaned,
    }),
    removed: true,
  };
}
