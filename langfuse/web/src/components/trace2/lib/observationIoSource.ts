import { normalizeParserNodeNameForGraph } from "@/src/features/trace-graph-view/nodeNameUtils";

type ObservationLike = {
  id: string;
  name: string | null;
  startTime: Date;
  endTime: Date | null;
};

export type ObservationIoSource = {
  observationId: string;
  startTime: Date;
};

const SESSION_TURN_NODE_RE = /^session\.turn\.(?<turn>\d+)$/;
const STRUCTURED_OUTPUT_UI_NODE_RE =
  /^parser\.turn_\d+\.(?:structured_output|strucutured_output)$/;

export function isKernelObservationName(
  observationName: string | null | undefined,
): boolean {
  return (
    typeof observationName === "string" &&
    observationName.includes(" - kernel.")
  );
}

export function isStructuredOutputObservationName(
  observationName: string | null | undefined,
): boolean {
  if (!observationName) {
    return false;
  }
  const uiName = normalizeParserNodeNameForGraph(observationName);
  if (!uiName) {
    return false;
  }
  return STRUCTURED_OUTPUT_UI_NODE_RE.test(uiName);
}

/**
 * Builds a UI-only kernel -> structured_output IO source map.
 * For kernel nodes, preview/log I/O should come from the paired structured_output
 * observation, while preserving the kernel node identity in UI.
 */
export function buildKernelObservationIoSourceMap(
  observations: ObservationLike[],
): Map<string, ObservationIoSource> {
  const chronological = [...observations].sort(
    (a, b) => a.startTime.getTime() - b.startTime.getTime(),
  );

  const sessionTurns = chronological
    .map((obs) => {
      const match = SESSION_TURN_NODE_RE.exec(obs.name ?? "");
      if (!match?.groups?.turn) {
        return null;
      }
      const start = obs.startTime.getTime();
      return { id: obs.id, start };
    })
    .filter((turn) => turn !== null)
    .sort((a, b) => a.start - b.start);

  const sessionTurnEndBounds = sessionTurns.map((turn, idx) => {
    const next = sessionTurns[idx + 1];
    return next ? next.start : Number.POSITIVE_INFINITY;
  });

  const latestKernelIdByTurnId = new Map<string, string>();
  let latestKernelId: string | null = null;
  let activeTurnIndex = 0;

  const ioSourceByKernelId = new Map<string, ObservationIoSource>();

  for (const obs of chronological) {
    while (
      activeTurnIndex < sessionTurns.length &&
      obs.startTime.getTime() >= sessionTurnEndBounds[activeTurnIndex]!
    ) {
      activeTurnIndex++;
    }

    const activeTurn = sessionTurns[activeTurnIndex];
    const activeTurnEndBound = sessionTurnEndBounds[activeTurnIndex];
    const isWithinActiveTurn =
      !!activeTurn &&
      obs.startTime.getTime() >= activeTurn.start &&
      obs.startTime.getTime() <
        (activeTurnEndBound ?? Number.POSITIVE_INFINITY);

    if (isKernelObservationName(obs.name)) {
      latestKernelId = obs.id;
      if (activeTurn && isWithinActiveTurn) {
        latestKernelIdByTurnId.set(activeTurn.id, obs.id);
      }
      continue;
    }

    if (!isStructuredOutputObservationName(obs.name)) {
      continue;
    }

    const kernelId =
      (activeTurn ? latestKernelIdByTurnId.get(activeTurn.id) : null) ??
      latestKernelId;
    if (!kernelId || kernelId === obs.id) {
      continue;
    }

    ioSourceByKernelId.set(kernelId, {
      observationId: obs.id,
      startTime: obs.startTime,
    });
  }

  return ioSourceByKernelId;
}
