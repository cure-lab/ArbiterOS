import errno
import json
import hashlib

import pytest

from checkpoint_plugin.store import CheckpointStore
from checkpoint_plugin.types import CheckpointManifest, TrajectoryReference


def test_blob_dedup_and_manifest_roundtrip(tmp_path):
    store = CheckpointStore(tmp_path / "session")

    first = store.store_blob(b"hello")
    second = store.store_blob(b"hello")
    assert first == second
    assert store.load_blob(first) == b"hello"

    manifest = CheckpointManifest(
        turn_id=3,
        session_id="s1",
        created_ts="2026-05-28T00:00:00Z",
        env_ref=first,
        fs_ref=second,
        trajectory_offset=12,
        trajectory_end_offset=34,
        trajectory_ref=TrajectoryReference(
            provider="codex",
            transcript_path="/tmp/transcript.jsonl",
            start_offset=12,
            end_offset=34,
            record_count=2,
        ),
        user_message_preview="hi",
        parent_turn_id=2,
    )
    store.write_manifest(manifest)

    assert store.read_manifest(3) == manifest
    assert store.list_turn_ids() == [3]


def test_blobs_are_global_across_sessions(tmp_path):
    first_store = CheckpointStore(tmp_path / "plugin" / "sessions" / "s1")
    second_store = CheckpointStore(tmp_path / "plugin" / "sessions" / "s2")

    sha = first_store.store_blob(b"shared")
    assert second_store.store_blob(b"shared") == sha

    assert first_store.blob_path(sha) == second_store.blob_path(sha)
    assert first_store.blob_path(sha).is_file()
    assert not first_store.legacy_blob_path(sha).exists()
    assert not second_store.legacy_blob_path(sha).exists()
    assert second_store.load_blob(sha) == b"shared"


def test_legacy_session_blob_fallback_and_promotion(tmp_path):
    store = CheckpointStore(tmp_path / "plugin" / "sessions" / "s1")
    sha = hashlib.sha256(b"legacy").hexdigest()
    legacy_path = store.legacy_blob_path(sha)
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"legacy")

    assert store.load_blob(sha) == b"legacy"
    assert store.promote_legacy_blob(sha) is True
    assert store.blob_path(sha).read_bytes() == b"legacy"
    assert not legacy_path.exists()
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"corrupt")
    assert store.blob_path(sha).read_bytes() == b"legacy"


def test_store_blob_falls_back_when_hardlink_is_unavailable(tmp_path, monkeypatch):
    store = CheckpointStore(tmp_path / "plugin" / "sessions" / "s1")

    def fail_link(_source, _target):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr("checkpoint_plugin.store.os.link", fail_link)

    sha = store.store_blob(b"fallback")

    assert store.blob_path(sha).read_bytes() == b"fallback"


def test_store_blob_reraises_unexpected_link_errors(tmp_path, monkeypatch):
    store = CheckpointStore(tmp_path / "plugin" / "sessions" / "s1")

    def fail_link(_source, _target):
        raise OSError(errno.EIO, "io error")

    monkeypatch.setattr("checkpoint_plugin.store.os.link", fail_link)

    with pytest.raises(OSError):
        store.store_blob(b"broken")

    sha = hashlib.sha256(b"broken").hexdigest()
    assert not store.blob_path(sha).exists()
    assert not list(store.blob_path(sha).parent.glob("*.tmp"))


def test_promote_legacy_blob_does_not_depend_on_hardlinks(tmp_path, monkeypatch):
    store = CheckpointStore(tmp_path / "plugin" / "sessions" / "s1")
    sha = hashlib.sha256(b"legacy").hexdigest()
    legacy_path = store.legacy_blob_path(sha)
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"legacy")

    def fail_link(_source, _target):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr("checkpoint_plugin.store.os.link", fail_link)

    assert store.promote_legacy_blob(sha) is True
    assert store.blob_path(sha).read_bytes() == b"legacy"
    assert not legacy_path.exists()


def test_promote_legacy_blob_rejects_hash_mismatch(tmp_path):
    store = CheckpointStore(tmp_path / "plugin" / "sessions" / "s1")
    sha = hashlib.sha256(b"expected").hexdigest()
    legacy_path = store.legacy_blob_path(sha)
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"wrong")

    assert store.promote_legacy_blob(sha) is False
    assert not store.blob_path(sha).exists()
    assert legacy_path.read_bytes() == b"wrong"


def test_append_trajectory_returns_start_and_end_offsets(tmp_path):
    store = CheckpointStore(tmp_path / "session")

    first_start, first_end = store.append_trajectory({"event": 1})
    second_start, second_end = store.append_trajectory({"event": 2})

    assert first_start == 0
    assert first_end == second_start
    assert second_end > second_start
    assert store.slice_trajectory(first_end).count(b"\n") == 1
    assert store.slice_trajectory(second_end).count(b"\n") == 2


def test_read_trajectory_slice_reads_external_transcript_range(tmp_path):
    store = CheckpointStore(tmp_path / "session")
    transcript = tmp_path / "provider.jsonl"
    transcript.write_bytes(b'{"a":1}\n{"a":2}\n')
    ref = TrajectoryReference(
        provider="codex",
        transcript_path=str(transcript),
        start_offset=8,
        end_offset=16,
        record_count=1,
    )

    assert store.read_trajectory_slice(ref) == b'{"a":2}\n'


def test_write_manifest_creates_human_readable_env_snapshot_groups(tmp_path):
    store = CheckpointStore(tmp_path / "session")
    env_ref = store.store_json_blob(
        {
            "provider": "codex",
            "model": "gpt-5",
            "permission_mode": "workspace-write",
            "memory_files": {},
            "mcp_config": None,
            "skills": {},
            "settings": {"config.toml": "settings-sha"},
            "project_context": {},
            "extra": {"cwd": "/tmp/work"},
        }
    )
    fs_ref = store.store_json_blob({"cwd": "/tmp/work", "files": {}, "git": None})

    store.write_manifest(
        CheckpointManifest(
            turn_id=0,
            session_id="s1",
            created_ts="2026-05-28T00:00:00Z",
            env_ref=env_ref,
            fs_ref=fs_ref,
            user_message_preview="first",
        )
    )
    store.write_manifest(
        CheckpointManifest(
            turn_id=1,
            session_id="s1",
            created_ts="2026-05-28T00:01:00Z",
            env_ref=env_ref,
            fs_ref=fs_ref,
            user_message_preview="second",
            parent_turn_id=0,
        )
    )

    paths = sorted((tmp_path / "session" / "env-snapshots").glob("env_*.json"))
    assert [path.name for path in paths] == ["env_0000_turns_0000-0001.json"]

    snapshot = json.loads(paths[0].read_text(encoding="utf-8"))
    assert snapshot["env_ref"] == env_ref
    assert snapshot["environment"]["provider"] == "codex"
    assert snapshot["environment"]["model"] == "gpt-5"
    assert [turn["turn_id"] for turn in snapshot["turns"]] == [0, 1]
    assert [turn["user_message_preview"] for turn in snapshot["turns"]] == ["first", "second"]


def test_env_snapshot_groups_split_when_env_ref_changes(tmp_path):
    store = CheckpointStore(tmp_path / "session")
    first_env_ref = store.store_json_blob({"provider": "codex", "model": "gpt-5"})
    second_env_ref = store.store_json_blob({"provider": "codex", "model": "gpt-5-mini"})
    fs_ref = store.store_json_blob({"cwd": "/tmp/work", "files": {}, "git": None})

    for turn_id, env_ref in enumerate([first_env_ref, first_env_ref, second_env_ref]):
        store.write_manifest(
            CheckpointManifest(
                turn_id=turn_id,
                session_id="s1",
                created_ts=f"2026-05-28T00:0{turn_id}:00Z",
                env_ref=env_ref,
                fs_ref=fs_ref,
                user_message_preview=f"turn {turn_id}",
                parent_turn_id=turn_id - 1 if turn_id else None,
            )
        )

    paths = sorted((tmp_path / "session" / "env-snapshots").glob("env_*.json"))
    assert [path.name for path in paths] == [
        "env_0000_turns_0000-0001.json",
        "env_0001_turns_0002-0002.json",
    ]

    snapshots = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert snapshots[0]["environment"]["model"] == "gpt-5"
    assert [turn["turn_id"] for turn in snapshots[0]["turns"]] == [0, 1]
    assert snapshots[1]["environment"]["model"] == "gpt-5-mini"
    assert [turn["turn_id"] for turn in snapshots[1]["turns"]] == [2]
