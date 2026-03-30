/**
 * Tree building utilities for trace component.
 *
 * IMPLEMENTATION APPROACH:
 * Uses fully iterative algorithms (no recursion) to avoid stack overflow on deep trees (10k+ depth).
 *
 * Algorithm Overview:
 * 1. Filter observations by level threshold and sort by startTime
 * 2. Build dependency graph: Map-based parent-child relationships (O(N))
 * 3. Topological sort: Process nodes bottom-up (leaves first) using queue with index-based traversal
 * 4. Cost aggregation: Compute bottom-up during tree construction (children before parents)
 * 5. Flatten to searchItems: Iterative pre-order traversal using explicit stack
 *
 * Complexity: O(N) time, O(N) space - handles unlimited depth without stack overflow.
 *
 * Main export: buildTraceUiData() - builds tree, nodeMap, and searchItems from trace + observations.
 */

import { type TreeNode, type TraceSearchListItem } from "./types";
import { type ObservationReturnType } from "@/src/server/api/routers/traces";
import Decimal from "decimal.js";
import {
  type ObservationLevelType,
  ObservationLevel,
  type TraceDomain,
} from "@langfuse/shared";
import { type WithStringifiedMetadata } from "@/src/utils/clientSideDomainTypes";
import {
  isParserContainerNodeName,
  normalizeParserNodeNameForGraph,
} from "@/src/features/trace-graph-view/nodeNameUtils";
import {
  getMetadataRecord,
  getGovernanceDisplayLevel,
  getObservationTurnIndex,
  parseStringArray,
} from "@/src/features/governance/utils/policyMetadata";
import {
  getHumanPolicyConfirmationState,
  getPolicyConfirmationState,
} from "@/src/features/governance/utils/policyConfirmation";
type TraceType = Omit<
  WithStringifiedMetadata<TraceDomain>,
  "input" | "output"
> & {
  input: string | null;
  output: string | null;
  latency?: number;
  // For events-based traces: when set, root observation becomes tree root
  rootObservationType?: string;
  rootObservationId?: string;
};

type ObservationWithOptionalMetadata = ObservationReturnType & {
  metadata?: string | null;
};

const SESSION_TURN_NODE_RE = /^session\.turn\.(?<turn>\d+)$/;
const SESSION_OUTPUT_TURN_NODE_PREFIX = "session.output.turn_";

function parseSessionTurnIndex(
  observationName: string | null | undefined,
): number | null {
  const match = SESSION_TURN_NODE_RE.exec(observationName ?? "");
  if (!match?.groups?.turn) {
    return null;
  }

  const parsed = Number.parseInt(match.groups.turn, 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizePolicyFallbackText(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }

  const normalized = value.replace(/\s+/g, " ").trim().toLowerCase();
  return normalized.length > 0 ? normalized : null;
}

function isTracePolicyFallbackCandidateObservation(
  observationName: string | null | undefined,
) {
  return (observationName ?? "").startsWith(SESSION_OUTPUT_TURN_NODE_PREFIX);
}

function getAlignedPolicyConfirmationMetadata(params: {
  traceMetadata: unknown;
  observation: ObservationWithOptionalMetadata;
}): Record<string, unknown> {
  const observationMetadata = getMetadataRecord(params.observation.metadata);
  if (!isTracePolicyFallbackCandidateObservation(params.observation.name)) {
    return observationMetadata;
  }

  const traceMetadata = getMetadataRecord(params.traceMetadata);
  const tracePolicyNames = parseStringArray(traceMetadata.policy_names);
  const traceState = getPolicyConfirmationState(traceMetadata);
  if (!traceState || tracePolicyNames.length === 0) {
    return observationMetadata;
  }

  const observationTurnIndex = getObservationTurnIndex({
    metadata: observationMetadata,
    observationName: params.observation.name,
  });
  const traceTurnIndex = getObservationTurnIndex({
    metadata: traceMetadata,
  });
  const matchesTurn =
    observationTurnIndex != null &&
    traceTurnIndex != null &&
    observationTurnIndex === traceTurnIndex;

  const normalizedStatusMessage = normalizePolicyFallbackText(
    params.observation.statusMessage,
  );
  const matchesText =
    normalizedStatusMessage != null &&
    [traceMetadata.raw_output_content, traceMetadata.policy_protected]
      .map(normalizePolicyFallbackText)
      .some(
        (candidate): candidate is string =>
          candidate === normalizedStatusMessage,
      );

  if (!matchesTurn && !matchesText) {
    return observationMetadata;
  }

  return {
    ...observationMetadata,
    policy_confirmation_state: traceMetadata.policy_confirmation_state,
    policy_names: traceMetadata.policy_names,
  };
}

function collectPolicyConfirmationTurnIndexes(params: {
  trace: TraceType;
  observations: ObservationWithOptionalMetadata[];
}): Set<number> {
  const policyConfirmationTurnIndexes = new Set<number>();

  for (const observation of params.observations) {
    const alignedMetadata = getAlignedPolicyConfirmationMetadata({
      traceMetadata: params.trace.metadata,
      observation,
    });
    const state = getHumanPolicyConfirmationState({
      metadata: alignedMetadata,
      traceInput: params.trace.input,
      traceMetadata: params.trace.metadata,
    });
    if (!state) {
      continue;
    }

    if (parseStringArray(alignedMetadata.policy_names).length === 0) {
      continue;
    }

    const turnIndex = getObservationTurnIndex({
      metadata: alignedMetadata,
      observationName: observation.name,
    });
    if (turnIndex != null) {
      policyConfirmationTurnIndexes.add(turnIndex);
    }
  }

  return policyConfirmationTurnIndexes;
}

/**
 * Processing node for iterative tree building.
 * Tracks parent-child relationships and processing state for bottom-up traversal.
 */
interface ProcessingNode {
  observation: ObservationWithOptionalMetadata;
  childrenIds: string[];
  inDegree: number; // Number of unprocessed children (for topological sort)
  depth: number; // Tree depth (calculated during graph building)
  treeNode?: TreeNode; // Set when node is processed
}

/**
 * Returns observation levels at or above the minimum level.
 */
function getObservationLevels(minLevel: ObservationLevelType | undefined) {
  const ascendingLevels = [
    ObservationLevel.DEBUG,
    ObservationLevel.DEFAULT,
    ObservationLevel.WARNING,
    ObservationLevel.ERROR,
    ObservationLevel.POLICY_VIOLATION,
  ];

  if (!minLevel) return ascendingLevels;

  const minLevelIndex = ascendingLevels.indexOf(minLevel);
  return ascendingLevels.slice(minLevelIndex);
}

/**
 * Filters and prepares observations for tree building.
 * Filters by minimum observation level, cleans orphaned parents, and sorts by startTime.
 * Returns flat array (nesting happens in buildDependencyGraph).
 */
function filterAndPrepareObservations(
  list: ObservationWithOptionalMetadata[],
  minLevel?: ObservationLevelType,
): {
  sortedObservations: ObservationWithOptionalMetadata[];
  hiddenObservationsCount: number;
} {
  if (list.length === 0)
    return { sortedObservations: [], hiddenObservationsCount: 0 };

  const getUiNodeName = (observationName: string | null | undefined) =>
    normalizeParserNodeNameForGraph(observationName) ?? observationName ?? "";

  // Always hide internal parser "container" observations from Trace UI.
  // (Tree/timeline/search/log are all derived from TreeNode.name here.)
  const listWithoutParserContainers = list.filter(
    (o) => !isParserContainerNodeName(o.name),
  );

  // Filter for observations with minimum level
  const mutableList = listWithoutParserContainers.filter((o) =>
    getObservationLevels(minLevel).includes(o.level),
  );
  const hiddenObservationsCount =
    listWithoutParserContainers.length - mutableList.length;

  // Re-parent observations whose parent is hidden/filtered out (container node or level filter).
  // This keeps descendants visible and prevents "dangling parent" roots from disappearing.
  const parentById = new Map<string, string | null>();
  for (const o of list) {
    parentById.set(o.id, o.parentObservationId ?? null);
  }

  const keptIds = new Set(mutableList.map((o) => o.id));

  // Remove/skip parentObservationId if parent doesn't exist in kept set
  mutableList.forEach((observation) => {
    let parentId = observation.parentObservationId ?? null;
    if (!parentId) {
      return;
    }

    const visited = new Set<string>();
    while (parentId && !keptIds.has(parentId)) {
      if (visited.has(parentId)) {
        parentId = null;
        break;
      }
      visited.add(parentId);
      parentId = parentById.get(parentId) ?? null;
    }

    observation.parentObservationId = parentId;
  });

  // UI-only re-parenting rules for parser-derived nodes.
  // - parser.<tool>.<n> (tool_result) should live under <tool>.<n>
  // - parser.turn_XXX.structured_output is hidden as a parser container node
  const chronological = [...mutableList].sort(
    (a, b) => a.startTime.getTime() - b.startTime.getTime(),
  );
  const sessionTurns = chronological
    .map((obs) => {
      const turnIndex = parseSessionTurnIndex(obs.name);
      if (turnIndex == null) {
        return null;
      }
      const start = obs.startTime.getTime();
      const end = obs.endTime ? obs.endTime.getTime() : null;
      return { id: obs.id, start, end, turn: turnIndex };
    })
    .filter((t) => t !== null)
    .sort((a, b) => a.start - b.start);
  const sessionTurnEndBounds = sessionTurns.map((turn, idx) => {
    // Turns often have an endTime earlier than their last "logical" children.
    // For UI grouping, prefer the next turn's start as the boundary.
    const next = sessionTurns[idx + 1];
    return next ? next.start : Number.POSITIVE_INFINITY;
  });

  const latestObservationIdByName = new Map<string, string>();
  const latestParserPreIdByToolNodeName = new Map<string, string>();
  const TOOL_RESULT_UI_NODE_RE = /^parser\.(?<toolName>[^.]+)\.(?<index>\d+)$/;
  const TOOL_PRE_UI_NODE_RE = /^parser\.pre_(?<toolName>[^.]+)\.(?<index>\d+)$/;

  let activeTurnIndex = 0;
  for (const obs of chronological) {
    const uiName = getUiNodeName(obs.name);

    // Assign observations to the active session turn (UI-only grouping).
    // Ensures: all intermediate nodes between `session.turn.xxx` and `topic - kernel.xxx`
    // are rendered under the corresponding `session.turn.xxx` node.
    while (
      activeTurnIndex < sessionTurns.length &&
      obs.startTime.getTime() >= sessionTurnEndBounds[activeTurnIndex]!
    ) {
      activeTurnIndex++;
    }
    const activeTurn = sessionTurns[activeTurnIndex];
    const activeTurnEndBound = sessionTurnEndBounds[activeTurnIndex];
    if (
      activeTurn &&
      obs.id !== activeTurn.id &&
      obs.startTime.getTime() >= activeTurn.start &&
      obs.startTime.getTime() <
        (activeTurnEndBound ?? Number.POSITIVE_INFINITY) &&
      obs.parentObservationId == null
    ) {
      obs.parentObservationId = activeTurn.id;
    }

    // (1) Attach parser tool_result nodes under the actual tool node.
    const toolResultMatch = TOOL_RESULT_UI_NODE_RE.exec(uiName);
    if (toolResultMatch?.groups?.toolName && toolResultMatch.groups.index) {
      const targetParentName = `${toolResultMatch.groups.toolName}.${toolResultMatch.groups.index}`;
      const toolNodeId = latestObservationIdByName.get(targetParentName);
      if (toolNodeId && toolNodeId !== obs.id) {
        obs.parentObservationId = toolNodeId;
      }
    }

    // (1b) Record parser.pre_{tool}.{n} and attach <tool>.<n> under it.
    // This supports kernel "instruction parser pre tool call" nodes that should be parents
    // of the corresponding tool invocation/result node in trace2 tree/timeline/log views.
    const toolPreMatch = TOOL_PRE_UI_NODE_RE.exec(uiName);
    if (toolPreMatch?.groups?.toolName && toolPreMatch.groups.index) {
      const toolNodeName = `${toolPreMatch.groups.toolName}.${toolPreMatch.groups.index}`;
      latestParserPreIdByToolNodeName.set(toolNodeName, obs.id);
    }
    if (
      obs.type === "TOOL" &&
      typeof uiName === "string" &&
      latestParserPreIdByToolNodeName.has(uiName)
    ) {
      const parserPreId = latestParserPreIdByToolNodeName.get(uiName);
      if (parserPreId && parserPreId !== obs.id) {
        obs.parentObservationId = parserPreId;
      }
    }

    if (typeof obs.name === "string") {
      latestObservationIdByName.set(obs.name, obs.id);
    }
  }

  // Sort by start time
  const sortedObservations = mutableList.sort(
    (a, b) => a.startTime.getTime() - b.startTime.getTime(),
  );

  return {
    sortedObservations,
    hiddenObservationsCount,
  };
}

/**
 * Phase 2: Builds dependency graph for bottom-up tree construction.
 * Creates ProcessingNodes with parent-child relationships via IDs.
 * Calculates in-degrees for topological sort (children count per node).
 * Calculates depth for each node based on parent relationships.
 */
function buildDependencyGraph(
  sortedObservations: ObservationWithOptionalMetadata[],
): {
  nodeRegistry: Map<string, ProcessingNode>;
  leafIds: string[];
} {
  const nodeRegistry = new Map<string, ProcessingNode>();

  // First pass: create all ProcessingNodes with initial depth
  for (const obs of sortedObservations) {
    nodeRegistry.set(obs.id, {
      observation: obs,
      childrenIds: [],
      inDegree: 0,
      depth: 0, // Will be calculated in third pass
      treeNode: undefined,
    });
  }

  // Second pass: build parent-child relationships
  for (const obs of sortedObservations) {
    if (obs.parentObservationId) {
      const parent = nodeRegistry.get(obs.parentObservationId);
      if (parent) {
        parent.childrenIds.push(obs.id);
      }
    }
  }

  // Third pass: calculate depth top-down using BFS
  const rootIds: string[] = [];
  for (const [id, node] of nodeRegistry) {
    if (!node.observation.parentObservationId) {
      rootIds.push(id);
      node.depth = 0;
    }
  }

  // BFS to propagate depth down the tree
  const queue = [...rootIds];
  let queueIndex = 0;
  while (queueIndex < queue.length) {
    const currentId = queue[queueIndex++];
    const currentNode = nodeRegistry.get(currentId)!;

    for (const childId of currentNode.childrenIds) {
      const childNode = nodeRegistry.get(childId)!;
      childNode.depth = currentNode.depth + 1;
      queue.push(childId);
    }
  }

  // Fourth pass: calculate in-degrees and identify leaf nodes
  // Note: Children are already in correct order because observations are pre-sorted
  // by startTime in filterAndPrepareObservations, and children are added in iteration order.
  const leafIds: string[] = [];
  for (const [id, node] of nodeRegistry) {
    // Set in-degree to children count (for topological sort)
    node.inDegree = node.childrenIds.length;

    // Track leaf nodes (no children = ready to process first)
    if (node.childrenIds.length === 0) {
      leafIds.push(id);
    }
  }

  return { nodeRegistry, leafIds };
}

/**
 * Phase 3: Builds TreeNodes bottom-up using topological sort.
 * Processes leaf nodes first, then parents once all children are processed.
 * Calculates costs bottom-up: node cost + aggregated children costs.
 * Also calculates temporal properties (startTimeSinceTrace, startTimeSinceParentStart) and depth.
 */
function buildTreeNodesBottomUp(
  nodeRegistry: Map<string, ProcessingNode>,
  leafIds: string[],
  nodeMap: Map<string, TreeNode>,
  traceStartTime: Date,
  traceMetadata: unknown,
  policyConfirmationTurnIndexes: Set<number>,
): string[] {
  // Queue starts with all leaf nodes (inDegree === 0)
  // Use index-based traversal instead of shift() for O(1) dequeue (shift is O(N))
  const queue = [...leafIds];
  let queueIndex = 0;
  const rootIds: string[] = [];

  while (queueIndex < queue.length) {
    const currentId = queue[queueIndex++];
    const currentNode = nodeRegistry.get(currentId)!;
    const obs = currentNode.observation;

    // Get child TreeNodes (already processed)
    const childTreeNodes: TreeNode[] = [];
    for (const childId of currentNode.childrenIds) {
      const childNode = nodeRegistry.get(childId)!;
      if (childNode.treeNode) {
        childTreeNodes.push(childNode.treeNode);
      }
    }

    // Calculate this node's own cost
    let nodeCost: Decimal | undefined;

    if (obs.totalCost != null) {
      const cost = new Decimal(obs.totalCost);
      if (!cost.isZero()) {
        nodeCost = cost;
      }
    } else if (obs.inputCost != null || obs.outputCost != null) {
      const inputCost =
        obs.inputCost != null ? new Decimal(obs.inputCost) : new Decimal(0);
      const outputCost =
        obs.outputCost != null ? new Decimal(obs.outputCost) : new Decimal(0);
      const combinedCost = inputCost.plus(outputCost);
      if (!combinedCost.isZero()) {
        nodeCost = combinedCost;
      }
    }

    // Sum children's total costs (already computed bottom-up)
    const childrenTotalCost = childTreeNodes.reduce<Decimal | undefined>(
      (acc, child) => {
        if (!child.totalCost) return acc;
        return acc ? acc.plus(child.totalCost) : child.totalCost;
      },
      undefined,
    );

    // Total = node cost + children costs
    const totalCost =
      nodeCost && childrenTotalCost
        ? nodeCost.plus(childrenTotalCost)
        : nodeCost || childrenTotalCost;

    // Calculate temporal and structural properties
    const startTimeSinceTrace =
      obs.startTime.getTime() - traceStartTime.getTime();

    let startTimeSinceParentStart: number | null = null;

    if (obs.parentObservationId) {
      const parentNode = nodeRegistry.get(obs.parentObservationId);
      if (parentNode) {
        startTimeSinceParentStart =
          obs.startTime.getTime() - parentNode.observation.startTime.getTime();
      }
    }

    // Use pre-calculated depth from ProcessingNode
    const depth = currentNode.depth;
    const sessionTurnIndex = parseSessionTurnIndex(obs.name);

    // Calculate childrenDepth (max depth of subtree rooted at this node)
    // Leaf nodes have childrenDepth = 0
    // Parent nodes have childrenDepth = max(children.childrenDepth) + 1
    const childrenDepth =
      childTreeNodes.length > 0
        ? Math.max(...childTreeNodes.map((c) => c.childrenDepth)) + 1
        : 0;

    // Create TreeNode
    const treeNode: TreeNode = {
      id: obs.id,
      type: obs.type,
      name: normalizeParserNodeNameForGraph(obs.name) ?? obs.name ?? "",
      startTime: obs.startTime,
      endTime: obs.endTime,
      level: obs.level,
      effectiveLevel: getGovernanceDisplayLevel({
        level: obs.level,
        observationMetadata: obs.metadata,
        traceMetadata,
        observationName: obs.name,
        statusMessage: obs.statusMessage,
      }),
      hasPolicyConfirmation:
        sessionTurnIndex != null &&
        policyConfirmationTurnIndexes.has(sessionTurnIndex),
      children: childTreeNodes,
      inputUsage: obs.inputUsage,
      outputUsage: obs.outputUsage,
      totalUsage: obs.totalUsage,
      calculatedInputCost: obs.inputCost,
      calculatedOutputCost: obs.outputCost,
      calculatedTotalCost: obs.totalCost,
      parentObservationId: obs.parentObservationId,
      traceId: obs.traceId,
      totalCost,
      startTimeSinceTrace,
      startTimeSinceParentStart,
      depth,
      childrenDepth,
    };

    // Store in registry and nodeMap
    currentNode.treeNode = treeNode;
    nodeMap.set(currentId, treeNode);

    // Decrement parent's in-degree and queue if ready
    if (obs.parentObservationId) {
      const parent = nodeRegistry.get(obs.parentObservationId);
      if (parent) {
        parent.inDegree--;
        if (parent.inDegree === 0) {
          queue.push(obs.parentObservationId);
        }
      }
    } else {
      // No parent = root observation
      rootIds.push(currentId);
    }
  }

  return rootIds;
}

/**
 * Builds hierarchical tree from trace and observations (ITERATIVE - optimal).
 * Uses topological sort for bottom-up cost aggregation.
 * Handles unlimited tree depth without stack overflow.
 *
 * Returns `roots` array:
 * - Traditional traces: [TRACE node] with observations as children
 * - Events-based traces (rootObservationType set): [obs1, obs2, ...] directly. Array because there could be multiple roots now
 */
function buildTraceTree(
  trace: TraceType,
  observations: ObservationWithOptionalMetadata[],
  minLevel?: ObservationLevelType,
  precomputedPolicyConfirmationTurnIndexes?: number[],
): {
  roots: TreeNode[];
  hiddenObservationsCount: number;
  nodeMap: Map<string, TreeNode>;
} {
  const policyConfirmationTurnIndexes =
    precomputedPolicyConfirmationTurnIndexes &&
    precomputedPolicyConfirmationTurnIndexes.length > 0
      ? new Set(precomputedPolicyConfirmationTurnIndexes)
      : collectPolicyConfirmationTurnIndexes({
          trace,
          observations,
        });

  // Phase 1: Filter and prepare observations
  const { sortedObservations, hiddenObservationsCount } =
    filterAndPrepareObservations(observations, minLevel);

  // Handle empty case
  if (sortedObservations.length === 0) {
    // For events-based traces with no observations, return empty roots
    if (trace.rootObservationType) {
      return { roots: [], hiddenObservationsCount, nodeMap: new Map() };
    }

    // Traditional traces: return TRACE node with no children
    const emptyTree: TreeNode = {
      id: `trace-${trace.id}`,
      type: "TRACE",
      name: trace.name ?? "",
      startTime: trace.timestamp,
      endTime: null,
      children: [],
      latency: trace.latency,
      totalCost: undefined,
      startTimeSinceTrace: 0,
      startTimeSinceParentStart: null,
      // depth: -1 for TRACE wrapper so its children (observations) start at depth 0
      depth: -1,
      childrenDepth: 0,
    };
    const nodeMap = new Map<string, TreeNode>();
    nodeMap.set(emptyTree.id, emptyTree);
    return { roots: [emptyTree], hiddenObservationsCount, nodeMap };
  }

  // Phase 2: Build dependency graph
  const { nodeRegistry, leafIds } = buildDependencyGraph(sortedObservations);

  // Phase 3: Build TreeNodes bottom-up with cost aggregation
  const nodeMap = new Map<string, TreeNode>();
  const rootIds = buildTreeNodesBottomUp(
    nodeRegistry,
    leafIds,
    nodeMap,
    trace.timestamp,
    trace.metadata,
    policyConfirmationTurnIndexes,
  );

  // Phase 4: Build roots array
  const rootTreeNodes: TreeNode[] = [];
  for (const rootId of rootIds) {
    const rootNode = nodeRegistry.get(rootId)!;
    if (rootNode.treeNode) {
      rootTreeNodes.push(rootNode.treeNode);
    }
  }

  // Sort roots by startTime for consistent ordering
  rootTreeNodes.sort((a, b) => a.startTime.getTime() - b.startTime.getTime());

  // Events-based traces (rootObservationType set): return observations as roots directly
  if (trace.rootObservationType) {
    return { roots: rootTreeNodes, hiddenObservationsCount, nodeMap };
  }

  // Traditional traces: wrap in TRACE node

  // Calculate trace root total cost
  const traceTotalCost = rootTreeNodes.reduce<Decimal | undefined>(
    (acc, child) => {
      if (!child.totalCost) return acc;
      return acc ? acc.plus(child.totalCost) : child.totalCost;
    },
    undefined,
  );

  // Calculate trace root childrenDepth
  const traceChildrenDepth =
    rootTreeNodes.length > 0
      ? Math.max(...rootTreeNodes.map((c) => c.childrenDepth)) + 1
      : 0;

  // Create trace root node
  const traceNode: TreeNode = {
    id: `trace-${trace.id}`,
    type: "TRACE",
    name: trace.name ?? "",
    startTime: trace.timestamp,
    endTime: null,
    children: rootTreeNodes,
    latency: trace.latency,
    totalCost: traceTotalCost,
    startTimeSinceTrace: 0,
    startTimeSinceParentStart: null,
    // depth: -1 for TRACE wrapper so its children (observations) start at depth 0
    depth: -1,
    childrenDepth: traceChildrenDepth,
  };

  nodeMap.set(traceNode.id, traceNode);

  return { roots: [traceNode], hiddenObservationsCount, nodeMap };
}

/**
 * Main entry point: builds complete UI data from trace and observations.
 *
 * Returns:
 * - roots: Array of root TreeNodes (single TRACE root for traditional, multiple obs roots for events-based)
 * - nodeMap: Map<id, TreeNode> for O(1) lookup
 * - searchItems: Flattened list for search/virtualized rendering
 * - hiddenObservationsCount: Number filtered by minLevel
 */
export function buildTraceUiData(
  trace: TraceType,
  observations: ObservationWithOptionalMetadata[],
  minLevel?: ObservationLevelType,
  policyConfirmationTurnIndexes?: number[],
): {
  roots: TreeNode[];
  hiddenObservationsCount: number;
  searchItems: TraceSearchListItem[];
  nodeMap: Map<string, TreeNode>;
} {
  const { roots, hiddenObservationsCount, nodeMap } = buildTraceTree(
    trace,
    observations,
    minLevel,
    policyConfirmationTurnIndexes,
  );

  // Handle empty roots case
  if (roots.length === 0) {
    return { roots, hiddenObservationsCount, searchItems: [], nodeMap };
  }

  // TODO: Extract aggregation logic to shared utility - duplicated in TraceTree.tsx and TraceTimeline/index.tsx
  // Calculate aggregated totals across all roots for heatmap scaling
  const rootTotalCost = roots.reduce<Decimal | undefined>((acc, r) => {
    if (!r.totalCost) return acc;
    return acc ? acc.plus(r.totalCost) : r.totalCost;
  }, undefined);

  const rootDuration =
    roots.length > 0
      ? Math.max(
          ...roots.map((r) =>
            r.latency
              ? r.latency * 1000
              : r.endTime
                ? r.endTime.getTime() - r.startTime.getTime()
                : 0,
          ),
        )
      : undefined;

  // Build flat search items list (iterative to avoid stack overflow on deep trees)
  const searchItems: TraceSearchListItem[] = [];

  // Initialize stack with all roots (in reverse order for correct DFS traversal)
  const stack: TreeNode[] = [];
  for (let i = roots.length - 1; i >= 0; i--) {
    stack.push(roots[i]!);
  }

  while (stack.length > 0) {
    const node = stack.pop()!;
    searchItems.push({
      node,
      parentTotalCost: rootTotalCost,
      parentTotalDuration: rootDuration,
      observationId: node.type === "TRACE" ? undefined : node.id,
    });
    // Push children in reverse order to maintain depth-first left-to-right traversal
    for (let i = node.children.length - 1; i >= 0; i--) {
      stack.push(node.children[i]!);
    }
  }

  return { roots, hiddenObservationsCount, searchItems, nodeMap };
}
