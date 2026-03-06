#!/usr/bin/env bash

set -euo pipefail

SESSION_NAME="${SESSION_NAME:-openclaw-stack}"
WORKSPACE_DIR="${WORKSPACE_DIR:-$(pwd)}"

# Ensure user-local bins are on PATH (uv, pnpm often install here)
export PATH="${HOME}/.local/bin:${PATH}"
[ -d "${HOME}/.local/share/pnpm" ] && export PATH="${HOME}/.local/share/pnpm:${PATH}"

log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
err() { echo "[ERROR] $*" >&2; }

has_cmd() { command -v "$1" >/dev/null 2>&1; }

# Detect package manager (apt, yum, dnf) for installing system packages
detect_pkg_manager() {
  if command -v apt-get >/dev/null 2>&1 && [ -x /usr/bin/apt-get ]; then
    echo "apt"
  elif command -v dnf >/dev/null 2>&1; then
    echo "dnf"
  elif command -v yum >/dev/null 2>&1; then
    echo "yum"
  else
    echo ""
  fi
}

# Install system package(s). Uses sudo. Returns 0 on success.
install_system() {
  local pkg_manager
  pkg_manager="$(detect_pkg_manager)"
  if [ -z "$pkg_manager" ]; then
    err "Could not detect package manager (apt/yum/dnf). Install missing tools manually."
    return 1
  fi
  log "Installing system package(s): $* (using $pkg_manager)..."
  case "$pkg_manager" in
    apt) sudo apt-get update -qq && sudo apt-get install -y "$@" ;;
    dnf) sudo dnf install -y "$@" ;;
    yum) sudo yum install -y "$@" ;;
    *) return 1 ;;
  esac
}

install_uv() {
  log "Installing uv..."
  local install_dir="${HOME}/.local/bin"
  mkdir -p "$install_dir"
  if curl -LsSf https://astral.sh/uv/install.sh | sh; then
    export PATH="${HOME}/.local/bin:${PATH}"
    if has_cmd uv; then
      log "uv installed successfully ($(uv --version 2>/dev/null || true))."
      return 0
    fi
  fi
  err "uv installation failed."
  return 1
}

install_pnpm() {
  log "Installing pnpm..."
  if ! has_cmd node; then
    err "Node.js is required to install pnpm. Install node first (e.g. install_node)."
    return 1
  fi
  # Prefer corepack (bundled with Node 16.13+), non-interactive
  if has_cmd corepack; then
    log "Enabling pnpm via corepack..."
    corepack enable
    corepack prepare pnpm@latest --activate 2>/dev/null || true
    if has_cmd pnpm; then
      log "pnpm installed successfully via corepack ($(pnpm --version 2>/dev/null || true))."
      return 0
    fi
  fi
  # Fallback: official install script (may modify shell profile)
  log "Trying pnpm install script..."
  if curl -fsSL https://get.pnpm.io/install.sh | sh -; then
    export PNPM_HOME="${HOME}/.local/share/pnpm"
    export PATH="${PNPM_HOME}:${PATH}"
    [ -x "${HOME}/.local/share/pnpm/pnpm" ] && export PATH="${HOME}/.local/share/pnpm:${PATH}"
  fi
  if has_cmd pnpm; then
    log "pnpm installed successfully ($(pnpm --version 2>/dev/null || true))."
    return 0
  fi
  # Fallback: npm install -g pnpm
  if has_cmd npm; then
    log "Trying 'npm install -g pnpm'..."
    npm install -g pnpm
    if has_cmd pnpm; then return 0; fi
  fi
  err "pnpm installation failed."
  return 1
}

install_node() {
  log "Installing Node.js..."
  local pkg_manager
  pkg_manager="$(detect_pkg_manager)"
  if [ "$pkg_manager" = "apt" ]; then
    # NodeSource for Node 20 (many distros); for 24 we'd need a different setup
    if ! has_cmd node; then
      curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
      install_system nodejs
    fi
  elif [ "$pkg_manager" = "dnf" ] || [ "$pkg_manager" = "yum" ]; then
    if ! has_cmd node; then
      install_system nodejs
    fi
  fi
  if has_cmd node; then
    log "Node.js installed ($(node -v 2>/dev/null))."
    return 0
  fi
  err "Node.js installation failed. Install node manually (e.g. from https://nodejs.org or your package manager)."
  return 1
}

install_docker() {
  log "Installing Docker..."
  local pkg_manager
  pkg_manager="$(detect_pkg_manager)"
  if [ "$pkg_manager" = "apt" ]; then
    install_system docker.io || install_system docker-ce docker-ce-cli containerd.io
  elif [ "$pkg_manager" = "dnf" ] || [ "$pkg_manager" = "yum" ]; then
    install_system docker
  fi
  if has_cmd docker; then
    log "Docker installed. You may need to run: sudo usermod -aG docker \$USER and re-login if 'docker ps' fails."
    return 0
  fi
  err "Docker installation failed. Install Docker manually and ensure your user is in the 'docker' group."
  return 1
}

# Ensure a command is available; if not, run the given install function and re-check.
ensure_cmd() {
  local cmd="$1"
  local install_fn="$2"
  if has_cmd "$cmd"; then
    return 0
  fi
  log "'$cmd' not found. Attempting to install..."
  if "$install_fn"; then
    export PATH="${HOME}/.local/bin:${PATH}"
    [ -d "${HOME}/.local/share/pnpm" ] && export PATH="${HOME}/.local/share/pnpm:${PATH}"
    if has_cmd "$cmd"; then
      return 0
    fi
  fi
  err "Required command '$cmd' is still not available after install attempt."
  return 1
}

# Return 0 if Python 3.12+ is available (system python3 or uv-managed).
has_python312() {
  if command -v python3 >/dev/null 2>&1; then
    py_ver="$(python3 -V 2>&1 | awk '{print $2}')"
    py_major="$(echo "$py_ver" | cut -d. -f1)"
    py_minor="$(echo "$py_ver" | cut -d. -f2)"
    [ "$py_major" -gt 3 ] || { [ "$py_major" -eq 3 ] && [ "${py_minor:-0}" -ge 12 ]; } && return 0
  fi
  if has_cmd uv && uv python list 2>/dev/null | grep -qE '3\.12'; then
    return 0
  fi
  return 1
}

# Install Python 3.12 via uv when system Python is too old. ArbiterOS uses uv, so uv will use this for the project.
ensure_python312() {
  if has_python312; then
    return 0
  fi
  if ! has_cmd uv; then
    err "Python 3.12+ is required. System Python is too old and uv is not available to install it."
    return 1
  fi
  log "System Python is older than 3.12. Installing Python 3.12 via uv..."
  if uv python install 3.12; then
    if has_python312; then
      log "Python 3.12 is now available (uv will use it for ArbiterOS)."
      return 0
    fi
  fi
  err "Failed to install Python 3.12 via uv."
  return 1
}

check_versions() {
  # Python 3.12+ for ArbiterOS (system or uv-managed)
  if has_python312; then
    if command -v python3 >/dev/null 2>&1; then
      py_ver="$(python3 -V 2>&1 | awk '{print $2}')"
      py_major="$(echo "$py_ver" | cut -d. -f1)"
      py_minor="$(echo "$py_ver" | cut -d. -f2)"
      if [ "$py_major" -gt 3 ] || { [ "$py_major" -eq 3 ] && [ "${py_minor:-0}" -ge 12 ]; }; then
        log "Using system Python $py_ver for ArbiterOS."
      else
        log "Using uv-managed Python 3.12 for ArbiterOS (system is $py_ver)."
      fi
    else
      log "Using uv-managed Python 3.12 for ArbiterOS."
    fi
  else
    err "Python 3.12+ is required for ArbiterOS. Run ensure_python312 or install Python 3.12+ manually."
    return 1
  fi

  # Node 24 for Langfuse (recommended)
  if command -v node >/dev/null 2>&1; then
    node_ver="$(node -v 2>/dev/null | sed 's/^v//')"
    node_major="$(echo "$node_ver" | cut -d. -f1)"
    if [ "${node_major:-0}" -lt 24 ]; then
      warn "Node 24 is recommended for Langfuse (found v$node_ver). Proceeding, but you may see build/runtime issues."
    fi
  else
    err "node (Node.js) is required for Langfuse but not found."
    return 1
  fi
}

install_curl() { install_system curl; }
install_git() { install_system git; }
install_tmux() { install_system tmux; }

check_prereqs() {
  log "Checking required commands and installing missing ones..."

  # Order matters: curl and git first (needed for installers)
  ensure_cmd curl install_curl
  ensure_cmd git install_git
  ensure_cmd uv install_uv
  ensure_python312
  ensure_cmd tmux install_tmux

  # Node and pnpm for Langfuse
  if ! has_cmd node; then
    install_node
  fi
  ensure_cmd pnpm install_pnpm

  # Docker for Langfuse infra
  if ! has_cmd docker; then
    install_docker || warn "Docker could not be installed automatically. Install it manually and re-run if Langfuse fails."
  fi

  check_versions

  # Check docker permissions
  if has_cmd docker && ! docker ps >/dev/null 2>&1; then
    warn "Current user cannot access Docker daemon. Run: sudo usermod -aG docker \$USER then log out and back in."
  fi
}

install_openclaw() {
  if command -v openclaw >/dev/null 2>&1; then
    log "OpenClaw already installed."
  else
    log "Installing OpenClaw (this may take several minutes)..."
    if ! curl -fsSL https://openclaw.ai/install.sh | bash; then
      err "OpenClaw installation failed."
      return 1
    fi
  fi

  local cfg="$HOME/.openclaw/openclaw.json"
  if [ ! -f "$cfg" ]; then
    warn "OpenClaw config '$cfg' not found. Please create and edit it as described in the README before running services."
  else
    log "Found OpenClaw config at '$cfg'. Ensure workspace path and models are correctly configured."
  fi
}

install_arbiteros() {
  cd "$WORKSPACE_DIR"

  if [ ! -d "ArbiterOS-Kernel" ]; then
    log "Cloning ArbiterOS-Kernel (branch trace-vis)..."
    git clone -b trace-vis https://github.com/DavidChen-PKU/ArbiterOS-Kernel.git
  else
    log "ArbiterOS-Kernel directory already exists."
  fi

  cd "$WORKSPACE_DIR/ArbiterOS-Kernel"

  log "Installing ArbiterOS Python dependencies with uv..."
  uv sync --group dev

  if [ -f ".env.example" ] && [ ! -f ".env" ]; then
    log "Creating ArbiterOS .env from .env.example (please edit with your real keys)..."
    cp .env.example .env
  elif [ -f ".env" ]; then
    log "ArbiterOS .env already exists."
  else
    warn "ArbiterOS .env/.env.example not found. You must create and fill .env manually."
  fi

  if [ ! -f "litellm_config.yaml" ]; then
    warn "ArbiterOS litellm_config.yaml not found. Please create it and configure model_list as described in the README."
  else
    log "Found litellm_config.yaml. Ensure model_list is configured correctly."
  fi
}

install_langfuse() {
  cd "$WORKSPACE_DIR"

  if [ ! -d "langfuse" ]; then
    log "Cloning Langfuse (branch dev)..."
    git clone -b user https://github.com/ChangranXU/langfuse.git
  else
    log "Langfuse directory already exists."
  fi

  cd "$WORKSPACE_DIR/langfuse"

  if [ -f ".env.dev.example" ] && [ ! -f ".env" ]; then
    log "Creating Langfuse .env from .env.dev.example (please edit with your real keys)..."
    cp .env.dev.example .env
  elif [ -f ".env" ]; then
    log "Langfuse .env already exists."
  else
    warn "Langfuse .env/.env.dev.example not found. You must create and fill .env manually."
  fi

  # Optional: warn about clickhouse-client if missing
  if ! command -v clickhouse-client >/dev/null 2>&1; then
    warn "clickhouse-client not found. 'pnpm --filter=shared run ch:dev-tables' may fail. Install clickhouse-client as described in the README."
  fi
}

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
  trap 'err "Script failed. See messages above."; exit 1' ERR

  log "Workspace directory: $WORKSPACE_DIR"
  check_prereqs
  install_openclaw
  install_arbiteros
  install_langfuse
  create_tmux_session

  log "Setup complete (subject to any warnings above)."
  log "Ensure configuration files (.env, litellm_config.yaml, openclaw.json) contain correct values."
}

main "$@"

