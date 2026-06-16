#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/print_session_tokens.sh [--show-cache] [--show-messages] <session-id>

Export an OpenCode session to ./<session-id>.json, then parse token usage
from the exported file and print it as a table. The script strips ANSI
escape sequences and ignores any non-JSON preamble before the JSON payload.

Options:
    --show-cache    Include cache token columns in the output tables.
    --show-messages Include the per-message table before agent summary.
EOF
}

require_cmd() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "Error: required command not found: $cmd" >&2
        exit 1
    fi
}

sanitize_session_file() {
    local file_path="$1"
    sed -E $'s/\x1B\[[0-9;]*[[:alpha:]]//g' "$file_path" | awk '
        BEGIN { started = 0 }
        {
            if (!started) {
                if ($0 ~ /^[[:space:]]*\{[[:space:]]*$/) {
                    started = 1
                    print
                }
                next
            }

            print
        }
    '
}

require_cmd jq
require_cmd column
require_cmd opencode

show_cache=0
show_messages=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --show-cache)
            show_cache=1
            shift
            ;;
        --show-messages)
            show_messages=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            echo "Error: unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            break
            ;;
    esac
done

if [[ $# -ne 1 ]]; then
    usage >&2
    exit 1
fi

session_id="$1"
session_file="./${session_id}.json"

if ! opencode export "$session_id" > "$session_file"; then
    echo "Error: failed to export session: $session_id" >&2
    exit 1
fi

json_payload="$(sanitize_session_file "$session_file")"

if [[ -z "$json_payload" ]]; then
    echo "Error: no JSON payload found in $session_file" >&2
    exit 1
fi

if ! printf '%s\n' "$json_payload" | jq empty >/dev/null 2>&1; then
    echo "Error: sanitized content is not valid JSON: $session_file" >&2
    exit 1
fi

message_query_no_cache='
    .messages
    | to_entries
    | map(select(.value.info.tokens != null)) as $rows
    | $rows[]
    | [
        (.key + 1 | tostring),
        (.value.info.agent // ""),
        (.value.info.mode // ""),
        (.value.info.finish // ""),
        (.value.info.tokens.total // 0),
        (.value.info.tokens.input // 0),
        (.value.info.tokens.output // 0),
        (.value.info.tokens.reasoning // 0),
        ((.value.info.id // "")[:16])
      ]
    | @tsv
'

message_total_query_no_cache='
    .messages
    | map(select(.info.tokens != null)) as $rows
    | [
        "TOTAL",
        "",
        "",
        "",
        ($rows | map(.info.tokens.total // 0) | add // 0),
        ($rows | map(.info.tokens.input // 0) | add // 0),
        ($rows | map(.info.tokens.output // 0) | add // 0),
        ($rows | map(.info.tokens.reasoning // 0) | add // 0),
        ""
      ]
    | @tsv
'

message_query_with_cache='
    .messages
    | to_entries
    | map(select(.value.info.tokens != null)) as $rows
    | $rows[]
    | [
        (.key + 1 | tostring),
        (.value.info.agent // ""),
        (.value.info.mode // ""),
        (.value.info.finish // ""),
        (.value.info.tokens.total // 0),
        (.value.info.tokens.input // 0),
        (.value.info.tokens.output // 0),
        (.value.info.tokens.reasoning // 0),
        (.value.info.tokens.cache.read // 0),
        (.value.info.tokens.cache.write // 0),
        ((.value.info.id // "")[:16])
      ]
    | @tsv
'

message_total_query_with_cache='
    .messages
    | map(select(.info.tokens != null)) as $rows
    | [
        "TOTAL",
        "",
        "",
        "",
        ($rows | map(.info.tokens.total // 0) | add // 0),
        ($rows | map(.info.tokens.input // 0) | add // 0),
        ($rows | map(.info.tokens.output // 0) | add // 0),
        ($rows | map(.info.tokens.reasoning // 0) | add // 0),
        ($rows | map(.info.tokens.cache.read // 0) | add // 0),
        ($rows | map(.info.tokens.cache.write // 0) | add // 0),
        ""
      ]
    | @tsv
'

agent_query_no_cache='
    .messages
    | map(select(.info.tokens != null))
    | sort_by(.info.agent // "")
    | group_by(.info.agent // "")[]
    | [
        (.[0].info.agent // ""),
        length,
        (map(.info.tokens.total // 0) | add // 0),
        (map(.info.tokens.input // 0) | add // 0),
        (map(.info.tokens.output // 0) | add // 0),
        (map(.info.tokens.reasoning // 0) | add // 0)
      ]
    | @tsv
'

agent_total_query_no_cache='
    .messages
    | map(select(.info.tokens != null)) as $rows
    | [
        "TOTAL",
        ($rows | length),
        ($rows | map(.info.tokens.total // 0) | add // 0),
        ($rows | map(.info.tokens.input // 0) | add // 0),
        ($rows | map(.info.tokens.output // 0) | add // 0),
        ($rows | map(.info.tokens.reasoning // 0) | add // 0)
      ]
    | @tsv
'

agent_query_with_cache='
    .messages
    | map(select(.info.tokens != null))
    | sort_by(.info.agent // "")
    | group_by(.info.agent // "")[]
    | [
        (.[0].info.agent // ""),
        length,
        (map(.info.tokens.total // 0) | add // 0),
        (map(.info.tokens.input // 0) | add // 0),
        (map(.info.tokens.output // 0) | add // 0),
        (map(.info.tokens.reasoning // 0) | add // 0),
        (map(.info.tokens.cache.read // 0) | add // 0),
        (map(.info.tokens.cache.write // 0) | add // 0)
      ]
    | @tsv
'

agent_total_query_with_cache='
    .messages
    | map(select(.info.tokens != null)) as $rows
    | [
        "TOTAL",
        ($rows | length),
        ($rows | map(.info.tokens.total // 0) | add // 0),
        ($rows | map(.info.tokens.input // 0) | add // 0),
        ($rows | map(.info.tokens.output // 0) | add // 0),
        ($rows | map(.info.tokens.reasoning // 0) | add // 0),
        ($rows | map(.info.tokens.cache.read // 0) | add // 0),
        ($rows | map(.info.tokens.cache.write // 0) | add // 0)
      ]
    | @tsv
'

if [[ "$show_cache" -eq 1 ]]; then
    if [[ "$show_messages" -eq 1 ]]; then
        printf 'Message Tokens\n'
        {
            printf 'idx\tagent\tmode\tfinish\ttotal\tinput\toutput\treasoning\tcache_read\tcache_write\tmsg_id\n'
            printf '%s\n' "$json_payload" | jq -r "$message_query_with_cache"
            printf '%s\n' "$json_payload" | jq -r "$message_total_query_with_cache"
        } | column -t -s $'\t'

        printf '\n'
    fi

    printf 'Agent Summary\n'
    {
        printf 'agent\tcount\ttotal\tinput\toutput\treasoning\tcache_read\tcache_write\n'
        printf '%s\n' "$json_payload" | jq -r "$agent_query_with_cache"
        printf '%s\n' "$json_payload" | jq -r "$agent_total_query_with_cache"
    } | column -t -s $'\t'
else
    if [[ "$show_messages" -eq 1 ]]; then
        printf 'Message Tokens\n'
        {
            printf 'idx\tagent\tmode\tfinish\ttotal\tinput\toutput\treasoning\tmsg_id\n'
            printf '%s\n' "$json_payload" | jq -r "$message_query_no_cache"
            printf '%s\n' "$json_payload" | jq -r "$message_total_query_no_cache"
        } | column -t -s $'\t'

        printf '\n'
    fi

    printf 'Agent Summary\n'
    {
        printf 'agent\tcount\ttotal\tinput\toutput\treasoning\n'
        printf '%s\n' "$json_payload" | jq -r "$agent_query_no_cache"
        printf '%s\n' "$json_payload" | jq -r "$agent_total_query_no_cache"
    } | column -t -s $'\t'
fi