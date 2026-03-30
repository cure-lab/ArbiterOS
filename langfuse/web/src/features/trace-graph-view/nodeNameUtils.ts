const LEGACY_TOOL_RESULT_NODE_RE =
  /^tool\.(?<toolName>.+)\.result\.call_(?<index>\d+)$/;
const PARSER_NODE_RE =
  /^(?:session\.)?parser\.turn_(?<turn>\d+)(?:\.(?<suffix>.+))?$/;

const SESSION_PARSER_NODE_RE = /^session\.parser\.(?<rest>.+)$/;
const PARSER_TURN_TOOL_RESULT_RE =
  /^parser\.turn_(?<turn>\d+)\.tool_result\.(?<toolName>[^.]+)\.(?<index>\d+)$/;
const PARSER_TURN_TOOL_PRE_RE =
  /^parser\.turn_(?<turn>\d+)\.pre_(?<toolName>[^.]+)\.(?<index>\d+)$/;

type ParserNodeParts = {
  turn: number;
  suffixSegments: string[];
};

export function isParserContainerNodeName(
  nodeName: string | null | undefined,
): boolean {
  const parsed = parseParserNodeName(nodeName);
  if (!parsed) {
    return false;
  }

  if (parsed.suffixSegments.length === 0) {
    return true;
  }

  // Hide high-level "container" nodes (not the specific tool_call/tool_result leaves)
  // Examples:
  // - session.parser.turn_002.tool_calls
  // - session.parser.turn_002.tool_results
  // - session.parser.turn_002.tool_call
  // - session.parser.turn_002.tool_result
  // - session.parser.turn_002.structured_output
  const [kind, ...rest] = parsed.suffixSegments;
  if (rest.length > 0) {
    return false;
  }

  return (
    kind === "tool_calls" ||
    kind === "tool_results" ||
    kind === "tool_call" ||
    kind === "tool_result" ||
    kind === "structured_output" ||
    kind === "strucutured_output"
  );
}

export function normalizeToolResultNodeName(
  nodeName: string | null | undefined,
): string | null | undefined {
  if (!nodeName) {
    return nodeName;
  }

  const match = LEGACY_TOOL_RESULT_NODE_RE.exec(nodeName);
  if (!match?.groups) {
    return nodeName;
  }

  const toolName = match.groups.toolName?.trim();
  const index = match.groups.index;
  if (!toolName || !index) {
    return nodeName;
  }

  return `${toolName}.${index}`;
}

export function parseParserNodeName(
  nodeName: string | null | undefined,
): ParserNodeParts | null {
  if (!nodeName) {
    return null;
  }

  const match = PARSER_NODE_RE.exec(nodeName);
  if (!match?.groups?.turn) {
    return null;
  }

  const turn = Number.parseInt(match.groups.turn, 10);
  if (Number.isNaN(turn)) {
    return null;
  }

  const suffixSegments = match.groups.suffix
    ? match.groups.suffix.split(".").filter(Boolean)
    : [];

  return {
    turn,
    suffixSegments,
  };
}

export function isParserNodeName(nodeName: string | null | undefined): boolean {
  return parseParserNodeName(nodeName) !== null;
}

/**
 * Normalizes internal session-scoped parser node names for graph display.
 *
 * Examples:
 * - session.parser.turn_002.tool_result.web_search.4 -> parser.web_search.4
 * - session.parser.turn_002.tool_calls -> parser.turn_002.tool_calls
 * - session.parser.turn_002.structured_output -> parser.turn_002.structured_output
 */
export function normalizeParserNodeNameForGraph(
  nodeName: string | null | undefined,
): string | null | undefined {
  if (!nodeName) {
    return nodeName;
  }

  const sessionMatch = SESSION_PARSER_NODE_RE.exec(nodeName);
  if (!sessionMatch?.groups?.rest) {
    return nodeName;
  }

  const withoutSession = `parser.${sessionMatch.groups.rest}`;
  const toolPreMatch = PARSER_TURN_TOOL_PRE_RE.exec(withoutSession);
  if (
    toolPreMatch?.groups?.toolName &&
    toolPreMatch.groups.index &&
    /^\d+$/.test(toolPreMatch.groups.index)
  ) {
    return `parser.pre_${toolPreMatch.groups.toolName}.${toolPreMatch.groups.index}`;
  }
  const toolResultMatch = PARSER_TURN_TOOL_RESULT_RE.exec(withoutSession);
  if (
    toolResultMatch?.groups?.toolName &&
    toolResultMatch.groups.index &&
    /^\d+$/.test(toolResultMatch.groups.index)
  ) {
    return `parser.${toolResultMatch.groups.toolName}.${toolResultMatch.groups.index}`;
  }

  return withoutSession;
}

function toTitleCase(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function getParserSuffixLabel(suffixSegments: string[]): string {
  if (suffixSegments.length === 0) {
    return "Parser";
  }

  const [kind, second, third, ...rest] = suffixSegments;

  if (kind === "tool_result") {
    if (second && third && /^\d+$/.test(third)) {
      return `${second} result #${third}`;
    }
    if (second) {
      return `${second} result`;
    }
    return "Tool result";
  }

  if (kind === "tool_call") {
    if (second && third && /^\d+$/.test(third)) {
      return `${second} call #${third}`;
    }
    if (second) {
      return `${second} call`;
    }
    return "Tool call";
  }

  if (kind === "tool_calls") {
    return "Tool calls";
  }

  if (kind === "tool_results") {
    return "Tool results";
  }

  if (kind === "input") {
    return "Input";
  }

  if (kind === "output") {
    return "Output";
  }

  if (kind === "metadata") {
    return "Metadata";
  }

  if (second) {
    const tail = [second, third, ...rest].filter(Boolean).join(".");
    return `${toTitleCase(kind)}: ${tail}`;
  }

  return toTitleCase(kind);
}

export function formatParserNodeName(
  nodeName: string | null | undefined,
  options?: { multiline?: boolean },
): string | null {
  const parsed = parseParserNodeName(nodeName);
  if (!parsed) {
    return null;
  }

  const turnLabel = `Turn ${parsed.turn}`;
  const suffixLabel = getParserSuffixLabel(parsed.suffixSegments);
  const separator = options?.multiline === false ? " - " : "\n";
  return `${turnLabel}${separator}${suffixLabel}`;
}
