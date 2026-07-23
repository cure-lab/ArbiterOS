#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_FILE="$PROJECT_DIR/log/litellm.log"
PORT=5000

mkdir -p "$PROJECT_DIR/log"

# Check if port is already in use by something other than our own litellm
PORT_PID=$(lsof -ti :"$PORT" 2>/dev/null || true)
if [ -n "$PORT_PID" ]; then
    echo "==> Port $PORT is in use by PID(s): $PORT_PID"
    IS_OURS=false
    for pid in $PORT_PID; do
        if ps -p "$pid" -o cmd= 2>/dev/null | grep -qE "litellm|hypercorn|uv run poe"; then
            IS_OURS=true
            break
        fi
    done
    if [ "$IS_OURS" = true ]; then
        echo "    Port used by existing litellm instance, killing first..."
        bash "$SCRIPT_DIR/stop_litellm.sh"
    else
        echo "    ERROR: Port $PORT is in use by an unrelated process. Stop it first or change PORT."
        exit 1
    fi
fi

# Kill any existing litellm/hypercorn processes (belt-and-suspenders)
echo "==> Killing existing litellm processes..."
pkill -9 -f "litellm --config" 2>/dev/null || true
pkill -9 -f "uv run poe litellm" 2>/dev/null || true
pkill -9 -f "hypercorn" 2>/dev/null || true
sleep 1

# Verify clean
REMAINING=$(ps aux | grep -E "(litellm|hypercorn)" | grep -v grep | wc -l)
if [ "$REMAINING" -gt 0 ]; then
    echo "WARNING: $REMAINING litellm/hypercorn processes still running"
fi

# Start litellm
echo "==> Starting litellm..."
cd "$PROJECT_DIR"
nohup uv run poe litellm > "$LOG_FILE" 2>&1 &
PID=$!
echo "    PID: $PID"
echo "    Log: $LOG_FILE"

# Wait for ready
echo -n "==> Waiting for server..."
for i in $(seq 1 30); do
    CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/health 2>/dev/null || true)
    if [ "$CODE" = "200" ]; then
        echo " ready (${i}s)"
        echo "==> litellm is running on http://127.0.0.1:5000"
        exit 0
    fi
    echo -n "."
    sleep 1
done
echo " TIMEOUT"
echo "Check $LOG_FILE for errors."
exit 1
