#!/bin/bash

# Development ClickHouse bootstrap script.
# This script creates dev-only helper tables and (re)seeds sample data.
# Canonical production schemas are managed via clickhouse/migrations.
#
# Usage:
#   pnpm run ch:dev-tables  (from packages/shared/)
#
# This script is automatically run as part of:
#   - pnpm run dx
#   - pnpm run dx-f
#   - pnpm run ch:reset

# Load environment variables
[ -f ../../.env ] && source ../../.env

# Check if CLICKHOUSE_MIGRATION_URL is configured
if [ -z "${CLICKHOUSE_MIGRATION_URL}" ]; then
  echo "Error: CLICKHOUSE_MIGRATION_URL is not configured."
  echo "Please set CLICKHOUSE_MIGRATION_URL in your environment variables."
  exit 1
fi

# Check if CLICKHOUSE_USER is set
if [ -z "${CLICKHOUSE_USER}" ]; then
  echo "Error: CLICKHOUSE_USER is not set."
  echo "Please set CLICKHOUSE_USER in your environment variables."
  exit 1
fi

# Check if CLICKHOUSE_PASSWORD is set
if [ -z "${CLICKHOUSE_PASSWORD}" ]; then
  echo "Error: CLICKHOUSE_PASSWORD is not set."
  echo "Please set CLICKHOUSE_PASSWORD in your environment variables."
  exit 1
fi

# Ensure CLICKHOUSE_DB is set
if [ -z "${CLICKHOUSE_DB}" ]; then
  export CLICKHOUSE_DB="default"
fi

# Parse the CLICKHOUSE_MIGRATION_URL to extract host and port
# Expected format: clickhouse://localhost:9000
if [[ $CLICKHOUSE_MIGRATION_URL =~ ^clickhouse://([^:]+):([0-9]+)$ ]]; then
  CLICKHOUSE_HOST="${BASH_REMATCH[1]}"
  CLICKHOUSE_PORT="${BASH_REMATCH[2]}"
elif [[ $CLICKHOUSE_MIGRATION_URL =~ ^clickhouse://([^:]+)$ ]]; then
  CLICKHOUSE_HOST="${BASH_REMATCH[1]}"
  CLICKHOUSE_PORT="9000" # Default native protocol port
else
  echo "Error: Could not parse CLICKHOUSE_MIGRATION_URL: ${CLICKHOUSE_MIGRATION_URL}"
  exit 1
fi

if ! command -v clickhouse &>/dev/null; then
  echo "Error: clickhouse binary could not be found. Please install ClickHouse client tools."
  exit 1
fi

echo "Creating development tables in ClickHouse..."

# Execute development-only table setup (schema migrations are applied via ch:up).

clickhouse client \
  --host="${CLICKHOUSE_HOST}" \
  --port="${CLICKHOUSE_PORT}" \
  --user="${CLICKHOUSE_USER}" \
  --password="${CLICKHOUSE_PASSWORD}" \
  --database="${CLICKHOUSE_DB}" \
  --multiquery <<EOF

-- Create observations_batch_staging table for batch processing
-- This table uses 3-minute partitions to efficiently process observations in batches
-- and merge them with traces data into the events table.
-- Partitions are automatically expired after 12 hours via TTL (ttl_only_drop_parts=1
-- ensures only complete partitions are dropped, not individual rows).
-- See LFE-7122 for implementation details.
CREATE TABLE IF NOT EXISTS observations_batch_staging
(
    id String,
    trace_id String,
    project_id String,
    type LowCardinality(String),
    parent_observation_id Nullable(String),
    start_time DateTime64(3),
    end_time Nullable(DateTime64(3)),
    name String,
    metadata Map(LowCardinality(String), String),
    level LowCardinality(String),
    status_message Nullable(String),
    version Nullable(String),
    input Nullable(String) CODEC(ZSTD(3)),
    output Nullable(String) CODEC(ZSTD(3)),
    provided_model_name Nullable(String),
    internal_model_id Nullable(String),
    model_parameters Nullable(String),
    provided_usage_details Map(LowCardinality(String), UInt64),
    usage_details Map(LowCardinality(String), UInt64),
    provided_cost_details Map(LowCardinality(String), Decimal64(12)),
    cost_details Map(LowCardinality(String), Decimal64(12)),
    total_cost Nullable(Decimal64(12)),
    usage_pricing_tier_id Nullable(String),
    usage_pricing_tier_name Nullable(String),
    tool_definitions Map(String, String),
    tool_calls Array(String),
    tool_call_names Array(String),
    completion_start_time Nullable(DateTime64(3)),
    prompt_id Nullable(String),
    prompt_name Nullable(String),
    prompt_version Nullable(UInt16),
    created_at DateTime64(3) DEFAULT now(),
    updated_at DateTime64(3) DEFAULT now(),
    event_ts DateTime64(3),
    is_deleted UInt8,
    s3_first_seen_timestamp DateTime64(3),
    environment LowCardinality(String) DEFAULT 'default',
) ENGINE = ReplacingMergeTree(event_ts, is_deleted)
PARTITION BY toStartOfInterval(s3_first_seen_timestamp, INTERVAL 3 MINUTE)
PRIMARY KEY (project_id, toDate(s3_first_seen_timestamp))
ORDER BY (
    project_id,
    toDate(s3_first_seen_timestamp),
    trace_id,
    id
)
TTL s3_first_seen_timestamp + INTERVAL 12 HOUR
SETTINGS ttl_only_drop_parts = 1;

-- events/events_full/events_core schemas and MVs are now managed via
-- official ClickHouse migrations.
-- Fail fast to make drift obvious when this script is run standalone.
SELECT throwIf(
  count() != 3,
  'Missing required events base tables (events, events_full, events_core). Run: pnpm --filter=shared ch:up'
)
FROM system.tables
WHERE database = currentDatabase()
  AND name IN ('events', 'events_full', 'events_core');

SELECT throwIf(
  count() != 2,
  'Missing required events materialized views (events_full_mv, events_core_mv). Run: pnpm --filter=shared ch:up'
)
FROM system.tables
WHERE database = currentDatabase()
  AND name IN ('events_full_mv', 'events_core_mv');

EOF

echo "Populating development tables with sample data..."

clickhouse client \
  --host="${CLICKHOUSE_HOST}" \
  --port="${CLICKHOUSE_PORT}" \
  --user="${CLICKHOUSE_USER}" \
  --password="${CLICKHOUSE_PASSWORD}" \
  --database="${CLICKHOUSE_DB}" \
  --multiquery <<EOF
  SET type_json_skip_duplicated_paths = 1;
  TRUNCATE events;
  TRUNCATE events_core;
  TRUNCATE events_full;

  -- Note: production excludes experiment traces here (LEFT ANTI JOIN dataset_run_items_rmt)
  -- and re-inserts them with experiment metadata via handleExperimentBackfill.
  -- For dev seeding, we include all traces directly to ensure events_core and
  -- traces/observations tables have matching row counts for dashboard testing.
  INSERT INTO events (project_id, trace_id, span_id, parent_span_id, start_time, end_time, name, type,
                      environment, version, release, tags, trace_name, user_id, session_id, public, bookmarked, level, status_message, completion_start_time, prompt_id,
                      prompt_name, prompt_version, model_id, provided_model_name, model_parameters,
                      provided_usage_details, usage_details, provided_cost_details, cost_details,
                      usage_pricing_tier_id, usage_pricing_tier_name,
                      tool_definitions, tool_calls, tool_call_names, input,
                      output, metadata, metadata_names, metadata_raw_values,
                      source, blob_storage_file_path, event_bytes,
                      created_at, updated_at, event_ts, is_deleted)
  SELECT o.project_id,
         o.trace_id,
         o.id                                                                            AS span_id,
         CASE
           WHEN o.id = concat('t-', o.trace_id) THEN ''
           ELSE coalesce(o.parent_observation_id, concat('t-', o.trace_id))
         END                                                                             AS parent_span_id,
         o.start_time,
         o.end_time,
         o.name,
         o.type,
         o.environment,
         coalesce(o.version, t.version)                                                  AS version,
         coalesce(t.release, '')                                                         AS release,
         t.tags                                                                          AS tags,
         t.name                                                                          AS trace_name,
         coalesce(t.user_id, '')                                                         AS user_id,
         coalesce(t.session_id, '')                                                      AS session_id,
         t.public                                                                        AS public,
         t.bookmarked AND (o.parent_observation_id IS NULL OR o.parent_observation_id = '') AS bookmarked,
         o.level,
         coalesce(o.status_message, '')                                                  AS status_message,
         o.completion_start_time,
         o.prompt_id,
         o.prompt_name,
         o.prompt_version,
         o.internal_model_id                                                             AS model_id,
         o.provided_model_name,
         coalesce(o.model_parameters, '{}'),
         o.provided_usage_details,
         o.usage_details,
         o.provided_cost_details,
         o.cost_details,
         o.usage_pricing_tier_id,
         o.usage_pricing_tier_name,
         o.tool_definitions,
         o.tool_calls,
         o.tool_call_names,
         coalesce(o.input, '')                                                           AS input,
         coalesce(o.output, '')                                                          AS output,
         CAST(mapConcat(o.metadata, coalesce(t.metadata, map())), 'JSON(max_dynamic_paths=0)') AS metadata,
         mapKeys(mapConcat(o.metadata, coalesce(t.metadata, map())))                     AS metadata_names,
         mapValues(mapConcat(o.metadata, coalesce(t.metadata, map())))                   AS metadata_raw_values,
         multiIf(mapContains(o.metadata, 'resourceAttributes'), 'otel-dual-write', 'ingestion-api-dual-write') AS source,
         ''                                                                              AS blob_storage_file_path,
         byteSize(*)                                                                     AS event_bytes,
         o.created_at,
         o.updated_at,
         o.event_ts,
         o.is_deleted
  FROM observations o FINAL
  LEFT JOIN traces t ON o.project_id = t.project_id AND o.trace_id = t.id
  WHERE (o.is_deleted = 0);
  -- Backfill events from traces table as well
  -- Traces are converted to synthetic observations with id = 't-' + trace_id
  -- (matching convertTraceToStagingObservation in the ingestion pipeline)
  INSERT INTO events (project_id, trace_id, span_id, parent_span_id, start_time, name, type,
                      environment, version, release, tags, trace_name, user_id, session_id, public, bookmarked, level,
                      model_parameters, provided_usage_details, usage_details, provided_cost_details, cost_details,
                      usage_pricing_tier_id, usage_pricing_tier_name,
                      tool_definitions, tool_calls, tool_call_names,
                      input, output,
                      metadata, metadata_names, metadata_raw_values,
                      source, blob_storage_file_path, event_bytes,
                      created_at, updated_at, event_ts, is_deleted)
  SELECT t.project_id,
         t.id,
         concat('t-', t.id)                                                              AS span_id,
         ''                                                                               AS parent_span_id,
         t.timestamp,
         t.name,
         'SPAN',
         t.environment,
         t.version,
         coalesce(t.release, '')                                                         AS release,
         t.tags                                                                          AS tags,
         t.name                                                                          AS trace_name,
         coalesce(t.user_id, '')                                                         AS user_id,
         coalesce(t.session_id, '')                                                      AS session_id,
         t.public                                                                        AS public,
         t.bookmarked                                                                    AS bookmarked,
         'DEFAULT'                                                                       AS level,
         '{}'                                                                            AS model_parameters,
         map(),
         map(),
         map(),
         map(),
         NULL,
         NULL,
         map(),
         [],
         [],
         coalesce(t.input, '')                                                           AS input,
         coalesce(t.output, '')                                                          AS output,
         CAST(t.metadata, 'JSON(max_dynamic_paths=0)'),
         mapKeys(t.metadata)                                                             AS metadata_names,
         mapValues(t.metadata)                                                           AS metadata_raw_values,
         multiIf(mapContains(t.metadata, 'resourceAttributes'), 'otel-dual-write', 'ingestion-api-dual-write') AS source,
         ''                                                                              AS blob_storage_file_path,
         byteSize(*)                                                                     AS event_bytes,
         t.created_at,
         t.updated_at,
         t.event_ts,
         t.is_deleted
  FROM traces t FINAL
  WHERE (t.is_deleted = 0);

EOF

echo "Development tables created successfully (or already exist)."
echo ""
