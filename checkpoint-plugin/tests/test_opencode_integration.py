"""Test OpenCode integration and hook installation."""

import json
from pathlib import Path

import pytest

from checkpoint_plugin.integrations.hook_installer import install_hooks, uninstall_hooks


def test_opencode_plugin_installs_typescript_file(tmp_path, monkeypatch):
    """Verify that installing OpenCode hooks creates a TypeScript plugin file."""
    config_dir = tmp_path / ".config" / "opencode"
    monkeypatch.setenv("OPENCODE_HOME", str(config_dir))

    results = install_hooks("opencode")

    assert len(results) == 1
    result = results[0]
    assert result.provider == "opencode"
    assert result.path == config_dir / "plugins" / "checkpoint.ts"
    assert result.changed is True
    assert result.path.exists()

    # Verify it's a TypeScript file with correct content
    content = result.path.read_text(encoding="utf-8")
    assert "export const CheckpointPlugin" in content
    assert "event.type === \"session.created\"" in content
    assert "event.type === \"session.idle\"" in content
    assert "info?.model?.variant" in content
    assert "checkpoint_plugin.integrations.opencode_hook" in content


def test_opencode_plugin_uninstall_removes_file(tmp_path, monkeypatch):
    """Verify that uninstalling OpenCode hooks removes the plugin file."""
    config_dir = tmp_path / ".config" / "opencode"
    monkeypatch.setenv("OPENCODE_HOME", str(config_dir))

    # Install first
    install_results = install_hooks("opencode")
    plugin_path = install_results[0].path
    assert plugin_path.exists()

    # Uninstall
    uninstall_results = uninstall_hooks("opencode")
    assert len(uninstall_results) == 1
    assert uninstall_results[0].changed is True
    assert not plugin_path.exists()


def test_opencode_plugin_reinstall_is_idempotent(tmp_path, monkeypatch):
    """Verify that reinstalling doesn't change an already-installed plugin."""
    config_dir = tmp_path / ".config" / "opencode"
    monkeypatch.setenv("OPENCODE_HOME", str(config_dir))

    # First install
    results1 = install_hooks("opencode")
    assert results1[0].changed is True

    # Second install - should be idempotent
    results2 = install_hooks("opencode")
    assert results2[0].changed is False
    assert results2[0].path.exists()


def test_opencode_hook_handles_session_created_payload(tmp_path, monkeypatch):
    """Verify the Python hook processes session.created payloads correctly."""
    from io import StringIO
    from checkpoint_plugin.integrations.opencode_hook import main

    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENCODE_SESSION_ID", "test-session")

    # Simulate TypeScript plugin calling Python hook with session_start event
    payload = json.dumps({
        "sessionID": "test-session",
        "directory": str(tmp_path),
        "agent_type": "primary",
        "event_metadata": {
            "timestamp": "2026-06-03T14:00:00Z",
            "hook_event_name": "SessionStart",
        }
    })

    monkeypatch.setattr("sys.stdin", StringIO(payload))

    exit_code = main(["session_start"])
    assert exit_code == 0

    # Verify checkpoint was created
    session_dir = tmp_path / "sessions" / "test-session"
    assert session_dir.exists()
    metadata = session_dir / "metadata.json"
    assert metadata.exists()


def test_opencode_hook_handles_session_idle_payload(tmp_path, monkeypatch):
    """Verify the Python hook processes session.idle payloads correctly."""
    from io import StringIO
    from checkpoint_plugin.integrations.opencode_hook import main

    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENCODE_SESSION_ID", "test-session")

    # Create session first
    session_dir = tmp_path / "sessions" / "test-session"
    session_dir.mkdir(parents=True)
    (session_dir / "manifests").mkdir()
    (session_dir / "blobs").mkdir()
    metadata = session_dir / "metadata.json"
    metadata.write_text(json.dumps({
        "session_id": "test-session",
        "created_at": "2026-06-03T14:00:00Z",
    }), encoding="utf-8")

    # Simulate TypeScript plugin calling Python hook with turn_end event
    payload = json.dumps({
        "sessionID": "test-session",
        "directory": str(tmp_path),
        "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ],
        "event_metadata": {
            "timestamp": "2026-06-03T14:01:00Z",
            "hook_event_name": "Stop",
            "message_count": 2,
        }
    })

    monkeypatch.setattr("sys.stdin", StringIO(payload))

    exit_code = main(["turn_end"])
    assert exit_code == 0

    # Verify turn was recorded - check manifests directory
    manifests_dir = session_dir / "manifests"
    assert manifests_dir.exists()
    # A turn should create a manifest file
    manifest_files = list(manifests_dir.glob("*.json"))
    assert len(manifest_files) > 0


def test_opencode_fork_inherits_session_env(tmp_path, monkeypatch):
    """OC1: Verify fork sessions inherit session_env fields from source session."""
    from io import StringIO
    from checkpoint_plugin.integrations.opencode_hook import main

    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(tmp_path))

    # Step 1: Create parent session with model
    parent_id = "parent-session"
    monkeypatch.setenv("OPENCODE_SESSION_ID", parent_id)

    parent_payload = json.dumps({
        "sessionID": parent_id,
        "directory": str(tmp_path),
        "agent_type": "primary",
        "model": "big-pickle",
        "effort": "max",
        "event_metadata": {
            "timestamp": "2026-06-03T14:00:00Z",
            "hook_event_name": "SessionStart",
        }
    })

    monkeypatch.setattr("sys.stdin", StringIO(parent_payload))
    exit_code = main(["session_start"])
    assert exit_code == 0

    # Verify parent has model in session_env
    parent_dir = tmp_path / "sessions" / parent_id
    parent_meta = json.loads((parent_dir / "metadata.json").read_text())
    assert parent_meta["session_env"]["model"] == "big-pickle"
    assert parent_meta["session_env"]["effort"] == "max"

    # Step 2: Create fork session WITHOUT model in payload (simulating real behavior)
    fork_id = "fork-session"
    monkeypatch.setenv("OPENCODE_SESSION_ID", fork_id)

    fork_payload = json.dumps({
        "sessionID": fork_id,
        "directory": str(tmp_path),
        "agent_type": "primary",
        "source": "fork",
        "forked_from_session_id": parent_id,
        # Note: NO model field in payload (this is the bug scenario)
        "event_metadata": {
            "timestamp": "2026-06-03T14:05:00Z",
            "hook_event_name": "SessionStart",
        }
    })

    monkeypatch.setattr("sys.stdin", StringIO(fork_payload))
    exit_code = main(["session_start"])
    assert exit_code == 0

    # Verify fork inherited model from parent (OC1 fix)
    fork_dir = tmp_path / "sessions" / fork_id
    fork_meta = json.loads((fork_dir / "metadata.json").read_text())
    assert fork_meta["session_env"]["model"] == "big-pickle", "Fork should inherit model from parent"
    assert fork_meta["session_env"]["effort"] == "max", "Fork should inherit effort from parent"
    assert fork_meta["session_env"]["agent_type"] == "primary"


def test_opencode_subagent_inherits_session_env(tmp_path, monkeypatch):
    """OC1: Verify subagent sessions inherit session_env fields from parent session."""
    from io import StringIO
    from checkpoint_plugin.integrations.opencode_hook import main

    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(tmp_path))

    # Step 1: Create parent session with model
    parent_id = "parent-session"
    monkeypatch.setenv("OPENCODE_SESSION_ID", parent_id)

    parent_payload = json.dumps({
        "sessionID": parent_id,
        "directory": str(tmp_path),
        "agent_type": "primary",
        "model": "big-pickle",
        "permission_mode": "auto",
        "event_metadata": {
            "timestamp": "2026-06-03T14:00:00Z",
            "hook_event_name": "SessionStart",
        }
    })

    monkeypatch.setattr("sys.stdin", StringIO(parent_payload))
    exit_code = main(["session_start"])
    assert exit_code == 0

    # Step 2: Create subagent session WITHOUT model in payload
    subagent_id = "subagent-session"
    monkeypatch.setenv("OPENCODE_SESSION_ID", subagent_id)

    subagent_payload = json.dumps({
        "sessionID": subagent_id,
        "directory": str(tmp_path),
        "agent_type": "subagent",
        "parent_session_id": parent_id,
        # Note: NO model or permission_mode (simulating real behavior)
        "event_metadata": {
            "timestamp": "2026-06-03T14:05:00Z",
            "hook_event_name": "SessionStart",
        }
    })

    monkeypatch.setattr("sys.stdin", StringIO(subagent_payload))
    exit_code = main(["session_start"])
    assert exit_code == 0

    # Verify subagent inherited model from parent (OC1 fix)
    subagent_dir = tmp_path / "sessions" / subagent_id
    subagent_meta = json.loads((subagent_dir / "metadata.json").read_text())
    assert subagent_meta["session_env"]["model"] == "big-pickle", "Subagent should inherit model from parent"
    assert subagent_meta["session_env"]["permission_mode"] == "auto", "Subagent should inherit permission_mode"
    assert subagent_meta["session_env"]["agent_type"] == "subagent"


def test_opencode_permission_capture_from_session_info(tmp_path, monkeypatch):
    """OC2: Verify permission policies are captured from session_info."""
    from io import StringIO
    from checkpoint_plugin.integrations.opencode_hook import main

    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENCODE_SESSION_ID", "test-session")

    # Simulate hook payload with permission in session_info (typical for subagents)
    payload = json.dumps({
        "sessionID": "test-session",
        "directory": str(tmp_path),
        "agent_type": "subagent",
        "model": "big-pickle",
        "session_info": {
            "id": "test-session",
            "title": "Test subagent",
            "permission": [
                {"permission": "task", "action": "deny", "pattern": "*"}
            ],
        },
        "event_metadata": {
            "timestamp": "2026-06-03T14:00:00Z",
            "hook_event_name": "SessionStart",
        }
    })

    monkeypatch.setattr("sys.stdin", StringIO(payload))
    exit_code = main(["session_start"])
    assert exit_code == 0

    # Verify permission was captured in session_env (OC2 fix)
    session_dir = tmp_path / "sessions" / "test-session"
    metadata = json.loads((session_dir / "metadata.json").read_text())

    assert "permission" in metadata["session_env"], "Permission should be captured"
    permission = json.loads(metadata["session_env"]["permission"])
    assert len(permission) == 1
    assert permission[0]["permission"] == "task"
    assert permission[0]["action"] == "deny"
    assert permission[0]["pattern"] == "*"


def test_opencode_mcp_status_capture_from_payload(tmp_path, monkeypatch):
    """Runtime MCP status from OpenCode is persisted for later turn snapshots."""
    from io import StringIO
    from checkpoint_plugin.integrations.opencode_hook import main

    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENCODE_SESSION_ID", "test-session")

    payload = json.dumps({
        "sessionID": "test-session",
        "directory": str(tmp_path),
        "agent_type": "primary",
        "model": "big-pickle",
        "mcp_status": {"context7": {"status": "disabled"}},
        "event_metadata": {
            "timestamp": "2026-06-03T14:00:00Z",
            "hook_event_name": "SessionStart",
        },
    })

    monkeypatch.setattr("sys.stdin", StringIO(payload))
    assert main(["session_start"]) == 0

    metadata = json.loads((tmp_path / "sessions" / "test-session" / "metadata.json").read_text())
    assert json.loads(metadata["session_env"]["mcp_status"]) == {"context7": {"status": "disabled"}}


def test_opencode_session_env_prefers_resolved_config_over_stale_env(tmp_path, monkeypatch):
    """Resolved OpenCode config plus live MCP status should replace stale env snapshots."""
    from io import StringIO
    from checkpoint_plugin.integrations.opencode_hook import main

    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENCODE_SESSION_ID", "test-session")
    monkeypatch.setenv(
        "OPENCODE_CONFIG_CONTENT",
        json.dumps({"mcp": {"context7": {"enabled": False}}}),
    )

    payload = json.dumps({
        "sessionID": "test-session",
        "directory": str(tmp_path),
        "agent_type": "primary",
        "mcp_status": {"context7": {"status": "disabled"}},
        "resolved_config": {
            "mcp": {"context7": {"enabled": True}},
            "provider": {"x": {"options": {"apiKey": "secret-value"}}},
        },
        "event_metadata": {
            "timestamp": "2026-06-03T14:00:00Z",
            "hook_event_name": "SessionStart",
        },
    })

    monkeypatch.setattr("sys.stdin", StringIO(payload))
    assert main(["session_start"]) == 0

    metadata = json.loads((tmp_path / "sessions" / "test-session" / "metadata.json").read_text())
    config_content = json.loads(metadata["session_env"]["opencode_config_content"])
    assert config_content["mcp"]["context7"]["enabled"] is False
    assert config_content["provider"]["x"]["options"]["apiKey"] == "***redacted***"


def test_opencode_hook_captures_mode_and_effort_from_payload_context(tmp_path, monkeypatch):
    """Advisory mode/effort fields should use OpenCode payload data when present."""
    from io import StringIO
    from checkpoint_plugin.integrations.opencode_hook import main

    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(tmp_path))
    monkeypatch.setenv("OPENCODE_SESSION_ID", "test-session")

    payload = json.dumps({
        "sessionID": "test-session",
        "directory": str(tmp_path),
        "agent_type": "primary",
        "raw_messages": [
            {"info": {"role": "user"}},
            {"info": {"role": "assistant", "mode": "build", "thinkingEffort": "high"}},
        ],
        "event_metadata": {
            "timestamp": "2026-06-03T14:00:00Z",
            "hook_event_name": "SessionStart",
        },
    })

    monkeypatch.setattr("sys.stdin", StringIO(payload))
    assert main(["session_start"]) == 0

    metadata = json.loads((tmp_path / "sessions" / "test-session" / "metadata.json").read_text())
    assert metadata["session_env"]["mode"] == "build"
    assert metadata["session_env"]["effort"] == "high"


def test_opencode_turn_end_captures_model_variant_as_effort_per_turn(tmp_path, monkeypatch):
    from io import StringIO
    from checkpoint_plugin.env.collector import environment_from_blob
    from checkpoint_plugin.integrations.opencode_hook import main
    from checkpoint_plugin.store import CheckpointStore

    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "project"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("OPENCODE_SESSION_ID", "test-session")
    monkeypatch.setenv("OPENCODE_MODEL", "stale-model")
    monkeypatch.setenv("OPENCODE_EFFORT", "stale-effort")

    start_payload = json.dumps({
        "sessionID": "test-session",
        "directory": str(cwd),
        "agent_type": "primary",
        "event_metadata": {"hook_event_name": "SessionStart"},
    })
    monkeypatch.setattr("sys.stdin", StringIO(start_payload))
    assert main(["session_start"]) == 0

    for prompt, model, variant in (
        ("use high", "deepseek-v4-flash-free", "high"),
        ("use max", "deepseek-v4-flash-free", "max"),
    ):
        stop_payload = json.dumps({
            "sessionID": "test-session",
            "directory": str(cwd),
            "model": model,
            "messages": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "ok"},
            ],
            "session_info": {
                "model": {"id": model, "providerID": "opencode", "variant": variant},
            },
            "event_metadata": {"hook_event_name": "Stop"},
        })
        monkeypatch.setattr("sys.stdin", StringIO(stop_payload))
        assert main(["turn_end"]) == 0

    store = CheckpointStore(plugin_home / "sessions" / "test-session")
    first = environment_from_blob(store.read_manifest(0).env_ref, store)
    second = environment_from_blob(store.read_manifest(1).env_ref, store)

    assert first.model == "deepseek-v4-flash-free"
    assert first.effort == "high"
    assert second.model == "deepseek-v4-flash-free"
    assert second.effort == "max"
    assert store.read_manifest(0).env_ref != store.read_manifest(1).env_ref


def test_opencode_hook_captures_session_messages_and_todos_from_sqlite(tmp_path, monkeypatch):
    from io import StringIO
    import sqlite3
    from checkpoint_plugin.integrations.opencode_hook import main

    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "project"
    data_dir = tmp_path / "data"
    db_dir = data_dir / "opencode"
    db_dir.mkdir(parents=True)
    cwd.mkdir()
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
    conn.execute(
        "INSERT INTO session_message VALUES (?, ?, ?, ?, ?, ?)",
        ("evt_1", "test-session", "model-switched", 100, 101, json.dumps({"model": {"id": "big-pickle"}})),
    )
    conn.execute(
        "INSERT INTO todo VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("test-session", "Capture todo", "pending", "high", 0, 200, 201),
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("OPENCODE_SESSION_ID", "test-session")
    monkeypatch.setenv("OPENCODE_DATA_DIR", str(data_dir))

    payload = json.dumps({
        "sessionID": "test-session",
        "directory": str(cwd),
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
        "event_metadata": {
            "timestamp": "2026-06-03T14:00:00Z",
            "hook_event_name": "Stop",
        },
    })
    monkeypatch.setattr("sys.stdin", StringIO(payload))
    assert main(["turn_end"]) == 0

    trajectory = (plugin_home / "sessions" / "test-session" / "trajectory.jsonl").read_text(encoding="utf-8")
    record = json.loads(trajectory.strip())
    hook_payload = record["metadata"]["hook_payload"]
    assert hook_payload["session_messages"][0]["type"] == "model-switched"
    assert hook_payload["session_messages"][0]["data"]["model"]["id"] == "big-pickle"
    assert hook_payload["todos"][0]["content"] == "Capture todo"


def test_opencode_turn_end_uses_runtime_mcp_status_in_env_snapshot(tmp_path, monkeypatch):
    """A runtime UI disconnect should override static opencode.json in the checkpoint env."""
    from io import StringIO
    from checkpoint_plugin.env.collector import environment_from_blob
    from checkpoint_plugin.integrations.opencode_hook import main
    from checkpoint_plugin.store import CheckpointStore

    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    opencode_home = home / ".config" / "opencode"
    cwd.mkdir()
    opencode_home.mkdir(parents=True)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("OPENCODE_HOME", str(opencode_home))
    monkeypatch.setenv("OPENCODE_SESSION_ID", "test-session")

    (opencode_home / "opencode.json").write_text(
        json.dumps(
            {
                "mcp": {
                    "context7": {
                        "type": "local",
                        "command": ["npx", "-y", "@upstash/context7-mcp@latest"],
                        "enabled": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    start_payload = json.dumps({
        "sessionID": "test-session",
        "directory": str(cwd),
        "agent_type": "primary",
        "mcp_status": {"context7": {"status": "disabled"}},
        "event_metadata": {"hook_event_name": "SessionStart"},
    })
    monkeypatch.setattr("sys.stdin", StringIO(start_payload))
    assert main(["session_start"]) == 0

    stop_payload = json.dumps({
        "sessionID": "test-session",
        "directory": str(cwd),
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
        "mcp_status": {"context7": {"status": "disabled"}},
        "event_metadata": {"hook_event_name": "Stop"},
    })
    monkeypatch.setattr("sys.stdin", StringIO(stop_payload))
    assert main(["turn_end"]) == 0

    store = CheckpointStore(plugin_home / "sessions" / "test-session")
    manifest = store.read_manifest(0)
    env = environment_from_blob(manifest.env_ref, store)
    assert env.mcp_servers == {"context7": "inactive"}
