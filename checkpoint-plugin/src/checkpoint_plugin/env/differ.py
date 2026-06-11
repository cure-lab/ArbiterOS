"""Human-readable environment diffs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable

from checkpoint_plugin.types import EnvironmentState

from .hook_filter import is_hook_config_basename, is_hook_config_path, strip_plugin_hooks


@dataclass(frozen=True)
class CategoryDiff:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)

    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.modified)


@dataclass(frozen=True)
class EnvDiff:
    provider_changed: bool
    model_changed: bool
    permission_changed: bool
    effort_changed: bool
    memory: CategoryDiff
    mcp_changed: bool
    mcp_configs: CategoryDiff
    mcp_servers: CategoryDiff
    skills: CategoryDiff
    skill_status: CategoryDiff
    plugin_files: CategoryDiff
    plugin_status: CategoryDiff
    settings: CategoryDiff
    project_context: CategoryDiff

    def has_changes(self) -> bool:
        return any(
            [
                self.provider_changed,
                self.model_changed,
                self.permission_changed,
                self.effort_changed,
                self.memory.has_changes(),
                self.mcp_changed,
                self.mcp_configs.has_changes(),
                self.mcp_servers.has_changes(),
                self.skills.has_changes(),
                self.skill_status.has_changes(),
                self.plugin_files.has_changes(),
                self.plugin_status.has_changes(),
                self.settings.has_changes(),
                self.project_context.has_changes(),
            ]
        )


def diff_environments(
    current: EnvironmentState,
    target: EnvironmentState,
    *,
    blob_loader: Callable[[str], bytes] | None = None,
    ignore_plugin_hooks: bool = False,
) -> EnvDiff:
    settings_normalize = _hook_normalizer(
        target.provider, blob_loader, ignore_plugin_hooks, by_basename=True
    )
    project_normalize = _hook_normalizer(
        target.provider, blob_loader, ignore_plugin_hooks, by_basename=False
    )
    return EnvDiff(
        provider_changed=current.provider != target.provider,
        model_changed=current.model != target.model,
        permission_changed=current.permission_mode != target.permission_mode,
        effort_changed=current.effort != target.effort,
        memory=_diff_maps(current.memory_files, target.memory_files),
        mcp_changed=current.mcp_config != target.mcp_config,
        mcp_configs=_diff_maps(current.mcp_configs, target.mcp_configs),
        mcp_servers=_diff_maps(current.mcp_servers, target.mcp_servers),
        skills=_diff_maps(current.skills, target.skills),
        skill_status=_diff_maps(current.skill_status, target.skill_status),
        plugin_files=_diff_maps(current.plugin_files, target.plugin_files),
        plugin_status=_diff_maps(current.plugin_status, target.plugin_status),
        settings=_diff_maps(current.settings, target.settings, normalize=settings_normalize),
        project_context=_diff_maps(
            current.project_context, target.project_context, normalize=project_normalize
        ),
    )


def render_diff(diff: EnvDiff, current: EnvironmentState, target: EnvironmentState) -> str:
    if not diff.has_changes():
        return "Environment: no changes"

    lines = ["Environment:"]
    if diff.provider_changed:
        lines.append(f"  Provider: {current.provider or '-'} -> {target.provider or '-'}")
    if diff.model_changed:
        lines.append(f"  Model: {current.model or '-'} -> {target.model or '-'}")
    if diff.permission_changed:
        lines.append(f"  Permission: {current.permission_mode or '-'} -> {target.permission_mode or '-'}")
    if diff.effort_changed:
        lines.append(f"  Effort: {current.effort or '-'} -> {target.effort or '-'}")
    if diff.mcp_changed:
        lines.append("  MCP config: modified")
    _append_category(lines, "MCP config files", diff.mcp_configs)
    _append_category(lines, "MCP servers", diff.mcp_servers)
    _append_category(lines, "Memory", diff.memory)
    _append_category(lines, "Skills", diff.skills)
    _append_category(lines, "Skill status", diff.skill_status)
    _append_category(lines, "Plugin files", diff.plugin_files)
    _append_category(lines, "Plugin status", diff.plugin_status)
    _append_category(lines, "Settings", diff.settings)
    _append_category(lines, "Project context", diff.project_context)
    return "\n".join(lines)


def _diff_maps(
    current: dict[str, str],
    target: dict[str, str],
    *,
    normalize: Callable[[str, str], str] | None = None,
) -> CategoryDiff:
    current_keys = set(current)
    target_keys = set(target)
    common = current_keys & target_keys
    if normalize is None:
        modified = sorted(key for key in common if current[key] != target[key])
    else:
        modified = sorted(
            key for key in common if normalize(key, current[key]) != normalize(key, target[key])
        )
    return CategoryDiff(
        added=sorted(target_keys - current_keys),
        removed=sorted(current_keys - target_keys),
        modified=modified,
    )


def _hook_normalizer(
    provider: str,
    blob_loader: Callable[[str], bytes] | None,
    ignore_plugin_hooks: bool,
    *,
    by_basename: bool,
) -> Callable[[str, str], str] | None:
    if not ignore_plugin_hooks or blob_loader is None:
        return None
    cache: dict[tuple[str, str], str] = {}

    def normalize(key: str, sha: str) -> str:
        applies = (
            is_hook_config_basename(key, provider)
            if by_basename
            else is_hook_config_path(key, provider)
        )
        if not applies:
            return sha
        cache_key = (key, sha)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        normalized = hashlib.sha256(strip_plugin_hooks(blob_loader(sha))).hexdigest()
        cache[cache_key] = normalized
        return normalized

    return normalize


def _append_category(lines: list[str], label: str, diff: CategoryDiff) -> None:
    if not diff.has_changes():
        return
    total = len(diff.added) + len(diff.removed) + len(diff.modified)
    lines.append(f"  {label} ({total} changes):")
    lines.extend(f"    + {item}" for item in diff.added)
    lines.extend(f"    - {item}" for item in diff.removed)
    lines.extend(f"    ~ {item}" for item in diff.modified)
