# ArbiterOS Defender Hook

PreToolUse gate integrated with the ArbiterOS Kernel **said/done** matcher.

- Gateway-known tool actions (declared `TOOLCALL` on the same trace) **with matching details** → **auto-allow**
- Unknown / mismatched id **or same id with different command/args** → escalate to sidecar `watch.py` (type `1` to allow)

No LLM calls. Matching is by `tool_use_id` / `tool_call_id`, then canonical args.

## Layout

| Path | Role |
|------|------|
| `arbiteros_kernel/session_index.py` | Scheme-B: session / pck / tool_call_id → `trace_id` |
| `arbiteros_kernel/execution_check.py` | Pending Said TOOLCALLs + PreToolUse check |
| `hooks/defender/hook.py` | Codex PreToolUse entry |
| `hooks/defender/watch.py` | Human escalate terminal |

Kernel index files (under `ArbiterOS-Kernel/log/said_done/`):

- `session_index.json`
- `pending_actions.json`

Hook event queue (default): `~/.arbiteros/defender-hook/`

## Setup

1. Kernel proxy running (`poe litellm` or `poe arbiteros`).
2. Install Codex hooks:

```bash
python3 ArbiterOS-Kernel/hooks/defender/install_hooks.py
```

3. Terminal A — watch (only needed for escalations):

```bash
python3 ArbiterOS-Kernel/hooks/defender/watch.py
```

4. Terminal B — Codex (traffic through ArbiterOS Gateway).

In Codex: `/hooks` → trust the ArbiterOS defender hook if prompted.

## Claude Code

Kernel already indexes Claude `session_id` via `session_index`. Point Claude Code’s PreToolUse hook at the same `hook.py` (or a thin wrapper) when ready; the matcher API is agent-agnostic.

## Env

| Variable | Meaning |
|----------|---------|
| `ARBITEROS_KERNEL_ROOT` | Kernel root (default: parent of `hooks/`) |
| `ARBITEROS_DEFENDER_HOOK_DIR` | Hook pending/events dir |
| `ARBITEROS_SESSION_INDEX_FILE` | Override session index path |
| `ARBITEROS_SAID_DONE_PENDING_FILE` | Override pending actions path |
| `ARBITEROS_SAID_DONE_HOOK_ENABLED` | Override `said_done_hook_enabled` in `litellm_config.yaml` (`0`/`1`) |
| `ARBITEROS_DEFENDER_PYTHON` | Python used by `install_hooks.py` |

## Config switch

In `ArbiterOS-Kernel/litellm_config.yaml`:

```yaml
said_done_hook_enabled: true   # false => no pending Said; PreToolUse auto-allows
```

When `false`, Gateway skips pending registration and the hook allows every PreToolUse (no watch escalate). Codex `hooks.json` can stay installed.
