"""Content-addressed checkpoint storage."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from .paths import blobs_dir, sessions_dir
from .types import CheckpointManifest, TrajectoryReference


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _home_for_session(session_dir: Path) -> Path:
    parent = session_dir.parent
    if parent.name == sessions_dir(parent.parent).name:
        return parent.parent
    return parent


class CheckpointStore:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = Path(session_dir).expanduser().resolve()
        self.manifest_dir = self.session_dir / "manifests"
        self.index_path = self.manifest_dir / "index.json"
        self.legacy_blobs_dir = self.session_dir / "blobs"
        self.blobs_dir = blobs_dir(_home_for_session(self.session_dir))
        self.trajectory_path = self.session_dir / "trajectory.jsonl"
        self.env_snapshot_dir = self.session_dir / "env-snapshots"
        self.lock_path = self.session_dir / ".checkpoint.lock"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.env_snapshot_dir.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def session_lock(self) -> Iterator[None]:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as handle:
            _lock_file(handle)
            try:
                yield
            finally:
                _unlock_file(handle)

    def blob_path(self, sha: str) -> Path:
        return self.blobs_dir / sha[:2] / sha

    def legacy_blob_path(self, sha: str) -> Path:
        return self.legacy_blobs_dir / sha[:2] / sha

    def store_blob(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        path = self.blob_path(sha)
        if path.exists():
            return sha
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            _publish_blob_tmp(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return sha

    def promote_legacy_blob(self, sha: str) -> bool:
        path = self.blob_path(sha)
        legacy_path = self.legacy_blob_path(sha)
        if path.exists():
            _remove_blob_alias(legacy_path, self.legacy_blobs_dir)
            return True
        if not legacy_path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            try:
                digest = _copy_file_with_hash(legacy_path, tmp)
            except FileNotFoundError:
                return False
            if digest != sha:
                return False
            _publish_blob_tmp(tmp, path)
            _remove_blob_alias(legacy_path, self.legacy_blobs_dir)
            return True
        finally:
            tmp.unlink(missing_ok=True)

    def legacy_blob_matches(self, sha: str) -> bool:
        try:
            return _hash_file(self.legacy_blob_path(sha)) == sha
        except FileNotFoundError:
            return False

    def store_json_blob(self, data: Any) -> str:
        return self.store_blob((canonical_json(data) + "\n").encode("utf-8"))

    def load_blob(self, sha: str) -> bytes:
        path = self.blob_path(sha)
        if path.exists():
            return path.read_bytes()
        legacy_path = self.legacy_blob_path(sha)
        if legacy_path.exists():
            return legacy_path.read_bytes()
        raise FileNotFoundError(f"Missing checkpoint blob {sha}")

    def load_json_blob(self, sha: str) -> Any:
        return json.loads(self.load_blob(sha).decode("utf-8"))

    def write_manifest(self, manifest: CheckpointManifest) -> None:
        content = canonical_json(manifest.to_json()) + "\n"
        self._atomic_write(self._manifest_path(manifest.turn_id), content)
        index = {str(m.turn_id): f"turn_{m.turn_id:04d}.json" for m in self.list_manifests()}
        index[str(manifest.turn_id)] = f"turn_{manifest.turn_id:04d}.json"
        ordered = dict(sorted(index.items(), key=lambda item: int(item[0])))
        self._atomic_write(
            self.index_path,
            canonical_json(ordered) + "\n",
        )
        self.write_env_snapshots()

    def read_manifest(self, turn_id: int) -> CheckpointManifest:
        return CheckpointManifest.from_json(
            json.loads(self._manifest_path(turn_id).read_text(encoding="utf-8"))
        )

    def list_manifests(self) -> list[CheckpointManifest]:
        if not self.index_path.exists():
            return []
        raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        manifests: list[CheckpointManifest] = []
        for turn_text, rel_path in raw.items():
            path = self.manifest_dir / rel_path
            if path.exists():
                manifests.append(CheckpointManifest.from_json(json.loads(path.read_text(encoding="utf-8"))))
            else:
                manifests.append(self.read_manifest(int(turn_text)))
        return sorted(manifests, key=lambda item: item.turn_id)

    def list_turn_ids(self) -> list[int]:
        return [manifest.turn_id for manifest in self.list_manifests()]

    def latest_manifest(self) -> CheckpointManifest | None:
        manifests = self.list_manifests()
        return manifests[-1] if manifests else None

    def append_trajectory(self, record: dict[str, Any]) -> tuple[int, int]:
        """Deprecated compatibility path for pre-reference checkpoints."""
        self.trajectory_path.parent.mkdir(parents=True, exist_ok=True)
        start_offset = self.trajectory_path.stat().st_size if self.trajectory_path.exists() else 0
        line = canonical_json(record) + "\n"
        encoded = line.encode("utf-8")
        with self.trajectory_path.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        return start_offset, start_offset + len(encoded)

    def slice_trajectory(self, end_offset: int) -> bytes:
        """Deprecated compatibility path for pre-reference checkpoints."""
        if not self.trajectory_path.exists() or end_offset <= 0:
            return b""
        with self.trajectory_path.open("rb") as handle:
            data = handle.read(end_offset)
        last_newline = data.rfind(b"\n")
        return data[: last_newline + 1] if last_newline >= 0 else b""

    def read_trajectory_slice(self, ref: TrajectoryReference) -> bytes:
        # An empty path resolves to "." (a directory), so guard on the raw string
        # and require a regular file — otherwise open("rb") raises IsADirectoryError
        # for empty-ref checkpoints (e.g. a subagent with no sidechain transcript).
        if not ref.transcript_path:
            raise FileNotFoundError("Trajectory reference has no transcript path")
        path = Path(ref.transcript_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Missing trajectory transcript {path}")
        if ref.start_offset < 0 or ref.end_offset < ref.start_offset:
            raise ValueError("Invalid trajectory byte range")
        file_size = path.stat().st_size
        if ref.end_offset > file_size:
            raise ValueError(
                f"Trajectory byte range {ref.start_offset}:{ref.end_offset} exceeds {path} size {file_size}"
            )
        with path.open("rb") as handle:
            handle.seek(ref.start_offset)
            return handle.read(ref.end_offset - ref.start_offset)

    def write_env_snapshots(self) -> None:
        manifests = self.list_manifests()
        stale_paths = set(self.env_snapshot_dir.glob("env_*.json"))
        for index, group in enumerate(_env_groups(manifests)):
            filename = f"env_{index:04d}_turns_{group[0].turn_id:04d}-{group[-1].turn_id:04d}.json"
            path = self.env_snapshot_dir / filename
            stale_paths.discard(path)
            self._atomic_write(
                path,
                canonical_json(_env_snapshot_json(group, self)) + "\n",
            )
        for path in stale_paths:
            path.unlink()

    def _manifest_path(self, turn_id: int) -> Path:
        return self.manifest_dir / f"turn_{turn_id:04d}.json"

    @staticmethod
    def _atomic_write(path: Path, content: str | bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        binary = isinstance(content, bytes)
        mode = "wb" if binary else "w"
        kwargs = {} if binary else {"encoding": "utf-8"}
        with tmp.open(mode, **kwargs) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)


_LINK_FALLBACK_ERRNOS = {
    value
    for value in (
        errno.EXDEV,
        errno.EPERM,
        getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "ENOTSUP", None),
    )
    if value is not None
}


def _is_link_fallback_error(exc: OSError) -> bool:
    return exc.errno in _LINK_FALLBACK_ERRNOS


def _publish_blob_tmp(tmp: Path, path: Path) -> None:
    try:
        os.link(tmp, path)
    except FileExistsError:
        return
    except OSError as exc:
        if not _is_link_fallback_error(exc):
            raise
        os.replace(tmp, path)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file_with_hash(source: Path, tmp: Path) -> str:
    digest = hashlib.sha256()
    with source.open("rb") as src, tmp.open("xb") as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    return digest.hexdigest()


def _remove_blob_alias(path: Path, stop: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    parent = path.parent
    while parent != stop and parent.exists():
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent
    try:
        stop.rmdir()
    except OSError:
        pass


def _lock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        _ensure_windows_lock_byte(handle)
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _ensure_windows_lock_byte(handle: BinaryIO) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())


def _env_groups(manifests: list[CheckpointManifest]) -> list[list[CheckpointManifest]]:
    groups: list[list[CheckpointManifest]] = []
    for manifest in manifests:
        if not groups or groups[-1][-1].env_ref != manifest.env_ref:
            groups.append([manifest])
        else:
            groups[-1].append(manifest)
    return groups


def _env_snapshot_json(group: list[CheckpointManifest], store: CheckpointStore) -> dict[str, Any]:
    first = group[0]
    turns = [
        {
            "turn_id": manifest.turn_id,
            "manifest": f"manifests/turn_{manifest.turn_id:04d}.json",
            "created_ts": manifest.created_ts,
            "user_message_preview": manifest.user_message_preview,
        }
        for manifest in group
    ]
    return {
        "env_ref": first.env_ref,
        "turn_start": group[0].turn_id,
        "turn_end": group[-1].turn_id,
        "turns": turns,
        "environment": _load_env_snapshot(first.env_ref, store),
    }


def _load_env_snapshot(env_ref: str, store: CheckpointStore) -> Any:
    try:
        return store.load_json_blob(env_ref)
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"Warning: environment snapshot unavailable for {env_ref}: {exc}", file=sys.stderr)
        return {"unavailable": True, "error": str(exc)}
