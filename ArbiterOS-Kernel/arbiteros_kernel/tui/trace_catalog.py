from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from arbiteros_kernel.tui.config_catalog import kernel_root
from arbiteros_kernel.session_traces import load_running_trace_ids
from arbiteros_kernel.tui_bridge import list_pending_confirms


@dataclass(frozen=True)
class TraceRow:
    trace_id: str
    status: str
    agent: str
    created_at: str
    context: str
    tokens: str
    pending_block: str
    instruction_count: int
    is_test: bool


def _instruction_dir() -> Path:
    return kernel_root() / "log" / "instruction"


def _trace_state_path() -> Path:
    return kernel_root() / "log" / "trace_state.json"


def _load_active_trace_meta() -> dict[str, dict[str, Any]]:
    """trace_id -> state payload for current session pointers."""
    path = _trace_state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    states = raw.get("states") if isinstance(raw, dict) else None
    if not isinstance(states, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for value in states.values():
        if not isinstance(value, dict):
            continue
        tid = value.get("trace_id")
        if isinstance(tid, str) and tid.strip():
            out[tid.strip()] = value
    return out


def _format_created_date(created_at: str) -> str:
    if not created_at or created_at == "-":
        return "-"
    # Keep YYYY-MM-DD only.
    return created_at[:10]


def _status_for_trace(trace_id: str, running_ids: set[str]) -> str:
    # Running = this Kernel process has accepted at least one request for this trace_id.
    return "running" if trace_id in running_ids else "offline"


def _agent_from_state(state: Optional[dict[str, Any]]) -> str:
    if not isinstance(state, dict):
        return "unknown"
    channel = state.get("channel")
    if isinstance(channel, str) and channel.strip() and channel != "unknown-channel":
        return channel.strip()
    device_key = state.get("device_key")
    if isinstance(device_key, str) and ":" in device_key:
        return device_key.split(":", 1)[0]
    return "unknown"


def _tokens_from_state(state: Optional[dict[str, Any]]) -> str:
    if not isinstance(state, dict):
        return "-"
    tokens = state.get("trace_total_tokens")
    cost = state.get("trace_total_cost_usd")
    if isinstance(tokens, int):
        if isinstance(cost, (int, float)):
            return f"{tokens} (${float(cost):.4f})"
        return str(tokens)
    return "-"


def _infer_agent_from_instructions(instructions: list[Any]) -> str:
    for instr in instructions[:8]:
        if not isinstance(instr, dict):
            continue
        content = instr.get("content")
        if isinstance(content, str):
            lower = content.lower()
            if "you are codex" in lower or "codex, a coding agent" in lower:
                return "codex"
            if "claude code" in lower or "anthropic's official cli" in lower:
                return "claude_code"
            if "openclaw" in lower:
                return "openclaw"
            if "nanobot" in lower:
                return "nanobot"
            if "hermes" in lower:
                return "hermes"
    return "unknown"


def _context_summary(instructions: list[Any]) -> str:
    count = len(instructions)
    try:
        nbytes = len(json.dumps(instructions, ensure_ascii=False).encode("utf-8"))
    except Exception:
        nbytes = 0
    if nbytes >= 1024:
        return f"{count} instr / {nbytes // 1024} KB"
    return f"{count} instr / {nbytes} B"


def _is_test_trace(trace_id: str) -> bool:
    return trace_id.startswith("trace-") and trace_id.endswith("-test")


def _pending_block_trace_ids() -> set[str]:
    out: set[str] = set()
    for item in list_pending_confirms():
        if not isinstance(item, dict):
            continue
        tid = item.get("trace_id")
        if isinstance(tid, str) and tid.strip():
            out.add(tid.strip())
    return out


def load_trace_rows(*, include_tests: bool = False) -> list[TraceRow]:
    active_meta = _load_active_trace_meta()
    running_ids = load_running_trace_ids()
    pending_ids = _pending_block_trace_ids()
    rows: list[TraceRow] = []
    inst_dir = _instruction_dir()
    if not inst_dir.is_dir():
        return rows
    for path in sorted(inst_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        trace_id = path.stem
        is_test = _is_test_trace(trace_id)
        if is_test and not include_tests:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        instructions = raw.get("instructions")
        if not isinstance(instructions, list):
            instructions = []
        created_raw = raw.get("created_at")
        created_at = _format_created_date(created_raw if isinstance(created_raw, str) else "-")
        state = active_meta.get(trace_id)
        agent = _agent_from_state(state)
        if agent == "unknown":
            agent = _infer_agent_from_instructions(instructions)
        status = _status_for_trace(trace_id, running_ids)
        rows.append(
            TraceRow(
                trace_id=trace_id,
                status=status,
                agent=agent,
                created_at=created_at,
                context=_context_summary(instructions),
                tokens=_tokens_from_state(state),
                pending_block="pending" if trace_id in pending_ids else "-",
                instruction_count=len(instructions),
                is_test=is_test,
            )
        )
    return rows


def resolve_trace_id(prefix_or_id: str, rows: list[TraceRow]) -> Optional[str]:
    needle = prefix_or_id.strip()
    if not needle:
        return None
    exact = [r.trace_id for r in rows if r.trace_id == needle]
    if len(exact) == 1:
        return exact[0]
    matches = [r.trace_id for r in rows if r.trace_id.startswith(needle)]
    if len(matches) == 1:
        return matches[0]
    return None


def load_trace_detail(trace_id: str) -> dict[str, Any]:
    path = _instruction_dir() / f"{trace_id}.json"
    detail: dict[str, Any] = {"trace_id": trace_id, "instructions": []}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                detail = raw
        except (OSError, json.JSONDecodeError):
            pass
    row = next((r for r in load_trace_rows(include_tests=True) if r.trace_id == trace_id), None)
    if row is not None:
        detail["status"] = row.status
        detail["agent"] = row.agent
        detail["tokens"] = row.tokens
        detail["context"] = row.context
        detail["pending_block"] = row.pending_block
    else:
        detail["pending_block"] = "-"
    return detail
