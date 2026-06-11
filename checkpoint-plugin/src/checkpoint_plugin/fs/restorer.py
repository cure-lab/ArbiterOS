"""Restore workspace filesystem snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from checkpoint_plugin._utils import backup_file
from checkpoint_plugin.store import CheckpointStore
from checkpoint_plugin.types import FilesystemSnapshot, RestoreReport

from .ignore import IgnoreMatcher


@dataclass(frozen=True)
class FsDiff:
    added: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)

    def has_changes(self) -> bool:
        return bool(self.added or self.deleted or self.modified)


def diff_filesystems(current: FilesystemSnapshot, target: FilesystemSnapshot) -> FsDiff:
    current_keys = set(current.files)
    target_keys = set(target.files)
    common = current_keys & target_keys
    return FsDiff(
        added=sorted(target_keys - current_keys),
        deleted=sorted(current_keys - target_keys),
        modified=sorted(key for key in common if current.files[key] != target.files[key]),
    )


def render_fs_diff(diff: FsDiff, cwd: str) -> str:
    if not diff.has_changes():
        return f"Filesystem (cwd: {cwd}): no changes"
    return (
        f"Filesystem (cwd: {cwd}):\n"
        f"  modified: {len(diff.modified)} files    "
        f"deleted: {len(diff.deleted)} files    "
        f"added: {len(diff.added)} files"
    )


def restore_cwd(
    snapshot: FilesystemSnapshot,
    target: Path,
    store: CheckpointStore,
    backup_dir: Path,
    ignore: IgnoreMatcher | None = None,
) -> RestoreReport:
    target = Path(target).expanduser().resolve()
    ignore = ignore or IgnoreMatcher(target)
    target.mkdir(parents=True, exist_ok=True)
    current = _current_files(target, store, ignore)
    diff = diff_filesystems(current, snapshot)

    changed: list[str] = []
    backed_up: list[str] = []

    for rel in diff.deleted:
        path = target / rel
        if path.exists() and not ignore.matches(path):
            backup_file(path, backup_dir / rel, backed_up)
            path.unlink()
            _prune_empty_parents(path.parent, target)
            changed.append(str(path))

    for rel in [*diff.added, *diff.modified]:
        path = target / rel
        if ignore.matches(path):
            continue
        if path.exists():
            backup_file(path, backup_dir / rel, backed_up)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(store.load_blob(snapshot.files[rel]))
        changed.append(str(path))

    return RestoreReport(changed=changed, backed_up=backed_up, backup_dir=str(backup_dir))


def _current_files(target: Path, store: CheckpointStore, ignore: IgnoreMatcher) -> FilesystemSnapshot:
    files: dict[str, str] = {}
    for path in sorted(target.rglob("*")):
        if path.is_file() and not ignore.matches(path):
            files[path.relative_to(target).as_posix()] = store.store_blob(path.read_bytes())
    return FilesystemSnapshot(cwd=str(target), files=files, git=None)




def _prune_empty_parents(path: Path, stop: Path) -> None:
    while path != stop and path.exists():
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent
