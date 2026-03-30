import { IBackgroundMigration } from "./IBackgroundMigration";
import { prisma } from "@langfuse/shared/src/db";
import {
  commandClickhouse,
  logger,
  queryClickhouse,
} from "@langfuse/shared/src/server";
import { parseArgs } from "node:util";

// Hard-coded migration ID (must match the Prisma migration INSERT)
const backgroundMigrationId = "3e6be5ee-8f93-4bf4-8eb5-cab5bd01395c";
const DEFAULT_MAX_RETRIES = 3;

type PartitionStatus = "pending" | "in_progress" | "completed" | "failed";

interface PartitionTodo {
  partition: string;
  status: PartitionStatus;
  startedAt?: string;
  completedAt?: string;
  retryCount?: number;
  error?: string;
}

interface MigrationState {
  phase: "init" | "backfill" | "completed";
  partitions: PartitionTodo[];
  totalProcessed: number;
  lastUpdated: string;
}

interface MigrationArgs {
  maxRetries?: number;
}

const buildInsertQuery = () => `
INSERT INTO events_full (
  project_id,
  trace_id,
  span_id,
  parent_span_id,
  start_time,
  end_time,
  name,
  type,
  environment,
  version,
  release,
  trace_name,
  user_id,
  session_id,
  tags,
  level,
  status_message,
  completion_start_time,
  bookmarked,
  public,
  prompt_id,
  prompt_name,
  prompt_version,
  model_id,
  provided_model_name,
  model_parameters,
  provided_usage_details,
  usage_details,
  provided_cost_details,
  cost_details,
  usage_pricing_tier_id,
  usage_pricing_tier_name,
  tool_definitions,
  tool_calls,
  tool_call_names,
  input,
  output,
  metadata_names,
  metadata_values,
  experiment_id,
  experiment_name,
  experiment_metadata_names,
  experiment_metadata_values,
  experiment_description,
  experiment_dataset_id,
  experiment_item_id,
  experiment_item_version,
  experiment_item_expected_output,
  experiment_item_metadata_names,
  experiment_item_metadata_values,
  experiment_item_root_span_id,
  source,
  service_name,
  service_version,
  scope_name,
  scope_version,
  telemetry_sdk_language,
  telemetry_sdk_name,
  telemetry_sdk_version,
  blob_storage_file_path,
  event_bytes,
  created_at,
  updated_at,
  event_ts,
  is_deleted
)
SELECT
  project_id,
  trace_id,
  span_id,
  parent_span_id,
  start_time,
  end_time,
  name,
  type,
  environment,
  version,
  release,
  trace_name,
  user_id,
  session_id,
  tags,
  level,
  status_message,
  completion_start_time,
  bookmarked,
  public,
  prompt_id,
  prompt_name,
  prompt_version,
  model_id,
  provided_model_name,
  model_parameters,
  provided_usage_details,
  usage_details,
  provided_cost_details,
  cost_details,
  usage_pricing_tier_id,
  usage_pricing_tier_name,
  tool_definitions,
  tool_calls,
  tool_call_names,
  input,
  output,
  metadata_names,
  metadata_raw_values as metadata_values,
  experiment_id,
  experiment_name,
  experiment_metadata_names,
  experiment_metadata_values,
  experiment_description,
  experiment_dataset_id,
  experiment_item_id,
  experiment_item_version,
  experiment_item_expected_output,
  experiment_item_metadata_names,
  experiment_item_metadata_values,
  experiment_item_root_span_id,
  source,
  service_name,
  service_version,
  scope_name,
  scope_version,
  telemetry_sdk_language,
  telemetry_sdk_name,
  telemetry_sdk_version,
  blob_storage_file_path,
  event_bytes,
  created_at,
  updated_at,
  event_ts,
  is_deleted
FROM events
WHERE toYYYYMM(start_time) = {partition: UInt32}
`;

export default class BackfillEventsFullFromEvents
  implements IBackgroundMigration
{
  private isAborted = false;

  private async loadState(): Promise<MigrationState> {
    const migration = await prisma.backgroundMigration.findUnique({
      where: { id: backgroundMigrationId },
      select: { state: true },
    });

    const defaultState: MigrationState = {
      phase: "init",
      partitions: [],
      totalProcessed: 0,
      lastUpdated: new Date().toISOString(),
    };

    if (!migration || !migration.state) return defaultState;

    const state = migration.state as Partial<MigrationState>;
    return {
      phase: state.phase ?? defaultState.phase,
      partitions: state.partitions ?? defaultState.partitions,
      totalProcessed: state.totalProcessed ?? 0,
      lastUpdated: state.lastUpdated ?? defaultState.lastUpdated,
    };
  }

  private async updateState(state: MigrationState): Promise<void> {
    await prisma.backgroundMigration.update({
      where: { id: backgroundMigrationId },
      data: { state: state as any },
    });
  }

  private async loadPartitions(): Promise<string[]> {
    const rows = await queryClickhouse<{ partition: string }>({
      query: `
        SELECT DISTINCT toString(toYYYYMM(start_time)) AS partition
        FROM events
        ORDER BY partition DESC
      `,
      allowLegacyEventsRead: true,
      tags: {
        feature: "background-migration",
        operation: "loadEventsPartitions",
      },
    });

    return rows.map((r) => r.partition);
  }

  private async validateTables(): Promise<{ valid: boolean; reason?: string }> {
    const required = [
      "events",
      "events_full",
      "events_core",
      "events_full_mv",
      "events_core_mv",
    ];

    const rows = await queryClickhouse<{ name: string }>({
      query: `
        SELECT name
        FROM system.tables
        WHERE database = currentDatabase()
          AND name IN {required: Array(String)}
      `,
      params: { required },
      tags: {
        feature: "background-migration",
        operation: "validateEventsTables",
      },
    });

    const found = new Set(rows.map((r) => r.name));
    const missing = required.filter((table) => !found.has(table));
    if (missing.length > 0) {
      return {
        valid: false,
        reason: `Missing required ClickHouse tables/views: ${missing.join(", ")}`,
      };
    }

    return { valid: true };
  }

  async validate(
    _args: Record<string, unknown>,
  ): Promise<{ valid: boolean; invalidReason: string | undefined }> {
    try {
      const tableCheck = await this.validateTables();
      if (!tableCheck.valid) {
        return { valid: false, invalidReason: tableCheck.reason };
      }
      return { valid: true, invalidReason: undefined };
    } catch (error) {
      return {
        valid: false,
        invalidReason: error instanceof Error ? error.message : String(error),
      };
    }
  }

  async run(args: Record<string, unknown>): Promise<void> {
    const parsedArgs: MigrationArgs = {
      maxRetries:
        typeof args.maxRetries === "number"
          ? args.maxRetries
          : DEFAULT_MAX_RETRIES,
    };

    let state = await this.loadState();

    // Recover interrupted state by resetting any in-progress partition back to pending.
    if (state.partitions.some((p) => p.status === "in_progress")) {
      state.partitions = state.partitions.map((p) =>
        p.status === "in_progress"
          ? {
              ...p,
              status: "pending",
              retryCount: (p.retryCount ?? 0) + 1,
              error: "Recovered from interrupted execution",
            }
          : p,
      );
      state.lastUpdated = new Date().toISOString();
      await this.updateState(state);
    }

    if (state.phase === "init" || state.partitions.length === 0) {
      const partitions = await this.loadPartitions();
      state = {
        ...state,
        phase: "backfill",
        partitions: partitions.map((partition) => ({
          partition,
          status: "pending" as const,
          retryCount: 0,
        })),
        totalProcessed: 0,
        lastUpdated: new Date().toISOString(),
      };
      await this.updateState(state);
    }

    if (state.partitions.length === 0) {
      state.phase = "completed";
      state.lastUpdated = new Date().toISOString();
      await this.updateState(state);
      logger.info(
        "[Background Migration] No events partitions found, migration completed",
      );
      return;
    }

    logger.info(
      `[Background Migration] Backfilling events_full from events (${state.partitions.length} partitions)`,
    );

    for (const todo of state.partitions) {
      if (this.isAborted) {
        logger.info(
          "[Background Migration] BackfillEventsFullFromEvents aborted by signal",
        );
        return;
      }
      if (todo.status === "completed") continue;
      if (
        todo.status === "failed" &&
        (todo.retryCount ?? 0) >= (parsedArgs.maxRetries ?? DEFAULT_MAX_RETRIES)
      ) {
        continue;
      }

      todo.status = "in_progress";
      todo.startedAt = new Date().toISOString();
      todo.error = undefined;
      state.lastUpdated = new Date().toISOString();
      await this.updateState(state);

      try {
        await commandClickhouse({
          query: buildInsertQuery(),
          params: { partition: Number(todo.partition) },
          tags: {
            feature: "background-migration",
            operation: "backfillEventsFullPartition",
            partition: todo.partition,
          },
        });

        todo.status = "completed";
        todo.completedAt = new Date().toISOString();
        state.totalProcessed += 1;
        state.lastUpdated = new Date().toISOString();
        await this.updateState(state);
      } catch (error) {
        todo.retryCount = (todo.retryCount ?? 0) + 1;
        todo.status =
          todo.retryCount >= (parsedArgs.maxRetries ?? DEFAULT_MAX_RETRIES)
            ? "failed"
            : "pending";
        todo.error = error instanceof Error ? error.message : String(error);
        state.lastUpdated = new Date().toISOString();
        await this.updateState(state);

        if (todo.status === "failed") {
          throw new Error(
            `Partition ${todo.partition} exceeded retries: ${todo.error}`,
          );
        }
      }
    }

    const unfinished = state.partitions.filter((p) => p.status !== "completed");
    if (unfinished.length === 0) {
      state.phase = "completed";
      state.lastUpdated = new Date().toISOString();
      await this.updateState(state);
      logger.info(
        `[Background Migration] Completed events_full backfill (${state.totalProcessed} partitions)`,
      );
      return;
    }

    throw new Error(
      `Backfill finished with ${unfinished.length} unfinished partitions`,
    );
  }

  async abort(): Promise<void> {
    logger.info(
      "[Background Migration] Aborting BackfillEventsFullFromEvents migration",
    );
    this.isAborted = true;
  }
}

async function main() {
  const { values } = parseArgs({
    options: {
      maxRetries: { type: "string", short: "r" },
    },
  });

  const migration = new BackfillEventsFullFromEvents();
  const args: Record<string, unknown> = {};
  if (values.maxRetries) {
    const parsed = Number(values.maxRetries);
    if (!Number.isFinite(parsed) || parsed < 1) {
      throw new Error("maxRetries must be a positive number");
    }
    args.maxRetries = parsed;
  }

  await migration.validate(args);
  await migration.run(args);
}

if (require.main === module) {
  main()
    .then(() => {
      process.exit(0);
    })
    .catch((error) => {
      logger.error(
        `[Background Migration] Migration execution failed: ${error}`,
        error,
      );
      process.exit(1);
    });
}
