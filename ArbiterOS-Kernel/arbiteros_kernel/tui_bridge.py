"""File-based bridge between Kernel confirms and the ArbiterOS TUI shell.

Supports:
- ``kind=policy`` (default): Gateway policy Yes/No
- ``kind=said_done``: PreToolUse said/done escalate Yes/No (Y=deny, N=allow)
"""

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

KIND_POLICY = "policy"
KIND_SAID_DONE = "said_done"


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


def pending_kind(item: dict[str, Any]) -> str:
    kind = item.get("kind")
    if isinstance(kind, str) and kind.strip():
        return kind.strip()
    return KIND_POLICY


def sort_pending_confirms(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Policy first, then said/done; stable by created_at within each kind."""

    def _key(item: dict[str, Any]) -> tuple[int, str]:
        kind = pending_kind(item)
        order = 0 if kind == KIND_POLICY else 1
        return (order, str(item.get("created_at") or ""))

    return sorted(
        [i for i in items if isinstance(i, dict)],
        key=_key,
    )


def _defender_hook_dir() -> Path:
    override = (
        os.environ.get("ARBITEROS_DEFENDER_HOOK_DIR", "").strip()
        or os.environ.get("CODEX_DEFENDER_HOOK_DIR", "").strip()
    )
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".arbiteros" / "defender-hook"


def _write_defender_decision(request_id: str, decision: str, reason: str) -> None:
    """Notify waiting PreToolUse hook (same files as hooks/defender/common.py)."""
    base = _defender_hook_dir()
    decisions = base / "decisions"
    pending = base / "pending"
    decisions.mkdir(parents=True, exist_ok=True)
    payload = {
        "request_id": request_id,
        "decision": decision,
        "reason": reason,
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    path = decisions / f"{request_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    try:
        (pending / f"{request_id}.json").unlink(missing_ok=True)
    except OSError:
        pass


def clear_pending_confirm(request_id: str) -> bool:
    """Drop a TUI pending item without writing an answer (e.g. watch.py answered)."""
    items = list(_read_pending().get("items", []))
    remaining: list[dict[str, Any]] = []
    removed = False
    for item in items:
        if isinstance(item, dict) and item.get("request_id") == request_id:
            removed = True
            continue
        remaining.append(item)
    if removed:
        _write_pending(remaining)
    return removed


def enqueue_confirm(
    *,
    trace_id: str,
    error_type: str,
    policy_names: list[str],
    kind: str = KIND_POLICY,
    request_id: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> str:
    rid = (request_id or "").strip() or str(uuid.uuid4())
    kind_norm = (kind or KIND_POLICY).strip() or KIND_POLICY
    items = list(_read_pending().get("items", []))
    # Replace existing row with same request_id (idempotent re-enqueue).
    items = [
        i
        for i in items
        if not (isinstance(i, dict) and i.get("request_id") == rid)
    ]
    row: dict[str, Any] = {
        "request_id": rid,
        "trace_id": trace_id,
        "error_type": error_type,
        "policy_names": policy_names,
        "kind": kind_norm,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        row["extra"] = extra
    items.append(row)
    _write_pending(items)
    if kind_norm == KIND_SAID_DONE:
        message = "Said/Done mismatch needs confirmation (Y=deny, N=allow)."
    else:
        message = "Policy block needs confirmation (Y/N)."
    emit_event(
        trace_id=trace_id,
        level="confirm",
        message=message,
        extra={
            "request_id": rid,
            "policy_names": policy_names,
            "kind": kind_norm,
        },
    )
    return rid


def submit_confirm_answer(*, request_id: str, keep_block: bool) -> bool:
    """Record Y/N. keep_block=True means Y (deny / keep block); False means N (allow)."""
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
    kind = pending_kind(matched)
    answers_dir = tui_dir() / _ANSWERS_DIR
    answers_dir.mkdir(parents=True, exist_ok=True)
    answer_path = answers_dir / f"{request_id}.json"
    answer_path.write_text(
        json.dumps(
            {
                "request_id": request_id,
                "trace_id": matched.get("trace_id"),
                "keep_block": keep_block,
                "kind": kind,
                "answered_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if kind == KIND_SAID_DONE:
        # Align with policy: Y keep_block → deny tool; N → allow mismatched Done.
        decision = "deny" if keep_block else "allow"
        reason = (
            "user_denied_in_tui_said_done"
            if keep_block
            else "user_allowed_in_tui_said_done"
        )
        _write_defender_decision(request_id, decision, reason)
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
        kind=KIND_POLICY,
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
