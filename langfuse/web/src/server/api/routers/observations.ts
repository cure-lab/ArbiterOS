import { env } from "@/src/env.mjs";
import {
  createTRPCRouter,
  protectedGetTraceProcedure,
} from "@/src/server/api/trpc";
import { parseIO } from "@langfuse/shared";
import {
  getObservationById,
  getObservationByIdFromEventsTable,
  logger,
} from "@langfuse/shared/src/server";
import { TRPCError } from "@trpc/server";
import { z } from "zod/v4";
import { toDomainWithStringifiedMetadata } from "@/src/utils/clientSideDomainTypes";

/**
 * Check whether an error is a Langfuse "not-found" error.
 * We use a property-based check instead of `instanceof` because the monorepo
 * bundler can produce duplicate class identities for the shared package,
 * which causes `instanceof` to fail.
 */
const isNotFoundError = (e: unknown): e is Error & { httpCode: number } =>
  e instanceof Error &&
  "httpCode" in e &&
  (e as Record<string, unknown>).httpCode === 404;

export const observationsRouter = createTRPCRouter({
  byId: protectedGetTraceProcedure
    .input(
      z.object({
        observationId: z.string(),
        traceId: z.string(), // required for protectedGetTraceProcedure
        projectId: z.string(), // required for protectedGetTraceProcedure
        startTime: z.date().nullish(),
        verbosity: z.enum(["compact", "truncated", "full"]).default("full"),
      }),
    )
    .query(async ({ input }) => {
      const queryOpts = {
        id: input.observationId,
        projectId: input.projectId,
        fetchWithInputOutput: true,
        traceId: input.traceId,
        startTime: input.startTime ?? undefined,
        renderingProps: {
          truncated: input.verbosity === "truncated",
          shouldJsonParse: false,
        },
      };

      let obs;
      try {
        if (env.LANGFUSE_ENABLE_EVENTS_TABLE_OBSERVATIONS === "true") {
          try {
            // Prefer events table when enabled (v4), but fall back to legacy
            // observations table for traces that are not present in events yet.
            obs = await getObservationByIdFromEventsTable(queryOpts);
          } catch (e) {
            if (!isNotFoundError(e)) {
              logger.warn(
                "observations.byId events-table lookup failed, falling back to legacy observations lookup",
                {
                  observationId: input.observationId,
                  traceId: input.traceId,
                  projectId: input.projectId,
                  error: e instanceof Error ? e.message : String(e),
                },
              );
            }
            obs = await getObservationById(queryOpts);
          }
        } else {
          obs = await getObservationById(queryOpts);
        }
      } catch (e) {
        if (isNotFoundError(e)) {
          throw new TRPCError({
            code: "NOT_FOUND",
            message: e.message,
          });
        }
        throw e;
      }

      if (!obs) {
        throw new TRPCError({
          code: "NOT_FOUND",
          message: "Observation not found within authorized project",
        });
      }
      return {
        ...toDomainWithStringifiedMetadata(obs),
        input: parseIO(obs.input, input.verbosity) as string,
        output: parseIO(obs.output, input.verbosity) as string,
        internalModel: obs?.internalModelId,
      };
    }),
});
