"""Tests for read-time tail recovery in the shared trajectory slicer.

`recover_trailing_tail` chases bytes a provider flushed after the Stop hook read
the file. The guard it applies depends on `TrajectoryReference.boundary_mode`:
single-turn slices (`per_turn_key`) reject a tail bearing a different per-turn
key; multi-turn subagent slices (`session_boundary`) accept the tail up to the
next `session_meta`, because their closing record legitimately carries the LAST
turn's key.
"""

import json
from pathlib import Path

from checkpoint_plugin.integrations._trajectory_slicer import (
    jsonl_after_leading_metas,
    recover_trailing_tail,
)
from checkpoint_plugin.types import TrajectoryReference


def _codex_meta(meta_id: str, forked_from: str | None = None) -> str:
    payload = {"id": meta_id}
    if forked_from is not None:
        payload["forked_from_id"] = forked_from
    return json.dumps({"type": "session_meta", "payload": payload}) + "\n"


def _codex_turn(turn_id: str, payload_type: str = "message") -> str:
    return json.dumps({"type": "response_item", "turn_id": turn_id, "payload": {"type": payload_type}}) + "\n"


def _codex_task_complete(turn_id: str) -> str:
    return json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}, "turn_id": turn_id}) + "\n"


def test_session_boundary_recovers_trailing_task_complete(tmp_path):
    """A codex subagent slice spans many turns; the trailing `task_complete`
    carries the LAST turn's id, not the slice's first. session_boundary accepts
    it (the per_turn_key guard would wrongly reject it)."""
    rollout = tmp_path / "rollout.jsonl"
    body = _codex_meta("agent") + _codex_turn("t1") + _codex_turn("t2") + _codex_turn("t3")
    rollout.write_text(body, encoding="utf-8")
    end = len(body.encode("utf-8"))
    # The closing record flushes after capture, bearing the LAST turn's id (t3).
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(_codex_task_complete("t3"))
    grown = rollout.stat().st_size

    ref = TrajectoryReference("codex", str(rollout), len(_codex_meta("agent").encode()), end, 3, boundary_mode="session_boundary")
    tail = recover_trailing_tail(ref)
    assert len(tail) == grown - end
    assert json.loads(tail.splitlines()[-1])["payload"]["type"] == "task_complete"


def test_session_boundary_stops_at_new_session_meta(tmp_path):
    """session_boundary refuses a tail that opens a NEW session (`session_meta`):
    those bytes belong to a different session, never the captured slice."""
    rollout = tmp_path / "rollout.jsonl"
    body = _codex_meta("agent") + _codex_turn("t1")
    rollout.write_text(body, encoding="utf-8")
    end = len(body.encode("utf-8"))
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(_codex_meta("other"))

    ref = TrajectoryReference("codex", str(rollout), 0, end, 2, boundary_mode="session_boundary")
    assert recover_trailing_tail(ref) == b""


def test_session_boundary_refuses_mid_flush_partial_line(tmp_path):
    """A tail without a closing newline is a mid-flush write — refuse all of it
    rather than parse a truncated JSON record."""
    rollout = tmp_path / "rollout.jsonl"
    body = _codex_meta("agent") + _codex_turn("t1")
    rollout.write_text(body, encoding="utf-8")
    end = len(body.encode("utf-8"))
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"event_msg","payload":{')  # truncated, no newline

    ref = TrajectoryReference("codex", str(rollout), 0, end, 2, boundary_mode="session_boundary")
    assert recover_trailing_tail(ref) == b""


def test_per_turn_key_rejects_a_different_turn_tail(tmp_path):
    """Default (single-turn) mode: a trailing record with a distinct per-turn key
    is a new turn and must not be absorbed."""
    rollout = tmp_path / "rollout.jsonl"
    body = _codex_turn("t1") + _codex_turn("t1")
    rollout.write_text(body, encoding="utf-8")
    end = len(body.encode("utf-8"))
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(_codex_turn("t2"))  # a new turn

    ref = TrajectoryReference("codex", str(rollout), 0, end, 2)  # per_turn_key default
    assert recover_trailing_tail(ref) == b""


def test_per_turn_key_accepts_same_turn_tail(tmp_path):
    """Default mode still recovers a trailing record that shares the anchor key."""
    rollout = tmp_path / "rollout.jsonl"
    body = _codex_turn("t1")
    rollout.write_text(body, encoding="utf-8")
    end = len(body.encode("utf-8"))
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(_codex_task_complete("t1"))  # same turn, closes it
    grown = rollout.stat().st_size

    ref = TrajectoryReference("codex", str(rollout), 0, end, 1)
    assert len(recover_trailing_tail(ref)) == grown - end


def test_recover_tail_missing_or_truncated_file_returns_empty(tmp_path):
    """No file, or a file shorter than end_offset (truncated/rotated), recovers
    nothing rather than raising."""
    missing = TrajectoryReference("codex", str(tmp_path / "gone.jsonl"), 0, 100, 1, boundary_mode="session_boundary")
    assert recover_trailing_tail(missing) == b""

    rollout = tmp_path / "short.jsonl"
    rollout.write_text(_codex_turn("t1"), encoding="utf-8")
    shrunk = TrajectoryReference("codex", str(rollout), 0, 10_000, 1, boundary_mode="session_boundary")
    assert recover_trailing_tail(shrunk) == b""

    empty_path = TrajectoryReference("codex", "", 0, 0, 0)
    assert recover_trailing_tail(empty_path) == b""


def test_jsonl_after_leading_metas_tags_session_boundary(tmp_path):
    """The subagent slicer stamps session_boundary so read-time recovery uses the
    right guard; the slice still begins after the leading inherited meta block."""
    rollout = tmp_path / "rollout.jsonl"
    leading = _codex_meta("sub", "parent") + _codex_meta("parent")
    body = leading + _codex_turn("t1") + _codex_turn("t2")
    rollout.write_text(body, encoding="utf-8")

    ref = jsonl_after_leading_metas("codex", rollout, is_leading_meta=lambda r: r.get("type") == "session_meta")
    assert ref is not None
    assert ref.boundary_mode == "session_boundary"
    assert ref.start_offset == len(leading.encode("utf-8"))
    assert ref.end_offset == rollout.stat().st_size


def test_trajectory_reference_boundary_mode_roundtrips():
    """The field serializes via to_json and survives from_json; legacy manifests
    without it default to per_turn_key."""
    ref = TrajectoryReference("codex", "/x.jsonl", 5, 99, 3, boundary_mode="session_boundary")
    assert ref.to_json()["boundary_mode"] == "session_boundary"
    assert TrajectoryReference.from_json(ref.to_json()).boundary_mode == "session_boundary"

    legacy = {"provider": "codex", "transcript_path": "/x.jsonl", "start_offset": 0, "end_offset": 1, "record_count": 1}
    assert TrajectoryReference.from_json(legacy).boundary_mode == "per_turn_key"
