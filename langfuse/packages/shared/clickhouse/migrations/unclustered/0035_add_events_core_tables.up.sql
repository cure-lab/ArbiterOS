CREATE TABLE IF NOT EXISTS events
(
    project_id String,
    trace_id String,
    span_id String,
    parent_span_id String,

    start_time DateTime64(6),
    end_time Nullable(DateTime64(6)),

    -- Core properties
    name String,
    type LowCardinality(String),
    environment LowCardinality(String) DEFAULT 'default',
    version String,
    release String,

    trace_name String,
    user_id String,
    session_id String,

    tags Array(String),
    bookmarked Bool DEFAULT false,
    public Bool DEFAULT false,

    level LowCardinality(String),
    status_message String,
    completion_start_time Nullable(DateTime64(6)),

    -- Prompt
    prompt_id String,
    prompt_name String,
    prompt_version Nullable(UInt16),

    -- Model
    model_id String,
    provided_model_name String,
    model_parameters String,
    model_parameters_json JSON MATERIALIZED model_parameters::JSON,

    -- Usage
    provided_usage_details Map(LowCardinality(String), UInt64),
    provided_usage_details_json JSON(max_dynamic_paths=64, max_dynamic_types=8) MATERIALIZED provided_usage_details::JSON,
    usage_details Map(LowCardinality(String), UInt64),
    usage_details_json JSON(
      max_dynamic_paths=64,
      max_dynamic_types=8,
      input UInt64,
      output UInt64,
      total UInt64
    ) MATERIALIZED usage_details::JSON,
    provided_cost_details Map(LowCardinality(String), Decimal(18,12)),
    provided_cost_details_json JSON(max_dynamic_paths=64, max_dynamic_types=8) MATERIALIZED provided_cost_details::JSON,
    cost_details Map(LowCardinality(String), Decimal(18,12)),
    cost_details_json JSON(
      max_dynamic_paths=64,
      max_dynamic_types=8,
      input Decimal(18,12),
      output Decimal(18,12),
      total Decimal(18,12)
    ) MATERIALIZED cost_details::JSON,
    calculated_input_cost Decimal(18, 12) MATERIALIZED arraySum(mapValues(mapFilter(x -> positionCaseInsensitive(x.1, 'input') > 0, cost_details))),
    calculated_output_cost Decimal(18, 12) MATERIALIZED arraySum(mapValues(mapFilter(x -> positionCaseInsensitive(x.1, 'output') > 0, cost_details))),
    calculated_total_cost Decimal(18, 12) MATERIALIZED arraySum(mapValues(mapFilter(x -> positionCaseInsensitive(x.1, 'input') > 0 OR positionCaseInsensitive(x.1, 'output') > 0, cost_details))),
    total_cost Decimal(18, 12) ALIAS cost_details_json.total,
    usage_pricing_tier_id Nullable(String),
    usage_pricing_tier_name Nullable(String),

    -- Tools
    tool_definitions Map(String, String),
    tool_calls Array(String),
    tool_call_names Array(String),

    -- I/O
    input String CODEC(ZSTD(3)),
    input_truncated String MATERIALIZED leftUTF8(input, 1024),
    input_length UInt64 MATERIALIZED lengthUTF8(input),
    output String CODEC(ZSTD(3)),
    output_truncated String MATERIALIZED leftUTF8(output, 1024),
    output_length UInt64 MATERIALIZED lengthUTF8(output),

    -- Metadata
    metadata JSON(max_dynamic_paths=0),
    metadata_names Array(String),
    metadata_raw_values Array(String),
    metadata_prefixes Array(String) MATERIALIZED arrayMap(v -> leftUTF8(CAST(v, 'String'), 200), metadata_raw_values),
    metadata_hashes Array(Nullable(UInt32)) MATERIALIZED arrayMap(v -> if(lengthUTF8(CAST(v, 'String')) > 200, xxHash32(CAST(v, 'String')), NULL), metadata_raw_values),
    metadata_long_values Map(UInt32, String) MATERIALIZED mapFromArrays(
      arrayMap(v -> xxHash32(CAST(v, 'String')), arrayFilter(v -> lengthUTF8(CAST(v, 'String')) > 200, metadata_raw_values)),
      arrayMap(v -> CAST(v, 'String'), arrayFilter(v -> lengthUTF8(CAST(v, 'String')) > 200, metadata_raw_values))
    ),

    -- Experiment properties
    experiment_id String,
    experiment_name String,
    experiment_metadata_names Array(String),
    experiment_metadata_values Array(String),
    experiment_description String,
    experiment_dataset_id String,
    experiment_item_id String,
    experiment_item_version Nullable(DateTime64(6)),
    experiment_item_expected_output String,
    experiment_item_metadata_names Array(String),
    experiment_item_metadata_values Array(String),
    experiment_item_root_span_id String,

    -- Source metadata (Instrumentation)
    source LowCardinality(String),
    service_name String,
    service_version String,
    scope_name String,
    scope_version String,
    telemetry_sdk_language LowCardinality(String),
    telemetry_sdk_name String,
    telemetry_sdk_version String,

    -- Generic props
    blob_storage_file_path String,
    event_bytes UInt64,
    created_at DateTime64(6) DEFAULT now(),
    updated_at DateTime64(6) DEFAULT now(),
    event_ts DateTime64(6),
    is_deleted UInt8,

    -- Indexes
    INDEX idx_span_id span_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_trace_id trace_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_type type TYPE set(50) GRANULARITY 1,
    INDEX idx_created_at created_at TYPE minmax GRANULARITY 1,
    INDEX idx_updated_at updated_at TYPE minmax GRANULARITY 1
)
ENGINE = ReplacingMergeTree(event_ts, is_deleted)
PARTITION BY toYYYYMM(start_time)
PRIMARY KEY (project_id, start_time, xxHash32(trace_id))
ORDER BY (project_id, start_time, xxHash32(trace_id), span_id)
SAMPLE BY xxHash32(trace_id)
SETTINGS
    index_granularity = 8192,
    index_granularity_bytes = '64Mi',
    enable_block_number_column = 1,
    enable_block_offset_column = 1,
    dynamic_serialization_version='v3',
    object_serialization_version='v3',
    object_shared_data_serialization_version='advanced',
    object_shared_data_serialization_version_for_zero_level_parts='map_with_buckets';

CREATE TABLE IF NOT EXISTS events_full
(
    project_id String,
    trace_id String,
    span_id String,
    parent_span_id String,

    start_time DateTime64(6),
    end_time Nullable(DateTime64(6)),

    -- Core properties
    name String,
    type LowCardinality(String),
    environment LowCardinality(String) DEFAULT 'default',
    version String,
    release String,
    trace_name String,
    user_id String,
    session_id String,
    tags Array(String),
    level LowCardinality(String),
    status_message String,
    completion_start_time Nullable(DateTime64(6)),

    -- Updateable properties
    bookmarked Bool DEFAULT false,
    public Bool DEFAULT false,

    -- Prompt
    prompt_id String,
    prompt_name String,
    prompt_version Nullable(UInt16),

    -- Model
    model_id String,
    provided_model_name String,
    model_parameters String,

    -- Usage and Cost
    provided_usage_details Map(LowCardinality(String), UInt64),
    usage_details Map(LowCardinality(String), UInt64),
    provided_cost_details Map(LowCardinality(String), Decimal(18,12)),
    cost_details Map(LowCardinality(String), Decimal(18,12)),
    calculated_input_cost Decimal(18, 12) MATERIALIZED arraySum(mapValues(mapFilter(x -> positionCaseInsensitive(x.1, 'input') > 0, cost_details))),
    calculated_output_cost Decimal(18, 12) MATERIALIZED arraySum(mapValues(mapFilter(x -> positionCaseInsensitive(x.1, 'output') > 0, cost_details))),
    calculated_total_cost Decimal(18, 12) MATERIALIZED arraySum(mapValues(mapFilter(x -> positionCaseInsensitive(x.1, 'input') > 0 OR positionCaseInsensitive(x.1, 'output') > 0, cost_details))),
    total_cost Decimal(18, 12) ALIAS cost_details['total'],

    usage_pricing_tier_id Nullable(String),
    usage_pricing_tier_name Nullable(String),

    -- Tools
    tool_definitions Map(String, String),
    tool_calls Array(String),
    tool_call_names Array(String),

    -- I/O
    input String CODEC(ZSTD(3)),
    input_length UInt64 MATERIALIZED lengthUTF8(input),
    output String CODEC(ZSTD(3)),
    output_length UInt64 MATERIALIZED lengthUTF8(output),

    -- Metadata
    metadata_names Array(String),
    metadata_values Array(String),

    -- Experiment properties
    experiment_id String,
    experiment_name String,
    experiment_metadata_names Array(String),
    experiment_metadata_values Array(String),
    experiment_description String,
    experiment_dataset_id String,
    experiment_item_id String,
    experiment_item_version Nullable(DateTime64(6)),
    experiment_item_expected_output String,
    experiment_item_metadata_names Array(String),
    experiment_item_metadata_values Array(String),
    experiment_item_root_span_id String,

    -- Source metadata (Instrumentation)
    source LowCardinality(String),
    service_name String,
    service_version String,
    scope_name String,
    scope_version String,
    telemetry_sdk_language LowCardinality(String),
    telemetry_sdk_name String,
    telemetry_sdk_version String,

    -- Generic props
    blob_storage_file_path String,
    event_bytes UInt64,
    created_at DateTime64(6) DEFAULT now(),
    updated_at DateTime64(6) DEFAULT now(),
    event_ts DateTime64(6),
    is_deleted UInt8,

    -- Indexes
    INDEX idx_span_id span_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_trace_id trace_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_user_id user_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_session_id session_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_created_at created_at TYPE minmax GRANULARITY 1,
    INDEX idx_updated_at updated_at TYPE minmax GRANULARITY 1
)
ENGINE = ReplacingMergeTree(event_ts, is_deleted)
PARTITION BY toYYYYMM(start_time)
PRIMARY KEY (project_id, toStartOfMinute(start_time), xxHash32(trace_id))
ORDER BY (project_id, toStartOfMinute(start_time), xxHash32(trace_id), span_id, start_time)
SAMPLE BY xxHash32(trace_id)
SETTINGS
    index_granularity_bytes = '64Mi',
    merge_max_block_size_bytes = '64Mi',
    enable_block_number_column = 1,
    enable_block_offset_column = 1;

CREATE TABLE IF NOT EXISTS events_core
(
    project_id String,
    trace_id String,
    span_id String,
    parent_span_id String,

    start_time DateTime64(6),
    end_time Nullable(DateTime64(6)),

    -- Core properties
    name String,
    type LowCardinality(String),
    environment LowCardinality(String) DEFAULT 'default',
    version String,
    release String,
    trace_name String,
    user_id String,
    session_id String,
    tags Array(String),
    level LowCardinality(String),
    status_message String,
    completion_start_time Nullable(DateTime64(6)),

    -- Updateable properties
    bookmarked Bool DEFAULT false,
    public Bool DEFAULT false,

    -- Prompt
    prompt_id String,
    prompt_name String,
    prompt_version Nullable(UInt16),

    -- Model
    model_id String,
    provided_model_name String,
    model_parameters String,

    -- Usage
    provided_usage_details Map(LowCardinality(String), UInt64),
    usage_details Map(LowCardinality(String), UInt64),
    provided_cost_details Map(LowCardinality(String), Decimal(18,12)),
    cost_details Map(LowCardinality(String), Decimal(18,12)),
    calculated_input_cost Decimal(18, 12) MATERIALIZED arraySum(mapValues(mapFilter(x -> positionCaseInsensitive(x.1, 'input') > 0, cost_details))),
    calculated_output_cost Decimal(18, 12) MATERIALIZED arraySum(mapValues(mapFilter(x -> positionCaseInsensitive(x.1, 'output') > 0, cost_details))),
    calculated_total_cost Decimal(18, 12) MATERIALIZED arraySum(mapValues(mapFilter(x -> positionCaseInsensitive(x.1, 'input') > 0 OR positionCaseInsensitive(x.1, 'output') > 0, cost_details))),
    total_cost Decimal(18, 12) ALIAS cost_details['total'],

    usage_pricing_tier_id Nullable(String),
    usage_pricing_tier_name Nullable(String),

    -- Tools
    tool_definitions Map(String, String),
    tool_calls Array(String),
    tool_call_names Array(String),

    -- I/O
    input String,
    input_length UInt64 MATERIALIZED lengthUTF8(input),
    output String,
    output_length UInt64 MATERIALIZED lengthUTF8(output),

    -- Metadata
    metadata_names Array(String),
    metadata_values Array(String),

    -- Experiment properties
    experiment_id String,
    experiment_name String,
    experiment_metadata_names Array(String),
    experiment_metadata_values Array(String),
    experiment_description String,
    experiment_dataset_id String,
    experiment_item_id String,
    experiment_item_version Nullable(DateTime64(6)),
    experiment_item_expected_output String,
    experiment_item_metadata_names Array(String),
    experiment_item_metadata_values Array(String),
    experiment_item_root_span_id String,

    -- Source metadata (Instrumentation)
    source LowCardinality(String),
    service_name String,
    service_version String,
    scope_name String,
    scope_version String,
    telemetry_sdk_language LowCardinality(String),
    telemetry_sdk_name String,
    telemetry_sdk_version String,

    -- Generic props
    blob_storage_file_path String,
    event_bytes UInt64,
    created_at DateTime64(6) DEFAULT now(),
    updated_at DateTime64(6) DEFAULT now(),
    event_ts DateTime64(6),
    is_deleted UInt8,

    -- Indexes
    INDEX idx_span_id span_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_trace_id trace_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_user_id user_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_session_id session_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_created_at created_at TYPE minmax GRANULARITY 1,
    INDEX idx_updated_at updated_at TYPE minmax GRANULARITY 1
)
ENGINE = ReplacingMergeTree(event_ts, is_deleted)
PARTITION BY toYYYYMM(start_time)
PRIMARY KEY (project_id, toStartOfMinute(start_time), xxHash32(trace_id))
ORDER BY (project_id, toStartOfMinute(start_time), xxHash32(trace_id), span_id, start_time)
SAMPLE BY xxHash32(trace_id)
SETTINGS
    enable_block_number_column = 1,
    enable_block_offset_column = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS events_core_mv TO events_core AS
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
    leftUTF8(input, 200) as input,
    leftUTF8(output, 200) as output,
    metadata_names,
    arrayMap(v -> leftUTF8(v, 200), metadata_values) as metadata_values,
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
FROM events_full;

CREATE MATERIALIZED VIEW IF NOT EXISTS events_full_mv TO events_full AS
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
FROM events;
