"""File-based bridge between Kernel policy confirm and the ArbiterOS TUI shell."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_TUI_DIR: Optional[Path] = None
_PENDING_FILE = "pending_confirm.json"
_ANSWERS_DIR = "answers"
_EVENTS_FILE = "events.jsonl"
_ENABLED_MARKER = "shell.enabled"


def _kernel_root() -> Path:
    return Path(__file__).resolve().parent.parent


def tui_dir() -> Path:
    global _TUI_DIR
    if _TUI_DIR is None:
        override = os.environ.get("ARBITEROS_TUI_DIR", "").strip()
        if override:
            _TUI_DIR = Path(override).expanduser().resolve()
        else:
            _TUI_DIR = _kernel_root() / "log" / "tui"
    _TUI_DIR.mkdir(parents=True, exist_ok=True)
    return _TUI_DIR


def enable_tui_mode() -> None:
    marker = tui_dir() / _ENABLED_MARKER
    marker.write_text(
        json.dumps({"enabled": True, "pid": os.getpid()}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.environ["ARBITEROS_TUI"] = "1"


def disable_tui_mode() -> None:
    marker = tui_dir() / _ENABLED_MARKER
    try:
        if marker.exists():
            marker.unlink()
    except OSError:
        pass


def is_tui_mode() -> bool:
    if os.environ.get("ARBITEROS_TUI", "").strip() in {"1", "true", "yes"}:
        return True
    marker = tui_dir() / _ENABLED_MARKER
    return marker.exists()


def emit_event(
    *,
    trace_id: str,
    level: str,
    message: str,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    if not is_tui_mode():
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id,
        "level": level,
        "message": message,
    }
    if extra:
        record["extra"] = extra
    path = tui_dir() / _EVENTS_FILE
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _read_pending() -> dict[str, Any]:
    path = tui_dir() / _PENDING_FILE
    if not path.exists():
        return {"items": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": []}
    if not isinstance(raw, dict):
        return {"items": []}
    items = raw.get("items")
    if not isinstance(items, list):
        return {"items": []}
    return {"items": items}


def _write_pending(items: list[dict[str, Any]]) -> None:
    path = tui_dir() / _PENDING_FILE
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "items": items}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def list_pending_confirms() -> list[dict[str, Any]]:
    return list(_read_pending().get("items", []))


def enqueue_confirm(
    *,
    trace_id: str,
    error_type: str,
    policy_names: list[str],
) -> str:
    request_id = str(uuid.uuid4())
    items = list(_read_pending().get("items", []))
    items.append(
        {
            "request_id": request_id,
            "trace_id": trace_id,
            "error_type": error_type,
            "policy_names": policy_names,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _write_pending(items)
    emit_event(
        trace_id=trace_id,
        level="confirm",
        message="Policy block needs confirmation (Y/N).",
        extra={"request_id": request_id, "policy_names": policy_names},
    )
    return request_id


def submit_confirm_answer(*, request_id: str, keep_block: bool) -> bool:
    items = list(_read_pending().get("items", []))
    matched = None
    remaining: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and item.get("request_id") == request_id:
            matched = item
            continue
        remaining.append(item)
    if matched is None:
        return False
    _write_pending(remaining)
    answers_dir = tui_dir() / _ANSWERS_DIR
    answers_dir.mkdir(parents=True, exist_ok=True)
    answer_path = answers_dir / f"{request_id}.json"
    answer_path.write_text(
        json.dumps(
            {
                "request_id": request_id,
                "trace_id": matched.get("trace_id"),
                "keep_block": keep_block,
                "answered_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return True


def request_confirm_via_tui(
    *,
    trace_id: str,
    error_type: str,
    policy_names: list[str],
    timeout_sec: float = 3600.0,
    poll_sec: float = 0.2,
) -> bool:
    """Block until the TUI answers, or timeout (default keep block)."""
    request_id = enqueue_confirm(
        trace_id=trace_id,
        error_type=error_type,
        policy_names=policy_names,
    )
    answer_path = tui_dir() / _ANSWERS_DIR / f"{request_id}.json"
    deadline = time.monotonic() + max(1.0, timeout_sec)
    while time.monotonic() < deadline:
        if answer_path.exists():
            try:
                raw = json.loads(answer_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return bool(raw.get("keep_block", True))
            except (OSError, json.JSONDecodeError):
                pass
            return True
        time.sleep(poll_sec)
    emit_event(
        trace_id=trace_id,
        level="warning",
        message="Confirm timed out; defaulting to keep block.",
        extra={"request_id": request_id},
    )
    submit_confirm_answer(request_id=request_id, keep_block=True)
    return True


def read_events(*, trace_id: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
    path = tui_dir() / _EVENTS_FILE
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-limit * 3 :]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if trace_id and row.get("trace_id") != trace_id:
            continue
        rows.append(row)
    return rows[-limit:]
