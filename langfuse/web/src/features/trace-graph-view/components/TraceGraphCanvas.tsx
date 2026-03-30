import React, {
  useEffect,
  useRef,
  useMemo,
  useState,
  useCallback,
} from "react";
import { Network, DataSet } from "vis-network/standalone";
import {
  ZoomIn,
  ZoomOut,
  RotateCcw,
  Maximize2,
  GitBranch,
  Search,
  ChevronLeft,
  ChevronRight,
  X,
} from "lucide-react";

import type { GraphCanvasData, GraphNodeData, TraceGraphMode } from "../types";
import {
  LANGFUSE_START_NODE_NAME,
  LANGFUSE_END_NODE_NAME,
  LANGGRAPH_START_NODE_NAME,
  LANGGRAPH_END_NODE_NAME,
} from "../types";
import { Button } from "@/src/components/ui/button";
import { Dialog, DialogContent } from "@/src/components/ui/dialog";
import { Input } from "@/src/components/ui/input";
import { useLanguage } from "@/src/features/i18n/LanguageProvider";
import { localize } from "@/src/features/i18n/localize";
import { cn } from "@/src/utils/tailwind";

type TraceGraphCanvasProps = {
  graph: GraphCanvasData;
  graphMode: TraceGraphMode;
  selectedNodeName: string | null;
  onCanvasNodeNameChange: (
    nodeName: string | null,
    options?: { shouldCycleObservation?: boolean },
  ) => void;
  disablePhysics?: boolean;
  nodeToObservationsMap?: Record<string, string[]>;
  currentObservationIndices?: Record<string, number>;
  onGraphModeToggle?: () => void;
  allowFullscreen?: boolean;
  whiteBackground?: boolean;
};

type ModeSearchState = {
  isSearchOpen: boolean;
  searchQuery: string;
  activeSearchResultIndex: number;
};

function createInitialModeSearchState(): ModeSearchState {
  return {
    isSearchOpen: false,
    searchQuery: "",
    activeSearchResultIndex: 0,
  };
}

export const TraceGraphCanvas: React.FC<TraceGraphCanvasProps> = (props) => {
  const {
    graph: graphData,
    graphMode,
    selectedNodeName,
    onCanvasNodeNameChange,
    disablePhysics = false,
    nodeToObservationsMap = {},
    currentObservationIndices = {},
    onGraphModeToggle,
    allowFullscreen = true,
    whiteBackground = false,
  } = props;
  const { language } = useLanguage();
  const [isHovering, setIsHovering] = useState(false);
  const [isFullscreenOpen, setIsFullscreenOpen] = useState(false);
  const [searchStateByMode, setSearchStateByMode] = useState<
    Record<TraceGraphMode, ModeSearchState>
  >({
    execution: createInitialModeSearchState(),
    hierarchy: createInitialModeSearchState(),
  });

  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);
  const nodesDataSetRef = useRef<DataSet<any> | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const onCanvasNodeNameChangeRef = useRef(onCanvasNodeNameChange);
  const selectedNodeNameRef = useRef(selectedNodeName);
  const lastAppliedSearchKeyRef = useRef<Record<TraceGraphMode, string | null>>(
    {
      execution: null,
      hierarchy: null,
    },
  );

  // Keep ref up to date without triggering Network recreation
  useEffect(() => {
    onCanvasNodeNameChangeRef.current = onCanvasNodeNameChange;
  }, [onCanvasNodeNameChange]);

  useEffect(() => {
    selectedNodeNameRef.current = selectedNodeName;
  }, [selectedNodeName]);

  const updateCurrentModeSearchState = useCallback(
    (updater: (previousState: ModeSearchState) => ModeSearchState) => {
      setSearchStateByMode((previousStateByMode) => ({
        ...previousStateByMode,
        [graphMode]: updater(previousStateByMode[graphMode]),
      }));
    },
    [graphMode],
  );

  const isSearchOpen = searchStateByMode[graphMode].isSearchOpen;
  const searchQuery = searchStateByMode[graphMode].searchQuery;
  const activeSearchResultIndex =
    searchStateByMode[graphMode].activeSearchResultIndex;

  const setIsSearchOpen = useCallback(
    (value: React.SetStateAction<boolean>) => {
      updateCurrentModeSearchState((previousState) => ({
        ...previousState,
        isSearchOpen:
          typeof value === "function"
            ? value(previousState.isSearchOpen)
            : value,
      }));
    },
    [updateCurrentModeSearchState],
  );

  const setSearchQuery = useCallback(
    (value: React.SetStateAction<string>) => {
      updateCurrentModeSearchState((previousState) => ({
        ...previousState,
        searchQuery:
          typeof value === "function"
            ? value(previousState.searchQuery)
            : value,
      }));
    },
    [updateCurrentModeSearchState],
  );

  const setActiveSearchResultIndex = useCallback(
    (value: React.SetStateAction<number>) => {
      updateCurrentModeSearchState((previousState) => ({
        ...previousState,
        activeSearchResultIndex:
          typeof value === "function"
            ? value(previousState.activeSearchResultIndex)
            : value,
      }));
    },
    [updateCurrentModeSearchState],
  );

  const getNodeStyle = (params: {
    nodeType: string;
    level?: string | null;
  }) => {
    if (params.level === "POLICY_VIOLATION") {
      return {
        border: "#b91c1c", // red-700
        background: "#fee2e2", // red-100
        highlight: { border: "#991b1b", background: "#fecaca" }, // red-800 / red-200
      };
    }
    if (params.level === "ERROR") {
      return {
        border: "#b91c1c", // red-700
        background: "#fee2e2", // red-100
        highlight: { border: "#991b1b", background: "#fecaca" }, // red-800 / red-200
      };
    }
    if (params.level === "WARNING") {
      return {
        border: "#b45309", // amber-700
        background: "#ffedd5", // orange-100
        highlight: { border: "#92400e", background: "#fed7aa" }, // amber-800 / orange-200
      };
    }

    switch (params.nodeType) {
      case "AGENT":
        return {
          border: "#c4b5fd", // purple-300 (former background)
          background: "#f3f4f6", // gray-100
          highlight: { border: "#a78bfa", background: "#e5e7eb" }, // gray-200
        };
      case "INTENT":
        return {
          border: "#99f6e4", // teal-200
          background: "#f3f4f6", // gray-100
          highlight: { border: "#2dd4bf", background: "#e5e7eb" }, // teal-400
        };
      case "POLICY":
        return {
          border: "#fca5a5", // red-300-ish
          background: "#f3f4f6", // gray-100
          highlight: { border: "#f87171", background: "#e5e7eb" }, // red-400-ish
        };
      case "TOOLS":
        return {
          border: "#fdba74", // orange-300
          background: "#f3f4f6", // gray-100
          highlight: { border: "#fb923c", background: "#e5e7eb" }, // orange-400
        };
      case "OUTPUT":
        return {
          border: "#a7f3d0", // emerald-200
          background: "#f3f4f6", // gray-100
          highlight: { border: "#34d399", background: "#e5e7eb" }, // emerald-400
        };
      case "TOOL":
        return {
          border: "#fed7aa", // orange-300 (former background)
          background: "#f3f4f6", // gray-100
          highlight: { border: "#fdba74", background: "#e5e7eb" }, // gray-200
        };
      case "GENERATION":
        return {
          border: "#f0abfc", // fuchsia-300 (former background)
          background: "#f3f4f6", // gray-100
          highlight: { border: "#e879f9", background: "#e5e7eb" }, // gray-200
        };
      case "SPAN":
        return {
          border: "#93c5fd", // blue-300 (former background)
          background: "#f3f4f6", // gray-100
          highlight: { border: "#60a5fa", background: "#e5e7eb" }, // gray-200
        };
      case "CHAIN":
        return {
          border: "#f9a8d4", // pink-300 (former background)
          background: "#f3f4f6", // gray-100
          highlight: { border: "#f472b6", background: "#e5e7eb" }, // gray-200
        };
      case "RETRIEVER":
        return {
          border: "#5eead4", // teal-300 (former background)
          background: "#f3f4f6", // gray-100
          highlight: { border: "#2dd4bf", background: "#e5e7eb" }, // gray-200
        };
      case "EVENT":
        return {
          border: "#6ee7b7", // green-300 (former background)
          background: "#f3f4f6", // gray-100
          highlight: { border: "#34d399", background: "#e5e7eb" }, // gray-200
        };
      case "PARSER":
        return {
          border: "#a5b4fc", // indigo-300
          background: "#f3f4f6", // gray-100
          highlight: { border: "#818cf8", background: "#e5e7eb" }, // indigo-400
        };
      case "EMBEDDING":
        return {
          border: "#fbbf24", // amber-300 (former background)
          background: "#f3f4f6", // gray-100
          highlight: { border: "#f59e0b", background: "#e5e7eb" }, // gray-200
        };
      case "GUARDRAIL":
        return {
          border: "#fca5a5", // red-300 (former background)
          background: "#f3f4f6", // gray-100
          highlight: { border: "#f87171", background: "#e5e7eb" }, // gray-200
        };
      case "LANGGRAPH_SYSTEM":
        return {
          border: "#d1d5db", // gray (former background)
          background: "#f3f4f6", // gray-100
          highlight: { border: "#9ca3af", background: "#e5e7eb" }, // gray-200
        };
      default:
        return {
          border: "#93c5fd", // blue-300 (former background)
          background: "#f3f4f6", // gray-100
          highlight: { border: "#60a5fa", background: "#e5e7eb" }, // gray-200
        };
    }
  };

  const nodes = useMemo(() => {
    const seen = new Set<string>();
    const uniqueNodes = graphData.nodes.filter((node) => {
      if (seen.has(node.id)) return false;
      seen.add(node.id);
      return true;
    });

    return uniqueNodes.map((node) => {
      const metadataLines =
        graphMode === "hierarchy" && node.metadataSummary
          ? [
              node.metadataSummary.core
                ? `${localize(language, "Core", "核心")}: ${truncateText(node.metadataSummary.core, 24)}`
                : null,
              node.metadataSummary.category
                ? `${localize(language, "Category", "类别")}: ${truncateText(node.metadataSummary.category, 24)}`
                : null,
              node.metadataSummary.instructionType
                ? `${localize(language, "Type", "类型")}: ${truncateText(node.metadataSummary.instructionType, 24)}`
                : null,
              node.metadataSummary.policy?.authorityLabel
                ? `${localize(language, "Policy", "策略")}: ${truncateText(
                    `${node.metadataSummary.policy.authorityLabel}${node.metadataSummary.policy.hasBlock ? " (BLOCK)" : ""}`,
                    32,
                  )}`
                : node.metadataSummary.policy?.hasBlock
                  ? `${localize(language, "Policy", "策略")}: BLOCK`
                  : null,
              node.metadataSummary.observationCount != null
                ? `${localize(language, "Obs", "观测")}: ${node.metadataSummary.observationCount} | ${localize(language, "Tools", "工具")}: ${node.metadataSummary.toolCount ?? 0}`
                : null,
              (node.metadataSummary.errorCount ?? 0) > 0 ||
              (node.metadataSummary.warningCount ?? 0) > 0 ||
              (node.metadataSummary.policyViolationCount ?? 0) > 0 ||
              (node.metadataSummary.parserInconsistencyCount ?? 0) > 0
                ? `${localize(language, "Risk", "风险")}: E${node.metadataSummary.errorCount ?? 0} W${node.metadataSummary.warningCount ?? 0} V${node.metadataSummary.policyViolationCount ?? 0} P${node.metadataSummary.parserInconsistencyCount ?? 0}`
                : null,
            ].filter((line): line is string => Boolean(line))
          : [];

      const label =
        metadataLines.length > 0
          ? `${node.label}\n${metadataLines.join("\n")}`
          : node.label;

      const hasShortLabel = node.label !== node.id;
      const nodeData = {
        id: node.id,
        label,
        color: getNodeStyle({ nodeType: node.type, level: node.level }),
        title: node.title ?? (hasShortLabel ? node.id : undefined),
      };

      // Special positioning and colors for system nodes
      if (
        node.id === LANGFUSE_START_NODE_NAME ||
        node.id === LANGGRAPH_START_NODE_NAME
      ) {
        return {
          ...nodeData,
          x: -200,
          y: 0,
          color: {
            border: "#166534", // green
            background: "#86efac",
            highlight: {
              border: "#15803d",
              background: "#4ade80",
            },
          },
        };
      }
      if (
        node.id === LANGFUSE_END_NODE_NAME ||
        node.id === LANGGRAPH_END_NODE_NAME
      ) {
        return {
          ...nodeData,
          x: 200,
          y: 0,
          color: {
            border: "#7f1d1d", // red
            background: "#fecaca",
            highlight: {
              border: "#991b1b",
              background: "#fca5a5",
            },
          },
        };
      }
      return nodeData;
    });
  }, [graphData.nodes, graphMode, language]);

  const options = useMemo(
    () => ({
      autoResize: true,
      layout: {
        hierarchical: {
          enabled: true,
          direction: "UD", // Up-Down (top to bottom)
          levelSeparation: 60,
          nodeSpacing: 175,
          sortMethod: "hubsize",
          shakeTowards: "roots",
        },
        randomSeed: 1,
      },
      physics: {
        enabled: !disablePhysics,
        stabilization: {
          iterations: disablePhysics ? 0 : 500,
        },
      },
      interaction: {
        // Enable mouse wheel and touchpad pinch zoom on graph canvas.
        zoomView: true,
        // Enable dragging on empty canvas to pan whole graph.
        dragView: true,
      },
      nodes: {
        shape: "box",
        margin: {
          top: graphMode === "hierarchy" ? 8 : 10,
          right: graphMode === "hierarchy" ? 8 : 10,
          bottom: graphMode === "hierarchy" ? 8 : 10,
          left: graphMode === "hierarchy" ? 8 : 10,
        },
        borderWidth: 2,
        font: {
          size: graphMode === "hierarchy" ? 13 : 14,
          color: "#000000",
        },
        shadow: {
          enabled: true,
          color: "rgba(0,0,0,0.2)",
          size: 3,
          x: 3,
          y: 3,
        },
        scaling: {
          label: {
            enabled: true,
            min: 14,
            max: 16,
          },
        },
      },
      edges: {
        arrows: {
          to: { enabled: true, scaleFactor: 0.5 },
        },
        width: 1.5,
        color: {
          color: "#64748b",
        },
        selectionWidth: 0,
        chosen: false,
      },
    }),
    [disablePhysics, graphMode],
  );

  const handleZoomIn = () => {
    if (networkRef.current) {
      const currentScale = networkRef.current.getScale();
      networkRef.current.moveTo({
        scale: currentScale * 1.2,
      });
    }
  };

  const handleZoomOut = () => {
    if (networkRef.current) {
      const currentScale = networkRef.current.getScale();
      networkRef.current.moveTo({
        scale: currentScale / 1.2,
      });
    }
  };

  const handleReset = () => {
    if (networkRef.current) {
      networkRef.current.fit({
        animation: {
          duration: 300,
          easingFunction: "easeInOutQuad",
        },
      });
    }
  };

  const searchResultNodeIds = useMemo(() => {
    const normalizedQuery = normalizeSearchText(searchQuery);
    if (!normalizedQuery) {
      return [];
    }
    const queryTokens = tokenizeSearchText(normalizedQuery);

    const scoredMatches = graphData.nodes
      .map((node) => {
        const searchableText = buildGraphNodeSearchText({
          node,
          graphMode,
        });
        const labelText = normalizeSearchText(node.label);
        const idText = normalizeSearchText(node.id);

        if (!queryTokens.every((token) => searchableText.includes(token))) {
          return null;
        }

        const score =
          idText === normalizedQuery
            ? 0
            : idText.startsWith(normalizedQuery)
              ? 1
              : labelText === normalizedQuery
                ? 2
                : labelText.startsWith(normalizedQuery)
                  ? 3
                  : searchableText.includes(normalizedQuery)
                    ? 4
                    : 5;

        return { id: node.id, score };
      })
      .filter((item): item is { id: string; score: number } => item !== null)
      .sort((a, b) => {
        if (a.score !== b.score) return a.score - b.score;
        return a.id.localeCompare(b.id);
      });

    const uniqueNodeIds: string[] = [];
    const seen = new Set<string>();
    for (const item of scoredMatches) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      uniqueNodeIds.push(item.id);
    }
    return uniqueNodeIds;
  }, [graphData.nodes, graphMode, searchQuery]);

  const graphNodeIds = useMemo(
    () => new Set(graphData.nodes.map((node) => node.id)),
    [graphData.nodes],
  );

  const focusNode = useCallback(
    (nodeId: string): boolean => {
      const network = networkRef.current;
      if (!network) return false;
      if (!graphNodeIds.has(nodeId)) return false;

      try {
        network.focus(nodeId, {
          scale: Math.max(network.getScale(), 0.9),
          animation: {
            duration: 250,
            easingFunction: "easeInOutQuad",
          },
        });
        return true;
      } catch (error) {
        console.error("Error focusing node:", nodeId, error);
        return false;
      }
    },
    [graphNodeIds],
  );

  const findParentFallbackNodeId = useCallback(
    (nodeId: string): string | null => {
      const parentIds = Array.from(
        new Set(
          graphData.edges
            .filter((edge) => edge.to === nodeId)
            .map((edge) => edge.from)
            .filter((parentId) => graphNodeIds.has(parentId)),
        ),
      );

      for (const parentId of parentIds) {
        const observationIds = nodeToObservationsMap[parentId] ?? [];
        if (observationIds.length > 0 || isSystemNodeId(parentId)) {
          return parentId;
        }
      }

      return parentIds[0] ?? null;
    },
    [graphData.edges, graphNodeIds, nodeToObservationsMap],
  );

  const navigateToNode = useCallback(
    (nodeId: string): boolean => {
      const observationIds = nodeToObservationsMap[nodeId] ?? [];
      const canSyncSelection =
        observationIds.length > 0 || isSystemNodeId(nodeId);

      if (canSyncSelection && selectedNodeNameRef.current !== nodeId) {
        onCanvasNodeNameChangeRef.current(nodeId, {
          shouldCycleObservation: false,
        });
      }

      return focusNode(nodeId);
    },
    [focusNode, nodeToObservationsMap],
  );

  useEffect(() => {
    if (!isSearchOpen) return;
    const frameId = window.requestAnimationFrame(() => {
      searchInputRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(frameId);
  }, [isSearchOpen]);

  useEffect(() => {
    setActiveSearchResultIndex(0);
  }, [searchQuery, setActiveSearchResultIndex]);

  useEffect(() => {
    if (searchResultNodeIds.length === 0) return;
    if (activeSearchResultIndex <= searchResultNodeIds.length - 1) return;
    setActiveSearchResultIndex(searchResultNodeIds.length - 1);
  }, [
    searchResultNodeIds.length,
    activeSearchResultIndex,
    setActiveSearchResultIndex,
  ]);

  useEffect(() => {
    const normalizedQuery = normalizeSearchText(searchQuery);
    if (!normalizedQuery || searchResultNodeIds.length === 0) {
      lastAppliedSearchKeyRef.current[graphMode] = null;
      return;
    }

    const resultIndex = Math.min(
      activeSearchResultIndex,
      searchResultNodeIds.length - 1,
    );
    const matchedNodeId = searchResultNodeIds[resultIndex];
    if (!matchedNodeId) {
      return;
    }

    const appliedSearchKey = `${normalizedQuery}::${resultIndex}::${matchedNodeId}`;
    if (lastAppliedSearchKeyRef.current[graphMode] === appliedSearchKey) {
      return;
    }
    lastAppliedSearchKeyRef.current[graphMode] = appliedSearchKey;

    const matchedObservationIds = nodeToObservationsMap[matchedNodeId] ?? [];
    let didNavigate = false;

    if (matchedObservationIds.length > 0 || isSystemNodeId(matchedNodeId)) {
      didNavigate = navigateToNode(matchedNodeId);
    }

    if (!didNavigate) {
      const parentFallbackNodeId = findParentFallbackNodeId(matchedNodeId);
      if (parentFallbackNodeId) {
        didNavigate = navigateToNode(parentFallbackNodeId);
      }
    }
  }, [
    activeSearchResultIndex,
    findParentFallbackNodeId,
    graphMode,
    navigateToNode,
    nodeToObservationsMap,
    searchQuery,
    searchResultNodeIds,
  ]);

  const moveSearchSelection = useCallback(
    (direction: 1 | -1) => {
      if (searchResultNodeIds.length === 0) return;
      setActiveSearchResultIndex((currentIndex) => {
        const nextIndex =
          (currentIndex + direction + searchResultNodeIds.length) %
          searchResultNodeIds.length;
        return nextIndex;
      });
    },
    [searchResultNodeIds.length, setActiveSearchResultIndex],
  );

  const toggleSearch = useCallback(() => {
    setIsSearchOpen((previous) => {
      const next = !previous;
      if (!next) {
        setSearchQuery("");
        setActiveSearchResultIndex(0);
      }
      return next;
    });
  }, [setActiveSearchResultIndex, setIsSearchOpen, setSearchQuery]);

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }

    const nodesDataSet = new DataSet(nodes);
    nodesDataSetRef.current = nodesDataSet;

    // Create the network
    const network = new Network(
      containerRef.current,
      { ...graphData, nodes: nodesDataSet },
      options,
    );
    networkRef.current = network;
    network.fit({
      animation: false,
    });

    // Use click event instead of selectNode/deselectNode to handle cycling properly
    network.on("click", (params) => {
      if (params.nodes.length > 0) {
        // Node was clicked
        onCanvasNodeNameChangeRef.current(params.nodes[0], {
          shouldCycleObservation: true,
        });
      } else {
        // Empty area was clicked
        onCanvasNodeNameChangeRef.current(null);
        network.unselectAll();
      }
    });

    // Prevent dragging the view completely out of bounds
    // this resets the graph position so that always a little bit is visible
    const constrainView = () => {
      const position = network.getViewPosition();
      const scale = network.getScale();
      const container = containerRef.current;

      if (!container) return;
      const containerRect = container.getBoundingClientRect();

      const nodePositions = network.getPositions();
      const nodeIds = Object.keys(nodePositions);

      if (nodeIds.length === 0) {
        return;
      }

      let minX = Infinity,
        maxX = -Infinity,
        minY = Infinity,
        maxY = -Infinity;

      nodeIds.forEach((nodeId) => {
        const pos = nodePositions[nodeId];
        minX = Math.min(minX, pos.x);
        maxX = Math.max(maxX, pos.x);
        minY = Math.min(minY, pos.y);
        maxY = Math.max(maxY, pos.y);
      });

      // Add some padding for node sizes (approximate node width/height)
      const nodePadding = 100;
      const graphWidth = (maxX - minX + nodePadding * 2) * scale;
      const graphHeight = (maxY - minY + nodePadding * 2) * scale;

      // max amount that a graph can be dragged on respective axis
      const maxDragX = (containerRect.width / 2 + graphWidth * 0.35) / scale;
      const maxDragY = (containerRect.height / 2 + graphHeight * 0.35) / scale;

      // Clamp position within bounds
      const constrainedX = Math.max(-maxDragX, Math.min(maxDragX, position.x));
      const constrainedY = Math.max(-maxDragY, Math.min(maxDragY, position.y));

      if (constrainedX !== position.x || constrainedY !== position.y) {
        network.moveTo({
          position: { x: constrainedX, y: constrainedY },
          scale: scale,
          animation: false,
        });
      }
    };

    // Apply constraints after drag ends
    network.on("dragEnd", (params) => {
      // only if dragging graph not nodes
      if (params.nodes.length === 0) {
        constrainView();
      }
    });

    network.on("zoom", () => {
      constrainView();
    });

    // force redraw on resetting view
    const handleResize = () => {
      if (network) {
        network.redraw();
        network.fit();
      }
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      networkRef.current = null;
      nodesDataSetRef.current = null;
      network.destroy();
    };
  }, [graphData, nodes, options]);

  // Update node labels when observation indices change, without recreating network
  useEffect(() => {
    const nodesDataSet = nodesDataSetRef.current;
    if (!nodesDataSet) return;

    try {
      const updates: { id: string; label: string }[] = [];

      graphData.nodes.forEach((node) => {
        const isSystemNode =
          node.id === LANGFUSE_START_NODE_NAME ||
          node.id === LANGFUSE_END_NODE_NAME ||
          node.id === LANGGRAPH_START_NODE_NAME ||
          node.id === LANGGRAPH_END_NODE_NAME;

        if (isSystemNode) return;

        const observations = nodeToObservationsMap[node.id] || [];
        const currentIndex = currentObservationIndices[node.id] || 0;
        const counter =
          observations.length > 1
            ? ` (${observations.length - currentIndex}/${observations.length})`
            : "";

        const newLabel = `${node.label}${counter}`;
        updates.push({ id: node.id, label: newLabel });
      });

      if (updates.length > 0) {
        nodesDataSet.update(updates);
      }
    } catch (error) {
      console.error("Error updating node labels:", error);
    }
  }, [graphData.nodes, nodeToObservationsMap, currentObservationIndices]);

  useEffect(() => {
    const network = networkRef.current;
    if (!network) return;

    if (selectedNodeName) {
      // Validate that the node exists before trying to select it
      const nodeExists = graphData.nodes.some(
        (node) => node.id === selectedNodeName,
      );

      if (nodeExists) {
        try {
          network.selectNodes([selectedNodeName]);
        } catch (error) {
          console.error("Error selecting node:", selectedNodeName, error);
          // Fallback to clearing selection
          network.unselectAll();
        }
      } else {
        console.warn(
          "Cannot select node that doesn't exist:",
          selectedNodeName,
        );
        network.unselectAll();
      }
    } else {
      network.unselectAll();
    }
  }, [selectedNodeName, graphData.nodes]);

  if (!graphData.nodes.length) {
    return (
      <div className="flex h-full items-center justify-center">
        {localize(language, "No graph data available", "没有可用的图数据")}
      </div>
    );
  }

  return (
    <>
      <div
        className={cn(
          "relative h-full min-h-[50dvh] w-full pb-2",
          whiteBackground && "bg-white",
        )}
        onMouseEnter={() => setIsHovering(true)}
        onMouseLeave={() => setIsHovering(false)}
      >
        {(isHovering || isSearchOpen) && (
          <div className="absolute left-2 right-2 top-2 z-10 flex items-start justify-end gap-2">
            {isSearchOpen && (
              <div className="flex w-[min(26rem,calc(100%-2.75rem))] min-w-0 items-center gap-1 rounded-md border bg-background/95 p-1 shadow-md dark:shadow-border sm:w-auto sm:min-w-64">
                <Input
                  ref={searchInputRef}
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter") return;
                    event.preventDefault();
                    moveSearchSelection(event.shiftKey ? -1 : 1);
                  }}
                  placeholder={localize(
                    language,
                    "Search node...",
                    "搜索节点...",
                  )}
                  className="h-8 min-w-0 flex-1 border-0 bg-transparent shadow-none focus-visible:ring-0"
                />
                <span className="min-w-12 text-center text-xs text-muted-foreground">
                  {searchQuery.trim().length === 0
                    ? localize(language, "Search", "搜索")
                    : searchResultNodeIds.length === 0
                      ? localize(language, "No match", "无匹配")
                      : `${activeSearchResultIndex + 1}/${searchResultNodeIds.length}`}
                </span>
                <Button
                  onClick={() => moveSearchSelection(-1)}
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 p-1"
                  title={localize(language, "Previous match", "上一个匹配")}
                  disabled={searchResultNodeIds.length < 2}
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <Button
                  onClick={() => moveSearchSelection(1)}
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 p-1"
                  title={localize(language, "Next match", "下一个匹配")}
                  disabled={searchResultNodeIds.length < 2}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
                <Button
                  onClick={toggleSearch}
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 p-1"
                  title={localize(language, "Close search", "关闭搜索")}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            )}
            <div className="flex flex-col gap-1">
              <Button
                onClick={toggleSearch}
                variant="ghost"
                size="icon"
                className="p-1.5 shadow-md dark:shadow-border"
                title={localize(language, "Search nodes", "搜索节点")}
              >
                <Search className="h-4 w-4" />
              </Button>
              <Button
                onClick={handleZoomIn}
                variant="ghost"
                size="icon"
                className="p-1.5 shadow-md dark:shadow-border"
                title={localize(language, "Zoom in", "放大")}
              >
                <ZoomIn className="h-4 w-4" />
              </Button>
              <Button
                onClick={handleZoomOut}
                variant="ghost"
                size="icon"
                className="p-1.5 shadow-md dark:shadow-border"
                title={localize(language, "Zoom out", "缩小")}
              >
                <ZoomOut className="h-4 w-4" />
              </Button>
              <Button
                onClick={handleReset}
                variant="ghost"
                size="icon"
                className="p-1.5 shadow-md dark:shadow-border"
                title={localize(language, "Reset view", "重置视图")}
              >
                <RotateCcw className="h-4 w-4" />
              </Button>
              {allowFullscreen && (
                <Button
                  onClick={() => setIsFullscreenOpen(true)}
                  variant="ghost"
                  size="icon"
                  className="p-1.5 shadow-md dark:shadow-border"
                  title={localize(
                    language,
                    "Open fullscreen graph",
                    "打开全屏图表",
                  )}
                >
                  <Maximize2 className="h-4 w-4" />
                </Button>
              )}
              {onGraphModeToggle && (
                <Button
                  onClick={onGraphModeToggle}
                  variant="ghost"
                  size="icon"
                  className="p-1.5 shadow-md dark:shadow-border"
                  title={
                    graphMode === "hierarchy"
                      ? localize(
                          language,
                          "Switch to execution flow graph",
                          "切换到执行流图",
                        )
                      : localize(
                          language,
                          "Switch to hierarchy graph",
                          "切换到层级图",
                        )
                  }
                >
                  <GitBranch className="h-4 w-4" />
                </Button>
              )}
            </div>
          </div>
        )}
        <div ref={containerRef} className="h-full w-full" />
      </div>
      {allowFullscreen && (
        <Dialog open={isFullscreenOpen} onOpenChange={setIsFullscreenOpen}>
          <DialogContent size="xxl" className="overflow-hidden bg-white p-0">
            <div className="h-full w-full bg-white p-3">
              <TraceGraphCanvas
                graph={graphData}
                graphMode={graphMode}
                selectedNodeName={selectedNodeName}
                onCanvasNodeNameChange={onCanvasNodeNameChange}
                disablePhysics={disablePhysics}
                nodeToObservationsMap={nodeToObservationsMap}
                currentObservationIndices={currentObservationIndices}
                onGraphModeToggle={onGraphModeToggle}
                allowFullscreen={false}
                whiteBackground={true}
              />
            </div>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
};

function truncateText(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  return `${value.slice(0, maxLength - 1)}...`;
}

function normalizeSearchText(value: string): string {
  return value
    .toLowerCase()
    .replace(/[_./:-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function tokenizeSearchText(value: string): string[] {
  return normalizeSearchText(value).split(" ").filter(Boolean);
}

export function buildGraphNodeSearchText(params: {
  node: GraphNodeData;
  graphMode: TraceGraphMode;
}): string {
  const { node, graphMode } = params;
  const nodeId =
    graphMode === "hierarchy" && node.id.includes("::")
      ? ""
      : normalizeSearchText(node.id);
  const nodeLabel = normalizeSearchText(node.label);
  const nodeType = normalizeSearchText(node.type);
  const inputOutputText = extractInputOutputText(node.title);

  // Search scope whitelist:
  // - graph node id
  // - graph node label/type (node info shown on graph)
  // - input/output snippets from node title
  // Metadata summaries are intentionally excluded.
  return [nodeId, nodeLabel, nodeType, inputOutputText].join(" ").trim();
}

function extractInputOutputText(title: string | undefined): string {
  if (!title) return "";

  const titleLines = title
    .split("\n")
    .map((line) => normalizeSearchText(line))
    .filter(Boolean);

  const ioLines = titleLines.filter((line) => /\b(input|output)\b/.test(line));

  return ioLines.join(" ");
}

function isSystemNodeId(nodeId: string): boolean {
  return (
    nodeId === LANGFUSE_START_NODE_NAME ||
    nodeId === LANGFUSE_END_NODE_NAME ||
    nodeId === LANGGRAPH_START_NODE_NAME ||
    nodeId === LANGGRAPH_END_NODE_NAME
  );
}
