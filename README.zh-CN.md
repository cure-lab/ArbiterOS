**语言:** [English](./README.md) | 简体中文

<div align="center">

# 🛡️ ArbiterOS

### ArbiterOS：面向 AI Agent 的一键式安全护栏

#### 在可控中实现自治：LLM 负责推理，ArbiterOS 负责执行约束。

[![platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-blue)](https://github.com/cure-lab/ArbiterOS)
[![python](https://img.shields.io/badge/python-%3E%3D3.12-3776AB)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-Apache%202.0-green)](./LICENSE)
[![status](https://img.shields.io/badge/status-active-success)](https://github.com/cure-lab/ArbiterOS)

[![Landing Page](https://img.shields.io/badge/Landing-arbiteros.ai-0ea5e9?logo=googlechrome&logoColor=white)](https://arbiteros.ai/)
[![Live Demo](https://img.shields.io/badge/Live%20Demo-selected%20cases-22c55e?logo=vercel&logoColor=white)](https://arbiteros.ai/demo/selected-cases/index.html?demoLang=en)
[![GitHub](https://img.shields.io/badge/GitHub-cure--lab%2FArbiterOS-111827?logo=github&logoColor=white)](https://github.com/cure-lab/ArbiterOS)
[![Paper](https://img.shields.io/badge/Paper-arXiv%3A2510.13857-b91c1c?logo=arxiv&logoColor=white)](https://arxiv.org/abs/2510.13857)

</div>

🦾 ArbiterOS 提供超轻量、单命令安装 `ArbiterOS-Kernel` 的能力。

⚡ 在保持本地部署与启动简洁的同时，为 Agent 系统提供可落地的运行时安全与治理能力。

📊 可选集成 `langfuse`，用于可视化追踪与治理可观测性。

🧭 以确定性规则约束概率式 AI，在指令执行前进行治理。

## ArbiterOS 在 Agent 系统中的位置

![ArbiterOS 在 Agent 系统中的定位](./assets/readme/position_of_arbiteros_zh.png)

## 为什么选择 ArbiterOS

- 对 OpenClaw、Nanobot 这类全能力 Agent 实现零代码侵入。
- 基于指令流解析 + 污点感知数据流的策略执行机制。
- 100% 支持本地私有化部署。
- 深度支持对治理过程的全程监管, 分析, 以及治理策略的更新。

## 基准结果

ArbiterOS 在多项 Agent 安全评测中显著提升拦截与告警覆盖率：

- Native OpenClaw (GPT + Claude): **6.17% -> 92.95%**
- Agent-SafetyBench (Claude Sonnet 4): **0% -> 94.25%**
- AgentDojo (GPT-4o): **0% -> 93.94%**
- WildClawBench (GPT-5.2): **55% -> 100%**（告警导向指标）

## 功能说明

仓库根目录安装脚本会执行以下步骤：

- 检查必要命令（`curl`, `git`），并将 `uv` 安装到用户空间
- 确保 Python 3.12+（必要时通过 `uv` 安装）
- 克隆或更新 `ArbiterOS`
- 安装内核依赖（`uv sync --group dev`）
- 由 `.env.example` 生成 `ArbiterOS-Kernel/.env`
- 引导你填写 `ArbiterOS-Kernel/litellm_config.yaml` 中第一个模型配置项
- 更新 `~/.openclaw/openclaw.json`，设置 `arbiteros` provider 与默认模型
- 重启 OpenClaw gateway 并运行 `openclaw dashboard`
- 生成可直接运行脚本：`run-kernel.sh` / `run-kernel.ps1`

## 项目结构

- **`ArbiterOS-Kernel`**：核心安全/治理模块。通过 `install.sh` + `run-kernel.sh`（或 Windows 对应脚本）安装并后台运行。
- **`langfuse`**：可选模块，提供 Langfuse 风格 UI、治理详情和运行时追踪可视化。

## 快速开始

### 3 步完成接入

1. 安装并启动 `ArbiterOS-Kernel`（默认端口：`4000`）。
2. 在 `ArbiterOS-Kernel/litellm_config.yaml` 中配置模型、API Key 与策略规则。
3. 将 Agent 的模型 provider 指向 `http://127.0.0.1:4000/v1`。

### 安装

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

### 启动 Kernel

```bash
# Linux / macOS
./run-kernel.sh
```

```powershell
# Windows (PowerShell)
.\run-kernel.ps1
```

## 可选：Langfuse UI

```bash
cd ArbiterOS/langfuse
cp .env.prob.example .env
docker compose -f docker-compose.yml up -d --build
```

## 可选：用户级 systemd 服务

如果你希望后台自动重启并简化运维操作，可使用用户级服务：

- service name: `arbiteros-kernel`
- service file: `~/.config/systemd/user/arbiteros-kernel.service`
- working directory: `ArbiterOS/ArbiterOS-Kernel`
- start command: `uv run poe litellm`

常用命令：

```bash
systemctl --user status arbiteros-kernel
journalctl --user -u arbiteros-kernel -f
systemctl --user restart arbiteros-kernel
```

## TODO

- [x] 支持 NanoBot
- [x] 在 Agent SafetyBench 上评估
- [x] 在 AgentDojo 上评估
- [x] 在 Wild Claw Bench 上评估
- [x] 在 ToolEmu 上评估
- [x] 使用 skill-scanner 做 skill 安全分析
- [x] 支持 Linux
- [x] 支持 Windows
- [x] 支持 macOS
- [x] 保护 Agent 的长期记忆文件
- [ ] 定期分析角色定位、意图与行为一致性
- [ ] 利用聚类数据流信息检测 prompt injection
- [ ] 输入数据预检
- [ ] 自进化策略机制
- [ ] 支持多模态模型
