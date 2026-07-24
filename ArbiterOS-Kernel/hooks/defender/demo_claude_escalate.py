#!/usr/bin/env python3
"""Demo: Claude-style PreToolUse escalate → ArbiterOS TUI attach Y/N.

Manufactures a Said/Done mismatch (Gateway said one command, hook Done another),
then blocks in hook.py until you answer in ArbiterOS TUI:

  attach <trace_id>
  Y = deny   |   N = allow

Usage (Kernel/TUI already running recommended):

  python3 ArbiterOS-Kernel/hooks/defender/demo_claude_escalate.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

_HOOK_DIR = Path(__file__).resolve().parent
_KERNEL_ROOT = _HOOK_DIR.parent.parent
_HOOK = _HOOK_DIR / "hook.py"


def _ensure_path() -> None:
    root = str(_KERNEL_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def main() -> int:
    _ensure_path()
    os.environ.setdefault("ARBITEROS_KERNEL_ROOT", str(_KERNEL_ROOT))
    # Fail fast if nobody answers (10 min); 0 = wait forever.
    os.environ.setdefault("ARBITEROS_DEFENDER_PROMPT_TIMEOUT_SEC", "600")

    from arbiteros_kernel.execution_check import register_pending_toolcalls
    from arbiteros_kernel.session_index import register_binding
    from arbiteros_kernel.tui_bridge import enable_tui_mode, list_pending_confirms

    enable_tui_mode()

    trace_id = f"demo-claude-hook-{uuid.uuid4().hex[:8]}"
    session_id = f"claude-demo-session-{uuid.uuid4().hex[:8]}"
    tool_use_id = f"toolu_demo_{uuid.uuid4().hex[:10]}"

    # Gateway "Said": safe echo.
    register_binding(
        trace_id=trace_id,
        session_id=session_id,
        channel="claude_code",
    )
    register_pending_toolcalls(
        trace_id=trace_id,
        toolcalls=[
            {
                "tool_call_id": tool_use_id,
                "tool_name": "Bash",
                "arguments": {"command": "echo said-safe"},
            }
        ],
    )

    # Claude Code PreToolUse "Done": different command → detail mismatch → escalate.
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,
        "cwd": str(Path.home()),
        "tool_name": "Bash",
        "tool_use_id": tool_use_id,
        "tool_input": {"command": "echo done-MISMATCH-please-confirm"},
    }

    print("=" * 60)
    print("ArbiterOS Claude hook escalate demo")
    print("=" * 60)
    print(f"trace_id:   {trace_id}")
    print(f"session_id: {session_id}")
    print(f"tool_use:   {tool_use_id}")
    print()
    print("Said (Gateway):  Bash  command=echo said-safe")
    print("Done (Hook):     Bash  command=echo done-MISMATCH-please-confirm")
    print()
    print("In ArbiterOS TUI:")
    print(f"  1) list   (should show block pending on this trace)")
    print(f"  2) attach {trace_id}")
    print("  3) Y = deny tool   |   N = allow mismatched Done")
    print()
    print("Hook is now waiting for your decision...")
    print("=" * 60)

    def _watch_pending() -> None:
        for _ in range(40):
            time.sleep(0.25)
            pending = [
                p
                for p in list_pending_confirms()
                if p.get("trace_id") == trace_id
            ]
            if pending:
                kind = pending[0].get("kind")
                print(f"\n[demo] TUI pending visible: kind={kind} request_id={pending[0].get('request_id')}")
                return
        print("\n[demo] warning: TUI pending not seen yet (is ArbiterOS TUI / enable_tui_mode ok?)")

    threading.Thread(target=_watch_pending, daemon=True).start()

    py = os.environ.get("ARBITEROS_DEFENDER_PYTHON") or sys.executable
    proc = subprocess.run(
        [py, str(_HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=str(_HOOK_DIR),
        env={**os.environ, "ARBITEROS_KERNEL_ROOT": str(_KERNEL_ROOT)},
    )
    print("\n--- hook stdout ---")
    print(proc.stdout.strip() or "(empty = allow / exit 0 with no deny JSON)")
    if proc.stderr.strip():
        print("--- hook stderr ---")
        print(proc.stderr.strip())
    print(f"--- exit {proc.returncode} ---")
    if proc.returncode == 0 and not (proc.stdout or "").strip():
        print("Result: ALLOWED (you pressed N, or equivalent allow)")
    elif "permissionDecision" in (proc.stdout or ""):
        print("Result: DENIED via hook JSON (you pressed Y, or timeout/deny)")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
