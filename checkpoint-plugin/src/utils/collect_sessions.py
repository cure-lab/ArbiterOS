#!/usr/bin/env python3
"""Collect raw agent session paths and raw agent environment setting paths.

This script intentionally does not normalize, parse, or transform session
records. By default, it writes a manifest of native file paths. With --copy,
it also copies raw files into the output directory.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SourceGroup:
    provider: str
    label: str
    root: Path
    patterns: tuple[str, ...]
    recursive: bool = True


@dataclass(frozen=True)
class SingleFileSource:
    provider: str
    label: str
    path: Path


@dataclass(frozen=True)
class ProviderLayout:
    provider: str
    sessions: tuple[SourceGroup, ...]
    environment_groups: tuple[SourceGroup, ...]
    environment_files: tuple[SingleFileSource, ...]


def expand_user_path(value: str | None) -> Path | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return Path(os.path.expandvars(os.path.expanduser(value)))


def home_dir() -> Path:
    test_home = expand_user_path(os.environ.get("TEST_HOME"))
    return test_home if test_home is not None else Path.home()


def provider_layouts() -> list[ProviderLayout]:
    home = home_dir()
    codex_dir = home / ".codex"
    agents_dir = home / ".agents"
    claude_dir = home / ".claude"

    return [
        ProviderLayout(
            "codex",
            sessions=(
                SourceGroup("codex", "active_sessions", codex_dir / "sessions", ("*.jsonl",)),
                SourceGroup(
                    "codex",
                    "archived_sessions",
                    codex_dir / "archived_sessions",
                    ("*.jsonl",),
                ),
            ),
            environment_groups=(
                SourceGroup("codex", "skills", codex_dir / "skills", ("**/*",)),
                SourceGroup("codex", "agent_skills", agents_dir / "skills", ("**/*",)),
                SourceGroup("codex", "admin_skills", Path("/etc/codex/skills"), ("**/*",)),
                SourceGroup("codex", "hooks", codex_dir, ("hooks.json",), recursive=False),
                SourceGroup("codex", "rules", codex_dir / "rules", ("**/*",)),
                SourceGroup(
                    "codex", "profile_configs", codex_dir, ("*.config.toml",), recursive=False
                ),
                SourceGroup("codex", "runtime_logs", codex_dir / "log", ("**/*",)),
                SourceGroup(
                    "codex", "runtime_sqlite", codex_dir, ("*.sqlite*",), recursive=False
                ),
                SourceGroup("codex", "runtime_sqlite_dir", codex_dir / "sqlite", ("**/*",)),
                SourceGroup("codex", "shell_snapshots", codex_dir / "shell_snapshots", ("**/*",)),
                SourceGroup("codex", "runtime_cache", codex_dir / "cache", ("**/*",)),
                SourceGroup("codex", "plugins", codex_dir / "plugins", ("**/*",)),
                SourceGroup("codex", "vendor_imports", codex_dir / "vendor_imports", ("**/*",)),
                SourceGroup("codex", "memories", codex_dir / "memories", ("**/*",)),
                SourceGroup(
                    "codex", "memories_extensions", codex_dir / "memories_extensions", ("**/*",)
                ),
                SourceGroup("codex", "node_repl", codex_dir / "node_repl", ("**/*",)),
                SourceGroup("codex", "computer_use", codex_dir / "computer-use", ("**/*",)),
                SourceGroup("codex", "app_server_control", codex_dir / "app-server-control", ("**/*",)),
                SourceGroup("codex", "app_server_state", codex_dir / "app-server-daemon", ("**/*",)),
                SourceGroup("codex", "model_catalogs", codex_dir / "model-catalogs", ("**/*",)),
                SourceGroup("codex", "package_cache", codex_dir / "packages", ("**/*",)),
            ),
            environment_files=(
                SingleFileSource("codex", "agent_config", codex_dir / "config.toml"),
                SingleFileSource("codex", "managed_config", Path("/etc/codex/managed_config.toml")),
                SingleFileSource("codex", "requirements", Path("/etc/codex/requirements.toml")),
                SingleFileSource(
                    "codex",
                    "macos_managed_preferences",
                    Path("/Library/Managed Preferences/com.openai.codex.plist"),
                ),
                SingleFileSource("codex", "agent_auth", codex_dir / "auth.json"),
                SingleFileSource("codex", "mcp_config", codex_dir / "mcp.json"),
                SingleFileSource("codex", "agent_instructions", codex_dir / "AGENTS.md"),
                SingleFileSource(
                    "codex", "agent_instructions_override", codex_dir / "AGENTS.override.md"
                ),
                SingleFileSource("codex", "session_index", codex_dir / "session_index.jsonl"),
                SingleFileSource("codex", "history", codex_dir / "history.jsonl"),
                SingleFileSource("codex", "models_cache", codex_dir / "models_cache.json"),
                SingleFileSource("codex", "version", codex_dir / "version.json"),
                SingleFileSource(
                    "codex", "global_state", codex_dir / ".codex-global-state.json"
                ),
            ),
        ),
        ProviderLayout(
            "claude",
            sessions=(
                SourceGroup("claude", "projects", claude_dir / "projects", ("*.jsonl",)),
                SourceGroup("claude", "sessions", claude_dir / "sessions", ("*.jsonl",)),
            ),
            environment_groups=(
                SourceGroup("claude", "skills", claude_dir / "skills", ("**/*",)),
                SourceGroup("claude", "agents", claude_dir / "agents", ("**/*",)),
                SourceGroup("claude", "commands", claude_dir / "commands", ("**/*",)),
                SourceGroup("claude", "output_styles", claude_dir / "output-styles", ("**/*",)),
                SourceGroup("claude", "rules", claude_dir / "rules", ("**/*",)),
                SourceGroup("claude", "file_history", claude_dir / "file-history", ("**/*",)),
                SourceGroup("claude", "runtime_cache", claude_dir / "cache", ("**/*",)),
                SourceGroup("claude", "config_backups", claude_dir / "backups", ("**/*",)),
                SourceGroup("claude", "plugins", claude_dir / "plugins", ("**/*",)),
                SourceGroup("claude", "downloads", claude_dir / "downloads", ("**/*",)),
                SourceGroup("claude", "paste_cache", claude_dir / "paste-cache", ("**/*",)),
                SourceGroup("claude", "plans", claude_dir / "plans", ("**/*",)),
                SourceGroup("claude", "session_env", claude_dir / "session-env", ("**/*",)),
                SourceGroup("claude", "shell_snapshots", claude_dir / "shell-snapshots", ("**/*",)),
                SourceGroup("claude", "tasks", claude_dir / "tasks", ("**/*",)),
                SourceGroup("claude", "todos", claude_dir / "todos", ("**/*",)),
                SourceGroup("claude", "telemetry", claude_dir / "telemetry", ("**/*",)),
                SourceGroup("claude", "statsig", claude_dir / "statsig", ("**/*",)),
                SourceGroup("claude", "ide", claude_dir / "ide", ("**/*",)),
                SourceGroup(
                    "claude",
                    "managed_settings_dir_macos",
                    Path("/Library/Application Support/ClaudeCode"),
                    ("**/*",),
                ),
                SourceGroup(
                    "claude", "managed_settings_dir_unix", Path("/etc/claude-code"), ("**/*",)
                ),
            ),
            environment_files=(
                SingleFileSource("claude", "agent_settings", claude_dir / "settings.json"),
                SingleFileSource("claude", "agent_config", claude_dir / "config.json"),
                SingleFileSource("claude", "legacy_agent_settings", claude_dir / "claude.json"),
                SingleFileSource("claude", "mcp_config", home / ".claude.json"),
                SingleFileSource("claude", "agent_instructions", claude_dir / "CLAUDE.md"),
                SingleFileSource(
                    "claude",
                    "macos_managed_preferences",
                    Path("/Library/Managed Preferences/com.anthropic.claudecode.plist"),
                ),
            ),
        ),
    ]


def iter_matching_files(group: SourceGroup) -> Iterable[Path]:
    for pattern in group.patterns:
        if group.recursive:
            yield from group.root.rglob(pattern)
        else:
            yield from group.root.glob(pattern)


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def file_record(
    provider: str,
    kind: str,
    label: str,
    source: Path,
    output_dir: Path,
    copy_files: bool,
    relative_destination: Path | None = None,
) -> tuple[dict, str]:
    record = {
        "kind": kind,
        "label": label,
        "source": str(source),
        "bytes": source.stat().st_size,
    }

    if not copy_files:
        ref = str(source)
        record["ref"] = ref
        return record, ref

    destination = output_dir / provider / kind / label / (
        relative_destination if relative_destination is not None else source.name
    )
    copy_file(source, destination)
    ref = str(destination.relative_to(output_dir))
    record["destination"] = str(destination)
    record["ref"] = ref
    record["bytes"] = destination.stat().st_size
    return record, ref


def relative_to_root(path: Path, root: Path) -> Path:
    try:
        return path.relative_to(root)
    except ValueError:
        return Path("_external") / absolute_mirror_path(path.resolve())


def absolute_mirror_path(path: Path) -> Path:
    if path.is_absolute():
        return Path(*path.parts[1:])
    return path


def nearest_project_root(cwd: Path) -> Path:
    for path in (cwd, *cwd.parents):
        if (path / ".git").exists():
            return path
    return cwd


def ancestor_chain(root: Path, cwd: Path) -> list[Path]:
    try:
        relative = cwd.relative_to(root)
    except ValueError:
        return [cwd]

    paths = [root]
    current = root
    for part in relative.parts:
        current = current / part
        paths.append(current)
    return paths


def extract_cwds(value: object) -> set[Path]:
    found: set[Path] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "cwd" and isinstance(nested, str):
                cwd = expand_user_path(nested)
                if cwd is not None and cwd.is_absolute():
                    found.add(cwd)
            else:
                found.update(extract_cwds(nested))
    elif isinstance(value, list):
        for nested in value:
            found.update(extract_cwds(nested))
    return found


def session_cwds(session_path: Path, manifest: dict, provider: str) -> set[Path]:
    cwds: set[Path] = set()
    try:
        with session_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    cwds.update(extract_cwds(json.loads(line)))
                except json.JSONDecodeError as exc:
                    manifest["errors"].append(
                        {
                            "provider": provider,
                            "kind": "sessions",
                            "source": str(session_path),
                            "error": f"invalid jsonl line while reading cwd: {exc}",
                        }
                    )
                    continue
    except OSError as exc:
        manifest["errors"].append(
            {
                "provider": provider,
                "kind": "sessions",
                "source": str(session_path),
                "error": f"could not read session cwd: {exc}",
            }
        )
    return cwds


def provider_entry(manifest: dict, provider: str) -> dict:
    return manifest["providers"].setdefault(
        provider,
        {
            "sources": [],
            "files": [],
            "sessionContextRefs": [],
        },
    )


def collect_group(
    group: SourceGroup,
    output_dir: Path,
    entry: dict,
    manifest: dict,
    kind: str,
    copy_files: bool,
) -> list[str]:
    entry["sources"].append(
        {
            "kind": kind,
            "label": group.label,
            "root": str(group.root),
            "patterns": list(group.patterns),
            "exists": group.root.exists(),
        }
    )

    refs: list[str] = []
    if not group.root.exists():
        return refs

    try:
        sources = sorted(set(iter_matching_files(group)))
    except OSError as exc:
        manifest["errors"].append(
            {
                "provider": group.provider,
                "kind": kind,
                "source": str(group.root),
                "error": str(exc),
            }
        )
        return refs

    for source in sources:
        if not source.is_file():
            continue
        rel = relative_to_root(source, group.root)
        try:
            record, ref = file_record(
                group.provider,
                kind,
                group.label,
                source,
                output_dir,
                copy_files,
                rel,
            )
            refs.append(ref)
            entry["files"].append(record)
        except OSError as exc:
            manifest["errors"].append(
                {
                    "provider": group.provider,
                    "kind": kind,
                    "source": str(source),
                    "error": str(exc),
                }
            )
    return refs


def collect_single_file(
    source: SingleFileSource,
    output_dir: Path,
    entry: dict,
    manifest: dict,
    kind: str,
    copy_files: bool,
) -> str | None:
    entry["sources"].append(
        {
            "kind": kind,
            "label": source.label,
            "root": str(source.path),
            "patterns": [source.path.name],
            "exists": source.path.exists(),
        }
    )

    if not source.path.exists() or not source.path.is_file():
        return None

    try:
        record, ref = file_record(
            source.provider,
            kind,
            source.label,
            source.path,
            output_dir,
            copy_files,
        )
        entry["files"].append(record)
        return ref
    except OSError as exc:
        manifest["errors"].append(
            {
                "provider": source.provider,
                "kind": kind,
                "source": str(source.path),
                "error": str(exc),
            }
        )
        return None


def collect_context_file_once(
    provider: str,
    label: str,
    source: Path,
    output_dir: Path,
    entry: dict,
    manifest: dict,
    copied_context: dict[str, str],
    copy_files: bool,
) -> str | None:
    if not source.exists() or not source.is_file():
        return None

    cache_key = str(source)
    cached = copied_context.get(cache_key)
    if cached is not None:
        return cached

    try:
        record, ref = file_record(
            provider,
            "environment",
            label,
            source,
            output_dir,
            copy_files,
            absolute_mirror_path(source),
        )
        copied_context[cache_key] = ref
        entry["files"].append(record)
        return ref
    except OSError as exc:
        manifest["errors"].append(
            {
                "provider": provider,
                "kind": "environment",
                "source": str(source),
                "error": str(exc),
            }
        )
        return None


def collect_context_tree_once(
    provider: str,
    label: str,
    root: Path,
    output_dir: Path,
    entry: dict,
    manifest: dict,
    copied_context: dict[str, str],
    copy_files: bool,
) -> list[str]:
    refs: list[str] = []
    if not root.exists() or not root.is_dir():
        return refs

    try:
        sources = sorted(root.rglob("*"))
    except OSError as exc:
        manifest["errors"].append(
            {
                "provider": provider,
                "kind": "environment",
                "source": str(root),
                "error": str(exc),
            }
        )
        return refs

    for source in sources:
        if not source.is_file():
            continue
        ref = collect_context_file_once(
            provider, label, source, output_dir, entry, manifest, copied_context, copy_files
        )
        if ref is not None:
            refs.append(ref)
    return refs


def collect_codex_project_context(
    cwd: Path,
    output_dir: Path,
    entry: dict,
    manifest: dict,
    copied_context: dict[str, str],
    copy_files: bool,
) -> list[str]:
    refs: list[str] = []
    for path in ancestor_chain(nearest_project_root(cwd), cwd):
        for name in ("AGENTS.override.md", "AGENTS.md"):
            ref = collect_context_file_once(
                "codex",
                "project_instructions",
                path / name,
                output_dir,
                entry,
                manifest,
                copied_context,
                copy_files,
            )
            if ref is not None:
                refs.append(ref)

        codex_dir = path / ".codex"
        for name in ("config.toml", "hooks.json", "requirements.toml"):
            ref = collect_context_file_once(
                "codex",
                "project_config",
                codex_dir / name,
                output_dir,
                entry,
                manifest,
                copied_context,
                copy_files,
            )
            if ref is not None:
                refs.append(ref)

        for name in ("rules", "skills"):
            refs.extend(
                collect_context_tree_once(
                    "codex",
                    f"project_{name}",
                    codex_dir / name,
                    output_dir,
                    entry,
                    manifest,
                    copied_context,
                    copy_files,
                )
            )

        refs.extend(
            collect_context_tree_once(
                "codex",
                "project_agent_skills",
                path / ".agents" / "skills",
                output_dir,
                entry,
                manifest,
                copied_context,
                copy_files,
            )
        )
    return refs


def collect_claude_project_context(
    cwd: Path,
    session_path: Path,
    output_dir: Path,
    entry: dict,
    manifest: dict,
    copied_context: dict[str, str],
    copy_files: bool,
) -> list[str]:
    refs: list[str] = []
    for path in ancestor_chain(nearest_project_root(cwd), cwd):
        for source in (
            path / "CLAUDE.md",
            path / "CLAUDE.local.md",
            path / ".mcp.json",
            path / ".claude" / "CLAUDE.md",
            path / ".claude" / "settings.json",
            path / ".claude" / "settings.local.json",
        ):
            ref = collect_context_file_once(
                "claude",
                "project_config",
                source,
                output_dir,
                entry,
                manifest,
                copied_context,
                copy_files,
            )
            if ref is not None:
                refs.append(ref)

        for name in ("rules", "skills", "agents", "commands", "output-styles"):
            refs.extend(
                collect_context_tree_once(
                    "claude",
                    f"project_{name}",
                    path / ".claude" / name,
                    output_dir,
                    entry,
                    manifest,
                    copied_context,
                    copy_files,
                )
            )

    refs.extend(
        collect_context_tree_once(
            "claude",
            "project_memory",
            session_path.parent / "memory",
            output_dir,
            entry,
            manifest,
            copied_context,
            copy_files,
        )
    )
    return refs


def session_project_context_refs(
    provider: str,
    session_path: Path,
    output_dir: Path,
    entry: dict,
    manifest: dict,
    copied_context: dict[str, str],
    copy_files: bool,
) -> list[str]:
    refs: list[str] = []
    for cwd in sorted(session_cwds(session_path, manifest, provider)):
        if provider == "codex":
            refs.extend(
                collect_codex_project_context(
                    cwd, output_dir, entry, manifest, copied_context, copy_files
                )
            )
        elif provider == "claude":
            refs.extend(
                collect_claude_project_context(
                    cwd, session_path, output_dir, entry, manifest, copied_context, copy_files
                )
            )
    return sorted(set(refs))


def collect(output_dir: Path, copy_files: bool = False) -> dict:
    manifest: dict = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": "raw-copy" if copy_files else "path-reference",
        "settingsSource": "raw-agent-settings",
        "notes": [
            "sessionContextRefs point to raw agent settings captured at collection time.",
            "In path-reference mode, refs are native source paths and raw files are not copied.",
        ],
        "providers": {},
        "errors": [],
    }

    output_dir.mkdir(parents=True, exist_ok=True)

    for layout in provider_layouts():
        entry = provider_entry(manifest, layout.provider)
        context_refs: list[str] = []
        copied_context: dict[str, str] = {}

        for group in layout.environment_groups:
            context_refs.extend(
                collect_group(group, output_dir, entry, manifest, "environment", copy_files)
            )

        for source in layout.environment_files:
            ref = collect_single_file(source, output_dir, entry, manifest, "environment", copy_files)
            if ref is not None:
                context_refs.append(ref)

        entry["sessionContextRefs"] = context_refs

        for group in layout.sessions:
            session_refs = collect_group(group, output_dir, entry, manifest, "sessions", copy_files)
            session_ref_set = set(session_refs)
            for file_entry in entry["files"]:
                if file_entry.get("kind") != "sessions":
                    continue
                if file_entry["ref"] in session_ref_set:
                    project_refs = session_project_context_refs(
                        layout.provider,
                        Path(file_entry["source"]),
                        output_dir,
                        entry,
                        manifest,
                        copied_context,
                        copy_files,
                    )
                    file_entry["contextRefs"] = sorted(set(context_refs + project_refs))

    manifest_path = output_dir / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    return manifest


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(__file__).resolve().parent / "raw_sessions" / stamp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect raw agent session and settings path references."
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=default_output_dir(),
        help="Output directory for manifest.json and optional copied files.",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy raw files into the output directory instead of only recording source paths.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output.expanduser().resolve()
    manifest = collect(output_dir, copy_files=args.copy)
    found = sum(len(provider["files"]) for provider in manifest["providers"].values())
    verb = "Copied" if args.copy else "Referenced"
    print(f"{verb} {found} raw files in {output_dir}")
    if manifest["errors"]:
        print(f"Completed with {len(manifest['errors'])} errors; see manifest.json")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
