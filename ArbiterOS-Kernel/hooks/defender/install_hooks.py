#!/usr/bin/env python3
"""Install ArbiterOS defender hook into ~/.codex/hooks.json (keeps other hooks)."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

HOOKS = Path.home() / ".codex" / "hooks.json"
_HOOK_SCRIPT = Path(__file__).resolve().parent / "hook.py"
_KERNEL_ROOT = Path(__file__).resolve().parent.parent.parent


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


OLD_HOOK_MARKERS = (
    "/scripts/defender-hook/hook.py",
    "/scripts/defender/hook.py",
    "codex-cli/scripts/defender-hook",
)

DEFENDER_MARKER = "ArbiterOS-Kernel/hooks/defender/hook.py"


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


def _prepend(blocks: list[dict] | None, hook: dict) -> list[dict]:
    blocks = _strip_legacy(blocks)
    if not blocks:
        blocks = [{"matcher": ".*", "hooks": []}]
    if not blocks[0].get("hooks"):
        blocks[0]["hooks"] = []
    if not _has_arbiteros_defender(blocks):
        blocks[0]["hooks"].insert(0, dict(hook))
    if "matcher" not in blocks[0]:
        blocks[0]["matcher"] = ".*"
    return blocks


def main() -> int:
    if not HOOKS.exists():
        # Create a minimal hooks.json if Codex has never written one.
        HOOKS.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {"hooks": {}}
    else:
        data = json.loads(HOOKS.read_text(encoding="utf-8"))

    cmd = _hook_command()
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
    hooks["PreToolUse"] = _prepend(hooks.get("PreToolUse"), confirm)
    hooks["PostToolUse"] = _prepend(hooks.get("PostToolUse"), log_only)
    hooks["PermissionRequest"] = _prepend(hooks.get("PermissionRequest"), log_only)
    HOOKS.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"updated {HOOKS}")
    print(f"hook command: {cmd}")
    print("open Codex and run /hooks to trust the defender hook if prompted")
    print("start watch: python3", Path(__file__).resolve().parent / "watch.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
