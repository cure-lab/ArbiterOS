"""Trace-scoped role assignment (init from agent + OS runtime override)."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from arbiteros_kernel.policy_check import (
    DEFAULT_ROLE_NAME,
    is_registered_role,
)

_LOCK = threading.Lock()


def _kernel_root() -> Path:
    return Path(__file__).resolve().parent.parent


def trace_state_path() -> Path:
    return _kernel_root() / "log" / "trace_state.json"


def display_role_name(role_name: Optional[str]) -> str:
    if isinstance(role_name, str) and role_name.strip():
        normalized = role_name.strip()
        if normalized.lower() != DEFAULT_ROLE_NAME:
            return normalized
    return DEFAULT_ROLE_NAME


def _empty_role_record() -> dict[str, Any]:
    return {
        "role_name": None,
        "role_source": None,
        "role_locked_by_os": False,
    }


def _normalize_role_record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _empty_role_record()
    role_name = raw.get("role_name")
    if not isinstance(role_name, str) or not role_name.strip():
        role_name = None
    else:
        role_name = role_name.strip()
        if role_name.lower() == DEFAULT_ROLE_NAME:
            role_name = None
    role_source = raw.get("role_source")
    if not isinstance(role_source, str) or not role_source.strip():
        role_source = None
    else:
        role_source = role_source.strip().lower()
        if role_source not in {"init", "os"}:
            role_source = None
    return {
        "role_name": role_name,
        "role_source": role_source,
        "role_locked_by_os": bool(raw.get("role_locked_by_os", False)),
    }


def _read_payload() -> dict[str, Any]:
    path = trace_state_path()
    if not path.exists():
        return {
            "version": 1,
            "updated_at": datetime.now().isoformat(),
            "states": {},
            "latest_user_id_by_channel": {},
            "roles_by_trace_id": {},
        }
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    if not isinstance(parsed.get("states"), dict):
        parsed["states"] = {}
    if not isinstance(parsed.get("latest_user_id_by_channel"), dict):
        parsed["latest_user_id_by_channel"] = {}
    roles = parsed.get("roles_by_trace_id")
    if not isinstance(roles, dict):
        parsed["roles_by_trace_id"] = {}
    return parsed


def _write_payload(payload: dict[str, Any]) -> None:
    path = trace_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["version"] = int(payload.get("version") or 1)
    payload["updated_at"] = datetime.now().isoformat()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def get_trace_role(trace_id: str) -> dict[str, Any]:
    tid = (trace_id or "").strip()
    if not tid:
        return _empty_role_record()
    with _LOCK:
        payload = _read_payload()
        roles = payload.get("roles_by_trace_id")
        if isinstance(roles, dict) and tid in roles:
            return _normalize_role_record(roles.get(tid))
        # Fallback: role fields mirrored onto an active state row.
        states = payload.get("states")
        if isinstance(states, dict):
            for value in states.values():
                if not isinstance(value, dict):
                    continue
                if str(value.get("trace_id") or "").strip() != tid:
                    continue
                return _normalize_role_record(value)
    return _empty_role_record()


def set_trace_role(
    trace_id: str,
    *,
    role_name: Optional[str],
    source: str,
    locked_by_os: bool,
) -> dict[str, Any]:
    """
    Persist role assignment for a trace.

    ``role_name`` None / ``default`` clears to global registry path.
    ``source`` should be ``init`` or ``os``.
    """
    tid = (trace_id or "").strip()
    if not tid:
        raise ValueError("trace_id is required")

    normalized_source = (source or "").strip().lower()
    if normalized_source not in {"init", "os"}:
        raise ValueError("source must be 'init' or 'os'")

    display = display_role_name(role_name)
    stored_name: Optional[str]
    if display == DEFAULT_ROLE_NAME:
        stored_name = None
    else:
        if not is_registered_role(display):
            raise ValueError(f"role not registered: {display}")
        stored_name = display

    record = {
        "role_name": stored_name,
        "role_source": normalized_source,
        "role_locked_by_os": bool(locked_by_os),
    }

    with _LOCK:
        payload = _read_payload()
        roles = dict(payload.get("roles_by_trace_id") or {})
        roles[tid] = dict(record)
        payload["roles_by_trace_id"] = roles

        states = payload.get("states")
        if isinstance(states, dict):
            for key, value in list(states.items()):
                if not isinstance(value, dict):
                    continue
                if str(value.get("trace_id") or "").strip() != tid:
                    continue
                updated = dict(value)
                updated["role_name"] = stored_name
                updated["role_source"] = normalized_source
                updated["role_locked_by_os"] = bool(locked_by_os)
                states[key] = updated
            payload["states"] = states

        _write_payload(payload)

    return {
        "trace_id": tid,
        "role_name": stored_name,
        "role_source": normalized_source,
        "role_locked_by_os": bool(locked_by_os),
        "display_role": display_role_name(stored_name),
    }


def resolve_effective_role_for_request(
    *,
    trace_id: str,
    requested_role: Optional[str],
) -> tuple[Optional[str], str, bool, Optional[str]]:
    """
    Decide effective role for one request.

    Returns:
      (effective_role_or_None_for_default, source, locked_by_os, warning_reason)

    Rules:
      - If OS-locked: keep stored role; ignore request suffix.
      - Else if requested role is registered: refresh init role.
      - Else if requested role present but unregistered: warning + default
        (clear stored role unless OS-locked — not locked here).
      - Else keep stored role (may be None = default).
    """
    tid = (trace_id or "").strip()
    current = get_trace_role(tid)
    locked = bool(current.get("role_locked_by_os"))
    stored = current.get("role_name")
    stored_name = stored.strip() if isinstance(stored, str) and stored.strip() else None

    requested = (
        requested_role.strip()
        if isinstance(requested_role, str) and requested_role.strip()
        else None
    )
    if requested and requested.lower() == DEFAULT_ROLE_NAME:
        requested = None

    if locked:
        source = str(current.get("role_source") or "os")
        return stored_name, source, True, None

    if requested:
        if is_registered_role(requested):
            set_trace_role(
                tid,
                role_name=requested,
                source="init",
                locked_by_os=False,
            )
            return requested, "init", False, None
        # Unregistered → default, warn, do not reject.
        set_trace_role(
            tid,
            role_name=None,
            source="init",
            locked_by_os=False,
        )
        return None, "init", False, f"role_not_registered:{requested}"

    source = str(current.get("role_source") or "init")
    return stored_name, source, False, None
