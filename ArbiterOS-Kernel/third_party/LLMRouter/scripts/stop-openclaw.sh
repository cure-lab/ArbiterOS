#!/bin/bash
# ============================================================
# OpenClaw Router + OpenClaw Gateway Stop Script
# ============================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  Stopping OpenClaw Router + OpenClaw Gateway${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""

# Stop OpenClaw Router
echo -n "Stopping OpenClaw Router... "
pkill -f "python -m openclaw_router" 2>/dev/null && echo -e "${GREEN}OK${NC}" || echo -e "${RED}Not running${NC}"

# Stop OpenClaw Gateway
echo -n "Stopping OpenClaw Gateway... "
pkill -f "openclaw gateway run --bind loopback --port 18789" 2>/dev/null && echo -e "${GREEN}OK${NC}" || echo -e "${RED}Not running${NC}"
pkill -f "openclaw gateway run" 2>/dev/null || true

echo ""
echo "All services stopped"
