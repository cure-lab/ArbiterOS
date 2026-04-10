# ArbiterOS One-Command Installer

This installer lives at the `ArbiterOS` level and sets up `ArbiterOS-Kernel` automatically, without requiring `sudo`.

It will:

- verify required commands (`curl`, `git`) and install `uv` to user space
- ensure Python 3.12+ (install via `uv` when needed)
- clone or update `ArbiterOS`
- install kernel dependencies (`uv sync --group dev`)
- create `ArbiterOS-Kernel/.env` from `.env.example`
- guide you to fill the first model entry in `ArbiterOS-Kernel/litellm_config.yaml`
- update `~/.openclaw/openclaw.json` for `arbiteros` provider and model defaults
- restart OpenClaw gateway and run `openclaw dashboard`
- create a runnable script `./run-kernel.sh` (and optional user-level `systemd` service)


## TODO List

- [x] Support NanoBot
- [x] Evaluate on Agent SafetyBench
- [x] Evaluate on AgentDojo
- [x] Evaluate on Wild Claw Bench
- [x] Evaluate on ToolEmu
- [x] Use skill-scanner for skill safety analysis
- [x] Support Linux system
- [x] Support Windows system
- [x] Support MacOS
- [x] Protect the long-term memory files in the agent
- [ ] Periodically analyze the consistency of agent role positioning, intent, and behavior
- [ ] Prompt injection detection by using the clustered information in the dataflow
- [ ] Pre-checking on input data
- [ ] Policy self-evolving
- [ ] Support multi-modal models

## Run (install kernel and setup)

```bash
git clone https://github.com/cure-lab/ArbiterOS.git
cd ArbiterOS
chmod +x install.sh
./install.sh
```

Or remote:

```bash
curl -fsSL <YOUR_ARBITEROS_INSTALL_SH_RAW_URL> | bash
```

## Start Kernel

Default (recommended for quick start):

```bash
./run-kernel.sh
```

## Optional: user systemd service

If you want background auto-restart and easier ops, use the user service:

- Service name: `arbiteros-kernel`
- Service file: `~/.config/systemd/user/arbiteros-kernel.service`
- Working directory: `ArbiterOS/ArbiterOS-Kernel`
- Start command: `uv run poe litellm`

Useful commands:

```bash
systemctl --user status arbiteros-kernel
journalctl --user -u arbiteros-kernel -f
systemctl --user restart arbiteros-kernel
```
