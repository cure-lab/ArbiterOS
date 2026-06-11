import json
import uuid
from pathlib import Path

from checkpoint_plugin.cli import main
from checkpoint_plugin.coordinator import CheckpointCoordinator, TurnRecord
from checkpoint_plugin.paths import load_config, write_config
from checkpoint_plugin.resume import ResumeOptions, ResumeOrchestrator, _resume_command
from checkpoint_plugin.store import CheckpointStore
from checkpoint_plugin.types import RestoreReport, TrajectoryReference


def _isolate_provider_env(monkeypatch):
    for name in (
        "CHECKPOINT_PROVIDER",
        "CLAUDE_PROVIDER",
        "CLAUDE_SESSION_ID",
        "CLAUDE_PROJECT_DIR",
        "CODEX_HOME",
        "CODEX_SESSION_ID",
        "ANTHROPIC_MODEL",
        "CLAUDE_MODEL",
        "OPENAI_MODEL",
        "CODEX_MODEL",
        "CLAUDE_PERMISSION_MODE",
        "CODEX_PERMISSION_MODE",
        "CODEX_SANDBOX_MODE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_resume_diff_backup_and_restore(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    target_file = cwd / "file.txt"
    target_file.write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    target_file.write_text("v2", encoding="utf-8")
    (cwd / "new.txt").write_text("new", encoding="utf-8")

    orchestrator = ResumeOrchestrator(cwd=cwd)
    plan = orchestrator.plan("s1", 0)
    assert "modified: 1 files" in plan.fs_diff_text

    report = orchestrator.execute(plan, lambda _text: True)

    assert target_file.read_text(encoding="utf-8") == "v1"
    assert not (cwd / "new.txt").exists()
    uuid.UUID(report.new_session_id)
    resumed_store = CheckpointStore(plugin_home / "sessions" / report.new_session_id)
    resumed_manifest = resumed_store.read_manifest(0)
    resumed_fs = resumed_store.load_json_blob(resumed_manifest.fs_ref)
    file_ref = resumed_fs["files"]["file.txt"]
    assert resumed_manifest.session_id == report.new_session_id
    assert not resumed_store.legacy_blobs_dir.exists()
    assert resumed_store.load_blob(file_ref) == b"v1"
    assert (plugin_home / "backups").exists()


def test_resume_promotes_legacy_session_blobs_without_copying_blob_tree(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    target_file = cwd / "file.txt"
    target_file.write_text("legacy checkpoint", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    source_store = CheckpointStore(plugin_home / "sessions" / "s1")
    manifest = source_store.read_manifest(0)
    fs_snapshot = source_store.load_json_blob(manifest.fs_ref)
    file_ref = fs_snapshot["files"]["file.txt"]
    for blob in list(source_store.blobs_dir.glob("*/*")):
        if blob.is_file():
            legacy_path = source_store.legacy_blob_path(blob.name)
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_bytes(blob.read_bytes())
            blob.unlink()
    assert not source_store.blob_path(file_ref).exists()
    assert source_store.legacy_blob_path(file_ref).exists()

    target_file.write_text("current", encoding="utf-8")
    report = ResumeOrchestrator(cwd=cwd).execute(ResumeOrchestrator(cwd=cwd).plan("s1", 0), lambda _text: True)

    resumed_store = CheckpointStore(plugin_home / "sessions" / report.new_session_id)
    assert not resumed_store.legacy_blobs_dir.exists()
    assert not source_store.legacy_blob_path(file_ref).exists()
    assert source_store.blob_path(file_ref).read_bytes() == b"legacy checkpoint"
    assert resumed_store.load_blob(file_ref) == b"legacy checkpoint"
    assert target_file.read_text(encoding="utf-8") == "legacy checkpoint"


def test_resume_copies_trajectory_through_target_turn(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="first", assistant_text="one"))
    coordinator.on_turn_end(TurnRecord(user_message="second", assistant_text="two"))

    orchestrator = ResumeOrchestrator(cwd=cwd)
    report = orchestrator.execute(orchestrator.plan("s1", 1), lambda _text: True)

    resumed_store = CheckpointStore(plugin_home / "sessions" / report.new_session_id)
    events = [
        json.loads(line)
        for line in resumed_store.trajectory_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [event["turn_id"] for event in events] == [0, 1]
    assert [event["user_message"] for event in events] == ["first", "second"]


def test_resume_copies_referenced_transcript_slices(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    transcript = tmp_path / "provider.jsonl"
    cwd.mkdir()
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    transcript.write_bytes(b'{"turn_id":"one"}\n{"turn_id":"two"}\n')
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(user_message="first"),
        TrajectoryReference("codex", str(transcript), 0, 18, 1),
    )
    coordinator.on_turn_end(
        TurnRecord(user_message="second"),
        TrajectoryReference("codex", str(transcript), 18, transcript.stat().st_size, 1),
    )

    orchestrator = ResumeOrchestrator(cwd=cwd)
    report = orchestrator.execute(orchestrator.plan("s1", 1), lambda _text: True)

    resumed_store = CheckpointStore(plugin_home / "sessions" / report.new_session_id)
    assert resumed_store.trajectory_path.read_bytes() == transcript.read_bytes()


def test_resume_same_checkpoint_multiple_times_creates_distinct_sessions(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    orchestrator = ResumeOrchestrator(cwd=cwd)
    first = orchestrator.execute(orchestrator.plan("s1", 0), lambda _text: True)
    second = orchestrator.execute(orchestrator.plan("s1", 0), lambda _text: True)

    assert first.new_session_id != second.new_session_id
    assert (plugin_home / "sessions" / first.new_session_id).exists()
    assert (plugin_home / "sessions" / second.new_session_id).exists()


def test_resume_can_restore_into_new_folder_copy(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    copy_cwd = tmp_path / "work-copy"
    cwd.mkdir()
    target_file = cwd / "file.txt"
    target_file.write_text("v1\n", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))
    target_file.write_text("v2\n", encoding="utf-8")
    (cwd / "new.txt").write_text("new\n", encoding="utf-8")

    orchestrator = ResumeOrchestrator(cwd=cwd)
    report = orchestrator.execute(
        orchestrator.plan("s1", 0),
        lambda _text: ResumeOptions(proceed=True, target_cwd=copy_cwd),
    )

    assert target_file.read_text(encoding="utf-8") == "v2\n"
    assert (cwd / "new.txt").exists()
    assert (copy_cwd / "file.txt").read_text(encoding="utf-8") == "v1\n"
    assert not (copy_cwd / "new.txt").exists()
    assert report.target_cwd == str(copy_cwd)
    resumed_store = CheckpointStore(plugin_home / "sessions" / report.new_session_id)
    resumed_fs = resumed_store.load_json_blob(resumed_store.read_manifest(0).fs_ref)
    metadata = json.loads((resumed_store.session_dir / "metadata.json").read_text(encoding="utf-8"))
    assert resumed_fs["cwd"] == str(copy_cwd)
    assert metadata["cwd"] == str(copy_cwd)


def test_resume_plan_tolerates_nonexistent_target_dir(tmp_path, monkeypatch):
    """P7-8: planning a resume into a --target dir that does not exist yet must not
    crash. plan() snapshots the target cwd (for the diff) before execute() creates
    it, so a missing dir previously raised FileNotFoundError from git/rglob."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    missing_target = tmp_path / "does-not-exist-yet"
    cwd.mkdir()
    (cwd / "file.txt").write_text("v1\n", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    assert not missing_target.exists()
    orchestrator = ResumeOrchestrator(cwd=missing_target)
    # plan() must not raise even though the target cwd does not exist.
    plan = orchestrator.plan("s1", 0)
    report = orchestrator.execute(plan, lambda _text: True)

    # execute() creates the target and restores into it.
    assert missing_target.exists()
    assert (missing_target / "file.txt").read_text(encoding="utf-8") == "v1\n"
    assert report.target_cwd == str(missing_target)


def test_resume_fails_loudly_when_workspace_is_not_created(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    missing_target = tmp_path / "missing-workspace"
    cwd.mkdir()
    (cwd / "file.txt").write_text("v1\n", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    orchestrator = ResumeOrchestrator(cwd=missing_target)
    plan = orchestrator.plan("s1", 0)
    monkeypatch.setattr("checkpoint_plugin.resume.restore_cwd", lambda *_args, **_kwargs: RestoreReport())

    try:
        orchestrator.execute(plan, lambda _text: True)
    except RuntimeError as exc:
        assert str(exc) == f"Resume workspace was not created: {missing_target}"
    else:
        raise AssertionError("resume should fail when the workspace directory is missing")


def test_resume_materializes_codex_native_session(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    codex_home = home / ".codex"
    cwd = tmp_path / "work"
    transcript = tmp_path / "codex.jsonl"
    cwd.mkdir()
    transcript.write_text(
        '\n'.join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "old",
                            "cwd": str(cwd),
                            "cli_version": "1.2.3",
                            "model_provider": "test-provider",
                            "base_instructions": {"text": "be helpful"},
                        },
                    }
                ),
                json.dumps({"type": "event_msg", "payload": {"turn_id": "turn-1", "type": "turn_start"}}),
                json.dumps(
                    {
                        "type": "turn_context",
                        "payload": {
                            "type": "turn_context",
                            "turn_id": "turn-1",
                            "model": "old-model",
                            "permission_profile": "old-permission",
                            "sandbox_policy": "workspace-write",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "codex")
    monkeypatch.setenv("CODEX_MODEL", "gpt-target")
    monkeypatch.setenv("CODEX_PERMISSION_MODE", "acceptEdits")
    (codex_home / "config.toml").parent.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text('model_reasoning_effort = "low"\n', encoding="utf-8")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(user_message="first"),
        TrajectoryReference("codex", str(transcript), 0, transcript.stat().st_size, 2),
    )

    report = ResumeOrchestrator(cwd=cwd).execute(ResumeOrchestrator(cwd=cwd).plan("s1", 0), lambda _text: True)

    assert report.provider_session_path is not None
    assert report.env_state_dir is not None
    assert report.resume_command == f"checkpoint resume-open {report.new_session_id}"
    runtime_codex_home = Path(report.env_state_dir) / "codex"
    provider_path = runtime_codex_home / "sessions"
    materialized = list(provider_path.glob("**/*.jsonl"))
    assert [path.as_posix() for path in materialized] == [report.provider_session_path]
    records = [json.loads(line) for line in materialized[0].read_text(encoding="utf-8").splitlines()]
    assert records[0]["payload"]["id"] == report.new_session_id
    assert records[0]["payload"]["originator"] == "Codex Desktop"
    assert records[0]["payload"]["source"] == "vscode"
    assert records[0]["payload"]["thread_source"] == "user"
    assert records[0]["payload"]["cli_version"] == "1.2.3"
    assert records[0]["payload"]["model_provider"] == "test-provider"
    assert records[0]["payload"]["base_instructions"] == {"text": "be helpful"}
    # RF2: resumed sessions should NOT have forked_from_id in HEAD meta (native behavior).
    # Only forks and subagents have forked_from_id; resumes are like startup sessions.
    head_keys = list(records[0]["payload"].keys())
    assert head_keys[0] == "id"
    assert head_keys[1] == "timestamp"  # RF2: no forked_from_id for resumes
    assert "forked_from_id" not in records[0]["payload"]
    # F2: native codex resume keeps the inlined source meta chain (depth-scaled count
    # = 2 for a resume of a startup), rather than collapsing to one head meta.
    metas = [r for r in records if r.get("type") == "session_meta"]
    assert len(metas) == 2
    assert metas[1]["payload"]["id"] == "old"  # inlined source meta keeps its id
    turn_context = next(r for r in records if r.get("type") == "turn_context")
    assert turn_context["payload"]["model"] == "gpt-target"
    assert turn_context["payload"]["model_reasoning_effort"] == "low"
    assert turn_context["payload"]["permission_profile"] == "acceptEdits"
    # Permission mode must not bleed into the sandbox policy (B2): it stays as
    # whatever the original transcript recorded.
    assert turn_context["payload"]["sandbox_policy"] == "workspace-write"
    # M5: the resumed codex session is registered in the picker index.
    index_path = runtime_codex_home / "session_index.jsonl"
    assert index_path.exists()
    assert not (codex_home / "session_index.jsonl").exists()
    index_ids = [
        json.loads(line)["id"]
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert report.new_session_id in index_ids

    resumed_store = CheckpointStore(plugin_home / "sessions" / report.new_session_id)
    manifest = resumed_store.read_manifest(0)
    assert manifest.trajectory_ref is not None
    assert manifest.trajectory_ref.transcript_path == report.provider_session_path
    metadata = json.loads((resumed_store.session_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["session_id"] == report.new_session_id
    assert metadata["resumed_from_session_id"] == "s1"
    # FK1: a resume stamps its own identity rather than inheriting the source's.
    # The source s1 was source="startup"; the resume must report source="resume",
    # carry a fresh start_ts (not s1's), and drop any stale codex fork anchor.
    assert metadata["source"] == "resume"
    assert metadata["session_env"] == {
        "model": "gpt-target",
        "permission_mode": "acceptEdits",
        "effort": "low",
    }
    source_meta = json.loads((CheckpointStore(plugin_home / "sessions" / "s1").session_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["start_ts"] != source_meta["start_ts"]
    # start_ts is stamped at the resume moment, so it tracks resumed_ts (same UTC day).
    assert metadata["start_ts"][:10] == metadata["resumed_ts"][:10]
    assert "forked_from_transcript" not in metadata
    assert "forked_at_offset" not in metadata
    assert "forked_at_record_count" not in metadata


def test_codex_resume_uses_turn_context_effort_over_stale_config(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    codex_home = home / ".codex"
    cwd = tmp_path / "work"
    transcript = tmp_path / "codex.jsonl"
    cwd.mkdir()
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "old", "cwd": str(cwd)}}),
                json.dumps(
                    {
                        "type": "turn_context",
                        "payload": {
                            "turn_id": "turn-1",
                            "model": "gpt-5.5",
                            "effort": "high",
                            "collaboration_mode": {
                                "mode": "default",
                                "settings": {"reasoning_effort": "high"},
                            },
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "codex")
    monkeypatch.setenv("CODEX_MODEL", "gpt-5.5")
    (codex_home / "config.toml").parent.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.5"\nmodel_reasoning_effort = "xhigh"\n',
        encoding="utf-8",
    )

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(user_message="effort changed"),
        TrajectoryReference("codex", str(transcript), 0, transcript.stat().st_size, 2),
    )

    orchestrator = ResumeOrchestrator(cwd=cwd)
    plan = orchestrator.plan("s1", 0)

    assert plan.target_env.effort == "high"
    assert "Effort: xhigh -> high" in plan.env_diff_text

    report = orchestrator.execute(plan, lambda _text: True)

    assert report.resume_command == f"checkpoint resume-open {report.new_session_id}"
    records = [
        json.loads(line)
        for line in Path(report.provider_session_path).read_text(encoding="utf-8").splitlines()
    ]
    turn_context = next(r for r in records if r.get("type") == "turn_context")
    assert turn_context["payload"]["model_reasoning_effort"] == "high"
    resumed_store = CheckpointStore(plugin_home / "sessions" / report.new_session_id)
    metadata = json.loads((resumed_store.session_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["session_env"]["effort"] == "high"


def test_resume_copy_materializes_codex_session_with_copy_cwd(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    codex_home = home / ".codex"
    cwd = tmp_path / "work"
    copy_cwd = tmp_path / "work-copy"
    transcript = tmp_path / "codex.jsonl"
    cwd.mkdir()
    transcript.write_text(
        '\n'.join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "old", "cwd": str(cwd)}}),
                json.dumps({"type": "event_msg", "payload": {"turn_id": "turn-1", "type": "turn_start"}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "codex")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(user_message="first"),
        TrajectoryReference("codex", str(transcript), 0, transcript.stat().st_size, 2),
    )

    orchestrator = ResumeOrchestrator(cwd=cwd)
    report = orchestrator.execute(
        orchestrator.plan("s1", 0),
        lambda _text: ResumeOptions(proceed=True, target_cwd=copy_cwd),
    )

    assert report.provider_session_path is not None
    records = [
        json.loads(line)
        for line in Path(report.provider_session_path).read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["payload"]["cwd"] == str(copy_cwd)


def test_resume_materializes_codex_session_meta_for_sliced_trajectory(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    codex_home = home / ".codex"
    cwd = tmp_path / "work"
    copy_cwd = tmp_path / "work-copy"
    transcript = tmp_path / "codex.jsonl"
    cwd.mkdir()
    prefix = json.dumps({"type": "session_meta", "payload": {"id": "old", "cwd": str(cwd)}}) + "\n"
    suffix = json.dumps({"type": "event_msg", "payload": {"turn_id": "turn-1", "type": "turn_start"}}) + "\n"
    transcript.write_text(prefix + suffix, encoding="utf-8")
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "codex")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(user_message="first"),
        TrajectoryReference("codex", str(transcript), len(prefix.encode("utf-8")), transcript.stat().st_size, 1),
    )

    orchestrator = ResumeOrchestrator(cwd=cwd)
    report = orchestrator.execute(
        orchestrator.plan("s1", 0),
        lambda _text: ResumeOptions(proceed=True, target_cwd=copy_cwd),
    )

    assert report.provider_session_path is not None
    records = [
        json.loads(line)
        for line in Path(report.provider_session_path).read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["type"] == "session_meta"
    assert records[0]["payload"]["id"] == report.new_session_id
    assert records[0]["payload"]["cwd"] == str(copy_cwd)
    assert records[0]["payload"]["originator"] == "Codex Desktop"
    assert records[0]["payload"]["source"] == "vscode"
    assert records[0]["payload"]["thread_source"] == "user"
    # F2: the inlined source meta is kept (id=old), cwd re-pinned to the copy.
    assert records[1]["type"] == "session_meta"
    assert records[1]["payload"]["id"] == "old"
    assert records[1]["payload"]["cwd"] == str(copy_cwd)
    assert records[2]["type"] == "event_msg"


def test_resume_materializes_claude_native_session(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    claude_home = home / ".claude"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    transcript.write_text(
        '\n'.join(
            [
                json.dumps({"type": "permission-mode", "sessionId": "old", "permissionMode": "default"}),
                json.dumps(
                    {
                        "type": "user",
                        "sessionId": "old",
                        "uuid": "old-user",
                        "parentUuid": None,
                        "cwd": "/old",
                        "message": {"role": "user", "content": "hi"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "sessionId": "old",
                        "uuid": "old-assistant",
                        "parentUuid": "old-user",
                        "cwd": "/old",
                        "message": {"role": "assistant", "content": []},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")
    monkeypatch.setenv("CLAUDE_MODEL", "sonnet-target")
    monkeypatch.setenv("CLAUDE_PERMISSION_MODE", "acceptEdits")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(user_message="first"),
        TrajectoryReference("claude", str(transcript), 0, transcript.stat().st_size, 3),
    )

    report = ResumeOrchestrator(cwd=cwd).execute(ResumeOrchestrator(cwd=cwd).plan("s1", 0), lambda _text: True)

    assert report.provider_session_path is not None
    assert report.env_state_dir is not None
    runtime_claude_home = Path(report.env_state_dir) / "claude"
    materialized = runtime_claude_home / "projects" / str(cwd).replace("/", "-") / f"{report.new_session_id}.jsonl"
    assert str(materialized) == report.provider_session_path
    records = [json.loads(line) for line in materialized.read_text(encoding="utf-8").splitlines()]
    # F4: a claude resume is fork-shaped — the leading keyless permission-mode record
    # is stripped (native b57f8e6f drops all such records), leaving the uuid-bearing
    # user + assistant records.
    assert [r["type"] for r in records] == ["user", "assistant"]
    assert not any(r.get("type") == "permission-mode" for r in records)
    # sessionId re-pinned on records that carry it (F8); cwd re-pinned to the target.
    assert {r["sessionId"] for r in records} == {report.new_session_id}
    assert all(r["cwd"] == str(cwd) for r in records)
    # F1: inherited uuids preserved byte-identical, forkedFrom resolves into the source.
    assert [r["uuid"] for r in records] == ["old-user", "old-assistant"]
    assert all(r["forkedFrom"]["sessionId"] == "s1" for r in records)
    assert all(r["forkedFrom"]["messageUuid"] == r["uuid"] for r in records)
    # parentUuid chain preserved: the assistant still points at the user.
    assert records[1]["parentUuid"] == records[0]["uuid"]


def test_resume_restores_environment_with_target_provider_layout(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    claude_home = home / ".claude"
    codex_home = home / ".codex"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    (claude_home / "skills" / "skill-a").mkdir(parents=True)
    (claude_home / "skills" / "skill-a" / "SKILL.md").write_text("claude skill", encoding="utf-8")
    (claude_home / "settings.json").write_text('{"target": true}', encoding="utf-8")
    (cwd / "file.txt").write_text("v1", encoding="utf-8")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    (claude_home / "skills" / "skill-a" / "SKILL.md").write_text("changed", encoding="utf-8")
    (codex_home / "skills" / "codex-only").mkdir(parents=True)
    (codex_home / "skills" / "codex-only" / "SKILL.md").write_text("do not delete", encoding="utf-8")
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "codex")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    report = ResumeOrchestrator(cwd=cwd).execute(ResumeOrchestrator(cwd=cwd).plan("s1", 0), lambda _text: True)

    assert report.env_state_dir is not None
    runtime_claude_home = Path(report.env_state_dir) / "claude"
    assert (claude_home / "skills" / "skill-a" / "SKILL.md").read_text(encoding="utf-8") == "changed"
    assert (claude_home / "settings.json").read_text(encoding="utf-8") == '{"target": true}'
    assert (runtime_claude_home / "skills" / "skill-a" / "SKILL.md").read_text(encoding="utf-8") == "claude skill"
    assert (runtime_claude_home / "settings.json").read_text(encoding="utf-8") == '{"target": true}'
    assert (codex_home / "skills" / "codex-only" / "SKILL.md").read_text(encoding="utf-8") == "do not delete"


def test_resume_reports_only_environment_files_that_changed(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    codex_home = home / ".codex"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "codex")

    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text("model = 'old'\napi_key = 'secret-value'\n", encoding="utf-8")
    (codex_home / "auth.json").write_text('{"token":"same"}\n', encoding="utf-8")
    (cwd / "README.md").write_text("v1\n", encoding="utf-8")
    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    (codex_home / "config.toml").write_text("model = 'new'\napi_key = 'secret-value'\n", encoding="utf-8")
    (cwd / "README.md").write_text("v2\n", encoding="utf-8")

    orchestrator = ResumeOrchestrator(cwd=cwd)
    report = orchestrator.execute(orchestrator.plan("s1", 0), lambda _text: True)

    assert sorted(Path(path).name for path in report.env.changed) == ["config.toml"]
    assert sorted(Path(path).name for path in report.fs.changed) == ["README.md"]
    assert len(report.changed_files) == 2
    assert report.env_state_dir is not None
    runtime_codex_home = Path(report.env_state_dir) / "codex"
    runtime_config = (runtime_codex_home / "config.toml").read_text(encoding="utf-8")
    assert "model = 'old'" in runtime_config
    assert "api_key = 'secret-value'" in runtime_config
    assert "***redacted***" not in runtime_config
    assert (runtime_codex_home / "auth.json").read_text(encoding="utf-8") == '{"token":"same"}\n'
    assert (codex_home / "config.toml").read_text(encoding="utf-8") == "model = 'new'\napi_key = 'secret-value'\n"


def test_resume_rewrites_codex_config_paths_to_runtime_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plugin_home = home / ".checkpoint-plugin"
    codex_home = home / ".codex"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "codex")

    source_skill = codex_home / "skills" / "doc" / "SKILL.md"
    source_skill.parent.mkdir(parents=True)
    source_skill.write_text("doc skill", encoding="utf-8")
    (codex_home / "hooks.json").write_text("{}", encoding="utf-8")
    (codex_home / "config.toml").write_text(
        f"""
[mcp_servers.node_repl.env]
CODEX_HOME = "{codex_home}"
NODE_REPL_TRUSTED_CODE_PATHS = "{codex_home}"

[marketplaces.local]
source = "{codex_home}/.tmp/bundled-marketplaces/local"

[hooks.state."{codex_home}/hooks.json:stop:0:0"]
trusted_hash = "sha256:old"

[[skills.config]]
path = "{source_skill}"
enabled = false
""",
        encoding="utf-8",
    )
    (cwd / "file.txt").write_text("v1", encoding="utf-8")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("s1", 0),
        lambda _text: True,
    )

    assert report.env_state_dir is not None
    runtime_codex_home = Path(report.env_state_dir) / "codex"
    runtime_config = (runtime_codex_home / "config.toml").read_text(encoding="utf-8")
    assert str(codex_home) not in runtime_config
    assert f'CODEX_HOME = "{runtime_codex_home}"' in runtime_config
    assert f'path = "{runtime_codex_home}/skills/doc/SKILL.md"' in runtime_config
    assert f'[hooks.state."{runtime_codex_home}/hooks.json:stop:0:0"]' in runtime_config


def test_resume_runtime_hooks_use_live_plugin_commands(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plugin_home = home / ".checkpoint-plugin"
    codex_home = home / ".codex"
    cwd = tmp_path / "work"
    cwd.mkdir()
    live_python = tmp_path / "arbiteros-venv" / "bin" / "python3"
    live_python.parent.mkdir(parents=True)
    live_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    live_python.chmod(0o755)
    live_hook = {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                f"{live_python} -m checkpoint_plugin.integrations.codex_hook turn_end"
                            ),
                            "statusMessage": "Saving checkpoint",
                        }
                    ]
                }
            ]
        }
    }
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "codex")
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "hooks.json").write_text(json.dumps(live_hook, indent=2) + "\n", encoding="utf-8")
    (cwd / "file.txt").write_text("v1", encoding="utf-8")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("s1", 0),
        lambda _text: True,
    )

    assert report.env_state_dir is not None
    runtime_hooks_path = Path(report.env_state_dir) / "codex" / "hooks.json"
    runtime_hooks = json.loads(runtime_hooks_path.read_text(encoding="utf-8"))
    command = runtime_hooks["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert str(live_python) in command
    assert "/external/" not in command
    assert live_python.is_file()


def test_resume_restores_codex_plugin_cache_skills(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    codex_home = home / ".codex"
    cwd = tmp_path / "work"
    plugin_root = (
        codex_home
        / "plugins"
        / "cache"
        / "openai-bundled"
        / "browser"
        / "26.1.0"
    )
    plugin_skill = (
        plugin_root
        / "skills"
        / "control-browser"
        / "SKILL.md"
    )
    plugin_script = plugin_root / "scripts" / "browser-client.mjs"
    marketplace_root = codex_home / ".tmp" / "bundled-marketplaces" / "openai-bundled"
    marketplace_plugin = marketplace_root / "plugins" / "browser"
    implicit_marketplace_root = codex_home / ".tmp" / "plugins"
    implicit_marketplace_manifest = implicit_marketplace_root / ".agents" / "plugins" / "marketplace.json"
    implicit_marketplace_plugin = implicit_marketplace_root / "plugins" / "github" / ".codex-plugin" / "plugin.json"
    cwd.mkdir()
    plugin_skill.parent.mkdir(parents=True)
    (plugin_root / ".codex-plugin").mkdir(parents=True)
    plugin_script.parent.mkdir(parents=True)
    (marketplace_plugin / ".codex-plugin").mkdir(parents=True)
    implicit_marketplace_manifest.parent.mkdir(parents=True)
    implicit_marketplace_plugin.parent.mkdir(parents=True)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "codex")

    plugin_skill.write_text("browser skill", encoding="utf-8")
    plugin_script.write_text("export const run = true;\n", encoding="utf-8")
    (plugin_root / ".codex-plugin" / "plugin.json").write_text('{"name":"browser","version":"26.1.0"}', encoding="utf-8")
    (marketplace_plugin / ".codex-plugin" / "plugin.json").write_text('{"name":"browser"}', encoding="utf-8")
    implicit_marketplace_manifest.write_text(
        """
{
  "name": "openai-curated",
  "plugins": [
    {
      "name": "github",
      "source": {"source": "local", "path": "./plugins/github"}
    }
  ]
}
""",
        encoding="utf-8",
    )
    implicit_marketplace_plugin.write_text('{"name":"github"}', encoding="utf-8")
    (codex_home / "config.toml").parent.mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text(
        f"""
[marketplaces.openai-bundled]
source = "{marketplace_root}"

[plugins."browser@openai-bundled"]
enabled = true
""",
        encoding="utf-8",
    )
    (cwd / "file.txt").write_text("v1", encoding="utf-8")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("s1", 0),
        lambda _text: True,
    )

    assert report.env_state_dir is not None
    restored = (
        Path(report.env_state_dir)
        / "codex"
        / "plugins"
        / "cache"
        / "openai-bundled"
        / "browser"
        / "26.1.0"
        / "skills"
        / "control-browser"
        / "SKILL.md"
    )
    assert restored.read_text(encoding="utf-8") == "browser skill"
    restored_script = (
        Path(report.env_state_dir)
        / "codex"
        / "plugins"
        / "cache"
        / "openai-bundled"
        / "browser"
        / "26.1.0"
        / "scripts"
        / "browser-client.mjs"
    )
    assert restored_script.read_text(encoding="utf-8") == "export const run = true;\n"
    restored_manifest = (
        Path(report.env_state_dir)
        / "codex"
        / "plugins"
        / "cache"
        / "openai-bundled"
        / "browser"
        / "26.1.0"
        / ".codex-plugin"
        / "plugin.json"
    )
    assert restored_manifest.read_text(encoding="utf-8") == '{"name":"browser","version":"26.1.0"}'
    runtime_config = (Path(report.env_state_dir) / "codex" / "config.toml").read_text(encoding="utf-8")
    assert str(marketplace_root) not in runtime_config
    restored_marketplace_manifest = (
        Path(report.env_state_dir)
        / "codex"
        / ".tmp"
        / "bundled-marketplaces"
        / "openai-bundled"
        / "plugins"
        / "browser"
        / ".codex-plugin"
        / "plugin.json"
    )
    assert restored_marketplace_manifest.read_text(encoding="utf-8") == '{"name":"browser"}'
    restored_implicit_marketplace_manifest = (
        Path(report.env_state_dir)
        / "codex"
        / ".tmp"
        / "plugins"
        / ".agents"
        / "plugins"
        / "marketplace.json"
    )
    assert '"name": "openai-curated"' in restored_implicit_marketplace_manifest.read_text(encoding="utf-8")
    restored_implicit_marketplace_plugin = (
        Path(report.env_state_dir)
        / "codex"
        / ".tmp"
        / "plugins"
        / "plugins"
        / "github"
        / ".codex-plugin"
        / "plugin.json"
    )
    assert restored_implicit_marketplace_plugin.read_text(encoding="utf-8") == '{"name":"github"}'


def test_resume_rewrites_claude_settings_paths_to_runtime_home(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    claude_home = home / ".claude"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    (claude_home / "settings.json").parent.mkdir(parents=True)
    (claude_home / "settings.json").write_text(
        json.dumps({"hooks": {"Stop": [{"command": f"{claude_home}/bin/hook"}]}}),
        encoding="utf-8",
    )
    (cwd / "file.txt").write_text("v1", encoding="utf-8")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("s1", 0),
        lambda _text: True,
    )

    assert report.env_state_dir is not None
    runtime_claude_home = Path(report.env_state_dir) / "claude"
    runtime_settings = (runtime_claude_home / "settings.json").read_text(encoding="utf-8")
    assert str(claude_home) not in runtime_settings
    assert str(runtime_claude_home / "bin" / "hook") in runtime_settings


def test_path_map_rejects_destinations_outside_runtime_root(tmp_path):
    from checkpoint_plugin.resume import _validated_path_map

    root = tmp_path / "runtime"
    path_map = _validated_path_map({"/source": str(tmp_path / "outside")}, root)

    assert path_map == {}


def test_runtime_path_map_includes_captured_provider_home(tmp_path):
    from checkpoint_plugin.env.providers import ProviderLayout
    from checkpoint_plugin.resume import _runtime_path_map

    root = tmp_path / "runtime"
    runtime_home = root / "codex"
    live_home = tmp_path / "live" / ".codex"
    captured_home = tmp_path / "captured" / ".codex"
    provider = ProviderLayout(
        name="codex",
        home=live_home,
        memory_dir=live_home / "memories",
        mcp_config=live_home / "config.toml",
        mcp_config_files=[live_home / "config.toml"],
        settings_files=[live_home / "config.toml"],
        skills_dirs={"codex-user": live_home / "skills"},
        project_files=[],
    )

    path_map = _runtime_path_map(provider, root, runtime_home, captured_provider_home=captured_home)

    assert path_map[str(live_home)] == str(runtime_home)
    assert path_map[str(captured_home)] == str(runtime_home)


def test_secret_runtime_copy_ignores_symlink_source(tmp_path):
    from checkpoint_plugin.resume import _link_runtime_secret_files

    source_home = tmp_path / "source"
    runtime_home = tmp_path / "runtime"
    attacker_target = tmp_path / "attacker-auth.json"
    source_home.mkdir()
    runtime_home.mkdir()
    attacker_target.write_text('{"token":"attacker"}\n', encoding="utf-8")
    (source_home / "auth.json").symlink_to(attacker_target)

    _link_runtime_secret_files("codex", source_home, runtime_home)

    assert not (runtime_home / "auth.json").exists()


def test_secret_runtime_copy_preserves_restrictive_mode(tmp_path):
    from checkpoint_plugin.resume import _link_runtime_secret_files

    source_home = tmp_path / "source"
    runtime_home = tmp_path / "runtime"
    source_home.mkdir()
    runtime_home.mkdir()
    source = source_home / "auth.json"
    source.write_text('{"token":"secret"}\n', encoding="utf-8")
    source.chmod(0o600)

    _link_runtime_secret_files("codex", source_home, runtime_home)

    dest = runtime_home / "auth.json"
    assert dest.read_text(encoding="utf-8") == '{"token":"secret"}\n'
    assert dest.stat().st_mode & 0o777 == 0o600


def test_secret_runtime_copy_without_fchmod(tmp_path, monkeypatch):
    import os

    from checkpoint_plugin.resume import _link_runtime_secret_files

    source_home = tmp_path / "source"
    runtime_home = tmp_path / "runtime"
    source_home.mkdir()
    runtime_home.mkdir()
    source = source_home / "auth.json"
    source.write_text('{"token":"secret"}\n', encoding="utf-8")
    source.chmod(0o600)
    monkeypatch.delattr(os, "fchmod", raising=False)

    _link_runtime_secret_files("codex", source_home, runtime_home)

    dest = runtime_home / "auth.json"
    assert dest.read_text(encoding="utf-8") == '{"token":"secret"}\n'
    assert dest.stat().st_mode & 0o777 == 0o600


def test_resume_plan_diffs_environment_with_target_provider_layout(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    codex_home = home / ".codex"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "codex")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    (codex_home / "skills" / "codex-skill").mkdir(parents=True)
    (codex_home / "skills" / "codex-skill" / "SKILL.md").write_text("skill", encoding="utf-8")
    (codex_home / "config.toml").write_text(
        """
[plugins."hugging-face@openai-curated"]
enabled = true
""",
        encoding="utf-8",
    )
    (cwd / "file.txt").write_text("v1", encoding="utf-8")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    plan = ResumeOrchestrator(cwd=cwd).plan("s1", 0)

    assert "Provider: claude -> codex" not in plan.env_diff_text
    assert "Skills" not in plan.env_diff_text
    assert "Plugin status" not in plan.env_diff_text


def test_cli_resume_cancel_returns_without_traceback(tmp_path, monkeypatch, capsys):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.chdir(cwd)

    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))
    (cwd / "file.txt").write_text("v2", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    assert main(["resume", "s1", "0"]) == 1
    captured = capsys.readouterr()
    assert "Resume cancelled" in captured.err
    assert "Traceback" not in captured.err
    assert (cwd / "file.txt").read_text(encoding="utf-8") == "v2"


def test_cli_resume_can_show_file_diff_then_restore(tmp_path, monkeypatch, capsys):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.chdir(cwd)

    target_file = cwd / "file.txt"
    target_file.write_text("v1\n", encoding="utf-8")
    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))
    target_file.write_text("v2\n", encoding="utf-8")

    answers = iter(["d", "1", "q", "y", "i"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert main(["resume", "s1", "0"]) == 0
    captured = capsys.readouterr()
    assert captured.out.count("Resume: session s1, turn 0") == 1
    assert "Detailed resume changes:" in captured.out
    assert "Filesystem:" in captured.out
    assert "--- current/file.txt" in captured.out
    assert "+++ checkpoint/file.txt" in captured.out
    assert "-v2" in captured.out
    assert "+v1" in captured.out
    assert target_file.read_text(encoding="utf-8") == "v1\n"


def test_cli_resume_prints_resume_command_hint(tmp_path, monkeypatch, capsys):
    """P4-6: the CLI must surface the simple resume-open hint and env state."""
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    transcript.write_text(
        json.dumps({"type": "permission-mode", "sessionId": "old", "permissionMode": "default"}) + "\n"
        + json.dumps({"type": "user", "sessionId": "old", "uuid": "u1", "parentUuid": None, "promptId": "p", "cwd": "/old", "message": {"role": "user", "content": "hi"}}) + "\n",
        encoding="utf-8",
    )
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")
    monkeypatch.chdir(cwd)

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(user_message="first"),
        TrajectoryReference("claude", str(transcript), 0, transcript.stat().st_size, 2),
    )

    assert main(["resume", "s1", "0", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Resume with:" in out
    assert "checkpoint resume-open" in out
    assert "claude --resume" not in out
    assert "Provider session:" in out
    assert "Env state:" in out


def test_cli_resume_open_dispatches_to_descriptor_executor(monkeypatch):
    calls = []

    def fake_resume_open(session_id):
        calls.append(session_id)
        return 0

    monkeypatch.setattr("checkpoint_plugin.cli.execute_resume_open", fake_resume_open)

    assert main(["resume-open", "session-123"]) == 0
    assert calls == ["session-123"]


def test_resume_open_rejects_tampered_descriptor_command(tmp_path):
    import pytest

    from checkpoint_plugin.resume import execute_resume_open

    session_id = "019e91aa-5d69-7180-8729-1a9a31c7e182"
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    provider_home = tmp_path / "env-state" / session_id / "codex"
    session_path = plugin_home / "sessions" / session_id
    cwd.mkdir(parents=True)
    provider_home.mkdir(parents=True)
    session_path.mkdir(parents=True)
    (session_path / "resume-open.json").write_text(
        json.dumps(
            {
                "provider": "codex",
                "session_id": session_id,
                "cwd": str(cwd),
                "env_state_dir": str(provider_home.parent),
                "provider_home": str(provider_home),
                "env": {"CODEX_HOME": str(provider_home)},
                "preflight": [],
                "command": ["rm", "-rf", "/"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="command"):
        execute_resume_open(session_id, plugin_home, execvpe=lambda *_args: None)


def test_resume_open_rejects_env_path_hijack(tmp_path):
    import pytest

    from checkpoint_plugin.resume import execute_resume_open

    session_id = "019e91aa-5d69-7180-8729-1a9a31c7e182"
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    provider_home = tmp_path / "env-state" / session_id / "codex"
    session_path = plugin_home / "sessions" / session_id
    cwd.mkdir(parents=True)
    provider_home.mkdir(parents=True)
    session_path.mkdir(parents=True)
    (session_path / "resume-open.json").write_text(
        json.dumps(
            {
                "provider": "codex",
                "session_id": session_id,
                "cwd": str(cwd),
                "env_state_dir": str(provider_home.parent),
                "provider_home": str(provider_home),
                "env": {"CODEX_HOME": str(provider_home), "PATH": str(tmp_path / "bin")},
                "preflight": [],
                "command": ["codex", "resume", session_id],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="env"):
        execute_resume_open(session_id, plugin_home, execvpe=lambda *_args: None)


def test_resume_open_rejects_path_like_session_id(tmp_path):
    import pytest

    from checkpoint_plugin.resume import execute_resume_open

    with pytest.raises(RuntimeError, match="session id"):
        execute_resume_open("../evil", tmp_path / "plugin", execvpe=lambda *_args: None)


def test_restore_environment_refuses_absolute_path_outside_allowed_roots(tmp_path):
    from checkpoint_plugin.env.providers import ProviderLayout
    from checkpoint_plugin.env.restorer import restore_environment
    from checkpoint_plugin.store import CheckpointStore
    from checkpoint_plugin.types import EnvironmentState

    cwd = tmp_path / "work"
    provider_home = tmp_path / "provider"
    backup_dir = tmp_path / "backup"
    outside = tmp_path / "outside.txt"
    cwd.mkdir()
    provider_home.mkdir()
    store = CheckpointStore(tmp_path / "session")
    good_sha = store.store_blob(b"good")
    bad_sha = store.store_blob(b"bad")
    provider = ProviderLayout(
        name="codex",
        home=provider_home,
        memory_dir=None,
        mcp_config=None,
        mcp_config_files=[],
        settings_files=[],
        skills_dirs={},
        project_files=["AGENTS.md"],
    )
    target = EnvironmentState(
        provider="codex",
        project_context={
            str(cwd / "AGENTS.md"): good_sha,
            str(outside): bad_sha,
        },
        extra={"cwd": str(cwd)},
    )

    report = restore_environment(target, provider, store, backup_dir)

    assert (cwd / "AGENTS.md").read_text(encoding="utf-8") == "good"
    assert not outside.exists()
    assert str(outside) not in report.changed


def test_restore_environment_refuses_ancestor_chain_path_traversal(tmp_path):
    from checkpoint_plugin.env.providers import ProviderLayout
    from checkpoint_plugin.env.restorer import restore_environment
    from checkpoint_plugin.store import CheckpointStore
    from checkpoint_plugin.types import EnvironmentState

    repo = tmp_path / "repo"
    nested = repo / "sub"
    escaped = tmp_path / "outside"
    provider_home = tmp_path / "provider"
    (repo / ".git").mkdir(parents=True)
    nested.mkdir()
    provider_home.mkdir()
    store = CheckpointStore(tmp_path / "session")
    sha = store.store_blob(b"escaped")
    provider = ProviderLayout(
        name="codex",
        home=provider_home,
        memory_dir=None,
        mcp_config=None,
        mcp_config_files=[],
        settings_files=[],
        skills_dirs={},
        project_files=["AGENTS.md"],
    )
    target = EnvironmentState(
        provider="codex",
        project_context={str(escaped / "AGENTS.md"): sha},
        extra={"cwd": str(nested / ".." / ".." / "outside")},
    )

    report = restore_environment(target, provider, store, tmp_path / "backup")

    assert not (escaped / "AGENTS.md").exists()
    assert str(escaped / "AGENTS.md") not in report.changed


def test_restore_environment_allows_project_root_from_nested_cwd(tmp_path):
    from checkpoint_plugin.env.providers import ProviderLayout
    from checkpoint_plugin.env.restorer import restore_environment
    from checkpoint_plugin.store import CheckpointStore
    from checkpoint_plugin.types import EnvironmentState

    repo = tmp_path / "repo"
    cwd = repo / "pkg"
    provider_home = tmp_path / "provider"
    (repo / ".git").mkdir(parents=True)
    cwd.mkdir()
    provider_home.mkdir()
    store = CheckpointStore(tmp_path / "session")
    sha = store.store_blob(b"root instructions")
    provider = ProviderLayout(
        name="codex",
        home=provider_home,
        memory_dir=None,
        mcp_config=None,
        mcp_config_files=[],
        settings_files=[],
        skills_dirs={},
        project_files=["AGENTS.md"],
    )
    target = EnvironmentState(
        provider="codex",
        project_context={str(repo / "AGENTS.md"): sha},
        extra={"cwd": str(cwd)},
    )

    restore_environment(target, provider, store, tmp_path / "backup")

    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == "root instructions"


def test_restore_environment_allows_dot_named_project_directory(tmp_path):
    from checkpoint_plugin.env.providers import ProviderLayout
    from checkpoint_plugin.env.restorer import restore_environment
    from checkpoint_plugin.store import CheckpointStore
    from checkpoint_plugin.types import EnvironmentState

    repo = tmp_path / "my.repo"
    provider_home = tmp_path / "provider"
    repo.mkdir()
    provider_home.mkdir()
    store = CheckpointStore(tmp_path / "session")
    sha = store.store_blob(b"dot repo instructions")
    provider = ProviderLayout(
        name="codex",
        home=provider_home,
        memory_dir=None,
        mcp_config=None,
        mcp_config_files=[],
        settings_files=[],
        skills_dirs={},
        project_files=["AGENTS.md"],
    )
    target = EnvironmentState(
        provider="codex",
        project_context={str(repo / "AGENTS.md"): sha},
        extra={"cwd": str(repo)},
    )

    restore_environment(target, provider, store, tmp_path / "backup")

    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == "dot repo instructions"


def test_restore_environment_allows_stringified_absolute_project_directory_root(tmp_path):
    from checkpoint_plugin.env.providers import ProviderLayout
    from checkpoint_plugin.env.restorer import restore_environment
    from checkpoint_plugin.store import CheckpointStore
    from checkpoint_plugin.types import EnvironmentState

    provider_home = tmp_path / "provider"
    config_home = tmp_path / "config"
    cwd = tmp_path / "work"
    provider_home.mkdir()
    config_home.mkdir()
    cwd.mkdir()
    store = CheckpointStore(tmp_path / "session")
    sha = store.store_blob(b"agent docs")
    provider = ProviderLayout(
        name="opencode",
        home=provider_home,
        memory_dir=None,
        mcp_config=None,
        mcp_config_files=[],
        settings_files=[],
        skills_dirs={},
        project_files=[str(config_home / "agent") + "/"],
    )
    target = EnvironmentState(
        provider="opencode",
        project_context={str(config_home / "agent" / "build.md"): sha},
        extra={"cwd": str(cwd)},
    )

    restore_environment(target, provider, store, tmp_path / "backup")

    assert (config_home / "agent" / "build.md").read_text(encoding="utf-8") == "agent docs"


def test_restore_environment_allows_remapped_global_opencode_directory_root(tmp_path):
    from checkpoint_plugin.env.providers import ProviderLayout
    from checkpoint_plugin.env.restorer import restore_environment
    from checkpoint_plugin.store import CheckpointStore
    from checkpoint_plugin.types import EnvironmentState

    provider_home = tmp_path / "runtime" / "opencode"
    opencode_root = tmp_path / "runtime" / "external" / "home" / ".opencode"
    cwd = tmp_path / "work"
    provider_home.mkdir(parents=True)
    cwd.mkdir()
    store = CheckpointStore(tmp_path / "session")
    sha = store.store_blob(b"global agent docs")
    provider = ProviderLayout(
        name="opencode",
        home=provider_home,
        memory_dir=None,
        mcp_config=None,
        mcp_config_files=[],
        settings_files=[],
        skills_dirs={},
        project_files=[str(opencode_root) + "/"],
    )
    target = EnvironmentState(
        provider="opencode",
        project_context={str(opencode_root / "agent" / "build.md"): sha},
        extra={"cwd": str(cwd)},
    )

    restore_environment(target, provider, store, tmp_path / "backup")

    assert (opencode_root / "agent" / "build.md").read_text(encoding="utf-8") == "global agent docs"


def test_cli_resume_can_show_file_diff_then_cancel(tmp_path, monkeypatch, capsys):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.chdir(cwd)

    target_file = cwd / "file.txt"
    target_file.write_text("v1\n", encoding="utf-8")
    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))
    target_file.write_text("v2\n", encoding="utf-8")

    answers = iter(["d", "q", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert main(["resume", "s1", "0"]) == 1
    captured = capsys.readouterr()
    assert captured.out.count("Resume: session s1, turn 0") == 1
    assert "Detailed resume changes:" in captured.out
    assert "Filesystem:" in captured.out
    assert "Resume cancelled" in captured.err
    assert target_file.read_text(encoding="utf-8") == "v2\n"


def test_cli_resume_defaults_to_checkpoint_cwd(tmp_path, monkeypatch, capsys):
    plugin_home = tmp_path / "plugin"
    checkpoint_cwd = tmp_path / "checkpoint-work"
    other_cwd = tmp_path / "other-work"
    checkpoint_cwd.mkdir()
    other_cwd.mkdir()
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    target_file = checkpoint_cwd / "file.txt"
    target_file.write_text("v1\n", encoding="utf-8")
    (other_cwd / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    coordinator = CheckpointCoordinator(session_id="s1", cwd=checkpoint_cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))
    target_file.write_text("v2\n", encoding="utf-8")
    monkeypatch.chdir(other_cwd)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    assert main(["resume", "s1", "0"]) == 1
    captured = capsys.readouterr()
    assert f"Filesystem (cwd: {checkpoint_cwd})" in captured.out
    assert "modified: 1 files" in captured.out
    assert "deleted: 0 files" in captured.out
    assert "unrelated.txt" not in captured.out


def test_cli_resume_can_restore_into_chosen_copy(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    copy_cwd = tmp_path / "chosen-copy"
    cwd.mkdir()
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.chdir(cwd)

    target_file = cwd / "file.txt"
    target_file.write_text("v1\n", encoding="utf-8")
    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))
    target_file.write_text("v2\n", encoding="utf-8")

    prompts = []
    answers = iter(["y", "c", str(copy_cwd)])

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)

    assert main(["resume", "s1", "0"]) == 0
    assert target_file.read_text(encoding="utf-8") == "v2\n"
    assert (copy_cwd / "file.txt").read_text(encoding="utf-8") == "v1\n"
    assert any("Copy folder (Enter for default, or type an absolute path)" in prompt for prompt in prompts)


def test_cli_resume_rejects_relative_copy_folder(tmp_path, monkeypatch, capsys):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.chdir(cwd)

    target_file = cwd / "file.txt"
    target_file.write_text("v1\n", encoding="utf-8")
    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))
    target_file.write_text("v2\n", encoding="utf-8")

    answers = iter(["y", "c", "relative-copy"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert main(["resume", "s1", "0"]) == 1
    captured = capsys.readouterr()
    assert "Copy folder must be an absolute path: relative-copy" in captured.err
    assert not (cwd / "relative-copy").exists()
    assert target_file.read_text(encoding="utf-8") == "v2\n"


def test_cli_resume_diff_viewer_includes_environment_changes(tmp_path, monkeypatch, capsys):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    codex_home = home / ".codex"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "codex")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.chdir(cwd)

    (codex_home / "config.toml").parent.mkdir(parents=True)
    (codex_home / "config.toml").write_text("model = 'old'\n", encoding="utf-8")
    (cwd / "file.txt").write_text("v1\n", encoding="utf-8")
    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    (codex_home / "config.toml").write_text("model = 'new'\n", encoding="utf-8")
    (cwd / "file.txt").write_text("v2\n", encoding="utf-8")
    answers = iter(["d", "3", "q", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    assert main(["resume", "s1", "0"]) == 1
    captured = capsys.readouterr()
    assert "Environment:" in captured.out
    assert "  Settings (1 changes):" in captured.out
    assert "    ~ config.toml" in captured.out
    assert "Filesystem:" in captured.out
    assert "  ~ file.txt" in captured.out
    assert "--- current/environment/Settings/config.toml" in captured.out
    assert "+++ checkpoint/environment/Settings/config.toml" in captured.out
    assert "-model = 'new'" in captured.out
    assert "+model = 'old'" in captured.out


def test_resume_skips_missing_referenced_transcript(tmp_path, monkeypatch, capsys):
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    missing = tmp_path / "missing.jsonl"
    cwd.mkdir()
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(user_message="first"),
        TrajectoryReference("codex", str(missing), 0, 10, 1),
    )

    orchestrator = ResumeOrchestrator(cwd=cwd)
    report = orchestrator.execute(orchestrator.plan("s1", 0), lambda _text: True)

    resumed_store = CheckpointStore(plugin_home / "sessions" / report.new_session_id)
    assert not resumed_store.trajectory_path.exists()
    assert "trajectory unavailable" in capsys.readouterr().err


def test_resume_empty_trajectory_ref_does_not_crash(tmp_path, monkeypatch, capsys):
    """P4-1: a checkpoint with an empty trajectory_ref (e.g. a subagent with no
    sidechain file) must resume without crashing. Empty path resolves to '.'
    (a directory), which previously raised IsADirectoryError out of execute()."""
    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(user_message="first"),
        TrajectoryReference("claude", "", 0, 0, 0),
    )

    orchestrator = ResumeOrchestrator(cwd=cwd)
    report = orchestrator.execute(orchestrator.plan("s1", 0), lambda _text: True)

    # Resume completes; no provider session is materialized (no trajectory bytes).
    assert report.new_session_id
    assert report.provider_session_path is None
    assert "trajectory unavailable" in capsys.readouterr().err


def test_resume_subagent_session_recovers_session_boundary_tail(tmp_path, monkeypatch):
    """A subagent slice (session_boundary) whose stored end_offset trails EOF — a
    late `task_complete` carrying the LAST turn's id — is recovered at read time.
    The per_turn_key guard would drop it (key != the slice's first turn), so the
    resume prefix would be short a record without session_boundary mode."""
    from checkpoint_plugin.resume import _read_trajectory_slice_for_manifest

    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    rollout = tmp_path / "rollout.jsonl"
    body = (
        json.dumps({"type": "response_item", "turn_id": "t1", "payload": {"type": "message"}}) + "\n"
        + json.dumps({"type": "response_item", "turn_id": "t2", "payload": {"type": "message"}}) + "\n"
    )
    rollout.write_text(body, encoding="utf-8")
    captured = rollout.stat().st_size

    coordinator = CheckpointCoordinator(session_id="parent--subagent-x", cwd=cwd)
    coordinator.on_session_start(source="subagent", lineage={"parent_session_id": "parent", "agent_id": "x"})
    coordinator.on_turn_end(
        TurnRecord(user_message="sub work"),
        TrajectoryReference("codex", str(rollout), 0, captured, 2, boundary_mode="session_boundary"),
    )

    # The turn-closing record (LAST turn's id) flushes after capture.
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}, "turn_id": "t2"}) + "\n")

    manifest = coordinator.store.read_manifest(0)
    extended = _read_trajectory_slice_for_manifest(coordinator.store, manifest, extend_to_eof=True)
    assert b"task_complete" in extended
    # Without extend_to_eof the stored (short) slice is returned verbatim.
    stored = _read_trajectory_slice_for_manifest(coordinator.store, manifest, extend_to_eof=False)
    assert b"task_complete" not in stored


def _settings_without_plugin_hooks() -> str:
    return json.dumps({"hooks": {}, "model": "sonnet"}, indent=2, sort_keys=True) + "\n"


def _settings_with_plugin_hooks() -> str:
    return (
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/usr/bin/python3 -m checkpoint_plugin.integrations.claude_code_hook turn_end",
                                }
                            ]
                        }
                    ]
                },
                "model": "sonnet",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def test_resume_keeps_freshly_installed_plugin_hooks(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    claude_home = home / ".claude"
    cwd = tmp_path / "work"
    cwd.mkdir()
    claude_home.mkdir(parents=True)
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    (claude_home / "settings.json").write_text(_settings_without_plugin_hooks(), encoding="utf-8")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    (claude_home / "settings.json").write_text(_settings_with_plugin_hooks(), encoding="utf-8")

    orchestrator = ResumeOrchestrator(cwd=cwd)
    plan = orchestrator.plan("s1", 0)
    assert "Settings" not in plan.env_diff_text

    report = orchestrator.execute(plan, lambda _text: True)

    assert report.env_state_dir is not None
    runtime_claude_home = Path(report.env_state_dir) / "claude"
    after = (runtime_claude_home / "settings.json").read_text(encoding="utf-8")
    parsed = json.loads(after)
    assert parsed["model"] == "sonnet"
    commands = [
        hook["command"]
        for entry in parsed["hooks"].get("Stop", [])
        for hook in entry["hooks"]
    ]
    assert any("checkpoint_plugin.integrations" in c for c in commands)


def test_resume_does_not_reinstall_uninstalled_plugin_hooks(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    claude_home = home / ".claude"
    cwd = tmp_path / "work"
    cwd.mkdir()
    claude_home.mkdir(parents=True)
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    (claude_home / "settings.json").write_text(_settings_with_plugin_hooks(), encoding="utf-8")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    (claude_home / "settings.json").write_text(_settings_without_plugin_hooks(), encoding="utf-8")

    orchestrator = ResumeOrchestrator(cwd=cwd)
    plan = orchestrator.plan("s1", 0)
    assert "Settings" not in plan.env_diff_text

    report = orchestrator.execute(plan, lambda _text: True)

    assert report.env_state_dir is not None
    runtime_claude_home = Path(report.env_state_dir) / "claude"
    after = json.loads((runtime_claude_home / "settings.json").read_text(encoding="utf-8"))
    assert after["model"] == "sonnet"
    assert after["hooks"] == {}


def test_resume_reverts_plugin_hooks_when_flag_disabled(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    claude_home = home / ".claude"
    cwd = tmp_path / "work"
    cwd.mkdir()
    claude_home.mkdir(parents=True)
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    (claude_home / "settings.json").write_text(_settings_without_plugin_hooks(), encoding="utf-8")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="checkpoint"))

    (claude_home / "settings.json").write_text(_settings_with_plugin_hooks(), encoding="utf-8")

    config = load_config()
    config["ignore_plugin_hook_diffs"] = False
    write_config(config)

    orchestrator = ResumeOrchestrator(cwd=cwd)
    plan = orchestrator.plan("s1", 0)
    assert "Settings" in plan.env_diff_text

    report = orchestrator.execute(plan, lambda _text: True)

    assert report.env_state_dir is not None
    runtime_claude_home = Path(report.env_state_dir) / "claude"
    after = json.loads((runtime_claude_home / "settings.json").read_text(encoding="utf-8"))
    assert after["hooks"] == {}


def _seed_claude_session_for_resume(
    plugin_home, home, cwd, transcript, *, transcript_text, file_history=None, todos=None
):
    claude_home = home / ".claude"
    cwd.mkdir(exist_ok=True)
    transcript.write_text(transcript_text, encoding="utf-8")
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    if file_history:
        history_dir = claude_home / "file-history" / "s1"
        history_dir.mkdir(parents=True, exist_ok=True)
        for name, content in file_history.items():
            (history_dir / name).write_text(content, encoding="utf-8")
    if todos:
        todos_dir = claude_home / "todos"
        todos_dir.mkdir(parents=True, exist_ok=True)
        for suffix, content in todos.items():
            (todos_dir / f"s1-{suffix}").write_text(content, encoding="utf-8")
    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(user_message="hi"),
        TrajectoryReference("claude", str(transcript), 0, transcript.stat().st_size, 0),
    )


def test_resume_extends_latest_turn_to_eof_when_tail_is_complete(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    captured = (
        json.dumps({"type": "user", "promptId": "p-1", "uuid": "u1", "parentUuid": None,
                    "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a1", "parentUuid": "u1",
                      "message": {"role": "assistant", "content": []}}) + "\n"
    )
    _seed_claude_session_for_resume(plugin_home, home, cwd, transcript, transcript_text=captured)

    # Simulate the trailing flush: same promptId records, complete lines.
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "system", "subtype": "stop_hook_summary"}) + "\n")
        handle.write(json.dumps({"type": "system", "subtype": "turn_duration", "durationMs": 12}) + "\n")

    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("s1", 0), lambda _text: True
    )
    materialized = Path(report.provider_session_path)
    records = [json.loads(line) for line in materialized.read_text(encoding="utf-8").splitlines()]
    subtypes = [record.get("subtype") for record in records if record.get("type") == "system"]
    assert "stop_hook_summary" in subtypes
    assert "turn_duration" in subtypes


def test_resume_does_not_extend_when_tail_starts_new_turn(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    captured = (
        json.dumps({"type": "user", "promptId": "p-1", "uuid": "u1", "parentUuid": None,
                    "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a1", "parentUuid": "u1",
                      "message": {"role": "assistant", "content": []}}) + "\n"
    )
    _seed_claude_session_for_resume(plugin_home, home, cwd, transcript, transcript_text=captured)

    # User raced ahead and started turn 2 before resume fired.
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "user", "promptId": "p-2", "uuid": "u2", "parentUuid": "a1",
                                 "message": {"role": "user", "content": "next"}}) + "\n")

    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("s1", 0), lambda _text: True
    )
    materialized = Path(report.provider_session_path)
    prompt_ids = [
        record.get("promptId")
        for record in (json.loads(line) for line in materialized.read_text(encoding="utf-8").splitlines())
        if record.get("promptId") is not None
    ]
    assert "p-2" not in prompt_ids


def test_resume_hardlinks_file_history_and_todos(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    captured = json.dumps({"type": "user", "promptId": "p-1", "uuid": "u1", "parentUuid": None,
                           "message": {"role": "user", "content": "hi"}}) + "\n"
    _seed_claude_session_for_resume(
        plugin_home,
        home,
        cwd,
        transcript,
        transcript_text=captured,
        file_history={"006a1ba@v1": "snapshot-bytes"},
        todos={"agent-x.json": '{"items": []}'},
    )

    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("s1", 0), lambda _text: True
    )

    assert report.env_state_dir is not None
    runtime_claude_home = Path(report.env_state_dir) / "claude"
    src_history = home / ".claude" / "file-history" / "s1" / "006a1ba@v1"
    dst_history = runtime_claude_home / "file-history" / report.new_session_id / "006a1ba@v1"
    assert dst_history.exists()
    assert src_history.stat().st_ino == dst_history.stat().st_ino  # hardlink, not copy

    dst_todo = runtime_claude_home / "todos" / f"{report.new_session_id}-agent-x.json"
    src_todo = home / ".claude" / "todos" / "s1-agent-x.json"
    assert dst_todo.exists()
    assert src_todo.stat().st_ino == dst_todo.stat().st_ino


def test_resume_command_is_set_in_report(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    captured = json.dumps({"type": "user", "promptId": "p-1", "uuid": "u1", "parentUuid": None,
                           "message": {"role": "user", "content": "hi"}}) + "\n"
    _seed_claude_session_for_resume(plugin_home, home, cwd, transcript, transcript_text=captured)

    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("s1", 0), lambda _text: True
    )
    assert report.resume_command == f"checkpoint resume-open {report.new_session_id}"
    resume_open = json.loads(
        (plugin_home / "sessions" / report.new_session_id / "resume-open.json").read_text(encoding="utf-8")
    )
    assert resume_open["env_state_dir"] == report.env_state_dir
    assert resume_open["command"] == ["claude", "--resume", report.new_session_id]


def test_resume_places_claude_json_under_config_dir_for_mcp(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    copy_cwd = tmp_path / "work-copy"
    transcript = tmp_path / "claude.jsonl"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

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
    captured = json.dumps({"type": "user", "promptId": "p-1", "uuid": "u1", "parentUuid": None,
                           "message": {"role": "user", "content": "hi"}}) + "\n"
    _seed_claude_session_for_resume(plugin_home, home, cwd, transcript, transcript_text=captured)

    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("s1", 0),
        lambda _text: ResumeOptions(proceed=True, target_cwd=copy_cwd),
    )

    assert report.env_state_dir is not None
    runtime_claude_home = Path(report.env_state_dir) / "claude"
    runtime_claude_json = runtime_claude_home / ".claude.json"
    assert runtime_claude_json.exists()
    runtime_config = json.loads(runtime_claude_json.read_text(encoding="utf-8"))
    assert runtime_config["mcpServers"] == {
        "context7": {"type": "stdio", "command": "npx"}
    }
    assert runtime_config["projects"][str(copy_cwd)]["disabledMcpServers"] == ["context7"]
    assert not (Path(report.env_state_dir) / ".claude.json").exists()


def test_resume_clears_claude_disabled_mcp_servers_when_active(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    copy_cwd = tmp_path / "work-copy"
    transcript = tmp_path / "claude.jsonl"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    home.mkdir()
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {"context7": {"type": "stdio", "command": "npx"}},
                "projects": {str(cwd): {"disabledMcpServers": []}},
            }
        ),
        encoding="utf-8",
    )
    captured = json.dumps({"type": "user", "promptId": "p-1", "uuid": "u1", "parentUuid": None,
                           "message": {"role": "user", "content": "hi"}}) + "\n"
    _seed_claude_session_for_resume(plugin_home, home, cwd, transcript, transcript_text=captured)

    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("s1", 0),
        lambda _text: ResumeOptions(proceed=True, target_cwd=copy_cwd),
    )

    assert report.env_state_dir is not None
    runtime_config = json.loads(
        (Path(report.env_state_dir) / "claude" / ".claude.json").read_text(encoding="utf-8")
    )
    assert runtime_config["projects"][str(copy_cwd)]["disabledMcpServers"] == []


def test_resume_replays_claude_mcp_delta_when_snapshot_config_was_stale(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    copy_cwd = tmp_path / "work-copy"
    transcript = tmp_path / "claude.jsonl"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

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
    captured = "\n".join(
        [
            json.dumps({"type": "user", "promptId": "p-1", "uuid": "u1", "parentUuid": None,
                        "message": {"role": "user", "content": "hi"}}),
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
    )
    _seed_claude_session_for_resume(plugin_home, home, cwd, transcript, transcript_text=captured)

    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("s1", 0),
        lambda _text: ResumeOptions(proceed=True, target_cwd=copy_cwd),
    )

    assert report.env_state_dir is not None
    runtime_config = json.loads(
        (Path(report.env_state_dir) / "claude" / ".claude.json").read_text(encoding="utf-8")
    )
    assert runtime_config["projects"][str(copy_cwd)]["disabledMcpServers"] == []


def test_resume_command_pins_claude_model_effort_and_permission():
    from checkpoint_plugin.types import EnvironmentState

    target_env = EnvironmentState(
        provider="claude",
        model="Opus 4.8",
        effort="xhigh",
        permission_mode="acceptEdits",
    )

    assert (
        _resume_command("claude", "019e91aa-5d69-7180-8729-1a9a31c7e182", target_env=target_env)
        == "checkpoint resume-open 019e91aa-5d69-7180-8729-1a9a31c7e182"
    )


def test_resume_command_pins_codex_model_and_effort():
    from checkpoint_plugin.types import EnvironmentState

    target_env = EnvironmentState(provider="codex", model="gpt-5.5", effort="xhigh")

    assert (
        _resume_command("codex", "019e91aa-5d69-7180-8729-1a9a31c7e182", target_env=target_env)
        == "checkpoint resume-open 019e91aa-5d69-7180-8729-1a9a31c7e182"
    )


def test_resume_parent_uuid_chain_skips_summary_records(tmp_path, monkeypatch):
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    transcript_text = (
        json.dumps({"type": "user", "promptId": "p-1", "uuid": "u1", "parentUuid": None,
                    "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a1", "parentUuid": "u1",
                      "message": {"role": "assistant", "content": []}}) + "\n"
        + json.dumps({"type": "summary", "uuid": "sum1", "parentUuid": None}) + "\n"
        + json.dumps({"type": "user", "promptId": "p-2", "uuid": "u2", "parentUuid": "a1",
                      "message": {"role": "user", "content": "again"}}) + "\n"
    )
    _seed_claude_session_for_resume(plugin_home, home, cwd, transcript, transcript_text=transcript_text)

    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("s1", 0), lambda _text: True
    )
    records = [
        json.loads(line)
        for line in Path(report.provider_session_path).read_text(encoding="utf-8").splitlines()
    ]
    by_type = {record["type"]: record for record in records if "uuid" in record}
    # The second user record's parentUuid must point at the assistant uuid,
    # NOT at the summary uuid even though summary was written between them.
    assistant_uuid = by_type["assistant"]["uuid"]
    second_user = [record for record in records if record.get("type") == "user" and record.get("promptId") == "p-2"][0]
    assert second_user["parentUuid"] == assistant_uuid


def test_codex_rewrite_repoints_forked_from_id():
    """P4-5: session_meta.forked_from_id must point at the session we forked FROM
    (the original id), not keep the stale ancestor it had on disk."""
    from checkpoint_plugin.resume import _rewrite_codex_trajectory

    trajectory = (
        json.dumps({"type": "session_meta", "payload": {"id": "ORIGINAL", "forked_from_id": "STALE-ANCESTOR", "cwd": "/old"}}) + "\n"
        + json.dumps({"type": "turn_context", "payload": {"turn_id": "t-1", "model": "m"}}) + "\n"
    ).encode("utf-8")
    out = _rewrite_codex_trajectory(trajectory, "NEW", Path("/new"), None, None, None, None)
    metas = [json.loads(l)["payload"] for l in out.splitlines() if json.loads(l).get("type") == "session_meta"]
    assert metas, "expected a session_meta record"
    meta = metas[0]
    assert meta["id"] == "NEW"
    # Lineage now points at the original session, not the stale ancestor.
    assert meta["forked_from_id"] == "ORIGINAL"
    assert meta["forked_from_id"] != "STALE-ANCESTOR"


def test_codex_rewrite_preserves_structured_permission_profile():
    """F1: a real turn_context.permission_profile is an object; resume must not
    overwrite it with the bare permission_mode string. Uses the REAL Codex shape
    (type at record level, payload carries no `type`) so it exercises the live
    path (P4-2)."""
    from checkpoint_plugin.resume import _rewrite_codex_trajectory

    profile = {"type": "managed", "file_system": {"type": "restricted"}, "network": "restricted"}
    trajectory = (
        json.dumps({"type": "session_meta", "payload": {"id": "old", "cwd": "/old"}}) + "\n"
        + json.dumps(
            {
                "type": "turn_context",
                "payload": {
                    "turn_id": "t-1",
                    "model": "old-model",
                    "permission_profile": profile,
                    "sandbox_policy": {"type": "workspace-write"},
                    "approval_policy": "on-request",
                },
            }
        )
        + "\n"
    ).encode("utf-8")

    out = _rewrite_codex_trajectory(trajectory, "NEW", Path("/new"), "gpt-target", "acceptEdits", None, None)
    records = [json.loads(line) for line in out.splitlines()]
    turn_context = next(r["payload"] for r in records if r.get("type") == "turn_context")
    # Structured profile is preserved verbatim; model is re-pinned; sandbox/approval untouched.
    assert turn_context["permission_profile"] == profile
    assert turn_context["model"] == "gpt-target"
    assert turn_context["sandbox_policy"] == {"type": "workspace-write"}
    assert turn_context["approval_policy"] == "on-request"


def test_codex_rewrite_repins_model_on_real_turn_context_shape():
    """P4-2: model must be re-pinned on turn_context even when `type` lives only
    at the record level (the real Codex shape)."""
    from checkpoint_plugin.resume import _rewrite_codex_trajectory

    trajectory = (
        json.dumps({"type": "session_meta", "payload": {"id": "old", "cwd": "/old"}}) + "\n"
        + json.dumps({"type": "turn_context", "payload": {"turn_id": "t-1", "model": "old-model"}}) + "\n"
    ).encode("utf-8")
    out = _rewrite_codex_trajectory(trajectory, "NEW", Path("/new"), "gpt-target", None, None, None)
    tc = next(json.loads(l)["payload"] for l in out.splitlines() if json.loads(l).get("type") == "turn_context")
    assert tc["model"] == "gpt-target"


def test_codex_rewrite_repins_string_permission_profile():
    """Legacy/simple string permission_profile is still re-pinned for back-compat."""
    from checkpoint_plugin.resume import _rewrite_codex_trajectory

    trajectory = (
        json.dumps({"type": "session_meta", "payload": {"id": "old", "cwd": "/old"}}) + "\n"
        + json.dumps(
            {"type": "turn_context", "payload": {"permission_profile": "old", "sandbox_policy": "workspace-write"}}
        )
        + "\n"
    ).encode("utf-8")
    out = _rewrite_codex_trajectory(trajectory, "NEW", Path("/new"), None, "acceptEdits", None, None)
    tc = next(json.loads(l)["payload"] for l in out.splitlines() if json.loads(l).get("type") == "turn_context")
    assert tc["permission_profile"] == "acceptEdits"
    assert tc["sandbox_policy"] == "workspace-write"


def test_claude_rewrite_repins_model_on_assistant_message():
    """F2: Claude model lives at message.model on assistant records, not top-level.
    F8: sessionId is rewritten only on records that already carry it (native FHS
    records have none and must not gain one)."""
    from checkpoint_plugin.resume import _rewrite_claude_trajectory

    trajectory = (
        json.dumps({"type": "user", "sessionId": "old", "uuid": "u1", "parentUuid": None, "promptId": "p", "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "sessionId": "old", "uuid": "a1", "parentUuid": "u1", "message": {"role": "assistant", "model": "claude-opus-4-8", "content": []}}) + "\n"
        + json.dumps({"type": "file-history-snapshot", "messageId": "a1", "snapshot": {}, "isSnapshotUpdate": False}) + "\n"
    ).encode("utf-8")
    out = _rewrite_claude_trajectory(trajectory, "NEW", Path("/new"), "claude-sonnet-4-6", None, None)
    records = [json.loads(line) for line in out.splitlines()]
    assistant = next(r for r in records if r.get("type") == "assistant")
    assert assistant["message"]["model"] == "claude-sonnet-4-6"
    # F8: sessionId rewritten on records that carry it; FHS keeps its native key-set
    # (no sessionId added).
    assert {r["sessionId"] for r in records if "sessionId" in r} == {"NEW"}
    fhs = next(r for r in records if r.get("type") == "file-history-snapshot")
    assert "sessionId" not in fhs
    assert set(fhs.keys()) == {"type", "messageId", "snapshot", "isSnapshotUpdate"}


def test_claude_rewrite_remaps_file_history_message_id():
    """P4-4: file-history-snapshot.messageId and last-prompt.leafUuid must be
    remapped through the uuid map (incl. forward references), not left dangling."""
    from checkpoint_plugin.resume import _rewrite_claude_trajectory

    # messageId points FORWARD to an assistant uuid that appears later.
    trajectory = (
        json.dumps({"type": "file-history-snapshot", "messageId": "a1", "snapshot": {}}) + "\n"
        + json.dumps({"type": "user", "uuid": "u1", "parentUuid": None, "promptId": "p", "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a1", "parentUuid": "u1", "message": {"role": "assistant", "content": []}}) + "\n"
        + json.dumps({"type": "last-prompt", "leafUuid": "a1"}) + "\n"
    ).encode("utf-8")
    out = _rewrite_claude_trajectory(trajectory, "NEW", Path("/new"), None, None, None)
    records = [json.loads(line) for line in out.splitlines()]
    new_uuids = {r["uuid"] for r in records if isinstance(r.get("uuid"), str)}
    fhs = next(r for r in records if r.get("type") == "file-history-snapshot")
    last_prompt = next(r for r in records if r.get("type") == "last-prompt")
    assistant = next(r for r in records if r.get("type") == "assistant")
    # Pointers were remapped to the NEW assistant uuid, not left as "a1".
    assert fhs["messageId"] != "a1"
    assert fhs["messageId"] == assistant["uuid"]
    assert fhs["messageId"] in new_uuids
    assert last_prompt["leafUuid"] == assistant["uuid"]


def test_claude_rewrite_remaps_nested_snapshot_message_id():
    """P6-7: file-history-snapshot also nests snapshot.messageId; it must be remapped
    through the uuid map so it doesn't dangle after the rename (breaks rewind)."""
    from checkpoint_plugin.resume import _rewrite_claude_trajectory

    trajectory = (
        json.dumps({"type": "user", "uuid": "u1", "parentUuid": None, "promptId": "p", "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a1", "parentUuid": "u1", "message": {"role": "assistant", "content": []}}) + "\n"
        # nested snapshot.messageId points at the assistant uuid a1
        + json.dumps({"type": "file-history-snapshot", "messageId": "a1", "snapshot": {"messageId": "a1", "files": {}}}) + "\n"
    ).encode("utf-8")
    out = _rewrite_claude_trajectory(trajectory, "NEW", Path("/new"), None, None, None)
    records = [json.loads(line) for line in out.splitlines()]
    assistant = next(r for r in records if r.get("type") == "assistant")
    fhs = next(r for r in records if r.get("type") == "file-history-snapshot")
    # Both the top-level AND the nested pointer track the renamed assistant uuid.
    assert fhs["messageId"] == assistant["uuid"]
    assert fhs["snapshot"]["messageId"] == assistant["uuid"]
    assert fhs["snapshot"]["messageId"] != "a1"
    # No field still holds the old uuid as its exact value.
    assert fhs["messageId"] != "a1"
    assert "a1" not in {r.get("uuid") for r in records}


def test_resume_forked_session_includes_inherited_prefix(tmp_path, monkeypatch):
    """F3: resuming a forked session (first turn anchored mid-transcript) must
    materialize the inherited pre-fork records, not start amnesiac."""
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    claude_home = home / ".claude"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    # Inherited pre-fork history, then the forked turn (new promptId).
    inherited = (
        json.dumps({"type": "mode", "mode": "normal", "sessionId": "old"}) + "\n"
        + json.dumps({"type": "user", "sessionId": "old", "uuid": "iu", "parentUuid": None, "promptId": "old-p", "cwd": "/old", "message": {"role": "user", "content": "INHERITED-PROMPT"}}) + "\n"
        + json.dumps({"type": "assistant", "sessionId": "old", "uuid": "ia", "parentUuid": "iu", "cwd": "/old", "message": {"role": "assistant", "content": []}}) + "\n"
    )
    fork_offset = len(inherited.encode("utf-8"))
    forked_turn = json.dumps({"type": "user", "sessionId": "old", "uuid": "fu", "parentUuid": "ia", "promptId": "fork-p", "cwd": "/old", "message": {"role": "user", "content": "FORKED-PROMPT"}}) + "\n"
    transcript.write_text(inherited + forked_turn, encoding="utf-8")
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    coordinator = CheckpointCoordinator(session_id="forked", cwd=cwd)
    coordinator.on_session_start(source="resume", source_transcript_path=str(transcript))
    # The captured turn anchors at the fork offset, mirroring real on-disk forks.
    coordinator.on_turn_end(
        TurnRecord(user_message="forked"),
        TrajectoryReference("claude", str(transcript), fork_offset, transcript.stat().st_size, 1),
    )

    report = ResumeOrchestrator(cwd=cwd).execute(ResumeOrchestrator(cwd=cwd).plan("forked", 0), lambda _text: True)
    materialized = Path(report.provider_session_path).read_text(encoding="utf-8")
    # Both the inherited history AND the forked turn are present in the resume.
    assert "INHERITED-PROMPT" in materialized
    assert "FORKED-PROMPT" in materialized
    records = [json.loads(line) for line in materialized.splitlines()]
    assert {r["sessionId"] for r in records} == {report.new_session_id}


def test_resume_of_a_resume_preserves_all_records(tmp_path, monkeypatch):
    """P4-3: resuming a resumed session must not drop records. The resumed
    manifests' byte offsets must align to the REWRITTEN provider file, else the
    next resume raw-seeks mid-line and loses a record per generation."""
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    # Leading control record + two turns (so the rewriter inserts a synthetic
    # permission-mode record and re-serializes, shifting byte offsets).
    lines = [
        json.dumps({"type": "mode", "mode": "normal", "sessionId": "old"}),
        json.dumps({"type": "user", "sessionId": "old", "uuid": "u1", "parentUuid": None, "promptId": "p1", "cwd": "/old", "message": {"role": "user", "content": "turn one"}}),
        json.dumps({"type": "assistant", "sessionId": "old", "uuid": "a1", "parentUuid": "u1", "cwd": "/old", "message": {"role": "assistant", "model": "m", "content": []}}),
        json.dumps({"type": "user", "sessionId": "old", "uuid": "u2", "parentUuid": "a1", "promptId": "p2", "cwd": "/old", "message": {"role": "user", "content": "turn two"}}),
        json.dumps({"type": "assistant", "sessionId": "old", "uuid": "a2", "parentUuid": "u2", "cwd": "/old", "message": {"role": "assistant", "model": "m", "content": []}}),
    ]
    data = ("\n".join(lines) + "\n").encode("utf-8")
    transcript.write_bytes(data)
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")
    monkeypatch.setenv("CLAUDE_PERMISSION_MODE", "acceptEdits")

    # Two turns sliced by promptId boundary.
    turn0_end = data.find(b'"p2"')
    turn0_end = data.rfind(b"\n", 0, turn0_end) + 1
    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(TurnRecord(user_message="one"), TrajectoryReference("claude", str(transcript), 0, turn0_end, 3))
    coordinator.on_turn_end(TurnRecord(user_message="two"), TrajectoryReference("claude", str(transcript), turn0_end, len(data), 2))

    def _count(path):
        return sum(1 for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip())

    gen1 = ResumeOrchestrator(cwd=cwd).execute(ResumeOrchestrator(cwd=cwd).plan("s1", 1), lambda _t: True)
    gen1_records = _count(gen1.provider_session_path)

    # Resume the resumed session — this is where stale offsets would drop records.
    gen2 = ResumeOrchestrator(cwd=cwd).execute(ResumeOrchestrator(cwd=cwd).plan(gen1.new_session_id, 1), lambda _t: True)
    gen2_records = _count(gen2.provider_session_path)

    assert gen2_records == gen1_records, f"resume-of-resume dropped records: {gen1_records} -> {gen2_records}"
    # Chain still coherent after two generations.
    recs = [json.loads(line) for line in Path(gen2.provider_session_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {r["sessionId"] for r in recs} == {gen2.new_session_id}
    seen: set[str] = set()
    for r in recs:
        pu = r.get("parentUuid")
        if isinstance(pu, str):
            assert pu in seen, "broken parentUuid chain after resume-of-resume"
        if isinstance(r.get("uuid"), str):
            seen.add(r["uuid"])


def test_fork_resume_of_resume_does_not_reinject_permission_mode(tmp_path, monkeypatch):
    """P7-3: resuming a fork-prefix resume must not re-inject a synthetic
    permission-mode on the second hop. The byte-offset inherited-prefix signal is
    lost after a round-trip (the prefix folds into turn 0 at byte 0), but the
    `forkedFrom` stamp on the inherited records persists, so the verdict stays True
    and no synthetic permission-mode is added gen-over-gen."""
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    # Inherited pre-fork history carrying forkedFrom (as a captured native/plugin
    # fork would), then the forked turn (new promptId) anchored past byte 0.
    inherited = (
        json.dumps({"type": "user", "sessionId": "old", "uuid": "iu", "parentUuid": None, "promptId": "old-p", "cwd": "/old", "forkedFrom": {"sessionId": "anc", "messageUuid": "iu"}, "message": {"role": "user", "content": "INHERITED"}}) + "\n"
        + json.dumps({"type": "assistant", "sessionId": "old", "uuid": "ia", "parentUuid": "iu", "cwd": "/old", "forkedFrom": {"sessionId": "anc", "messageUuid": "ia"}, "message": {"role": "assistant", "model": "m", "content": []}}) + "\n"
    )
    fork_offset = len(inherited.encode("utf-8"))
    forked_turn = json.dumps({"type": "user", "sessionId": "old", "uuid": "fu", "parentUuid": "ia", "promptId": "fork-p", "cwd": "/old", "message": {"role": "user", "content": "FORKED"}}) + "\n"
    transcript.write_text(inherited + forked_turn, encoding="utf-8")
    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")
    monkeypatch.setenv("CLAUDE_PERMISSION_MODE", "acceptEdits")

    coordinator = CheckpointCoordinator(session_id="forked", cwd=cwd)
    coordinator.on_session_start(source="resume", source_transcript_path=str(transcript))
    coordinator.on_turn_end(
        TurnRecord(user_message="forked"),
        TrajectoryReference("claude", str(transcript), fork_offset, transcript.stat().st_size, 1),
    )

    def _counts(path):
        recs = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
        return len(recs), sum(1 for r in recs if r.get("type") == "permission-mode")

    gen1 = ResumeOrchestrator(cwd=cwd).execute(ResumeOrchestrator(cwd=cwd).plan("forked", 0), lambda _t: True)
    n1, pm1 = _counts(gen1.provider_session_path)
    gen2 = ResumeOrchestrator(cwd=cwd).execute(ResumeOrchestrator(cwd=cwd).plan(gen1.new_session_id, 0), lambda _t: True)
    n2, pm2 = _counts(gen2.provider_session_path)

    # A fork-prefix resume never carries a synthetic permission-mode, and that holds
    # across the second hop (no drift).
    assert pm1 == 0, "fork-prefix resume should not inject a synthetic permission-mode"
    assert pm2 == 0, f"resume-of-resume re-injected a permission-mode: {pm1} -> {pm2}"
    assert n2 == n1, f"resume-of-resume drifted record count: {n1} -> {n2}"


def test_has_inherited_prefix_survives_resume_round_trip():
    """P7-3: the inherited-prefix verdict must be idempotent across resume hops.

    The byte-offset signal (earliest span start > 0) is lost when a resumed
    session is captured and resumed again — realign folds the inherited prefix
    back into turn 0 at byte 0. The `forkedFrom` stamp persists in the bytes, so
    it keeps the verdict True and prevents re-injecting a synthetic permission-mode
    every generation.
    """
    from checkpoint_plugin.resume import _has_inherited_prefix

    # Fresh native fork: earliest turn anchors past byte 0 -> True via offset.
    assert _has_inherited_prefix({0: (512, 1024, 3)}) is True
    # Normal new session: turn 0 at byte 0, no forkedFrom -> False.
    assert _has_inherited_prefix({0: (0, 1024, 3)}) is False
    assert _has_inherited_prefix({0: (0, 1024, 3)}, b'{"type":"user"}\n') is False
    # Resume-of-resume: prefix folded to byte 0 BUT forkedFrom in the bytes -> True.
    folded = b'{"type":"user","forkedFrom":{"sessionId":"anc","messageUuid":"u"}}\n'
    assert _has_inherited_prefix({0: (0, 1024, 3)}, folded) is True
    # No spans at all but forkedFrom present -> still True.
    assert _has_inherited_prefix({}, folded) is True
    assert _has_inherited_prefix({}) is False


def test_codex_rewrite_preserves_session_meta_chain():
    """F2: native codex resume/fork keeps the FULL inlined ancestor session_meta
    chain (depth-scaled: startup=1, resume=2, fork-of-fork=3) and prepends a fresh
    head meta. The old behavior collapsed every chain to one meta; that was
    non-native. The head points forked_from_id at the source (first) id; each inlined
    meta keeps its own id + forked_from_id verbatim."""
    from checkpoint_plugin.resume import _rewrite_codex_trajectory

    trajectory = (
        json.dumps({"type": "session_meta", "payload": {"id": "SUB", "forked_from_id": "PARENT", "cwd": "/old"}}) + "\n"
        + json.dumps({"type": "session_meta", "payload": {"id": "PARENT", "forked_from_id": "GRAND", "cwd": "/old"}}) + "\n"
        + json.dumps({"type": "session_meta", "payload": {"id": "GRAND", "cwd": "/old"}}) + "\n"
        + json.dumps({"type": "turn_context", "payload": {"turn_id": "t-1", "model": "m"}}) + "\n"
    ).encode("utf-8")
    out = _rewrite_codex_trajectory(trajectory, "NEW", Path("/new"), None, None, None, None)
    records = [json.loads(line) for line in out.splitlines()]
    metas = [r for r in records if r.get("type") == "session_meta"]
    # Fresh head + the three inlined source metas.
    assert len(metas) == 4
    assert records[0]["type"] == "session_meta"
    # Fresh head: new id, forked_from_id at idx1 pointing at the source head id.
    head = metas[0]["payload"]
    assert head["id"] == "NEW"
    assert list(head.keys())[:2] == ["id", "forked_from_id"]
    assert head["forked_from_id"] == "SUB"
    # Inlined chain preserved verbatim (own id + own forked_from_id), cwd re-pinned.
    assert [m["payload"]["id"] for m in metas[1:]] == ["SUB", "PARENT", "GRAND"]
    assert metas[1]["payload"]["forked_from_id"] == "PARENT"
    assert metas[2]["payload"]["forked_from_id"] == "GRAND"
    assert "forked_from_id" not in metas[3]["payload"]
    assert all(m["payload"]["cwd"] == "/new" for m in metas)


def test_rewrite_preserves_source_key_order_not_alphabetical():
    """P7-1: resumed provider records must keep native (insertion) key order, not
    be alphabetized. sort_keys re-serialization was a 100%-vs-0% fingerprint
    distinguishing every resumed record from a native one."""
    from collections import OrderedDict
    from checkpoint_plugin.resume import _rewrite_claude_trajectory, _rewrite_codex_trajectory

    def _key_order(line: bytes) -> list[str]:
        return list(json.loads(line, object_pairs_hook=OrderedDict).keys())

    # Claude: a deliberately non-alphabetical native key order must round-trip as-is.
    claude_src = (
        json.dumps({"type": "user", "sessionId": "old", "uuid": "u1", "parentUuid": None, "promptId": "p1", "cwd": "/old", "message": {"role": "user", "content": "hi"}}) + "\n"
    ).encode("utf-8")
    claude_out = _rewrite_claude_trajectory(claude_src, "NEW", Path("/new"), "m", None, None)
    claude_keys = _key_order(claude_out.splitlines()[0])
    assert claude_keys == ["type", "sessionId", "uuid", "parentUuid", "promptId", "cwd", "message"]
    assert claude_keys != sorted(claude_keys), "claude record must NOT be alphabetized"

    # Codex: synthetic + rewritten meta keeps native payload order (id, timestamp, cwd, ...).
    codex_src = (
        json.dumps({"timestamp": "t", "type": "session_meta", "payload": {"id": "old", "timestamp": "t", "cwd": "/old", "originator": "X"}}) + "\n"
        + json.dumps({"type": "turn_context", "payload": {"turn_id": "t-1", "model": "m"}}) + "\n"
    ).encode("utf-8")
    codex_out = _rewrite_codex_trajectory(codex_src, "NEW", Path("/new"), "m", None, None, None)
    codex_top = _key_order(codex_out.splitlines()[0])
    assert codex_top == ["timestamp", "type", "payload"]
    assert codex_top != sorted(codex_top), "codex meta must NOT be alphabetized"

    # P8-F3: native rollouts/transcripts use COMPACT separators (no space after
    # `,`/`:`). Python's default spacing was a 100%-vs-0% fingerprint AND shifted
    # downstream byte offsets. Both rewriters route through `_json_line`, so every
    # emitted line must be compact. (`", "` only appears with the default
    # separators; a compact line has `","`.)
    for out in (claude_out, codex_out):
        for line in out.splitlines():
            if not line.strip():
                continue
            assert b'", "' not in line and b'": ' not in line, (
                f"resumed record must use compact JSON separators, got: {line!r}"
            )
            # round-trips (compact is still valid JSON)
            assert isinstance(json.loads(line), dict)



def test_codex_rewrite_keeps_full_inlined_meta_chain_with_original_ids():
    """F2: native codex resume keeps the source's FULL inlined meta history verbatim
    (each meta keeps its ORIGINAL id), and prepends a single fresh head meta. This
    supersedes the old P6-3 collapse/drop-ancestor model. Modeled on a fork whose
    inlined history carries BOTH same-head-id continuation metas (in-place rollback
    restarts) and different-id inlined-ancestor metas, appearing leading AND
    mid-stream — all are part of the source's real on-disk history and a native
    resume replays them as-is under their original ids."""
    from checkpoint_plugin.resume import _rewrite_codex_trajectory

    HEAD, ANCESTOR = "019e648f", "019e644b"
    trajectory = (
        # head/fork meta — carries forked_from_id (it IS a fork of the ancestor)
        json.dumps({"type": "session_meta", "payload": {"id": HEAD, "forked_from_id": ANCESTOR, "cwd": "/old"}}) + "\n"
        # leading inlined ancestor meta — id != head, forked_from_id=None
        + json.dumps({"type": "session_meta", "payload": {"id": ANCESTOR, "cwd": "/old"}}) + "\n"
        + json.dumps({"type": "turn_context", "payload": {"turn_id": "t-1", "model": "m"}}) + "\n"
        + json.dumps({"type": "response_item", "payload": {"type": "message"}}) + "\n"
        # MID-STREAM inlined ancestor meta — id != head
        + json.dumps({"type": "session_meta", "payload": {"id": ANCESTOR, "cwd": "/old"}}) + "\n"
        + json.dumps({"type": "turn_context", "payload": {"turn_id": "t-2", "model": "m"}}) + "\n"
        # MID-STREAM same-head-id meta — this fork's own restart marker
        + json.dumps({"type": "session_meta", "payload": {"id": HEAD, "forked_from_id": ANCESTOR, "cwd": "/old"}}) + "\n"
        + json.dumps({"type": "turn_context", "payload": {"turn_id": "t-3", "model": "m"}}) + "\n"
    ).encode("utf-8")

    out = _rewrite_codex_trajectory(trajectory, "NEW", Path("/new"), None, None, None, None)
    records = [json.loads(line) for line in out.splitlines()]
    metas = [r for r in records if r.get("type") == "session_meta"]
    # Fresh head (id=NEW) + all four inlined source metas preserved with original ids.
    assert len(metas) == 5
    assert records[0]["type"] == "session_meta"
    assert metas[0]["payload"]["id"] == "NEW"
    assert metas[0]["payload"]["forked_from_id"] == HEAD
    assert [m["payload"]["id"] for m in metas[1:]] == [HEAD, ANCESTOR, ANCESTOR, HEAD]
    # cwd re-pinned on every meta; original ids otherwise untouched.
    assert all(m["payload"]["cwd"] == "/new" for m in metas)
    # All turns survive (no records dropped between metas).
    turn_ids = {r["payload"].get("turn_id") for r in records if r.get("type") == "turn_context"}
    assert turn_ids == {"t-1", "t-2", "t-3"}



def test_codex_rewrite_keeps_thread_rolled_back():
    """F11: native codex forks REPLAY thread_rolled_back verbatim (verified on
    a67e idx 35/55 and 8c17 idx 34, both reload fine), and inside a captured turn
    it is the real edit-and-resend seam. The old M1 strip both diverged from native
    and erased that seam, so the rewrite must keep it."""
    from checkpoint_plugin.resume import _rewrite_codex_trajectory

    trajectory = (
        json.dumps({"type": "session_meta", "payload": {"id": "old", "cwd": "/old"}}) + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "thread_rolled_back", "num_turns": 1}}) + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "task_started"}}) + "\n"
    ).encode("utf-8")
    out = _rewrite_codex_trajectory(trajectory, "NEW", Path("/new"), None, None, None, None)
    assert b"thread_rolled_back" in out
    assert b"task_started" in out


def test_codex_rewrite_preserves_structured_source_and_provenance():
    """P6-1/P6-11: provenance fields are carried verbatim from source_meta and the
    Desktop/vscode/user defaults only fill when a field is ABSENT — a structured
    subagent `source` dict and a CLI/TUI entrypoint must never be clobbered."""
    from checkpoint_plugin.resume import _rewrite_codex_trajectory

    # (a) subagent meta: dict source + thread_source=subagent + agent_nickname.
    sub_source = {"subagent": {"thread_spawn": {"parent": "p"}}}
    trajectory = (
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": "old",
                    "cwd": "/old",
                    "source": sub_source,
                    "thread_source": "subagent",
                    "agent_nickname": "Tesla",
                    "originator": "codex_cli_rs",
                },
            }
        )
        + "\n"
        + json.dumps({"type": "turn_context", "payload": {"turn_id": "t-1", "model": "m"}}) + "\n"
    ).encode("utf-8")
    out = _rewrite_codex_trajectory(trajectory, "NEW", Path("/new"), None, None, None, None)
    metas = [json.loads(l)["payload"] for l in out.splitlines() if json.loads(l).get("type") == "session_meta"]
    # F2: the structured-source meta is kept as the INLINED source meta (records[1]),
    # under its original id; the fresh head (records[0]) is the new session.
    assert metas[0]["id"] == "NEW"
    inlined = metas[1]
    assert inlined["id"] == "old"
    assert inlined["source"] == sub_source, "structured dict source must survive, not coerced to 'vscode'"
    assert inlined["thread_source"] == "subagent"
    assert inlined["agent_nickname"] == "Tesla"
    assert inlined["originator"] == "codex_cli_rs"

    # (b) meta missing provenance fields → defaults applied (on both head + inlined).
    bare = (
        json.dumps({"type": "session_meta", "payload": {"id": "old", "cwd": "/old"}}) + "\n"
        + json.dumps({"type": "turn_context", "payload": {"turn_id": "t-1", "model": "m"}}) + "\n"
    ).encode("utf-8")
    out2 = _rewrite_codex_trajectory(bare, "NEW", Path("/new"), None, None, None, None)
    meta2 = next(json.loads(l)["payload"] for l in out2.splitlines() if json.loads(l).get("type") == "session_meta")
    assert meta2["source"] == "vscode"
    assert meta2["originator"] == "Codex Desktop"
    assert meta2["thread_source"] == "user"


def test_codex_synthetic_meta_preserves_nickname_and_forked_from_id():
    """P6-11: when the trajectory has no leading session_meta, the synthetic meta
    must still carry agent_nickname and forked_from_id from source_meta."""
    from checkpoint_plugin.resume import _codex_session_meta

    source_meta = {
        "source": {"subagent": {}},
        "thread_source": "subagent",
        "agent_nickname": "Dewey",
        "forked_from_id": "ANCESTOR",
    }
    record = _codex_session_meta("NEW", Path("/new"), source_meta)
    payload = record["payload"]
    assert payload["agent_nickname"] == "Dewey"
    assert payload["forked_from_id"] == "ANCESTOR"
    assert payload["source"] == {"subagent": {}}
    assert payload["thread_source"] == "subagent"


def test_codex_session_index_uses_zulu_timestamp(tmp_path):
    """P6-4: the appended session_index entry's updated_at must end with Z."""
    import datetime as _dt
    from checkpoint_plugin.resume import _append_codex_session_index

    _append_codex_session_index(tmp_path, "NEW", "a title")
    line = (tmp_path / "session_index.jsonl").read_text().strip().splitlines()[-1]
    entry = json.loads(line)
    assert entry["updated_at"].endswith("Z")
    # Round-trips as RFC3339 (Z → +00:00 for fromisoformat).
    parsed = _dt.datetime.fromisoformat(entry["updated_at"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_codex_session_index_thread_name_never_null(tmp_path, monkeypatch):
    """P6-5: with no recorded session_title, the derived title comes from the TARGET
    turn (not turn 0) and is always a non-empty string."""
    from types import SimpleNamespace
    from checkpoint_plugin.resume import _derive_session_title
    from checkpoint_plugin.store import CheckpointStore
    from checkpoint_plugin.types import CheckpointManifest

    store = CheckpointStore(tmp_path / "sess")

    def _mk(turn_id, preview):
        return CheckpointManifest(
            turn_id=turn_id,
            session_id="s",
            created_ts="2026-01-01T00:00:00Z",
            env_ref="e",
            fs_ref="f",
            user_message_preview=preview,
        )

    store.write_manifest(_mk(0, "inherited unrelated context"))
    store.write_manifest(_mk(1, "the real target prompt"))
    store.write_manifest(_mk(2, "a later turn not included"))

    title = _derive_session_title(store, SimpleNamespace(turn_id=1))
    assert title == "the real target prompt"
    assert title != "inherited unrelated context"

    # Target preview empty → fall back to nearest preceding non-empty preview.
    store2 = CheckpointStore(tmp_path / "sess2")
    store2.write_manifest(_mk(0, "first prompt"))
    store2.write_manifest(_mk(1, ""))
    assert _derive_session_title(store2, SimpleNamespace(turn_id=1)) == "first prompt"

    # No previews anywhere → constant fallback, never null/empty.
    store3 = CheckpointStore(tmp_path / "sess3")
    store3.write_manifest(_mk(0, ""))
    assert _derive_session_title(store3, SimpleNamespace(turn_id=0)) == "Resumed session"



def test_render_diff_shows_effort_change():
    """M3: a thinking-effort drift must be surfaced in the rendered diff."""
    from checkpoint_plugin.env.differ import diff_environments, render_diff
    from checkpoint_plugin.types import EnvironmentState

    current = EnvironmentState(provider="claude", effort="high")
    target = EnvironmentState(provider="claude", effort="medium")
    diff = diff_environments(current, target)
    assert diff.effort_changed
    rendered = render_diff(diff, current, target)
    assert "Effort: high -> medium" in rendered


def test_claude_rewrite_drops_dangling_trailing_pointer():
    """M4: a trailing keyless file-history-snapshot whose messageId points at a
    uuid outside the slice (a forward reference) is dropped, not left dangling."""
    from checkpoint_plugin.resume import _rewrite_claude_trajectory

    trajectory = (
        json.dumps({"type": "user", "uuid": "u-1", "sessionId": "old", "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a-1", "parentUuid": "u-1", "sessionId": "old", "message": {"role": "assistant", "content": "yo"}}) + "\n"
        + json.dumps({"type": "file-history-snapshot", "messageId": "FORWARD-MISSING", "snapshot": {}}) + "\n"
    ).encode("utf-8")
    out = _rewrite_claude_trajectory(trajectory, "NEW", Path("/new"), None, None, None)
    records = [json.loads(line) for line in out.splitlines()]
    assert all(r.get("type") != "file-history-snapshot" for r in records), "dangling snapshot must be dropped"
    # The real message records survive and are remapped.
    assert any(r.get("type") == "assistant" for r in records)


def test_resume_subagent_session_refuses_with_parent_redirect(tmp_path, monkeypatch):
    """H2: resuming a subagent checkpoint standalone is refused; the error names
    the parent session and a redirect command."""
    import pytest

    plugin_home = tmp_path / "plugin"
    cwd = tmp_path / "work"
    cwd.mkdir()
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))

    # Parent session with one turn.
    parent = CheckpointCoordinator(session_id="parent-1", cwd=cwd)
    parent.on_session_start()
    parent.on_turn_end(TurnRecord(user_message="spawn agent agent-xyz"))

    # Subagent session keyed under the parent, carrying lineage metadata.
    sub = CheckpointCoordinator(session_id="parent-1--subagent-agent-xyz", cwd=cwd)
    sub.on_session_start(
        source="subagent",
        lineage={"parent_session_id": "parent-1", "agent_id": "agent-xyz"},
    )
    sub.on_turn_end(TurnRecord(user_message="sub work"))

    orchestrator = ResumeOrchestrator(cwd=cwd)
    with pytest.raises(RuntimeError) as exc:
        orchestrator.plan("parent-1--subagent-agent-xyz", 0)
    message = str(exc.value)
    assert "parent-1" in message
    assert "checkpoint resume parent-1" in message


def test_parent_resume_rewrites_carried_subagent_sessionid(tmp_path, monkeypatch):
    """H3: when a parent resume carries subagent transcripts, each carried
    record's sessionId is rewritten to the new parent id while uuid/parentUuid
    stay byte-identical."""
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    claude_home = home / ".claude"
    cwd = tmp_path / "work"
    transcript = tmp_path / "claude.jsonl"
    cwd.mkdir()
    transcript.write_text(
        "\n".join(
            [
                json.dumps({"type": "permission-mode", "sessionId": "old", "permissionMode": "default"}),
                json.dumps({"type": "user", "sessionId": "old", "uuid": "u-1", "parentUuid": None, "cwd": "/old", "message": {"role": "user", "content": "hi"}}),
                json.dumps({"type": "assistant", "sessionId": "old", "uuid": "a-1", "parentUuid": "u-1", "cwd": "/old", "message": {"role": "assistant", "content": []}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    # A subagent transcript stored under the OLD parent session id.
    project = str(cwd).replace("/", "-")
    sub_dir = claude_home / "projects" / project / "s1" / "subagents"
    sub_dir.mkdir(parents=True)
    sub_file = sub_dir / "agent-abc.jsonl"
    sub_records = [
        {"type": "user", "sessionId": "s1", "uuid": "sub-u1", "parentUuid": None, "cwd": "/old", "isSidechain": True, "sourceToolAssistantUUID": "sub-u1", "message": {"role": "user", "content": "go"}},
        {"type": "assistant", "sessionId": "s1", "uuid": "sub-a1", "parentUuid": "sub-u1", "cwd": "/old", "isSidechain": True, "message": {"role": "assistant", "content": []}},
    ]
    sub_file.write_text("\n".join(json.dumps(r) for r in sub_records) + "\n", encoding="utf-8")

    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    coordinator = CheckpointCoordinator(session_id="s1", cwd=cwd)
    coordinator.on_session_start()
    coordinator.on_turn_end(
        TurnRecord(user_message="first"),
        TrajectoryReference("claude", str(transcript), 0, transcript.stat().st_size, 3),
    )

    report = ResumeOrchestrator(cwd=cwd).execute(ResumeOrchestrator(cwd=cwd).plan("s1", 0), lambda _text: True)

    assert report.env_state_dir is not None
    runtime_claude_home = Path(report.env_state_dir) / "claude"
    carried = runtime_claude_home / "projects" / project / report.new_session_id / "subagents" / "agent-abc.jsonl"
    assert carried.exists()
    out = [json.loads(line) for line in carried.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {r["sessionId"] for r in out} == {report.new_session_id}
    # Internal uuid/parentUuid chain is preserved verbatim (self-contained sidechain).
    assert [r["uuid"] for r in out] == ["sub-u1", "sub-a1"]
    assert [r.get("parentUuid") for r in out] == [None, "sub-u1"]
    # P6-8: cwd is rewritten to the resume cwd on every record...
    assert {r["cwd"] for r in out} == {str(cwd)}
    # ...but sourceToolAssistantUUID is an intra-sidechain pointer and is left
    # byte-identical (NOT remapped through the parent's uuid map).
    assert out[0]["sourceToolAssistantUUID"] == "sub-u1"


def _write_jsonl(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def test_realign_handles_interior_record_drop(tmp_path):
    """P6-2: a 3-turn rewritten codex file where a record was dropped in turn 1 must
    still produce gap-free, line-aligned spans with a correct turn-2 boundary."""
    from checkpoint_plugin.resume import _realign_spans_to_provider_file

    # Rewritten file: leading session_meta (keyless), then 3 turns. Turn 1 has only
    # ONE content record (a record was dropped vs the pre-rewrite count of 2).
    records = [
        {"type": "session_meta", "payload": {"id": "S"}},                      # keyless -> turn 0
        {"type": "turn_context", "payload": {"turn_id": "t0"}},                # turn 0
        {"type": "response_item", "payload": {"turn_id": "t0", "n": 1}},       # turn 0
        {"type": "turn_context", "payload": {"turn_id": "t1"}},                # turn 1
        {"type": "response_item", "payload": {"turn_id": "t1", "n": 1}},       # turn 1 (the 2nd was dropped)
        {"type": "turn_context", "payload": {"turn_id": "t2"}},                # turn 2
        {"type": "response_item", "payload": {"turn_id": "t2", "n": 1}},       # turn 2
    ]
    f = _write_jsonl(tmp_path / "rollout.jsonl", records)
    # Pre-rewrite spans had turn1 count=3 (stale); realign must fix it from keys.
    spans = {0: (0, 0, 3), 1: (0, 0, 3), 2: (0, 0, 2)}
    turn_keys = {0: "t0", 1: "t1", 2: "t2"}
    out = _realign_spans_to_provider_file(f, spans, provider_name="codex", turn_keys=turn_keys)

    data = f.read_bytes()
    # Gap-free, line-aligned, full coverage.
    assert out[0][0] == 0
    assert out[0][1] == out[1][0]
    assert out[1][1] == out[2][0]
    assert out[2][1] == len(data)
    # Each turn's byte range begins exactly at a record boundary in the file.
    line_starts = {0}
    acc = 0
    for line in data.splitlines(keepends=True):
        acc += len(line)
        line_starts.add(acc)
    for s, e, _ in out.values():
        assert s in line_starts and e in line_starts
    # Turn 0 owns the keyless leading meta + its 2 records = 3 records.
    assert out[0][2] == 3
    # Turn 1 owns just its 1 surviving record (NOT the stale 3).
    assert out[1][2] == 2  # turn_context + 1 response_item
    assert out[2][2] == 2


def test_realign_attaches_midstream_meta_both_orderings(tmp_path):
    """P6-2: a keyless mid-stream session_meta attaches to the open turn under BOTH
    real codex orderings. Ordering A (meta after task_started) -> next turn; ordering
    B (meta before task_started) -> previous turn. Spans stay gap-free either way."""
    from checkpoint_plugin.resume import _realign_spans_to_provider_file

    # Ordering A: turn_context(t1) opens turn1 BEFORE the keyless meta.
    recs_a = [
        {"type": "turn_context", "payload": {"turn_id": "t0"}},
        {"type": "response_item", "payload": {"turn_id": "t0"}},
        {"type": "turn_context", "payload": {"turn_id": "t1"}},  # opens turn 1
        {"type": "session_meta", "payload": {"id": "S"}},        # keyless -> turn 1
        {"type": "response_item", "payload": {"turn_id": "t1"}},
    ]
    fa = _write_jsonl(tmp_path / "a.jsonl", recs_a)
    out_a = _realign_spans_to_provider_file(
        fa, {0: (0, 0, 2), 1: (0, 0, 3)}, provider_name="codex", turn_keys={0: "t0", 1: "t1"}
    )
    assert out_a[0][2] == 2  # turn 0: its 2 records only
    assert out_a[1][2] == 3  # turn 1: opens at t1, absorbs the keyless meta

    # Ordering B: keyless meta appears BEFORE turn_context(t1) -> attaches to turn 0.
    recs_b = [
        {"type": "turn_context", "payload": {"turn_id": "t0"}},
        {"type": "response_item", "payload": {"turn_id": "t0"}},
        {"type": "session_meta", "payload": {"id": "S"}},        # keyless -> still turn 0
        {"type": "turn_context", "payload": {"turn_id": "t1"}},  # opens turn 1 here
        {"type": "response_item", "payload": {"turn_id": "t1"}},
    ]
    fb = _write_jsonl(tmp_path / "b.jsonl", recs_b)
    out_b = _realign_spans_to_provider_file(
        fb, {0: (0, 0, 2), 1: (0, 0, 3)}, provider_name="codex", turn_keys={0: "t0", 1: "t1"}
    )
    assert out_b[0][2] == 3  # turn 0 keeps the keyless meta (most-recent-keyed = t0)
    assert out_b[1][2] == 2  # turn 1: just its 2 keyed records
    # Both orderings: gap-free, EOF-terminated.
    for out, f in ((out_a, fa), (out_b, fb)):
        assert out[0][0] == 0 and out[0][1] == out[1][0]
        assert out[1][1] == len(f.read_bytes())


def test_parent_turn_for_subagent_picks_spawning_turn(tmp_path, monkeypatch):
    """P6-6: (a) agent_id present in a parent slice -> that turn. (b) agent_id absent
    everywhere -> the CORRECTED fallback picks the earliest turn with created_ts >=
    start_ts (the turn the subagent ran in), not the latest turn that ended before."""
    from checkpoint_plugin.resume import _parent_turn_for_subagent
    from checkpoint_plugin.store import CheckpointStore
    from checkpoint_plugin.types import CheckpointManifest
    from checkpoint_plugin.paths import session_dir

    monkeypatch.setenv("TEST_HOME", str(tmp_path))
    home = tmp_path / "plugin"
    store = CheckpointStore(session_dir("parent", home))

    def _mk(turn_id, ts, ref=None):
        return CheckpointManifest(
            turn_id=turn_id, session_id="parent", created_ts=ts,
            env_ref="e", fs_ref="f", trajectory_ref=ref,
        )

    # Three turns at increasing timestamps; no agent_id reference in any slice.
    store.write_manifest(_mk(0, "2026-01-01T00:00:00Z"))
    store.write_manifest(_mk(1, "2026-01-01T00:01:00Z"))
    store.write_manifest(_mk(2, "2026-01-01T00:02:00Z"))

    # (b) subagent started at 00:00:30 — it ran during turn 1 (the earliest turn
    # that ended at/after start), NOT turn 0 (which ended before it started).
    turn = _parent_turn_for_subagent(home, "parent", None, {"start_ts": "2026-01-01T00:00:30Z"})
    assert turn == 1, "corrected fallback must pick the earliest turn ending >= start_ts"

    # A start before all turns -> earliest turn.
    assert _parent_turn_for_subagent(home, "parent", None, {"start_ts": "2025-12-31T00:00:00Z"}) == 0
    # A start after all turns -> latest turn (nothing ended at/after it).
    assert _parent_turn_for_subagent(home, "parent", None, {"start_ts": "2027-01-01T00:00:00Z"}) == 2


def test_no_synthetic_permission_mode_for_fork_resume():
    """P6-14: a fork-style resume (inherited prefix present) must NOT get a synthetic
    lone permission-mode record; a normal resume still does. Also: off-enum modes
    coerce to 'default'."""
    from checkpoint_plugin.resume import _rewrite_claude_trajectory, _normalize_permission_mode

    traj = (
        json.dumps({"type": "user", "uuid": "u1", "parentUuid": None, "promptId": "p", "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a1", "parentUuid": "u1", "message": {"role": "assistant", "content": []}}) + "\n"
    ).encode("utf-8")

    # Normal resume (no inherited prefix) -> synthetic permission-mode injected.
    normal = _rewrite_claude_trajectory(traj, "NEW", Path("/new"), None, "acceptEdits", None)
    normal_recs = [json.loads(l) for l in normal.splitlines()]
    assert any(r.get("type") == "permission-mode" for r in normal_recs)

    # Fork-style resume (inherited prefix) -> NO synthetic permission-mode.
    forked = _rewrite_claude_trajectory(
        traj, "NEW", Path("/new"), None, "acceptEdits", None, has_inherited_prefix=True
    )
    forked_recs = [json.loads(l) for l in forked.splitlines()]
    assert not any(r.get("type") == "permission-mode" for r in forked_recs)

    # Off-enum mode coerces to default.
    assert _normalize_permission_mode("bogusMode") == "default"
    assert _normalize_permission_mode("plan") == "plan"
    assert _normalize_permission_mode(None) is None


def test_claude_resume_stamps_forkedfrom_resolving_into_parent():
    """F1/F4: a claude resume is fork-shaped — every inherited record keeps its uuid
    BYTE-IDENTICAL to the source and stamps forkedFrom={sessionId, messageUuid} with
    messageUuid == its own (preserved) uuid, so the link resolves INTO the parent
    session (the native invariant, verified 26/26 on b57f8e6f). The old behavior
    remapped uuids and pointed forkedFrom at the remapped uuid (resolved into source
    0/26 — link severed)."""
    from checkpoint_plugin.resume import _rewrite_claude_trajectory

    traj = (
        json.dumps({"type": "user", "uuid": "src-u", "parentUuid": None, "promptId": "p0", "message": {"role": "user", "content": "first"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "src-a", "parentUuid": "src-u", "message": {"role": "assistant", "content": "resp"}}) + "\n"
        + json.dumps({"type": "user", "uuid": "src-u2", "parentUuid": "src-a", "promptId": "P", "message": {"role": "user", "content": "second"}}) + "\n"
    ).encode("utf-8")
    source_uuids = {"src-u", "src-a", "src-u2"}

    out = _rewrite_claude_trajectory(
        traj, "NEW", Path("/new"), None, None, None, source_session_id="SOURCE",
    )
    recs = [json.loads(l) for l in out.splitlines()]
    # Every uuid-bearing record is fork-stamped, uuid preserved byte-identical.
    assert len(recs) == 3
    for r in recs:
        assert r["uuid"] in source_uuids, "inherited uuid must be preserved byte-identical"
        assert r["forkedFrom"]["sessionId"] == "SOURCE"
        # messageUuid == own preserved uuid → resolves INTO the parent session.
        assert r["forkedFrom"]["messageUuid"] == r["uuid"]
        assert r["forkedFrom"]["messageUuid"] in source_uuids
    # Distinct anchor per record.
    anchors = {r["forkedFrom"]["messageUuid"] for r in recs}
    assert len(anchors) == 3


def test_claude_resume_of_resume_preserves_existing_forkedfrom():
    """F1: under byte-identical uuid preservation, a forkedFrom carried over from a
    prior resume generation keeps pointing at its original ancestor (messageUuid ==
    the preserved own uuid), so the cross-session link stays resolvable across resume
    generations rather than being re-pointed or dangled."""
    from checkpoint_plugin.resume import _rewrite_claude_trajectory

    # An inherited record already stamped (gen1): forkedFrom.messageUuid == own uuid.
    traj = (
        json.dumps({"type": "user", "uuid": "g1-u", "parentUuid": None, "promptId": "old-p", "forkedFrom": {"sessionId": "ANC", "messageUuid": "g1-u"}, "message": {"role": "user", "content": "inh"}}) + "\n"
        + json.dumps({"type": "user", "uuid": "cap-u", "parentUuid": "g1-u", "promptId": "P", "message": {"role": "user", "content": "cap"}}) + "\n"
    ).encode("utf-8")

    out = _rewrite_claude_trajectory(
        traj, "NEW", Path("/new"), None, None, None,
        has_inherited_prefix=True, source_session_id="ANC",
        inherited_record_count=1,
    )
    recs = [json.loads(l) for l in out.splitlines()]
    inh = next(r for r in recs if r["message"]["content"] == "inh")
    # uuid preserved byte-identical; pre-existing forkedFrom kept (not overwritten).
    assert inh["uuid"] == "g1-u"
    assert inh["forkedFrom"] == {"sessionId": "ANC", "messageUuid": "g1-u"}
    # The other record (no prior forkedFrom) gets one pointing at its own preserved uuid.
    cap = next(r for r in recs if r["message"]["content"] == "cap")
    assert cap["uuid"] == "cap-u"
    assert cap["forkedFrom"]["messageUuid"] == "cap-u"


def test_claude_resume_repins_version_uniform_to_latest():
    """P7-6: a resumed transcript carries one uniform CLI version like a native
    session. An inherited prefix written by an older client is re-pinned to the
    most recent version present, instead of mixing versions."""
    from checkpoint_plugin.resume import _rewrite_claude_trajectory, _latest_claude_version

    traj = (
        json.dumps({"type": "user", "uuid": "u1", "parentUuid": None, "promptId": "p1", "version": "2.1.150", "message": {"role": "user", "content": "inherited"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a1", "parentUuid": "u1", "version": "2.1.156", "message": {"role": "assistant", "content": "resp"}}) + "\n"
    ).encode("utf-8")
    assert _latest_claude_version([{"version": "2.1.150"}, {"version": "2.1.156"}]) == "2.1.156"
    assert _latest_claude_version([{"type": "x"}]) is None

    out = _rewrite_claude_trajectory(traj, "NEW", Path("/new"), None, None, None)
    recs = [json.loads(line) for line in out.splitlines()]
    versions = {r["version"] for r in recs if "version" in r}
    assert versions == {"2.1.156"}, f"expected uniform latest version, got {versions}"


def test_claude_resume_is_fork_shaped_even_from_startup():
    """F4: every claude resume is fork-shaped, even a resume of a plain startup
    session (no prior inherited prefix). All inherited records carry forkedFrom and
    keep their uuids byte-identical; no synthetic permission-mode is injected."""
    from checkpoint_plugin.resume import _rewrite_claude_trajectory

    traj = (
        json.dumps({"type": "user", "uuid": "u1", "parentUuid": None, "promptId": "P", "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a1", "parentUuid": "u1", "message": {"role": "assistant", "content": []}}) + "\n"
    ).encode("utf-8")
    out = _rewrite_claude_trajectory(traj, "NEW", Path("/new"), None, "acceptEdits", None, source_session_id="SOURCE")
    recs = [json.loads(l) for l in out.splitlines()]
    # Fork-shaped: forkedFrom on every record, uuids preserved, no synthetic mode rec.
    assert all(r["forkedFrom"]["sessionId"] == "SOURCE" for r in recs)
    assert {r["uuid"] for r in recs} == {"u1", "a1"}
    assert not any(r.get("type") == "permission-mode" for r in recs)


def test_claude_fork_resume_linearizes_branched_inherited_region():
    """N1: a native claude resume LINEARIZES the inherited DAG into a single parent
    spine — every uuid-bearing record's parentUuid points at the immediately-
    preceding emitted uuid record. A source can branch (parallel subagents / an
    edit-and-resend); preserving the source parentUuid under the identity uuid_map
    left two leaves where native has one. Verified against native oracle 62a9ea3c."""
    from checkpoint_plugin.resume import _rewrite_claude_trajectory

    # Source with a BRANCH: both `a2` (idx3) and `u2` (idx4) point at `a1` (idx2).
    traj = (
        json.dumps({"type": "user", "uuid": "u1", "parentUuid": None, "promptId": "P1", "message": {"role": "user", "content": "one"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a1", "parentUuid": "u1", "message": {"role": "assistant", "content": []}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a2", "parentUuid": "a1", "message": {"role": "assistant", "content": []}}) + "\n"
        + json.dumps({"type": "user", "uuid": "u2", "parentUuid": "a1", "promptId": "P2", "message": {"role": "user", "content": "two"}}) + "\n"
    ).encode("utf-8")
    out = _rewrite_claude_trajectory(traj, "NEW", Path("/new"), None, None, None, source_session_id="SRC")
    recs = [json.loads(l) for l in out.splitlines()]
    by_uuid = {r["uuid"]: r for r in recs}
    # The branching record u2 is re-pointed to the PREVIOUS emitted record (a2),
    # collapsing the branch into a single spine: u1 -> a1 -> a2 -> u2.
    assert by_uuid["a1"]["parentUuid"] == "u1"
    assert by_uuid["a2"]["parentUuid"] == "a1"
    assert by_uuid["u2"]["parentUuid"] == "a2", "branch must linearize to previous record"
    # Exactly one leaf (u2 is unreferenced; everything else is a parent).
    referenced = {r.get("parentUuid") for r in recs if isinstance(r.get("parentUuid"), str)}
    leaves = [u for u in by_uuid if u not in referenced]
    assert leaves == ["u2"], f"expected single leaf, got {leaves}"


def test_claude_linearization_chains_through_all_content_types():
    """N1 detail: native chains parentUuid through EVERY content record (system,
    attachment, user, assistant), not just user/assistant. A system record between
    two assistants parents the previous record, not the last assistant."""
    from checkpoint_plugin.resume import _rewrite_claude_trajectory

    traj = (
        json.dumps({"type": "user", "uuid": "u1", "parentUuid": None, "promptId": "P", "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a1", "parentUuid": "u1", "message": {"role": "assistant", "content": []}}) + "\n"
        + json.dumps({"type": "system", "uuid": "s1", "parentUuid": "a1", "content": "note"}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a2", "parentUuid": "wrong-branch", "message": {"role": "assistant", "content": []}}) + "\n"
    ).encode("utf-8")
    out = _rewrite_claude_trajectory(traj, "NEW", Path("/new"), None, None, None, source_session_id="SRC")
    by_uuid = {r["uuid"]: r for r in (json.loads(l) for l in out.splitlines())}
    assert by_uuid["s1"]["parentUuid"] == "a1"
    # a2 chains to the previous emitted record (the system record), NOT the last assistant.
    assert by_uuid["a2"]["parentUuid"] == "s1"


def test_codex_resume_id_is_uuidv7_but_claude_is_uuid4(tmp_path, monkeypatch):
    """B1: native codex session ids are uuidv7 (time-ordered, version nibble 7);
    native claude ids are uuid4 (version 4). Resume must match each provider."""
    import uuid as _uuid
    from checkpoint_plugin.resume import _new_resume_session_id

    codex_id = _new_resume_session_id("codex")
    claude_id = _new_resume_session_id("claude")
    assert _uuid.UUID(codex_id).version == 7, "codex resume id must be uuidv7"
    assert _uuid.UUID(claude_id).version == 4, "claude resume id must be uuid4"
    # uuidv7 is time-ordered: a later-generated id sorts after an earlier one.
    import time
    first = _new_resume_session_id("codex")
    time.sleep(0.002)
    second = _new_resume_session_id("codex")
    assert second > first, "uuidv7 ids must be monotonically sortable"


def test_codex_head_meta_native_key_order():
    """N2: the fresh head session_meta serializes provenance fields in native order
    (id, forked_from_id, timestamp, cwd, originator, cli_version, source,
    thread_source, model_provider, base_instructions, dynamic_tools), not the
    two-phase preserved-then-defaults order. Verified against native bf0."""
    from collections import OrderedDict
    from checkpoint_plugin.resume import _rewrite_codex_trajectory

    src_meta = {
        "id": "old", "timestamp": "t", "cwd": "/old",
        "originator": "Codex Desktop", "cli_version": "1.2.3", "source": "vscode",
        "thread_source": "user", "model_provider": "prov",
        "base_instructions": {"text": "x"}, "dynamic_tools": [],
    }
    traj = (json.dumps({"timestamp": "t", "type": "session_meta", "payload": src_meta}) + "\n").encode("utf-8")
    out = _rewrite_codex_trajectory(traj, "NEW", Path("/new"), None, None, None, src_meta)
    head = json.loads(out.splitlines()[0], object_pairs_hook=OrderedDict)
    assert list(head["payload"].keys()) == [
        "id", "forked_from_id", "timestamp", "cwd", "originator", "cli_version",
        "source", "thread_source", "model_provider", "base_instructions", "dynamic_tools",
    ]


def test_codex_resume_timestamps_are_millisecond_precision():
    """N3: native codex record/payload timestamps are 3-digit milliseconds (…653Z),
    not 6-digit microseconds. The synthetic head meta's record-ts must be ms-Z."""
    import re
    from checkpoint_plugin.resume import _rewrite_codex_trajectory

    traj = (json.dumps({"timestamp": "t", "type": "session_meta", "payload": {"id": "old", "cwd": "/old"}}) + "\n").encode("utf-8")
    out = _rewrite_codex_trajectory(traj, "NEW", Path("/new"), None, None, None, None)
    head = json.loads(out.splitlines()[0])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", head["timestamp"]), head["timestamp"]


def test_codex_resume_restamps_body_record_ts_no_temporal_inversion():
    """N5: native codex forks re-stamp every inlined body record's record-level
    timestamp to the fork moment (0 body records precede the head meta), while
    preserving payload-internal timestamps. Plugin must do the same so a resumed
    file has no record sorting before its head meta."""
    from checkpoint_plugin.resume import _rewrite_codex_trajectory

    traj = (
        json.dumps({"timestamp": "2020-01-01T00:00:00.000Z", "type": "session_meta", "payload": {"id": "old", "timestamp": "2020-01-01T00:00:00.000Z", "cwd": "/old"}}) + "\n"
        + json.dumps({"timestamp": "2020-01-01T00:00:01.000Z", "type": "response_item", "payload": {"type": "message", "turn_id": "t-1"}}) + "\n"
        + json.dumps({"timestamp": "2020-01-01T00:00:02.000Z", "type": "turn_context", "payload": {"turn_id": "t-1", "model": "m"}}) + "\n"
    ).encode("utf-8")
    out = _rewrite_codex_trajectory(traj, "NEW", Path("/new"), None, None, None, None)
    recs = [json.loads(l) for l in out.splitlines()]
    head_ts = recs[0]["timestamp"]
    body = [r for r in recs if r.get("type") != "session_meta"]
    # No body record carries a record-ts earlier than the head meta.
    assert all(r["timestamp"] >= head_ts for r in body), "temporal inversion: body precedes head"
    # All body record-ts equal the resume moment (== head record-ts).
    assert all(r["timestamp"] == head_ts for r in body)
    # Payload-internal timestamps untouched on inlined ancestor metas.
    inlined = [r for r in recs if r.get("type") == "session_meta"][1:]
    assert all(m["payload"].get("timestamp") == "2020-01-01T00:00:00.000Z" for m in inlined)


def test_codex_resume_rewrites_cwd_in_dict_keys():
    """N4: codex patch_apply changes are keyed by absolute file path, so the source
    cwd survives as a dict KEY unless keys are rewritten too (a residual F5 leak)."""
    from checkpoint_plugin.resume import _rewrite_codex_trajectory

    src_meta = {"id": "old", "cwd": "/src/work"}
    traj = (
        json.dumps({"timestamp": "t", "type": "session_meta", "payload": src_meta}) + "\n"
        + json.dumps({"timestamp": "t", "type": "event_msg", "payload": {"type": "patch_apply_end", "changes": {"/src/work/README.md": {"add": 1}}}}) + "\n"
    ).encode("utf-8")
    out = _rewrite_codex_trajectory(traj, "NEW", Path("/dst/copy"), None, None, None, src_meta)
    assert b"/src/work" not in out, "source cwd must not survive (incl. dict keys)"
    # The path key was rewritten to the target cwd.
    changes_rec = [json.loads(l) for l in out.splitlines() if json.loads(l).get("payload", {}).get("type") == "patch_apply_end"][0]
    assert "/dst/copy/README.md" in changes_rec["payload"]["changes"]


def test_resume_fork_truncation_recovery_from_blob(tmp_path, monkeypatch, capsys):
    """FORK-TRUNCATION recovery: when parent file is rewritten/truncated after fork
    (forked_at_offset > parent file size), recover from fork_point_trajectory_ref blob."""
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    claude_home = home / ".claude"
    cwd = tmp_path / "work"
    parent_transcript = tmp_path / "parent.jsonl"
    cwd.mkdir()

    # Original parent trajectory at fork time
    inherited = (
        json.dumps({"type": "mode", "mode": "normal", "sessionId": "parent"}) + "\n"
        + json.dumps({"type": "user", "sessionId": "parent", "uuid": "u1", "parentUuid": None, "promptId": "p1", "cwd": "/old", "message": {"role": "user", "content": "PARENT-TURN-1"}}) + "\n"
        + json.dumps({"type": "assistant", "sessionId": "parent", "uuid": "a1", "parentUuid": "u1", "cwd": "/old", "message": {"role": "assistant", "content": []}}) + "\n"
        + json.dumps({"type": "user", "sessionId": "parent", "uuid": "u2", "parentUuid": "a1", "promptId": "p2", "cwd": "/old", "message": {"role": "user", "content": "PARENT-TURN-2"}}) + "\n"
        + json.dumps({"type": "assistant", "sessionId": "parent", "uuid": "a2", "parentUuid": "u2", "cwd": "/old", "message": {"role": "assistant", "content": []}}) + "\n"
    )
    fork_offset = len(inherited.encode("utf-8"))
    original_parent_data = inherited.encode("utf-8")

    # Write full parent trajectory
    parent_transcript.write_bytes(original_parent_data)

    # Forked turn
    forked_turn = json.dumps({"type": "user", "sessionId": "parent", "uuid": "fu", "parentUuid": "a2", "promptId": "fork-p", "cwd": "/old", "message": {"role": "user", "content": "FORKED-PROMPT"}}) + "\n"
    fork_transcript = tmp_path / "fork.jsonl"
    fork_transcript.write_text(inherited + forked_turn, encoding="utf-8")

    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    # Capture fork session with fork_point_trajectory_ref
    coordinator = CheckpointCoordinator(session_id="forked", cwd=cwd)
    coordinator.on_session_start(source="resume", source_transcript_path=str(fork_transcript))
    coordinator.on_turn_end(
        TurnRecord(user_message="forked"),
        TrajectoryReference("claude", str(fork_transcript), fork_offset, fork_transcript.stat().st_size, 1),
    )

    # Verify fork_point_trajectory_ref was stored
    store = CheckpointStore(plugin_home / "sessions" / "forked")
    metadata = json.loads((store.session_dir / "metadata.json").read_text(encoding="utf-8"))
    assert "fork_point_trajectory_ref" in metadata, "fork_point_trajectory_ref should be stored"
    fork_point_ref = metadata["fork_point_trajectory_ref"]

    # Verify the blob contains the original parent data
    fork_point_blob = store.load_blob(fork_point_ref)
    assert fork_point_blob == original_parent_data + forked_turn.encode("utf-8")

    # Simulate parent file truncation (rewritten after fork, now shorter)
    truncated_parent = (
        json.dumps({"type": "mode", "mode": "normal", "sessionId": "parent"}) + "\n"
        + json.dumps({"type": "user", "sessionId": "parent", "uuid": "u1", "parentUuid": None, "promptId": "p1", "cwd": "/old", "message": {"role": "user", "content": "PARENT-TURN-1"}}) + "\n"
    )
    fork_transcript.write_text(truncated_parent, encoding="utf-8")

    # Now fork_offset > file size, triggering recovery
    assert fork_offset > len(truncated_parent.encode("utf-8"))

    # Resume should recover from blob
    capsys.readouterr()  # Clear any previous output
    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("forked", 0),
        lambda _text: True
    )

    # Check that recovery message was printed
    captured = capsys.readouterr()
    assert "Fork lineage truncation detected" in captured.err
    assert "Successfully recovered" in captured.err
    assert "from stored fork-point blob" in captured.err

    # Verify materialized session includes inherited content from blob
    materialized = Path(report.provider_session_path).read_text(encoding="utf-8")
    assert "PARENT-TURN-1" in materialized, "Should recover parent turn 1 from blob"
    assert "PARENT-TURN-2" in materialized, "Should recover parent turn 2 from blob"
    # The forked turn is captured separately in the manifest, not part of inherited prefix
    # The key test is that the inherited prefix (before fork_offset) was recovered


def test_resume_fork_without_recovery_blob_shows_warning(tmp_path, monkeypatch, capsys):
    """FORK-TRUNCATION: when truncation detected but no fork_point_trajectory_ref exists
    (old session), show appropriate warning about backward compatibility."""
    plugin_home = tmp_path / "plugin"
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    transcript = tmp_path / "fork.jsonl"
    cwd.mkdir()

    # Minimal forked session
    inherited = json.dumps({"type": "mode", "mode": "normal", "sessionId": "old"}) + "\n"
    fork_offset = len(inherited.encode("utf-8"))
    forked_turn = json.dumps({"type": "user", "sessionId": "old", "uuid": "fu", "parentUuid": None, "promptId": "fork-p", "cwd": "/old", "message": {"role": "user", "content": "FORKED"}}) + "\n"
    transcript.write_text(inherited + forked_turn, encoding="utf-8")

    (cwd / "file.txt").write_text("v1", encoding="utf-8")
    _isolate_provider_env(monkeypatch)
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(plugin_home))
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CHECKPOINT_PROVIDER", "claude")

    # Manually create session without fork_point_trajectory_ref (simulating old capture)
    coordinator = CheckpointCoordinator(session_id="old-fork", cwd=cwd)
    coordinator.on_session_start(source="resume", source_transcript_path=str(transcript))
    coordinator.on_turn_end(
        TurnRecord(user_message="forked"),
        TrajectoryReference("claude", str(transcript), fork_offset, transcript.stat().st_size, 1),
    )

    # Remove fork_point_trajectory_ref to simulate old session
    store = CheckpointStore(plugin_home / "sessions" / "old-fork")
    metadata_path = store.session_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.pop("fork_point_trajectory_ref", None)
    metadata_path.write_text(json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    # Truncate the transcript
    transcript.write_text("", encoding="utf-8")

    # Resume should show warning about missing recovery blob
    capsys.readouterr()
    report = ResumeOrchestrator(cwd=cwd).execute(
        ResumeOrchestrator(cwd=cwd).plan("old-fork", 0),
        lambda _text: True
    )

    captured = capsys.readouterr()
    assert "No fork_point_trajectory_ref found in metadata" in captured.err
    assert "before recovery feature was added" in captured.err
