import { env } from "@/src/env.mjs";
import { aggregateScores } from "@/src/features/scores/lib/aggregateScores";
import {
  AGGREGATABLE_SCORE_TYPES,
  filterAndValidateDbScoreList,
} from "@langfuse/shared";
import {
  getObservationsTableWithModelData,
  getObservationsWithModelDataFromEventsTable,
  getScoresForObservations,
  logger,
  traceException,
} from "@langfuse/shared/src/server";
import { type GetAllGenerationsInput } from "../getAllQueries";

export function shouldFallbackToLegacyObservationsTable(
  error: unknown,
): boolean {
  if (!(error instanceof Error)) return false;

  return /(?:unknown_table|unknown table(?: expression identifier)?|doesn't exist).*events_(?:core|full)|events_(?:core|full).*(?:unknown_table|unknown table|doesn't exist)/i.test(
    error.message,
  );
}

export async function getAllGenerations({
  input,
  selectIOAndMetadata,
}: {
  input: GetAllGenerationsInput;
  selectIOAndMetadata: boolean;
}) {
  const queryOpts = {
    projectId: input.projectId,
    filter: input.filter,
    orderBy: input.orderBy,
    searchQuery: input.searchQuery ?? undefined,
    searchType: input.searchType,
    selectIOAndMetadata: selectIOAndMetadata,
    offset: input.page * input.limit,
    limit: input.limit,
  };
  const eventsEnabled =
    env.LANGFUSE_ENABLE_EVENTS_TABLE_OBSERVATIONS === "true";
  let generations = eventsEnabled
    ? await (async () => {
        try {
          return await getObservationsWithModelDataFromEventsTable(queryOpts);
        } catch (error) {
          if (!shouldFallbackToLegacyObservationsTable(error)) {
            throw error;
          }
          const errorMessage =
            error instanceof Error ? error.message : String(error);

          logger.warn(
            "generations.all events-table lookup failed, falling back to legacy observations lookup",
            {
              projectId: input.projectId,
              error: errorMessage,
            },
          );

          return getObservationsTableWithModelData(queryOpts);
        }
      })()
    : await getObservationsTableWithModelData(queryOpts);

  // Compatibility fallback: some installations still ingest into the legacy observations table
  // while the events-based list view is enabled. In that case, the list would be empty even
  // though traces show observations. If events returned no rows, fall back to legacy.
  if (eventsEnabled && generations.length === 0) {
    generations = await getObservationsTableWithModelData(queryOpts);
  }

  const scores = await getScoresForObservations({
    projectId: input.projectId,
    observationIds: generations.map((gen) => gen.id),
    excludeMetadata: true,
    includeHasMetadata: true,
  });

  const validatedScores = filterAndValidateDbScoreList({
    scores,
    dataTypes: AGGREGATABLE_SCORE_TYPES,
    includeHasMetadata: true,
    onParseError: traceException,
  });

  const fullGenerations = generations.map((generation) => {
    const filteredScores = aggregateScores(
      validatedScores.filter((s) => s.observationId === generation.id),
    );
    return {
      ...generation,
      scores: filteredScores,
    };
  });

  return {
    generations: fullGenerations,
  };
}
