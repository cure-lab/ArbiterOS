"""Test OpenCode resume ID ordering and continuation logic."""

import json
import tempfile
from pathlib import Path

import pytest


def test_opencode_resume_generates_time_ordered_message_ids(tmp_path):
    """OpenCode requires time-ordered message IDs for continuation logic.

    OpenCode's session continuation logic (packages/opencode/src/session/prompt.ts)
    uses lexicographic ID comparison to find the "latest" user and assistant messages:

        if (info.role === "user" && (!user || info.id > user.id)) user = info
        if (info.role === "assistant" && (!assistant || info.id > assistant.id)) assistant = info

    If IDs are random (uuid4), the ordering breaks and the loop incorrectly identifies
    an intermediate assistant message as "latest", potentially triggering infinite
    continuation if that message has finish="tool-calls".

    The fix uses uuid7 (time-ordered) instead of uuid4 for message and part IDs.
    """
    from checkpoint_plugin.coordinator import CheckpointCoordinator

    # Create a minimal OpenCode session with 3 turns
    session_dir = tmp_path / "plugin" / "sessions" / "test_oc_session"
    session_dir.mkdir(parents=True)

    coordinator = CheckpointCoordinator(session_id="test_oc_session", cwd=tmp_path)
    coordinator.session_dir = session_dir

    # Simulate 3 turns with different finish reasons
    turns = [
        {"user": "hello", "assistant": "Hi!", "finish": "stop"},
        {"user": "read file.txt", "assistant": "", "finish": "tool-calls"},
        {"user": "", "assistant": "File contains: test", "finish": "stop"},
    ]

    trajectory = []
    for i, turn in enumerate(turns):
        trajectory.append(
            json.dumps({
                "type": "turn",
                "user_message": turn["user"],
                "assistant_text": turn["assistant"],
                "created_ts": f"2026-06-03T10:00:{i:02d}+00:00",
                "metadata": {
                    "hook_payload": {
                        "finish": turn["finish"],
                    }
                }
            })
        )

    (session_dir / "trajectory.jsonl").write_text("\n".join(trajectory))
    (session_dir / "metadata.json").write_text(json.dumps({
        "session_id": "test_oc_session",
        "provider": "opencode",
        "cwd": str(tmp_path),
        "start_ts": "2026-06-03T10:00:00+00:00"
    }))
    (session_dir / "manifests").mkdir()
    (session_dir / "manifests" / "index.json").write_text(json.dumps({"0": "turn_0000.json"}))
    (session_dir / "manifests" / "turn_0000.json").write_text(json.dumps({
        "turn": 0,
        "trajectory_ref": None,
        "cwd": str(tmp_path),
    }))

    # Resume at turn 2 (all 3 turns should be in the import file)
    opencode_home = tmp_path / ".config" / "opencode"
    opencode_home.mkdir(parents=True)

    from checkpoint_plugin.resume import _write_opencode_session

    trajectory_bytes = (session_dir / "trajectory.jsonl").read_bytes()
    import_path = _write_opencode_session(
        opencode_home, tmp_path, "ses_new_resume", trajectory_bytes
    )

    assert import_path is not None
    import_data = json.loads(import_path.read_text())

    # Check that message IDs are in chronological order
    messages = import_data["messages"]
    assert len(messages) > 0

    msg_ids = [m["info"]["id"] for m in messages]
    sorted_ids = sorted(msg_ids)

    # Time-ordered IDs should sort lexicographically in chronological order
    assert msg_ids == sorted_ids, (
        f"Message IDs are not time-ordered!\n"
        f"Original: {msg_ids}\n"
        f"Sorted:   {sorted_ids}\n"
        f"This breaks OpenCode's continuation logic which uses ID comparison."
    )

    # Check that part IDs within each message are also ordered
    for i, msg in enumerate(messages):
        part_ids = [p["id"] for p in msg.get("parts", []) if isinstance(p, dict)]
        sorted_part_ids = sorted(part_ids)
        assert part_ids == sorted_part_ids, (
            f"Part IDs in message {i} are not time-ordered!\n"
            f"Original: {part_ids}\n"
            f"Sorted:   {sorted_part_ids}"
        )


def test_opencode_resume_sets_finish_stop_in_fallback_reconstruction():
    """Reconstructed messages must have finish="stop" to prevent infinite loops.

    When raw_messages are unavailable (pre-fix captures), the fallback
    _reconstruct_opencode_messages() builds minimal message structures.
    These MUST include finish="stop" on assistant messages to signal completion.

    Without finish, or with finish="tool-calls", OpenCode's continuation logic
    will immediately generate a new turn, causing an infinite loop.
    """
    from checkpoint_plugin.resume import _reconstruct_opencode_messages

    records = [
        {
            "type": "turn",
            "user_message": "hello",
            "assistant_text": "Hi! How can I help?",
            "created_ts": "2026-06-03T10:00:00+00:00",
        }
    ]

    messages = _reconstruct_opencode_messages(records, "ses_test")

    assert len(messages) == 2  # user + assistant

    assistant_msg = messages[1]
    assert assistant_msg["info"]["role"] == "assistant"
    assert assistant_msg["info"]["finish"] == "stop", (
        "Reconstructed assistant message must have finish='stop' "
        "to prevent infinite continuation loop"
    )
    assert "completed" in assistant_msg["info"]["time"], (
        "Reconstructed assistant message must have time.completed "
        "to signal the message is complete"
    )


def test_opencode_resume_preserves_parent_id_chain():
    """parentID references must be remapped to maintain message threading."""
    from checkpoint_plugin.resume import _write_opencode_session

    # Create trajectory with raw_messages that have parentID references
    raw_messages = [
        {
            "info": {"id": "msg_orig_001", "sessionID": "ses_orig", "role": "user", "time": {"created": 1000}, "agent": "build"},
            "parts": [{"id": "prt_001", "type": "text", "text": "hello", "messageID": "msg_orig_001", "sessionID": "ses_orig"}]
        },
        {
            "info": {
                "id": "msg_orig_002",
                "sessionID": "ses_orig",
                "role": "assistant",
                "parentID": "msg_orig_001",  # References first message
                "time": {"created": 2000, "completed": 3000},
                "finish": "stop",
                "mode": "build",
                "agent": "build",
                "modelID": "big-pickle",
                "providerID": "opencode",
                "path": {"cwd": "/test", "root": "/"},
                "cost": 0,
                "tokens": {"input": 10, "output": 5, "reasoning": 2, "cache": {"read": 0, "write": 0}}
            },
            "parts": [{"id": "prt_002", "type": "text", "text": "Hi!", "messageID": "msg_orig_002", "sessionID": "ses_orig"}]
        }
    ]

    trajectory = json.dumps({
        "type": "turn",
        "user_message": "hello",
        "assistant_text": "Hi!",
        "metadata": {
            "hook_payload": {
                "raw_messages": raw_messages,
                "session_info": {"id": "ses_orig", "slug": "test", "projectID": "global"}
            }
        }
    }).encode()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        opencode_home = tmppath / ".config" / "opencode"
        opencode_home.mkdir(parents=True)

        import_path = _write_opencode_session(
            opencode_home, tmppath, "ses_resumed", trajectory
        )

        import_data = json.loads(import_path.read_text())
        messages = import_data["messages"]

        # IDs should be remapped
        assert messages[0]["info"]["id"] != "msg_orig_001"
        assert messages[1]["info"]["id"] != "msg_orig_002"

        # But parentID reference should still point to the first message's NEW id
        parent_id = messages[1]["info"]["parentID"]
        assert parent_id == messages[0]["info"]["id"], (
            f"parentID chain broken: assistant parentID={parent_id}, "
            f"but user id={messages[0]['info']['id']}"
        )


def test_opencode_resume_preserves_session_info_fields(tmp_path):
    from checkpoint_plugin.resume import _write_opencode_session

    raw_messages = [
        {
            "info": {
                "id": "msg_user",
                "sessionID": "ses_orig",
                "role": "user",
                "time": {"created": 1000},
                "agent": "build",
            },
            "parts": [
                {
                    "id": "prt_user",
                    "type": "text",
                    "text": "hello",
                    "messageID": "msg_user",
                    "sessionID": "ses_orig",
                }
            ],
        },
        {
            "info": {
                "id": "msg_assistant",
                "sessionID": "ses_orig",
                "role": "assistant",
                "parentID": "msg_user",
                "time": {"created": 2000, "completed": 3000},
                "finish": "stop",
                "agent": "build",
                "path": {"cwd": "/old/cwd", "root": "/"},
            },
            "parts": [
                {
                    "id": "prt_assistant",
                    "type": "text",
                    "text": "Hi!",
                    "messageID": "msg_assistant",
                    "sessionID": "ses_orig",
                }
            ],
        },
    ]
    session_info = {
        "id": "ses_orig",
        "parentID": "ses_parent",
        "slug": "original-slug",
        "projectID": "project-old",
        "directory": "/old/cwd",
        "path": "old/path",
        "title": "Original title",
        "agent": "build",
        "model": {"id": "big-pickle", "providerID": "opencode"},
        "permission": [{"permission": "task", "action": "deny", "pattern": "*"}],
        "metadata": {"runtime": "kept"},
        "workspaceID": "workspace-1",
        "share": {"url": "https://example.com/share"},
        "revert": {"messageID": "msg_user"},
        "time": {"created": 1234, "updated": 2345},
    }
    trajectory = (
        json.dumps(
            {
                "type": "turn",
                "user_message": "hello",
                "assistant_text": "Hi!",
                "metadata": {
                    "hook_payload": {
                        "raw_messages": raw_messages,
                        "session_info": session_info,
                    }
                },
            }
        )
        + "\n"
    ).encode()

    opencode_home = tmp_path / ".config" / "opencode"
    opencode_home.mkdir(parents=True)
    import_path = _write_opencode_session(opencode_home, tmp_path, "ses_resumed", trajectory)

    assert import_path is not None
    import_data = json.loads(import_path.read_text())
    info = import_data["info"]
    assert info["id"] == "ses_resumed"
    assert "parentID" not in info
    assert info["directory"] == str(tmp_path)
    assert info["agent"] == "build"
    assert info["model"] == {"id": "big-pickle", "providerID": "opencode"}
    assert info["permission"] == [{"permission": "task", "action": "deny", "pattern": "*"}]
    assert info["metadata"] == {"runtime": "kept"}
    assert info["workspaceID"] == "workspace-1"
    assert info["share"] == {"url": "https://example.com/share"}
    assert info["revert"] == {"messageID": "msg_user"}
    assert info["time"]["created"] == 1234
    assert info["time"]["updated"] >= 1234
    assert all(
        msg["info"].get("path", {}).get("cwd") == str(tmp_path)
        for msg in import_data["messages"]
        if msg["info"].get("path")
    )


def test_opencode_resume_restores_session_messages_and_todos(tmp_path, monkeypatch):
    import sqlite3
    from checkpoint_plugin.resume import _write_opencode_session, restore_opencode_metadata

    raw_messages = [
        {
            "info": {"id": "msg_user", "sessionID": "ses_orig", "role": "user", "time": {"created": 1000}},
            "parts": [{"id": "prt_user", "type": "text", "text": "hello", "messageID": "msg_user", "sessionID": "ses_orig"}],
        },
        {
            "info": {
                "id": "msg_assistant",
                "sessionID": "ses_orig",
                "role": "assistant",
                "parentID": "msg_user",
                "time": {"created": 2000, "completed": 3000},
                "finish": "stop",
            },
            "parts": [{"id": "prt_assistant", "type": "text", "text": "Hi!", "messageID": "msg_assistant", "sessionID": "ses_orig"}],
        },
    ]
    trajectory = (
        json.dumps(
            {
                "type": "turn",
                "user_message": "hello",
                "assistant_text": "Hi!",
                "metadata": {
                    "hook_payload": {
                        "raw_messages": raw_messages,
                        "session_info": {"id": "ses_orig", "slug": "test", "projectID": "global"},
                        "session_messages": [
                            {
                                "id": "evt_orig",
                                "sessionID": "ses_orig",
                                "type": "model-switched",
                                "time": {"created": 111, "updated": 112},
                                "data": {"model": {"id": "big-pickle", "variant": "default"}},
                            }
                        ],
                        "todos": [
                            {
                                "sessionID": "ses_orig",
                                "content": "Keep todo",
                                "status": "pending",
                                "priority": "medium",
                                "position": 0,
                                "time": {"created": 211, "updated": 212},
                            }
                        ],
                    }
                },
            }
        )
        + "\n"
    ).encode()

    opencode_home = tmp_path / ".config" / "opencode"
    opencode_home.mkdir(parents=True)
    import_path = _write_opencode_session(opencode_home, tmp_path, "ses_resumed", trajectory)
    assert import_path is not None

    import_data = json.loads(import_path.read_text(encoding="utf-8"))
    assert import_data["session_messages"][0]["sessionID"] == "ses_resumed"
    assert import_data["session_messages"][0]["id"] != "evt_orig"
    assert import_data["todos"][0]["sessionID"] == "ses_resumed"

    data_dir = tmp_path / "data"
    db_dir = data_dir / "opencode"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "opencode.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE session_message ("
        "id TEXT PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL, "
        "time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE todo ("
        "session_id TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL, "
        "priority TEXT NOT NULL, position INTEGER NOT NULL, "
        "time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, "
        "PRIMARY KEY(session_id, position))"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("OPENCODE_DATA_DIR", str(data_dir))

    assert restore_opencode_metadata(import_path, "ses_resumed") == (1, 1)
    assert restore_opencode_metadata(import_path, "ses_resumed") == (1, 1)

    conn = sqlite3.connect(str(db_path))
    session_row = conn.execute(
        "SELECT session_id, type, time_created, time_updated, data FROM session_message"
    ).fetchone()
    todo_row = conn.execute(
        "SELECT session_id, content, status, priority, position, time_created, time_updated FROM todo"
    ).fetchone()
    conn.close()

    assert session_row[0] == "ses_resumed"
    assert session_row[1] == "model-switched"
    assert session_row[2:4] == (111, 112)
    assert json.loads(session_row[4])["model"]["variant"] == "default"
    assert todo_row == ("ses_resumed", "Keep todo", "pending", "medium", 0, 211, 212)


def test_opencode_runtime_config_carries_mcp_overlay_without_secrets(tmp_path):
    from checkpoint_plugin.resume import _materialize_runtime_config, _runtime_process_env
    from checkpoint_plugin.types import EnvironmentState

    target_env = EnvironmentState(
        provider="opencode",
        mcp_servers={"context7": "inactive", "filesystem": "active", "failed_server": "failed"},
        extra={
            "opencode_config_content": json.dumps(
                {
                    "mcp": {"context7": {"enabled": True, "type": "local"}},
                    "model": "opencode/model",
                    "provider": {"custom": {"options": {"apiKey": "***redacted***"}}},
                }
            ),
            "opencode_runtime_env": {
                "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
                "OPENCODE_PERMISSION": json.dumps({"bash": {"*": "ask"}}),
            },
        },
    )
    root = tmp_path / "env-state" / "ses_resumed"
    runtime_home = root / "opencode"
    runtime_home.mkdir(parents=True)
    (runtime_home / "opencode.json").write_text(
        json.dumps(
            {
                "mcp": {"context7": {"enabled": True, "type": "local"}},
                "model": "current/model",
                "provider": {"custom": {"options": {"apiKey": "secret-value"}}},
            }
        ),
        encoding="utf-8",
    )

    _materialize_runtime_config("opencode", runtime_home, target_env)
    runtime_env = _runtime_process_env("opencode", runtime_home, root, target_env, {})

    config = json.loads((runtime_home / "opencode.json").read_text(encoding="utf-8"))
    assert config["mcp"]["context7"]["enabled"] is False
    assert config["mcp"]["context7"]["type"] == "local"
    assert config["mcp"]["filesystem"] == {"enabled": True}
    assert config["model"] == "opencode/model"
    assert config["provider"]["custom"]["options"]["apiKey"] == "secret-value"
    assert "***redacted***" not in json.dumps(config)
    assert "failed_server" not in config["mcp"]
    assert runtime_env["OPENCODE_CONFIG_DIR"] == str(runtime_home)
    assert runtime_env["OPENCODE_DATA_DIR"] == str(root / "opencode-data")
    assert runtime_env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    assert "OPENCODE_PERMISSION" in runtime_env
    assert "OPENCODE_CONFIG_CONTENT" not in runtime_env


def test_opencode_runtime_config_rewrites_path_values(tmp_path, monkeypatch):
    from checkpoint_plugin.resume import _environment_for_runtime, _materialize_runtime_config, _runtime_path_map
    from checkpoint_plugin.env.providers import opencode_layout
    from checkpoint_plugin.types import EnvironmentState

    home = tmp_path / "home"
    source_home = home / ".config" / "opencode"
    source_cwd = tmp_path / "work"
    target_cwd = tmp_path / "work-copy"
    source_home.mkdir(parents=True)
    source_cwd.mkdir()
    target_cwd.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))

    source_provider = opencode_layout()
    root = tmp_path / "env-state" / "ses_resumed"
    runtime_home = root / "opencode"
    path_map = _runtime_path_map(source_provider, root, runtime_home)
    target_env = EnvironmentState(
        provider="opencode",
        extra={
            "cwd": str(source_cwd),
            "provider_home": str(source_home),
            "opencode_config_content": json.dumps(
                {
                    "plugin": [str(source_home / "plugin" / "checkpoint.ts")],
                    "instructions": str(source_cwd / "AGENTS.md"),
                    "skills": {"paths": [str(source_home / "skills")]},
                }
            ),
        },
    )

    runtime_env = _environment_for_runtime(target_env, path_map, target_cwd)
    _materialize_runtime_config("opencode", runtime_home, runtime_env)

    config = json.loads((runtime_home / "opencode.json").read_text(encoding="utf-8"))
    assert str(source_home / "plugin" / "checkpoint.ts") not in json.dumps(config)
    assert str(source_cwd / "AGENTS.md") not in json.dumps(config)
    assert config["plugin"] == [str(runtime_home / "plugin" / "checkpoint.ts")]
    assert config["instructions"] == str(target_cwd / "AGENTS.md")
    assert config["skills"]["paths"] == [str(runtime_home / "skills")]


def test_opencode_runtime_config_skips_redacted_only_config(tmp_path):
    from checkpoint_plugin.resume import _materialize_runtime_config
    from checkpoint_plugin.types import EnvironmentState

    runtime_home = tmp_path / "opencode"
    runtime_home.mkdir()
    existing = {"provider": {"custom": {"options": {"apiKey": "secret-value"}}}}
    (runtime_home / "opencode.json").write_text(json.dumps(existing), encoding="utf-8")
    target_env = EnvironmentState(
        provider="opencode",
        extra={
            "opencode_config_content": json.dumps(
                {"provider": {"custom": {"options": {"apiKey": "***redacted***"}}}}
            )
        },
    )

    _materialize_runtime_config("opencode", runtime_home, target_env)

    assert json.loads((runtime_home / "opencode.json").read_text(encoding="utf-8")) == existing


def test_opencode_runtime_config_preserves_empty_container_overrides(tmp_path):
    from checkpoint_plugin.resume import _materialize_runtime_config
    from checkpoint_plugin.types import EnvironmentState

    runtime_home = tmp_path / "opencode"
    runtime_home.mkdir()
    (runtime_home / "opencode.json").write_text(
        json.dumps({"plugin": [{"path": "current.ts"}], "mcp": {"old": {"enabled": True}}}),
        encoding="utf-8",
    )
    target_env = EnvironmentState(
        provider="opencode",
        extra={"opencode_config_content": json.dumps({"plugin": [], "mcp": {}})},
    )

    _materialize_runtime_config("opencode", runtime_home, target_env)

    assert json.loads((runtime_home / "opencode.json").read_text(encoding="utf-8")) == {"mcp": {}, "plugin": []}


def test_opencode_resume_command_is_simple_resume_open():
    from checkpoint_plugin.resume import _resume_command

    assert _resume_command("opencode", "ses_resumed", None) == "checkpoint resume-open ses_resumed"


def test_opencode_resume_open_spec_imports_metadata_then_opens(tmp_path):
    import sys

    from checkpoint_plugin.resume import _resume_open_spec
    from checkpoint_plugin.types import EnvironmentState

    import_path = tmp_path / "imports" / "ses_resumed.json"
    runtime_env = {"OPENCODE_CONFIG_DIR": str(tmp_path / "env-state" / "opencode")}
    spec = _resume_open_spec(
        "opencode",
        "ses_resumed",
        tmp_path,
        import_path,
        EnvironmentState(provider="opencode"),
        runtime_env,
    )

    assert spec is not None
    assert spec.env == runtime_env
    assert spec.preflight == [
        ["opencode", "import", str(import_path)],
        [sys.executable, "-m", "checkpoint_plugin.cli", "opencode-restore-metadata", str(import_path), "ses_resumed"],
    ]
    assert spec.command == ["opencode", "--session", "ses_resumed"]


def test_opencode_resume_of_resume_uses_checkpoint_jsonl(tmp_path, monkeypatch):
    from checkpoint_plugin.coordinator import CheckpointCoordinator, TurnRecord
    from checkpoint_plugin.resume import ResumeOrchestrator
    from checkpoint_plugin.store import CheckpointStore

    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "opencode")

    def msg(message_id, role, text, parent_id=None):
        info = {"id": message_id, "sessionID": "ses_orig", "role": role, "time": {"created": 1000}}
        if parent_id is not None:
            info["parentID"] = parent_id
        if role == "assistant":
            info["finish"] = "stop"
            info["time"]["completed"] = 1100
        return {
            "info": info,
            "parts": [
                {
                    "id": f"prt_{message_id}",
                    "type": "text",
                    "text": text,
                    "messageID": message_id,
                    "sessionID": "ses_orig",
                }
            ],
        }

    messages = [
        msg("msg_user_1", "user", "first"),
        msg("msg_assistant_1", "assistant", "one", "msg_user_1"),
        msg("msg_user_2", "user", "second", "msg_assistant_1"),
        msg("msg_assistant_2", "assistant", "two", "msg_user_2"),
    ]
    session_info = {"id": "ses_orig", "slug": "test", "projectID": "global", "title": "Test"}

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(
            user_message="first",
            assistant_text="one",
            metadata={"hook_payload": {"raw_messages": messages[:2], "session_info": session_info}},
        )
    )
    coordinator.on_turn_end(
        TurnRecord(
            user_message="second",
            assistant_text="two",
            metadata={"hook_payload": {"raw_messages": messages, "session_info": session_info}},
        )
    )

    orchestrator = ResumeOrchestrator(cwd=cwd)
    gen1 = orchestrator.execute(orchestrator.plan("s1", 1), lambda _text: True)
    assert gen1.provider_session_path is not None

    gen1_store = CheckpointStore(plugin_home / "sessions" / gen1.new_session_id)
    assert gen1_store.trajectory_path.exists()
    assert Path(gen1.provider_session_path) != gen1_store.trajectory_path
    for turn_id in (0, 1):
        manifest = gen1_store.read_manifest(turn_id)
        assert manifest.trajectory_ref is not None
        assert manifest.trajectory_ref.transcript_path == str(gen1_store.trajectory_path)

    gen2_orchestrator = ResumeOrchestrator(cwd=cwd)
    gen2 = gen2_orchestrator.execute(gen2_orchestrator.plan(gen1.new_session_id, 1), lambda _text: True)

    assert gen2.provider_session_path is not None
    import_data = json.loads(Path(gen2.provider_session_path).read_text(encoding="utf-8"))
    assert import_data["info"]["id"] == gen2.new_session_id
    assert len(import_data["messages"]) == 4
