import type { ExperienceSummaryJson } from "./types";

function uniqueStableLines(lines: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of lines) {
    const normalized = line.trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    out.push(normalized);
  }
  return out;
}

export function buildExperienceCopyAllText(
  summary: ExperienceSummaryJson,
): string {
  const promptPackLines = uniqueStableLines(summary.promptPack.lines ?? []);
  const additions = uniqueStableLines(
    summary.experiences.flatMap((exp) => exp.promptAdditions ?? []),
  );
  return [...promptPackLines, ...additions].join("\n");
}
