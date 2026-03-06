#!/usr/bin/env bash
#
# Interactive configuration script for OpenClaw, Langfuse (docker compose), and ArbiterOS.
# Run after user-AutoInstall.sh. Same workspace convention: WORKSPACE_DIR (default: $(pwd)).
#

set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-$(pwd)}"
OPENCLAW_CFG="${OPENCLAW_CFG:-$HOME/.openclaw/openclaw.json}"
ARBITEROS_KERNEL="$WORKSPACE_DIR/ArbiterOS-Kernel"
LANGFUSE_DIR="$WORKSPACE_DIR/langfuse"

export PATH="${HOME}/.local/bin:${PATH}"
[ -d "${HOME}/.local/share/pnpm" ] && export PATH="${HOME}/.local/share/pnpm:${PATH}"

log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
err() { echo "[ERROR] $*" >&2; }

# Stop OpenClaw gateway if available.
stop_openclaw_gateway() {
  if command -v openclaw >/dev/null 2>&1; then
    log "Stopping OpenClaw gateway (if running)..."
    openclaw gateway stop >/dev/null 2>&1 || true
  fi
}

# Stop Langfuse docker compose stack.
stop_langfuse() {
  if ! command -v docker >/dev/null 2>&1; then
    warn "docker not found; cannot automatically stop Langfuse."
    return 0
  fi
  if [ ! -d "$LANGFUSE_DIR" ]; then
    return 0
  fi
  if [ ! -f "$LANGFUSE_DIR/docker-compose.yml" ]; then
    warn "docker-compose.yml not found in $LANGFUSE_DIR; skipping Langfuse stop."
    return 0
  fi
  log "Stopping Langfuse (docker compose down)..."
  (cd "$LANGFUSE_DIR" && docker compose -f docker-compose.yml down) >/dev/null 2>&1 || true
}

# Ask Y/N until valid answer. Returns 0 for Y, 1 for N.
ask_yn() {
  local prompt="$1"
  local reply
  while true; do
    read -r -p "$prompt (Y/N): " reply
    reply="$(echo "${reply}" | tr '[:lower:]' '[:upper:]')"
    case "$reply" in
      Y|YES) return 0 ;;
      N|NO)  return 1 ;;
      *)     echo "Please enter Y or N." ;;
    esac
  done
}

ensure_jq() {
  if command -v jq >/dev/null 2>&1; then
    return 0
  fi
  err "jq is required but not found. Install it (e.g. apt install jq, yum install jq) and re-run."
  return 1
}

config_openclaw_json() {
  log "=== Step 1: Configure OpenClaw (openclaw.json) ==="
  ensure_jq || return 1

  local existing
  if [ -f "$OPENCLAW_CFG" ]; then
    existing="$(cat "$OPENCLAW_CFG")"
  else
    existing="{}"
  fi

  local current_api_key current_workspace
  current_api_key="$(echo "$existing" | jq -r '.models.providers.arbiteros.apiKey // empty' 2>/dev/null || true)"
  current_workspace="$(echo "$existing" | jq -r '.agents.defaults.workspace // empty' 2>/dev/null || true)"

  if [ -n "${current_api_key}${current_workspace}" ]; then
    echo "Current OpenClaw configuration in $OPENCLAW_CFG:"
    [ -n "$current_api_key" ] && echo "  ArbiterOS API key: $current_api_key"
    [ -n "$current_workspace" ] && echo "  Workspace: $current_workspace"

    if ! ask_yn "Do you want to update these OpenClaw settings in $OPENCLAW_CFG?"; then
      log "Keeping existing OpenClaw settings in $OPENCLAW_CFG. Skipping OpenClaw configuration."
      return 0
    fi
  fi

  local api_key workspace
  read -r -p "Enter ArbiterOS API key (e.g. sk-zk...): " api_key
  api_key="${api_key:-${current_api_key:-sk-zk}}"
  read -r -p "Enter OpenClaw workspace path [default: ${current_workspace:-$HOME/.openclaw/workspace}]: " workspace
  workspace="${workspace:-${current_workspace:-$HOME/.openclaw/workspace}}"

  mkdir -p "$(dirname "$OPENCLAW_CFG")"

  local updated
  updated="$(echo "$existing" | jq --arg apiKey "$api_key" --arg workspace "$workspace" '
    (.models.providers //= {} | .agents //= {}) |
    .models.providers.arbiteros = {
      "baseUrl": "http://127.0.0.1:4000/v1",
      "apiKey": $apiKey,
      "api": "openai-completions",
      "authHeader": false,
      "models": [
        {
          "id": "gpt-5.2",
          "name": "GPT-5.2",
          "reasoning": false,
          "input": ["text"],
          "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
          "contextWindow": 200000,
          "maxTokens": 8192,
          "compat": { "supportsStore": false }
        }
      ]
    } |
    .agents.defaults = {
      "model": { "primary": "arbiteros/gpt-5.2" },
      "models": {
        "arbiteros/gpt-5.2": { "alias": "gpt" },
        "qwen-portal/coder-model": { "alias": "qwen" },
        "qwen-portal/vision-model": {},
        "openai/gpt-4": {},
        "openai/gpt-5": {},
        "qwen-portal/qwen-plus": {}
      },
      "workspace": $workspace,
      "compaction": { "mode": "safeguard" },
      "maxConcurrent": 4,
      "subagents": { "maxConcurrent": 8 }
    }
  ')"

  echo "$updated" > "$OPENCLAW_CFG"
  log "Updated $OPENCLAW_CFG with arbiteros provider and agents.defaults (apiKey and workspace)."
}

run_openclaw_onboard() {
  log "=== Step 2: OpenClaw onboard ==="
  if ! command -v openclaw >/dev/null 2>&1; then
    err "openclaw not found. Run user-AutoInstall.sh first."
    return 1
  fi
  log "Running: openclaw onboard (complete the wizard in the terminal)."
  openclaw onboard
  log "OpenClaw onboard finished."
}

langfuse_wait_main_ui() {
  log "=== Step 3: Langfuse (production via docker compose) – start UI and confirm main interface ==="
  if [ ! -d "$LANGFUSE_DIR" ]; then
    err "Langfuse directory not found: $LANGFUSE_DIR. Run user-AutoInstall.sh first."
    return 1
  fi
  if [ ! -f "$LANGFUSE_DIR/docker-compose.yml" ]; then
    err "docker-compose.yml not found in $LANGFUSE_DIR."
    return 1
  fi
  if ! command -v docker >/dev/null 2>&1; then
    err "docker is required for Langfuse production but not found."
    return 1
  fi

  log "Starting Langfuse stack in background (docker compose up -d)..."
  (cd "$LANGFUSE_DIR" && docker compose -f docker-compose.yml up -d)
  log "Langfuse started. Open the URL in your browser (commonly http://localhost:3000)."

  while ! ask_yn "Have you seen the Langfuse main interface?"; do
    echo "Please open the Langfuse URL in your browser and confirm when you see the main interface."
  done
  log "Proceeding to next step."
}

langfuse_new_organization_prompt() {
  log "=== Step 4: New Organization ==="
  echo "In the Langfuse UI, click the 'New Organization' button."
  while ! ask_yn "Are you now on the 'New Organization setup' page?"; do
    echo "Please click 'New Organization' and confirm when you see the setup page."
  done
}

langfuse_org_and_project_prompt() {
  log "=== Step 5: Organization and project setup ==="
  echo "Enter your organization name in the form, then click Next."
  while ! ask_yn "Have you entered the organization name and clicked Next?"; do
    echo "Enter the organization name and click Next to go to 'Invite Member'."
  done

  echo "On the 'Invite Member' step you can skip by clicking Next."
  while ! ask_yn "Have you clicked Next (past Invite Member) to reach 'Create project'?"; do
    echo "Click Next to skip Invite Member and reach the Create project step."
  done

  echo "Enter your project name, then continue (e.g. click Create / Next)."
  while ! ask_yn "Have you entered the project name and proceeded?"; do
    echo "Enter the project name and click Create/Next to continue."
  done
}

langfuse_settings_prompt() {
  log "=== Step 6: Open Settings ==="
  echo "In the project view, find and click 'Settings'."
  while ! ask_yn "Have you opened the Settings page?"; do
    echo "Click 'Settings' in the project interface and confirm when you are on the Settings page."
  done
}

langfuse_api_keys_prompt() {
  log "=== Step 7: Create API Keys ==="
  echo "In Settings, click 'API Keys', then click 'Create new API keys'."
  while ! ask_yn "Have you created a new API key?"; do
    echo "Create a new API key in Langfuse (API Keys -> Create new API keys) and confirm when done."
  done
}

collect_langfuse_keys_and_update_env() {
  log "=== Step 8: Enter Langfuse keys and update ArbiterOS .env ==="
  local public_key secret_key base_url

  if [ ! -d "$ARBITEROS_KERNEL" ]; then
    err "ArbiterOS-Kernel not found: $ARBITEROS_KERNEL"
    return 1
  fi

  local env_file="$ARBITEROS_KERNEL/.env"
  local example_file="$ARBITEROS_KERNEL/.env.example"
  if [ -f "$example_file" ] && [ ! -f "$env_file" ]; then
    cp "$example_file" "$env_file"
    log "Created $env_file from .env.example."
  fi

  local existing_public existing_secret existing_base
  if [ -f "$env_file" ]; then
    existing_public="$(grep -E '^LANGFUSE_PUBLIC_KEY=' "$env_file" | head -n1 | sed 's/^LANGFUSE_PUBLIC_KEY=//')"
    existing_secret="$(grep -E '^LANGFUSE_SECRET_KEY=' "$env_file" | head -n1 | sed 's/^LANGFUSE_SECRET_KEY=//')"
    existing_base="$(grep -E '^LANGFUSE_BASE_URL=' "$env_file" | head -n1 | sed 's/^LANGFUSE_BASE_URL=//')"
  fi

  if [ -n "${existing_public}${existing_secret}${existing_base}" ]; then
    echo "Current Langfuse configuration found in $env_file:"
    [ -n "$existing_public" ] && echo "  LANGFUSE_PUBLIC_KEY=$existing_public"
    [ -n "$existing_secret" ] && echo "  LANGFUSE_SECRET_KEY=$existing_secret"
    [ -n "$existing_base" ] && echo "  LANGFUSE_BASE_URL=$existing_base"

    if ! ask_yn "Do you want to update these Langfuse keys in $env_file?"; then
      log "Keeping existing Langfuse keys in $env_file. Skipping Langfuse key update."
      return 0
    fi
  fi

  read -r -p "Enter Langfuse PUBLIC key (starts with pk-lf-): " public_key
  read -r -p "Enter Langfuse SECRET key (starts with sk-lf-): " secret_key
  read -r -p "Enter Langfuse base URL [default: http://localhost:3000]: " base_url
  base_url="${base_url:-http://localhost:3000}"

  [ -f "$env_file" ] || touch "$env_file"
  for key in LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_BASE_URL; do
    case "$key" in
      LANGFUSE_PUBLIC_KEY) val="$public_key" ;;
      LANGFUSE_SECRET_KEY) val="$secret_key" ;;
      LANGFUSE_BASE_URL)   val="$base_url" ;;
    esac
    sed -i "/^${key}=/d" "$env_file" 2>/dev/null || true
    printf '%s="%s"\n' "$key" "$val" >> "$env_file"
  done
  log "Updated $env_file with LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL."
}

main() {
  trap 'stop_langfuse; stop_openclaw_gateway' EXIT
  log "Interactive configuration for OpenClaw, Langfuse (docker), and ArbiterOS."
  log "Workspace: $WORKSPACE_DIR"

  config_openclaw_json
  run_openclaw_onboard
  langfuse_wait_main_ui
  langfuse_new_organization_prompt
  langfuse_org_and_project_prompt
  langfuse_settings_prompt
  langfuse_api_keys_prompt
  collect_langfuse_keys_and_update_env

  stop_langfuse
  stop_openclaw_gateway

  log "Configuration complete. You can start services (e.g. via tmux or manually): ArbiterOS kernel, Langfuse, OpenClaw gateway."
}

main "$@"

