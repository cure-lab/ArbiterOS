"""Filter checkpoint-plugin's own hook entries from settings/hooks JSON blobs.

Used by the resume diff and restorer so freshly-installed plugin hooks don't
appear as environment changes and aren't reverted on restore.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_PLUGIN_COMMAND_RE = re.compile(r"-m\s+checkpoint_plugin\.integrations\.")

_CLAUDE_BASENAMES = frozenset({"settings.json", "settings.local.json"})
_CODEX_BASENAMES = frozenset({"hooks.json"})

_CLAUDE_PATH_SUFFIXES = (
    ".claude/settings.json",
    ".claude/settings.local.json",
)
_CODEX_PATH_SUFFIXES = (".codex/hooks.json",)


def is_hook_config_basename(name: str, provider: str) -> bool:
    if provider == "claude":
        return name in _CLAUDE_BASENAMES
    if provider == "codex":
        return name in _CODEX_BASENAMES
    return False


def is_hook_config_path(path: Path | str, provider: str) -> bool:
    text = str(path).replace("\\", "/")
    if provider == "claude":
        return any(text.endswith(suffix) for suffix in _CLAUDE_PATH_SUFFIXES)
    if provider == "codex":
        return any(text.endswith(suffix) for suffix in _CODEX_PATH_SUFFIXES)
    return False


def strip_plugin_hooks(blob: bytes) -> bytes:
    data = _load(blob)
    if data is None:
        return blob
    if not _strip_in_place(data):
        return blob
    return _dump(data)


def merge_plugin_hooks(current: bytes, target: bytes) -> bytes:
    target_data = _load(target)
    current_data = _load(current)
    if target_data is None or current_data is None:
        return target

    plugin_entries = _collect_plugin_entries(current_data)
    target_plugin_entries = _collect_plugin_entries(target_data)
    if not plugin_entries and not target_plugin_entries:
        return target

    _strip_in_place(target_data)

    if plugin_entries:
        hooks = target_data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            hooks = {}
            target_data["hooks"] = hooks
        for event, entries in plugin_entries.items():
            existing = hooks.setdefault(event, [])
            if not isinstance(existing, list):
                existing = []
                hooks[event] = existing
            existing_commands = set(_iter_commands(existing))
            for entry in entries:
                command = _first_command(entry)
                if command is None or command in existing_commands:
                    continue
                existing.append(entry)
                existing_commands.add(command)

    return _dump(target_data)


def _load(blob: bytes) -> dict[str, Any] | None:
    if not blob:
        return {}
    try:
        data = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _dump(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _strip_in_place(data: dict[str, Any]) -> bool:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        kept_entries: list[Any] = []
        for entry in entries:
            if not isinstance(entry, dict):
                kept_entries.append(entry)
                continue
            entry_hooks = entry.get("hooks")
            if not isinstance(entry_hooks, list):
                kept_entries.append(entry)
                continue
            kept_hooks = [hook for hook in entry_hooks if not _is_plugin_hook(hook)]
            if not kept_hooks:
                continue
            new_entry = dict(entry)
            new_entry["hooks"] = kept_hooks
            kept_entries.append(new_entry)
        if kept_entries:
            hooks[event] = kept_entries
        else:
            del hooks[event]
    return True


def _collect_plugin_entries(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            continue
        plugin_entries: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_hooks = entry.get("hooks")
            if not isinstance(entry_hooks, list):
                continue
            plugin_hooks = [hook for hook in entry_hooks if _is_plugin_hook(hook)]
            if not plugin_hooks:
                continue
            new_entry = dict(entry)
            new_entry["hooks"] = plugin_hooks
            plugin_entries.append(new_entry)
        if plugin_entries:
            out[event] = plugin_entries
    return out


def _is_plugin_hook(hook: Any) -> bool:
    if not isinstance(hook, dict):
        return False
    if hook.get("type") != "command":
        return False
    command = hook.get("command")
    return isinstance(command, str) and bool(_PLUGIN_COMMAND_RE.search(command))


def _iter_commands(entries: list[Any]) -> list[str]:
    out: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_hooks = entry.get("hooks")
        if not isinstance(entry_hooks, list):
            continue
        for hook in entry_hooks:
            if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                out.append(hook["command"])
    return out


def _first_command(entry: dict[str, Any]) -> str | None:
    entry_hooks = entry.get("hooks")
    if not isinstance(entry_hooks, list):
        return None
    for hook in entry_hooks:
        if isinstance(hook, dict) and isinstance(hook.get("command"), str):
            return hook["command"]
    return None
