import { type TraceDomain } from "@langfuse/shared";
import { type UrlUpdateType } from "use-query-params";
import { type ObservationReturnTypeWithMetadata } from "@/src/server/api/routers/traces";
import { type ScoreDomain } from "@langfuse/shared";
import { type WithStringifiedMetadata } from "@/src/utils/clientSideDomainTypes";
import { TraceDataProvider } from "./contexts/TraceDataContext";
import { ViewPreferencesProvider } from "./contexts/ViewPreferencesContext";
import { SelectionProvider } from "./contexts/SelectionContext";
import { SearchProvider } from "./contexts/SearchContext";
import { JsonExpansionProvider } from "./contexts/JsonExpansionContext";
import { TraceGraphDataProvider } from "./contexts/TraceGraphDataContext";
import { TraceLayoutMobile } from "./components/_layout/TraceLayoutMobile";
import { TraceLayoutDesktop } from "./components/_layout/TraceLayoutDesktop";
import { TracePanelNavigation } from "./components/_layout/TracePanelNavigation";
import { TracePanelDetail } from "./components/_layout/TracePanelDetail";
import { TracePanelNavigationLayoutDesktop } from "./components/_layout/TracePanelNavigationLayoutDesktop";
import { TracePanelNavigationLayoutMobile } from "./components/_layout/TracePanelNavigationLayoutMobile";
import { useIsMobile } from "@/src/hooks/use-mobile";
import { useTraceComments } from "./api/useTraceComments";
import { useViewPreferences } from "./contexts/ViewPreferencesContext";
import { useTraceGraphData } from "./contexts/TraceGraphDataContext";
import { TraceGraphView } from "./components/TraceGraphView/TraceGraphView";
import { TraceGovernanceBanner } from "./components/GovernanceBanner/TraceGovernanceBanner";

import { useMemo } from "react";

export type TraceProps = {
  observations: Array<ObservationReturnTypeWithMetadata>;
  trace: Omit<WithStringifiedMetadata<TraceDomain>, "input" | "output"> & {
    input: string | null;
    output: string | null;
  };
  scores: WithStringifiedMetadata<ScoreDomain>[];
  corrections: ScoreDomain[];
  projectId: string;
  policyConfirmationTurnIndexes?: number[];
  viewType?: "detailed" | "focused";
  context?: "peek" | "fullscreen";
  isValidObservationId?: boolean;
  selectedTab?: string;
  setSelectedTab?: (
    newValue?: string | null,
    updateType?: UrlUpdateType,
  ) => void;
};

export function Trace({
  trace,
  observations,
  scores,
  corrections,
  projectId,
  policyConfirmationTurnIndexes,
  context,
}: TraceProps) {
  // Fetch comment counts using existing hook
  const { observationCommentCounts, traceCommentCount } = useTraceComments({
    projectId,
    traceId: trace.id,
  });

  // Merge observation + trace comments into single Map for TraceDataContext
  const commentsMap = useMemo(() => {
    const map = new Map(observationCommentCounts);
    if (traceCommentCount > 0) {
      map.set(trace.id, traceCommentCount);
    }
    return map;
  }, [observationCommentCounts, traceCommentCount, trace.id]);

  return (
    <ViewPreferencesProvider traceContext={context}>
      <TraceDataProvider
        trace={trace}
        observations={observations}
        policyConfirmationTurnIndexes={policyConfirmationTurnIndexes}
        serverScores={scores}
        corrections={corrections}
        comments={commentsMap}
      >
        <TraceGraphDataProvider
          projectId={trace.projectId}
          traceId={trace.id}
          observations={observations}
        >
          <SelectionProvider>
            <SearchProvider>
              <JsonExpansionProvider>
                <TraceContent />
              </JsonExpansionProvider>
            </SearchProvider>
          </SelectionProvider>
        </TraceGraphDataProvider>
      </TraceDataProvider>
    </ViewPreferencesProvider>
  );
}

/**
 * TraceContent - Platform detection and routing component
 *
 * Purpose:
 * - Detects mobile vs desktop viewport
 * - Routes to appropriate platform-specific implementation
 * - Manages shared graph visibility logic
 *
 * Hooks:
 * - useIsMobile() - for responsive platform detection
 * - useViewPreferences() - for graph toggle state
 * - useTraceGraphData() - for graph availability
 */
function TraceContent() {
  const isMobile = useIsMobile();
  const { showGraph } = useViewPreferences();
  const { isGraphViewAvailable } = useTraceGraphData();
  const shouldShowGraph = showGraph && isGraphViewAvailable;

  return (
    <div className="flex h-full w-full flex-col gap-2 overflow-hidden p-2">
      <TraceGovernanceBanner />
      <div className="min-h-0 flex-1">
        {isMobile ? (
          <MobileTraceContent shouldShowGraph={shouldShowGraph} />
        ) : (
          <DesktopTraceContent shouldShowGraph={shouldShowGraph} />
        )}
      </div>
    </div>
  );
}

/**
 * DesktopTraceContent - Desktop layout composition
 *
 * Purpose:
 * - Composes desktop-specific layout structure
 * - Horizontal resizable panels with collapse functionality
 * - Navigation panel (left) + Graph panel (middle) + Detail panel (right)
 */
function DesktopTraceContent({
  shouldShowGraph,
}: {
  shouldShowGraph: boolean;
}) {
  return (
    <TraceLayoutDesktop>
      <TraceLayoutDesktop.NavigationPanel>
        <TracePanelNavigationLayoutDesktop>
          <TracePanelNavigation />
        </TracePanelNavigationLayoutDesktop>
      </TraceLayoutDesktop.NavigationPanel>
      <TraceLayoutDesktop.ResizeHandle />
      {shouldShowGraph ? (
        <>
          <TraceLayoutDesktop.GraphPanel defaultSize={32} minSize={20}>
            <TracePanelGraph />
          </TraceLayoutDesktop.GraphPanel>
          <TraceLayoutDesktop.ResizeHandle />
          <TraceLayoutDesktop.DetailPanel defaultSize={40} minSize={30}>
            <TracePanelDetail />
          </TraceLayoutDesktop.DetailPanel>
        </>
      ) : (
        <TraceLayoutDesktop.DetailPanel defaultSize={70} minSize={40}>
          <TracePanelDetail />
        </TraceLayoutDesktop.DetailPanel>
      )}
    </TraceLayoutDesktop>
  );
}

function TracePanelGraph() {
  return (
    <div className="flex h-full w-full flex-col border-r bg-background">
      <TraceGraphView />
    </div>
  );
}

/**
 * MobileTraceContent - Mobile layout composition
 *
 * Purpose:
 * - Composes mobile-specific layout structure
 * - Vertical accordion-style panels
 * - Navigation panel (top, collapsible) + Detail panel (bottom)
 */
function MobileTraceContent({ shouldShowGraph }: { shouldShowGraph: boolean }) {
  return (
    <div className="h-full w-full">
      <TraceLayoutMobile>
        <TraceLayoutMobile.NavigationPanel>
          <TracePanelNavigationLayoutMobile
            secondaryContent={shouldShowGraph ? <TraceGraphView /> : undefined}
          >
            <TracePanelNavigation />
          </TracePanelNavigationLayoutMobile>
        </TraceLayoutMobile.NavigationPanel>
        <TraceLayoutMobile.DetailPanel>
          <TracePanelDetail />
        </TraceLayoutMobile.DetailPanel>
      </TraceLayoutMobile>
    </div>
  );
}
