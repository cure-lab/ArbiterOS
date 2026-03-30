/**
 * TraceGraphView wrapper for trace2
 *
 * This component wraps the TraceGraphView from features/trace-graph-view/
 * and uses data from GraphDataContext.
 */

import { TraceGraphView as TraceGraphViewComponent } from "@/src/features/trace-graph-view/components/TraceGraphView";
import { useTraceGraphData } from "../../contexts/TraceGraphDataContext";
import { useViewPreferences } from "../../contexts/ViewPreferencesContext";
import { useTraceData } from "../../contexts/TraceDataContext";
import { useMemo } from "react";

export function TraceGraphView() {
  const { agentGraphData, isLoading } = useTraceGraphData();
  const { graphViewMode, setGraphViewMode } = useViewPreferences();
  const { observations } = useTraceData();

  const observationMetadataById = useMemo(() => {
    return Object.fromEntries(
      observations.map((observation) => {
        if (!observation.metadata) {
          return [observation.id, null] as const;
        }

        try {
          const parsed = JSON.parse(observation.metadata);
          return [
            observation.id,
            typeof parsed === "object" && parsed !== null
              ? (parsed as Record<string, unknown>)
              : null,
          ] as const;
        } catch {
          return [observation.id, null] as const;
        }
      }),
    );
  }, [observations]);

  if (isLoading) {
    return (
      <div className="flex h-full w-full items-center justify-center">
        <span className="text-sm text-muted-foreground">Loading graph...</span>
      </div>
    );
  }

  if (agentGraphData.length === 0) {
    return null;
  }

  return (
    <TraceGraphViewComponent
      agentGraphData={agentGraphData}
      graphMode={graphViewMode}
      onGraphModeChange={setGraphViewMode}
      observationMetadataById={observationMetadataById}
    />
  );
}
