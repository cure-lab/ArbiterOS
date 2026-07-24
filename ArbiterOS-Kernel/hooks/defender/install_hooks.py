#!/usr/bin/env python3
"""Install ArbiterOS defender PreToolUse hook for Codex and/or Claude Code."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

_HOOK_SCRIPT = Path(__file__).resolve().parent / "hook.py"
_KERNEL_ROOT = Path(__file__).resolve().parent.parent.parent

CODEX_HOOKS = Path.home() / ".codex" / "hooks.json"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"

OLD_HOOK_MARKERS = (
    "/scripts/defender-hook/hook.py",
    "/scripts/defender/hook.py",
    "codex-cli/scripts/defender-hook",
)

DEFENDER_MARKER = "ArbiterOS-Kernel/hooks/defender/hook.py"


def _resolve_python() -> str:
    env_py = os.environ.get("ARBITEROS_DEFENDER_PYTHON", "").strip()
    if env_py:
        return env_py
    venv_py = _KERNEL_ROOT / ".venv" / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)
    return shutil.which("python3") or sys.executable or "/usr/bin/python3"


def _hook_command() -> str:
    return f"{_resolve_python()} {_HOOK_SCRIPT}"


def _is_arbiteros_defender(cmd: str) -> bool:
    return DEFENDER_MARKER in cmd or "hooks/defender/hook.py" in cmd


def _is_legacy_defender(cmd: str) -> bool:
    return any(m in cmd for m in OLD_HOOK_MARKERS) or "defender-hook/hook.py" in cmd


def _strip_legacy(blocks: list[dict] | None) -> list[dict]:
    if not blocks:
        return []
    out: list[dict] = []
    for block in blocks:
        hooks = [
            h
            for h in block.get("hooks") or []
            if not _is_legacy_defender(str(h.get("command", "")))
            and not _is_arbiteros_defender(str(h.get("command", "")))
        ]
        if hooks:
            nb = dict(block)
            nb["hooks"] = hooks
            out.append(nb)
    return out


def _has_arbiteros_defender(blocks: list[dict]) -> bool:
    for block in blocks:
        for h in block.get("hooks") or []:
            if _is_arbiteros_defender(str(h.get("command", ""))):
                return True
    return False


def _prepend(blocks: list[dict] | None, hook: dict, *, matcher: str = ".*") -> list[dict]:
    blocks = _strip_legacy(blocks)
    if not blocks:
        blocks = [{"matcher": matcher, "hooks": []}]
    if not blocks[0].get("hooks"):
        blocks[0]["hooks"] = []
    if not _has_arbiteros_defender(blocks):
        blocks[0]["hooks"].insert(0, dict(hook))
    if "matcher" not in blocks[0]:
        blocks[0]["matcher"] = matcher
    return blocks


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def install_codex(cmd: str) -> Path:
    path = CODEX_HOOKS
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_json(path)
    confirm = {
        "type": "command",
        "command": cmd,
        "statusMessage": "ArbiterOS defender: said/done check",
        "timeout": 600,
    }
    log_only = {
        "type": "command",
        "command": cmd,
        "statusMessage": "ArbiterOS defender: recording",
        "timeout": 30,
    }
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    hooks["PreToolUse"] = _prepend(hooks.get("PreToolUse"), confirm, matcher=".*")
    hooks["PostToolUse"] = _prepend(hooks.get("PostToolUse"), log_only, matcher=".*")
    hooks["PermissionRequest"] = _prepend(
        hooks.get("PermissionRequest"), log_only, matcher=".*"
    )
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def install_claude(cmd: str) -> Path:
    """Install into ~/.claude/settings.json (same hook.py / matcher semantics)."""
    path = CLAUDE_SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_json(path)
    # Claude Code: long timeout while waiting for ArbiterOS TUI attach Y/N.
    confirm = {
        "type": "command",
        "command": cmd,
        "timeout": 600,
        "statusMessage": "ArbiterOS defender: said/done check",
    }
    log_only = {
        "type": "command",
        "command": cmd,
        "timeout": 30,
        "statusMessage": "ArbiterOS defender: recording",
    }
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    # "*" matches every tool (Claude docs); Codex uses ".*" — both work for their hosts.
    hooks["PreToolUse"] = _prepend(hooks.get("PreToolUse"), confirm, matcher="*")
    hooks["PostToolUse"] = _prepend(hooks.get("PostToolUse"), log_only, matcher="*")
    hooks["PermissionRequest"] = _prepend(
        hooks.get("PermissionRequest"), log_only, matcher="*"
    )
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install ArbiterOS defender hooks for Codex and/or Claude Code"
    )
    parser.add_argument(
        "--agent",
        choices=("codex", "claude", "all"),
        default="all",
        help="Which agent hook config to update (default: all)",
    )
    args = parser.parse_args()

    cmd = _hook_command()
    updated: list[Path] = []
    if args.agent in {"codex", "all"}:
        updated.append(install_codex(cmd))
    if args.agent in {"claude", "all"}:
        updated.append(install_claude(cmd))

    for path in updated:
        print(f"updated {path}")
    print(f"hook command: {cmd}")
    if args.agent in {"codex", "all"}:
        print("Codex: open Codex and run /hooks to trust the defender hook if prompted")
    if args.agent in {"claude", "all"}:
        print("Claude Code: restart Claude Code (or start a new session) to load hooks")
    print(
        "Confirm escalations in ArbiterOS TUI: attach <trace_id> then Y/N "
        "(Y=deny, N=allow). Optional fallback: python3",
        Path(__file__).resolve().parent / "watch.py",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
