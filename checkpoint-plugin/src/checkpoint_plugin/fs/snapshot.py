"""Workspace filesystem snapshotting."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

from checkpoint_plugin.store import CheckpointStore
from checkpoint_plugin.types import FilesystemSnapshot

from .ignore import IgnoreMatcher


def snapshot_cwd(cwd: Path, store: CheckpointStore, ignore: IgnoreMatcher) -> FilesystemSnapshot:
    cwd = Path(cwd).expanduser().resolve()
    files: dict[str, str] = {}
    for path in _walk_files(cwd, ignore):
        rel = path.relative_to(cwd).as_posix()
        files[rel] = store.store_blob(path.read_bytes())
    return FilesystemSnapshot(cwd=str(cwd), files=dict(sorted(files.items())), git=_git_state(cwd))


def filesystem_to_blob(snapshot: FilesystemSnapshot, store: CheckpointStore) -> str:
    return store.store_json_blob(snapshot.to_json())


def filesystem_from_blob(sha: str, store: CheckpointStore) -> FilesystemSnapshot:
    data = store.load_json_blob(sha)
    if not isinstance(data, dict):
        raise ValueError(f"Filesystem snapshot blob {sha} is not a JSON object")
    return FilesystemSnapshot.from_json(data)


def _walk_files(cwd: Path, ignore: IgnoreMatcher) -> Iterable[Path]:
    # P7-8: plan() may snapshot a --target cwd that does not exist yet (execute()
    # creates it later), so rglob would raise FileNotFoundError. A missing or
    # not-yet-created cwd simply has no files to snapshot.
    try:
        entries = sorted(cwd.rglob("*"))
    except (FileNotFoundError, NotADirectoryError):
        return
    for path in entries:
        if path.is_dir():
            continue
        if ignore.matches(path):
            continue
        try:
            if path.stat().st_size > 10 * 1024 * 1024:
                continue
        except OSError:
            continue
        yield path


def _git_state(cwd: Path) -> dict[str, str] | None:
    # P7-8: a missing/not-yet-created cwd (or one with no git on PATH) makes the
    # subprocess raise FileNotFoundError/NotADirectoryError rather than exit
    # non-zero; treat all of these as "no git state" instead of crashing plan().
    try:
        head = _git(cwd, ["rev-parse", "HEAD"])
        branch = _git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"])
        dirty = _git(cwd, ["status", "--porcelain"])
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
        return None
    return {
        "head": head,
        "branch": branch,
        "dirty_files": dirty,
    }


def _git(cwd: Path, args: list[str]) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=cwd,
        stderr=subprocess.DEVNULL,
        text=True,
    ).strip()
