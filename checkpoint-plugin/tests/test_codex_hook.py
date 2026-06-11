import io
import json

from checkpoint_plugin.env.collector import environment_from_blob
from checkpoint_plugin.integrations import codex_hook
from checkpoint_plugin.store import CheckpointStore


def test_codex_session_start_writes_metadata(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "codex-s1",
        "cwd": str(cwd),
        "model": "gpt-test",
        "permission_mode": "plan",
        "source": "startup",
    }
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert codex_hook.main([]) == 0

    metadata = json.loads((plugin_home / "sessions" / "codex-s1" / "metadata.json").read_text())
    assert metadata["provider"] == "codex"
    assert metadata["cwd"] == str(cwd)
    assert metadata["source"] == "startup"


def test_codex_session_env_captures_approval_and_sandbox_when_present(tmp_path, monkeypatch):
    """F15: when the codex hook payload carries approval_policy/sandbox, record them
    in session_env so the checkpoint metadata describes the actual policy, not just
    the coarse permission_mode. Best-effort: only when the payload provides them."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "codex-pol",
        "cwd": str(cwd),
        "model": "gpt-test",
        "permission_mode": "default",
        "approval_policy": "on-request",
        "sandbox_mode": "workspace-write",
        "source": "startup",
    }
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert codex_hook.main([]) == 0

    env = json.loads((plugin_home / "sessions" / "codex-pol" / "metadata.json").read_text())["session_env"]
    assert env["approval_policy"] == "on-request"
    assert env["sandbox_mode"] == "workspace-write"
    assert env["permission_mode"] == "default"


def test_codex_session_env_omits_policy_when_absent(tmp_path, monkeypatch):
    """F15: a payload without approval/sandbox fields records neither (no empty keys)."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "codex-nopol",
        "cwd": str(cwd),
        "model": "gpt-test",
        "source": "startup",
    }
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert codex_hook.main([]) == 0

    env = json.loads((plugin_home / "sessions" / "codex-nopol" / "metadata.json").read_text()).get("session_env", {})
    assert "approval_policy" not in env
    assert "sandbox_mode" not in env


def test_codex_resume_session_records_fork_lineage(tmp_path, monkeypatch):
    """B5: a native resume/compact fork records the transcript it forked from."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "codex-forked",
        "cwd": str(cwd),
        "source": "resume",
        "transcript_path": "/prior/rollout.jsonl",
    }
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert codex_hook.main([]) == 0

    metadata = json.loads((plugin_home / "sessions" / "codex-forked" / "metadata.json").read_text())
    assert metadata["source"] == "resume"
    assert metadata["forked_from_transcript"] == "/prior/rollout.jsonl"


def test_codex_resume_records_fork_anchor_offset(tmp_path, monkeypatch):
    """F5: a native fork records the byte offset + record count it branched at."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    prior = tmp_path / "prior-rollout.jsonl"
    prior.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "old"}}) + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "user_message"}}) + "\n",
        encoding="utf-8",
    )
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "codex-forked2",
        "cwd": str(cwd),
        "source": "resume",
        "transcript_path": str(prior),
    }
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert codex_hook.main([]) == 0

    metadata = json.loads((plugin_home / "sessions" / "codex-forked2" / "metadata.json").read_text())
    assert metadata["forked_from_transcript"] == str(prior)
    assert metadata["forked_at_offset"] == prior.stat().st_size
    assert metadata["forked_at_record_count"] == 2


def test_codex_turn_end_maps_payload_to_checkpoint(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    codex_home = tmp_path / "home" / ".codex"
    cwd = tmp_path / "work"
    transcript = tmp_path / "rollout.jsonl"
    codex_home.mkdir(parents=True)
    cwd.mkdir()
    (codex_home / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"turn_id": "turn-0", "message": "previous"}),
                json.dumps({"type": "event_msg", "payload": {"turn_id": "turn-1", "type": "user_message", "message": "current"}}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    payload = {
        "hook_event_name": "Stop",
        "session_id": "codex-s1",
        "cwd": str(cwd),
        "turn_id": "turn-1",
        "transcript_path": str(transcript),
        "model": "gpt-test",
        "permission_mode": "plan",
        "last_assistant_message": "done",
    }
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert codex_hook.main([]) == 0

    store = CheckpointStore(plugin_home / "sessions" / "codex-s1")
    manifest = store.read_manifest(0)
    env = environment_from_blob(manifest.env_ref, store)
    assert env.provider == "codex"
    assert env.model == "gpt-test"
    assert env.permission_mode == "plan"
    assert "config.toml" in env.settings
    assert manifest.trajectory_ref is not None
    assert manifest.trajectory_ref.transcript_path == str(transcript)
    assert manifest.trajectory_ref.record_count == 1
    assert manifest.user_message_preview == "current"
    assert b'"message": "current"' in store.read_trajectory_slice(manifest.trajectory_ref)
    assert not (plugin_home / "sessions" / "codex-s1" / "trajectory.jsonl").exists()


def test_codex_reference_includes_intervening_records_until_next_turn(tmp_path):
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "event_msg", "payload": {"turn_id": "turn-1", "type": "task_started"}}),
                json.dumps({"type": "response_item", "payload": {"message": "assistant"}}),
                json.dumps({"type": "event_msg", "payload": {"turn_id": "turn-1", "type": "task_complete"}}),
                json.dumps({"type": "event_msg", "payload": {"turn_id": "turn-2", "type": "task_started"}}),
                "",
            ]
        ),
        encoding="utf-8",
    )

    ref = codex_hook._trajectory_ref(
        {"transcript_path": str(transcript), "turn_id": "turn-1"},
        provider="codex",
    )

    assert ref is not None
    assert ref.record_count == 3
    assert transcript.read_bytes()[ref.start_offset : ref.end_offset].count(b"\n") == 3


def test_codex_turn_claims_leading_keyless_user_prompt(tmp_path):
    """B3: a turn's user prompt is a key-less response_item emitted *before* its
    task_started. It must be attributed to that turn, not the previous one."""
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "event_msg", "payload": {"turn_id": "turn-1", "type": "task_started"}}),
                json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": "answer-1"}}),
                json.dumps({"type": "event_msg", "payload": {"turn_id": "turn-1", "type": "task_complete"}}),
                # turn-2's user prompt carries no turn_id and precedes task_started.
                json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": "prompt-2"}}),
                json.dumps({"type": "event_msg", "payload": {"turn_id": "turn-2", "type": "task_started"}}),
                json.dumps({"type": "turn_context", "payload": {"type": "turn_context", "turn_id": "turn-2"}}),
                "",
            ]
        ),
        encoding="utf-8",
    )

    turn1 = codex_hook._trajectory_ref(
        {"transcript_path": str(transcript), "turn_id": "turn-1"}, provider="codex"
    )
    turn2 = codex_hook._trajectory_ref(
        {"transcript_path": str(transcript), "turn_id": "turn-2"}, provider="codex"
    )

    assert turn1 is not None and turn2 is not None
    data = transcript.read_bytes()
    turn1_bytes = data[turn1.start_offset : turn1.end_offset]
    turn2_bytes = data[turn2.start_offset : turn2.end_offset]
    # The prompt belongs to turn-2, not the tail of turn-1.
    assert b"prompt-2" not in turn1_bytes
    assert b"prompt-2" in turn2_bytes
    # Slices are contiguous and non-overlapping.
    assert turn1.end_offset == turn2.start_offset


def test_codex_subagent_stop_creates_separate_checkpoint(tmp_path, monkeypatch):
    """B4: Codex subagent end checkpoints under a derived session, not the parent."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    transcript = tmp_path / "sub-rollout.jsonl"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "event_msg", "payload": {"turn_id": "t-1", "type": "task_started"}}),
                json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": "sub-task"}}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": "codex-parent",
        "cwd": str(cwd),
        "agent_id": "agent-9",
        "agent_type": "Plan",
        "turn_id": "t-1",
        "transcript_path": str(transcript),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert codex_hook.main([]) == 0

    assert CheckpointStore(plugin_home / "sessions" / "codex-parent").list_manifests() == []
    sub_store = CheckpointStore(plugin_home / "sessions" / "codex-parent--subagent-agent-9")
    manifests = sub_store.list_manifests()
    assert len(manifests) == 1
    metadata = json.loads((sub_store.session_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["lineage"]["parent_session_id"] == "codex-parent"
    assert metadata["lineage"]["agent_type"] == "Plan"


def test_codex_subagent_slices_agent_transcript_not_parent(tmp_path, monkeypatch):
    """C1: SubagentStop must slice `agent_transcript_path`, not the parent rollout."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    parent_transcript = tmp_path / "parent-rollout.jsonl"
    agent_transcript = tmp_path / "agent-rollout.jsonl"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    parent_transcript.write_text(
        json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": "PARENT-ONLY"}}) + "\n",
        encoding="utf-8",
    )
    agent_transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "event_msg", "payload": {"turn_id": "t-1", "type": "task_started"}}),
                json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": "SUBAGENT-ONLY"}}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": "codex-parent",
        "cwd": str(cwd),
        "agent_id": "agent-9",
        "turn_id": "t-1",
        "transcript_path": str(parent_transcript),
        "agent_transcript_path": str(agent_transcript),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert codex_hook.main([]) == 0

    sub_store = CheckpointStore(plugin_home / "sessions" / "codex-parent--subagent-agent-9")
    manifest = sub_store.list_manifests()[0]
    assert manifest.trajectory_ref.transcript_path == str(agent_transcript.resolve())
    sliced = sub_store.read_trajectory_slice(manifest.trajectory_ref)
    assert b"SUBAGENT-ONLY" in sliced
    assert b"PARENT-ONLY" not in sliced


def test_codex_tool_events_do_not_create_checkpoint(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    tool_payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "codex-s1",
        "cwd": str(cwd),
        "turn_id": "provider-turn-1",
        "tool_name": "Bash",
        "tool_input": {"command": "pwd"},
        "tool_response": str(cwd),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(tool_payload)))
    assert codex_hook.main([]) == 0

    store = CheckpointStore(plugin_home / "sessions" / "codex-s1")
    assert store.list_manifests() == []
    assert not store.trajectory_path.exists()


def test_codex_stop_without_transcript_does_not_copy_trajectory(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    payload = {
        "hook_event_name": "Stop",
        "session_id": "codex-s1",
        "cwd": str(cwd),
        "last_assistant_message": "done",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert codex_hook.main([]) == 0

    store = CheckpointStore(plugin_home / "sessions" / "codex-s1")
    manifest = store.read_manifest(0)
    assert manifest.trajectory_ref is not None
    assert manifest.trajectory_ref.record_count == 0
    assert not store.trajectory_path.exists()


def test_subagent_ref_captures_full_conversation_after_leading_metas(tmp_path):
    """H4: a subagent's dedicated rollout has stacked inherited session_meta at
    the head, then the subagent's OWN turns. The capture must start right after
    the leading meta block and cover all own turns through EOF — not just the
    last turn."""
    from checkpoint_plugin.integrations.codex_hook import _subagent_trajectory_ref

    rollout = tmp_path / "rollout-sub.jsonl"
    lines = [
        json.dumps({"type": "session_meta", "payload": {"id": "SUB", "forked_from_id": "PARENT"}}),
        json.dumps({"type": "session_meta", "payload": {"id": "PARENT", "forked_from_id": "GRAND"}}),
        json.dumps({"type": "session_meta", "payload": {"id": "GRAND"}}),
        json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "own-1"}}),
        json.dumps({"type": "response_item", "payload": {"role": "user", "type": "message"}}),
        json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "own-2"}}),
        json.dumps({"type": "response_item", "payload": {"role": "user", "type": "message"}}),
    ]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    rollout.write_bytes(data)
    # SubagentStop reports only the LAST turn id; the old code would slice from there.
    payload = {"turn_id": "own-2"}
    ref = _subagent_trajectory_ref(payload, str(rollout))
    assert ref is not None
    # Slice starts after the 3 leading metas (their combined byte length).
    meta_bytes = sum(len((lines[i] + "\n").encode("utf-8")) for i in range(3))
    assert ref.start_offset == meta_bytes
    assert ref.end_offset == len(data)
    # Covers BOTH own turns (4 records: 2 task_started + 2 user messages).
    assert ref.record_count == 4


def test_codex_startup_fork_records_lineage_from_session_meta(tmp_path, monkeypatch):
    """M2: a Codex fork arrives source=startup; lineage must still be recorded by
    reading the new rollout's session_meta.forked_from_id and discovering the
    parent rollout."""
    plugin_home = tmp_path / "plugin"
    codex_home = tmp_path / "codex"
    cwd = tmp_path / "work"
    cwd.mkdir()
    # Parent rollout discoverable by the rollout-<ts>-<id>.jsonl convention.
    parent_dir = codex_home / "sessions" / "2026" / "05" / "30"
    parent_dir.mkdir(parents=True)
    parent = parent_dir / "rollout-2026-05-30T00-00-00-PARENTID.jsonl"
    parent.write_bytes(
        (json.dumps({"type": "session_meta", "payload": {"id": "PARENTID"}}) + "\n"
         + json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n").encode("utf-8")
    )
    # The new forked session's own rollout points at the parent via forked_from_id.
    own = tmp_path / "rollout-2026-05-30T01-00-00-FORKED.jsonl"
    own.write_bytes(
        (json.dumps({"type": "session_meta", "payload": {"id": "FORKED", "forked_from_id": "PARENTID"}}) + "\n").encode("utf-8")
    )
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "FORKED",
        "cwd": str(cwd),
        "source": "startup",
        "transcript_path": str(own),
    }
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert codex_hook.main([]) == 0

    metadata = json.loads((plugin_home / "sessions" / "FORKED" / "metadata.json").read_text())
    # P6-16: a fork that arrived disguised as source="startup" is normalized to "fork".
    assert metadata["source"] == "fork"
    assert metadata["forked_from_transcript"] == str(parent)
    # P6-10: anchor at the fork's OWN inlined-prefix length (drift-free), not the
    # parent's live EOF. At SessionStart the fork file holds only its inlined prefix.
    assert metadata["forked_at_offset"] == own.stat().st_size
    assert metadata["forked_at_record_count"] == 1


def test_codex_resume_resolves_ancestor_not_self_and_anchors_on_parent(tmp_path, monkeypatch):
    """F6/F7: a codex resume's SessionStart transcript_path is the resume's OWN
    rollout (first session_meta carries forked_from_id). Lineage must resolve the
    true ancestor (not self-reference) and anchor on the PARENT's EOF (the branch
    point), not the inflated self-file length which already holds the resume's own
    new turns by hook time."""
    plugin_home = tmp_path / "plugin"
    codex_home = tmp_path / "codex"
    cwd = tmp_path / "work"
    cwd.mkdir()
    parent_dir = codex_home / "sessions" / "2026" / "05" / "30"
    parent_dir.mkdir(parents=True)
    parent = parent_dir / "rollout-2026-05-30T00-00-00-ANCESTOR.jsonl"
    parent.write_bytes(
        (json.dumps({"type": "session_meta", "payload": {"id": "ANCESTOR"}}) + "\n"
         + json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n"
         + json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n").encode("utf-8")
    )
    # The resume's OWN rollout: inlines the ancestor head meta + ancestor history +
    # its own new turn (so the self-file is LONGER than the true branch point).
    own = parent_dir / "rollout-2026-05-30T02-00-00-RESUMED.jsonl"
    own.write_bytes(
        (json.dumps({"type": "session_meta", "payload": {"id": "RESUMED", "forked_from_id": "ANCESTOR"}}) + "\n"
         + json.dumps({"type": "session_meta", "payload": {"id": "ANCESTOR"}}) + "\n"
         + json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n"
         + json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n"
         + json.dumps({"type": "event_msg", "payload": {"type": "task_started", "note": "own new turn"}}) + "\n").encode("utf-8")
    )
    payload = {
        "hook_event_name": "SessionStart",
        "session_id": "RESUMED",
        "cwd": str(cwd),
        "source": "resume",
        "transcript_path": str(own),
    }
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert codex_hook.main([]) == 0

    metadata = json.loads((plugin_home / "sessions" / "RESUMED" / "metadata.json").read_text())
    # source stays "resume" (NOT relabeled to "fork").
    assert metadata["source"] == "resume"
    # F6: ancestor resolved to the PARENT rollout, not the self-referential own path.
    assert metadata["forked_from_transcript"] == str(parent)
    assert "RESUMED" not in metadata["forked_from_transcript"]
    # F7: anchor on the parent EOF (branch point), not the longer self-file.
    assert metadata["forked_at_offset"] == parent.stat().st_size
    assert metadata["forked_at_record_count"] == 3
    assert metadata["forked_at_offset"] < own.stat().st_size  # self-file would overshoot


def test_codex_subagent_settle_captures_late_flushed_task_complete(tmp_path, monkeypatch):
    """F12-codex: SubagentStop fires before codex flushes the turn-closing
    `task_complete` event. The hook settles (polls until the tail is task_complete)
    before the slice, so the closing event is captured rather than truncated."""
    import threading
    import time

    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    agent_transcript = tmp_path / "agent-rollout.jsonl"
    # At SubagentStop time the closing task_complete is NOT yet present.
    agent_transcript.write_text(
        json.dumps({"type": "event_msg", "payload": {"turn_id": "t-1", "type": "task_started"}}) + "\n"
        + json.dumps({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": "DELIVERABLE"}}) + "\n",
        encoding="utf-8",
    )

    def _flush_task_complete_late():
        time.sleep(0.15)
        with agent_transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t-1"}}) + "\n")

    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CHECKPOINT_SIDECHAIN_SETTLE_TIMEOUT", "2.0")
    monkeypatch.setenv("CHECKPOINT_SIDECHAIN_SETTLE_POLL", "0.02")
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": "codex-parent",
        "cwd": str(cwd),
        "agent_id": "agent-late",
        "agent_transcript_path": str(agent_transcript),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    t = threading.Thread(target=_flush_task_complete_late)
    t.start()
    try:
        assert codex_hook.main([]) == 0
    finally:
        t.join()

    sub_store = CheckpointStore(plugin_home / "sessions" / "codex-parent--subagent-agent-late")
    ref = sub_store.list_manifests()[0].trajectory_ref
    sliced = sub_store.read_trajectory_slice(ref)
    assert b"task_complete" in sliced, "late-flushed task_complete must be captured after settle"
    assert ref.end_offset == agent_transcript.stat().st_size
