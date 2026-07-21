"""Scheme-B session binding: map agent session keys → Kernel trace_id.

Additive index only — does not change device_key / trace resolution.
Hook PreToolUse and other agents resolve ownership through this file.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

_LOCK = threading.Lock()
_INDEX_PATH: Optional[Path] = None


def _kernel_root() -> Path:
    return Path(__file__).resolve().parent.parent


def index_file_path() -> Path:
    global _INDEX_PATH
    if _INDEX_PATH is None:
        override = os.environ.get("ARBITEROS_SESSION_INDEX_FILE", "").strip()
        if override:
            _INDEX_PATH = Path(override).expanduser().resolve()
        else:
            _INDEX_PATH = _kernel_root() / "log" / "said_done" / "session_index.json"
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _INDEX_PATH


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": _now_iso(),
        "by_key": {},
        "by_tool_call_id": {},
    }


def _read_payload() -> dict[str, Any]:
    path = index_file_path()
    if not path.exists():
        return _empty_payload()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_payload()
    if not isinstance(raw, dict):
        return _empty_payload()
    if not isinstance(raw.get("by_key"), dict):
        raw["by_key"] = {}
    if not isinstance(raw.get("by_tool_call_id"), dict):
        raw["by_tool_call_id"] = {}
    return raw


def _write_payload(payload: dict[str, Any]) -> None:
    path = index_file_path()
    payload["version"] = 1
    payload["updated_at"] = _now_iso()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _normalize_key(kind: str, value: str) -> Optional[str]:
    if not isinstance(kind, str) or not isinstance(value, str):
        return None
    k = kind.strip().lower()
    v = value.strip()
    if not k or not v:
        return None
    return f"{k}:{v}"


def register_binding(
    *,
    trace_id: Optional[str],
    device_key: Optional[str] = None,
    prompt_cache_key: Optional[str] = None,
    session_id: Optional[str] = None,
    channel: Optional[str] = None,
    extra_keys: Optional[Iterable[tuple[str, str]]] = None,
) -> None:
    """Register one or more lookup keys for ``trace_id``. Failures are swallowed by callers."""
    if not isinstance(trace_id, str) or not trace_id.strip():
        return
    tid = trace_id.strip()
    keys: list[str] = []

    dk = _normalize_key("device_key", device_key) if device_key else None
    if dk:
        keys.append(dk)

    pck = _normalize_key("prompt_cache_key", prompt_cache_key) if prompt_cache_key else None
    if pck:
        keys.append(pck)
        # Codex hook session_id is typically the same stable id as prompt_cache_key.
        sid_from_pck = _normalize_key("session_id", prompt_cache_key)
        if sid_from_pck:
            keys.append(sid_from_pck)

    sid = _normalize_key("session_id", session_id) if session_id else None
    if sid:
        keys.append(sid)

    if channel and session_id:
        ch_sid = _normalize_key(f"session_id@{channel.strip().lower()}", session_id)
        if ch_sid:
            keys.append(ch_sid)

    if extra_keys:
        for kind, value in extra_keys:
            nk = _normalize_key(kind, value)
            if nk:
                keys.append(nk)

    # de-dupe preserving order
    seen: set[str] = set()
    unique_keys: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            unique_keys.append(k)
    if not unique_keys:
        return

    entry = {
        "trace_id": tid,
        "device_key": (device_key or "").strip() or None,
        "channel": (channel or "").strip() or None,
        "updated_at": _now_iso(),
    }

    with _LOCK:
        payload = _read_payload()
        by_key = payload.setdefault("by_key", {})
        if not isinstance(by_key, dict):
            by_key = {}
            payload["by_key"] = by_key
        for k in unique_keys:
            by_key[k] = dict(entry)
        _write_payload(payload)


def register_tool_call_id(*, tool_call_id: Optional[str], trace_id: Optional[str]) -> None:
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        return
    if not isinstance(trace_id, str) or not trace_id.strip():
        return
    tcid = tool_call_id.strip()
    tid = trace_id.strip()
    with _LOCK:
        payload = _read_payload()
        by_tc = payload.setdefault("by_tool_call_id", {})
        if not isinstance(by_tc, dict):
            by_tc = {}
            payload["by_tool_call_id"] = by_tc
        by_tc[tcid] = {
            "trace_id": tid,
            "updated_at": _now_iso(),
        }
        # Cap growth: keep newest ~4000 tool_call mappings.
        if len(by_tc) > 4000:
            # dict preserves insertion order (3.7+); drop oldest.
            overflow = len(by_tc) - 3500
            for old_key in list(by_tc.keys())[:overflow]:
                by_tc.pop(old_key, None)
        _write_payload(payload)


def resolve_trace_id(
    *,
    session_id: Optional[str] = None,
    prompt_cache_key: Optional[str] = None,
    device_key: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    channel: Optional[str] = None,
) -> Optional[str]:
    """Resolve a Kernel ``trace_id`` from any available agent-side identity."""
    with _LOCK:
        payload = _read_payload()
        by_tc = payload.get("by_tool_call_id")
        if isinstance(tool_call_id, str) and tool_call_id.strip() and isinstance(by_tc, dict):
            hit = by_tc.get(tool_call_id.strip())
            if isinstance(hit, dict):
                tid = hit.get("trace_id")
                if isinstance(tid, str) and tid.strip():
                    return tid.strip()

        by_key = payload.get("by_key")
        if not isinstance(by_key, dict):
            return None

        candidates: list[str] = []
        if device_key:
            nk = _normalize_key("device_key", device_key)
            if nk:
                candidates.append(nk)
        if prompt_cache_key:
            nk = _normalize_key("prompt_cache_key", prompt_cache_key)
            if nk:
                candidates.append(nk)
            nk = _normalize_key("session_id", prompt_cache_key)
            if nk:
                candidates.append(nk)
        if session_id:
            if channel:
                nk = _normalize_key(f"session_id@{channel.strip().lower()}", session_id)
                if nk:
                    candidates.append(nk)
            nk = _normalize_key("session_id", session_id)
            if nk:
                candidates.append(nk)

        for key in candidates:
            hit = by_key.get(key)
            if isinstance(hit, dict):
                tid = hit.get("trace_id")
                if isinstance(tid, str) and tid.strip():
                    return tid.strip()
        return None
