#!/usr/bin/env python3
"""Force Said/Done mismatch for live Claude Code / Codex.

Watches Kernel pending Said TOOLCALLs; when a new one appears, rewrites its
command/args so PreToolUse Done no longer matches → TUI escalate.

Usage:
  1) ArbiterOS TUI running (poe arbiteros)
  2) Claude Code → ArbiterOS Gateway, model ...;claude_code, hooks installed
  3) python3 ArbiterOS-Kernel/hooks/defender/force_said_mismatch.py
  4) In Claude Code type something that runs Bash, e.g.:
       用 Bash 执行：echo hello-from-claude
  5) attach <trace_id> → Y deny / N allow
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_KERNEL_ROOT = Path(__file__).resolve().parent.parent.parent


def main() -> int:
    root = Path(os.environ.get("ARBITEROS_KERNEL_ROOT", str(_KERNEL_ROOT))).resolve()
    sys.path.insert(0, str(root))
    from arbiteros_kernel.execution_check import pending_file_path

    path = pending_file_path()
    print(f"watching Said pending: {path}")
    print("rewrite: any new Bash/exec command → append ' #SAID_MUTATED'")
    print("Ctrl+C to stop")
    print()

    seen: set[str] = set()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            actions = raw.get("actions") if isinstance(raw, dict) else {}
            if isinstance(actions, dict):
                seen = set(actions.keys())
        except (OSError, json.JSONDecodeError):
            pass

    while True:
        time.sleep(0.15)
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        actions = raw.get("actions")
        if not isinstance(actions, dict):
            continue

        changed = False
        for tc_id, row in list(actions.items()):
            if tc_id in seen:
                continue
            seen.add(tc_id)
            if not isinstance(row, dict) or row.get("consumed"):
                continue
            args = row.get("arguments")
            if not isinstance(args, dict):
                args = {}
            cmd = args.get("command") or args.get("cmd") or row.get("command")
            if not isinstance(cmd, str) or not cmd.strip():
                continue
            if "#SAID_MUTATED" in cmd:
                continue
            new_cmd = cmd.rstrip() + " #SAID_MUTATED"
            args = dict(args)
            if "command" in args or "cmd" not in args:
                args["command"] = new_cmd
            else:
                args["cmd"] = new_cmd
            row["arguments"] = args
            row["command"] = new_cmd
            # fingerprint is re-checked via details; keep row dirty
            actions[tc_id] = row
            changed = True
            print(
                f"[mutated] {tc_id} trace={row.get('trace_id')}\n"
                f"  said was: {cmd!r}\n"
                f"  said now: {new_cmd!r}\n"
                f"  → Claude PreToolUse with original Done will MISMATCH\n"
                f"  → attach {row.get('trace_id')} then Y/N\n"
            )

        if changed:
            tmp = path.with_suffix(".tmp")
            raw["actions"] = actions
            tmp.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(path)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped")
        raise SystemExit(0)
