"""Said/Done matcher: PreToolUse actions vs Gateway-declared TOOLCALLs.

No LLM. Bind by tool_call_id (primary) or canonical args (secondary), then
verify execution details. Id alone is not enough — detail mismatch escalates.
Unknown / mismatch → escalate to human confirm (fail-closed).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from arbiteros_kernel.policy_runtime import canonicalize_args
from arbiteros_kernel.session_index import register_tool_call_id, resolve_trace_id

_LOCK = threading.Lock()
_PENDING_PATH: Optional[Path] = None
_CONFIG_CACHE_MTIME_NS: Optional[int] = None
_CONFIG_CACHE_ENABLED: Optional[bool] = None

# Hook tool names (Codex/Claude) → Gateway / instruction tool names.
_TOOL_NAME_ALIASES: dict[str, str] = {
    "bash": "exec_command",
    "shell": "exec_command",
    "shell_command": "exec_command",
    "local_shell": "exec_command",
    "execute": "exec_command",
    "run_terminal_cmd": "exec_command",
    "exec_command": "exec_command",
    "apply_patch": "apply_patch",
    "edit": "apply_patch",
    "write": "apply_patch",
    "strreplace": "apply_patch",
}

# Presentational / scheduling fields — not part of "what executes".
_IGNORE_ARG_KEYS = frozenset(
    {
        "justification",
        "yield_time_ms",
        "max_output_tokens",
        "description",
        "timeout_ms",
        "timeout",
    }
)

_SHELL_EXEC_KEYS = frozenset(
    {
        "cmd",
        "command",
        "shell_command",
        "prefix_rule",
        "sandbox_permissions",
        "workdir",
        "working_directory",
        "cwd",
    }
)


def _kernel_root() -> Path:
    return Path(__file__).resolve().parent.parent


def is_said_done_hook_enabled() -> bool:
    """Whether Said/Done PreToolUse gating is on (litellm_config.yaml / env)."""
    env = os.environ.get("ARBITEROS_SAID_DONE_HOOK_ENABLED", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True

    global _CONFIG_CACHE_MTIME_NS, _CONFIG_CACHE_ENABLED
    cfg_path = _kernel_root() / "litellm_config.yaml"
    try:
        mtime_ns = cfg_path.stat().st_mtime_ns
    except OSError:
        return True

    if _CONFIG_CACHE_MTIME_NS == mtime_ns and _CONFIG_CACHE_ENABLED is not None:
        return _CONFIG_CACHE_ENABLED

    enabled = True
    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict) and isinstance(parsed.get("said_done_hook_enabled"), bool):
            enabled = bool(parsed["said_done_hook_enabled"])
    except Exception:
        enabled = True

    _CONFIG_CACHE_MTIME_NS = mtime_ns
    _CONFIG_CACHE_ENABLED = enabled
    return enabled


def pending_file_path() -> Path:
    global _PENDING_PATH
    if _PENDING_PATH is None:
        override = os.environ.get("ARBITEROS_SAID_DONE_PENDING_FILE", "").strip()
        if override:
            _PENDING_PATH = Path(override).expanduser().resolve()
        else:
            _PENDING_PATH = _kernel_root() / "log" / "said_done" / "pending_actions.json"
    _PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _PENDING_PATH


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_payload() -> dict[str, Any]:
    return {"version": 1, "updated_at": _now_iso(), "actions": {}}


def _read_payload() -> dict[str, Any]:
    path = pending_file_path()
    if not path.exists():
        return _empty_payload()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_payload()
    if not isinstance(raw, dict):
        return _empty_payload()
    if not isinstance(raw.get("actions"), dict):
        raw["actions"] = {}
    return raw


def _write_payload(payload: dict[str, Any]) -> None:
    path = pending_file_path()
    payload["version"] = 1
    payload["updated_at"] = _now_iso()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def normalize_tool_name(name: Optional[str]) -> str:
    if not isinstance(name, str) or not name.strip():
        return ""
    key = name.strip().lower()
    # MCP-style names: mcp__fs__read → keep as-is lowercased for exact match
    if key.startswith("mcp__"):
        return key
    return _TOOL_NAME_ALIASES.get(key, key)


def _strip_ignored_args(arguments: Any) -> Any:
    if not isinstance(arguments, dict):
        return arguments
    return {k: v for k, v in arguments.items() if str(k).lower() not in _IGNORE_ARG_KEYS}


def _action_fingerprint(tool_name: str, arguments: Any) -> str:
    canon_name = normalize_tool_name(tool_name)
    stripped = _strip_ignored_args(arguments)
    if isinstance(stripped, dict):
        # Unify shell command keys so cmd vs command does not false-mismatch.
        if canon_name == "exec_command":
            cmd = _comparable_command(stripped)
            exec_args: dict[str, Any] = {}
            if cmd is not None:
                exec_args["command"] = cmd
            for k, v in stripped.items():
                kl = str(k).lower()
                if kl in {"cmd", "command", "shell_command"}:
                    continue
                if kl in _SHELL_EXEC_KEYS or kl not in _IGNORE_ARG_KEYS:
                    exec_args[k] = v
            canon_args = canonicalize_args(exec_args)
        else:
            canon_args = canonicalize_args(stripped)
    elif isinstance(stripped, str):
        canon_args = {"_raw": stripped.strip()}
    else:
        try:
            canon_args = canonicalize_args(stripped) if stripped is not None else {}
        except Exception:
            canon_args = {"_raw": str(stripped)}
    try:
        blob = json.dumps(
            {"tool": canon_name, "args": canon_args},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    except Exception:
        blob = f"{canon_name}:{arguments!r}"
    return blob


def _comparable_command(arguments: Any) -> Optional[str]:
    if not isinstance(arguments, dict):
        return None
    for key in ("cmd", "command", "shell_command"):
        val = arguments.get(key)
        if isinstance(val, str) and val.strip():
            return " ".join(val.strip().split())
    return None


def _short(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _details_match(
    said_row: dict[str, Any],
    *,
    tool_name: str,
    tool_input: Any,
) -> tuple[bool, str, str, str]:
    """
    Return (ok, diff, said_summary, done_summary).

    Compares execution-relevant details; ignores presentational metadata.
    """
    said_name = normalize_tool_name(str(said_row.get("tool_name") or ""))
    done_name = normalize_tool_name(tool_name)
    said_args = said_row.get("arguments")
    if not isinstance(said_args, dict):
        said_args = {} if said_args is None else {"_raw": said_args}
    done_args = tool_input if isinstance(tool_input, dict) else (
        {} if tool_input is None else {"_raw": tool_input}
    )

    said_summary = _short(
        {"tool": said_name or said_row.get("tool_name"), "args": _strip_ignored_args(said_args)}
    )
    done_summary = _short({"tool": done_name or tool_name, "args": _strip_ignored_args(done_args)})

    if said_name and done_name and said_name != done_name:
        return (
            False,
            f"tool_name: said={said_name} done={done_name}",
            said_summary,
            done_summary,
        )

    # Shell: command string is the primary semantic.
    if (said_name or done_name) == "exec_command":
        said_cmd = _comparable_command(said_args) or said_row.get("command")
        done_cmd = _comparable_command(done_args)
        if isinstance(said_cmd, str):
            said_cmd = " ".join(said_cmd.strip().split())
        if said_cmd != done_cmd:
            return (
                False,
                f"command: said={_short(said_cmd)!r} done={_short(done_cmd)!r}",
                said_summary,
                done_summary,
            )
        # Other execution-affecting shell fields: only compare when both sides set them.
        # Codex Bash PreToolUse often only forwards `command`, while Gateway Said has
        # richer exec_command args — missing-on-done must not false-escalate.
        for key in ("prefix_rule", "sandbox_permissions", "workdir", "working_directory", "cwd"):
            s_val = said_args.get(key)
            d_val = done_args.get(key)
            if s_val is None or d_val is None:
                continue
            if canonicalize_args(s_val) != canonicalize_args(d_val):
                return (
                    False,
                    f"{key}: said={_short(s_val)!r} done={_short(d_val)!r}",
                    said_summary,
                    done_summary,
                )
        return True, "", said_summary, done_summary

    # apply_patch: compare patch body / path-ish fields via fingerprint.
    said_fp = _action_fingerprint(said_name or "apply_patch", said_args)
    done_fp = _action_fingerprint(done_name or tool_name, done_args)
    if said_fp != done_fp:
        return (
            False,
            "arguments fingerprint differs (patch/args changed)",
            said_summary,
            done_summary,
        )
    return True, "", said_summary, done_summary


def register_pending_toolcalls(
    *,
    trace_id: Optional[str],
    toolcalls: list[dict[str, Any]],
) -> int:
    """Record Gateway-declared TOOLCALLs as pending Said actions. Returns count registered."""
    if not is_said_done_hook_enabled():
        return 0
    if not isinstance(trace_id, str) or not trace_id.strip():
        return 0
    tid = trace_id.strip()
    registered = 0
    with _LOCK:
        payload = _read_payload()
        actions = payload.setdefault("actions", {})
        if not isinstance(actions, dict):
            actions = {}
            payload["actions"] = actions

        for tc in toolcalls:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("tool_call_id") or tc.get("id")
            tool_name = tc.get("tool_name") or tc.get("name")
            arguments = tc.get("arguments") or tc.get("args") or {}
            if not isinstance(tc_id, str) or not tc_id.strip():
                continue
            if not isinstance(tool_name, str) or not tool_name.strip():
                continue
            tc_id = tc_id.strip()
            existing = actions.get(tc_id)
            if isinstance(existing, dict) and existing.get("consumed"):
                # Do not revive consumed ids.
                continue
            actions[tc_id] = {
                "trace_id": tid,
                "tool_call_id": tc_id,
                "tool_name": tool_name.strip(),
                "tool_name_norm": normalize_tool_name(tool_name),
                "arguments": arguments if isinstance(arguments, (dict, list, str)) else str(arguments),
                "fingerprint": _action_fingerprint(tool_name, arguments),
                "command": _comparable_command(arguments),
                "created_at": _now_iso(),
                "consumed": False,
                "consumed_at": None,
            }
            registered += 1
            try:
                register_tool_call_id(tool_call_id=tc_id, trace_id=tid)
            except Exception:
                pass

        # Cap: drop oldest consumed, then oldest unconsumed if still huge.
        if len(actions) > 4000:
            items = list(actions.items())
            consumed = [(k, v) for k, v in items if isinstance(v, dict) and v.get("consumed")]
            for k, _ in consumed[: max(0, len(actions) - 3500)]:
                actions.pop(k, None)

        if registered:
            _write_payload(payload)
    return registered


@dataclass
class ExecutionCheckResult:
    decision: str  # "allow" | "escalate"
    reason: str
    trace_id: Optional[str] = None
    matched_tool_call_id: Optional[str] = None
    said_summary: Optional[str] = None
    done_summary: Optional[str] = None
    diff: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


def _consume(actions: dict[str, Any], tool_call_id: str) -> None:
    row = actions.get(tool_call_id)
    if not isinstance(row, dict):
        return
    row["consumed"] = True
    row["consumed_at"] = _now_iso()


def _match_by_id(
    actions: dict[str, Any],
    *,
    tool_use_id: str,
) -> Optional[dict[str, Any]]:
    row = actions.get(tool_use_id)
    if not isinstance(row, dict) or row.get("consumed"):
        return None
    return row


def _match_by_fingerprint(
    actions: dict[str, Any],
    *,
    trace_id: str,
    tool_name: str,
    tool_input: Any,
) -> Optional[dict[str, Any]]:
    done_fp = _action_fingerprint(tool_name, tool_input)
    done_cmd = _comparable_command(tool_input if isinstance(tool_input, dict) else {})
    done_name = normalize_tool_name(tool_name)
    candidates: list[dict[str, Any]] = []
    for row in actions.values():
        if not isinstance(row, dict) or row.get("consumed"):
            continue
        if str(row.get("trace_id") or "") != trace_id:
            continue
        if normalize_tool_name(str(row.get("tool_name") or "")) != done_name:
            continue
        if row.get("fingerprint") == done_fp:
            candidates.append(row)
            continue
        # Command-equality fallback for shell tools (cmd vs command key drift).
        said_cmd = row.get("command")
        if (
            done_cmd
            and isinstance(said_cmd, str)
            and said_cmd == done_cmd
            and done_name == "exec_command"
        ):
            # Still require full detail match (prefix_rule etc.).
            ok, _, _, _ = _details_match(row, tool_name=tool_name, tool_input=tool_input)
            if ok:
                candidates.append(row)
    if len(candidates) == 1:
        return candidates[0]
    return None


def check_pretool_use(payload: dict[str, Any]) -> ExecutionCheckResult:
    """Match a PreToolUse hook payload against pending Said TOOLCALLs."""
    if not isinstance(payload, dict):
        return ExecutionCheckResult("escalate", "invalid_payload")

    if not is_said_done_hook_enabled():
        return ExecutionCheckResult(decision="allow", reason="said_done_disabled")

    tool_name = str(payload.get("tool_name") or "")
    tool_use_id = payload.get("tool_use_id") or payload.get("tool_call_id")
    tool_input = payload.get("tool_input")
    if tool_input is None:
        tool_input = payload.get("arguments")
    session_id = payload.get("session_id")
    if isinstance(session_id, str):
        session_id = session_id.strip() or None
    else:
        session_id = None

    if isinstance(tool_use_id, str):
        tool_use_id = tool_use_id.strip() or None
    else:
        tool_use_id = None

    # Prefer tool_call_id resolution; also try session_id / prompt_cache_key aliases.
    trace_id = resolve_trace_id(
        session_id=session_id,
        prompt_cache_key=session_id,  # Codex: often identical
        tool_call_id=tool_use_id,
    )

    with _LOCK:
        payload_disk = _read_payload()
        actions = payload_disk.get("actions")
        if not isinstance(actions, dict):
            actions = {}

        if tool_use_id:
            row = _match_by_id(actions, tool_use_id=tool_use_id)
            if row is not None:
                ok, diff, said_s, done_s = _details_match(
                    row, tool_name=tool_name, tool_input=tool_input
                )
                tid = str(row.get("trace_id") or trace_id or "") or None
                if ok:
                    _consume(actions, tool_use_id)
                    _write_payload(payload_disk)
                    return ExecutionCheckResult(
                        decision="allow",
                        reason="matched_tool_call_id",
                        trace_id=tid,
                        matched_tool_call_id=tool_use_id,
                        said_summary=said_s,
                        done_summary=done_s,
                    )
                # Same id, different details → user must confirm. Do not consume.
                return ExecutionCheckResult(
                    decision="escalate",
                    reason="said_done_detail_mismatch",
                    trace_id=tid,
                    matched_tool_call_id=tool_use_id,
                    said_summary=said_s,
                    done_summary=done_s,
                    diff=diff,
                )

        if not trace_id:
            return ExecutionCheckResult(
                decision="escalate",
                reason="trace_unresolved",
                trace_id=None,
                done_summary=_short(
                    {"tool": normalize_tool_name(tool_name) or tool_name, "args": tool_input}
                ),
            )

        row = _match_by_fingerprint(
            actions,
            trace_id=trace_id,
            tool_name=tool_name,
            tool_input=tool_input,
        )
        if row is not None:
            matched_id = str(row.get("tool_call_id") or "")
            ok, diff, said_s, done_s = _details_match(
                row, tool_name=tool_name, tool_input=tool_input
            )
            if not ok:
                return ExecutionCheckResult(
                    decision="escalate",
                    reason="said_done_detail_mismatch",
                    trace_id=trace_id,
                    matched_tool_call_id=matched_id or None,
                    said_summary=said_s,
                    done_summary=done_s,
                    diff=diff,
                )
            if matched_id:
                _consume(actions, matched_id)
                _write_payload(payload_disk)
            return ExecutionCheckResult(
                decision="allow",
                reason="matched_canonical_args",
                trace_id=trace_id,
                matched_tool_call_id=matched_id or None,
                said_summary=said_s,
                done_summary=done_s,
            )

        return ExecutionCheckResult(
            decision="escalate",
            reason="said_done_mismatch",
            trace_id=trace_id,
            done_summary=_short(
                {"tool": normalize_tool_name(tool_name) or tool_name, "args": tool_input}
            ),
        )


def mark_consumed(tool_call_id: Optional[str]) -> bool:
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        return False
    tcid = tool_call_id.strip()
    with _LOCK:
        payload = _read_payload()
        actions = payload.get("actions")
        if not isinstance(actions, dict) or tcid not in actions:
            return False
        _consume(actions, tcid)
        _write_payload(payload)
        return True
