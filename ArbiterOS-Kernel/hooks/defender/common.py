"""Shared paths and helpers for ArbiterOS defender PreToolUse gate."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HOOKS_DIR = Path(__file__).resolve().parent
_KERNEL_ROOT = _HOOKS_DIR.parent.parent


def ensure_kernel_on_path() -> Path:
    """Make ``arbiteros_kernel`` importable for system python3 hook invocations."""
    root = Path(
        os.environ.get("ARBITEROS_KERNEL_ROOT", "").strip() or str(_KERNEL_ROOT)
    ).expanduser().resolve()
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return root


_DEFAULT_LOG_DIR = Path.home() / ".arbiteros" / "defender-hook"
LOG_DIR = Path(
    os.environ.get("ARBITEROS_DEFENDER_HOOK_DIR")
    or os.environ.get("CODEX_DEFENDER_HOOK_DIR")
    or _DEFAULT_LOG_DIR
)
EVENTS_FILE = LOG_DIR / "events.jsonl"
PENDING_DIR = LOG_DIR / "pending"
DECISIONS_DIR = LOG_DIR / "decisions"

PROMPT_TIMEOUT_SEC = int(
    os.environ.get(
        "ARBITEROS_DEFENDER_PROMPT_TIMEOUT_SEC",
        os.environ.get("DEFENDER_HOOK_PROMPT_TIMEOUT_SEC", "0"),
    )
)
POLL_INTERVAL_SEC = float(
    os.environ.get(
        "ARBITEROS_DEFENDER_POLL_INTERVAL_SEC",
        os.environ.get("DEFENDER_HOOK_POLL_INTERVAL_SEC", "0.25"),
    )
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)


def as_text(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str, indent=2)
        except Exception:
            text = str(value)
    if len(text) > limit:
        return text[:limit] + f"\n...<{len(text) - limit} more chars>"
    return text


def summarize_action(tool_name: str, tool_input: Any) -> str:
    name = tool_name or "unknown"
    if isinstance(tool_input, dict):
        if tool_input.get("command"):
            return f"{name}: {tool_input.get('command')}"
        if tool_input.get("cmd"):
            return f"{name}: {tool_input.get('cmd')}"
        if tool_input.get("patch"):
            return f"{name}: patch {as_text(tool_input.get('patch'), 500)}"
    return f"{name}: {as_text(tool_input, 800)}"


def new_request_id() -> str:
    return (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        f"-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )


def pending_path(request_id: str) -> Path:
    return PENDING_DIR / f"{request_id}.json"


def decision_path(request_id: str) -> Path:
    return DECISIONS_DIR / f"{request_id}.json"


def write_pending(request: dict[str, Any]) -> None:
    ensure_dirs()
    path = pending_path(str(request["request_id"]))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(request, ensure_ascii=False, default=str, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_decision(request_id: str, decision: str, reason: str) -> None:
    ensure_dirs()
    payload = {
        "request_id": request_id,
        "decision": decision,
        "reason": reason,
        "decided_at": now_iso(),
    }
    path = decision_path(request_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def cleanup_request(request_id: str) -> None:
    for path in (pending_path(request_id), decision_path(request_id)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def list_open_pending() -> list[dict[str, Any]]:
    ensure_dirs()
    rows: list[dict[str, Any]] = []
    for path in sorted(PENDING_DIR.glob("*.json")):
        req = read_json(path)
        if not req:
            continue
        rid = str(req.get("request_id") or path.stem)
        if decision_path(rid).exists():
            continue
        rows.append(req)
    rows.sort(key=lambda r: str(r.get("created_at") or ""))
    return rows
