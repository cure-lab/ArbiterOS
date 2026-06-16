#!/bin/bash
# ============================================================
# OpenClaw Router + OpenClaw Gateway Startup Script
# ============================================================

set -e

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # Points to LLMRouter root
CONFIG_FILE="${SCRIPT_DIR}/openclaw_router/config.yaml"
ROUTER_LOG="/tmp/openclaw.log"
GATEWAY_LOG="/tmp/openclaw-gateway.log"
ROUTER_PORT=8000
ROUTER_NAME=""
ROUTER_CONFIG=""
NO_GATEWAY=false
NO_PREFIX=false

# Print colored messages
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Show help
show_help() {
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  -c, --config FILE       Config file path (default: openclaw_router/config.yaml)"
    echo "  -p, --port PORT         Router port (default: 8000)"
    echo "  -r, --router NAME       Use specified router (e.g., knnrouter, mlprouter, randomrouter)"
    echo "  --router-config FILE    Router config file path"
    echo "  --no-gateway            Don't start OpenClaw Gateway"
    echo "  --no-prefix             Don't add model name prefix to responses"
    echo "  --list-routers          List all available routers"
    echo "  -h, --help              Show this help message"
    echo ""
    echo "Available Routers (26+):"
    echo "  Built-in strategies: random, round_robin, rules, llm"
    echo "  ML Routers: knnrouter, mlprouter, svmrouter, mfrouter, elorouter, dcrouter"
    echo "  Advanced Routers: graphrouter, gmtrouter, causallmrouter, personalizedrouter"
    echo "  Multi-round Routers: knnmultiroundrouter, llmmultiroundrouter"
    echo "  Hybrid Routers: hybridllm, automixrouter, routerdc, router_r1"
    echo "  Simple Selection: randomrouter, thresholdrouter, largest_llm, smallest_llm"
    echo ""
    echo "Examples:"
    echo "  $0                                    # Start with default config"
    echo "  $0 -r llm                             # Use LLM-based routing"
    echo "  $0 -r random                          # Use random routing"
    echo "  $0 -r knnrouter                       # Use KNN Router"
    echo "  $0 -r randomrouter -p 9000            # Use Random Router on port 9000"
    echo "  $0 --no-gateway                       # Start Router only, no Gateway"
    echo "  $0 -c my_config.yaml -r mlprouter     # Custom config + ML Router"
    exit 0
}

# Validate that an option has a value and the value is not another flag.
require_value() {
    local opt="$1"
    local val="${2-}"
    if [[ -z "$val" || "$val" == -* ]]; then
        error "Option $opt requires a value"
        exit 1
    fi
}

# Parse command line arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            -c|--config)
                require_value "$1" "${2-}"
                CONFIG_FILE="$2"
                shift 2
                ;;
            -p|--port)
                require_value "$1" "${2-}"
                ROUTER_PORT="$2"
                shift 2
                ;;
            -r|--router)
                require_value "$1" "${2-}"
                ROUTER_NAME="$2"
                shift 2
                ;;
            --router-config)
                require_value "$1" "${2-}"
                ROUTER_CONFIG="$2"
                shift 2
                ;;
            --no-gateway)
                NO_GATEWAY=true
                shift
                ;;
            --no-prefix)
                NO_PREFIX=true
                shift
                ;;
            -h|--help)
                show_help
                ;;
            --list-routers)
                list_routers
                ;;
            *)
                error "Unknown option: $1"
                echo "Use -h for help"
                exit 1
                ;;
        esac
    done
}

# List all available routers
list_routers() {
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN}  Available LLM Routers${NC}"
    echo -e "${GREEN}============================================================${NC}"
    echo ""
    cd "$SCRIPT_DIR"
    python -c "
from llmrouter.cli.router_inference import ROUTER_REGISTRY
print('Built-in strategies:')
print('  random, round_robin, rules, llm')
print()
print('LLMRouter ML-based Routers:')
for name in sorted(set(ROUTER_REGISTRY.keys())):
    print(f'  {name}')
" 2>/dev/null || echo "  (LLMRouter environment required)"
    echo ""
    exit 0
}

# Cleanup function
cleanup() {
    echo ""
    info "Stopping services..."

    # Stop Router
    if [ -n "$ROUTER_PID" ] && kill -0 "$ROUTER_PID" 2>/dev/null; then
        kill "$ROUTER_PID" 2>/dev/null || true
        success "OpenClaw Router stopped"
    fi

    # Stop Gateway
    if [ -n "$GATEWAY_PID" ] && kill -0 "$GATEWAY_PID" 2>/dev/null; then
        kill "$GATEWAY_PID" 2>/dev/null || true
        success "OpenClaw Gateway stopped"
    fi

    exit 0
}

# Trap exit signals
trap cleanup SIGINT SIGTERM

# Check if port is in use
check_port() {
    local port=$1

    if command -v lsof >/dev/null 2>&1; then
        if lsof -i :"$port" >/dev/null 2>&1; then
            return 0  # Port in use
        fi
        return 1
    fi

    if command -v ss >/dev/null 2>&1; then
        if ss -ltn "sport = :$port" 2>/dev/null | tail -n +2 | grep -q .; then
            return 0
        fi
        return 1
    fi

    if command -v netstat >/dev/null 2>&1; then
        if netstat -ltn 2>/dev/null | grep -E "[\\.:]${port}[[:space:]]" >/dev/null 2>&1; then
            return 0
        fi
        return 1
    fi

    warn "No port check tool found (lsof/ss/netstat); skipping port check"
    return 1
}

# Wait for service to start
wait_for_service() {
    local url=$1
    local name=$2
    local max_wait=30
    local count=0

    while [ $count -lt $max_wait ]; do
        if curl -s "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
        count=$((count + 1))
    done
    return 1
}

# Show banner
show_banner() {
    echo -e "${GREEN}"
    echo "============================================================"
    echo "  OpenClaw Router + OpenClaw Gateway"
    echo "============================================================"
    echo -e "${NC}"
}

# Main function
main() {
    # Parse arguments
    parse_args "$@"

    show_banner

    # Check config file
    if [ ! -f "$CONFIG_FILE" ]; then
        error "Config file not found: $CONFIG_FILE"
        exit 1
    fi

    # Check Router port
    if check_port "$ROUTER_PORT"; then
        warn "Port $ROUTER_PORT is in use, stopping old process..."
        pkill -f "python -m openclaw_router" 2>/dev/null || true
        sleep 2
    fi

    # Build startup command
    ROUTER_CMD=(python -m openclaw_router --config "$CONFIG_FILE" --port "$ROUTER_PORT")

    if [ -n "$ROUTER_NAME" ]; then
        ROUTER_CMD+=(--router "$ROUTER_NAME")
        info "Using Router: $ROUTER_NAME"
    fi

    if [ -n "$ROUTER_CONFIG" ]; then
        ROUTER_CMD+=(--router-config "$ROUTER_CONFIG")
    fi

    if [ "$NO_PREFIX" = true ]; then
        ROUTER_CMD+=(--no-prefix)
    fi

    # Start OpenClaw Router
    info "Starting OpenClaw Router..."
    cd "$SCRIPT_DIR"

    # Run using nohup
    nohup "${ROUTER_CMD[@]}" > "$ROUTER_LOG" 2>&1 &
    ROUTER_PID=$!

    # Wait for Router to start
    if wait_for_service "http://localhost:$ROUTER_PORT/health" "OpenClaw Router"; then
        success "OpenClaw Router started (PID: $ROUTER_PID)"
        echo "       API: http://localhost:$ROUTER_PORT/v1/chat/completions"
        echo "       Log: $ROUTER_LOG"
    else
        error "OpenClaw Router failed to start, check log: $ROUTER_LOG"
        cat "$ROUTER_LOG"
        exit 1
    fi

    echo ""

    # Start OpenClaw Gateway (if needed)
    if [ "$NO_GATEWAY" = false ]; then
        # Check if openclaw is installed
        if ! command -v openclaw &> /dev/null; then
            warn "OpenClaw CLI not found!"
            echo ""
            echo "       To install OpenClaw:"
            echo "         npm install -g openclaw"
            echo ""
            echo "       Or run without gateway:"
            echo "         $0 --no-gateway"
            echo ""
            warn "Continuing without OpenClaw Gateway..."
        else
            info "Starting OpenClaw Gateway..."

            # Stop any existing Gateway
            pkill -9 -f "openclaw gateway run --bind loopback --port 18789" 2>/dev/null || true
            pkill -9 -f "openclaw gateway run" 2>/dev/null || true
            sleep 1

            nohup openclaw gateway run --bind loopback --port 18789 --force > "$GATEWAY_LOG" 2>&1 &
            GATEWAY_PID=$!

            # Wait for Gateway to start
            sleep 3
            if kill -0 "$GATEWAY_PID" 2>/dev/null; then
                success "OpenClaw Gateway started (PID: $GATEWAY_PID)"
                echo "       WebSocket: ws://127.0.0.1:18789"
                echo "       Log: $GATEWAY_LOG"
            else
                warn "OpenClaw Gateway failed to start"
                echo "       Check log: $GATEWAY_LOG"
                echo ""
                echo "       Make sure you have configured:"
                echo "         Edit ~/.openclaw/openclaw.json and set:"
                echo "           - channels.slack.botToken (xoxb-...)"
                echo "           - channels.slack.appToken (xapp-...)"
                echo "           - models.providers.openclaw.baseUrl (http://127.0.0.1:${ROUTER_PORT}/v1)"
                echo "           - models.providers.openclaw.api (openai-completions)"
                echo ""
                echo "       See: openclaw_router/README.md"
            fi
        fi
    else
        info "Skipping OpenClaw Gateway (--no-gateway)"
    fi

    echo ""
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN}  Services Started!${NC}"
    echo -e "${GREEN}============================================================${NC}"
    echo ""
    echo "  OpenClaw Router: http://localhost:$ROUTER_PORT"
    if [ "$NO_GATEWAY" = false ]; then
        echo "  OpenClaw Gateway: ws://127.0.0.1:18789"
    fi
    if [ -n "$ROUTER_NAME" ]; then
        echo "  Router: $ROUTER_NAME"
    fi
    echo ""
    echo "  Press Ctrl+C to stop all services"
    echo ""

    # Show live logs
    info "Showing Router logs (Ctrl+C to exit)..."
    echo "------------------------------------------------------------"
    tail -f "$ROUTER_LOG"
}

# Run
main "$@"
