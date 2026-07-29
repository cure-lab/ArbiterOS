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
    """Prefer route agent (``model;agent``), never transport channel."""
    if not isinstance(state, dict):
        return "unknown"
    agent_name = state.get("agent_name")
    if isinstance(agent_name, str) and agent_name.strip():
        return agent_name.strip().lower()
    rounds = state.get("token_usage_rounds")
    if isinstance(rounds, list):
        for round_record in reversed(rounds):
            if not isinstance(round_record, dict):
                continue
            agent = round_record.get("agent")
            if isinstance(agent, str) and agent.strip():
                return agent.strip().lower()
            raw_model = round_record.get("model")
            if isinstance(raw_model, str) and ";" in raw_model:
                parts = [p.strip() for p in raw_model.split(";")]
                if len(parts) > 1 and parts[1]:
                    return parts[1].lower()
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
    """Best-effort agent guess from early instruction text.

    Prefer distinctive identity lines over tool-catalog mentions (OpenClaw system
    prompts often name Codex/Claude Code as tools).
    """
    for instr in instructions[:8]:
        if not isinstance(instr, dict):
            continue
        content = instr.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        lower = content.lower()
        head = lower[:400]
        # Strong identity markers first (usually near the top of system prompts).
        if "running inside openclaw" in head or "you are a personal assistant running inside openclaw" in head:
            return "openclaw"
        if "you are codex" in head or "codex, a coding agent" in head:
            return "codex"
        if "claude code, anthropic's official cli" in head or (
            "you are claude code" in head
        ):
            return "claude_code"
        if "nanobot" in head and "you are" in head:
            return "nanobot"
        if "hermes" in head and "you are" in head:
            return "hermes"
    # Weaker fallbacks on full early content.
    for instr in instructions[:3]:
        if not isinstance(instr, dict):
            continue
        content = instr.get("content")
        if not isinstance(content, str):
            continue
        lower = content.lower()
        if "openclaw" in lower[:200]:
            return "openclaw"
        if "anthropic's official cli for claude" in lower:
            return "claude_code"
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
