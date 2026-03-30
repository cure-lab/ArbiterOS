import {
  buildGraphFromStepData,
  buildHierarchyGraphFromStepData,
} from "@/src/features/trace-graph-view/buildGraphCanvasData";
import {
  formatParserNodeName,
  normalizeParserNodeNameForGraph,
  parseParserNodeName,
} from "@/src/features/trace-graph-view/nodeNameUtils";
import { type AgentGraphDataResponse } from "@/src/features/trace-graph-view/types";

function createObservation(
  overrides: Partial<AgentGraphDataResponse> = {},
): AgentGraphDataResponse {
  return {
    id: "obs-default",
    name: "default",
    node: "default",
    step: 1,
    parentObservationId: null,
    startTime: "2026-01-01T00:00:00.000Z",
    endTime: "2026-01-01T00:00:00.001Z",
    observationType: "TOOL",
    ...overrides,
  };
}

describe("parser node naming", () => {
  it("parses parser node names with turn and suffix", () => {
    const parsed = parseParserNodeName(
      "session.parser.turn_002.tool_result.web_search.4",
    );

    expect(parsed).toEqual({
      turn: 2,
      suffixSegments: ["tool_result", "web_search", "4"],
    });
  });

  it("formats parser tool result names for graph labels", () => {
    const formatted = formatParserNodeName(
      "session.parser.turn_002.tool_result.web_search.4",
      { multiline: true },
    );

    expect(formatted).toBe("Turn 2\nweb_search result #4");
  });

  it("formats parser container names for single-line header display", () => {
    const formatted = formatParserNodeName("parser.turn_002.tool_calls", {
      multiline: false,
    });

    expect(formatted).toBe("Turn 2 - Tool calls");
  });

  it("returns null for non-parser names", () => {
    expect(formatParserNodeName("regular.node.name")).toBeNull();
    expect(parseParserNodeName("regular.node.name")).toBeNull();
  });

  it("normalizes pre-tool parser nodes aligned with trace2 tree names", () => {
    const normalized = normalizeParserNodeNameForGraph(
      "session.parser.turn_002.pre_web_fetch.3",
    );
    expect(normalized).toBe("parser.pre_web_fetch.3");
  });
});

describe("buildGraphFromStepData parser pruning", () => {
  it("prunes redundant parser container nodes when detailed nodes exist", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "container",
        name: "container",
        node: "session.parser.turn_002.tool_calls",
        step: 1,
      }),
      createObservation({
        id: "call",
        name: "call",
        node: "session.parser.turn_002.tool_call.web_search.4",
        step: 2,
      }),
      createObservation({
        id: "result",
        name: "result",
        node: "session.parser.turn_002.tool_result.web_search.4",
        step: 3,
      }),
    ];

    const { graph, nodeToObservationsMap } = buildGraphFromStepData(data);
    const nodeIds = graph.nodes.map((n) => n.id);

    expect(nodeIds).toContain("parser.turn_002.tool_call.web_search.4");
    expect(nodeIds).toContain("parser.web_search.4");
    expect(nodeIds).not.toContain("parser.turn_002.tool_calls");
    expect(nodeToObservationsMap["parser.turn_002.tool_calls"]).toBe(undefined);
  });

  it("prunes parser container nodes even when no detailed nodes exist", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "container",
        name: "container",
        node: "session.parser.turn_002.tool_calls",
        step: 1,
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    const nodeIds = graph.nodes.map((n) => n.id);

    expect(nodeIds).not.toContain("parser.turn_002.tool_calls");
  });

  it("prunes parser structured_output nodes", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "kernel",
        name: "Topic - kernel.cognitive_core__respond",
        node: "Topic - kernel.cognitive_core__respond",
        step: 1,
        observationType: "AGENT",
      }),
      createObservation({
        id: "structured",
        name: "session.parser.turn_002.structured_output",
        node: "session.parser.turn_002.structured_output",
        step: 2,
        observationType: "SPAN",
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    const nodeIds = graph.nodes.map((n) => n.id);
    expect(nodeIds).not.toContain("parser.turn_002.structured_output");
  });

  it("uses normalized node ids/labels aligned with trace2 tree names", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "result",
        name: "result",
        node: "session.parser.turn_002.tool_result.web_search.4",
        step: 1,
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    const parserNode = graph.nodes.find((n) => n.id === "parser.web_search.4");

    expect(parserNode).toBeDefined();
    expect(parserNode?.label).toBe("parser.web_search.4");
    expect(parserNode?.title).toBe(undefined);
  });

  it("avoids self-loop edges when normalized node repeats across steps", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn",
        name: "session.turn.002",
        node: "session.turn.002",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "tool",
        name: "web_fetch.3",
        node: "web_fetch.3",
        step: 2,
        startTime: "2026-01-01T00:00:01.000Z",
        endTime: "2026-01-01T00:00:01.001Z",
        observationType: "TOOL",
      }),
      createObservation({
        id: "parser-result-a",
        name: "session.parser.turn_002.tool_result.web_fetch.3",
        node: "session.parser.turn_002.tool_result.web_fetch.3",
        step: 3,
        startTime: "2026-01-01T00:00:01.100Z",
        endTime: "2026-01-01T00:00:01.101Z",
        observationType: "SPAN",
      }),
      // same normalized node at a later step
      createObservation({
        id: "parser-result-b",
        name: "session.parser.turn_002.tool_result.web_fetch.3",
        node: "session.parser.turn_002.tool_result.web_fetch.3",
        step: 4,
        startTime: "2026-01-01T00:00:01.200Z",
        endTime: "2026-01-01T00:00:01.201Z",
        observationType: "SPAN",
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    expect(graph.edges.some((e) => e.from === e.to)).toBe(false);
  });

  it("does not connect parser nodes to __end__", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "parser-result",
        name: "session.parser.turn_002.tool_result.web_fetch.3",
        node: "session.parser.turn_002.tool_result.web_fetch.3",
        step: 1,
        observationType: "SPAN",
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    expect(graph.nodes.some((n) => n.id === "__end__")).toBe(true);
    expect(
      graph.edges.some(
        (e) => e.from === "parser.web_fetch.3" && e.to === "__end__",
      ),
    ).toBe(false);
  });

  it("only connects the global last main node to __end__", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn-1",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "a",
        name: "A - kernel.cognitive_core__respond",
        node: "A - kernel.cognitive_core__respond",
        step: 2,
        startTime: "2026-01-01T00:00:01.000Z",
        endTime: "2026-01-01T00:00:01.001Z",
        observationType: "AGENT",
      }),
      createObservation({
        id: "turn-2",
        name: "session.turn.002",
        node: "session.turn.002",
        step: 3,
        startTime: "2026-01-01T00:00:20.000Z",
        endTime: "2026-01-01T00:00:30.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "b",
        name: "B - kernel.cognitive_core__respond",
        node: "B - kernel.cognitive_core__respond",
        step: 4,
        startTime: "2026-01-01T00:00:21.000Z",
        endTime: "2026-01-01T00:00:21.001Z",
        observationType: "AGENT",
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    const edgesToEnd = graph.edges.filter((e) => e.to === "__end__");
    expect(edgesToEnd).toHaveLength(1);
    expect(edgesToEnd[0]?.from).toBe("B - kernel.cognitive_core__respond");
  });

  it("parser nodes have no outgoing edges", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "tool",
        name: "web_fetch.3",
        node: "web_fetch.3",
        step: 2,
        startTime: "2026-01-01T00:00:01.000Z",
        endTime: "2026-01-01T00:00:01.001Z",
        observationType: "TOOL",
      }),
      createObservation({
        id: "parser-result",
        name: "session.parser.turn_001.tool_result.web_fetch.3",
        node: "session.parser.turn_001.tool_result.web_fetch.3",
        step: 3,
        startTime: "2026-01-01T00:00:01.100Z",
        endTime: "2026-01-01T00:00:01.101Z",
        observationType: "SPAN",
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    expect(
      graph.edges.some(
        (e) =>
          e.from.startsWith("parser.") && !e.from.startsWith("parser.pre_"),
      ),
    ).toBe(false);
  });

  it("connects parser.pre_tool.{n} -> tool.{n}", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "parser-pre",
        name: "session.parser.turn_001.pre_web_fetch.3",
        node: "session.parser.turn_001.pre_web_fetch.3",
        step: 2,
        startTime: "2026-01-01T00:00:01.000Z",
        endTime: "2026-01-01T00:00:01.001Z",
        observationType: "SPAN",
      }),
      createObservation({
        id: "tool",
        name: "web_fetch.3",
        node: "web_fetch.3",
        step: 3,
        startTime: "2026-01-01T00:00:01.010Z",
        endTime: "2026-01-01T00:00:01.011Z",
        observationType: "TOOL",
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    expect(
      graph.edges.some(
        (e) => e.from === "parser.pre_web_fetch.3" && e.to === "web_fetch.3",
      ),
    ).toBe(true);
    expect(
      graph.edges.some(
        (e) =>
          e.from === "session.turn.001" && e.to === "parser.pre_web_fetch.3",
      ),
    ).toBe(true);
  });

  it("uses parser.pre_tool.{n} in main chain (not tool.{n})", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "parser-pre",
        name: "session.parser.turn_001.pre_web_fetch.3",
        node: "session.parser.turn_001.pre_web_fetch.3",
        step: 2,
        startTime: "2026-01-01T00:00:01.000Z",
        endTime: "2026-01-01T00:00:01.001Z",
        observationType: "SPAN",
      }),
      createObservation({
        id: "tool",
        name: "web_fetch.3",
        node: "web_fetch.3",
        step: 3,
        startTime: "2026-01-01T00:00:01.010Z",
        endTime: "2026-01-01T00:00:01.011Z",
        observationType: "TOOL",
      }),
      createObservation({
        id: "kernel",
        name: "Topic - kernel.cognitive_core__respond",
        node: "Topic - kernel.cognitive_core__respond",
        step: 4,
        startTime: "2026-01-01T00:00:01.020Z",
        endTime: "2026-01-01T00:00:01.021Z",
        observationType: "AGENT",
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    expect(
      graph.edges.some(
        (e) =>
          e.from === "parser.pre_web_fetch.3" &&
          e.to === "Topic - kernel.cognitive_core__respond",
      ),
    ).toBe(true);
    expect(
      graph.edges.some(
        (e) =>
          e.from === "web_fetch.3" &&
          e.to === "Topic - kernel.cognitive_core__respond",
      ),
    ).toBe(false);
  });

  it("turn grouping uses next turn start (not turn endTime)", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn-1",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:01.000Z", // early / unreliable
        observationType: "CHAIN",
      }),
      createObservation({
        id: "out-1",
        name: "session.output.turn_001",
        node: "session.output.turn_001",
        step: 2,
        startTime: "2026-01-01T00:00:05.000Z",
        endTime: "2026-01-01T00:00:05.001Z",
        observationType: "GENERATION",
      }),
      createObservation({
        id: "turn-2",
        name: "session.turn.002",
        node: "session.turn.002",
        step: 3,
        startTime: "2026-01-01T00:00:10.000Z",
        endTime: "2026-01-01T00:00:20.000Z",
        observationType: "CHAIN",
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    expect(
      graph.edges.some(
        (e) =>
          e.from === "session.turn.001" && e.to === "session.output.turn_001",
      ),
    ).toBe(true);
  });

  it("connects session.trace.start when present", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "trace-start",
        name: "session.trace.start",
        node: "session.trace.start",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:00.001Z",
        observationType: "SPAN",
      }),
      createObservation({
        id: "turn-1",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 2,
        startTime: "2026-01-01T00:00:01.000Z",
        endTime: "2026-01-01T00:00:02.000Z",
        observationType: "CHAIN",
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    expect(
      graph.edges.some(
        (e) => e.from === "__start__" && e.to === "session.trace.start",
      ),
    ).toBe(true);
    expect(
      graph.edges.some(
        (e) => e.from === "session.trace.start" && e.to === "session.turn.001",
      ),
    ).toBe(true);
  });

  it("keeps session.trace.start before turn chain when timestamps tie", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn-1",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "trace-start",
        name: "session.trace.start",
        node: "session.trace.start",
        step: 2,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:00.001Z",
        parentObservationId: "turn-1",
        observationType: "SPAN",
      }),
      createObservation({
        id: "kernel",
        name: "Topic - kernel.cognitive_core__respond",
        node: "Topic - kernel.cognitive_core__respond",
        step: 3,
        startTime: "2026-01-01T00:00:01.000Z",
        endTime: "2026-01-01T00:00:01.001Z",
        observationType: "AGENT",
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    expect(
      graph.edges.some(
        (e) => e.from === "__start__" && e.to === "session.trace.start",
      ),
    ).toBe(true);
    expect(
      graph.edges.some(
        (e) => e.from === "session.trace.start" && e.to === "session.turn.001",
      ),
    ).toBe(true);
    expect(
      graph.edges.some(
        (e) => e.from === "session.turn.001" && e.to === "session.trace.start",
      ),
    ).toBe(false);
  });

  it("numbers session.failure nodes to keep them distinct", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn-1",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      // Two failures that would otherwise collapse into one node name.
      createObservation({
        id: "fail-a",
        name: "session.failure",
        node: "session.turn.001", // simulate metadata pointing elsewhere
        step: 2,
        startTime: "2026-01-01T00:00:01.000Z",
        endTime: "2026-01-01T00:00:01.001Z",
        observationType: "SPAN",
        level: "ERROR",
      }),
      createObservation({
        id: "fail-b",
        name: "session.failure",
        node: "session.turn.001",
        step: 3,
        startTime: "2026-01-01T00:00:02.000Z",
        endTime: "2026-01-01T00:00:02.001Z",
        observationType: "SPAN",
        level: "ERROR",
      }),
    ];

    const { graph } = buildGraphFromStepData(data);
    const nodeIds = graph.nodes.map((n) => n.id);
    expect(nodeIds).toContain("session.failure.1");
    expect(nodeIds).toContain("session.failure.2");
  });
});

describe("buildHierarchyGraphFromStepData summaries", () => {
  it("aggregates turn activity and risk metrics from graph payload and metadata", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn-1",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "kernel-1",
        name: "Trip planning - kernel.cognitive_core__respond",
        node: "Trip planning - kernel.cognitive_core__respond",
        step: 2,
        startTime: "2026-01-01T00:00:01.000Z",
        endTime: "2026-01-01T00:00:03.000Z",
        observationType: "AGENT",
        category: "COGNITIVE_CORE__RESPOND",
      }),
      createObservation({
        id: "tool-1",
        name: "web_search.1",
        node: "web_search.1",
        step: 3,
        startTime: "2026-01-01T00:00:04.000Z",
        endTime: "2026-01-01T00:00:05.000Z",
        observationType: "TOOL",
        level: "ERROR",
        toolName: "web_search",
        traceIdConsistent: false,
      }),
      createObservation({
        id: "turn-2",
        name: "session.turn.002",
        node: "session.turn.002",
        step: 4,
        startTime: "2026-01-01T00:00:20.000Z",
        endTime: "2026-01-01T00:00:30.000Z",
        observationType: "CHAIN",
      }),
    ];

    const { graph } = buildHierarchyGraphFromStepData({
      data,
      observationMetadataById: {
        "kernel-1": { topic: "Trip planning" },
      },
    });

    const turnNode = graph.nodes.find((n) => n.id === "session.turn.001");
    expect(turnNode).toBeDefined();
    expect(turnNode?.metadataSummary?.topic).toBe("Trip planning");
    expect(turnNode?.metadataSummary?.instructionType).toBe("RESPOND");
    expect(turnNode?.metadataSummary?.observationCount).toBe(3);
    expect(turnNode?.metadataSummary?.toolCount).toBe(1);
    expect(turnNode?.metadataSummary?.errorCount).toBe(1);
    expect(turnNode?.metadataSummary?.parserInconsistencyCount).toBe(1);
    expect(turnNode?.level).toBe("ERROR");
    expect(turnNode?.title).toContain("Tool nodes: 1");
    expect(turnNode?.title).toContain("Errors: 1");
    expect(turnNode?.title).toContain("Parser consistency issues: 1");
  });

  it("falls back to observation name/topic extraction and category-derived instruction type", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn-1",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "kernel-1",
        name: "Portfolio review - kernel.cognitive_core__plan",
        node: "Portfolio review - kernel.cognitive_core__plan",
        step: 2,
        startTime: "2026-01-01T00:00:01.000Z",
        endTime: "2026-01-01T00:00:03.000Z",
        observationType: "AGENT",
      }),
    ];

    const { graph } = buildHierarchyGraphFromStepData({ data });
    const turnNode = graph.nodes.find((n) => n.id === "session.turn.001");

    expect(turnNode?.metadataSummary?.topic).toBe("Portfolio review");
    expect(turnNode?.metadataSummary?.category).toBe("COGNITIVE_CORE__PLAN");
    expect(turnNode?.metadataSummary?.instructionType).toBe("PLAN");
  });

  it("marks warning-level turn risk for warnings and parser consistency mismatches", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn-1",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "tool-1",
        name: "web_fetch.1",
        node: "web_fetch.1",
        step: 2,
        startTime: "2026-01-01T00:00:02.000Z",
        endTime: "2026-01-01T00:00:03.000Z",
        observationType: "TOOL",
        level: "WARNING",
      }),
      createObservation({
        id: "parser-1",
        name: "session.parser.turn_001.tool_result.web_fetch.1",
        node: "session.parser.turn_001.tool_result.web_fetch.1",
        step: 3,
        startTime: "2026-01-01T00:00:03.500Z",
        endTime: "2026-01-01T00:00:03.700Z",
        observationType: "SPAN",
      }),
    ];

    const { graph } = buildHierarchyGraphFromStepData({
      data,
      observationMetadataById: {
        "parser-1": {
          trace_id_consistent: "false",
        },
      },
    });

    const turnNode = graph.nodes.find((n) => n.id === "session.turn.001");
    expect(turnNode).toBeDefined();
    expect(turnNode?.metadataSummary?.warningCount).toBe(1);
    expect(turnNode?.metadataSummary?.errorCount).toBe(0);
    expect(turnNode?.metadataSummary?.parserInconsistencyCount).toBe(1);
    expect(turnNode?.level).toBe("WARNING");
    expect(turnNode?.title).toContain("Warnings: 1");
    expect(turnNode?.title).toContain("Parser consistency issues: 1");
  });

  it("deduplicates tool + parser.tool_result for same invocation", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn-1",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "tool-1",
        name: "web_fetch.3",
        node: "web_fetch.3",
        step: 2,
        startTime: "2026-01-01T00:00:02.000Z",
        endTime: "2026-01-01T00:00:03.000Z",
        observationType: "TOOL",
        toolName: "web_fetch",
      }),
      createObservation({
        id: "parser-result-1",
        name: "session.parser.turn_001.tool_result.web_fetch.3",
        node: "session.parser.turn_001.tool_result.web_fetch.3",
        step: 3,
        startTime: "2026-01-01T00:00:03.500Z",
        endTime: "2026-01-01T00:00:03.700Z",
        observationType: "SPAN",
      }),
    ];

    const { graph } = buildHierarchyGraphFromStepData({
      data,
      observationMetadataById: {
        "parser-result-1": {
          tool_name: "web_fetch",
          node_type: "tool_result",
        },
      },
    });

    const turnNode = graph.nodes.find((n) => n.id === "session.turn.001");
    expect(turnNode?.metadataSummary?.toolCount).toBe(1);
    expect(turnNode?.metadataSummary?.toolBreakdown).toEqual([
      {
        toolName: "web_fetch",
        count: 1,
        instructionTypes: null,
        hasBlock: null,
      },
    ]);

    const toolsNode = graph.nodes.find(
      (n) => n.id === "session.turn.001::tools",
    );
    expect(toolsNode?.label).toBe("Tools\n1");

    const perToolNode = graph.nodes.find(
      (n) => n.id === "session.turn.001::tool::web_fetch",
    );
    expect(perToolNode?.label).toContain("×1");
  });

  it("deduplicates repeated observation ids within the same turn window", () => {
    const turnObservation = createObservation({
      id: "turn-1",
      name: "session.turn.001",
      node: "session.turn.001",
      step: 1,
      startTime: "2026-01-01T00:00:00.000Z",
      endTime: "2026-01-01T00:00:10.000Z",
      observationType: "CHAIN",
    });
    const toolObservation = createObservation({
      id: "tool-1",
      name: "web_fetch.1",
      node: "web_fetch.1",
      step: 2,
      startTime: "2026-01-01T00:00:02.000Z",
      endTime: "2026-01-01T00:00:03.000Z",
      observationType: "TOOL",
      toolName: "web_fetch",
    });
    const data: AgentGraphDataResponse[] = [
      turnObservation,
      toolObservation,
      { ...toolObservation }, // duplicated row from exported traces
    ];

    const { graph } = buildHierarchyGraphFromStepData({ data });
    const turnNode = graph.nodes.find((n) => n.id === "session.turn.001");

    expect(turnNode?.metadataSummary?.observationCount).toBe(2);
    expect(turnNode?.metadataSummary?.toolCount).toBe(1);
    expect(turnNode?.metadataSummary?.toolBreakdown).toEqual([
      {
        toolName: "web_fetch",
        count: 1,
        instructionTypes: null,
        hasBlock: null,
      },
    ]);
  });

  it("does not count parser observations as tools", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn-1",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "parser-pre",
        name: "session.parser.turn_001.pre_web_fetch.1",
        node: "session.parser.turn_001.pre_web_fetch.1",
        step: 2,
        startTime: "2026-01-01T00:00:02.000Z",
        endTime: "2026-01-01T00:00:03.000Z",
        observationType: "SPAN",
      }),
      createObservation({
        id: "parser-result",
        name: "session.parser.turn_001.tool_result.web_fetch.1",
        node: "session.parser.turn_001.tool_result.web_fetch.1",
        step: 3,
        startTime: "2026-01-01T00:00:03.500Z",
        endTime: "2026-01-01T00:00:03.700Z",
        observationType: "SPAN",
      }),
    ];

    const { graph } = buildHierarchyGraphFromStepData({
      data,
      observationMetadataById: {
        "parser-result": {
          tool_name: "web_fetch",
          node_type: "tool_result",
        },
      },
    });
    const turnNode = graph.nodes.find((n) => n.id === "session.turn.001");
    expect(turnNode?.metadataSummary?.toolCount).toBe(0);

    expect(graph.nodes.some((n) => n.id === "session.turn.001::tools")).toBe(
      false,
    );
  });

  it("does not render a policy node when policy is only inferred from instruction metadata", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn-1",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "kernel-1",
        name: "Budget review - kernel.cognitive_core__respond",
        node: "Budget review - kernel.cognitive_core__respond",
        step: 2,
        startTime: "2026-01-01T00:00:01.000Z",
        endTime: "2026-01-01T00:00:03.000Z",
        observationType: "AGENT",
      }),
    ];

    const { graph } = buildHierarchyGraphFromStepData({
      data,
      observationMetadataById: {
        "kernel-1": {
          instruction_type: "RESPOND",
          instruction_category: "EXECUTION.Human",
        },
      },
    });

    const turnNode = graph.nodes.find((n) => n.id === "session.turn.001");
    const policyNode = graph.nodes.find(
      (n) => n.id === "session.turn.001::policy",
    );

    expect(turnNode?.metadataSummary?.policy?.authorityLabel).toBeNull();
    expect(turnNode?.metadataSummary?.policy?.inferredFromInstruction).toBe(
      true,
    );
    expect(policyNode).toBeUndefined();
    expect(graph.nodes.some((n) => n.label.includes("UNKNOWN"))).toBe(false);
  });

  it("does not render a policy node even when concrete policy metadata exists", () => {
    const data: AgentGraphDataResponse[] = [
      createObservation({
        id: "turn-1",
        name: "session.turn.001",
        node: "session.turn.001",
        step: 1,
        startTime: "2026-01-01T00:00:00.000Z",
        endTime: "2026-01-01T00:00:10.000Z",
        observationType: "CHAIN",
      }),
      createObservation({
        id: "kernel-1",
        name: "Budget review - kernel.cognitive_core__respond",
        node: "Budget review - kernel.cognitive_core__respond",
        step: 2,
        startTime: "2026-01-01T00:00:01.000Z",
        endTime: "2026-01-01T00:00:03.000Z",
        observationType: "AGENT",
      }),
    ];

    const { graph } = buildHierarchyGraphFromStepData({
      data,
      observationMetadataById: {
        "kernel-1": {
          instruction_type: "RESPOND",
          instruction_category: "EXECUTION.Human",
          policy_authority_label: "SYSTEM",
          policy_has_block: true,
        },
      },
    });

    const turnNode = graph.nodes.find((n) => n.id === "session.turn.001");
    const policyNode = graph.nodes.find(
      (n) => n.id === "session.turn.001::policy",
    );

    expect(turnNode?.metadataSummary?.policy?.authorityLabel).toBe("SYSTEM");
    expect(turnNode?.metadataSummary?.policy?.hasBlock).toBe(true);
    expect(policyNode).toBeUndefined();
  });
});
