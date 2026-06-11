import io
import json
import os
import hashlib

from checkpoint_plugin.cli import (
    main,
    _colorize,
    _edit_send_replaced_turns,
    _rolled_back_count,
    _supports_color,
)
from checkpoint_plugin.store import CheckpointStore
from checkpoint_plugin.types import CheckpointManifest, TrajectoryReference
import checkpoint_plugin.ui.session_browser as session_browser
from checkpoint_plugin.ui.session_browser import (
    _body_fragments,
    _command_action,
    _detail_fragments,
    _header_fragments,
    _output_fragments,
    _output_page_size,
    _resume_hint,
    _rows_for_nodes,
    collect_provider_trees,
    render_session_tree,
)


def test_list_sessions_shows_title_and_source(tmp_path, monkeypatch, capsys):
    home = tmp_path / "plugin"
    session = home / "sessions" / "s1"
    session.mkdir(parents=True)
    (session / "metadata.json").write_text(
        json.dumps(
            {
                "session_id": "s1",
                "session_title": "Respond to greeting",
                "source": "startup",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))

    assert main(["list", "--all"]) == 0

    assert capsys.readouterr().out == "s1  Respond to greeting  startup\n"


def test_list_sessions_handles_missing_metadata(tmp_path, monkeypatch, capsys):
    home = tmp_path / "plugin"
    (home / "sessions" / "s1").mkdir(parents=True)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))

    assert main(["list", "--all"]) == 0

    assert capsys.readouterr().out == "s1  -  -\n"


class _Stream(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_colorize_wraps_on_tty_and_is_plain_otherwise(monkeypatch):
    """The resume-command hint is colored only when stdout is a real TTY."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    cmd = "checkpoint resume-open 4adbaa3b-f00a-4882-8dd8-0f6184650a60"

    colored = _colorize(cmd, "bold green", stream=_Stream(tty=True))
    assert colored == f"\033[1m\033[32m{cmd}\033[0m"
    # The raw command is still present (selectable/copyable) inside the escapes.
    assert cmd in colored

    # Non-TTY (piped/redirected) gets no escape codes.
    assert _colorize(cmd, "bold green", stream=_Stream(tty=False)) == cmd


def test_colorize_respects_no_color_env(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    cmd = "checkpoint resume-open abc"
    assert _colorize(cmd, "bold green", stream=_Stream(tty=True)) == cmd
    assert _supports_color(_Stream(tty=True)) is False


def test_clean_blobs_compacts_legacy_session_blobs(tmp_path, monkeypatch, capsys):
    home = tmp_path / "plugin"
    store = CheckpointStore(home / "sessions" / "s1")
    sha = hashlib.sha256(b"legacy").hexdigest()
    legacy_path = store.legacy_blob_path(sha)
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"legacy")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))

    assert main(["clean", "--blobs", "--dry-run"]) == 0
    assert "Would compact 1 legacy blob(s); promoted 1; missing 0" in capsys.readouterr().out
    assert legacy_path.exists()
    assert not store.blob_path(sha).exists()

    assert main(["clean", "--blobs"]) == 0
    assert "Compacted 1 legacy blob(s); promoted 1; missing 0" in capsys.readouterr().out
    assert not legacy_path.exists()
    assert store.blob_path(sha).read_bytes() == b"legacy"


def test_clean_blobs_keeps_legacy_blob_when_promotion_hash_mismatches(tmp_path, monkeypatch, capsys):
    home = tmp_path / "plugin"
    store = CheckpointStore(home / "sessions" / "s1")
    sha = hashlib.sha256(b"expected").hexdigest()
    legacy_path = store.legacy_blob_path(sha)
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"wrong")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))

    assert main(["clean", "--blobs"]) == 0

    assert "Compacted 0 legacy blob(s); promoted 0; missing 1" in capsys.readouterr().out
    assert legacy_path.read_bytes() == b"wrong"
    assert not store.blob_path(sha).exists()


def test_clean_blobs_dry_run_reports_hash_mismatch_without_removal(tmp_path, monkeypatch, capsys):
    home = tmp_path / "plugin"
    store = CheckpointStore(home / "sessions" / "s1")
    sha = hashlib.sha256(b"expected").hexdigest()
    legacy_path = store.legacy_blob_path(sha)
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"wrong")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))

    assert main(["clean", "--blobs", "--dry-run"]) == 0

    assert "Would compact 0 legacy blob(s); promoted 0; missing 1" in capsys.readouterr().out
    assert legacy_path.read_bytes() == b"wrong"
    assert not store.blob_path(sha).exists()


def test_clean_blobs_reports_missing_reachable_refs(tmp_path, monkeypatch, capsys):
    home = tmp_path / "plugin"
    store = CheckpointStore(home / "sessions" / "s1")
    env_ref = hashlib.sha256(b"missing-env").hexdigest()
    fs_ref = hashlib.sha256(b"missing-fs").hexdigest()
    fork_ref = hashlib.sha256(b"missing-fork").hexdigest()
    store.write_manifest(
        CheckpointManifest(
            turn_id=1,
            session_id="s1",
            created_ts="2026-06-09T00:00:00Z",
            env_ref=env_ref,
            fs_ref=fs_ref,
        )
    )
    (store.session_dir / "metadata.json").write_text(
        json.dumps({"fork_point_trajectory_ref": fork_ref}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))

    assert main(["clean", "--blobs", "--dry-run"]) == 0

    assert "Would compact 0 legacy blob(s); promoted 0; missing 3" in capsys.readouterr().out


def test_clean_empty_prunes_only_unreferenced_global_blobs(tmp_path, monkeypatch, capsys):
    home = tmp_path / "plugin"
    empty_store = CheckpointStore(home / "sessions" / "empty")
    kept_store = CheckpointStore(home / "sessions" / "kept")
    shared_ref = empty_store.store_blob(b"shared")
    unique_ref = empty_store.store_blob(b"unique")
    empty_env = empty_store.store_json_blob({"provider": "codex"})
    empty_fs = empty_store.store_json_blob(
        {
            "cwd": str(tmp_path / "empty-work"),
            "files": {"shared.txt": shared_ref, "unique.txt": unique_ref},
            "git": None,
        }
    )
    kept_env = kept_store.store_json_blob({"provider": "codex"})
    kept_fs = kept_store.store_json_blob(
        {
            "cwd": str(tmp_path / "kept-work"),
            "files": {"shared.txt": shared_ref},
            "git": None,
        }
    )
    empty_store.write_manifest(
        CheckpointManifest(
            turn_id=0,
            session_id="empty",
            created_ts="2026-06-09T00:00:00Z",
            env_ref=empty_env,
            fs_ref=empty_fs,
            trajectory_ref=TrajectoryReference("codex", "", 0, 0, 0),
        )
    )
    kept_store.write_manifest(
        CheckpointManifest(
            turn_id=0,
            session_id="kept",
            created_ts="2026-06-09T00:00:00Z",
            env_ref=kept_env,
            fs_ref=kept_fs,
            trajectory_ref=TrajectoryReference("codex", "transcript.jsonl", 0, 1, 1),
        )
    )
    (empty_store.session_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (kept_store.session_dir / "metadata.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))

    assert main(["clean", "--empty"]) == 0

    output = capsys.readouterr().out
    assert "Removed 1 empty session(s)" in output
    assert not empty_store.session_dir.exists()
    assert kept_store.session_dir.exists()
    assert not empty_store.blob_path(unique_ref).exists()
    assert not empty_store.blob_path(empty_fs).exists()
    assert kept_store.blob_path(shared_ref).read_bytes() == b"shared"
    assert kept_store.blob_path(kept_fs).exists()


def test_clean_empty_ignores_malformed_blob_refs_when_pruning(tmp_path, monkeypatch, capsys):
    home = tmp_path / "plugin"
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me\n", encoding="utf-8")
    store = CheckpointStore(home / "sessions" / "bad")
    malformed_ref = "../victim.txt"
    store.write_manifest(
        CheckpointManifest(
            turn_id=0,
            session_id="bad",
            created_ts="2026-06-09T00:00:00Z",
            env_ref=malformed_ref,
            fs_ref=malformed_ref,
            trajectory_ref=TrajectoryReference("codex", "", 0, 0, 0),
        )
    )
    (store.session_dir / "metadata.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))

    assert main(["clean", "--empty"]) == 0

    output = capsys.readouterr().out
    assert "Removed 1 empty session(s)" in output
    assert not store.session_dir.exists()
    assert victim.read_text(encoding="utf-8") == "keep me\n"


def _seed_turn(coordinator, transcript, user_message, end_offset, *, boundary_mode="per_turn_key"):
    from checkpoint_plugin.coordinator import TurnRecord
    from checkpoint_plugin.types import TrajectoryReference

    record_count = sum(1 for line in transcript.read_bytes()[:end_offset].splitlines() if line.strip())
    coordinator.on_turn_end(
        TurnRecord(user_message=user_message),
        TrajectoryReference("codex", str(transcript), 0, end_offset, record_count, boundary_mode=boundary_mode),
    )


def test_list_session_reanchors_last_turn_to_eof(tmp_path, monkeypatch, capsys):
    """F1: `list --session` recovers a trailing record flushed after the Stop hook,
    matching show/diff/resume. The stored manifest was short; list reads at EOF.

    This is the timeout-free path: by read time the transcript is fully flushed, so
    recovery does not depend on the capture-time settle winning any race.
    """
    from checkpoint_plugin.coordinator import CheckpointCoordinator

    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_SIDECHAIN_SETTLE_TIMEOUT", "0")

    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        json.dumps({"type": "response_item", "turn_id": "t1", "payload": {"type": "message"}}) + "\n",
        encoding="utf-8",
    )
    c = CheckpointCoordinator(session_id="codexsess", cwd=cwd)
    c.on_session_start()
    captured = transcript.stat().st_size
    _seed_turn(c, transcript, "do work", captured)
    assert c.store.read_manifest(0).trajectory_ref.end_offset == captured

    # Provider flushes the turn-closing record AFTER the hook captured the slice.
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}, "turn_id": "t1"}) + "\n")
    grown = transcript.stat().st_size
    assert grown > captured

    assert main(["list", "--session", "codexsess"]) == 0
    capsys.readouterr()  # drain
    # The stored manifest is now reanchored to EOF as a side effect of the read.
    assert c.store.read_manifest(0).trajectory_ref.end_offset == grown


def _rolled_back_transcript(path):
    """A codex rollout with an edit-send: turn t2 rolls back turn t1 (version 1)."""
    path.write_text(
        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "version 1"}, "turn_id": "t1"}) + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "thread_rolled_back", "num_turns": 1}}) + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "version 2"}, "turn_id": "t2"}) + "\n",
        encoding="utf-8",
    )


def test_edit_send_replaced_turns_detects_rollback(tmp_path, monkeypatch):
    """F2: a turn whose slice carries `thread_rolled_back num_turns=K` supersedes the
    K preceding turns. The mapping marks each replaced turn with its replacement."""
    from checkpoint_plugin.coordinator import CheckpointCoordinator
    from checkpoint_plugin.types import TrajectoryReference
    from checkpoint_plugin.coordinator import TurnRecord

    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    transcript = tmp_path / "rollout.jsonl"
    _rolled_back_transcript(transcript)
    data = transcript.read_bytes()
    # turn 0 = version 1 (rolled back); turn 1 = the rollback marker + version 2.
    first_nl = data.index(b"\n") + 1
    c = CheckpointCoordinator(session_id="es", cwd=cwd)
    c.on_session_start()
    c.on_turn_end(TurnRecord(user_message="version 1"), TrajectoryReference("codex", str(transcript), 0, first_nl, 1))
    c.on_turn_end(TurnRecord(user_message="version 2"), TrajectoryReference("codex", str(transcript), first_nl, len(data), 2))

    manifests = c.store.list_manifests()
    replaced = _edit_send_replaced_turns(c.store, manifests)
    assert replaced == {0: 1}
    assert _rolled_back_count(manifests[1]) == 1
    assert _rolled_back_count(manifests[0]) == 0


def test_edit_send_no_rollback_is_empty(tmp_path, monkeypatch):
    """No thread_rolled_back marker -> no replaced turns (the common case)."""
    from checkpoint_plugin.coordinator import CheckpointCoordinator, TurnRecord
    from checkpoint_plugin.types import TrajectoryReference

    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}, "turn_id": "t1"}) + "\n",
        encoding="utf-8",
    )
    c = CheckpointCoordinator(session_id="noroll", cwd=cwd)
    c.on_session_start()
    c.on_turn_end(TurnRecord(user_message="hi"), TrajectoryReference("codex", str(transcript), 0, transcript.stat().st_size, 1))
    assert _edit_send_replaced_turns(c.store, c.store.list_manifests()) == {}


def test_list_marks_replaced_turn(tmp_path, monkeypatch, capsys):
    """F2: `list --session` annotates an edit-send-replaced turn."""
    from checkpoint_plugin.coordinator import CheckpointCoordinator, TurnRecord
    from checkpoint_plugin.types import TrajectoryReference

    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    monkeypatch.setenv("NO_COLOR", "1")
    transcript = tmp_path / "rollout.jsonl"
    _rolled_back_transcript(transcript)
    data = transcript.read_bytes()
    first_nl = data.index(b"\n") + 1
    c = CheckpointCoordinator(session_id="esl", cwd=cwd)
    c.on_session_start()
    c.on_turn_end(TurnRecord(user_message="version 1"), TrajectoryReference("codex", str(transcript), 0, first_nl, 1))
    c.on_turn_end(TurnRecord(user_message="version 2"), TrajectoryReference("codex", str(transcript), first_nl, len(data), 2))

    assert main(["list", "--session", "esl"]) == 0
    out = capsys.readouterr().out
    assert "[replaced by turn 1]" in out
    # Only the dead turn is marked; the replacement is not.
    replaced_lines = [line for line in out.splitlines() if "[replaced" in line]
    assert len(replaced_lines) == 1
    assert replaced_lines[0].startswith("0000")


def _write_session(home, session_id, metadata, turns):
    session = home / "sessions" / session_id
    session.mkdir(parents=True)
    (session / "metadata.json").write_text(json.dumps({"session_id": session_id, **metadata}), encoding="utf-8")
    store = CheckpointStore(session)
    transcript = session / f"{session_id}.jsonl"
    transcript.write_text("".join(json.dumps({"turn": index}) + "\n" for index, _ in enumerate(turns)), encoding="utf-8")
    offset = 0
    for turn_id, (created_ts, preview) in enumerate(turns):
        line = json.dumps({"turn": turn_id}) + "\n"
        end = offset + len(line.encode("utf-8"))
        store.write_manifest(
            CheckpointManifest(
                turn_id=turn_id,
                session_id=session_id,
                created_ts=created_ts,
                env_ref="env",
                fs_ref="fs",
                trajectory_ref=TrajectoryReference("codex", str(transcript), offset, end, 1),
                user_message_preview=preview,
                parent_turn_id=turn_id - 1 if turn_id else None,
            )
        )
        offset = end
    return session


def test_session_browser_collects_metadata_without_reanchoring_latest_turn(tmp_path, monkeypatch):
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    session = _write_session(
        home,
        "lazy",
        {"provider": "codex", "source": "startup", "start_ts": "2026-01-01T00:00:00Z"},
        [("2026-01-01T00:01:00Z", "latest turn")],
    )
    transcript = session / "lazy.jsonl"
    original_end = CheckpointStore(session).read_manifest(0).trajectory_ref.end_offset
    transcript.write_text(
        transcript.read_text(encoding="utf-8") + json.dumps({"turn": 0, "tail": True}) + "\n",
        encoding="utf-8",
    )

    providers = collect_provider_trees(home / "sessions")

    assert providers["codex"][0].session_id == "lazy"
    assert CheckpointStore(session).read_manifest(0).trajectory_ref.end_offset == original_end


def test_session_browser_groups_by_provider_and_nests_lineage(tmp_path, monkeypatch):
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    parent = _write_session(
        home,
        "parent",
        {
            "provider": "codex",
            "source": "startup",
            "start_ts": "2026-01-01T00:00:00Z",
            "session_title": "Parent work",
        },
        [
            ("2026-01-01T00:01:00Z", "start"),
            ("2026-01-01T00:02:00Z", "spawn child"),
        ],
    )
    parent_transcript = str(parent / "parent.jsonl")
    _write_session(
        home,
        "fork",
        {
            "provider": "codex",
            "source": "fork",
            "start_ts": "2026-01-01T00:03:00Z",
            "session_title": "Forked work",
            "forked_from_id": "parent",
            "forked_from_transcript": parent_transcript,
            "forked_at_offset": 12,
        },
        [("2026-01-01T00:03:30Z", "fork prompt")],
    )
    _write_session(
        home,
        "parent--subagent-a1",
        {
            "provider": "codex",
            "source": "subagent",
            "start_ts": "2026-01-01T00:01:30Z",
            "session_title": "Agent work",
            "lineage": {"parent_session_id": "parent", "agent_id": "a1"},
        },
        [("2026-01-01T00:01:45Z", "agent prompt")],
    )
    _write_session(
        home,
        "claude-session",
        {
            "provider": "claude",
            "source": "startup",
            "start_ts": "2026-01-01T00:04:00Z",
            "session_title": "Claude work",
        },
        [("2026-01-01T00:04:30Z", "claude prompt")],
    )

    providers = collect_provider_trees(home / "sessions")
    rendered = render_session_tree(providers)

    assert list(providers) == ["claude", "codex"]
    assert "codex (1 sessions, 4 turns)" in rendered
    # New format uses abbreviated labels with separators: "parent… │ [startup] │ 2T"
    assert "parent" in rendered and "[startup]" in rendered and "2T" in rendered
    assert "forked/resumed here" in rendered
    assert "fork" in rendered and "[fork]" in rendered
    assert "subagent spawned here" in rendered
    assert "subagent" in rendered and "[subagent]" in rendered


def test_session_browser_defaults_to_recent_sessions_with_turn_lists_collapsed(tmp_path, monkeypatch):
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    _write_session(
        home,
        "older",
        {"provider": "codex", "source": "startup", "start_ts": "2026-01-01T00:00:00Z"},
        [("2026-01-01T00:01:00Z", "older turn")],
    )
    _write_session(
        home,
        "newer",
        {"provider": "codex", "source": "startup", "start_ts": "2026-01-01T01:00:00Z"},
        [("2026-01-01T01:01:00Z", "newer turn")],
    )

    providers = collect_provider_trees(home / "sessions")

    assert [node.session_id for node in providers["codex"]] == ["newer", "older"]

    expanded = session_browser._default_expanded_groups(providers)
    rows = _rows_for_nodes(providers["codex"], expanded)

    assert [row.node.session_id for row in rows if row.kind == "session"] == ["newer", "older"]
    assert [row.kind for row in rows] == ["group", "session", "session"]
    assert all(not row.expanded for row in rows if row.kind == "session")


def test_checkpoint_without_tty_prints_browser_tree(tmp_path, monkeypatch, capsys):
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    _write_session(
        home,
        "s1",
        {"provider": "codex", "source": "startup", "start_ts": "2026-01-01T00:00:00Z"},
        [("2026-01-01T00:01:00Z", "hello")],
    )

    assert main([]) == 0

    out = capsys.readouterr().out
    assert "codex (1 sessions, 1 turns)" in out
    # New format: T0000 instead of "turn 0000"
    assert "T0000" in out
    assert "hello" in out


def test_session_browser_resume_only_on_valid_checkpoint_turn(tmp_path, monkeypatch):
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    _write_session(
        home,
        "parent",
        {"provider": "codex", "source": "startup", "start_ts": "2026-01-01T00:00:00Z"},
        [("2026-01-01T00:01:00Z", "parent turn")],
    )
    _write_session(
        home,
        "parent--subagent-a1",
        {
            "provider": "codex",
            "source": "subagent",
            "start_ts": "2026-01-01T00:01:30Z",
            "lineage": {"parent_session_id": "parent", "agent_id": "a1"},
        },
        [("2026-01-01T00:01:45Z", "agent turn")],
    )

    providers = collect_provider_trees(home / "sessions")
    rows = _rows_for_nodes(providers["codex"])
    parent_turn = next(row for row in rows if row.kind == "turn" and row.node.session_id == "parent")
    subagent_turn = next(row for row in rows if row.kind == "turn" and row.node.session_id == "parent--subagent-a1")
    session_header = next(row for row in rows if row.kind == "session" and row.node.session_id == "parent")

    assert _command_action("/resume", parent_turn).session_id == "parent"
    assert _command_action("/show", subagent_turn).session_id == "parent--subagent-a1"
    assert _command_action("/diff", subagent_turn).session_id == "parent--subagent-a1"
    assert _command_action("/resume", subagent_turn) is None
    assert _command_action("/terminal", parent_turn) is None
    assert _command_action("/resume", session_header) is None

    parent_detail = "".join(text for _style, text in _detail_fragments(parent_turn, {}))
    subagent_detail = "".join(text for _style, text in _detail_fragments(subagent_turn, {}))
    session_detail = "".join(text for _style, text in _detail_fragments(session_header, {}))
    assert "Commands: show:yes  diff:yes  resume:yes" in parent_detail
    assert "Commands: show:yes  diff:yes  resume:no  (resume unavailable: subagent)" in subagent_detail
    assert "Commands: show:no  diff:no  resume:no  (select a checkpoint turn)" in session_detail


def test_session_browser_resume_outputs_cli_command_hint_only():
    title, text = _resume_hint("parent", 3)

    assert title == "Resume parent turn 3"
    assert "checkpoint resume parent 3" in text
    assert "Run this command outside the browser" in text
    assert "Press y" not in text
    assert "restore in place" not in text

    fragments = _output_fragments(
        {
            "output_visible": True,
            "output_title": title,
            "output_text": text,
            "output_scroll": 0,
        }
    )
    assert ("class:output.command", "checkpoint resume parent 3\n") in fragments


def test_session_browser_detail_values_are_not_white():
    style_rules = dict(session_browser._browser_style().style_rules)

    assert style_rules["detail.value"] == "#875fd7"


def test_output_fragments_show_inline_command_result():
    fragments = _output_fragments(
        {
            "output_visible": True,
            "output_title": "Diff s1 turn 0",
            "output_text": "line 1\n+added\n-removed",
            "output_scroll": 0,
        }
    )
    rendered = "".join(text for _style, text in fragments)
    assert "Diff s1 turn 0" in rendered
    assert "+added" in rendered
    assert "-removed" in rendered


def test_output_fragments_scroll_and_clamp_to_visible_page():
    state = {
        "output_visible": True,
        "output_title": "Show s1 turn 0",
        "output_text": "\n".join(f"line {index}" for index in range(12)),
        "output_scroll": 999,
        "output_height": 6,
    }

    fragments = _output_fragments(state)
    rendered = "".join(text for _style, text in fragments)

    assert state["output_scroll"] == 8
    assert "9-12/12" in rendered
    assert "line 8" in rendered
    assert "line 11" in rendered
    assert "line 7" not in rendered
    assert _output_page_size(state) == 4


def test_body_fragments_respect_tree_scroll(tmp_path, monkeypatch):
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    turns = [(f"2026-01-01T00:{minute:02d}:00Z", f"turn {minute}") for minute in range(20)]
    _write_session(
        home,
        "long",
        {"provider": "codex", "source": "startup", "start_ts": "2026-01-01T00:00:00Z"},
        turns,
    )
    rows = _rows_for_nodes(collect_provider_trees(home / "sessions")["codex"])
    state = {"tree_scroll": 5, "output_visible": False}

    rendered = "".join(text for _style, text in _body_fragments(rows, 10, state))

    # New format: T0009, T0010 instead of "turn 0009", "turn 0010"
    assert "T0009" in rendered or "T0010" in rendered
    assert "T0000" not in rendered


def test_body_fragments_keep_selected_row_visible_when_scroll_hints_render(tmp_path, monkeypatch):
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    monkeypatch.setattr(session_browser.shutil, "get_terminal_size", lambda: os.terminal_size((100, 20)))
    turns = [(f"2026-01-01T00:{minute:02d}:00Z", f"turn {minute}") for minute in range(20)]
    _write_session(
        home,
        "long",
        {"provider": "codex", "source": "startup", "start_ts": "2026-01-01T00:00:00Z"},
        turns,
    )
    rows = _rows_for_nodes(collect_provider_trees(home / "sessions")["codex"])
    selected = 7
    state = {"tree_scroll": 0, "output_visible": False}

    fragments = _body_fragments(rows, selected, state)

    rendered = "".join(text for _style, text in fragments)
    selected_text = rows[selected].label
    selected_styles = [style for style, text in fragments if text == selected_text]
    assert selected_text in rendered
    assert any("reverse" in style for style in selected_styles)


def test_tui_fragments_render_claude_session_and_subagent(tmp_path, monkeypatch):
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    parent = _write_session(
        home,
        "claude-parent",
        {
            "provider": "claude",
            "source": "startup",
            "start_ts": "2026-01-01T00:00:00Z",
            "session_title": "Claude parent work",
        },
        [
            ("2026-01-01T00:01:00Z", "ask claude"),
            ("2026-01-01T00:02:00Z", "spawn claude subagent abc123"),
        ],
    )
    parent_transcript = parent / "claude-parent.jsonl"
    parent_transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "promptId": "p-1", "message": {"content": "ask claude"}}),
                json.dumps({"type": "assistant", "message": {"content": "uses abc123"}}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_session(
        home,
        "claude-parent--subagent-abc123",
        {
            "provider": "claude",
            "source": "subagent",
            "start_ts": "2026-01-01T00:01:30Z",
            "session_title": "Claude subagent work",
            "lineage": {"parent_session_id": "claude-parent", "agent_id": "abc123"},
        },
        [("2026-01-01T00:01:45Z", "subagent result")],
    )
    providers = collect_provider_trees(home / "sessions")

    assert list(providers) == ["claude"]
    header = "".join(text for _style, text in _header_fragments(["claude"], providers, {"provider": 0}))
    assert "claude" in header
    assert "(1/3)" in header

    rows = _rows_for_nodes(providers["claude"])
    body = "".join(text for _style, text in _body_fragments(rows, 0, {"tree_scroll": 0, "output_visible": False}))
    assert "Claude parent work" in body
    assert "subagent spawned here" in body
    assert "Claude subagent work" in body

    parent_row = next(row for row in rows if row.kind == "session" and row.node.session_id == "claude-parent")
    subagent_turn = next(
        row
        for row in rows
        if row.kind == "turn" and row.node.session_id == "claude-parent--subagent-abc123"
    )
    parent_detail = "".join(text for _style, text in _detail_fragments(parent_row, {}))
    subagent_detail = "".join(text for _style, text in _detail_fragments(subagent_turn, {}))
    assert "Provider: claude" in parent_detail
    assert "Source: startup" in parent_detail
    assert "Subagent: abc123" in subagent_detail
    assert "SUBAGENT" in subagent_detail


def test_tui_places_same_turn_subagent_before_fork_link(tmp_path, monkeypatch):
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    parent = _write_session(
        home,
        "claude-parent",
        {
            "provider": "claude",
            "source": "startup",
            "start_ts": "2026-01-01T00:00:00Z",
            "session_title": "Claude parent work",
        },
        [
            ("2026-01-01T00:01:00Z", "ask claude"),
            ("2026-01-01T00:02:00Z", "spawn abc123 and then fork"),
        ],
    )
    parent_transcript = parent / "claude-parent.jsonl"
    parent_transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "promptId": "p-2", "message": {"content": "spawn abc123"}}),
                json.dumps({"type": "assistant", "message": {"content": "abc123"}}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_session(
        home,
        "claude-parent--subagent-abc123",
        {
            "provider": "claude",
            "source": "subagent",
            "start_ts": "2026-01-01T00:01:30Z",
            "session_title": "Claude subagent work",
            "lineage": {"parent_session_id": "claude-parent", "agent_id": "abc123"},
        },
        [("2026-01-01T00:01:45Z", "subagent result")],
    )
    _write_session(
        home,
        "claude-fork",
        {
            "provider": "claude",
            "source": "resume",
            "start_ts": "2026-01-01T00:03:00Z",
            "session_title": "Claude fork work",
            "forked_from_id": "claude-parent",
            "forked_from_transcript": str(parent_transcript),
            "forked_at_offset": parent_transcript.stat().st_size,
        },
        [("2026-01-01T00:03:30Z", "fork prompt")],
    )

    providers = collect_provider_trees(home / "sessions")
    rows = _rows_for_nodes(providers["claude"])
    labels = [row.label for row in rows]

    turn_index = next(
        index
        for index, row in enumerate(rows)
        if row.kind == "turn" and row.node.session_id == "claude-parent" and row.manifest and row.manifest.turn_id == 1
    )
    subagent_index = labels.index("subagent spawned here")
    fork_index = labels.index("forked/resumed here")
    assert turn_index < subagent_index < fork_index


def test_cross_cwd_resume_creates_phantom_ancestry(tmp_path, monkeypatch):
    """A resume of a startup into a different cwd appears as a standalone in the new group."""
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    _write_session(
        home,
        "parent",
        {
            "provider": "codex",
            "source": "startup",
            "start_ts": "2026-01-01T00:00:00Z",
            "session_title": "Parent work",
            "cwd": "/projects/original",
        },
        [
            ("2026-01-01T00:01:00Z", "hello"),
            ("2026-01-01T00:02:00Z", "do work"),
        ],
    )
    _write_session(
        home,
        "resumed",
        {
            "provider": "codex",
            "source": "resume",
            "start_ts": "2026-01-01T00:05:00Z",
            "session_title": "Resumed work",
            "cwd": "/projects/copy",
            "resumed_from_session_id": "parent",
            "resumed_from_turn_id": 0,
            "forked_from_id": "parent",
        },
        [("2026-01-01T00:05:30Z", "resumed prompt")],
    )

    providers = collect_provider_trees(home / "sessions")
    rendered = render_session_tree(providers)

    # The parent stays in its own group
    assert "[original]" in rendered
    # The resumed session is in the copy group — no phantom needed (direct parent only)
    assert "[copy]" in rendered

    # Verify the tree structure: parent in original group has no fork children
    codex_nodes = providers["codex"]
    original_nodes = [n for n in codex_nodes if n.cwd == "/projects/original"]
    assert len(original_nodes) == 1
    assert original_nodes[0].session_id == "parent"
    assert not original_nodes[0].fork_children

    # The copy group has the resumed session as a top-level node (no phantom needed)
    copy_nodes = [n for n in codex_nodes if n.cwd == "/projects/copy"]
    assert len(copy_nodes) == 1
    assert copy_nodes[0].session_id == "resumed"
    assert copy_nodes[0].phantom_ref is None


def test_cross_cwd_recursive_fork_resume_builds_full_chain(tmp_path, monkeypatch):
    """Resume of a fork into a different cwd shows grandparent phantom chain (direct parent skipped)."""
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    parent = _write_session(
        home,
        "root",
        {
            "provider": "codex",
            "source": "startup",
            "start_ts": "2026-01-01T00:00:00Z",
            "session_title": "Root",
            "cwd": "/projects/original",
        },
        [
            ("2026-01-01T00:01:00Z", "start"),
            ("2026-01-01T00:02:00Z", "branch point"),
        ],
    )
    parent_transcript = str(parent / "root.jsonl")
    _write_session(
        home,
        "fork1",
        {
            "provider": "codex",
            "source": "fork",
            "start_ts": "2026-01-01T00:03:00Z",
            "session_title": "Fork 1",
            "cwd": "/projects/original",
            "forked_from_id": "root",
            "forked_from_transcript": parent_transcript,
            "forked_at_offset": 12,
        },
        [("2026-01-01T00:03:30Z", "fork prompt")],
    )
    _write_session(
        home,
        "resumed-fork",
        {
            "provider": "codex",
            "source": "resume",
            "start_ts": "2026-01-01T00:10:00Z",
            "session_title": "Resumed fork",
            "cwd": "/projects/copy",
            "resumed_from_session_id": "fork1",
            "resumed_from_turn_id": 0,
            "forked_from_id": "root",
        },
        [("2026-01-01T00:10:30Z", "resumed fork prompt")],
    )

    providers = collect_provider_trees(home / "sessions")
    rendered = render_session_tree(providers)

    # The copy group has: root phantom -> resumed-fork (fork1 skipped as direct parent)
    copy_nodes = [n for n in providers["codex"] if n.cwd == "/projects/copy"]
    assert len(copy_nodes) == 1
    phantom_root = copy_nodes[0]
    assert phantom_root.phantom_ref == "root"
    # Root phantom has the real resumed-fork as child (fork1 not phantomed)
    all_children = [c for children in phantom_root.fork_children.values() for c in children]
    assert len(all_children) == 1
    assert all_children[0].session_id == "resumed-fork"
    assert all_children[0].phantom_ref is None
    # The resumed-fork is nested under the fork point where fork1 was in root
    assert "forked/resumed here" in rendered
    assert "(ref)" in rendered


def test_codex_fork_attaches_at_true_turn_despite_inlined_prefix_anchor(tmp_path, monkeypatch):
    """A codex fork's forked_at_offset is in the fork's OWN file and may replay
    rolled-back turns; the attach turn must come from aligning the fork-point
    trajectory against the parent, not from comparing raw byte offsets."""
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    parent = _write_session(
        home,
        "parent",
        {
            "provider": "codex",
            "source": "startup",
            "start_ts": "2026-01-01T00:00:00Z",
            "session_title": "Parent",
            "cwd": "/projects/original",
        },
        [
            ("2026-01-01T00:01:00Z", "hi"),
            ("2026-01-01T00:02:00Z", "long second turn"),
        ],
    )
    parent_transcript = str(parent / "parent.jsonl")
    # Fork-point trajectory: fork's own session_meta, the parent's turn-0 record
    # (with a re-stamped timestamp), then a replayed-but-rolled-back user message
    # from the parent's in-flight turn 1.
    blob = (
        json.dumps({"type": "session_meta", "payload": {"forked_from_id": "parent"}}) + "\n"
        + json.dumps({"timestamp": "2026-01-01T00:03:00Z", "turn": 0}) + "\n"
        + json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "long second turn"}]}}) + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "thread_rolled_back", "num_turns": 1}}) + "\n"
    ).encode("utf-8")
    fork_session = _write_session(
        home,
        "fork",
        {
            "provider": "codex",
            "source": "fork",
            "start_ts": "2026-01-01T00:03:00Z",
            "session_title": "Fork",
            "cwd": "/projects/original",
            "forked_from_id": "parent",
            "forked_from_transcript": parent_transcript,
            # Inlined-prefix anchor: fork-file coordinates, larger than the whole
            # parent file, so a parent-coordinate comparison lands on turn 1.
            "forked_at_offset": len(blob),
        },
        [("2026-01-01T00:03:30Z", "this is fork")],
    )
    sha = CheckpointStore(fork_session).store_blob(blob)
    metadata = json.loads((fork_session / "metadata.json").read_text(encoding="utf-8"))
    metadata["fork_point_trajectory_ref"] = sha
    (fork_session / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    providers = collect_provider_trees(home / "sessions")
    parent_node = next(n for n in providers["codex"] if n.session_id == "parent")
    assert set(parent_node.fork_children) == {0}
    assert [c.session_id for c in parent_node.fork_children[0]] == ["fork"]


def test_cross_cwd_resume_of_fork_attaches_at_grandparent_fork_turn(tmp_path, monkeypatch):
    """The child carries the direct parent's turns, so it must attach in the
    grandparent phantom at the parent's fork point — not at resumed_from_turn_id,
    which is in the direct parent's coordinates."""
    home = tmp_path / "plugin"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    parent = _write_session(
        home,
        "root",
        {
            "provider": "codex",
            "source": "startup",
            "start_ts": "2026-01-01T00:00:00Z",
            "session_title": "Root",
            "cwd": "/projects/original",
        },
        [
            ("2026-01-01T00:01:00Z", "hi"),
            ("2026-01-01T00:02:00Z", "second turn"),
        ],
    )
    parent_transcript = str(parent / "root.jsonl")
    _write_session(
        home,
        "fork1",
        {
            "provider": "codex",
            "source": "fork",
            "start_ts": "2026-01-01T00:03:00Z",
            "session_title": "Fork 1",
            "cwd": "/projects/original",
            "forked_from_id": "root",
            "forked_from_transcript": parent_transcript,
            "forked_at_offset": 12,  # parent coordinates: end of root turn 0
        },
        [
            ("2026-01-01T00:03:30Z", "fork turn 0"),
            ("2026-01-01T00:04:00Z", "fork turn 1"),
        ],
    )
    _write_session(
        home,
        "resumed-fork",
        {
            "provider": "codex",
            "source": "resume",
            "start_ts": "2026-01-01T00:10:00Z",
            "session_title": "Resumed fork",
            "cwd": "/projects/copy",
            "resumed_from_session_id": "fork1",
            "resumed_from_turn_id": 1,
            "forked_from_id": "root",
        },
        [
            ("2026-01-01T00:10:30Z", "fork turn 0"),
            ("2026-01-01T00:11:00Z", "fork turn 1"),
        ],
    )

    providers = collect_provider_trees(home / "sessions")
    copy_nodes = [n for n in providers["codex"] if n.cwd == "/projects/copy"]
    assert len(copy_nodes) == 1
    phantom_root = copy_nodes[0]
    assert phantom_root.phantom_ref == "root"
    # Phantom truncated to the fork point: only root turn 0 survives.
    assert [m.turn_id for m in phantom_root.manifests] == [0]
    # Child attaches at root turn 0 (fork1's branch point), NOT turn 1
    # (resumed_from_turn_id, a fork1-coordinate value).
    assert set(phantom_root.fork_children) == {0}
    assert [c.session_id for c in phantom_root.fork_children[0]] == ["resumed-fork"]
