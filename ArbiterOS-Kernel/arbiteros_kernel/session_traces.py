"""Trace IDs touched since the current Kernel (proxy) process started."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_SESSION_LOCK = threading.Lock()
_SESSION_PATH: Optional[Path] = None


def _kernel_root() -> Path:
    return Path(__file__).resolve().parent.parent


def session_file_path() -> Path:
    global _SESSION_PATH
    if _SESSION_PATH is None:
        override = os.environ.get("ARBITEROS_SESSION_TRACES_FILE", "").strip()
        if override:
            _SESSION_PATH = Path(override).expanduser().resolve()
        else:
            _SESSION_PATH = _kernel_root() / "log" / "session" / "runtime_traces.json"
    _SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _SESSION_PATH


def _empty_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "kernel_pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "trace_ids": {},
    }


def _read_payload() -> dict[str, Any]:
    path = session_file_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    trace_ids = raw.get("trace_ids")
    if not isinstance(trace_ids, dict):
        raw["trace_ids"] = {}
    return raw


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _session_kernel_pid(payload: dict[str, Any]) -> Optional[int]:
    pid = payload.get("kernel_pid")
    if pid is None:
        pid = payload.get("pid")  # legacy field
    if isinstance(pid, int):
        return pid
    return None


def _session_is_live(payload: dict[str, Any]) -> bool:
    if not payload:
        return False
    return _pid_alive(_session_kernel_pid(payload))


def _write_payload(payload: dict[str, Any]) -> None:
    path = session_file_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def reset_session() -> None:
    """Clear running-trace registry (call before starting a fresh proxy)."""
    with _SESSION_LOCK:
        path = session_file_path()
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def register_trace(trace_id: Optional[str]) -> None:
    """Mark a trace as running for this Kernel process (proxy only)."""
    if not isinstance(trace_id, str) or not trace_id.strip():
        return
    tid = trace_id.strip()
    with _SESSION_LOCK:
        payload = _read_payload()
        kernel_pid = _session_kernel_pid(payload)
        # New proxy process boot: start a fresh session registry.
        if kernel_pid != os.getpid():
            payload = _empty_payload()
        trace_ids = payload.setdefault("trace_ids", {})
        if not isinstance(trace_ids, dict):
            trace_ids = {}
            payload["trace_ids"] = trace_ids
        if tid not in trace_ids:
            trace_ids[tid] = datetime.now(timezone.utc).isoformat()
        payload["kernel_pid"] = os.getpid()
        if "started_at" not in payload:
            payload["started_at"] = datetime.now(timezone.utc).isoformat()
        payload["version"] = 1
        _write_payload(payload)


def load_running_trace_ids() -> set[str]:
    """Read trace IDs registered by the current Kernel proxy (any process may read)."""
    with _SESSION_LOCK:
        payload = _read_payload()
        if not _session_is_live(payload):
            return set()
        trace_ids = payload.get("trace_ids")
        if not isinstance(trace_ids, dict):
            return set()
        return {str(k) for k in trace_ids.keys() if isinstance(k, str) and k.strip()}


def kernel_session_pid() -> Optional[int]:
    payload = _read_payload()
    if not _session_is_live(payload):
        return None
    return _session_kernel_pid(payload)


def is_trace_running(trace_id: str) -> bool:
    return trace_id in load_running_trace_ids()
