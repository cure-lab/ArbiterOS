import { buildGraphNodeSearchText } from "@/src/features/trace-graph-view/components/TraceGraphCanvas";
import { type GraphNodeData } from "@/src/features/trace-graph-view/types";

describe("TraceGraphCanvas search scope", () => {
  const baseNode: Omit<GraphNodeData, "id" | "label" | "type"> = {};

  it("ignores synthetic hierarchy child ids when building search text", () => {
    const searchText = buildGraphNodeSearchText({
      graphMode: "hierarchy",
      node: {
        ...baseNode,
        id: "session.turn.001::tool::web_fetch",
        label: "web_fetch\n×2",
        type: "TOOL",
      },
    });

    expect(searchText).toContain("web fetch");
    expect(searchText).not.toContain("session turn 001");
  });

  it("keeps node ids searchable in execution mode", () => {
    const searchText = buildGraphNodeSearchText({
      graphMode: "execution",
      node: {
        ...baseNode,
        id: "session.turn.001::tool::web_fetch",
        label: "web_fetch\n×2",
        type: "TOOL",
      },
    });

    expect(searchText).toContain("session turn 001");
  });
});
