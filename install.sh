#!/usr/bin/env bash

set -euo pipefail

ARBITEROS_REPO_URL="${ARBITEROS_REPO_URL:-https://github.com/cure-lab/ArbiterOS.git}"
ARBITEROS_BRANCH="${ARBITEROS_BRANCH:-main}"
INSTALL_ROOT="${INSTALL_ROOT:-$HOME}"
INSTALL_DIR="${INSTALL_DIR:-$INSTALL_ROOT/ArbiterOS}"
KERNEL_SUBDIR="${KERNEL_SUBDIR:-ArbiterOS-Kernel}"
KERNEL_DIR="${KERNEL_DIR:-$INSTALL_DIR/$KERNEL_SUBDIR}"
SERVICE_NAME="${SERVICE_NAME:-arbiteros-kernel}"
ENABLE_USER_SERVICE="${ENABLE_USER_SERVICE:-1}"
OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$HOME/.openclaw/openclaw.json}"

export PATH="$HOME/.local/bin:$PATH"

log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
err() { echo "[ERROR] $*" >&2; }

ensure_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1 && return 0
  case "$cmd" in
    curl|git)
      err "Missing required command: $cmd"
      err "Please install '$cmd' first, then rerun install.sh."
      return 1
      ;;
    uv)
      log "Installing uv in user space (~/.local/bin)..."
      curl -LsSf https://astral.sh/uv/install.sh | sh
      ;;
    *) err "Cannot auto-install $cmd"; return 1 ;;
  esac
}

ensure_python312() {
  if command -v python3 >/dev/null 2>&1 && python3 - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3,12) else 1)
PY
  then
    return 0
  fi
  uv python install 3.12
}

clone_or_use_repo() {
  # If running inside ArbiterOS root, reuse it.
  if [ -d "$PWD/$KERNEL_SUBDIR" ] && [ -f "$PWD/README.md" ]; then
    INSTALL_DIR="$PWD"
    KERNEL_DIR="$INSTALL_DIR/$KERNEL_SUBDIR"
    log "Using current directory: $INSTALL_DIR"
    return
  fi

  if [ -d "$INSTALL_DIR/.git" ]; then
    log "Updating existing ArbiterOS repo at $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch origin "$ARBITEROS_BRANCH"
    git -C "$INSTALL_DIR" checkout "$ARBITEROS_BRANCH"
    git -C "$INSTALL_DIR" pull --ff-only origin "$ARBITEROS_BRANCH"
  else
    log "Cloning ArbiterOS into $INSTALL_DIR"
    git clone -b "$ARBITEROS_BRANCH" "$ARBITEROS_REPO_URL" "$INSTALL_DIR"
  fi

  if [ ! -d "$KERNEL_DIR" ]; then
    err "Kernel directory not found: $KERNEL_DIR"
    err "Please set KERNEL_SUBDIR/KERNEL_DIR if repository layout differs."
    exit 1
  fi
}

setup_kernel() {
  cd "$KERNEL_DIR"
  uv sync --group dev
  if [ -f ".env.example" ] && [ ! -f ".env" ]; then
    cp .env.example .env
    log "Created $KERNEL_DIR/.env from .env.example"
  elif [ ! -f ".env" ]; then
    touch .env
  fi
}

prompt_with_default() {
  local p="$1" d="${2:-}" v
  if [ -n "$d" ]; then read -r -p "$p [$d]: " v; echo "${v:-$d}";
  else read -r -p "$p: " v; echo "$v"; fi
}

configure_litellm_yaml() {
  local cfg="$KERNEL_DIR/litellm_config.yaml"
  [ -f "$cfg" ] || { err "Missing $cfg"; exit 1; }
  local cur_name cur_model cur_key cur_base
  cur_name="$(awk '/^  - model_name:/ {print $3; exit}' "$cfg" || true)"
  cur_model="$(awk '/^      model:/ {print $2; exit}' "$cfg" || true)"
  cur_key="$(awk '/^      api_key:/ {print $2; exit}' "$cfg" || true)"
  cur_base="$(awk '/^      api_base:/ {print $2; exit}' "$cfg" || true)"
  local cur_skills_root cur_scanner_model cur_scanner_base cur_scanner_key
  cur_skills_root="$(awk '
    /^arbiteros_skill_trust:/ {f=1; next}
    /^skill_scanner_llm:/ {f=0}
    f && /^  skills_root:/ {print $2; exit}
  ' "$cfg" || true)"
  cur_scanner_model="$(awk '
    /^skill_scanner_llm:/ {f=1; next}
    /^litellm_settings:/ {f=0}
    f && /^  model:/ {print $2; exit}
  ' "$cfg" || true)"
  cur_scanner_base="$(awk '
    /^skill_scanner_llm:/ {f=1; next}
    /^litellm_settings:/ {f=0}
    f && /^  api_base:/ {print $2; exit}
  ' "$cfg" || true)"
  cur_scanner_key="$(awk '
    /^skill_scanner_llm:/ {f=1; next}
    /^litellm_settings:/ {f=0}
    f && /^  api_key:/ {print $2; exit}
  ' "$cfg" || true)"

  echo
  log "Configure first model entry in $cfg"
  local model_name model api_key api_base
  model_name="$(prompt_with_default "model_name" "${cur_name:-gpt-4o-mini}")"
  model="$(prompt_with_default "litellm_params.model" "${cur_model:-openai/gpt-4o-mini}")"
  api_key="$(prompt_with_default "litellm_params.api_key" "${cur_key:-}")"
  api_base="$(prompt_with_default "litellm_params.api_base" "${cur_base:-https://api.openai.com/v1}")"

  local tmp; tmp="$(mktemp)"
  awk -v n="$model_name" -v m="$model" -v k="$api_key" -v b="$api_base" '
    BEGIN {f=0;p=0}
    /^  - model_name:/ && !f {f=1; print "  - model_name: " n; next}
    f && /^  - model_name:/ {f=0; p=0; print; next}
    f && /^    litellm_params:/ {p=1; print; next}
    f && p && /^      model:/ {print "      model: " m; next}
    f && p && /^      api_key:/ {print "      api_key: " k; next}
    f && p && /^      api_base:/ {print "      api_base: " b; next}
    {print}
  ' "$cfg" > "$tmp"
  mv "$tmp" "$cfg"

  echo
  log "Configure skill trust / skill scanner in $cfg"
  local new_skills_root new_scanner_model new_scanner_base new_scanner_key
  new_skills_root="$(prompt_with_default "arbiteros_skill_trust.skills_root" "${cur_skills_root:-}")"
  new_scanner_model="$(prompt_with_default "skill_scanner_llm.model" "${cur_scanner_model:-openai/gpt-4.1-mini}")"
  new_scanner_base="$(prompt_with_default "skill_scanner_llm.api_base" "${cur_scanner_base:-https://api.openai.com/v1}")"
  new_scanner_key="$(prompt_with_default "skill_scanner_llm.api_key" "${cur_scanner_key:-}")"

  local tmp2; tmp2="$(mktemp)"
  awk -v skills_root="$new_skills_root" \
      -v smodel="$new_scanner_model" \
      -v sbase="$new_scanner_base" \
      -v skey="$new_scanner_key" '
    BEGIN { in_trust=0; in_scanner=0; }
    /^arbiteros_skill_trust:/ { in_trust=1; in_scanner=0; print; next }
    /^skill_scanner_llm:/ { in_scanner=1; in_trust=0; print; next }
    /^litellm_settings:/ { in_trust=0; in_scanner=0; print; next }
    in_trust && /^  skills_root:/ { print "  skills_root: " skills_root; next }
    in_scanner && /^  model:/ { print "  model: " smodel; next }
    in_scanner && /^  api_base:/ { print "  api_base: " sbase; next }
    in_scanner && /^  api_key:/ { print "  api_key: " skey; next }
    { print }
  ' "$cfg" > "$tmp2"
  mv "$tmp2" "$cfg"
}

configure_openclaw_json() {
  local cfg="$OPENCLAW_CONFIG_PATH"
  local litellm_cfg="$KERNEL_DIR/litellm_config.yaml"
  local model_name
  model_name="$(awk '/^  - model_name:/ {print $3; exit}' "$litellm_cfg" || true)"
  if [ -z "$model_name" ]; then
    err "Cannot read model_name from $litellm_cfg"
    return 1
  fi

  mkdir -p "$(dirname "$cfg")"
  if [ ! -f "$cfg" ]; then
    echo "{}" > "$cfg"
  fi

  python3 - "$cfg" "$model_name" <<'PY'
import json
import sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
model_name = sys.argv[2]
model_key = f"arbiteros/{model_name}"

try:
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
except Exception:
    data = {}

data.setdefault("models", {})
data["models"].setdefault("providers", {})
data["models"]["providers"]["arbiteros"] = {
    "baseUrl": "http://127.0.0.1:4000/v1",
    "apiKey": "n/a",
    "api": "openai-completions",
    "authHeader": False,
    "models": [
        {
            "id": model_name,
            "name": model_name,
            "reasoning": False,
            "input": ["text"],
            "cost": {
                "input": 0,
                "output": 0,
                "cacheRead": 0,
                "cacheWrite": 0,
            },
            "contextWindow": 200000,
            "maxTokens": 8192,
            "compat": {"supportsStore": False},
        }
    ],
}

data.setdefault("agents", {})
data["agents"].setdefault("defaults", {})
data["agents"]["defaults"].setdefault("model", {})
data["agents"]["defaults"]["model"]["primary"] = model_key
data["agents"]["defaults"].setdefault("models", {})
data["agents"]["defaults"]["models"].setdefault(model_key, {})

data.setdefault("auth", {})
data["auth"].setdefault("profiles", {})
data["auth"]["profiles"].setdefault("openai:default", {})
data["auth"]["profiles"]["openai:default"]["provider"] = "arbiteros"
data["auth"]["profiles"]["openai:default"].setdefault("mode", "api_key")

cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

  log "Updated OpenClaw config: $cfg"
  log "Set provider=arbiteros, primary=arbiteros/$model_name"
}

restart_openclaw_gateway_and_open_dashboard() {
  if ! command -v openclaw >/dev/null 2>&1; then
    warn "openclaw command not found. Skipping gateway restart/dashboard step."
    return 0
  fi

  log "Restarting OpenClaw gateway..."
  if ! openclaw gateway restart; then
    warn "openclaw gateway restart failed; trying openclaw gateway start..."
    openclaw gateway start || warn "Failed to start OpenClaw gateway."
  fi

  log "Opening OpenClaw dashboard..."
  openclaw dashboard || warn "Failed to open OpenClaw dashboard."
}

write_user_run_script() {
  local run_script="$INSTALL_DIR/run-kernel.sh"
  cat > "$run_script" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$KERNEL_DIR"
export PATH="\$HOME/.local/bin:\$PATH"
exec uv run poe litellm
EOF
  chmod +x "$run_script"
  log "Created run script: $run_script"
}

create_user_service() {
  local svc_dir="$HOME/.config/systemd/user"
  local svc="$svc_dir/${SERVICE_NAME}.service"
  mkdir -p "$svc_dir"
  cat > "$svc" <<EOF
[Unit]
Description=ArbiterOS Kernel (LiteLLM Proxy)
After=default.target

[Service]
Type=simple
WorkingDirectory=$KERNEL_DIR
Environment=PATH=/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin
ExecStart=uv run poe litellm
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
  log "Created user systemd service: $svc"

  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found. Skipping service enable/start."
    return 1
  fi

  if ! systemctl --user daemon-reload >/dev/null 2>&1; then
    warn "systemd user session unavailable in this shell."
    warn "You can still run the kernel with: $INSTALL_DIR/run-kernel.sh"
    return 1
  fi

  systemctl --user enable "$SERVICE_NAME" >/dev/null
  systemctl --user restart "$SERVICE_NAME"
  return 0
}

main() {
  ensure_cmd curl
  ensure_cmd git
  ensure_cmd uv
  ensure_python312
  clone_or_use_repo
  setup_kernel
  configure_litellm_yaml
  configure_openclaw_json
  write_user_run_script
  if [ "$ENABLE_USER_SERVICE" = "1" ]; then
    create_user_service || true
  fi
  log "Done. Kernel path: $KERNEL_DIR"
  log "Start manually: $INSTALL_DIR/run-kernel.sh"
  if command -v systemctl >/dev/null 2>&1; then
    log "User service status: systemctl --user status $SERVICE_NAME"
    log "User service logs: journalctl --user -u $SERVICE_NAME -f"
  fi
}

main "$@"
