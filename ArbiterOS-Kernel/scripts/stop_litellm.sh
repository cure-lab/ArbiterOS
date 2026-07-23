#!/usr/bin/env bash
set -euo pipefail

echo "==> Killing litellm processes..."
pkill -9 -f "litellm --config" 2>/dev/null || true
pkill -9 -f "uv run poe litellm" 2>/dev/null || true
pkill -9 -f "hypercorn" 2>/dev/null || true
sleep 1

REMAINING=$(ps aux | grep -E "(litellm|hypercorn)" | grep -v grep | wc -l)
if [ "$REMAINING" -eq 0 ]; then
    echo "==> All litellm processes stopped."
else
    echo "==> WARNING: $REMAINING processes still running:"
    ps aux | grep -E "(litellm|hypercorn)" | grep -v grep
fi
