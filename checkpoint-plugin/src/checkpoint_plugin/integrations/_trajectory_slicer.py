"""Shared JSONL transcript slicer for hook adapters.

Slices a provider transcript into a byte range that covers the current turn.
Each provider supplies a `key_extractor` returning a per-turn key; records
without a key are attributed to the most recent keyed record above them.
Turn 0 always anchors at byte 0 so leading provider-emitted records (mode,
permission-mode, file-history snapshots) are never lost.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from checkpoint_plugin.types import TrajectoryReference

KeyExtractor = Callable[[dict[str, Any]], Any]


def claude_key(record: dict[str, Any]) -> Any:
    """promptId is the only stable per-turn key Claude transcripts carry."""
    return record.get("promptId")


def codex_key(record: dict[str, Any]) -> Any:
    if "turn_id" in record:
        return record["turn_id"]
    if "turnId" in record:
        return record["turnId"]
    payload = record.get("payload")
    if isinstance(payload, dict):
        return payload.get("turn_id") or payload.get("turnId")
    return None


def jsonl_ref_for_turn(
    provider: str,
    path: Path,
    turn_id: Any,
    key_extractor: KeyExtractor,
    *,
    claim_leading_keyless: bool = False,
) -> TrajectoryReference | None:
    """Slice the transcript into the byte range covering one turn.

    `claim_leading_keyless` (Codex): the per-turn key (`turn_id`) only appears on
    a turn's `turn_context`/`task_*` records, never on the user prompt or other
    `response_item` content, and the prompt is emitted *before* `task_started`.
    Anchoring boundaries at the end of the last keyed record before each
    reference point pulls those leading key-less records into the turn they
    belong to. Claude does not need this: its `promptId` is carried on the first
    (`user`) record of the turn.
    """
    try:
        data = path.expanduser().read_bytes()
    except OSError:
        return None

    lines = _parse_jsonl_lines(data)
    if not lines:
        return None

    keyed = [(start, end, key_extractor(record) if isinstance(record, dict) else None) for start, end, record in lines]

    if turn_id is not None:
        match = _slice_for_turn_id(keyed, turn_id, len(data), claim_leading_keyless)
        if match is not None:
            start, end = match
            return _build_ref(provider, path, data, start, end)

    latest_key = _latest_distinct_key(keyed)
    if latest_key is None:
        return _build_ref(provider, path, data, 0, len(data))

    if claim_leading_keyless:
        first_start = _first_offset_for_key(keyed, latest_key, len(data))
        start_offset = _last_keyed_end_before(keyed, first_start)
        if start_offset is None or _no_prior_keys(keyed, latest_key):
            return _build_ref(provider, path, data, 0, len(data))
        return _build_ref(provider, path, data, start_offset, len(data))

    start_offset = _first_offset_for_key(keyed, latest_key, len(data))
    if start_offset == 0 or _no_prior_keys(keyed, latest_key):
        return _build_ref(provider, path, data, 0, len(data))
    return _build_ref(provider, path, data, start_offset, len(data))


def jsonl_count_records(data: bytes) -> int:
    return sum(1 for line in data.splitlines() if line.strip())


def jsonl_after_leading_metas(
    provider: str,
    path: Path,
    *,
    is_leading_meta: KeyExtractor,
) -> TrajectoryReference | None:
    """Slice from the end of the leading meta block to EOF (H4).

    A subagent's dedicated rollout begins with a run of inherited ancestor
    `session_meta` records (e.g. subagent<-parent<-grandparent), then the
    subagent's OWN turns. Slicing on the SubagentStop turn_id captured only the
    LAST turn, dropping the subagent's earlier own turns. We instead capture the
    subagent's full own conversation: everything after the leading meta block.

    Consistent with resume: `_inherited_fork_prefix` reads `[0:start_offset]`, so
    the inherited metas are still reproduced (then collapsed to one by H1). Zero
    leading metas -> start=0 (whole file), a safe fallback.

    The slice spans MANY turns, so its closing record (codex `task_complete`)
    carries the LAST turn's key, not the first. Tag it `session_boundary` so
    read-time tail recovery accepts that trailing record instead of rejecting it
    as a "different turn" (the bug a per-turn-key guard hits on multi-turn slices).
    """
    try:
        data = path.expanduser().read_bytes()
    except OSError:
        return None
    lines = _parse_jsonl_lines(data)
    if not lines:
        return None
    start = 0
    for line_start, line_end, record in lines:
        if isinstance(record, dict) and is_leading_meta(record):
            start = line_end
            continue
        break
    return _build_ref(provider, path, data, start, len(data), boundary_mode="session_boundary")


def recover_trailing_tail(ref: TrajectoryReference) -> bytes:
    """Bytes flushed after `ref.end_offset` that still belong to this slice.

    A provider can flush a turn-closing record (codex `task_complete`, claude's
    final assistant deliverable) moments after the Stop/SubagentStop hook reads
    the file, leaving the captured slice short of EOF. This recovers that tail at
    read time so resume and `show`/`diff` see the complete slice. All-or-nothing:
    the candidate tail must end on a newline (no truncated JSON line); if any
    record violates the boundary guard we return b"" rather than a partial tail.

    The guard depends on `ref.boundary_mode`:
      - "per_turn_key": single-turn slices. Reject when a tail record carries a
        per-turn key distinct from the slice's anchor key (a new turn started).
      - "session_boundary": multi-turn subagent slices. The closing record
        legitimately carries the LAST turn's key, so a per-turn-key guard would
        wrongly reject it; instead reject only on a `session_meta` (a new
        session, which never appends to a per-agent subagent rollout).
    """
    if not ref.transcript_path:
        return b""
    path = Path(ref.transcript_path).expanduser()
    try:
        size = path.stat().st_size
    except OSError:
        return b""
    if size <= ref.end_offset:
        return b""
    try:
        with path.open("rb") as handle:
            handle.seek(ref.end_offset)
            tail = handle.read(size - ref.end_offset)
    except OSError:
        return b""
    if not tail.endswith(b"\n"):
        return b""
    if ref.boundary_mode == "session_boundary":
        return tail if _tail_within_session(tail) else b""
    extractor = _key_extractor_for(ref.provider)
    if extractor is None:
        return tail
    anchor = _anchor_key(ref, extractor)
    for line in tail.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        key = extractor(record)
        if key is not None and key != anchor:
            return b""
    return tail


def _tail_within_session(tail: bytes) -> bool:
    """True unless the tail opens a NEW session (a `session_meta` record).

    Subagent rollouts are per-agent files that are never reused, so in practice
    the tail is just the last turn's closing records. A `session_meta` would mean
    a different session's bytes — refuse the whole tail then.
    """
    for line in tail.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("type") == "session_meta":
            return False
    return True


def _key_extractor_for(provider: str) -> KeyExtractor | None:
    if provider == "claude":
        return claude_key
    if provider in ("codex", "opencode"):
        return codex_key
    return None


def _anchor_key(ref: TrajectoryReference, extractor: KeyExtractor) -> Any:
    """First per-turn key inside the slice — the key the tail must match."""
    path = Path(ref.transcript_path).expanduser()
    try:
        with path.open("rb") as handle:
            handle.seek(ref.start_offset)
            data = handle.read(ref.end_offset - ref.start_offset)
    except OSError:
        return None
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            key = extractor(record)
            if key is not None:
                return key
    return None


def _parse_jsonl_lines(data: bytes) -> list[tuple[int, int, Any]]:
    lines: list[tuple[int, int, Any]] = []
    offset = 0
    for line in data.splitlines(keepends=True):
        end = offset + len(line)
        if line.strip():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                record = None
            lines.append((offset, end, record))
        offset = end
    return lines


def _build_ref(
    provider: str,
    path: Path,
    data: bytes,
    start: int,
    end: int,
    *,
    boundary_mode: str = "per_turn_key",
) -> TrajectoryReference:
    return TrajectoryReference(
        provider=provider,
        transcript_path=str(path.expanduser().resolve()),
        start_offset=start,
        end_offset=end,
        record_count=jsonl_count_records(data[start:end]),
        boundary_mode=boundary_mode,
    )


def _slice_for_turn_id(
    keyed: list[tuple[int, int, Any]],
    turn_id: Any,
    file_size: int,
    claim_leading_keyless: bool = False,
) -> tuple[int, int] | None:
    matches = [(start, end) for start, end, key in keyed if _keys_match(key, turn_id)]
    if not matches:
        return None
    keyed_start = matches[0][0]
    next_start = _next_distinct_key_offset(keyed, keyed_start, turn_id)
    if claim_leading_keyless:
        start_offset = _last_keyed_end_before(keyed, keyed_start)
        start_offset = 0 if start_offset is None else start_offset
        if next_start is None:
            return start_offset, file_size
        # End where the *next* turn's claimed region begins: the end of this
        # turn's last keyed record. Key-less records between here and the next
        # turn's first keyed record are that turn's leading prompt.
        prior_end = _last_keyed_end_before(keyed, next_start)
        return start_offset, prior_end if prior_end is not None else next_start
    end_offset = next_start if next_start is not None else file_size
    if keyed_start == 0 or _no_prior_keys_before(keyed, keyed_start):
        return 0, end_offset
    return keyed_start, end_offset


def _last_keyed_end_before(keyed: list[tuple[int, int, Any]], offset: int) -> int | None:
    """End byte of the last keyed record strictly before `offset`.

    None means no keyed record precedes `offset` (the turn is the first one, so
    its leading key-less records start at byte 0).
    """
    result: int | None = None
    for start, end, key in keyed:
        if start >= offset:
            break
        if key is not None:
            result = end
    return result


def _next_distinct_key_offset(
    keyed: list[tuple[int, int, Any]],
    start_offset: int,
    turn_id: Any,
) -> int | None:
    for line_start, _, key in keyed:
        if line_start > start_offset and key is not None and not _keys_match(key, turn_id):
            return line_start
    return None


def _latest_distinct_key(keyed: list[tuple[int, int, Any]]) -> Any:
    for _, _, key in reversed(keyed):
        if key is not None:
            return key
    return None


def _first_offset_for_key(
    keyed: list[tuple[int, int, Any]],
    target: Any,
    fallback: int,
) -> int:
    for start, _, key in keyed:
        if key is not None and _keys_match(key, target):
            return start
    return fallback


def _no_prior_keys(keyed: list[tuple[int, int, Any]], target: Any) -> bool:
    """True if `target` is the only distinct key in the transcript."""
    for _, _, key in keyed:
        if key is not None and not _keys_match(key, target):
            return False
    return True


def _no_prior_keys_before(keyed: list[tuple[int, int, Any]], offset: int) -> bool:
    for start, _, key in keyed:
        if start >= offset:
            return True
        if key is not None:
            return False
    return True


def _keys_match(left: Any, right: Any) -> bool:
    return left == right or (left is not None and right is not None and str(left) == str(right))
