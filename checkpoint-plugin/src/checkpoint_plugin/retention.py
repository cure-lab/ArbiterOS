"""Retention cleanup policies."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ._utils import extract_sha_refs, is_sha_ref
from .paths import blobs_dir, sessions_dir
from .store import CheckpointStore


def clean_keep_last(keep_last: int, plugin_home: Path | None = None) -> int:
    removed = 0
    for session in sessions_dir(plugin_home).glob("*"):
        if not session.is_dir():
            continue
        store = CheckpointStore(session)
        manifests = store.list_manifests()
        for manifest in manifests[:-keep_last] if keep_last >= 0 else manifests:
            path = store.manifest_dir / f"turn_{manifest.turn_id:04d}.json"
            if path.exists():
                path.unlink()
                removed += 1
        remaining = [m for m in manifests[-keep_last:]] if keep_last > 0 else []
        store._atomic_write(
            store.index_path,
            "{\n"
            + ",\n".join(f'  "{m.turn_id}": "turn_{m.turn_id:04d}.json"' for m in remaining)
            + ("\n" if remaining else "")
            + "}\n",
        )
    return removed


def compact_legacy_blobs(plugin_home: Path | None = None, dry_run: bool = False) -> dict[str, int]:
    """Move legacy per-session blobs into the global blob store."""
    root = sessions_dir(plugin_home)
    result = {"promoted": 0, "removed": 0, "missing": 0}
    if not root.exists():
        return result

    for session in sorted(root.iterdir()):
        if not session.is_dir():
            continue
        store = CheckpointStore(session)
        result["missing"] += _missing_reachable_refs(store)
        legacy_files = sorted(path for path in store.legacy_blobs_dir.glob("*/*") if path.is_file())
        for legacy_path in legacy_files:
            sha = legacy_path.name
            if not is_sha_ref(sha):
                continue
            if not store.blob_path(sha).exists():
                if dry_run:
                    if not store.legacy_blob_matches(sha):
                        result["missing"] += 1
                        continue
                else:
                    if not store.promote_legacy_blob(sha):
                        result["missing"] += 1
                        continue
                result["promoted"] += 1
            if not dry_run:
                if not store.blob_path(sha).exists():
                    result["missing"] += 1
                    continue
                if legacy_path.exists():
                    legacy_path.unlink()
                _prune_empty_blob_parents(legacy_path.parent, store.legacy_blobs_dir)
            result["removed"] += 1
    return result


def _missing_reachable_refs(store: CheckpointStore) -> int:
    missing = 0
    for sha in _reachable_blob_refs(store):
        if not store.blob_path(sha).exists() and not store.legacy_blob_path(sha).exists():
            missing += 1
    return missing


def _reachable_blob_refs(store: CheckpointStore) -> set[str]:
    refs = extract_sha_refs(_read_json(store.session_dir / "metadata.json"))
    for manifest in store.list_manifests():
        for root_ref in (manifest.env_ref, manifest.fs_ref):
            if not is_sha_ref(root_ref):
                continue
            refs.add(root_ref)
            try:
                data = store.load_json_blob(root_ref)
            except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            refs.update(extract_sha_refs(data))
    return refs


def _reachable_blob_refs_for_sessions(root: Path) -> set[str] | None:
    refs: set[str] = set()
    if not root.exists():
        return refs
    for session in sorted(root.iterdir()):
        if not session.is_dir():
            continue
        try:
            refs.update(_reachable_blob_refs(CheckpointStore(session)))
        except Exception:
            return None
    return refs


def _prune_global_blob_refs(plugin_home: Path | None, candidates: set[str]) -> int:
    if not candidates:
        return 0
    global_blobs = blobs_dir(plugin_home)
    if not global_blobs.exists():
        return 0
    reachable = _reachable_blob_refs_for_sessions(sessions_dir(plugin_home))
    if reachable is None:
        return 0
    removed = 0
    for sha in sorted(candidates - reachable):
        if not is_sha_ref(sha):
            continue
        path = global_blobs / sha[:2] / sha
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        removed += 1
        _prune_empty_blob_parents(path.parent, global_blobs, remove_stop=False)
    return removed


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _prune_empty_blob_parents(path: Path, stop: Path, *, remove_stop: bool = True) -> None:
    while path != stop and path.exists():
        try:
            path.rmdir()
        except OSError:
            return
        path = path.parent
    if not remove_stop:
        return
    try:
        stop.rmdir()
    except OSError:
        pass


def clean_empty_sessions(plugin_home: Path | None = None, dry_run: bool = False) -> dict[str, list[str]]:
    """Remove sessions with no captured turns or only metadata shells.

    Returns a dict with 'removed' and 'kept' lists of session IDs.
    """
    result = {"removed": [], "kept": [], "errors": []}
    root = sessions_dir(plugin_home)
    deleted_refs: set[str] = set()

    if not root.exists():
        return result

    for session_dir in sorted(root.iterdir()):
        if not session_dir.is_dir():
            continue

        session_id = session_dir.name

        try:
            # Read session metadata
            metadata_path = session_dir / "metadata.json"
            if not metadata_path.exists():
                result["errors"].append(f"{session_id}: no metadata.json")
                continue

            with metadata_path.open() as f:
                metadata = json.load(f)

            # Check if session has any captured turns
            store = CheckpointStore(session_dir)
            manifests = store.list_manifests()

            # Determine if session should be removed
            should_remove = False
            reason = ""

            if not manifests:
                # No turns at all - check if it's a subagent with no_sidechain_file
                lineage = metadata.get("lineage", {})
                if lineage.get("capture_status") == "no_sidechain_file":
                    should_remove = True
                    reason = f"no capture ({lineage.get('no_sidechain_file_reason', 'unknown')})"
                else:
                    should_remove = True
                    reason = "no turns captured"
            else:
                # Check if all turns have empty trajectories (0 records)
                all_empty = True
                for manifest in manifests:
                    traj_ref = manifest.trajectory_ref
                    # TrajectoryReference is a dataclass with record_count attribute
                    record_count = traj_ref.record_count if traj_ref else 0
                    if record_count > 0:
                        all_empty = False
                        break

                if all_empty:
                    should_remove = True
                    reason = f"{len(manifests)} turns with 0 records"

            if should_remove:
                if dry_run:
                    result["removed"].append(f"{session_id} [{reason}] (dry-run)")
                else:
                    deleted_refs.update(_reachable_blob_refs(store))
                    shutil.rmtree(session_dir)
                    result["removed"].append(f"{session_id} [{reason}]")
            else:
                result["kept"].append(session_id)

        except Exception as e:
            result["errors"].append(f"{session_id}: {e}")

    _prune_global_blob_refs(plugin_home, deleted_refs)
    return result
