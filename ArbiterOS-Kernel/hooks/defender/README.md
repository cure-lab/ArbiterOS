# ArbiterOS Defender Hook

PreToolUse gate integrated with the ArbiterOS Kernel **said/done** matcher.

- Gateway-known tool actions (declared `TOOLCALL` on the same trace) **with matching details** → **auto-allow**
- Unknown / mismatched id **or same id with different command/args** → escalate for human confirm

Confirmation UX (preferred):

1. ArbiterOS TUI shows the pending item on that `trace_id`
2. `attach <trace_id>` then answer **Y** (deny / keep blocked) or **N** (allow)
3. Same semantics as policy confirm; if both pending on one trace, **policy first**, then said/done

Fallback: sidecar `watch.py` (type `1` to allow). Kept for now.

No LLM calls. Matching is by `tool_use_id` / `tool_call_id`, then canonical args.

Works for **Codex** and **Claude Code** via the same `hook.py` (stdin JSON + `permissionDecision` deny).

## Layout

| Path | Role |
|------|------|
| `arbiteros_kernel/session_index.py` | Scheme-B: session / pck / tool_call_id → `trace_id` |
| `arbiteros_kernel/execution_check.py` | Pending Said TOOLCALLs + PreToolUse check |
| `arbiteros_kernel/tui_bridge.py` | TUI pending queue (`kind=said_done`) + Y/N → hook decision |
| `hooks/defender/hook.py` | Codex / Claude Code PreToolUse entry |
| `hooks/defender/watch.py` | Optional human escalate terminal (fallback) |

Kernel index files (under `ArbiterOS-Kernel/log/said_done/`):

- `session_index.json`
- `pending_actions.json`

Hook event queue (default): `~/.arbiteros/defender-hook/`

## Setup

1. Kernel proxy running (`poe litellm` or `poe arbiteros`).
2. Install hooks (Codex + Claude Code by default):

```bash
python3 ArbiterOS-Kernel/hooks/defender/install_hooks.py
# or: --agent codex | --agent claude | --agent all
```

3. Run ArbiterOS TUI (for attach Y/N on escalations).

4. Agent through ArbiterOS Gateway:

- Codex: `/hooks` → trust the defender hook if prompted
- Claude Code: restart / new session after install (`~/.claude/settings.json`)

Optional fallback terminal:

```bash
python3 ArbiterOS-Kernel/hooks/defender/watch.py
```

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

When `false`, Gateway skips pending registration and the hook allows every PreToolUse (no TUI / watch escalate). Agent hook configs can stay installed.
