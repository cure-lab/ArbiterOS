"""Restore provider environment state with backups."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from checkpoint_plugin._utils import backup_file
from checkpoint_plugin.path_utils import PathRootKind, mirror_path, path_matches_root, rewrite_path_references_bytes
from checkpoint_plugin.store import CheckpointStore
from checkpoint_plugin.types import EnvironmentState, RestoreReport

from .collector import _ancestor_chain, _nearest_project_root, _plugin_skill_roots
from .hook_filter import (
    is_hook_config_basename,
    is_hook_config_path,
    merge_plugin_hooks,
    strip_plugin_hooks,
)
from .providers import ProviderLayout


@dataclass(frozen=True)
class RestoreRoot:
    path: Path
    kind: PathRootKind = "directory"


def restore_environment(
    target: EnvironmentState,
    provider: ProviderLayout,
    store: CheckpointStore,
    backup_dir: Path,
    *,
    ignore_plugin_hooks: bool = False,
    preserve_redacted_values: bool = False,
) -> RestoreReport:
    changed: list[str] = []
    backed_up: list[str] = []
    allowed_roots = _allowed_restore_roots(provider, target)
    path_map = _restore_path_map(target)

    changed.extend(_restore_tree(target.memory_files, provider.memory_dir, store, backup_dir / "memory", backed_up))
    changed.extend(
        _restore_named_skill_trees(
            target.skills,
            _skill_restore_roots(provider, Path(target.extra.get("cwd") or "."), target.extra, target.skills),
            store,
            backup_dir / "skills",
            backed_up,
        )
    )
    changed.extend(
        _restore_named_plugin_files(
            target.plugin_files,
            _plugin_file_restore_roots(provider, target.extra),
            store,
            backup_dir / "plugins",
            backed_up,
        )
    )
    if provider.mcp_config is not None and provider.name != "opencode":
        changed.extend(
            _restore_optional_file(
                target.mcp_config,
                provider.mcp_config,
                store,
                backup_dir / "mcp",
                backed_up,
                preserve_redacted_values=preserve_redacted_values,
                path_map=path_map,
            )
        )
    changed.extend(
        _restore_settings(
            target.settings,
            provider.settings_files,
            allowed_roots,
            store,
            backup_dir / "settings",
            backed_up,
            provider_name=provider.name,
            ignore_plugin_hooks=ignore_plugin_hooks,
            preserve_redacted_values=preserve_redacted_values,
            path_map=path_map,
        )
    )
    changed.extend(
        _restore_project_context(
            target.project_context,
            allowed_roots,
            store,
            backup_dir / "project-context",
            backed_up,
            provider_name=provider.name,
            ignore_plugin_hooks=ignore_plugin_hooks,
            preserve_redacted_values=preserve_redacted_values,
            path_map=path_map,
        )
    )

    return RestoreReport(changed=changed, backed_up=backed_up, backup_dir=str(backup_dir))


def _restore_named_skill_trees(
    target: dict[str, str],
    roots: dict[str, Path],
    store: CheckpointStore,
    backup_dir: Path,
    backed_up: list[str],
) -> list[str]:
    by_root: dict[str, dict[str, str]] = {}
    legacy: dict[str, str] = {}
    for key, sha in target.items():
        match = _split_skill_root(key, roots)
        if match is None:
            legacy[key] = sha
            continue
        root_name, rel = match
        by_root.setdefault(root_name, {})[rel] = sha

    changed: list[str] = []
    for name, values in by_root.items():
        changed.extend(_restore_tree(values, roots[name], store, backup_dir / name, backed_up))
    if legacy:
        changed.extend(_restore_tree(legacy, roots.get("user"), store, backup_dir / "legacy", backed_up))
    return changed


def _split_skill_root(key: str, roots: dict[str, Path]) -> tuple[str, str] | None:
    for root_name in sorted(roots, key=len, reverse=True):
        prefix = f"{root_name}/"
        if key.startswith(prefix):
            return root_name, key[len(prefix) :]
    return None


def _skill_restore_roots(
    provider: ProviderLayout,
    cwd: Path,
    extra: dict[str, object] | None = None,
    skills: dict[str, str] | None = None,
) -> dict[str, Path]:
    roots = dict(provider.skills_dirs)
    roots.update(_plugin_skill_roots(provider))
    try:
        cwd = cwd.expanduser().resolve()
    except OSError:
        cwd = Path(".").resolve()
    if provider.name == "claude":
        for project_root in (cwd, *cwd.parents):
            if (project_root / ".git").exists() or project_root == _nearest_project_root(cwd):
                roots[f"project:{project_root}:.claude/skills"] = project_root / ".claude" / "skills"
    if provider.name == "codex":
        for project_root in (cwd, *cwd.parents):
            if (project_root / ".git").exists() or project_root == _nearest_project_root(cwd):
                roots[f"project:{project_root}:.codex/skills"] = project_root / ".codex" / "skills"
                roots[f"project:{project_root}:.agents/skills"] = project_root / ".agents" / "skills"
    if provider.name == "opencode":
        for project_root in (cwd, *cwd.parents):
            if (project_root / ".git").exists() or project_root == _nearest_project_root(cwd):
                roots[f"project:{project_root}:.opencode/skills"] = project_root / ".opencode" / "skills"
                roots[f"project:{project_root}:.opencode/skill"] = project_root / ".opencode" / "skill"
                roots[f"project:{project_root}:.agents/skills"] = project_root / ".agents" / "skills"
                roots[f"project:{project_root}:.claude/skills"] = project_root / ".claude" / "skills"
        config_roots = (extra or {}).get("opencode_config_skill_roots")
        if isinstance(config_roots, list):
            for root in config_roots:
                if isinstance(root, str) and root:
                    path = Path(root).expanduser()
                    roots[f"opencode-config-skills:{path}"] = path
    if provider.name == "codex" and skills:
        roots.update(_codex_plugin_skill_restore_roots(provider, skills))
    return roots


def _codex_plugin_skill_restore_roots(provider: ProviderLayout, skills: dict[str, str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for key in skills:
        root_name, separator, _rel = key.partition("/")
        if not separator or not root_name.startswith("plugin:"):
            continue
        parts = root_name.split(":")
        if len(parts) != 4 or not all(parts[1:]):
            continue
        _prefix, marketplace, plugin, version = parts
        roots[root_name] = provider.home / "plugins" / "cache" / marketplace / plugin / version / "skills"
    return roots


def _restore_named_plugin_files(
    target: dict[str, str],
    roots: dict[str, Path],
    store: CheckpointStore,
    backup_dir: Path,
    backed_up: list[str],
) -> list[str]:
    if not target:
        return []

    changed: list[str] = []
    for key, sha in target.items():
        match = _split_skill_root(key, roots)
        if match is None:
            continue
        root_name, rel = match
        restored = _restore_blob_to(sha, roots[root_name] / rel, store, backup_dir / root_name / rel, backed_up)
        if restored is not None:
            changed.append(str(restored))
    return changed


def _plugin_file_restore_roots(provider: ProviderLayout, extra: dict[str, object] | None = None) -> dict[str, Path]:
    if provider.name != "codex":
        return {}

    roots: dict[str, Path] = {"codex-plugin-cache": provider.home / "plugins" / "cache"}
    raw = (extra or {}).get("plugin_file_roots")
    if isinstance(raw, dict):
        for name, path in raw.items():
            if not isinstance(name, str) or not isinstance(path, str) or not path:
                continue
            roots[name] = Path(path).expanduser()
    return roots


def _allowed_restore_roots(provider: ProviderLayout, target: EnvironmentState) -> list[RestoreRoot]:
    roots: list[RestoreRoot] = [RestoreRoot(provider.home)]
    if provider.memory_dir is not None:
        roots.append(RestoreRoot(provider.memory_dir))
    roots.extend(RestoreRoot(path, "file") for path in provider.mcp_config_files)
    roots.extend(RestoreRoot(path, "file") for path in provider.settings_files)
    roots.extend(RestoreRoot(path) for path in provider.skills_dirs.values())
    cwd = target.extra.get("cwd")
    if isinstance(cwd, str) and cwd:
        cwd_path = Path(cwd).expanduser()
        roots.extend(RestoreRoot(path) for path in _ancestor_chain(_nearest_project_root(cwd_path), cwd_path))
    for item in provider.project_files:
        path = Path(item).expanduser()
        if path.is_absolute():
            roots.append(RestoreRoot(path, _project_file_root_kind(item, path)))
    config_roots = target.extra.get("opencode_config_skill_roots")
    if isinstance(config_roots, list):
        roots.extend(RestoreRoot(Path(root).expanduser()) for root in config_roots if isinstance(root, str) and root)
    return roots


def _restore_path_map(target: EnvironmentState) -> dict[str, str]:
    raw = target.extra.get("runtime_path_map")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, str] = {}
    for source, dest in raw.items():
        if isinstance(source, str) and isinstance(dest, str) and source and dest:
            result[source] = dest
    return result


def _restore_tree(
    target: dict[str, str],
    root: Path | None,
    store: CheckpointStore,
    backup_dir: Path,
    backed_up: list[str],
) -> list[str]:
    if root is None:
        return []
    changed: list[str] = []
    existing = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob("*")
        if root.exists() and path.is_file()
    }
    for rel, path in existing.items():
        if rel not in target:
            backup_file(path, backup_dir / rel, backed_up)
            path.unlink()
            changed.append(str(path))
    for rel, sha in target.items():
        path = root / rel
        current = path.read_bytes() if path.exists() and path.is_file() else None
        wanted = store.load_blob(sha)
        if current != wanted:
            if path.exists():
                backup_file(path, backup_dir / rel, backed_up)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(wanted)
            changed.append(str(path))
    return changed


def _restore_settings(
    settings: dict[str, str],
    settings_files: list[Path],
    allowed_roots: list[RestoreRoot],
    store: CheckpointStore,
    backup_dir: Path,
    backed_up: list[str],
    *,
    provider_name: str,
    ignore_plugin_hooks: bool,
    preserve_redacted_values: bool,
    path_map: dict[str, str],
) -> list[str]:
    by_name = _settings_restore_paths(settings_files)
    by_basename = _unique_basenames(settings_files)
    changed: list[str] = []
    for key, path in by_name.items():
        if not _setting_is_targeted(settings, key, path) and path.exists():
            if ignore_plugin_hooks and is_hook_config_basename(path.name, provider_name) and _is_plugin_hooks_only(path):
                continue
            backup_file(path, backup_dir / _setting_backup_rel(path), backed_up)
            path.unlink()
            changed.append(str(path))
    for name, sha in settings.items():
        path = by_name.get(name) or by_basename.get(name)
        if path is None and Path(name).is_absolute():
            path = Path(name)
        if path is None and settings_files:
            path = settings_files[0].parent / name
        if path is not None and _path_allowed(path, allowed_roots):
            preserve_plugin_hooks = ignore_plugin_hooks and is_hook_config_basename(path.name, provider_name)
            restored = _restore_blob_to(
                sha,
                path,
                store,
                backup_dir / _setting_backup_rel(path),
                backed_up,
                preserve_plugin_hooks=preserve_plugin_hooks,
                preserve_redacted_values=preserve_redacted_values,
                path_map=path_map,
            )
            if restored is not None:
                changed.append(str(restored))
    return changed


def _setting_is_targeted(settings: dict[str, str], key: str, path: Path) -> bool:
    return key in settings or path.name in settings or str(path) in settings


def _setting_backup_rel(path: Path) -> Path:
    return mirror_path(path) if path.is_absolute() else Path(path.name)


def _settings_restore_paths(paths: list[Path]) -> dict[str, Path]:
    basenames = _basename_counts(paths)
    return {
        str(path) if basenames[path.name] > 1 else path.name: path
        for path in paths
    }


def _unique_basenames(paths: list[Path]) -> dict[str, Path]:
    basenames = _basename_counts(paths)
    return {path.name: path for path in paths if basenames[path.name] == 1}


def _basename_counts(paths: list[Path]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in paths:
        counts[path.name] = counts.get(path.name, 0) + 1
    return counts


def _restore_optional_file(
    sha: str | None,
    path: Path,
    store: CheckpointStore,
    backup_dir: Path,
    backed_up: list[str],
    *,
    preserve_redacted_values: bool = False,
    path_map: dict[str, str] | None = None,
) -> list[str]:
    if sha is None:
        if path.exists():
            backup_file(path, backup_dir / path.name, backed_up)
            path.unlink()
            return [str(path)]
        return []
    restored = _restore_blob_to(
        sha,
        path,
        store,
        backup_dir,
        backed_up,
        preserve_redacted_values=preserve_redacted_values,
        path_map=path_map,
    )
    return [str(restored)] if restored is not None else []


def _restore_project_context(
    project_context: dict[str, str],
    allowed_roots: list[RestoreRoot],
    store: CheckpointStore,
    backup_dir: Path,
    backed_up: list[str],
    *,
    provider_name: str,
    ignore_plugin_hooks: bool,
    preserve_redacted_values: bool,
    path_map: dict[str, str],
) -> list[str]:
    changed: list[str] = []
    for key, sha in project_context.items():
        path = Path(key)
        if not path.is_absolute():
            continue
        if not _path_allowed(path, allowed_roots):
            continue
        preserve_plugin_hooks = ignore_plugin_hooks and is_hook_config_path(path, provider_name)
        restored = _restore_blob_to(
            sha,
            path,
            store,
            backup_dir / mirror_path(path),
            backed_up,
            preserve_plugin_hooks=preserve_plugin_hooks,
            preserve_redacted_values=preserve_redacted_values,
            path_map=path_map,
        )
        if restored is not None:
            changed.append(str(restored))
    return changed


def _path_allowed(path: Path, roots: list[RestoreRoot]) -> bool:
    return any(path_matches_root(path, root.path, kind=root.kind) for root in roots)


def _project_file_root_kind(item: str, path: Path) -> PathRootKind:
    if item.endswith(("/", os.sep)) or any(char in item for char in "*?["):
        return "directory"
    return "file"


def _restore_blob_to(
    sha: str,
    path: Path,
    store: CheckpointStore,
    backup_path_or_dir: Path,
    backed_up: list[str],
    *,
    preserve_plugin_hooks: bool = False,
    preserve_redacted_values: bool = False,
    path_map: dict[str, str] | None = None,
) -> Path | None:
    wanted = store.load_blob(sha)
    current = path.read_bytes() if path.exists() and path.is_file() else None
    if preserve_redacted_values:
        wanted = _preserve_redacted_values(current, wanted)
    if preserve_plugin_hooks:
        wanted = merge_plugin_hooks(current or b"", wanted)
    if path_map and not preserve_plugin_hooks:
        wanted = rewrite_path_references_bytes(wanted, path_map)
    if current == wanted:
        return None
    if path.exists():
        backup_path = backup_path_or_dir if backup_path_or_dir.suffix else backup_path_or_dir / path.name
        backup_file(path, backup_path, backed_up)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wanted)
    return path


def _is_plugin_hooks_only(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    stripped = strip_plugin_hooks(data)
    try:
        parsed = json.loads(stripped.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(parsed, dict):
        return False
    leftover = {key: value for key, value in parsed.items() if key != "hooks"}
    if leftover:
        return False
    hooks = parsed.get("hooks")
    return hooks in (None, {}, [])




_REDACTED = "***redacted***"
_ASSIGNMENT = re.compile(
    r'(?P<prefix>(?P<q>["\']?)(?P<key>[\w.-]+)(?P=q)\s*[:=]\s*)'
    r'(?P<val>"(?:[^"\\]|\\.)*"|\'[^\']*\')'
)


def _preserve_redacted_values(current: bytes | None, wanted: bytes) -> bytes:
    if current is None or _REDACTED.encode("utf-8") not in wanted:
        return wanted
    try:
        current_text = current.decode("utf-8")
        wanted_text = wanted.decode("utf-8")
    except UnicodeDecodeError:
        return wanted
    values = _assignment_values_by_key(current_text)

    def replace(match: re.Match[str]) -> str:
        raw = match.group("val")
        if raw.strip("\"'") != _REDACTED:
            return match.group(0)
        candidates = values.get(match.group("key")) or []
        if not candidates:
            return match.group(0)
        return match.group("prefix") + candidates.pop(0)

    return _ASSIGNMENT.sub(replace, wanted_text).encode("utf-8")


def _assignment_values_by_key(text: str) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for match in _ASSIGNMENT.finditer(text):
        raw = match.group("val")
        if raw.strip("\"'") == _REDACTED:
            continue
        values.setdefault(match.group("key"), []).append(raw)
    return values
