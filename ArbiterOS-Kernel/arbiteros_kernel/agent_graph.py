"""Parent/subagent graph for Claude Code and OpenClaw.

Claude Code: parent requests have ``x-claude-code-session-id`` only; subagents
reuse that session id and add ``x-claude-code-agent-id``.

OpenClaw: parent calls ``sessions_spawn``; the tool result has
``childSessionKey`` (``agent:<id>:subagent:<uuid>``). The child system prompt
repeats that key as ``Your session:``. Traces are already split by message-id;
this module records the spawn edge so TUI ``graph`` can print a tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

_LOCK = threading.Lock()
_GRAPH_PATH: Optional[Path] = None

_AGENT_ID_RE = re.compile(r"agentId:\s*([A-Za-z0-9._-]+)", re.IGNORECASE)
_USER_ID_PREFIX = "claude-code-session-"
_USER_ID_AGENT_MARK = ":agent-"
_OPENCLAW_CHILD_SESSION_RE = re.compile(
    r"agent:[A-Za-z0-9._-]+:subagent:[A-Za-z0-9._-]+",
    re.IGNORECASE,
)


def _kernel_root() -> Path:
    return Path(__file__).resolve().parent.parent


def reset_graph_file_cache() -> None:
    """Drop the cached default path (tests that change env or tmp files)."""
    global _GRAPH_PATH
    _GRAPH_PATH = None


def graph_file_path() -> Path:
    global _GRAPH_PATH
    override = os.environ.get("ARBITEROS_AGENT_GRAPH_FILE", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    if _GRAPH_PATH is None:
        _GRAPH_PATH = _kernel_root() / "log" / "said_done" / "agent_graph.json"
        _GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _GRAPH_PATH


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": _now_iso(),
        "by_agent_id": {},
        "by_trace": {},
    }


def _read_payload() -> dict[str, Any]:
    path = graph_file_path()
    if not path.exists():
        return _empty_payload()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_payload()
    if not isinstance(raw, dict):
        return _empty_payload()
    if not isinstance(raw.get("by_agent_id"), dict):
        raw["by_agent_id"] = {}
    if not isinstance(raw.get("by_trace"), dict):
        raw["by_trace"] = {}
    return raw


def _write_payload(payload: dict[str, Any]) -> None:
    path = graph_file_path()
    payload["version"] = 1
    payload["updated_at"] = _now_iso()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def parse_agent_id_from_text(text: Any) -> Optional[str]:
    if not isinstance(text, str) or not text.strip():
        return None
    match = _AGENT_ID_RE.search(text)
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def claude_code_user_id(session_id: str, agent_id: Optional[str] = None) -> str:
    sid = (session_id or "").strip()
    aid = (agent_id or "").strip() if isinstance(agent_id, str) else ""
    if not sid:
        return ""
    if aid:
        return f"{_USER_ID_PREFIX}{sid}{_USER_ID_AGENT_MARK}{aid}"
    return f"{_USER_ID_PREFIX}{sid}"


def claude_code_parent_user_id(session_id: str) -> str:
    return claude_code_user_id(session_id, agent_id=None)


def parse_claude_code_user_id(user_id: Any) -> tuple[Optional[str], Optional[str]]:
    """Return ``(session_id, agent_id)`` from a Claude Code Kernel user_id."""
    if not isinstance(user_id, str) or not user_id.startswith(_USER_ID_PREFIX):
        return None, None
    rest = user_id[len(_USER_ID_PREFIX) :]
    if _USER_ID_AGENT_MARK in rest:
        sid, aid = rest.split(_USER_ID_AGENT_MARK, 1)
        sid = sid.strip() or None
        aid = aid.strip() or None
        return sid, aid
    sid = rest.strip() or None
    return sid, None


def _json_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _block_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                nested = item.get("content")
                if isinstance(nested, str):
                    parts.append(nested)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def iter_agent_spawns_from_messages(messages: Any) -> list[dict[str, str]]:
    """Find Claude Code ``Agent`` launches and their ``agentId`` tool results."""
    if not isinstance(messages, list):
        return []
    meta_by_call: dict[str, str] = {}
    results: list[dict[str, str]] = []
    seen: set[str] = set()

    def _note_call(call_id: Any, subagent_type: Any) -> None:
        if not isinstance(call_id, str) or not call_id.strip():
            return
        label = ""
        if isinstance(subagent_type, str) and subagent_type.strip():
            label = subagent_type.strip()
        meta_by_call[call_id.strip()] = label

    def _note_result(call_id: Any, text: Any) -> None:
        aid = parse_agent_id_from_text(_block_text(text) if not isinstance(text, str) else text)
        if not aid or aid in seen:
            return
        cid = call_id.strip() if isinstance(call_id, str) else ""
        seen.add(aid)
        results.append(
            {
                "agent_id": aid,
                "tool_call_id": cid,
                "subagent_type": meta_by_call.get(cid, ""),
            }
        )

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = str(fn.get("name") or tc.get("name") or "").strip()
                if name.lower() != "agent":
                    continue
                args = _json_args(fn.get("arguments") or tc.get("arguments"))
                _note_call(
                    tc.get("id") or tc.get("tool_call_id"),
                    args.get("subagent_type"),
                )
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if str(block.get("type") or "").strip() != "tool_use":
                        continue
                    if str(block.get("name") or "").strip().lower() != "agent":
                        continue
                    args = block.get("input") if isinstance(block.get("input"), dict) else {}
                    _note_call(block.get("id"), args.get("subagent_type"))
        elif role == "tool":
            _note_result(msg.get("tool_call_id"), msg.get("content"))
        elif role == "user":
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if str(block.get("type") or "").strip() != "tool_result":
                        continue
                    _note_result(block.get("tool_use_id"), block.get("content"))

    return results


def spawn_task_key(task: Any) -> Optional[str]:
    if not isinstance(task, str):
        return None
    norm = re.sub(r"\s+", " ", task).strip()
    if not norm:
        return None
    digest = hashlib.sha256(norm.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"task:{digest}"


def parse_openclaw_child_session_key(raw: Any) -> Optional[str]:
    if isinstance(raw, dict):
        for key in ("childSessionKey", "sessionKey", "child_session_key"):
            value = raw.get(key)
            if isinstance(value, str) and ":subagent:" in value.lower():
                return value.strip().rstrip(".")
        for value in raw.values():
            hit = parse_openclaw_child_session_key(value)
            if hit:
                return hit
        return None
    if isinstance(raw, list):
        for item in raw:
            hit = parse_openclaw_child_session_key(item)
            if hit:
                return hit
        return None
    text = _block_text(raw)
    if not text.strip():
        return None
    parsed = _json_args(text)
    if parsed:
        hit = parse_openclaw_child_session_key(parsed)
        if hit:
            return hit
    match = _OPENCLAW_CHILD_SESSION_RE.search(text)
    if match:
        return match.group(0).strip().rstrip(".")
    return None


def _short_agent_id(agent_id: str) -> str:
    value = agent_id.strip()
    if ":subagent:" in value.lower():
        return value.rsplit(":", 1)[-1]
    return value


def iter_sessions_spawn_from_messages(messages: Any) -> list[dict[str, str]]:
    """Find OpenClaw ``sessions_spawn`` launches and ``childSessionKey`` results."""
    if not isinstance(messages, list):
        return []
    meta_by_call: dict[str, dict[str, str]] = {}
    results: list[dict[str, str]] = []
    seen: set[str] = set()

    def _note_call(call_id: Any, args: dict[str, Any]) -> None:
        if not isinstance(call_id, str) or not call_id.strip():
            return
        task = args.get("task") if isinstance(args.get("task"), str) else ""
        label = args.get("label") if isinstance(args.get("label"), str) else ""
        meta_by_call[call_id.strip()] = {
            "task": task.strip(),
            "subagent_type": label.strip(),
        }

    def _emit(*, agent_id: str, call_id: str, meta: dict[str, str]) -> None:
        keys = [agent_id] if agent_id and agent_id not in seen else []
        task_key = spawn_task_key(meta.get("task"))
        if task_key and task_key not in seen:
            keys.append(task_key)
        if not keys:
            return
        for key in keys:
            seen.add(key)
        results.append(
            {
                "agent_id": agent_id,
                "task_key": task_key or "",
                "task": meta.get("task") or "",
                "tool_call_id": call_id,
                "subagent_type": meta.get("subagent_type") or "",
            }
        )

    def _note_result(call_id: Any, text: Any) -> None:
        cid = call_id.strip() if isinstance(call_id, str) else ""
        meta = meta_by_call.get(cid, {})
        aid = parse_openclaw_child_session_key(text)
        _emit(agent_id=aid or "", call_id=cid, meta=meta)

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "assistant":
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = str(fn.get("name") or tc.get("name") or "").strip()
                if name.lower() != "sessions_spawn":
                    continue
                args = _json_args(fn.get("arguments") or tc.get("arguments"))
                _note_call(tc.get("id") or tc.get("tool_call_id"), args)
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if str(block.get("type") or "").strip() != "tool_use":
                        continue
                    if str(block.get("name") or "").strip().lower() != "sessions_spawn":
                        continue
                    args = block.get("input") if isinstance(block.get("input"), dict) else {}
                    _note_call(block.get("id"), args)
        elif role == "tool":
            _note_result(msg.get("tool_call_id"), msg.get("content"))
        elif role == "user":
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if str(block.get("type") or "").strip() != "tool_result":
                        continue
                    _note_result(block.get("tool_use_id"), block.get("content"))

    # Tool-call-only (post-call, before the spawn result lands).
    for call_id, meta in meta_by_call.items():
        task_key = spawn_task_key(meta.get("task"))
        if not task_key or task_key in seen:
            continue
        _emit(agent_id="", call_id=call_id, meta=meta)

    return results


def record_spawn(
    *,
    parent_trace_id: Optional[str],
    session_id: Optional[str],
    agent_id: Optional[str],
    subagent_type: Optional[str] = None,
    tool_call_id: Optional[str] = None,
) -> None:
    if not isinstance(parent_trace_id, str) or not parent_trace_id.strip():
        return
    if not isinstance(agent_id, str) or not agent_id.strip():
        return
    aid = agent_id.strip()
    parent = parent_trace_id.strip()
    sid = session_id.strip() if isinstance(session_id, str) and session_id.strip() else None
    label = (
        subagent_type.strip()
        if isinstance(subagent_type, str) and subagent_type.strip()
        else None
    )
    tcid = (
        tool_call_id.strip()
        if isinstance(tool_call_id, str) and tool_call_id.strip()
        else None
    )
    with _LOCK:
        payload = _read_payload()
        by_agent = payload.setdefault("by_agent_id", {})
        prev = by_agent.get(aid) if isinstance(by_agent.get(aid), dict) else {}
        entry = {
            "parent_trace_id": parent,
            "session_id": sid or prev.get("session_id"),
            "subagent_type": label or prev.get("subagent_type"),
            "tool_call_id": tcid or prev.get("tool_call_id"),
            "child_trace_id": prev.get("child_trace_id"),
            "updated_at": _now_iso(),
        }
        by_agent[aid] = entry
        child_tid = entry.get("child_trace_id")
        if isinstance(child_tid, str) and child_tid.strip():
            _upsert_child_unlocked(
                payload,
                child_trace_id=child_tid.strip(),
                parent_trace_id=parent,
                session_id=entry.get("session_id"),
                agent_id=aid,
                subagent_type=entry.get("subagent_type"),
            )
        _write_payload(payload)


def _upsert_child_unlocked(
    payload: dict[str, Any],
    *,
    child_trace_id: str,
    parent_trace_id: str,
    session_id: Any,
    agent_id: str,
    subagent_type: Any,
) -> None:
    by_trace = payload.setdefault("by_trace", {})
    prev = by_trace.get(child_trace_id) if isinstance(by_trace.get(child_trace_id), dict) else {}
    by_trace[child_trace_id] = {
        "parent_trace_id": parent_trace_id,
        "agent_id": agent_id,
        "session_id": session_id or prev.get("session_id"),
        "subagent_type": subagent_type or prev.get("subagent_type"),
        "updated_at": _now_iso(),
    }
    by_agent = payload.setdefault("by_agent_id", {})
    agent_entry = by_agent.get(agent_id)
    if isinstance(agent_entry, dict):
        agent_entry["child_trace_id"] = child_trace_id
        agent_entry["parent_trace_id"] = parent_trace_id
        if session_id:
            agent_entry["session_id"] = session_id
        if subagent_type:
            agent_entry["subagent_type"] = subagent_type
        agent_entry["updated_at"] = _now_iso()


def link_child(
    *,
    child_trace_id: Optional[str],
    session_id: Optional[str],
    agent_id: Optional[str],
    parent_trace_id: Optional[str],
) -> Optional[str]:
    """Attach ``child_trace_id`` under ``parent_trace_id``. Returns parent id used."""
    if not isinstance(child_trace_id, str) or not child_trace_id.strip():
        return None
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None
    child = child_trace_id.strip()
    aid = agent_id.strip()
    sid = session_id.strip() if isinstance(session_id, str) and session_id.strip() else None
    with _LOCK:
        payload = _read_payload()
        by_agent = payload.get("by_agent_id")
        spawn = by_agent.get(aid) if isinstance(by_agent, dict) else None
        parent = parent_trace_id.strip() if isinstance(parent_trace_id, str) else ""
        if not parent and isinstance(spawn, dict):
            stored = spawn.get("parent_trace_id")
            if isinstance(stored, str) and stored.strip():
                parent = stored.strip()
        if not parent or parent == child:
            return None
        label = None
        if isinstance(spawn, dict):
            raw_label = spawn.get("subagent_type")
            if isinstance(raw_label, str) and raw_label.strip():
                label = raw_label.strip()
            if not sid:
                stored_sid = spawn.get("session_id")
                if isinstance(stored_sid, str) and stored_sid.strip():
                    sid = stored_sid.strip()
        _upsert_child_unlocked(
            payload,
            child_trace_id=child,
            parent_trace_id=parent,
            session_id=sid,
            agent_id=aid,
            subagent_type=label,
        )
        _write_payload(payload)
        return parent


def resolve_parent_trace_id(*, agent_id: Optional[str]) -> Optional[str]:
    if not isinstance(agent_id, str) or not agent_id.strip():
        return None
    with _LOCK:
        payload = _read_payload()
        hit = payload.get("by_agent_id", {}).get(agent_id.strip())
        if not isinstance(hit, dict):
            return None
        parent = hit.get("parent_trace_id")
        if isinstance(parent, str) and parent.strip():
            return parent.strip()
        return None


def load_graph() -> dict[str, Any]:
    with _LOCK:
        return _read_payload()


def parent_of(trace_id: str) -> Optional[str]:
    if not isinstance(trace_id, str) or not trace_id.strip():
        return None
    with _LOCK:
        hit = _read_payload().get("by_trace", {}).get(trace_id.strip())
        if not isinstance(hit, dict):
            return None
        parent = hit.get("parent_trace_id")
        if isinstance(parent, str) and parent.strip():
            return parent.strip()
        return None


def children_of(trace_id: str) -> list[str]:
    if not isinstance(trace_id, str) or not trace_id.strip():
        return []
    parent = trace_id.strip()
    out: list[str] = []
    with _LOCK:
        by_trace = _read_payload().get("by_trace") or {}
        if not isinstance(by_trace, dict):
            return []
        for child_id, meta in by_trace.items():
            if not isinstance(child_id, str) or not isinstance(meta, dict):
                continue
            if meta.get("parent_trace_id") == parent:
                out.append(child_id)
    return sorted(out)


def _label_for_trace(
    trace_id: str,
    *,
    by_trace: dict[str, Any],
    extra: Optional[dict[str, Any]] = None,
) -> str:
    meta = by_trace.get(trace_id) if isinstance(by_trace.get(trace_id), dict) else {}
    info = extra or {}
    agent = str(info.get("agent") or "").strip() or "unknown"
    status = str(info.get("status") or "").strip()
    aid = meta.get("agent_id") if isinstance(meta, dict) else None
    kind = meta.get("subagent_type") if isinstance(meta, dict) else None
    if isinstance(aid, str) and aid.strip():
        kind_s = kind.strip() if isinstance(kind, str) and kind.strip() else "subagent"
        node = f"{kind_s} [{_short_agent_id(aid)}]"
    else:
        node = "parent"
    bits = [node, agent, trace_id]
    if status:
        bits.append(status)
    return "  ".join(bits)


def format_graph_trees(
    *,
    extra_by_trace: Optional[dict[str, dict[str, Any]]] = None,
    known_trace_ids: Optional[Iterable[str]] = None,
) -> str:
    """ASCII forest of parent → spawned subagents."""
    payload = load_graph()
    by_trace = payload.get("by_trace") if isinstance(payload.get("by_trace"), dict) else {}
    extra = extra_by_trace or {}

    children: dict[str, list[str]] = {}
    all_ids: set[str] = set()
    if known_trace_ids:
        all_ids.update(tid.strip() for tid in known_trace_ids if isinstance(tid, str) and tid.strip())
    for child_id, meta in by_trace.items():
        if not isinstance(child_id, str) or not isinstance(meta, dict):
            continue
        parent = meta.get("parent_trace_id")
        if not isinstance(parent, str) or not parent.strip():
            continue
        children.setdefault(parent.strip(), []).append(child_id)
        all_ids.add(parent.strip())
        all_ids.add(child_id)
    for kid_list in children.values():
        kid_list.sort()

    child_ids = {cid for kids in children.values() for cid in kids}
    roots = sorted(tid for tid in all_ids if tid not in child_ids and tid in children)
    # Isolated traces (no parent/child) are omitted — graph is about relations.

    if not roots:
        return "No parent/subagent relations recorded yet."

    lines = ["Agent graph  (parent → spawned subagent; Claude Code / OpenClaw)"]
    for root in roots:
        _walk_tree(
            lines,
            trace_id=root,
            children=children,
            by_trace=by_trace,
            extra=extra,
            prefix="",
            is_last=True,
            is_root=True,
            path=set(),
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _walk_tree(
    lines: list[str],
    *,
    trace_id: str,
    children: dict[str, list[str]],
    by_trace: dict[str, Any],
    extra: dict[str, dict[str, Any]],
    prefix: str,
    is_last: bool,
    is_root: bool,
    path: set[str],
) -> None:
    if trace_id in path:
        marker = f"(cycle) {trace_id}"
        if is_root:
            lines.append(marker)
        else:
            branch = "└─ " if is_last else "├─ "
            lines.append(f"{prefix}{branch}{marker}")
        return
    label = _label_for_trace(trace_id, by_trace=by_trace, extra=extra.get(trace_id))
    if is_root:
        lines.append(label)
        child_prefix = ""
    else:
        branch = "└─ " if is_last else "├─ "
        lines.append(f"{prefix}{branch}{label}")
        child_prefix = f"{prefix}{'   ' if is_last else '│  '}"
    next_path = path | {trace_id}
    kids = children.get(trace_id) or []
    for idx, kid in enumerate(kids):
        _walk_tree(
            lines,
            trace_id=kid,
            children=children,
            by_trace=by_trace,
            extra=extra,
            prefix=child_prefix,
            is_last=idx == len(kids) - 1,
            is_root=False,
            path=next_path,
        )
