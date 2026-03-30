import {
  type GraphCanvasData,
  type GraphNodeData,
  type AgentGraphDataResponse,
  LANGGRAPH_START_NODE_NAME,
  LANGGRAPH_END_NODE_NAME,
  LANGFUSE_START_NODE_NAME,
  LANGFUSE_END_NODE_NAME,
} from "./types";
import {
  normalizeParserNodeNameForGraph,
  normalizeToolResultNodeName,
  parseParserNodeName,
} from "./nodeNameUtils";

export interface GraphParseResult {
  graph: GraphCanvasData;
  nodeToObservationsMap: Record<string, string[]>;
}

type ObservationMetadataById = Record<
  string,
  Record<string, unknown> | null | undefined
>;

const SESSION_TURN_HIERARCHY_NODE_RE = /^session\.turn\.(?<turn>\d+)$/;
const TRACE_START_NODE_NAME = "session.trace.start";
const TOOL_NODE_WITH_INDEX_RE = /^(?<toolName>[^.]+)\.(?<index>\d+)$/;
const PARSER_TOOL_RESULT_NODE_RE =
  /^parser\.(?<toolName>[^.]+)\.(?<index>\d+)$/;
const PARSER_TOOL_PRE_NODE_RE =
  /^parser\.pre_(?<toolName>[^.]+)\.(?<index>\d+)$/;
const PARSER_TURN_TOOL_NODE_RE =
  /^parser\.turn_\d+\.tool_(?:result|call)\.(?<toolName>[^.]+)\.(?<index>\d+)$/;
const PARSER_TURN_TOOL_PRE_NODE_RE =
  /^parser\.turn_\d+\.pre_(?<toolName>[^.]+)\.(?<index>\d+)$/;

export function transformLanggraphToGeneralized(
  data: AgentGraphDataResponse[],
): AgentGraphDataResponse[] {
  // can't draw nodes without `node` property set for LangGraph
  const filteredData = data.filter(
    (obs) => obs.node && obs.node.trim().length > 0,
  );

  const transformedData = filteredData.map((obs) => {
    const normalizedNodeName =
      normalizeToolResultNodeName(obs.node || obs.name) || obs.name;
    let transformedObs = {
      ...obs,
      // fallback to node name if node empty (shouldn't happen!)
      name: normalizedNodeName,
      node: obs.node ? normalizeToolResultNodeName(obs.node) || obs.node : null,
    };

    // Transform system nodes to Langfuse system nodes
    if (obs.node === LANGGRAPH_START_NODE_NAME) {
      transformedObs.name = LANGFUSE_START_NODE_NAME;
      transformedObs.id = LANGFUSE_START_NODE_NAME;
    } else if (obs.node === LANGGRAPH_END_NODE_NAME) {
      transformedObs.name = LANGFUSE_END_NODE_NAME;
      transformedObs.id = LANGFUSE_END_NODE_NAME;
    }

    return transformedObs;
  });

  // Add Langfuse system nodes if they don't exist
  const hasStartNode = transformedData.some(
    (obs) => obs.name === LANGFUSE_START_NODE_NAME,
  );
  const hasEndNode = transformedData.some(
    (obs) => obs.name === LANGFUSE_END_NODE_NAME,
  );

  const systemNodes: AgentGraphDataResponse[] = [];

  if (!hasStartNode) {
    // Find the top-level parent for system node mapping
    const topLevelObs = transformedData.find((obs) => !obs.parentObservationId);
    systemNodes.push({
      id: LANGFUSE_START_NODE_NAME,
      name: LANGFUSE_START_NODE_NAME,
      node: LANGFUSE_START_NODE_NAME,
      step: 0,
      parentObservationId: topLevelObs?.parentObservationId || null,
      startTime: new Date().toISOString(),
      endTime: new Date().toISOString(),
      observationType: "LANGGRAPH_SYSTEM",
    });
  }

  if (!hasEndNode) {
    const topLevelObs = transformedData.find((obs) => !obs.parentObservationId);
    const maxStep = Math.max(...transformedData.map((obs) => obs.step || 0));
    systemNodes.push({
      id: LANGFUSE_END_NODE_NAME,
      name: LANGFUSE_END_NODE_NAME,
      node: LANGFUSE_END_NODE_NAME,
      step: maxStep + 1,
      parentObservationId: topLevelObs?.parentObservationId || null,
      startTime: new Date().toISOString(),
      endTime: new Date().toISOString(),
      observationType: "LANGGRAPH_SYSTEM",
    });
  }

  return [...transformedData, ...systemNodes];
}

export function buildGraphFromStepData(
  data: AgentGraphDataResponse[],
): GraphParseResult {
  if (data.length === 0) {
    return {
      graph: { nodes: [], edges: [] },
      nodeToObservationsMap: {},
    };
  }

  // Give session.failure nodes stable unique names for graph display/mapping.
  // This avoids collapsing multiple failures into one node, and ensures failures
  // show up even if metadata-based agent_graph_node points elsewhere.
  const failureNodeNameByObservationId = new Map<string, string>();
  const failureObservations = [...data]
    .filter((o) => o.name === "session.failure")
    .sort((a, b) => {
      const t =
        new Date(a.startTime).getTime() - new Date(b.startTime).getTime();
      if (t !== 0) return t;
      return a.id.localeCompare(b.id);
    });
  failureObservations.forEach((o, idx) => {
    failureNodeNameByObservationId.set(o.id, `session.failure.${idx + 1}`);
  });

  const getEffectiveRawNodeName = (
    obs: AgentGraphDataResponse,
  ): string | null => {
    const overriddenFailure = failureNodeNameByObservationId.get(obs.id);
    if (overriddenFailure) {
      return overriddenFailure;
    }
    return obs.node;
  };

  const stepToNodesMap = new Map<number, Set<string>>();
  const nodeToObservationsMap = new Map<string, string[]>();

  data.forEach((obs) => {
    const step = obs.step;
    const node = getEffectiveRawNodeName(obs);

    if (step !== null && node !== null) {
      if (!stepToNodesMap.has(step)) {
        stepToNodesMap.set(step, new Set());
      }
      stepToNodesMap.get(step)!.add(node);
    }

    if (node !== null) {
      const isSystemNode =
        node === LANGFUSE_START_NODE_NAME ||
        node === LANGFUSE_END_NODE_NAME ||
        node === LANGGRAPH_START_NODE_NAME ||
        node === LANGGRAPH_END_NODE_NAME;

      if (!isSystemNode) {
        if (!nodeToObservationsMap.has(node)) {
          nodeToObservationsMap.set(node, []);
        }
        nodeToObservationsMap.get(node)!.push(obs.id);
      }
    }
  });

  const parserNodesToPrune = getRedundantParserNodes(stepToNodesMap);
  if (parserNodesToPrune.size > 0) {
    for (const [step, nodesAtStep] of stepToNodesMap.entries()) {
      const remainingNodes = new Set(
        Array.from(nodesAtStep).filter((node) => !parserNodesToPrune.has(node)),
      );

      if (remainingNodes.size === 0) {
        stepToNodesMap.delete(step);
      } else {
        stepToNodesMap.set(step, remainingNodes);
      }
    }

    parserNodesToPrune.forEach((nodeName) => {
      nodeToObservationsMap.delete(nodeName);
    });
  }

  // Normalize internal parser node names for graph display (ids/edges/map keys),
  // while ensuring each normalized node appears only once in the hierarchy.
  const normalizedToRawNodeName = new Map<string, string>();
  const normalizedNodeToMinStep = new Map<string, number>();
  for (const [step, nodesAtStep] of stepToNodesMap.entries()) {
    for (const rawNodeName of nodesAtStep) {
      const normalizedNodeName =
        normalizeParserNodeNameForGraph(rawNodeName) ?? rawNodeName;
      const existing = normalizedNodeToMinStep.get(normalizedNodeName);
      if (existing === undefined || step < existing) {
        normalizedNodeToMinStep.set(normalizedNodeName, step);
      }
      if (!normalizedToRawNodeName.has(normalizedNodeName)) {
        normalizedToRawNodeName.set(normalizedNodeName, rawNodeName);
      }
    }
  }

  const normalizedStepToNodesMap = new Map<number, Set<string>>();
  for (const [normalizedNodeName, step] of normalizedNodeToMinStep.entries()) {
    if (!normalizedStepToNodesMap.has(step)) {
      normalizedStepToNodesMap.set(step, new Set());
    }
    normalizedStepToNodesMap.get(step)!.add(normalizedNodeName);
  }

  const normalizedNodeToObservationsMap = new Map<string, string[]>();
  for (const [rawNodeName, observationIds] of nodeToObservationsMap.entries()) {
    const normalizedNodeName =
      normalizeParserNodeNameForGraph(rawNodeName) ?? rawNodeName;
    const existing = normalizedNodeToObservationsMap.get(normalizedNodeName);
    if (existing) {
      existing.push(...observationIds);
    } else {
      normalizedNodeToObservationsMap.set(normalizedNodeName, [
        ...observationIds,
      ]);
    }
    if (!normalizedToRawNodeName.has(normalizedNodeName)) {
      normalizedToRawNodeName.set(normalizedNodeName, rawNodeName);
    }
  }

  // Build nodes from step mapping
  const nodeNames = [
    ...new Set([
      LANGFUSE_START_NODE_NAME,
      ...Array.from(normalizedNodeToObservationsMap.keys()),
      LANGFUSE_END_NODE_NAME,
    ]),
  ];

  const dataById = new Map(data.map((o) => [o.id, o]));

  const nodes: GraphNodeData[] = nodeNames.map((nodeName) => {
    if (
      nodeName === LANGFUSE_END_NODE_NAME ||
      nodeName === LANGFUSE_START_NODE_NAME
    ) {
      return {
        id: nodeName,
        label: nodeName,
        type: "LANGGRAPH_SYSTEM",
        level: null,
      };
    }
    const isParserNode = nodeName.startsWith("parser.");
    const obsIds = normalizedNodeToObservationsMap.get(nodeName) ?? [];
    const observations = obsIds.map((id) => dataById.get(id)).filter(Boolean);
    const obs = observations[0];

    const severityRank = (level?: string | null) => {
      if (level === "POLICY_VIOLATION") return 4;
      if (level === "ERROR") return 3;
      if (level === "WARNING") return 2;
      if (level === "DEFAULT") return 1;
      if (level === "DEBUG") return 0;
      return -1;
    };
    const nodeLevel =
      observations
        .map((o) => o?.level)
        .sort((a, b) => severityRank(b) - severityRank(a))[0] ?? null;

    const firstErrorStatus =
      observations.find((o) => o?.level === "POLICY_VIOLATION")
        ?.statusMessage ??
      observations.find((o) => o?.level === "ERROR")?.statusMessage ??
      observations.find((o) => o?.level === "WARNING")?.statusMessage ??
      null;
    return {
      id: nodeName,
      label: nodeName,
      type: isParserNode ? "PARSER" : obs?.observationType || "UNKNOWN",
      title: firstErrorStatus ?? undefined,
      level: nodeLevel,
    };
  });

  // Compute UI-aligned parent relationships to avoid "self edges" and keep the graph
  // consistent with the trace2 tree (node names + parent-child expectations).
  const forcedParentByNodeName = new Map<string, string>();
  const nodeExists = (name: string) =>
    normalizedNodeToObservationsMap.has(name);

  const obsChrono = data
    .filter((o) => o.step !== null)
    .filter((o) => {
      const rawNodeName = getEffectiveRawNodeName(o);
      return rawNodeName ? !parserNodesToPrune.has(rawNodeName) : false;
    })
    .map((o) => {
      const rawNodeName = getEffectiveRawNodeName(o)!;
      const normalizedNodeName =
        normalizeParserNodeNameForGraph(rawNodeName) ?? rawNodeName;
      return {
        name: o.name,
        normalizedNodeName,
        startMs: new Date(o.startTime).getTime(),
        endMs: o.endTime ? new Date(o.endTime).getTime() : null,
      };
    })
    .sort((a, b) => a.startMs - b.startMs);

  const SESSION_TURN_NODE_RE = /^session\.turn\.(?<turn>\d+)$/;
  const sessionTurns = obsChrono
    .map((o) => {
      if (!SESSION_TURN_NODE_RE.test(o.normalizedNodeName)) return null;
      return {
        nodeName: o.normalizedNodeName,
        start: o.startMs,
        end: o.endMs,
      };
    })
    .filter((t) => t !== null)
    .sort((a, b) => a.start - b.start);

  const sessionTurnEndBounds = sessionTurns.map((turn, idx) => {
    // Turns often have an endTime earlier than their last "logical" children.
    // For graph readability/alignment, prefer the next turn's start as the boundary.
    const next = sessionTurns[idx + 1];
    return next ? next.start : Number.POSITIVE_INFINITY;
  });

  const TOOL_RESULT_UI_NODE_RE = /^parser\.(?<toolName>[^.]+)\.(?<index>\d+)$/;
  const TOOL_PRE_UI_NODE_RE = /^parser\.pre_(?<toolName>[^.]+)\.(?<index>\d+)$/;
  const STRUCTURED_OUTPUT_UI_NODE_RE = /^parser\.turn_\d+\.structured_output$/;
  const isKernelLikeObservation = (params: {
    observationName: string | null | undefined;
    normalizedNodeName: string;
  }) => {
    const { observationName, normalizedNodeName } = params;
    const rawName = typeof observationName === "string" ? observationName : "";
    return (
      rawName.includes(" - kernel.") ||
      rawName.includes("kernel.") ||
      normalizedNodeName.includes("kernel.")
    );
  };

  let latestKernelNodeName: string | null = null;
  let activeTurnIndex = 0;
  const latestPreByToolNodeName = new Map<string, string>();

  for (const o of obsChrono) {
    // Group non-parser nodes under session.turn.xxx (node-level).
    while (
      activeTurnIndex < sessionTurns.length &&
      o.startMs >= sessionTurnEndBounds[activeTurnIndex]!
    ) {
      activeTurnIndex++;
    }
    const activeTurn = sessionTurns[activeTurnIndex];
    const activeTurnEndBound = sessionTurnEndBounds[activeTurnIndex];
    if (
      activeTurn &&
      o.normalizedNodeName !== activeTurn.nodeName &&
      !o.normalizedNodeName.startsWith("parser.") &&
      o.startMs >= activeTurn.start &&
      o.startMs < (activeTurnEndBound ?? Number.POSITIVE_INFINITY) &&
      nodeExists(activeTurn.nodeName) &&
      nodeExists(o.normalizedNodeName) &&
      !forcedParentByNodeName.has(o.normalizedNodeName)
    ) {
      forcedParentByNodeName.set(o.normalizedNodeName, activeTurn.nodeName);
    }

    // Attach parser tool_result nodes under tool_name.{n}
    const toolResultMatch = TOOL_RESULT_UI_NODE_RE.exec(o.normalizedNodeName);
    if (toolResultMatch?.groups?.toolName && toolResultMatch.groups.index) {
      const targetParentName = `${toolResultMatch.groups.toolName}.${toolResultMatch.groups.index}`;
      if (nodeExists(o.normalizedNodeName) && nodeExists(targetParentName)) {
        forcedParentByNodeName.set(o.normalizedNodeName, targetParentName);
      }
    }

    // Attach tool_name.{n} under parser.pre_tool_name.{n} when present.
    // This mirrors trace2 tree-building UI semantics for pre-tool parser nodes.
    const toolPreMatch = TOOL_PRE_UI_NODE_RE.exec(o.normalizedNodeName);
    if (toolPreMatch?.groups?.toolName && toolPreMatch.groups.index) {
      const toolNodeName = `${toolPreMatch.groups.toolName}.${toolPreMatch.groups.index}`;
      latestPreByToolNodeName.set(toolNodeName, o.normalizedNodeName);
      if (
        activeTurn &&
        nodeExists(activeTurn.nodeName) &&
        nodeExists(o.normalizedNodeName)
      ) {
        forcedParentByNodeName.set(o.normalizedNodeName, activeTurn.nodeName);
      }
    }
    if (
      !o.normalizedNodeName.startsWith("parser.") &&
      nodeExists(o.normalizedNodeName) &&
      latestPreByToolNodeName.has(o.normalizedNodeName)
    ) {
      const preNodeName = latestPreByToolNodeName.get(o.normalizedNodeName);
      if (preNodeName && nodeExists(preNodeName)) {
        forcedParentByNodeName.set(o.normalizedNodeName, preNodeName);
      }
    }

    // Track kernel nodes (topic - kernel.xxx)
    if (
      isKernelLikeObservation({
        observationName: o.name,
        normalizedNodeName: o.normalizedNodeName,
      })
    ) {
      if (nodeExists(o.normalizedNodeName)) {
        latestKernelNodeName = o.normalizedNodeName;
      }
    }

    // Attach structured_output nodes under latest kernel node
    if (
      STRUCTURED_OUTPUT_UI_NODE_RE.test(o.normalizedNodeName) &&
      latestKernelNodeName &&
      nodeExists(o.normalizedNodeName) &&
      nodeExists(latestKernelNodeName)
    ) {
      forcedParentByNodeName.set(o.normalizedNodeName, latestKernelNodeName);
    }
  }

  const edgesSet = new Set<string>();
  const addEdge = (from: string, to: string) => {
    if (!from || !to || from === to) return;
    if (to === LANGFUSE_START_NODE_NAME) return;
    if (from === LANGFUSE_END_NODE_NAME) return;
    // Parser-related nodes are UI-details; never allow outgoing edges from them,
    // except for parser.pre_* nodes which act as "pre tool call" parents.
    if (from.startsWith("parser.") && !from.startsWith("parser.pre_")) return;
    edgesSet.add(`${from}→${to}`);
  };

  // If we have session turn nodes, build a clean, readable graph aligned with the
  // trace2 tree expectations:
  // - A single main chain of non-parser nodes per turn (ordered by time)
  // - Parser nodes are leaf details (incoming edges only, no outgoing edges)
  // - Only the global last main-chain node connects to __end__
  if (sessionTurns.length > 0) {
    const byTurnNodeName = new Map<
      string,
      Map<string, { firstStartMs: number }>
    >();
    sessionTurns.forEach((t) => {
      byTurnNodeName.set(
        t.nodeName,
        new Map([[t.nodeName, { firstStartMs: t.start }]]),
      );
    });

    // Track latest kernel node per turn window for structured_output attachment.
    const latestKernelByTurn = new Map<string, string>();
    let activeIdx = 0;
    for (const o of obsChrono) {
      while (
        activeIdx < sessionTurns.length &&
        o.startMs >= sessionTurnEndBounds[activeIdx]!
      ) {
        activeIdx++;
      }
      const activeTurn = sessionTurns[activeIdx];
      const activeTurnEndBound = sessionTurnEndBounds[activeIdx];
      if (
        !activeTurn ||
        o.startMs < activeTurn.start ||
        o.startMs >= (activeTurnEndBound ?? Number.POSITIVE_INFINITY)
      ) {
        continue;
      }

      // Record kernel nodes for this turn.
      if (
        isKernelLikeObservation({
          observationName: o.name,
          normalizedNodeName: o.normalizedNodeName,
        })
      ) {
        latestKernelByTurn.set(activeTurn.nodeName, o.normalizedNodeName);
      }

      // Build per-turn main-chain candidate nodes (non-parser only).
      const isMainChainCandidate =
        nodeExists(o.normalizedNodeName) &&
        o.normalizedNodeName !== TRACE_START_NODE_NAME &&
        (!o.normalizedNodeName.startsWith("parser.") ||
          o.normalizedNodeName.startsWith("parser.pre_"));
      if (isMainChainCandidate) {
        const map = byTurnNodeName.get(activeTurn.nodeName);
        if (!map) continue;

        // If we have a parser.pre_{tool}.{n} node, treat it as the main-chain node
        // and keep the actual tool node as a child/leaf detail node.
        const toolNodeMatch = /^(?<toolName>[^.]+)\.(?<index>\d+)$/.exec(
          o.normalizedNodeName,
        );
        if (toolNodeMatch?.groups?.toolName && toolNodeMatch.groups.index) {
          const preNodeName = `parser.pre_${toolNodeMatch.groups.toolName}.${toolNodeMatch.groups.index}`;
          if (nodeExists(preNodeName)) {
            if (!map.has(preNodeName)) {
              map.set(preNodeName, { firstStartMs: o.startMs });
            }
            continue;
          }
        }

        if (!map.has(o.normalizedNodeName)) {
          map.set(o.normalizedNodeName, { firstStartMs: o.startMs });
        }
      }
    }

    // Create main chain edges per turn.
    const turnChains: Array<{ turnNode: string; chain: string[] }> = [];
    for (const turn of sessionTurns) {
      const nodeMap = byTurnNodeName.get(turn.nodeName);
      if (!nodeMap) continue;
      const chain = Array.from(nodeMap.entries())
        .map(([name, meta]) => ({ name, firstStartMs: meta.firstStartMs }))
        .sort((a, b) => a.firstStartMs - b.firstStartMs)
        .map((x) => x.name);

      // Ensure the turn node is first if present.
      if (chain.includes(turn.nodeName)) {
        const without = chain.filter((n) => n !== turn.nodeName);
        turnChains.push({
          turnNode: turn.nodeName,
          chain: [turn.nodeName, ...without],
        });
      } else {
        turnChains.push({ turnNode: turn.nodeName, chain });
      }
    }

    // Connect sequentially inside each turn.
    for (const { chain } of turnChains) {
      for (let i = 0; i < chain.length - 1; i++) {
        addEdge(chain[i]!, chain[i + 1]!);
      }
    }

    // Connect turns: last node of previous turn → next session.turn.xxx node.
    for (let i = 0; i < turnChains.length - 1; i++) {
      const prev = turnChains[i]!;
      const next = turnChains[i + 1]!;
      const prevLast = prev.chain[prev.chain.length - 1];
      if (prevLast) {
        addEdge(prevLast, next.turnNode);
      }
    }

    // Attach parser nodes:
    // - parser.<tool>.<n> (tool_result) → <tool>.<n>
    // - parser.turn_XXX.tool_call.<tool>.<n> → <tool>.<n>
    // - parser.pre_<tool>.<n> → <tool>.<n>  (pre-tool parser node is parent)
    // - parser.turn_XXX.structured_output → latest kernel node for that turn
    const TOOL_CALL_UI_NODE_RE =
      /^parser\.turn_\d+\.tool_call\.(?<toolName>[^.]+)\.(?<index>\d+)$/;
    const TOOL_PRE_UI_NODE_RE =
      /^parser\.pre_(?<toolName>[^.]+)\.(?<index>\d+)$/;
    const SESSION_OUTPUT_NODE_RE = /^session\.output\.turn_(?<turn>\d+)$/;

    // Find which turn a node belongs to by time window (based on first occurrence).
    const nodeFirstStart = new Map<string, number>();
    obsChrono.forEach((o) => {
      const existing = nodeFirstStart.get(o.normalizedNodeName);
      if (existing === undefined || o.startMs < existing) {
        nodeFirstStart.set(o.normalizedNodeName, o.startMs);
      }
    });
    const findTurnForNode = (nodeName: string): string | null => {
      const ts = nodeFirstStart.get(nodeName);
      if (ts === undefined) return null;
      for (let i = 0; i < sessionTurns.length; i++) {
        const t = sessionTurns[i]!;
        const endBound = sessionTurnEndBounds[i] ?? Number.POSITIVE_INFINITY;
        if (ts >= t.start && ts < endBound) return t.nodeName;
      }
      return null;
    };

    for (const nodeName of normalizedNodeToObservationsMap.keys()) {
      if (!nodeName.startsWith("parser.")) continue;

      const toolPreMatch = TOOL_PRE_UI_NODE_RE.exec(nodeName);
      if (toolPreMatch?.groups?.toolName && toolPreMatch.groups.index) {
        const toolNode = `${toolPreMatch.groups.toolName}.${toolPreMatch.groups.index}`;
        if (nodeExists(toolNode)) {
          addEdge(nodeName, toolNode);
        }
        continue;
      }

      const toolResultMatch = TOOL_RESULT_UI_NODE_RE.exec(nodeName);
      if (toolResultMatch?.groups?.toolName && toolResultMatch.groups.index) {
        const parent = `${toolResultMatch.groups.toolName}.${toolResultMatch.groups.index}`;
        if (nodeExists(parent)) addEdge(parent, nodeName);
        continue;
      }

      const toolCallMatch = TOOL_CALL_UI_NODE_RE.exec(nodeName);
      if (toolCallMatch?.groups?.toolName && toolCallMatch.groups.index) {
        const parent = `${toolCallMatch.groups.toolName}.${toolCallMatch.groups.index}`;
        if (nodeExists(parent)) addEdge(parent, nodeName);
        continue;
      }

      if (STRUCTURED_OUTPUT_UI_NODE_RE.test(nodeName)) {
        const turnNode = findTurnForNode(nodeName);
        const kernelNode =
          (turnNode ? latestKernelByTurn.get(turnNode) : null) ?? null;
        if (kernelNode && nodeExists(kernelNode)) {
          addEdge(kernelNode, nodeName);
        }
        continue;
      }
    }

    // Connect __start__ to the first node of the first turn.
    const firstTurnMainNode = turnChains.find((t) => t.chain.length > 0)
      ?.chain[0];

    // Prefer a dedicated trace-start node if present; otherwise fall back to the first turn node.
    // This avoids `session.trace.start` becoming an isolated node in some traces.
    const traceStartNode = nodeExists(TRACE_START_NODE_NAME)
      ? TRACE_START_NODE_NAME
      : null;

    const startAnchor = traceStartNode ?? firstTurnMainNode ?? null;
    if (startAnchor) {
      addEdge(LANGFUSE_START_NODE_NAME, startAnchor);
    }

    if (
      traceStartNode &&
      firstTurnMainNode &&
      traceStartNode !== firstTurnMainNode
    ) {
      addEdge(traceStartNode, firstTurnMainNode);
    }

    // Only the global last main-chain node connects to __end__.
    const lastMainNode = [...turnChains]
      .reverse()
      .find((t) => t.chain.length > 0)
      ?.chain.slice(-1)[0];
    if (lastMainNode) {
      addEdge(lastMainNode, LANGFUSE_END_NODE_NAME);
    }

    // Cleanup: remove any edges into __end__ that are not from the last main node.
    if (lastMainNode) {
      for (const key of Array.from(edgesSet)) {
        const [from, to] = key.split("→");
        if (to === LANGFUSE_END_NODE_NAME && from !== lastMainNode) {
          edgesSet.delete(key);
        }
      }
    }
  } else {
    // Fallback: no turn structure available → keep step-based rendering, but still:
    // - prevent outgoing edges from parser nodes
    // - avoid a noisy fan-out into __end__ (only last-step nodes connect to __end__)
    const stepEdges = generateEdgesWithParallelBranches(
      normalizedStepToNodesMap,
    );
    stepEdges.forEach(({ from, to }) => addEdge(from, to));

    forcedParentByNodeName.forEach((parent, child) => addEdge(parent, child));
  }

  const edges = Array.from(edgesSet).map((key) => {
    const [from, to] = key.split("→");
    return { from: from!, to: to! };
  });

  return {
    graph: { nodes, edges },
    nodeToObservationsMap: Object.fromEntries(
      normalizedNodeToObservationsMap.entries(),
    ),
  };
}

export function buildHierarchyGraphFromStepData(params: {
  data: AgentGraphDataResponse[];
  observationMetadataById?: ObservationMetadataById;
}): GraphParseResult {
  const { data, observationMetadataById = {} } = params;
  if (data.length === 0) {
    return {
      graph: { nodes: [], edges: [] },
      nodeToObservationsMap: {},
    };
  }

  const fallbackGraph = buildGraphFromStepData(data);
  const observationsById = new Map(data.map((obs) => [obs.id, obs]));

  const chronologicalObservations = [...data]
    .map((obs) => ({
      ...obs,
      startMs: new Date(obs.startTime).getTime(),
    }))
    .sort((a, b) => {
      if (a.startMs !== b.startMs) return a.startMs - b.startMs;
      return a.id.localeCompare(b.id);
    });

  const sessionTurns = chronologicalObservations
    .map((obs) => {
      const nodeName = obs.node ?? obs.name;
      const match = SESSION_TURN_HIERARCHY_NODE_RE.exec(nodeName);
      if (!match?.groups?.turn) return null;
      return {
        nodeName,
        turnNumber: Number.parseInt(match.groups.turn, 10),
        startMs: obs.startMs,
      };
    })
    .filter((turn): turn is NonNullable<typeof turn> => turn !== null);

  const uniqueTurnsByNodeName = new Map<
    string,
    { nodeName: string; turnNumber: number; startMs: number }
  >();
  for (const turn of sessionTurns) {
    const existing = uniqueTurnsByNodeName.get(turn.nodeName);
    if (!existing || turn.startMs < existing.startMs) {
      uniqueTurnsByNodeName.set(turn.nodeName, turn);
    }
  }
  const uniqueSessionTurns = Array.from(uniqueTurnsByNodeName.values()).sort(
    (a, b) =>
      a.startMs - b.startMs ||
      a.turnNumber - b.turnNumber ||
      a.nodeName.localeCompare(b.nodeName),
  );

  if (uniqueSessionTurns.length === 0) {
    return fallbackGraph;
  }

  const turnWindows = uniqueSessionTurns.map((turn, index) => ({
    ...turn,
    endMs: uniqueSessionTurns[index + 1]?.startMs ?? Number.POSITIVE_INFINITY,
  }));

  const nodeToObservationsMap = new Map<string, string[]>();
  const nodeToObservationIdsSetMap = new Map<string, Set<string>>();
  turnWindows.forEach((turn) => {
    nodeToObservationsMap.set(turn.nodeName, []);
    nodeToObservationIdsSetMap.set(turn.nodeName, new Set<string>());
  });

  let currentTurnIndex = 0;
  for (const observation of chronologicalObservations) {
    const observationNodeName = observation.node ?? observation.name;
    if (isSystemNodeName(observationNodeName)) continue;

    while (
      currentTurnIndex < turnWindows.length - 1 &&
      observation.startMs >= turnWindows[currentTurnIndex]!.endMs
    ) {
      currentTurnIndex++;
    }

    const activeTurn = turnWindows[currentTurnIndex];
    if (!activeTurn) continue;

    if (observation.startMs < activeTurn.startMs) {
      continue;
    }

    const observationIdsForTurn = nodeToObservationsMap.get(
      activeTurn.nodeName,
    );
    const observationIdsSetForTurn = nodeToObservationIdsSetMap.get(
      activeTurn.nodeName,
    );
    if (
      observationIdsForTurn &&
      observationIdsSetForTurn &&
      !observationIdsSetForTurn.has(observation.id)
    ) {
      observationIdsSetForTurn.add(observation.id);
      observationIdsForTurn.push(observation.id);
    }
  }

  const nodes: GraphNodeData[] = [
    {
      id: LANGFUSE_START_NODE_NAME,
      label: LANGFUSE_START_NODE_NAME,
      type: "LANGGRAPH_SYSTEM",
      level: null,
    },
  ];
  const edges: Array<{ from: string; to: string }> = [];
  const resultNodeIdByTurnNodeName = new Map<string, string>();

  const getMetadata = (observationId: string) =>
    observationMetadataById[observationId] ?? null;

  const getMetadataString = (params: {
    metadata: Record<string, unknown> | null;
    candidateKeys: string[][];
  }) =>
    getFirstStringValue({
      metadata: params.metadata,
      candidateKeys: params.candidateKeys,
    });

  const getMetadataBoolean = (params: {
    metadata: Record<string, unknown> | null;
    candidateKeys: string[][];
  }) =>
    getFirstBooleanValue({
      metadata: params.metadata,
      candidateKeys: params.candidateKeys,
    });

  const getMetadataToolName = (metadata: Record<string, unknown> | null) =>
    getMetadataString({
      metadata,
      candidateKeys: [["tool_name"], ["toolName"]],
    });

  const getMetadataNodeType = (metadata: Record<string, unknown> | null) =>
    getMetadataString({
      metadata,
      candidateKeys: [["node_type"], ["nodeType"]],
    });
  const isKernelObservation = (params: {
    observation: AgentGraphDataResponse | undefined;
    metadata: Record<string, unknown> | null;
  }) => {
    const { observation, metadata } = params;
    const observationName =
      typeof observation?.name === "string" ? observation.name : null;
    const metadataNodeType = getMetadataNodeType(metadata);
    return (
      metadataNodeType === "kernel_step" ||
      (typeof observationName === "string" &&
        (observationName.includes(" - kernel.") ||
          observationName.includes("kernel.")))
    );
  };

  for (const turnWindow of turnWindows) {
    const observationIds = nodeToObservationsMap.get(turnWindow.nodeName) ?? [];
    const turnMetadataSummary = buildTurnMetadataSummary({
      observationIds,
      observationsById,
      observationMetadataById,
    });

    const observationCount =
      turnMetadataSummary.observationCount ?? observationIds.length;
    const toolCount = turnMetadataSummary.toolCount ?? 0;
    const errorCount = turnMetadataSummary.errorCount ?? 0;
    const warningCount = turnMetadataSummary.warningCount ?? 0;
    const policyViolationCount = turnMetadataSummary.policyViolationCount ?? 0;
    const parserInconsistencyCount =
      turnMetadataSummary.parserInconsistencyCount ?? 0;
    const hasPolicyBlock = turnMetadataSummary.policy?.hasBlock ?? false;

    const level =
      errorCount > 0 || policyViolationCount > 0 || hasPolicyBlock
        ? "ERROR"
        : warningCount > 0 || parserInconsistencyCount > 0
          ? "WARNING"
          : null;

    const title = [
      `Turn ${turnWindow.turnNumber}`,
      turnMetadataSummary.topic ? `Topic: ${turnMetadataSummary.topic}` : null,
      turnMetadataSummary.category
        ? `Category: ${turnMetadataSummary.category}`
        : null,
      turnMetadataSummary.instructionType
        ? `Instruction Type: ${turnMetadataSummary.instructionType}`
        : null,
      turnMetadataSummary.policy?.authorityLabel
        ? `Authority: ${turnMetadataSummary.policy.authorityLabel}`
        : null,
      hasPolicyBlock ? "Policy: BLOCK" : null,
      turnMetadataSummary.durationMs != null
        ? `Duration: ${formatDurationMs(turnMetadataSummary.durationMs)}`
        : null,
      `Observations: ${observationCount}`,
      `Tool nodes: ${toolCount}`,
      errorCount > 0 ? `Errors: ${errorCount}` : null,
      warningCount > 0 ? `Warnings: ${warningCount}` : null,
      policyViolationCount > 0
        ? `Policy violations: ${policyViolationCount}`
        : null,
      parserInconsistencyCount > 0
        ? `Parser consistency issues: ${parserInconsistencyCount}`
        : null,
    ]
      .filter(Boolean)
      .join("\n");

    nodes.push({
      id: turnWindow.nodeName,
      label: turnMetadataSummary.topic
        ? `Turn ${turnWindow.turnNumber}\n${truncateLabel(turnMetadataSummary.topic, 40)}`
        : `Turn ${turnWindow.turnNumber}`,
      type: "AGENT",
      title,
      metadataSummary: turnMetadataSummary,
      level,
    });

    // Tools summary + per-tool nodes
    const toolObservationIds = observationIds.filter((id) => {
      const obs = observationsById.get(id);
      const metadata = getMetadata(id);
      const observationNodeName = obs?.node ?? obs?.name;
      if (isParserObservationNodeName(observationNodeName)) {
        return false;
      }
      const toolInvocation =
        extractToolInvocationFromNodeName(observationNodeName);
      const toolName =
        obs?.toolName ??
        getMetadataToolName(metadata) ??
        toolInvocation?.toolName;
      return Boolean(toolName && obs?.observationType === "TOOL");
    });

    if (toolCount > 0) {
      const toolsNodeId = `${turnWindow.nodeName}::tools`;
      nodes.push({
        id: toolsNodeId,
        label: `Tools\n${toolCount}`,
        type: "TOOLS",
        level: null,
        title: [
          `Tool nodes: ${toolCount}`,
          turnMetadataSummary.toolBreakdown?.length
            ? `Unique tools: ${turnMetadataSummary.toolBreakdown.length}`
            : null,
        ]
          .filter(Boolean)
          .join("\n"),
        metadataSummary: {
          toolCount,
          toolBreakdown: turnMetadataSummary.toolBreakdown ?? [],
        },
      });
      edges.push({ from: turnWindow.nodeName, to: toolsNodeId });
      nodeToObservationsMap.set(toolsNodeId, toolObservationIds);

      const breakdown = [...(turnMetadataSummary.toolBreakdown ?? [])].sort(
        (a, b) => b.count - a.count || a.toolName.localeCompare(b.toolName),
      );
      const MAX_TOOLS = 10;
      const shown = breakdown.slice(0, MAX_TOOLS);
      const hidden = breakdown.slice(MAX_TOOLS);

      for (const tool of shown) {
        const toolNodeId = `${turnWindow.nodeName}::tool::${tool.toolName}`;
        nodes.push({
          id: toolNodeId,
          label: `${truncateLabel(tool.toolName, 26)}\n×${tool.count}`,
          type: "TOOL",
          level: tool.hasBlock ? "ERROR" : null,
          title: [
            `Tool: ${tool.toolName}`,
            `Count: ${tool.count}`,
            tool.instructionTypes?.length
              ? `Instruction Types: ${tool.instructionTypes.join(", ")}`
              : null,
            tool.hasBlock ? "Policy: BLOCK present" : null,
          ]
            .filter(Boolean)
            .join("\n"),
        });
        edges.push({ from: toolsNodeId, to: toolNodeId });

        const obsIdsForTool = toolObservationIds.filter((id) => {
          const obs = observationsById.get(id);
          const metadata = getMetadata(id);
          const toolName = obs?.toolName ?? getMetadataToolName(metadata);
          return toolName === tool.toolName;
        });
        nodeToObservationsMap.set(toolNodeId, obsIdsForTool);
      }

      if (hidden.length > 0) {
        const otherNodeId = `${turnWindow.nodeName}::tool::(other)`;
        const otherCount = hidden.reduce((sum, t) => sum + t.count, 0);
        nodes.push({
          id: otherNodeId,
          label: `Other tools\n×${otherCount}`,
          type: "TOOL",
          level: null,
          title: `Other tools (hidden): ${hidden
            .map((t) => `${t.toolName}×${t.count}`)
            .join(", ")}`,
        });
        edges.push({ from: toolsNodeId, to: otherNodeId });
        nodeToObservationsMap.set(otherNodeId, toolObservationIds);
      }
    }

    // Result node: prefer kernel-step observations (with or without topic prefix).
    const kernelObservationIds = observationIds.filter((id) => {
      const observation = observationsById.get(id);
      const metadata = getMetadata(id);
      return isKernelObservation({ observation, metadata });
    });
    const resultObservationId =
      kernelObservationIds[kernelObservationIds.length - 1];
    if (resultObservationId) {
      const resultNodeId = `${turnWindow.nodeName}::result`;
      nodes.push({
        id: resultNodeId,
        label: `Result @ turn${turnWindow.turnNumber}`,
        type: "OUTPUT",
        level: null,
        title: "Result",
      });
      edges.push({ from: turnWindow.nodeName, to: resultNodeId });
      nodeToObservationsMap.set(resultNodeId, [resultObservationId]);
      resultNodeIdByTurnNodeName.set(turnWindow.nodeName, resultNodeId);
    }
  }

  // Connect start/end + turn chain
  const firstTurn = turnWindows[0];
  if (firstTurn) {
    edges.unshift({ from: LANGFUSE_START_NODE_NAME, to: firstTurn.nodeName });
  }
  for (let i = 0; i < turnWindows.length - 1; i++) {
    const currentTurnNodeName = turnWindows[i]!.nodeName;
    const nextTurnNodeName = turnWindows[i + 1]!.nodeName;
    const chainSourceNodeName =
      resultNodeIdByTurnNodeName.get(currentTurnNodeName) ??
      currentTurnNodeName;
    edges.push({
      from: chainSourceNodeName,
      to: nextTurnNodeName,
    });
  }
  const lastTurn = turnWindows[turnWindows.length - 1];
  if (lastTurn) {
    const lastChainSourceNodeName =
      resultNodeIdByTurnNodeName.get(lastTurn.nodeName) ?? lastTurn.nodeName;
    edges.push({
      from: lastChainSourceNodeName,
      to: LANGFUSE_END_NODE_NAME,
    });
  }

  nodes.push({
    id: LANGFUSE_END_NODE_NAME,
    label: LANGFUSE_END_NODE_NAME,
    type: "LANGGRAPH_SYSTEM",
    level: null,
  });

  return {
    graph: { nodes, edges },
    nodeToObservationsMap: Object.fromEntries(nodeToObservationsMap.entries()),
  };
}

function getRedundantParserNodes(stepToNodesMap: Map<number, Set<string>>) {
  const parserNodesByTurn = new Map<
    number,
    { nodeName: string; suffix: string; hasSuffix: boolean }[]
  >();

  stepToNodesMap.forEach((nodesAtStep) => {
    nodesAtStep.forEach((nodeName) => {
      const parsed = parseParserNodeName(nodeName);
      if (!parsed) {
        return;
      }

      const suffix = parsed.suffixSegments.join(".");
      if (!parserNodesByTurn.has(parsed.turn)) {
        parserNodesByTurn.set(parsed.turn, []);
      }

      parserNodesByTurn.get(parsed.turn)!.push({
        nodeName,
        suffix,
        hasSuffix: parsed.suffixSegments.length > 0,
      });
    });
  });

  const nodesToPrune = new Set<string>();

  parserNodesByTurn.forEach((turnNodes) => {
    turnNodes.forEach(({ nodeName, suffix, hasSuffix }) => {
      const isContainerNode =
        !hasSuffix ||
        suffix === "tool_calls" ||
        suffix === "tool_results" ||
        suffix === "tool_call" ||
        suffix === "tool_result" ||
        suffix === "structured_output" ||
        suffix === "strucutured_output";

      if (isContainerNode) {
        nodesToPrune.add(nodeName);
      }
    });
  });

  return nodesToPrune;
}

function generateEdgesWithParallelBranches(
  stepToNodesMap: Map<number, Set<string>>,
) {
  // generate edges with proper parallel branch handling
  const sortedSteps = [...stepToNodesMap.entries()].sort(([a], [b]) => a - b);
  const edges: Array<{ from: string; to: string }> = [];

  sortedSteps.forEach(([, currentNodes], i) => {
    const isLastStep = i === sortedSteps.length - 1;
    const targetNodes = isLastStep
      ? [LANGFUSE_END_NODE_NAME]
      : Array.from(sortedSteps[i + 1][1]);

    // connect all current nodes to all target nodes
    Array.from(currentNodes).forEach((currentNode) => {
      // end nodes should be terminal -> don't draw edges from them
      if (
        currentNode === LANGFUSE_END_NODE_NAME ||
        currentNode === LANGGRAPH_END_NODE_NAME
      ) {
        return;
      }

      targetNodes.forEach((targetNode) => {
        if (currentNode !== targetNode) {
          edges.push({ from: currentNode, to: targetNode });
        }
      });
    });
  });

  return edges;
}

function isSystemNodeName(nodeName: string | null | undefined): boolean {
  if (!nodeName) return false;
  return (
    nodeName === LANGFUSE_START_NODE_NAME ||
    nodeName === LANGFUSE_END_NODE_NAME ||
    nodeName === LANGGRAPH_START_NODE_NAME ||
    nodeName === LANGGRAPH_END_NODE_NAME
  );
}

function isParserObservationNodeName(
  nodeName: string | null | undefined,
): boolean {
  if (!nodeName) return false;
  const normalizedNodeName =
    normalizeParserNodeNameForGraph(nodeName) ?? nodeName;
  return normalizedNodeName.startsWith("parser.");
}

function parseToolInvocationNodeName(
  nodeName: string,
): { toolName: string; invocationKey: string } | null {
  const matchers = [
    TOOL_NODE_WITH_INDEX_RE,
    PARSER_TOOL_RESULT_NODE_RE,
    PARSER_TOOL_PRE_NODE_RE,
    PARSER_TURN_TOOL_NODE_RE,
    PARSER_TURN_TOOL_PRE_NODE_RE,
  ];
  for (const matcher of matchers) {
    const match = matcher.exec(nodeName);
    if (!match?.groups?.toolName || !match.groups.index) continue;
    return {
      toolName: match.groups.toolName,
      invocationKey: `${match.groups.toolName}.${match.groups.index}`,
    };
  }
  return null;
}

function extractToolInvocationFromNodeName(
  nodeName: string | null | undefined,
): { toolName: string; invocationKey: string } | null {
  if (!nodeName) return null;

  const normalizedNodeName =
    normalizeParserNodeNameForGraph(nodeName) ?? nodeName;
  return (
    parseToolInvocationNodeName(normalizedNodeName) ??
    parseToolInvocationNodeName(nodeName)
  );
}

function buildTurnMetadataSummary(params: {
  observationIds: string[];
  observationsById: Map<string, AgentGraphDataResponse>;
  observationMetadataById: ObservationMetadataById;
}) {
  const { observationIds, observationsById, observationMetadataById } = params;

  let topic: string | null = null;
  let core: string | null = null;
  let category: string | null = null;
  let instructionType: string | null = null;
  let instructionCategory: string | null = null;
  let toolCount = 0;
  const toolMetaByName = new Map<
    string,
    { count: number; instructionTypes: Set<string>; hasBlock: boolean }
  >();
  let errorCount = 0;
  let warningCount = 0;
  let policyViolationCount = 0;
  let parserInconsistencyCount = 0;
  let minStartMs = Number.POSITIVE_INFINITY;
  let maxEndMs = Number.NEGATIVE_INFINITY;

  const instructionTypeCounts = new Map<string, number>();
  const policyRuleEffectCounts: Record<string, number> = {};
  const countedToolInvocationKeys = new Set<string>();
  let policyHasBlock = false;
  let policyAuthorityLabel: string | null = null;
  let policyConfidentiality: string | null = null;
  let policyIntegrity: string | null = null;
  let policyTrustworthiness: string | null = null;
  let policyConfidence: number | null = null;
  let policyReversible: boolean | null = null;
  let policyConfidentialityLabel: boolean | null = null;

  for (const observationId of observationIds) {
    const observation = observationsById.get(observationId);
    if (!observation) continue;

    const metadata = observationMetadataById[observationId];
    const startMs = new Date(observation.startTime).getTime();
    const endMs = observation.endTime
      ? new Date(observation.endTime).getTime()
      : startMs;
    minStartMs = Math.min(minStartMs, startMs);
    maxEndMs = Math.max(maxEndMs, endMs);

    const metadataToolName =
      getFirstStringValue({
        metadata,
        candidateKeys: [["tool_name"], ["toolName"]],
      }) ?? null;
    const observationNodeName = observation.node ?? observation.name;
    const isParserObservation =
      isParserObservationNodeName(observationNodeName);
    const toolInvocation =
      extractToolInvocationFromNodeName(observationNodeName);
    const toolName =
      toolInvocation?.toolName ??
      observation.toolName ??
      metadataToolName ??
      null;
    const isToolObservation =
      !isParserObservation &&
      (observation.observationType === "TOOL" ||
        toolInvocation !== null ||
        toolName !== null);

    if (isToolObservation) {
      const invocationKey =
        toolInvocation?.invocationKey ??
        (toolName ? `${toolName}::${observation.id}` : `obs:${observation.id}`);
      const isNewInvocation = !countedToolInvocationKeys.has(invocationKey);

      if (isNewInvocation) {
        countedToolInvocationKeys.add(invocationKey);
        toolCount++;
      }

      if (toolName) {
        const existing = toolMetaByName.get(toolName) ?? {
          count: 0,
          instructionTypes: new Set<string>(),
          hasBlock: false,
        };
        if (isNewInvocation) {
          existing.count += 1;
        }
        const toolIType =
          getFirstStringValue({
            metadata,
            candidateKeys: [
              ["instruction_type"],
              ["instructionType"],
              ["instruction", "type"],
            ],
          }) ?? null;
        if (toolIType) {
          existing.instructionTypes.add(toolIType);
        }
        const hasBlock =
          getFirstBooleanValue({
            metadata,
            candidateKeys: [["policy_has_block"], ["policyHasBlock"]],
          }) ?? false;
        if (hasBlock) {
          existing.hasBlock = true;
        }
        toolMetaByName.set(toolName, existing);
      }
    }

    if (observation.level === "ERROR") {
      errorCount++;
    } else if (observation.level === "POLICY_VIOLATION") {
      policyViolationCount++;
    } else if (observation.level === "WARNING") {
      warningCount++;
    }

    const traceIdConsistent =
      observation.traceIdConsistent ??
      getFirstBooleanValue({
        metadata,
        candidateKeys: [["trace_id_consistent"], ["traceIdConsistent"]],
      });
    if (traceIdConsistent === false) {
      parserInconsistencyCount++;
    }

    if (!topic) {
      const metadataTopic = getFirstStringValue({
        metadata,
        candidateKeys: [
          ["topic"],
          ["turnTopic"],
          ["turn_topic"],
          ["session", "topic"],
        ],
      });

      topic =
        metadataTopic ??
        extractTopicFromObservationName(observation.name) ??
        null;
    }

    if (!category) {
      category =
        observation.category ??
        getFirstStringValue({
          metadata,
          candidateKeys: [
            ["category"],
            ["instructionCategory"],
            ["instruction_category"],
            ["session", "category"],
          ],
        }) ??
        extractCategoryFromObservationName(observation.name);
    }

    if (!instructionType) {
      instructionType =
        getFirstStringValue({
          metadata,
          candidateKeys: [
            ["instructionType"],
            ["instruction_type"],
            ["instruction", "type"],
            ["session", "instructionType"],
          ],
        }) ?? deriveInstructionTypeFromCategory(category);
    }

    // Instruction & policy metadata emitted by ArbiterOS callback (preferred)
    const itypeFromPolicy =
      getFirstStringValue({
        metadata,
        candidateKeys: [
          ["instruction_type"],
          ["instructionType"],
          ["instruction", "type"],
        ],
      }) ?? null;
    if (itypeFromPolicy) {
      instructionTypeCounts.set(
        itypeFromPolicy,
        (instructionTypeCounts.get(itypeFromPolicy) ?? 0) + 1,
      );
    }
    if (!instructionCategory) {
      instructionCategory =
        getFirstStringValue({
          metadata,
          candidateKeys: [
            ["instruction_category"],
            ["instructionCategory"],
            ["instruction", "category"],
          ],
        }) ?? null;
    }

    if (!core) {
      core = deriveCoreFromCategory(instructionCategory ?? category);
    }

    const hasBlock =
      getFirstBooleanValue({
        metadata,
        candidateKeys: [["policy_has_block"], ["policyHasBlock"]],
      }) ?? null;
    if (hasBlock === true) {
      policyHasBlock = true;
    }
    if (!policyAuthorityLabel) {
      policyAuthorityLabel =
        getFirstStringValue({
          metadata,
          candidateKeys: [
            ["policy_authority_label"],
            ["policy", "authority_label"],
            ["policy", "authorityLabel"],
          ],
        }) ?? null;
    }
    if (!policyConfidentiality) {
      policyConfidentiality =
        getFirstStringValue({
          metadata,
          candidateKeys: [
            ["policy_confidentiality"],
            ["policy", "confidentiality"],
          ],
        }) ?? null;
    }
    if (!policyIntegrity) {
      policyIntegrity =
        getFirstStringValue({
          metadata,
          candidateKeys: [["policy_integrity"], ["policy", "integrity"]],
        }) ?? null;
    }
    if (!policyTrustworthiness) {
      policyTrustworthiness =
        getFirstStringValue({
          metadata,
          candidateKeys: [
            ["policy_trustworthiness"],
            ["policy", "trustworthiness"],
          ],
        }) ?? null;
    }
    if (policyConfidence == null) {
      const v = getNestedValue((metadata ?? {}) as Record<string, unknown>, [
        "policy_confidence",
      ]) as unknown;
      if (typeof v === "number") policyConfidence = v;
    }
    if (policyReversible == null) {
      policyReversible =
        getFirstBooleanValue({
          metadata,
          candidateKeys: [["policy_reversible"], ["policy", "reversible"]],
        }) ?? null;
    }
    if (policyConfidentialityLabel == null) {
      policyConfidentialityLabel =
        getFirstBooleanValue({
          metadata,
          candidateKeys: [
            ["policy_confidentiality_label"],
            ["policy", "confidentiality_label"],
          ],
        }) ?? null;
    }

    const ruleEffectCountsRaw = getNestedValue(
      (metadata ?? {}) as Record<string, unknown>,
      ["policy_rule_effect_counts"],
    );
    if (ruleEffectCountsRaw && typeof ruleEffectCountsRaw === "object") {
      for (const [k, v] of Object.entries(
        ruleEffectCountsRaw as Record<string, unknown>,
      )) {
        if (typeof v !== "number") continue;
        policyRuleEffectCounts[k] = (policyRuleEffectCounts[k] ?? 0) + v;
      }
    }
  }

  if (!instructionType) {
    instructionType = deriveInstructionTypeFromCategory(category);
  }
  if (instructionTypeCounts.size > 0) {
    const best = [...instructionTypeCounts.entries()].sort(
      (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
    )[0]?.[0];
    if (best) {
      instructionType = best;
    }
  }

  const durationMs =
    Number.isFinite(minStartMs) && Number.isFinite(maxEndMs)
      ? Math.max(0, maxEndMs - minStartMs)
      : null;

  const toolBreakdown = [...toolMetaByName.entries()]
    .map(([toolName, meta]) => ({
      toolName,
      count: meta.count,
      instructionTypes: meta.instructionTypes.size
        ? [...meta.instructionTypes].sort()
        : null,
      hasBlock: meta.hasBlock ? true : null,
    }))
    .sort((a, b) => b.count - a.count || a.toolName.localeCompare(b.toolName));

  const policy =
    policyAuthorityLabel ||
    policyConfidentiality ||
    policyIntegrity ||
    policyTrustworthiness ||
    policyHasBlock ||
    Object.keys(policyRuleEffectCounts).length > 0
      ? {
          authorityLabel: policyAuthorityLabel,
          confidentiality: policyConfidentiality,
          integrity: policyIntegrity,
          trustworthiness: policyTrustworthiness,
          confidence: policyConfidence,
          reversible: policyReversible,
          confidentialityLabel: policyConfidentialityLabel,
          hasBlock: policyHasBlock ? true : null,
          ruleEffectCounts:
            Object.keys(policyRuleEffectCounts).length > 0
              ? policyRuleEffectCounts
              : null,
          inferredFromInstruction: false,
        }
      : instructionType || instructionCategory
        ? {
            authorityLabel: null,
            confidentiality: null,
            integrity: null,
            trustworthiness: null,
            confidence: null,
            reversible: null,
            confidentialityLabel: null,
            hasBlock: null,
            ruleEffectCounts: null,
            inferredFromInstruction: true,
          }
        : null;

  return {
    topic,
    core,
    category,
    instructionType,
    instructionCategory,
    observationCount: observationIds.length,
    toolCount,
    toolBreakdown,
    errorCount,
    warningCount,
    policyViolationCount,
    parserInconsistencyCount,
    durationMs,
    policy,
  };
}

function deriveCoreFromCategory(
  category: string | null | undefined,
): string | null {
  if (!category) return null;
  const normalized = category.trim();
  if (!normalized) return null;

  // ArbiterOS kernel categories often look like: COGNITIVE_CORE__RESPOND
  if (normalized.includes("__")) {
    const core = normalized.split("__")[0]?.trim();
    return core || null;
  }

  // InstructionBuilder categories look like: EXECUTION.Env / MEMORY.Management / COGNITIVE.Reasoning
  if (normalized.includes(".")) {
    const core = normalized.split(".")[0]?.trim();
    return core || null;
  }

  return null;
}
function extractTopicFromObservationName(
  observationName: string,
): string | null {
  if (!observationName.includes(" - kernel.")) return null;
  const [topic] = observationName.split(" - kernel.");
  if (!topic?.trim()) return null;
  return topic.trim();
}

function extractCategoryFromObservationName(
  observationName: string,
): string | null {
  if (!observationName.includes(" - kernel.")) return null;
  const [, rawCategory] = observationName.split(" - kernel.");
  if (!rawCategory?.trim()) return null;
  return rawCategory.trim().toUpperCase();
}

function deriveInstructionTypeFromCategory(
  category: string | null | undefined,
): string | null {
  if (!category) return null;
  const normalized = category.trim();
  if (!normalized) return null;
  if (normalized.includes("__")) {
    const instructionType = normalized.split("__").at(-1);
    return instructionType?.trim() || null;
  }
  return null;
}

function getFirstStringValue(params: {
  metadata: Record<string, unknown> | null | undefined;
  candidateKeys: string[][];
}): string | null {
  const { metadata, candidateKeys } = params;
  if (!metadata) return null;

  for (const keyPath of candidateKeys) {
    const value = getNestedValue(metadata, keyPath);
    if (typeof value === "string" && value.trim().length > 0) {
      return value.trim();
    }
  }

  return null;
}

function getFirstBooleanValue(params: {
  metadata: Record<string, unknown> | null | undefined;
  candidateKeys: string[][];
}): boolean | null {
  const { metadata, candidateKeys } = params;
  if (!metadata) return null;

  for (const keyPath of candidateKeys) {
    const value = getNestedValue(metadata, keyPath);
    if (typeof value === "boolean") return value;
    if (typeof value === "string") {
      const normalized = value.trim().toLowerCase();
      if (normalized === "true" || normalized === "1") return true;
      if (normalized === "false" || normalized === "0") return false;
    }
    if (typeof value === "number") {
      if (value === 1) return true;
      if (value === 0) return false;
    }
  }

  return null;
}

function getNestedValue(
  obj: Record<string, unknown>,
  keyPath: string[],
): unknown {
  let current: unknown = obj;
  for (const key of keyPath) {
    if (!current || typeof current !== "object") {
      return undefined;
    }
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

function truncateLabel(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  return `${value.slice(0, maxLength - 1)}...`;
}

function formatDurationMs(durationMs: number): string {
  if (durationMs < 1000) {
    return `${durationMs}ms`;
  }
  if (durationMs < 60_000) {
    return `${(durationMs / 1000).toFixed(1)}s`;
  }
  const minutes = Math.floor(durationMs / 60_000);
  const seconds = Math.round((durationMs % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}
