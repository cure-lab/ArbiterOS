import { isAbsolute, resolve, sep } from "path";

export function trimTrailingPathSeparators(path: string) {
  if (path.length <= 1) return path;
  return path.replace(/[\\/]+$/, "");
}

function normalizeAbsolutePath(path: string) {
  return trimTrailingPathSeparators(resolve(path));
}

function parseAbsolutePathPrefixMappings() {
  const raw =
    process.env.LANGFUSE_PATH_PREFIX_MAP?.trim() ||
    process.env.LANGFUSE_POLICY_PATH_PREFIX_MAP?.trim() ||
    "";
  if (!raw) return [];

  return raw
    .split(",")
    .map((entry) => {
      const [sourcePrefixRaw, ...targetPrefixParts] = entry.split("=");
      const sourcePrefix = sourcePrefixRaw?.trim();
      const targetPrefix = targetPrefixParts.join("=").trim();
      if (!sourcePrefix || !targetPrefix) return null;
      if (!isAbsolute(sourcePrefix) || !isAbsolute(targetPrefix)) return null;

      return {
        sourcePrefix: normalizeAbsolutePath(sourcePrefix),
        targetPrefix: normalizeAbsolutePath(targetPrefix),
      };
    })
    .filter(
      (mapping): mapping is { sourcePrefix: string; targetPrefix: string } =>
        mapping !== null,
    )
    .sort((a, b) => b.sourcePrefix.length - a.sourcePrefix.length);
}

export function rewriteAbsolutePathFromPrefixMappings(pathInput: string) {
  const normalizedInput = normalizeAbsolutePath(pathInput);

  for (const mapping of parseAbsolutePathPrefixMappings()) {
    if (
      normalizedInput === mapping.sourcePrefix ||
      normalizedInput.startsWith(`${mapping.sourcePrefix}${sep}`)
    ) {
      return `${mapping.targetPrefix}${normalizedInput.slice(
        mapping.sourcePrefix.length,
      )}`;
    }
  }

  return normalizedInput;
}
