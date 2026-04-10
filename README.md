**Language:** English | [简体中文](./README.zh-CN.md)

<div align="center">

# 🛡️ ArbiterOS

### ArbiterOS: One-Command Safety Harness for AI Agents

#### Autonomy under control: LLMs reason, ArbiterOS enforces.

[![platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-blue)](https://github.com/cure-lab/ArbiterOS)
[![python](https://img.shields.io/badge/python-%3E%3D3.12-3776AB)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-Apache%202.0-green)](./LICENSE)
[![status](https://img.shields.io/badge/status-active-success)](https://github.com/cure-lab/ArbiterOS)
[![kernel](https://img.shields.io/badge/module-ArbiterOS--Kernel-orange)](./ArbiterOS-Kernel)
[![ui](https://img.shields.io/badge/optional-langfuse-6e56cf)](./langfuse)
[![openclaw](https://img.shields.io/badge/integrates-OpenClaw-8a2be2)](https://github.com/cure-lab/ArbiterOS)

[![Landing Page](https://img.shields.io/badge/Landing-arbiteros.ai-0ea5e9?logo=googlechrome&logoColor=white)](https://arbiteros.ai/)
[![Live Demo](https://img.shields.io/badge/Live%20Demo-selected%20cases-22c55e?logo=vercel&logoColor=white)](https://arbiteros.ai/demo/selected-cases/index.html?demoLang=en)
[![GitHub](https://img.shields.io/badge/GitHub-cure--lab%2FArbiterOS-111827?logo=github&logoColor=white)](https://github.com/cure-lab/ArbiterOS)
[![Paper](https://img.shields.io/badge/Paper-arXiv%3A2510.13857-b91c1c?logo=arxiv&logoColor=white)](https://arxiv.org/abs/2510.13857)

</div>

🦾 ArbiterOS provides an ultra-lightweight, one-command installation for `ArbiterOS-Kernel`, without requiring `sudo`.

⚡ Delivers practical runtime safety and governance for agent systems while keeping local setup simple.

📊 Optional `langfuse` integration adds trace visualization and governance observability.

🧭 Deterministic rules for probabilistic AI, with instruction-level governance before execution.

## Why ArbiterOS

- Zero code intrusion for full-scope agents like OpenClaw and Nanobot.
- Instruction-flow parsing plus taint-aware data-flow policy enforcement.
- Policy outcomes support allow, deny, rewrite, or confirmation.
- 100% support for local private deployment.

## Benchmarks

ArbiterOS improves interception and warning coverage across multiple agent safety evaluations:

- Native OpenClaw (GPT + Claude): **6.17% -> 92.95%**
- Agent-SafetyBench (Claude Sonnet 4): **0% -> 94.25%**
- AgentDojo (GPT-4o): **0% -> 93.94%**
- WildClawBench (GPT-5.2): **55% -> 100%** (warning-oriented metric)

## News

- 2026-04-10: Refreshed README with nanobot-style layout, badges, and quick-start structure.
- 2026-04-08: Improved cross-platform bootstrap flow (`install.sh` / `install-windows.ps1`).
- 2026-04-06: Added optional `langfuse` module support for runtime trace visualization.

## What It Does

The installer at the repository root will:

- verify required commands (`curl`, `git`) and install `uv` to user space
- ensure Python 3.12+ (install via `uv` when needed)
- clone or update `ArbiterOS`
- install kernel dependencies (`uv sync --group dev`)
- create `ArbiterOS-Kernel/.env` from `.env.example`
- guide you to fill the first model entry in `ArbiterOS-Kernel/litellm_config.yaml`
- update `~/.openclaw/openclaw.json` for `arbiteros` provider and model defaults
- restart OpenClaw gateway and run `openclaw dashboard`
- create runnable scripts: `run-kernel.sh` / `run-kernel.ps1`

## Project Structure

- **`ArbiterOS-Kernel`**: core security/governance module. Use `install.sh` + `run-kernel.sh` (or Windows equivalents) to install and run it in background.
- **`langfuse`**: optional module for Langfuse-style UI, governance details, and runtime trace visualization.

## Quick Start

### Get Started in 3 Steps

1. Install and start `ArbiterOS-Kernel` (default port: `4000`).
2. Configure models, API keys, and policy rules in `ArbiterOS-Kernel/litellm_config.yaml`.
3. Point your agent model provider to `http://127.0.0.1:4000/v1`.

### Install

```bash
# Linux / macOS
git clone https://github.com/cure-lab/ArbiterOS.git
cd ArbiterOS
chmod +x install.sh
./install.sh
```

```powershell
# Windows (PowerShell)
git clone https://github.com/cure-lab/ArbiterOS.git
cd ArbiterOS
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install-windows.ps1
```

### Start Kernel

```bash
# Linux / macOS
./run-kernel.sh
```

```powershell
# Windows (PowerShell)
.\run-kernel.ps1
```

## Optional: Langfuse UI

```bash
cd ArbiterOS/langfuse
cp .env.prob.example .env
docker compose -f docker-compose.yml up -d --build
```

## Optional: User systemd Service

If you want background auto-restart and easier operations, use a user-level service:

- service name: `arbiteros-kernel`
- service file: `~/.config/systemd/user/arbiteros-kernel.service`
- working directory: `ArbiterOS/ArbiterOS-Kernel`
- start command: `uv run poe litellm`

Useful commands:

```bash
systemctl --user status arbiteros-kernel
journalctl --user -u arbiteros-kernel -f
systemctl --user restart arbiteros-kernel
```

## TODO

- [x] Support NanoBot
- [x] Evaluate on Agent SafetyBench
- [x] Evaluate on AgentDojo
- [x] Evaluate on Wild Claw Bench
- [x] Evaluate on ToolEmu
- [x] Use skill-scanner for skill safety analysis
- [x] Support Linux system
- [x] Support Windows system
- [x] Support macOS
- [x] Protect long-term memory files in agents
- [ ] Periodically analyze consistency of role positioning, intent, and behavior
- [ ] Detect prompt injection using clustered dataflow information
- [ ] Pre-check input data
- [ ] Self-evolving policy mechanism
- [ ] Support multi-modal models
