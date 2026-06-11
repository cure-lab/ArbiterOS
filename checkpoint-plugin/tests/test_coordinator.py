import json
from multiprocessing import Process

from checkpoint_plugin.coordinator import CheckpointCoordinator, TurnRecord
from checkpoint_plugin.store import CheckpointStore
from checkpoint_plugin.types import TrajectoryReference


def test_full_turn_cycle(tmp_path, monkeypatch):
    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    manifest = coordinator.on_turn_end(TurnRecord(user_message="hello", assistant_text="hi"))

    assert manifest.turn_id == 0
    assert manifest.trajectory_ref is not None
    assert manifest.user_message_preview == "hello"
    assert coordinator.get_checkpoint(0) == manifest
    assert (home / "sessions" / "s1" / "metadata.json").exists()


def test_metadata_session_title_defaults_to_none(tmp_path):
    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd, plugin_home=home)
    coordinator.on_session_start()

    metadata = json.loads((home / "sessions" / "s1" / "metadata.json").read_text())
    assert metadata["session_title"] is None
    assert "model" not in metadata


def test_codex_session_title_is_read_from_session_index(tmp_path, monkeypatch):
    home = tmp_path / "plugin"
    codex_home = tmp_path / ".codex"
    cwd = tmp_path / "work"
    cwd.mkdir()
    codex_home.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    (codex_home / "session_index.jsonl").write_text(
        '{"id":"other","thread_name":"Other"}\n'
        '{"id":"s1","thread_name":"Respond to greeting"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd, plugin_home=home)
    coordinator.on_session_start()

    metadata = json.loads((home / "sessions" / "s1" / "metadata.json").read_text())
    assert metadata["session_title"] == "Respond to greeting"
    assert "model" not in metadata


def test_claude_session_title_is_read_from_transcript_slug(tmp_path, monkeypatch):
    home = tmp_path / "plugin"
    claude_home = tmp_path / ".claude"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    claude_home.mkdir()
    (cwd / "CLAUDE.md").write_text("agent", encoding="utf-8")
    transcript.write_text(
        '{"type":"user","message":{"role":"user","content":"hello"},"slug":"complete-environment-configuration-documentation"}\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("CHECKPOINT_PROVIDER", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("TEST_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "s1")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd, plugin_home=home)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(),
        TrajectoryReference("claude", str(transcript), 0, transcript.stat().st_size, 1),
    )

    metadata = json.loads((home / "sessions" / "s1" / "metadata.json").read_text())
    assert metadata["session_title"] == "complete-environment-configuration-documentation"


def test_claude_session_title_prefers_ai_title_over_slug(tmp_path, monkeypatch):
    home = tmp_path / "plugin"
    claude_home = tmp_path / ".claude"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    claude_home.mkdir()
    (cwd / "CLAUDE.md").write_text("agent", encoding="utf-8")
    transcript.write_text(
        '{"type":"user","message":{"role":"user","content":"hello"}}\n'
        '{"type":"assistant","message":{"role":"assistant","content":"hi"}}\n'
        '{"type":"ai-title","aiTitle":"Fix the login bug","sessionId":"s1"}\n'
        '{"type":"user","slug":"random-slug-name","message":{"role":"user","content":"more"}}\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("CHECKPOINT_PROVIDER", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("TEST_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "s1")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd, plugin_home=home)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(),
        TrajectoryReference("claude", str(transcript), 0, transcript.stat().st_size, 4),
    )

    metadata = json.loads((home / "sessions" / "s1" / "metadata.json").read_text())
    assert metadata["session_title"] == "Fix the login bug"


def test_resolve_session_title_claude_from_transcript(tmp_path, monkeypatch):
    """resolve_session_title reads aiTitle from Claude transcript."""
    from checkpoint_plugin.coordinator import resolve_session_title

    cwd = tmp_path / "work"
    cwd.mkdir()
    claude_home = tmp_path / "home" / ".claude"
    project_dir_name = str(cwd).replace("/", "-")
    project_dir = claude_home / "projects" / project_dir_name
    project_dir.mkdir(parents=True)
    transcript = project_dir / "sess1.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"role":"user","content":"hello"}}\n'
        '{"type":"ai-title","aiTitle":"Implement dark mode","sessionId":"sess1"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")

    metadata = {"provider": "claude", "session_id": "sess1", "cwd": str(cwd)}
    assert resolve_session_title(metadata) == "Implement dark mode"


def test_opencode_session_title_is_read_from_sqlite(tmp_path, monkeypatch):
    import sqlite3

    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / ".opencode").mkdir()

    # Create a mock opencode.db
    data_dir = tmp_path / "data" / "opencode"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE project (id TEXT PRIMARY KEY)"
    )
    conn.execute("INSERT INTO project VALUES ('proj1')")
    conn.execute(
        "CREATE TABLE session ("
        "id TEXT PRIMARY KEY, project_id TEXT NOT NULL, slug TEXT NOT NULL, "
        "directory TEXT NOT NULL, title TEXT NOT NULL, version TEXT NOT NULL, "
        "time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, "
        "FOREIGN KEY (project_id) REFERENCES project(id))"
    )
    conn.execute(
        "INSERT INTO session VALUES (?, 'proj1', 'greeting', '/tmp', 'Greeting', '1.0', 1000, 2000)",
        ("ses_abc123",),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "opencode")

    coordinator = CheckpointCoordinator(session_id="ses_abc123", cwd=cwd, plugin_home=home)
    coordinator.on_session_start()

    metadata = json.loads((home / "sessions" / "ses_abc123" / "metadata.json").read_text())
    assert metadata["session_title"] == "Greeting"


def test_resolve_session_title_codex_lazy(tmp_path, monkeypatch):
    """resolve_session_title picks up a codex title written after session_start."""
    from checkpoint_plugin.coordinator import resolve_session_title

    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    # No index entry yet → None
    metadata = {"provider": "codex", "session_id": "s1", "session_title": None}
    assert resolve_session_title(metadata) is None

    # Write the index entry (simulating codex writing it after our hook)
    (codex_home / "session_index.jsonl").write_text(
        '{"id":"s1","thread_name":"Greet user","updated_at":"2026-06-03T07:35:49Z"}\n',
        encoding="utf-8",
    )
    assert resolve_session_title(metadata) == "Greet user"


def test_resolve_session_title_opencode_lazy(tmp_path, monkeypatch):
    """resolve_session_title reads opencode title from SQLite."""
    import sqlite3
    from checkpoint_plugin.coordinator import resolve_session_title

    data_dir = tmp_path / "data" / "opencode"
    data_dir.mkdir(parents=True)
    db_path = data_dir / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE project (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO project VALUES ('p1')")
    conn.execute(
        "CREATE TABLE session ("
        "id TEXT PRIMARY KEY, project_id TEXT NOT NULL, slug TEXT NOT NULL, "
        "directory TEXT NOT NULL, title TEXT NOT NULL, version TEXT NOT NULL, "
        "time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, "
        "FOREIGN KEY (project_id) REFERENCES project(id))"
    )
    conn.execute(
        "INSERT INTO session VALUES ('ses_xyz', 'p1', 'greet', '/tmp', 'Hello World', '1.0', 1000, 2000)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path / "data"))

    metadata = {"provider": "opencode", "session_id": "ses_xyz"}
    assert resolve_session_title(metadata) == "Hello World"
    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    transcript = tmp_path / "provider.jsonl"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    transcript.write_text('{"turn_id":"provider-turn-1","message":"hi"}\n', encoding="utf-8")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd, plugin_home=home)
    ref = TrajectoryReference(
        provider="codex",
        transcript_path=str(transcript),
        start_offset=0,
        end_offset=transcript.stat().st_size,
        record_count=1,
    )
    manifest = coordinator.on_turn_end(TurnRecord(assistant_text="done"), ref)

    assert manifest.trajectory_ref == ref
    assert not (home / "sessions" / "s1" / "trajectory.jsonl").exists()


def test_turn_preview_falls_back_to_codex_user_message_event(tmp_path):
    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    transcript = tmp_path / "codex.jsonl"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    transcript.write_text(
        '{"type":"event_msg","payload":{"type":"user_message","message":"hello from codex\\n"}}\n',
        encoding="utf-8",
    )

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd, plugin_home=home)
    manifest = coordinator.on_turn_end(
        TurnRecord(),
        TrajectoryReference("codex", str(transcript), 0, transcript.stat().st_size, 1),
    )

    assert manifest.user_message_preview == "hello from codex"


def test_turn_preview_falls_back_to_claude_user_record(tmp_path):
    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    (cwd / "CLAUDE.md").write_text("agent", encoding="utf-8")
    transcript.write_text(
        '{"type":"user","message":{"role":"user","content":"hello from claude"}}\n',
        encoding="utf-8",
    )

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd, plugin_home=home)
    manifest = coordinator.on_turn_end(
        TurnRecord(),
        TrajectoryReference("claude", str(transcript), 0, transcript.stat().st_size, 1),
    )

    assert manifest.user_message_preview == "hello from claude"


def test_next_turn_closes_previous_reference_range(tmp_path):
    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    transcript = tmp_path / "provider.jsonl"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    transcript.write_text(
        '{"turn_id":"one","message":"start"}\n'
        '{"turn_id":"one","message":"late"}\n'
        '{"turn_id":"two","message":"start"}\n',
        encoding="utf-8",
    )
    second_start = transcript.read_bytes().index(b'{"turn_id":"two"')

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd, plugin_home=home)
    coordinator.on_turn_end(
        TurnRecord(assistant_text="one"),
        TrajectoryReference("codex", str(transcript), 0, 35, 1),
    )
    coordinator.on_turn_end(
        TurnRecord(assistant_text="two"),
        TrajectoryReference("codex", str(transcript), second_start, transcript.stat().st_size, 1),
    )

    refreshed = CheckpointStore(home / "sessions" / "s1").read_manifest(0)
    assert refreshed.trajectory_ref is not None
    assert refreshed.trajectory_ref.end_offset == second_start
    assert refreshed.trajectory_ref.record_count == 2


def test_concurrent_turn_end_writes_are_serialized(tmp_path):
    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")

    processes = [
        Process(target=_write_turn, args=(home, cwd, "s1", f"message-{idx}"))
        for idx in range(8)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join()

    assert [process.exitcode for process in processes] == [0] * len(processes)

    store = CheckpointStore(home / "sessions" / "s1")
    manifests = store.list_manifests()

    assert [manifest.turn_id for manifest in manifests] == list(range(len(processes)))
    for manifest in manifests:
        assert manifest.trajectory_ref is not None
        assert manifest.trajectory_ref.record_count == 1


def _write_turn(home, cwd, session_id, user_message):
    coordinator = CheckpointCoordinator(session_id=session_id, cwd=cwd, plugin_home=home)
    coordinator.on_turn_end(TurnRecord(user_message=user_message))


def test_resolve_fork_ancestor_transcript_avoids_self_reference(tmp_path):
    """P6-15: a claude resume's transcript_path is the session's OWN file; the
    ancestor must be resolved via forkedFrom.sessionId, never recorded as self."""
    from checkpoint_plugin.coordinator import _resolve_fork_ancestor_transcript

    proj = tmp_path
    own = proj / "SELF.jsonl"
    ancestor = proj / "ANCESTOR.jsonl"
    ancestor.write_text(json.dumps({"type": "user", "sessionId": "ANCESTOR"}) + "\n", encoding="utf-8")
    own.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "sessionId": "SELF", "forkedFrom": {"sessionId": "ANCESTOR", "messageUuid": "m1"}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # claude self-named file -> resolves to the distinct ancestor sibling.
    resolved = _resolve_fork_ancestor_transcript("claude", str(own), "SELF")
    assert resolved == str(ancestor)

    # No forkedFrom / ancestor missing -> None (drop the self-pointer rather than lie).
    lonely = proj / "LONE.jsonl"
    lonely.write_text(json.dumps({"type": "user", "sessionId": "LONE"}) + "\n", encoding="utf-8")
    assert _resolve_fork_ancestor_transcript("claude", str(lonely), "LONE") is None

    # codex path is the real parent rollout already -> returned verbatim.
    assert _resolve_fork_ancestor_transcript("codex", "/prior/rollout.jsonl", "FORKED") == "/prior/rollout.jsonl"


def test_last_turn_end_offset_reanchored_to_eof_on_next_session_start(tmp_path, monkeypatch):
    """F13: the last turn's end_offset trails EOF when the provider flushes a
    trailing same-turn record after the Stop hook reads the file. There is no
    finalize hook, so the next session_start re-anchors it to the (now fully
    flushed) EOF — provided the tail is same-turn complete."""
    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "promptId": "p0", "uuid": "u0", "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a0", "promptId": "p0", "message": {"role": "assistant", "content": "x"}}) + "\n",
        encoding="utf-8",
    )
    c = CheckpointCoordinator(session_id="sx", cwd=cwd)
    c.on_session_start()
    captured = transcript.stat().st_size
    c.on_turn_end(TurnRecord(user_message="hi"), TrajectoryReference("claude", str(transcript), 0, captured, 2))
    assert c.store.read_manifest(0).trajectory_ref.end_offset == captured

    # Provider flushes a trailing same-turn record AFTER the Stop hook.
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "system", "subtype": "stop_hook_summary", "promptId": "p0"}) + "\n")
    grown = transcript.stat().st_size

    CheckpointCoordinator(session_id="sx", cwd=cwd).on_session_start()
    assert c.store.read_manifest(0).trajectory_ref.end_offset == grown


def test_reanchor_does_not_absorb_a_new_turn_tail(tmp_path, monkeypatch):
    """F13 guard: a trailing record bearing a DIFFERENT per-turn key is a new turn
    and must NOT be folded into the last captured turn."""
    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "promptId": "p0", "uuid": "u0", "message": {"role": "user", "content": "hi"}}) + "\n",
        encoding="utf-8",
    )
    c = CheckpointCoordinator(session_id="sy", cwd=cwd)
    c.on_session_start()
    captured = transcript.stat().st_size
    c.on_turn_end(TurnRecord(user_message="hi"), TrajectoryReference("claude", str(transcript), 0, captured, 1))

    # A NEW turn (distinct promptId) lands in the tail.
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "user", "promptId": "p1", "uuid": "u1", "message": {"role": "user", "content": "next"}}) + "\n")

    CheckpointCoordinator(session_id="sy", cwd=cwd).on_session_start()
    assert c.store.read_manifest(0).trajectory_ref.end_offset == captured


def test_reanchor_last_turn_to_eof_runs_on_read_for_terminal_session(tmp_path, monkeypatch):
    """F13: a terminal/forked session never restarts under its own id, so its last
    stored turn trails EOF for non-resume consumers (show/diff/rewind). The
    module-level reanchor lets a read-path consumer trigger the same lazy fix."""
    from checkpoint_plugin.coordinator import reanchor_last_turn_to_eof

    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "promptId": "p0", "uuid": "u0", "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a0", "promptId": "p0", "message": {"role": "assistant", "content": "x"}}) + "\n",
        encoding="utf-8",
    )
    c = CheckpointCoordinator(session_id="terminal", cwd=cwd)
    c.on_session_start()
    captured = transcript.stat().st_size
    c.on_turn_end(TurnRecord(user_message="hi"), TrajectoryReference("claude", str(transcript), 0, captured, 2))

    # Provider flushes a trailing same-turn record; the session never restarts.
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "system", "subtype": "stop_hook_summary", "promptId": "p0"}) + "\n")
    grown = transcript.stat().st_size

    # A read-path consumer reanchors on demand.
    changed = reanchor_last_turn_to_eof(c.store)
    assert changed is True
    assert c.store.read_manifest(0).trajectory_ref.end_offset == grown
    # Idempotent: a second call is a no-op once the manifest already reaches EOF.
    assert reanchor_last_turn_to_eof(c.store) is False


def test_reanchor_respects_session_boundary_for_subagent_slice(tmp_path, monkeypatch):
    """A subagent slice (boundary_mode=session_boundary) spans many turns, so its
    trailing `task_complete` carries the LAST turn's id. reanchor must still
    absorb it — the per_turn_key guard would reject it because the key differs
    from the slice's first turn, leaving the stored manifest short of EOF."""
    from checkpoint_plugin.coordinator import reanchor_last_turn_to_eof

    home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(home))
    transcript = tmp_path / "rollout.jsonl"
    # Multi-turn subagent body (turns t1, t2) — distinct keys within one slice.
    transcript.write_text(
        json.dumps({"type": "response_item", "turn_id": "t1", "payload": {"type": "message"}}) + "\n"
        + json.dumps({"type": "response_item", "turn_id": "t2", "payload": {"type": "message"}}) + "\n",
        encoding="utf-8",
    )
    c = CheckpointCoordinator(session_id="sub", cwd=cwd)
    c.on_session_start()
    captured = transcript.stat().st_size
    c.on_turn_end(
        TurnRecord(user_message="sub work"),
        TrajectoryReference("codex", str(transcript), 0, captured, 2, boundary_mode="session_boundary"),
    )

    # Codex flushes the turn-closing record bearing the LAST turn's id (t2).
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}, "turn_id": "t2"}) + "\n")
    grown = transcript.stat().st_size

    assert reanchor_last_turn_to_eof(c.store) is True
    assert c.store.read_manifest(0).trajectory_ref.end_offset == grown
    # boundary_mode survives the manifest rewrite.
    assert c.store.read_manifest(0).trajectory_ref.boundary_mode == "session_boundary"
