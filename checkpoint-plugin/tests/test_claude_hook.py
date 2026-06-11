import io
import json

from checkpoint_plugin.env.collector import environment_from_blob
from checkpoint_plugin.integrations import claude_code_hook
from checkpoint_plugin.store import CheckpointStore


def test_claude_tool_events_do_not_create_checkpoint(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "claude-s1")

    tool_payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "claude-s1",
        "cwd": str(cwd),
        "tool_name": "Read",
        "tool_input": {"file_path": str(cwd / "AGENTS.md")},
        "tool_response": {"file": {"content": "agent"}},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(tool_payload)))
    assert claude_code_hook.main(["turn_end"]) == 0

    store = CheckpointStore(plugin_home / "sessions" / "claude-s1")
    assert store.list_manifests() == []
    assert not store.trajectory_path.exists()


def test_claude_stop_records_trajectory_reference(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("agent", encoding="utf-8")
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"turnId": "provider-turn-1", "message": "tool"}),
                json.dumps({"turnId": "provider-turn-1", "message": "done"}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "claude-s1")
    stop_payload = {
        "hook_event_name": "Stop",
        "session_id": "claude-s1",
        "cwd": str(cwd),
        "turnId": "provider-turn-1",
        "transcript_path": str(transcript),
        "last_assistant_message": "done",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stop_payload)))
    assert claude_code_hook.main(["turn_end"]) == 0

    store = CheckpointStore(plugin_home / "sessions" / "claude-s1")
    manifests = store.list_manifests()
    assert len(manifests) == 1
    assert manifests[0].trajectory_ref is not None
    assert manifests[0].trajectory_ref.record_count == 2
    assert store.read_trajectory_slice(manifests[0].trajectory_ref).count(b"\n") == 2


def test_claude_slices_by_prompt_id(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "mode", "mode": "default"}),
                json.dumps({"type": "permission-mode", "permissionMode": "default"}),
                json.dumps({"type": "user", "promptId": "p-1", "message": {"role": "user", "content": "first"}}),
                json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "ok"}}),
                json.dumps({"type": "system", "subtype": "stop_hook_summary"}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "claude-prompt")
    payload = {
        "hook_event_name": "Stop",
        "session_id": "claude-prompt",
        "cwd": str(cwd),
        "transcript_path": str(transcript),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert claude_code_hook.main(["turn_end"]) == 0

    store = CheckpointStore(plugin_home / "sessions" / "claude-prompt")
    ref = store.list_manifests()[0].trajectory_ref
    assert ref is not None
    assert ref.start_offset == 0  # turn 0 anchors at beginning of file
    assert ref.record_count == 5

    # Append turn 2 and confirm slicer picks up the new promptId boundary
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "user", "promptId": "p-2", "message": {"role": "user", "content": "second"}}) + "\n")
        handle.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "ok"}}) + "\n")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert claude_code_hook.main(["turn_end"]) == 0
    manifests = store.list_manifests()
    assert len(manifests) == 2
    second_ref = manifests[1].trajectory_ref
    assert second_ref is not None
    assert second_ref.start_offset > 0
    assert second_ref.record_count == 2


def test_claude_subagent_stop_creates_separate_checkpoint(tmp_path, monkeypatch):
    """B4: a subagent is checkpointed as its own session, not on the parent."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    # Claude stores subagents under <project>/<parent-session>/subagents/agent-<id>.jsonl
    parent_dir = tmp_path / "proj" / "parent-session"
    sub_dir = parent_dir / "subagents"
    sub_dir.mkdir(parents=True)
    parent_transcript = parent_dir.with_suffix(".jsonl")
    parent_transcript.write_text(
        json.dumps({"type": "user", "promptId": "p-1", "message": {"role": "user", "content": "parent"}}) + "\n",
        encoding="utf-8",
    )
    sub_transcript = sub_dir / "agent-abc123.jsonl"
    sub_transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "promptId": "sp-1", "isSidechain": True, "agentId": "abc123", "message": {"role": "user", "content": "sub-task"}}),
                json.dumps({"type": "assistant", "isSidechain": True, "agentId": "abc123", "message": {"role": "assistant", "content": "sub-done"}}),
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "parent-session")
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": "parent-session",
        "cwd": str(cwd),
        "agent_id": "abc123",
        "agent_type": "Explore",
        "transcript_path": str(parent_transcript),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert claude_code_hook.main(["subagent_end"]) == 0

    # Parent session got no checkpoint from the subagent event.
    parent_store = CheckpointStore(plugin_home / "sessions" / "parent-session")
    assert parent_store.list_manifests() == []

    # Subagent has its own session, referencing its own transcript file.
    sub_store = CheckpointStore(plugin_home / "sessions" / "parent-session--subagent-abc123")
    manifests = sub_store.list_manifests()
    assert len(manifests) == 1
    ref = manifests[0].trajectory_ref
    assert ref is not None
    assert ref.transcript_path == str(sub_transcript.resolve())
    assert b"sub-task" in sub_store.read_trajectory_slice(ref)

    metadata = json.loads((sub_store.session_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["lineage"]["parent_session_id"] == "parent-session"
    assert metadata["lineage"]["agent_id"] == "abc123"
    assert metadata["lineage"]["agent_type"] == "Explore"
    # F12: the sidechain's observed size (== the sliced end_offset) and mtime are
    # recorded so a later flush (file grown past this size) is detectable as a stale
    # slice rather than a silent truncation.
    assert metadata["lineage"]["sidechain_observed_size"] == ref.end_offset
    assert metadata["lineage"]["sidechain_observed_size"] <= sub_transcript.stat().st_size
    assert isinstance(metadata["lineage"]["sidechain_observed_mtime"], str)
    assert metadata["lineage"]["sidechain_observed_mtime"].endswith("+00:00")


def test_claude_subagent_inherits_parent_model(tmp_path, monkeypatch):
    """G2: SubagentStop carries no model; inherit the parent's pinned session_env."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    parent_dir = tmp_path / "proj" / "parent-session"
    sub_dir = parent_dir / "subagents"
    sub_dir.mkdir(parents=True)
    parent_transcript = parent_dir.with_suffix(".jsonl")
    parent_transcript.write_text(
        json.dumps({"type": "user", "promptId": "p-1", "message": {"role": "user", "content": "parent"}}) + "\n",
        encoding="utf-8",
    )
    sub_transcript = sub_dir / "agent-abc123.jsonl"
    sub_transcript.write_text(
        json.dumps({"type": "user", "promptId": "sp-1", "isSidechain": True, "message": {"role": "user", "content": "sub"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "parent-session")
    # Parent SessionStart pins the model (delivered only here).
    start_payload = {
        "hook_event_name": "SessionStart",
        "session_id": "parent-session",
        "cwd": str(cwd),
        "source": "startup",
        "model": "claude-opus-4-8",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(start_payload)))
    assert claude_code_hook.main(["session_start"]) == 0

    # SubagentStop carries no model field, mirroring the real contract.
    sub_payload = {
        "hook_event_name": "SubagentStop",
        "session_id": "parent-session",
        "cwd": str(cwd),
        "agent_id": "abc123",
        "agent_type": "Explore",
        "transcript_path": str(parent_transcript),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(sub_payload)))
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)
    assert claude_code_hook.main(["subagent_end"]) == 0

    sub_store = CheckpointStore(plugin_home / "sessions" / "parent-session--subagent-abc123")
    env = environment_from_blob(sub_store.list_manifests()[0].env_ref, sub_store)
    assert env.model == "claude-opus-4-8"


def test_claude_subagent_without_sidechain_file_does_not_slice_parent(tmp_path, monkeypatch):
    """P11-ZOMBIE-1: when no dedicated subagents/agent-*.jsonl exists, the subagent
    checkpoint must record lineage metadata only — no manifest, no fs/env blobs."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    # Parent transcript exists; NO subagents/ dir is created alongside it.
    parent_transcript = tmp_path / "proj" / "parent-session.jsonl"
    parent_transcript.parent.mkdir(parents=True)
    parent_transcript.write_text(
        json.dumps({"type": "user", "promptId": "p-1", "isSidechain": False, "message": {"role": "user", "content": "PARENT-MAIN-THREAD"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "parent-session")
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": "parent-session",
        "cwd": str(cwd),
        "agent_id": "ghost",
        "agent_type": "Explore",
        "transcript_path": str(parent_transcript),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert claude_code_hook.main(["subagent_end"]) == 0

    sub_session_dir = plugin_home / "sessions" / "parent-session--subagent-ghost"
    # Metadata is written with lineage (session_start was called)
    metadata = json.loads((sub_session_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["lineage"]["parent_session_id"] == "parent-session"
    # P6-9: surface WHY the slice is empty so a reader doesn't mistake it for a bug.
    assert metadata["lineage"]["capture_status"] == "no_sidechain_file"
    # SA4: timestamp and reason fields are present
    assert "no_sidechain_file_timestamp" in metadata["lineage"]
    assert isinstance(metadata["lineage"]["no_sidechain_file_timestamp"], (int, float))
    assert metadata["lineage"]["no_sidechain_file_reason"] == "no_transcript_path"
    # P11-ZOMBIE-1: no manifest is written — no expensive fs/env snapshot.
    sub_store = CheckpointStore(sub_session_dir)
    assert sub_store.list_manifests() == []
    # No blob files should be written (no fs/env collection ran).
    blob_files = list((sub_session_dir / "blobs").rglob("*")) if (sub_session_dir / "blobs").exists() else []
    assert all(f.is_dir() for f in blob_files), "Expected no blob files for zombie subagent"


def test_no_sidechain_file_reasons(tmp_path, monkeypatch):
    """SA4: no_sidechain_file metadata includes timestamp and reason."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "parent-session")

    # Case 1: no_transcript_path (transcript_path resolves to None)
    parent_transcript = tmp_path / "proj" / "parent-session.jsonl"
    parent_transcript.parent.mkdir(parents=True)
    parent_transcript.write_text(
        json.dumps({"type": "user", "promptId": "p-1", "message": {"role": "user", "content": "hi"}}) + "\n",
        encoding="utf-8",
    )
    payload1 = {
        "hook_event_name": "SubagentStop",
        "session_id": "parent-session",
        "cwd": str(cwd),
        "agent_id": "agent1",
        "agent_type": "Explore",
        "transcript_path": str(parent_transcript),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload1)))
    assert claude_code_hook.main(["subagent_end"]) == 0

    metadata1 = json.loads((plugin_home / "sessions" / "parent-session--subagent-agent1" / "metadata.json").read_text(encoding="utf-8"))
    assert metadata1["lineage"]["capture_status"] == "no_sidechain_file"
    assert metadata1["lineage"]["no_sidechain_file_reason"] == "no_transcript_path"
    assert "no_sidechain_file_timestamp" in metadata1["lineage"]

    # Case 2: file_not_found (transcript_path exists but file doesn't)
    subagent_path = tmp_path / "proj" / "parent-session" / "subagents" / "agent-agent2.jsonl"
    payload2 = {
        "hook_event_name": "SubagentStop",
        "session_id": "parent-session",
        "cwd": str(cwd),
        "agent_id": "agent2",
        "agent_type": "Explore",
        "transcript_path": str(subagent_path),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload2)))
    assert claude_code_hook.main(["subagent_end"]) == 0

    metadata2 = json.loads((plugin_home / "sessions" / "parent-session--subagent-agent2" / "metadata.json").read_text(encoding="utf-8"))
    assert metadata2["lineage"]["capture_status"] == "no_sidechain_file"
    assert metadata2["lineage"]["no_sidechain_file_reason"] == "file_not_found"
    assert "no_sidechain_file_timestamp" in metadata2["lineage"]

    # Case 3: file_empty_or_unreadable (file exists but is empty)
    subagent_path3 = tmp_path / "proj" / "parent-session" / "subagents" / "agent-agent3.jsonl"
    subagent_path3.parent.mkdir(parents=True, exist_ok=True)
    subagent_path3.write_text("", encoding="utf-8")
    payload3 = {
        "hook_event_name": "SubagentStop",
        "session_id": "parent-session",
        "cwd": str(cwd),
        "agent_id": "agent3",
        "agent_type": "Explore",
        "transcript_path": str(subagent_path3),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload3)))
    assert claude_code_hook.main(["subagent_end"]) == 0

    metadata3 = json.loads((plugin_home / "sessions" / "parent-session--subagent-agent3" / "metadata.json").read_text(encoding="utf-8"))
    assert metadata3["lineage"]["capture_status"] == "no_sidechain_file"
    assert metadata3["lineage"]["no_sidechain_file_reason"] == "file_empty_or_unreadable"
    assert "no_sidechain_file_timestamp" in metadata3["lineage"]


def test_claude_model_captured_from_session_start(tmp_path, monkeypatch):
    """Claude delivers `model` only to SessionStart; Stop must still pin it (B1)."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    transcript.write_text(
        json.dumps({"type": "user", "promptId": "p-1", "message": {"role": "user", "content": "hi"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "claude-model")
    for name in ("ANTHROPIC_MODEL", "CLAUDE_MODEL", "OPENAI_MODEL", "CODEX_MODEL"):
        monkeypatch.delenv(name, raising=False)

    start_payload = {
        "hook_event_name": "SessionStart",
        "session_id": "claude-model",
        "cwd": str(cwd),
        "source": "startup",
        "model": "claude-sonnet-4-6",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(start_payload)))
    assert claude_code_hook.main(["session_start"]) == 0

    # Stop payload carries no model field, mirroring the real hook contract.
    stop_payload = {
        "hook_event_name": "Stop",
        "session_id": "claude-model",
        "cwd": str(cwd),
        "transcript_path": str(transcript),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stop_payload)))
    assert claude_code_hook.main(["turn_end"]) == 0

    store = CheckpointStore(plugin_home / "sessions" / "claude-model")
    manifest = store.list_manifests()[0]
    env = environment_from_blob(manifest.env_ref, store)
    assert env.model == "claude-sonnet-4-6"


def test_claude_seeds_payload_fields(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "claude-fields")
    monkeypatch.delenv("CLAUDE_PERMISSION_MODE", raising=False)
    monkeypatch.delenv("CLAUDE_EFFORT", raising=False)
    monkeypatch.delenv("CLAUDE_AGENT_TYPE", raising=False)
    payload = {
        "hook_event_name": "Stop",
        "session_id": "claude-fields",
        "cwd": str(cwd),
        "transcript_path": str(tmp_path / "missing.jsonl"),
        "permission_mode": "plan",
        "effort": {"level": "high"},
        "agent_type": "Explore",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert claude_code_hook.main(["turn_end"]) == 0

    store = CheckpointStore(plugin_home / "sessions" / "claude-fields")
    manifest = store.list_manifests()[0]
    env = environment_from_blob(manifest.env_ref, store)
    assert env.permission_mode == "plan"
    assert env.effort == "high"
    assert env.agent_type == "Explore"


def test_claude_mcp_delta_overrides_stale_config_status(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")
    monkeypatch.setenv("CLAUDE_SESSION_ID", "claude-mcp")
    home.mkdir()
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {"context7": {"type": "stdio", "command": "npx"}},
                "projects": {str(cwd): {"disabledMcpServers": ["context7"]}},
            }
        ),
        encoding="utf-8",
    )
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "user", "promptId": "p-1", "message": {"role": "user", "content": "hi"}}),
                json.dumps(
                    {
                        "type": "attachment",
                        "attachment": {
                            "type": "deferred_tools_delta",
                            "addedNames": ["mcp__context7__query-docs"],
                            "removedNames": [],
                            "readdedNames": ["mcp__context7__resolve-library-id"],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "attachment",
                        "attachment": {
                            "type": "mcp_instructions_delta",
                            "addedNames": ["context7"],
                            "removedNames": [],
                        },
                    }
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = {
        "hook_event_name": "Stop",
        "session_id": "claude-mcp",
        "cwd": str(cwd),
        "transcript_path": str(transcript),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert claude_code_hook.main(["turn_end"]) == 0

    store = CheckpointStore(plugin_home / "sessions" / "claude-mcp")
    manifest = store.list_manifests()[0]
    env = environment_from_blob(manifest.env_ref, store)
    assert env.mcp_servers["context7"] == "active"



def test_claude_subagent_settle_captures_late_flushed_deliverable(tmp_path, monkeypatch):
    """F12: SubagentStop can fire before the subagent's final assistant deliverable
    is flushed. The hook now settles (polls until the tail is a complete assistant
    record) before slicing, so the deliverable is captured rather than truncated."""
    import threading
    import time

    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    parent_dir = tmp_path / "proj" / "parent-session"
    sub_dir = parent_dir / "subagents"
    sub_dir.mkdir(parents=True)
    parent_transcript = parent_dir.with_suffix(".jsonl")
    parent_transcript.write_text(
        json.dumps({"type": "user", "promptId": "p-1", "message": {"role": "user", "content": "parent"}}) + "\n",
        encoding="utf-8",
    )
    sub_transcript = sub_dir / "agent-late.jsonl"
    # At SubagentStop time the deliverable (assistant/end_turn) is NOT yet present:
    # the file ends with the user prompt + attachments, no closing assistant record.
    sub_transcript.write_text(
        json.dumps({"type": "user", "promptId": "sp-1", "isSidechain": True, "agentId": "late", "message": {"role": "user", "content": "do research"}}) + "\n",
        encoding="utf-8",
    )

    def _flush_deliverable_late():
        time.sleep(0.15)
        with sub_transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "assistant", "isSidechain": True, "agentId": "late", "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "FINDINGS"}]}}) + "\n")

    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("CLAUDE_SESSION_ID", "parent-session")
    monkeypatch.setenv("CHECKPOINT_SIDECHAIN_SETTLE_TIMEOUT", "2.0")
    monkeypatch.setenv("CHECKPOINT_SIDECHAIN_SETTLE_POLL", "0.02")
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": "parent-session",
        "cwd": str(cwd),
        "agent_id": "late",
        "agent_type": "general-purpose",
        "transcript_path": str(parent_transcript),
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    t = threading.Thread(target=_flush_deliverable_late)
    t.start()
    try:
        assert claude_code_hook.main(["subagent_end"]) == 0
    finally:
        t.join()

    sub_store = CheckpointStore(plugin_home / "sessions" / "parent-session--subagent-late")
    ref = sub_store.list_manifests()[0].trajectory_ref
    captured = sub_store.read_trajectory_slice(ref)
    assert b"FINDINGS" in captured, "late-flushed deliverable must be captured after settle"
    assert ref.end_offset == sub_transcript.stat().st_size
