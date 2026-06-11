"""Claude Code hook adapter.

The hook reads the JSON event payload from stdin and writes checkpoints through
the shared coordinator. It intentionally keeps Claude-specific logic at the
edge so storage remains provider-neutral.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any

from checkpoint_plugin.coordinator import CheckpointCoordinator, TurnRecord
from checkpoint_plugin.types import TrajectoryReference

from ._hook_common import empty_trajectory_ref as _empty_trajectory_ref
from ._hook_common import first_string as _first_string
from ._hook_common import parent_session_env as _parent_session_env
from ._hook_common import read_payload as _read_payload
from ._hook_common import recording_enabled as _recording_enabled
from ._trajectory_slicer import claude_key, jsonl_ref_for_turn

# F12: SubagentStop can fire before the subagent's final assistant deliverable is
# flushed to its sidechain file (verified: a50fb captured 3/4 records, the end_turn
# deliverable flushed moments later). Claude's docs give NO flush/durability guarantee
# for any hook, so this is unguaranteed by construction. Correctness comes from
# read-time tail recovery instead (every read path calls recover_trailing_tail /
# reanchor, and by read time the file is fully flushed). The settle below only
# front-loads completeness so an immediate read is already at EOF — a latency
# optimization, NOT a correctness mechanism. Read from the env at call time (not
# import) so tests can tune/disable it per-case.
_SETTLE_TIMEOUT_ENV = "CHECKPOINT_SIDECHAIN_SETTLE_TIMEOUT"
_SETTLE_POLL_ENV = "CHECKPOINT_SIDECHAIN_SETTLE_POLL"


def _settle_timeout_s() -> float:
    try:
        return float(os.environ.get(_SETTLE_TIMEOUT_ENV, "1.0"))
    except ValueError:
        return 1.0


def _settle_poll_s() -> float:
    try:
        return float(os.environ.get(_SETTLE_POLL_ENV, "0.1"))
    except ValueError:
        return 0.1


def main(argv: list[str] | None = None) -> int:
    if not _recording_enabled():
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("event", choices=["session_start", "turn_end", "subagent_end"])
    args = parser.parse_args(argv)
    payload = _read_payload()
    cwd = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or Path.cwd())
    parent_session_id = os.environ.get("CLAUDE_SESSION_ID") or str(payload.get("session_id") or "claude-session")
    _seed_claude_env(parent_session_id, payload)

    if args.event == "subagent_end":
        return _on_subagent_end(payload, cwd, parent_session_id)

    coordinator = CheckpointCoordinator(session_id=parent_session_id, cwd=cwd)

    if args.event == "session_start":
        coordinator.on_session_start(
            source=_first_string(payload, "source"),
            session_env=_session_env(payload),
            source_transcript_path=_first_string(payload, "transcript_path", "transcriptPath"),
        )
        return 0

    if not _is_stop_event(payload):
        return 0

    turn_record = _turn_record(payload)
    coordinator.on_turn_end(turn_record, _trajectory_ref(payload, provider="claude") or _empty_trajectory_ref("claude"))
    return 0


def _on_subagent_end(payload: dict[str, Any], cwd: Path, parent_session_id: str) -> int:
    """Checkpoint a finished subagent as its own session (B4).

    Claude writes each subagent to a separate transcript
    `<parent>/subagents/agent-<agentId>.jsonl` with its own sessionId/promptId,
    so a subagent turn is recorded under a derived plugin session keyed by the
    agent id. The parent timeline is left untouched; lineage is kept in metadata.
    """
    agent_id = _first_string(payload, "agent_id", "agentId")
    transcript_path = _subagent_transcript_path(payload, agent_id)
    if agent_id is None and transcript_path is None:
        return 0  # Not enough to attribute this subagent; skip rather than guess.
    agent_type = _first_string(payload, "agent_type", "agentType")
    # SUB2: a resume/fork replays the parent's inherited `SubagentStop` events for
    # subagents that ran in the ORIGINAL session. Those replayed events carry an
    # `agent_id` but no sidechain file (it lives under the original session) AND no
    # `agent_type` — observed: phantom shells a2b78a8b / a0a18620 on a forked 3f913f6c
    # had agent_type=None and no file, while every genuine Task-spawned subagent
    # (a4f9c5d9 / afcd1cf3) carried agent_type="general-purpose". Without a file there
    # is nothing to checkpoint, so a typeless, fileless SubagentStop would only write a
    # noise `no_sidechain_file` shell for a subagent that isn't ours. Skip it.
    # We can't instead confirm a genuine fileless subagent via the parent manifest:
    # SubagentStop fires while the parent turn is still open, so the parent manifest
    # that references this agent_id is not written until ~tens of seconds later (its
    # turn ends after the child stops) — a capture-time parent lookup would wrongly
    # drop real in-turn subagents. `agent_type` IS present at capture time, so it is
    # the safe discriminator. A genuine fileless subagent still carries an agent_type
    # and is recorded as before.
    if transcript_path is None and agent_id is not None and agent_type is None:
        return 0
    sub_session_id = f"{parent_session_id}--subagent-{agent_id or _stem(transcript_path)}"
    coordinator = CheckpointCoordinator(session_id=sub_session_id, cwd=cwd)
    # SubagentStop omits `model` (SessionStart-only); inherit the parent's pinned
    # session_env so the subagent checkpoint records the same model/effort (G2).
    sub_env = {**_parent_session_env(parent_session_id), **_session_env(payload)}
    # P6-6: persist whatever durable spawn link SubagentStop provides — the
    # agent_id (primary match via _manifest_references_agent) plus the sidechain
    # filename stem, which survives even when agent_id is absent from any slice.
    lineage: dict[str, Any] = {
        "parent_session_id": parent_session_id,
        "agent_id": agent_id,
        "agent_type": agent_type,
    }
    if transcript_path is not None:
        lineage["sidechain_stem"] = transcript_path.stem
    # F12: block briefly for the subagent's final assistant record to flush before
    # slicing, so the deliverable isn't truncated. Bounded and best-effort.
    if transcript_path is not None:
        _settle_sidechain(transcript_path)
    ref = _subagent_trajectory_ref(payload, transcript_path)
    if ref is None:
        # P11-ZOMBIE-1: no sidechain file → record lineage-only metadata (no fs/env
        # snapshot). A full on_turn_end would snapshot the entire project directory
        # into blobs that serve no purpose — there's no trajectory to associate them
        # with. Write metadata so the session is discoverable, but skip the expensive
        # checkpoint.
        # SA4: record timestamp and reason for missing sidechain file
        lineage["capture_status"] = "no_sidechain_file"
        lineage["no_sidechain_file_timestamp"] = time.time()
        if transcript_path is None:
            lineage["no_sidechain_file_reason"] = "no_transcript_path"
        elif not transcript_path.exists():
            lineage["no_sidechain_file_reason"] = "file_not_found"
        else:
            lineage["no_sidechain_file_reason"] = "file_empty_or_unreadable"
        coordinator.on_session_start(
            source="subagent",
            session_env=sub_env,
            lineage=lineage,
        )
        return 0
    if transcript_path is not None:
        # Record the sidechain's observed size+mtime at slice time. After the settle
        # above the slice should reach EOF, but a still-growing file (extremely late
        # flush past the settle budget) remains detectable: the file having grown
        # past `sidechain_observed_size` means records were missed.
        observed = _sidechain_observed_state(transcript_path, ref.end_offset)
        if observed is not None:
            lineage["sidechain_observed_size"], lineage["sidechain_observed_mtime"] = observed
    coordinator.on_session_start(
        source="subagent",
        session_env=sub_env,
        lineage=lineage,
    )
    coordinator.on_turn_end(_turn_record(payload), ref)
    return 0


def _seed_claude_env(session_id: str, payload: dict[str, Any]) -> None:
    os.environ["CHECKPOINT_PROVIDER"] = "claude"
    os.environ.setdefault("CLAUDE_SESSION_ID", session_id)
    model = _first_string(payload, "model")
    if model:
        os.environ.setdefault("ANTHROPIC_MODEL", model)
    permission_mode = _first_string(payload, "permission_mode", "permissionMode")
    if permission_mode:
        os.environ.setdefault("CLAUDE_PERMISSION_MODE", permission_mode)
    mode = _first_string(payload, "mode")
    if mode:
        os.environ.setdefault("CLAUDE_MODE", mode)
    effort = _effort_level(payload)
    if effort:
        os.environ.setdefault("CLAUDE_EFFORT", effort)
    agent_type = _first_string(payload, "agent_type", "agentType")
    if agent_type:
        os.environ.setdefault("CLAUDE_AGENT_TYPE", agent_type)
    agent_id = _first_string(payload, "agent_id", "agentId")
    if agent_id:
        os.environ.setdefault("CLAUDE_AGENT_ID", agent_id)


def _effort_level(payload: dict[str, Any]) -> str | None:
    effort = payload.get("effort")
    if isinstance(effort, dict):
        level = effort.get("level")
        if isinstance(level, str):
            return level
    if isinstance(effort, str):
        return effort
    return None


def _session_env(payload: dict[str, Any]) -> dict[str, str]:
    """Provider fields delivered at SessionStart but not at Stop (e.g. model).

    SA2: capture mode field for plan mode support.
    """
    fields = {
        "model": _first_string(payload, "model"),
        "permission_mode": _first_string(payload, "permission_mode", "permissionMode"),
        "mode": _first_string(payload, "mode"),
        "effort": _effort_level(payload),
        "agent_type": _first_string(payload, "agent_type", "agentType"),
    }
    return {key: value for key, value in fields.items() if value}


def _turn_record(payload: dict[str, Any]) -> TurnRecord:
    user_message = _first_string(payload, "prompt", "user_message", "userMessage", "input")
    assistant_text = _first_string(payload, "assistant_text", "assistantText", "response", "output")
    tool_calls = payload.get("tool_calls") or payload.get("toolCalls") or []
    if not isinstance(tool_calls, list):
        tool_calls = [tool_calls]
    return TurnRecord(
        user_message=user_message or "",
        assistant_text=assistant_text or "",
        tool_calls=tool_calls,
        metadata={"hook_payload": payload},
    )


def _is_stop_event(payload: dict[str, Any]) -> bool:
    return _first_string(payload, "hook_event_name", "hookEventName") == "Stop"


def _trajectory_ref(payload: dict[str, Any], provider: str) -> TrajectoryReference | None:
    transcript_path = _first_string(payload, "transcript_path", "transcriptPath")
    if transcript_path is None:
        return None
    turn_id = payload.get("turn_id") or payload.get("turnId")
    return jsonl_ref_for_turn(provider, Path(transcript_path), turn_id, claude_key)


def _subagent_transcript_path(payload: dict[str, Any], agent_id: str | None) -> Path | None:
    """Resolve the subagent's OWN transcript file (never the parent's).

    Claude writes a subagent to a dedicated sidechain file
    `<parent-dir>/<session>/subagents/agent-<id>.jsonl` (or a flat sibling). The
    Stop payload's `transcript_path`, however, is often the PARENT main
    transcript. Slicing that would mislabel the parent's own thread as the
    subagent's work (F4), so we only ever return a path that is genuinely a
    subagent transcript; if we can't find one we return None and the caller
    records lineage without a (misleading) trajectory slice.
    """
    explicit = _first_string(payload, "transcript_path", "transcriptPath")
    parent = _first_string(payload, "parent_transcript_path", "parentTranscriptPath")
    if explicit:
        path = Path(explicit)
        if _is_subagent_transcript(path):
            return path
        # `explicit` is the parent main transcript: locate the real sidechain.
        nested = _existing_nested_subagent(path, agent_id)
        return nested  # None if no dedicated subagent file exists (don't slice parent).
    if parent and agent_id:
        return _existing_nested_subagent(Path(parent), agent_id)
    return None


def _is_subagent_transcript(path: Path) -> bool:
    """A subagent transcript lives in a `subagents/` dir or is named `agent-*`."""
    return path.parent.name == "subagents" or path.stem.startswith("agent-")


def _existing_nested_subagent(parent_transcript: Path, agent_id: str | None) -> Path | None:
    """First existing `subagents/agent-<id>.jsonl` derived from a parent path."""
    if not agent_id:
        return None
    candidates = [
        parent_transcript.parent / parent_transcript.stem / "subagents" / f"agent-{agent_id}.jsonl",
        parent_transcript.parent / "subagents" / f"agent-{agent_id}.jsonl",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _subagent_trajectory_ref(payload: dict[str, Any], transcript_path: Path | None) -> TrajectoryReference | None:
    if transcript_path is None:
        return None
    turn_id = payload.get("turn_id") or payload.get("turnId")
    return jsonl_ref_for_turn("claude", transcript_path, turn_id, claude_key)


def _settle_sidechain(transcript_path: Path) -> None:
    """Block (bounded) for the subagent's final deliverable to flush (F12).

    SubagentStop fires when the subagent finishes, but Claude flushes the closing
    assistant record to the sidechain file moments later. We poll until the file
    looks complete — the last JSONL record is an assistant message with a
    `stop_reason` (the deliverable) — or the timeout elapses. We do NOT bail on a
    "stable size": while we await the delayed flush the file is stable precisely
    BECAUSE the deliverable hasn't landed yet, so a stable-size bail would give up
    exactly when we must keep waiting. Best-effort: a missing file returns at once.
    """
    if _settle_timeout_s() <= 0:
        return
    deadline = time.monotonic() + _settle_timeout_s()
    poll = _settle_poll_s()
    while True:
        if _sidechain_tail_is_complete(transcript_path):
            return
        if not transcript_path.exists():
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(poll)


def _sidechain_tail_is_complete(transcript_path: Path) -> bool:
    """True when the sidechain's last record is a finished assistant deliverable.

    A complete subagent transcript ends with an assistant message carrying a
    `stop_reason` (e.g. `end_turn`). Reads only the final non-blank line.
    """
    import json

    try:
        data = transcript_path.read_bytes()
    except OSError:
        return False
    if not data.endswith(b"\n"):
        return False  # mid-flush write
    for line in reversed(data.splitlines()):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(record, dict):
            return False
        if record.get("type") != "assistant":
            return False
        message = record.get("message")
        return isinstance(message, dict) and message.get("stop_reason") is not None
    return False


def _stem(path: Path | None) -> str:
    return path.stem if path is not None else "unknown"


def _sidechain_observed_state(transcript_path: Path, sliced_end_offset: int) -> tuple[int, str] | None:
    """The sidechain file's (size, mtime-iso) at capture time, for staleness checks (F12).

    A later flush grows the file beyond the captured slice's end; recording the size
    we sliced against lets a reader tell whether the stored slice still covers the
    whole file. Returns None if the file can't be stat'd. We record the size we
    actually sliced to (`sliced_end_offset`) so a future re-stat that returns a larger
    size is an unambiguous staleness signal.
    """
    try:
        stat = transcript_path.stat()
    except OSError:
        return None
    from datetime import datetime, timezone

    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    return sliced_end_offset, mtime


if __name__ == "__main__":
    raise SystemExit(main())
