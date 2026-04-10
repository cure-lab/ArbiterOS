#!/usr/bin/env bash

set -euo pipefail

SESSION_NAME="${SESSION_NAME:-ArbiterOS-stack}"
WORKSPACE_DIR="${WORKSPACE_DIR:-$(pwd)}"

# Ensure user-local bins are on PATH (uv, pnpm often install here)
export PATH="${HOME}/.local/bin:${PATH}"
[ -d "${HOME}/.local/share/pnpm" ] && export PATH="${HOME}/.local/share/pnpm:${PATH}"

log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
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

  # So that uv/pnpm are found in tmux panes (e.g. if just installed and profile not reloaded)
  local path_prefix="export PATH=\"\${HOME}/.local/bin:\${HOME}/.local/share/pnpm:\$PATH\" && "

  # Window 0: ArbiterOS kernel
  tmux new-session -d -s "$SESSION_NAME" -n "arbiteros"
  local arb_cmd
  arb_cmd="${path_prefix}cd \"$WORKSPACE_DIR/ArbiterOS-Kernel\" && uv sync --group dev && uv run poe litellm"
  tmux send-keys -t "$SESSION_NAME:0" "$arb_cmd" C-m

  # Window 1: Langfuse (infra + web)
  tmux new-window -t "$SESSION_NAME" -n "langfuse"
  local lf_cmd
  lf_cmd="${path_prefix}cd \"$WORKSPACE_DIR/langfuse\""
  lf_cmd="$lf_cmd && pnpm run infra:dev:up"
  lf_cmd="$lf_cmd && pnpm i"
  lf_cmd="$lf_cmd && pnpm --filter=shared run db:deploy"
  lf_cmd="$lf_cmd && pnpm --filter=shared run ch:dev-tables"
  lf_cmd="$lf_cmd && pnpm -w run build --filter @langfuse/shared"
  lf_cmd="$lf_cmd && pnpm run dev:web"
  tmux send-keys -t "$SESSION_NAME:1" "$lf_cmd" C-m

  # Window 2: OpenClaw gateway
  tmux new-window -t "$SESSION_NAME" -n "openclaw"
  local oc_cmd
  oc_cmd="${path_prefix}openclaw gateway status || true; openclaw gateway start || openclaw gateway restart"
  tmux send-keys -t "$SESSION_NAME:2" "$oc_cmd" C-m

  log "tmux session '$SESSION_NAME' created."
  log "Windows:"
  log "  - arbiteros : ArbiterOS kernel (uv run poe litellm)"
  log "  - langfuse  : Langfuse infra and dev web"
  log "  - openclaw  : OpenClaw gateway"
  log "Attach with: tmux attach -t $SESSION_NAME"
}

main() {
  log "Workspace directory: $WORKSPACE_DIR"
  create_tmux_session
}

main "$@"

