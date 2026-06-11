"""Helpers shared by the Codex and Claude hook adapters.

These are provider-neutral: stdin payload parsing, first-string extraction, and
the empty trajectory reference used when a Stop event carries no usable
transcript. Keeping them in one place avoids drift between the two adapters.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from checkpoint_plugin.paths import load_config, session_dir
from checkpoint_plugin.types import TrajectoryReference


def recording_enabled() -> bool:
    """True when checkpoint capture is allowed (``config.json`` ``enabled``)."""
    try:
        return bool(load_config().get("enabled", True))
    except Exception:
        return True


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return value if isinstance(value, dict) else {"payload": value}


def first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def parent_session_env(parent_session_id: str) -> dict[str, str]:
    """Read the parent session's recorded session_env (model, effort, ...).

    Subagent Stop payloads omit fields that only arrive at the parent's
    SessionStart (notably Claude's `model`). The parent persisted them to its
    metadata.json at session start, so a subagent checkpoint inherits them rather
    than pinning model=None.
    """
    metadata_path = session_dir(parent_session_id) / "metadata.json"
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    session_env = data.get("session_env") if isinstance(data, dict) else None
    if not isinstance(session_env, dict):
        return {}
    return {str(key): str(value) for key, value in session_env.items() if value}


def empty_trajectory_ref(provider: str) -> TrajectoryReference:
    return TrajectoryReference(
        provider=provider,
        transcript_path="",
        start_offset=0,
        end_offset=0,
        record_count=0,
    )
