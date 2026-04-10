#!/usr/bin/env bash

set -euo pipefail

SESSION_NAME="${SESSION_NAME:-ArbiterOS-stack}"
WORKSPACE_DIR="${WORKSPACE_DIR:-$(pwd)}"

# Ensure user-local bins are on PATH (uv often installs here)
export PATH="${HOME}/.local/bin:${PATH}"

log() { echo "[INFO] $*"; }
err() { echo "[ERROR] $*" >&2; }

create_tmux_session() {
  if ! command -v tmux >/dev/null 2>&1; then
    err "tmux is required but not found."
    return 1
  fi

  if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    err "tmux session '$SESSION_NAME' already exists. Attach with 'tmux attach -t $SESSION_NAME' or kill it before re-running this script."
    return 1
  fi

  log "Creating tmux session '$SESSION_NAME' for services..."

  # So that uv/openclaw are found in tmux panes (e.g. if just installed and profile not reloaded)
  local path_prefix="export PATH=\"\${HOME}/.local/bin:\$PATH\" && "

  # Window 0: ArbiterOS kernel
  tmux new-session -d -s "$SESSION_NAME" -n "arbiteros"
  local arb_cmd
  arb_cmd="${path_prefix}cd \"$WORKSPACE_DIR/ArbiterOS-Kernel\" && uv sync --group dev && uv run poe litellm"
  tmux send-keys -t "$SESSION_NAME:0" "$arb_cmd" C-m

  # Window 1: Langfuse (production via docker compose)
  tmux new-window -t "$SESSION_NAME" -n "langfuse"
  local lf_cmd
  lf_cmd="${path_prefix}cd \"$WORKSPACE_DIR/langfuse\""
  lf_cmd="$lf_cmd && docker compose -f docker-compose.yml up -d"
  lf_cmd="$lf_cmd && docker compose -f docker-compose.yml ps"
  lf_cmd="$lf_cmd && docker compose -f docker-compose.yml logs -f --tail=50"
  tmux send-keys -t "$SESSION_NAME:1" "$lf_cmd" C-m

  # Window 2: OpenClaw gateway
  tmux new-window -t "$SESSION_NAME" -n "openclaw"
  local oc_cmd
  oc_cmd="${path_prefix}openclaw gateway status || true; openclaw gateway start || openclaw gateway restart"
  tmux send-keys -t "$SESSION_NAME:2" "$oc_cmd" C-m

  log "tmux session '$SESSION_NAME' created."
  log "Windows:"
  log "  - arbiteros : ArbiterOS kernel (uv run poe litellm)"
  log "  - langfuse  : Langfuse (docker compose up -d) + logs -f"
  log "  - openclaw  : OpenClaw gateway"
  log "Attach with: tmux attach -t $SESSION_NAME"
}

main() {
  log "Workspace directory: $WORKSPACE_DIR"
  create_tmux_session
}

main "$@"

