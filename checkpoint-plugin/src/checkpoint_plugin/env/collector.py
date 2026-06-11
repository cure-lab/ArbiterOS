"""Collect provider environment state into checkpoint blobs."""

from __future__ import annotations

import fnmatch
import glob as globlib
import json
import os
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Iterable

from checkpoint_plugin.store import CheckpointStore
from checkpoint_plugin.types import EnvironmentState, TrajectoryReference

from .providers import ProviderLayout

# Credential-shaped files are never copied into checkpoint blobs. Re-auth is the
# correct resume path; a checkpoint must not become a secrets store. Mirrors the
# fs-snapshot SECRET_PATTERNS but applied to the basename of every env file.
SECRET_BASENAME_PATTERNS = (
    "auth.json",
    ".env",
    ".env*",
    "*credential*",
    "*.pem",
    "*.key",
    "*.secret",
    "*token*",
)

# Config files (config.toml, settings.json, .mcp.json, ...) are kept verbatim for
# faithful restore, but they can embed secret material inline (e.g. Codex
# `experimental_bearer_token`). We redact the VALUE of any secret-shaped key
# before storing, gated to structured config so source/markdown is never altered.
_REDACTABLE_SUFFIXES = (".toml", ".json", ".jsonc")
_SECRET_VALUE_KEY_PATTERNS = (
    "*token*",
    "*secret*",
    "*password*",
    "*passwd*",
    "*credential*",
    "*bearer*",
    "*api_key*",
    "*apikey*",
    "*access_key*",
    "*private_key*",
    "trusted_hash",
)
_REDACTED = '"***redacted***"'
# Matches `key = "..."` (TOML) and `"key": "..."` (JSON), preserving the key and
# separator so only the quoted value is replaced. Bare/numeric values are left
# alone — inline secrets are quoted strings in practice.
_SECRET_ASSIGNMENT = re.compile(
    r'(?P<prefix>(?P<q>["\']?)(?P<key>[\w.-]+)(?P=q)\s*[:=]\s*)'
    r'(?P<val>"(?:[^"\\]|\\.)*"|\'[^\']*\')'
)


def _is_secret_path(path: Path) -> bool:
    name = path.name.lower()
    return any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_BASENAME_PATTERNS)


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in _SECRET_VALUE_KEY_PATTERNS)


def _redact_secret_values(data: bytes) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data

    def _replace(match: re.Match[str]) -> str:
        if _is_secret_key(match.group("key")):
            return match.group("prefix") + _REDACTED
        return match.group(0)

    return _SECRET_ASSIGNMENT.sub(_replace, text).encode("utf-8")


def _read_blob_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() in _REDACTABLE_SUFFIXES:
        return _redact_secret_values(data)
    return data


def collect_environment(
    cwd: Path,
    provider: ProviderLayout,
    store: CheckpointStore,
    trajectory_ref: TrajectoryReference | None = None,
) -> EnvironmentState:
    cwd = cwd.expanduser().resolve()
    # Some provider fields (notably Claude's model) are only delivered to the
    # SessionStart hook, which runs in a separate process from Stop. on_session_start
    # persists them to metadata.json; we fall back to that when the live env var is
    # absent so the captured state still pins the model/agent/effort.
    session_env = _session_env_fallback(store)
    skill_roots = _skill_roots(provider, cwd, session_env)
    skills = _collect_named_trees(skill_roots, store, follow_symlink_dirs=True)
    plugin_file_roots = _plugin_file_roots(provider, cwd)
    plugin_status = _collect_plugin_status(provider, cwd, session_env)
    opencode_configs = _opencode_configs(provider, cwd, session_env) if provider.name == "opencode" else []
    return EnvironmentState(
        provider=provider.name,
        model=_first_env("ANTHROPIC_MODEL", "CLAUDE_MODEL", "OPENAI_MODEL", "CODEX_MODEL", "OPENCODE_MODEL")
        or session_env.get("model")
        or _opencode_model(opencode_configs),
        permission_mode=_first_env("CLAUDE_PERMISSION_MODE", "CODEX_PERMISSION_MODE", "CODEX_SANDBOX_MODE", "OPENCODE_PERMISSION_MODE")
        or session_env.get("permission_mode"),
        mode=_first_env("CLAUDE_MODE", "CODEX_MODE", "OPENCODE_MODE") or session_env.get("mode"),
        effort=_first_env("CLAUDE_EFFORT", "OPENCODE_EFFORT") or session_env.get("effort") or _codex_effort(provider, cwd),
        agent_type=_first_env("CLAUDE_AGENT_TYPE", "CODEX_AGENT_TYPE", "OPENCODE_AGENT_TYPE") or session_env.get("agent_type"),
        memory_files=_collect_tree(provider.memory_dir, store),
        mcp_config=_store_file(provider.mcp_config, store),
        mcp_configs=_collect_named_files(_mcp_config_files(provider, cwd), store),
        mcp_servers=_collect_mcp_servers(provider, cwd, session_env, trajectory_ref),
        skills=skills,
        skill_status=_collect_skill_status(provider, cwd, skills),
        plugin_files=_collect_plugin_files(plugin_file_roots, store, installed_plugins=set(plugin_status)),
        plugin_status=plugin_status,
        settings=_collect_settings(provider.settings_files, store, force_absolute=_force_absolute_settings(provider)),
        project_context=_collect_project_context(cwd, provider.project_files, store),
        extra={
            "provider_home": str(provider.home),
            "cwd": str(cwd),
            "skill_symlinks": _collect_named_symlinks(skill_roots),
            **_codex_plugin_file_roots_extra(provider, plugin_file_roots),
            **_codex_history_extra(provider, store),
            **_opencode_extra(provider, session_env),
        },
    )


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _session_env_fallback(store: CheckpointStore) -> dict[str, str]:
    """Provider hints captured at SessionStart (e.g. Claude's model)."""
    metadata_path = store.session_dir / "metadata.json"
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    session_env = data.get("session_env") if isinstance(data, dict) else None
    if not isinstance(session_env, dict):
        return {}
    return {str(key): str(value) for key, value in session_env.items() if value}


def _collect_mcp_servers(
    provider: ProviderLayout,
    cwd: Path,
    session_env: dict[str, str] | None = None,
    trajectory_ref: TrajectoryReference | None = None,
) -> dict[str, str]:
    if provider.name == "codex":
        servers: dict[str, str] = {}
        for config in _codex_configs(provider, cwd):
            servers.update(
                {
                    str(name): _status_from_config(server_config)
                    for name, server_config in (config.get("mcp_servers") or {}).items()
                }
            )
        for config in _mcp_json_configs(provider, cwd):
            servers.update({str(name): _status_from_config(value) for name, value in _json_mcp_servers(config).items()})
        return servers
    if provider.name == "claude":
        config = _load_json(provider.home.parent / ".claude.json")
        servers = {str(name): "active" for name in (config.get("mcpServers") or {})}
        project = _nearest_project_config(config, cwd)
        if isinstance(project, dict):
            for name in project.get("mcpServers") or {}:
                servers[str(name)] = "active"
            for name in project.get("enabledMcpjsonServers") or []:
                servers[str(name)] = "active"
            for name in project.get("disabledMcpjsonServers") or []:
                servers[str(name)] = "inactive"
            for name in project.get("disabledMcpServers") or []:
                servers[str(name)] = "inactive"
        servers.update(_claude_mcp_statuses_from_trajectory_ref(trajectory_ref))
        return servers
    if provider.name == "opencode":
        servers: dict[str, str] = {}
        for config in _opencode_configs(provider, cwd, session_env):
            value = config.get("mcp")
            if isinstance(value, dict):
                servers.update({str(name): _status_from_config(server_config) for name, server_config in value.items()})
        servers.update(_opencode_runtime_mcp_servers(session_env or {}))
        return dict(sorted(servers.items()))
    return {}


def _collect_skill_status(provider: ProviderLayout, cwd: Path, skills: dict[str, str]) -> dict[str, str]:
    status = {name: "present" for name in _skill_names_from_files(skills)}
    if provider.name == "codex":
        for config in _codex_configs(provider, cwd):
            for item in (config.get("skills") or {}).get("config") or []:
                if not isinstance(item, dict):
                    continue
                name = _skill_name_from_config(item)
                enabled = item.get("enabled")
                if not name or not isinstance(enabled, bool):
                    continue
                status[name] = "active" if enabled else "inactive"
    elif provider.name == "claude":
        config = _load_json(provider.home.parent / ".claude.json")
        for name, value in _claude_skill_overrides(config).items():
            if isinstance(value, bool):
                status[str(name)] = "active" if value else "inactive"
    return dict(sorted(status.items()))


def _skill_names_from_files(skills: dict[str, str]) -> set[str]:
    names: set[str] = set()
    for rel_path in skills:
        path = PurePosixPath(rel_path)
        if path.name == "SKILL.md" and path.parent.name:
            names.add(path.parent.name)
    return names


def _skill_roots(provider: ProviderLayout, cwd: Path, session_env: dict[str, str] | None = None) -> dict[str, Path]:
    roots = dict(provider.skills_dirs)
    roots.update(_plugin_skill_roots(provider))
    if provider.name == "opencode":
        roots = _filter_opencode_skill_roots(roots, session_env)
    if provider.name == "claude":
        for project_root in _ancestor_chain(_nearest_project_root(cwd), cwd):
            roots[f"project:{project_root}:.claude/skills"] = project_root / ".claude" / "skills"
    if provider.name == "codex":
        for project_root in _ancestor_chain(_nearest_project_root(cwd), cwd):
            roots[f"project:{project_root}:.codex/skills"] = project_root / ".codex" / "skills"
            roots[f"project:{project_root}:.agents/skills"] = project_root / ".agents" / "skills"
    if provider.name == "opencode":
        for project_root in _ancestor_chain(_nearest_project_root(cwd), cwd):
            roots[f"project:{project_root}:.opencode/skills"] = project_root / ".opencode" / "skills"
            roots[f"project:{project_root}:.opencode/skill"] = project_root / ".opencode" / "skill"
            if not _opencode_flag_enabled("OPENCODE_DISABLE_EXTERNAL_SKILLS", session_env):
                roots[f"project:{project_root}:.agents/skills"] = project_root / ".agents" / "skills"
                if not _opencode_flag_enabled("OPENCODE_DISABLE_CLAUDE_CODE", session_env) and not _opencode_flag_enabled(
                    "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS",
                    session_env,
                ):
                    roots[f"project:{project_root}:.claude/skills"] = project_root / ".claude" / "skills"
        for root in _opencode_config_skill_roots(provider, cwd, session_env or {}):
            roots[f"opencode-config-skills:{root}"] = root
    return roots


def _filter_opencode_skill_roots(
    roots: dict[str, Path],
    session_env: dict[str, str] | None,
) -> dict[str, Path]:
    if not _opencode_flag_enabled("OPENCODE_DISABLE_EXTERNAL_SKILLS", session_env):
        if not _opencode_flag_enabled("OPENCODE_DISABLE_CLAUDE_CODE", session_env) and not _opencode_flag_enabled(
            "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS",
            session_env,
        ):
            return roots
        return {name: root for name, root in roots.items() if not name.startswith("claude-")}
    return {
        name: root
        for name, root in roots.items()
        if not name.startswith("agent-") and not name.startswith("claude-")
    }


def _plugin_skill_roots(provider: ProviderLayout) -> dict[str, Path]:
    if provider.name != "codex":
        return {}

    cache_root = provider.home / "plugins" / "cache"
    if not cache_root.exists() or not cache_root.is_dir():
        return {}

    roots: dict[str, Path] = {}
    for skills_dir in sorted(cache_root.glob("*/*/*/skills")):
        if not skills_dir.is_dir():
            continue
        try:
            rel = skills_dir.relative_to(cache_root)
        except ValueError:
            continue
        marketplace, plugin, version, _skills = rel.parts
        roots[f"plugin:{marketplace}:{plugin}:{version}"] = skills_dir
    return roots


def _plugin_file_roots(provider: ProviderLayout, cwd: Path) -> dict[str, Path]:
    if provider.name != "codex":
        return {}

    roots: dict[str, Path] = {}
    cache_root = provider.home / "plugins" / "cache"
    if cache_root.exists() and cache_root.is_dir():
        roots["codex-plugin-cache"] = cache_root

    for config in _codex_configs(provider, cwd):
        marketplaces = config.get("marketplaces")
        if not isinstance(marketplaces, dict):
            continue
        for name, marketplace_config in marketplaces.items():
            if not isinstance(marketplace_config, dict):
                continue
            source = marketplace_config.get("source")
            if not isinstance(source, str) or not source:
                continue
            path = Path(source).expanduser()
            if path.exists() and path.is_dir():
                roots[f"codex-marketplace:{name}"] = path

    for name, path in _codex_implicit_marketplace_roots(provider.home).items():
        roots.setdefault(f"codex-marketplace:{name}", path)

    return roots


def _codex_implicit_marketplace_roots(codex_home: Path) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    tmp = codex_home / ".tmp"
    if not tmp.exists() or not tmp.is_dir():
        return roots
    for base, use_setdefault in [(tmp, False), (tmp / "bundled-marketplaces", True)]:
        for manifest in sorted(base.glob("*/.agents/plugins/marketplace.json")):
            root = manifest.parent.parent.parent
            data = _load_json(manifest)
            name = data.get("name")
            if isinstance(name, str) and name and root.is_dir():
                if use_setdefault:
                    roots.setdefault(name, root)
                else:
                    roots[name] = root
    return roots


def _collect_plugin_files(
    roots: dict[str, Path],
    store: CheckpointStore,
    *,
    installed_plugins: set[str] | None = None,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, root in sorted(roots.items()):
        for rel, sha in _collect_plugin_file_tree(
            root,
            store,
            installed_plugins=installed_plugins if name == "codex-plugin-cache" else None,
        ).items():
            result[f"{name}/{rel}"] = sha
    return result


def _collect_plugin_file_tree(
    root: Path | None,
    store: CheckpointStore,
    *,
    installed_plugins: set[str] | None = None,
) -> dict[str, str]:
    if root is None or not root.exists() or not root.is_dir():
        return {}
    result: dict[str, str] = {}
    for path in _iter_files(root):
        if _is_secret_path(path):
            continue
        rel = path.relative_to(root).as_posix()
        if not _is_plugin_metadata_path(rel, installed_plugins=installed_plugins):
            continue
        result[rel] = store.store_blob(_read_blob_bytes(path))
    return result


def _is_plugin_metadata_path(rel: str, *, installed_plugins: set[str] | None = None) -> bool:
    parts = PurePosixPath(rel).parts
    name = parts[-1] if parts else ""
    if installed_plugins is not None and _is_installed_plugin_cache_path(parts, installed_plugins):
        return True
    if ".codex-plugin" in parts:
        return True
    if "assets" in parts:
        return True
    return name in {".app.json", ".mcp.json", "marketplace.json"}


def _is_installed_plugin_cache_path(parts: tuple[str, ...], installed_plugins: set[str]) -> bool:
    if len(parts) < 4:
        return False
    marketplace, plugin, version = parts[:3]
    if not marketplace or not plugin or not version:
        return False
    return f"{plugin}@{marketplace}" in installed_plugins


def _codex_plugin_file_roots_extra(provider: ProviderLayout, roots: dict[str, Path]) -> dict[str, object]:
    if provider.name != "codex" or not roots:
        return {}
    return {"plugin_file_roots": {name: str(path) for name, path in sorted(roots.items())}}


def _mcp_config_files(provider: ProviderLayout, cwd: Path) -> dict[str, Path]:
    files = _named_paths(provider.mcp_config_files)
    for project_root in _ancestor_chain(_nearest_project_root(cwd), cwd):
        path = project_root / ".mcp.json"
        files[f"project:{project_root}:.mcp.json"] = path
        if provider.name == "codex":
            files[f"project:{project_root}:.codex/config.toml"] = project_root / ".codex" / "config.toml"
        if provider.name == "opencode":
            files[f"project:{project_root}:opencode.json"] = project_root / "opencode.json"
            files[f"project:{project_root}:opencode.jsonc"] = project_root / "opencode.jsonc"
            files[f"project:{project_root}:.opencode/opencode.json"] = project_root / ".opencode" / "opencode.json"
            files[f"project:{project_root}:.opencode/opencode.jsonc"] = project_root / ".opencode" / "opencode.jsonc"
    return files


def _codex_configs(provider: ProviderLayout, cwd: Path) -> list[dict[str, object]]:
    configs = [_load_toml(provider.home / "config.toml")]
    for project_root in _ancestor_chain(_nearest_project_root(cwd), cwd):
        configs.append(_load_toml(project_root / ".codex" / "config.toml"))
    return [config for config in configs if config]


def _opencode_configs(
    provider: ProviderLayout,
    cwd: Path,
    session_env: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    if provider.name != "opencode":
        return []
    runtime_env = _opencode_runtime_env_from_session(session_env or {})
    config_dir = os.environ.get("OPENCODE_CONFIG_DIR") or runtime_env.get("OPENCODE_CONFIG_DIR")
    config_home = Path(config_dir).expanduser() if config_dir else provider.home
    global_config_home = _opencode_default_config_home() if config_dir else provider.home
    configs = [
        _load_json(global_config_home / "config.json"),
        _load_json(global_config_home / "opencode.json"),
        _load_json(global_config_home / "opencode.jsonc"),
    ]
    custom_config = os.environ.get("OPENCODE_CONFIG") or runtime_env.get("OPENCODE_CONFIG")
    if custom_config:
        configs.append(_load_json(Path(custom_config).expanduser()))
    if not _opencode_flag_enabled("OPENCODE_DISABLE_PROJECT_CONFIG", session_env):
        chain = _ancestor_chain(_nearest_project_root(cwd), cwd)
        for project_root in chain:
            configs.extend(_opencode_config_files(project_root))
        for project_root in reversed(chain):
            configs.extend(_opencode_config_files(project_root / ".opencode"))
    configs.extend(_opencode_config_files(Path(os.environ.get("TEST_HOME", str(Path.home()))).expanduser() / ".opencode"))
    if config_dir:
        configs.extend(_opencode_config_files(config_home))
    config_content = _opencode_config_content(session_env or {})
    if config_content:
        configs.append(_load_json_text(config_content))
    permission = os.environ.get("OPENCODE_PERMISSION") or (session_env or {}).get("opencode_permission")
    if permission:
        try:
            parsed_permission = json.loads(permission)
        except json.JSONDecodeError:
            parsed_permission = None
        if isinstance(parsed_permission, dict):
            configs.append({"permission": parsed_permission})
    resolved = _opencode_resolved_config(session_env or {})
    if resolved:
        configs.append(resolved)
    return [config for config in configs if config]


def _opencode_default_config_home() -> Path:
    home = Path(os.environ.get("TEST_HOME", str(Path.home()))).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))).expanduser() / "opencode"


def _opencode_config_files(directory: Path) -> list[dict[str, object]]:
    return [_load_json(directory / "opencode.json"), _load_json(directory / "opencode.jsonc")]


def _opencode_resolved_config(session_env: dict[str, str]) -> dict[str, object]:
    raw = os.environ.get("OPENCODE_RESOLVED_CONFIG") or session_env.get("resolved_config")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return _opencode_config_with_mcp_status(_redact_secret_object(data), session_env)


def _opencode_config_content(session_env: dict[str, str]) -> str | None:
    resolved = _opencode_resolved_config(session_env)
    if resolved:
        try:
            return json.dumps(_opencode_config_with_mcp_status(resolved, session_env), separators=(",", ":"))
        except (TypeError, ValueError):
            pass
    raw = os.environ.get("OPENCODE_CONFIG_CONTENT") or session_env.get("opencode_config_content")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(data, dict):
        return raw
    try:
        return json.dumps(_opencode_config_with_mcp_status(data, session_env), separators=(",", ":"))
    except (TypeError, ValueError):
        return raw


def _opencode_config_with_mcp_status(config: dict[str, object], session_env: dict[str, str]) -> dict[str, object]:
    statuses = _opencode_runtime_mcp_servers(session_env)
    if not statuses:
        return config
    result = dict(config)
    existing_mcp = result.get("mcp")
    mcp = dict(existing_mcp) if isinstance(existing_mcp, dict) else {}
    for name, status in statuses.items():
        if status == "active":
            enabled = True
        elif status == "inactive":
            enabled = False
        else:
            continue
        existing_server = mcp.get(name)
        server = dict(existing_server) if isinstance(existing_server, dict) else {}
        server["enabled"] = enabled
        mcp[name] = server
    if mcp:
        result["mcp"] = mcp
    return result


def _opencode_config_skill_roots(provider: ProviderLayout, cwd: Path, session_env: dict[str, str]) -> list[Path]:
    roots: list[Path] = []
    for config in _opencode_configs(provider, cwd, session_env):
        skills = config.get("skills")
        if not isinstance(skills, dict):
            continue
        paths = skills.get("paths")
        if not isinstance(paths, list):
            continue
        for item in paths:
            if not isinstance(item, str) or not item:
                continue
            expanded = _expand_opencode_path(item)
            roots.append(expanded if expanded.is_absolute() else cwd / expanded)
    return sorted(set(roots))


def _expand_opencode_path(value: str) -> Path:
    home = Path(os.environ.get("TEST_HOME", str(Path.home()))).expanduser()
    if value.startswith("~/"):
        return home / value[2:]
    return Path(value).expanduser()


def _truthy_env(name: str) -> bool:
    return _truthy_value(os.environ.get(name))


def _truthy_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    value = str(value or "").lower()
    return value in {"1", "true"}


def _opencode_flag_enabled(name: str, session_env: dict[str, str] | None) -> bool:
    if _truthy_env(name):
        return True
    runtime_env = _opencode_runtime_env_from_session(session_env or {})
    return _truthy_value(runtime_env.get(name))


def _opencode_runtime_mcp_servers(session_env: dict[str, str]) -> dict[str, str]:
    raw = os.environ.get("OPENCODE_MCP_STATUS") or session_env.get("mcp_status")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    statuses: dict[str, str] = {}
    for name, value in data.items():
        status = value.get("status") if isinstance(value, dict) else value
        if status == "connected":
            statuses[str(name)] = "active"
        elif status == "disabled":
            statuses[str(name)] = "inactive"
        elif isinstance(status, str) and status:
            statuses[str(name)] = status
    return statuses


def _claude_mcp_statuses_from_trajectory_ref(ref: TrajectoryReference | None) -> dict[str, str]:
    if ref is None or ref.provider != "claude" or not ref.transcript_path:
        return {}
    path = Path(ref.transcript_path).expanduser()
    if not path.is_file() or ref.start_offset < 0 or ref.end_offset < ref.start_offset:
        return {}
    try:
        with path.open("rb") as handle:
            handle.seek(ref.start_offset)
            data = handle.read(ref.end_offset - ref.start_offset)
    except OSError:
        return {}
    return claude_mcp_statuses_from_trajectory(data)


def claude_mcp_statuses_from_trajectory(data: bytes) -> dict[str, str]:
    """Infer Claude's live MCP status from transcript deltas.

    Claude can re-add/remove MCP tools for a turn before `~/.claude.json` reflects
    the final project field. The transcript attachments are the source of truth
    for the tool surface that the model actually saw in that turn.
    """
    statuses: dict[str, str] = {}
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        attachment = record.get("attachment")
        if not isinstance(attachment, dict):
            continue
        kind = attachment.get("type")
        if kind == "mcp_instructions_delta":
            _apply_claude_mcp_name_delta(statuses, attachment.get("removedNames"), "inactive")
            _apply_claude_mcp_name_delta(statuses, attachment.get("addedNames"), "active")
        elif kind == "deferred_tools_delta":
            _apply_claude_mcp_tool_delta(statuses, attachment.get("removedNames"), "inactive")
            _apply_claude_mcp_tool_delta(statuses, attachment.get("addedNames"), "active")
            _apply_claude_mcp_tool_delta(statuses, attachment.get("readdedNames"), "active")
    return statuses


def _apply_claude_mcp_name_delta(statuses: dict[str, str], names: object, status: str) -> None:
    if not isinstance(names, list):
        return
    for name in names:
        if isinstance(name, str) and name:
            statuses[name] = status


def _apply_claude_mcp_tool_delta(statuses: dict[str, str], names: object, status: str) -> None:
    if not isinstance(names, list):
        return
    for name in names:
        server = _claude_mcp_server_name_from_tool(name)
        if server:
            statuses[server] = status


def _claude_mcp_server_name_from_tool(name: object) -> str | None:
    if not isinstance(name, str):
        return None
    match = re.fullmatch(r"mcp__(?P<server>.+?)__[^_].*", name)
    if match is None:
        return None
    server = match.group("server")
    return server or None


def _opencode_model(configs: list[dict[str, object]]) -> str | None:
    for config in reversed(configs):
        value = config.get("model")
        if isinstance(value, str) and value:
            return value
    return None


def _opencode_extra(provider: ProviderLayout, session_env: dict[str, str]) -> dict[str, object]:
    if provider.name != "opencode":
        return {}
    extra: dict[str, object] = {}
    resolved = _opencode_resolved_config(session_env)
    if resolved:
        extra["opencode_resolved_config"] = resolved
    runtime_env = _opencode_runtime_env(session_env)
    if runtime_env:
        extra["opencode_runtime_env"] = runtime_env
    config_skill_roots = session_env.get("opencode_config_skill_roots")
    if config_skill_roots:
        try:
            roots = json.loads(config_skill_roots)
        except json.JSONDecodeError:
            roots = None
        if isinstance(roots, list):
            extra["opencode_config_skill_roots"] = [str(root) for root in roots if isinstance(root, str)]
    elif resolved:
        roots = _opencode_skill_paths_from_config(resolved)
        if roots:
            extra["opencode_config_skill_roots"] = roots
    config_content = _opencode_config_content(session_env)
    if config_content:
        extra["opencode_config_content"] = _redact_secret_values(config_content.encode("utf-8")).decode("utf-8")
    return extra


def _opencode_runtime_env(session_env: dict[str, str]) -> dict[str, str]:
    keys = (
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
        "OPENCODE_TUI_CONFIG",
        "OPENCODE_DISABLE_PROJECT_CONFIG",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS",
        "OPENCODE_DISABLE_CLAUDE_CODE",
        "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS",
        "OPENCODE_DISABLE_AUTOCOMPACT",
        "OPENCODE_DISABLE_PRUNE",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS",
        "OPENCODE_PURE",
        "OPENCODE_WORKSPACE_ID",
        "OPENCODE_EXPERIMENTAL_WORKSPACES",
        "OPENCODE_DATA_DIR",
    )
    result = _opencode_runtime_env_from_session(session_env)
    result.update({key: os.environ[key] for key in keys if os.environ.get(key)})
    permission = os.environ.get("OPENCODE_PERMISSION") or session_env.get("opencode_permission")
    if permission:
        result["OPENCODE_PERMISSION"] = permission
    return result


def _opencode_runtime_env_from_session(session_env: dict[str, str]) -> dict[str, str]:
    raw = session_env.get("opencode_runtime_env")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if isinstance(value, (str, int, float, bool))}


def _opencode_skill_paths_from_config(config: dict[str, object]) -> list[str]:
    skills = config.get("skills")
    if not isinstance(skills, dict):
        return []
    paths = skills.get("paths")
    if not isinstance(paths, list):
        return []
    return [item for item in paths if isinstance(item, str) and item]


def _redact_secret_object(value: object, key: str | None = None) -> object:
    if key and _is_secret_key(key):
        return "***redacted***"
    if isinstance(value, dict):
        return {str(k): _redact_secret_object(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_secret_object(item) for item in value]
    return value


def _codex_history_extra(provider: ProviderLayout, store: CheckpointStore) -> dict[str, str]:
    """Capture Codex prompt history (`history.jsonl`) by content hash (G3).

    Cross-session prompt recall lives here; like ~/.claude.json it's global state
    we record for drift visibility but do NOT restore wholesale (doing so would
    rewrite unrelated sessions' history). Stored as a deduped blob sha.
    """
    if provider.name != "codex":
        return {}
    path = provider.home / "history.jsonl"
    if not path.is_file() or _is_secret_path(path):
        return {}
    try:
        data = path.read_bytes()
    except OSError:
        return {}
    return {"codex_history_ref": store.store_blob(data)}


def _codex_effort(provider: ProviderLayout, cwd: Path) -> str | None:
    """Reasoning effort from Codex config (`model_reasoning_effort`).

    Codex delivers no effort field to hooks; it lives only in config.toml. Pin it
    on EnvironmentState so a resume can flag a drift, mirroring Claude's effort.
    Project-level config wins over home (later in the ancestor chain).
    """
    effort: str | None = None
    for config in _codex_configs(provider, cwd):
        value = config.get("model_reasoning_effort")
        if isinstance(value, str) and value:
            effort = value
    return effort


def _mcp_json_configs(provider: ProviderLayout, cwd: Path) -> list[dict[str, object]]:
    configs = [_load_json(path) for path in provider.mcp_config_files if path.name == ".mcp.json"]
    for project_root in _ancestor_chain(_nearest_project_root(cwd), cwd):
        configs.append(_load_json(project_root / ".mcp.json"))
    return [config for config in configs if config]


def _json_mcp_servers(config: dict[str, object]) -> dict[str, object]:
    servers = config.get("mcpServers")
    if isinstance(servers, dict):
        return servers
    return config


def _claude_skill_overrides(config: dict[str, object]) -> dict[str, object]:
    for key in ("skillOverrides", "skills"):
        value = config.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _enabled_claude_plugins(config: dict[str, object]) -> set[str]:
    enabled: set[str] = set()
    value = config.get("enabledPlugins")
    if isinstance(value, list):
        enabled.update(str(item) for item in value)
    elif isinstance(value, dict):
        enabled.update(str(name) for name, active in value.items() if active)
    return enabled


def _collect_plugin_status(
    provider: ProviderLayout,
    cwd: Path,
    session_env: dict[str, str] | None = None,
) -> dict[str, str]:
    if provider.name == "codex":
        status: dict[str, str] = {}
        for config in _codex_configs(provider, cwd):
            for name, plugin_config in (config.get("plugins") or {}).items():
                status[str(name)] = _status_from_config(plugin_config)
        return dict(sorted(status.items()))
    if provider.name == "claude":
        plugins_dir = provider.home / "plugins" / "marketplaces"
        status = {name: "present" for name in _installed_claude_plugins(plugins_dir)}
        config = _load_json(provider.home.parent / ".claude.json")
        for name in _enabled_claude_plugins(config):
            status[name] = "active"
        return dict(sorted(status.items()))
    if provider.name == "opencode":
        status: dict[str, str] = {}
        for config in _opencode_configs(provider, cwd, session_env):
            plugins = config.get("plugin")
            if isinstance(plugins, list):
                for plugin in plugins:
                    name = _opencode_plugin_name(plugin)
                    if name:
                        status[name] = "active"
        return dict(sorted(status.items()))
    return {}


def _opencode_plugin_name(plugin: object) -> str:
    if isinstance(plugin, str):
        return plugin
    if isinstance(plugin, list) and plugin and isinstance(plugin[0], str):
        return plugin[0]
    if isinstance(plugin, tuple) and plugin and isinstance(plugin[0], str):
        return plugin[0]
    return ""


def _status_from_config(config: object) -> str:
    if isinstance(config, dict):
        enabled = config.get("enabled")
        disabled = config.get("disabled")
        if isinstance(enabled, bool):
            return "active" if enabled else "inactive"
        if isinstance(disabled, bool):
            return "inactive" if disabled else "active"
    return "active"


def _skill_name_from_config(item: dict[str, object]) -> str:
    path = item.get("path")
    if isinstance(path, str) and path:
        skill_path = Path(path)
        if skill_path.name == "SKILL.md":
            return skill_path.parent.name
        return skill_path.stem or skill_path.name
    name = item.get("name")
    return str(name) if name else ""


def _installed_claude_plugins(plugins_dir: Path) -> list[str]:
    names: set[str] = set()
    for group in ("plugins", "external_plugins"):
        for path in plugins_dir.glob(f"*/{group}/*"):
            if path.is_dir():
                names.add(path.name)
    return sorted(names)


def _nearest_project_config(config: dict[str, object], cwd: Path) -> dict[str, object] | None:
    projects = config.get("projects")
    if not isinstance(projects, dict):
        return None
    for path in (cwd, *cwd.parents):
        value = projects.get(str(path))
        if isinstance(value, dict):
            return value
    return None


def _load_json(path: Path) -> dict[str, object]:
    try:
        return _load_json_text(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def _load_json_text(text: str) -> dict[str, object]:
    try:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = json.loads(_strip_jsonc(text))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _strip_jsonc(text: str) -> str:
    """Remove JSONC comments/trailing commas without touching string contents."""
    out: list[str] = []
    in_string = False
    quote = ""
    escape = False
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_string = False
            i += 1
            continue
        if ch in ('"', "'"):
            in_string = True
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _load_toml(path: Path) -> dict[str, object]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _store_file(path: Path | None, store: CheckpointStore) -> str | None:
    if path is None or not path.exists() or not path.is_file() or _is_secret_path(path):
        return None
    return store.store_blob(_read_blob_bytes(path))


def _collect_named_files(paths: dict[str, Path], store: CheckpointStore) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, path in sorted(paths.items()):
        sha = _store_file(path, store)
        if sha is not None:
            result[name] = sha
    return result


def _named_paths(paths: Iterable[Path], *, force_absolute: bool = False) -> dict[str, Path]:
    path_list = list(paths)
    basenames: dict[str, int] = {}
    for path in path_list:
        basenames[path.name] = basenames.get(path.name, 0) + 1
    result: dict[str, Path] = {}
    for path in path_list:
        key = str(path) if force_absolute or basenames[path.name] > 1 else path.name
        result[key] = path
    return result


def _collect_named_trees(
    roots: dict[str, Path],
    store: CheckpointStore,
    *,
    follow_symlink_dirs: bool = False,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, root in sorted(roots.items()):
        for rel, sha in _collect_tree(root, store, follow_symlink_dirs=follow_symlink_dirs).items():
            result[f"{name}/{rel}"] = sha
    return result


def _collect_tree(
    root: Path | None,
    store: CheckpointStore,
    *,
    follow_symlink_dirs: bool = False,
) -> dict[str, str]:
    if root is None or not root.exists() or not root.is_dir():
        return {}
    result: dict[str, str] = {}
    for path in _iter_files(root, follow_symlink_dirs=follow_symlink_dirs):
        if _is_secret_path(path):
            continue
        rel = path.relative_to(root).as_posix()
        result[rel] = store.store_blob(_read_blob_bytes(path))
    return result


def _collect_settings(
    paths: Iterable[Path],
    store: CheckpointStore,
    *,
    force_absolute: bool = False,
) -> dict[str, str]:
    settings: dict[str, str] = {}
    for name, path in _named_paths(paths, force_absolute=force_absolute).items():
        if path.exists() and path.is_file() and not _is_secret_path(path):
            settings[name] = store.store_blob(_read_blob_bytes(path))
    return settings


def _force_absolute_settings(provider: ProviderLayout) -> bool:
    return provider.name == "opencode"


def _collect_project_context(cwd: Path, project_files: list[str], store: CheckpointStore) -> dict[str, str]:
    context: dict[str, str] = {}
    for root in _ancestor_chain(_nearest_project_root(cwd), cwd):
        for rel_name in project_files:
            path = Path(rel_name)
            target = path if path.is_absolute() else root / rel_name
            if globlib.has_magic(str(target)):
                for match in sorted(Path(item) for item in globlib.glob(str(target), recursive=True)):
                    _collect_project_context_path(context, match, root, store)
                continue
            _collect_project_context_path(context, target, root, store)
    return context


def _collect_project_context_path(
    context: dict[str, str],
    path: Path,
    root: Path,
    store: CheckpointStore,
) -> None:
    if path.exists() and path.is_file() and not _is_secret_path(path):
        context[str(path.resolve())] = store.store_blob(_read_blob_bytes(path))
    elif path.exists() and path.is_dir():
        for rel, sha in _collect_tree(path, store, follow_symlink_dirs=True).items():
            try:
                key = path.relative_to(root).joinpath(rel).as_posix()
                context[str(root / key)] = sha
            except ValueError:
                context[str(path / rel)] = sha


def _collect_named_symlinks(roots: dict[str, Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, root in sorted(roots.items()):
        for rel, target in _collect_symlinks(root).items():
            result[f"{name}/{rel}"] = target
    return result


def _collect_symlinks(root: Path | None) -> dict[str, str]:
    if root is None or not root.exists() or not root.is_dir():
        return {}
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            result[path.relative_to(root).as_posix()] = str(path.resolve(strict=False))
    return result


def _iter_files(root: Path, *, follow_symlink_dirs: bool = False) -> Iterable[Path]:
    if not follow_symlink_dirs:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                yield path
        return

    seen_dirs: set[Path] = set()
    yield from _iter_files_following_symlink_dirs(root, seen_dirs)


def _iter_files_following_symlink_dirs(root: Path, seen_dirs: set[Path]) -> Iterable[Path]:
    try:
        resolved = root.resolve(strict=True)
    except OSError:
        return
    if resolved in seen_dirs:
        return
    seen_dirs.add(resolved)

    try:
        children = sorted(root.iterdir())
    except OSError:
        return
    for child in children:
        if child.is_file():
            yield child
        elif child.is_dir():
            yield from _iter_files_following_symlink_dirs(child, seen_dirs)


def _nearest_project_root(cwd: Path) -> Path:
    for path in (cwd, *cwd.parents):
        if (path / ".git").exists():
            return path
    return cwd


def _ancestor_chain(root: Path, cwd: Path) -> list[Path]:
    root = root.resolve(strict=False)
    cwd = cwd.resolve(strict=False)
    try:
        relative = cwd.relative_to(root)
    except ValueError:
        return []

    paths = [root]
    current = root
    for part in relative.parts:
        current = current / part
        paths.append(current)
    return paths


def environment_to_blob(state: EnvironmentState, store: CheckpointStore) -> str:
    return store.store_json_blob(state.to_json())


def environment_from_blob(sha: str, store: CheckpointStore) -> EnvironmentState:
    data = store.load_json_blob(sha)
    if not isinstance(data, dict):
        raise ValueError(f"Environment blob {sha} is not a JSON object")
    return EnvironmentState.from_json(data)
