#!/usr/bin/env bash

set -euo pipefail

WORKSPACE_DIR="${WORKSPACE_DIR:-$(pwd)}"

# Ensure user-local bins are on PATH (uv often installs here)
export PATH="${HOME}/.local/bin:${PATH}"

log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
err() { echo "[ERROR] $*" >&2; }

has_cmd() { command -v "$1" >/dev/null 2>&1; }

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

install_curl() { install_system curl; }
install_git() { install_system git; }
install_tmux() { install_system tmux; }

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

ensure_cmd() {
  local cmd="$1"
  local install_fn="$2"
  if has_cmd "$cmd"; then
    return 0
  fi
  log "'$cmd' not found. Attempting to install..."
  if "$install_fn"; then
    export PATH="${HOME}/.local/bin:${PATH}"
    if has_cmd "$cmd"; then
      return 0
    fi
  fi
  err "Required command '$cmd' is still not available after install attempt."
  return 1
}

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

check_prereqs() {
  log "Checking required commands and installing missing ones..."
  ensure_cmd curl install_curl
  ensure_cmd git install_git
  ensure_cmd uv install_uv
  ensure_python312
  ensure_cmd tmux install_tmux

  if ! has_cmd docker; then
    install_docker || warn "Docker could not be installed automatically. Install it manually and re-run if Langfuse fails."
  fi

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
    warn "OpenClaw config '$cfg' not found. You will generate/update it in user-ConfigInteractive.sh."
  else
    log "Found OpenClaw config at '$cfg'."
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
    log "Creating ArbiterOS .env from .env.example..."
    cp .env.example .env
  elif [ -f ".env" ]; then
    log "ArbiterOS .env already exists."
  else
    warn "ArbiterOS .env/.env.example not found. You must create and fill .env manually."
  fi
}

install_langfuse() {
  cd "$WORKSPACE_DIR"

  if [ ! -d "langfuse" ]; then
    log "Cloning Langfuse (branch user)..."
    git clone -b user https://github.com/ChangranXU/langfuse.git
  else
    log "Langfuse directory already exists."
  fi

  cd "$WORKSPACE_DIR/langfuse"

  if [ -f ".env.prod.example" ] && [ ! -f ".env" ]; then
    log "Creating Langfuse .env from .env.prod.example..."
    cp .env.prod.example .env
  elif [ -f ".env" ]; then
    log "Langfuse .env already exists."
  else
    warn "Langfuse .env/.env.prod.example not found. You must create and fill .env manually."
  fi
}

main() {
  trap 'err "Script failed. See messages above."; exit 1' ERR
  log "Workspace directory: $WORKSPACE_DIR"
  check_prereqs
  install_openclaw
  install_arbiteros
  install_langfuse
  log "Install complete. Next: run user-ConfigInteractive.sh, then user-AutoRun.sh."
}

main "$@"

