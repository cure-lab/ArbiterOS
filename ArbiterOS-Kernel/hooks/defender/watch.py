#!/usr/bin/env python3
"""
ArbiterOS defender watch — run in a separate terminal from the agent.

Shows escalated (unknown / mismatched) tool actions; type 1 to allow.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from common import (
    LOG_DIR,
    POLL_INTERVAL_SEC,
    as_text,
    list_open_pending,
    pending_path,
    write_decision,
)


def _print_banner() -> None:
    print("=" * 60)
    print("ARBITEROS DEFENDER WATCH")
    print("=" * 60)
    print(f"queue: {LOG_DIR / 'pending'}")
    print("Kernel auto-allows Gateway-known actions.")
    print("Only unknown / mismatched actions land here.")
    print("Prefer ArbiterOS TUI: attach <trace_id> then Y/N (Y=deny, N=allow).")
    print("This watch is a fallback. Type 1 + Enter to ALLOW. Anything else DENIES.")
    print("Ctrl+C to quit.")
    print("=" * 60)
    print("Ready — idle until an escalated action needs approval.")
    print()


def _format_request(req: dict[str, Any]) -> str:
    lines = [
        f"request: {req.get('request_id')}",
        f"time:    {req.get('created_at')}",
        f"event:   {req.get('hook_event_name')}",
        f"tool:    {req.get('summary')}",
    ]
    if req.get("escalate_reason"):
        lines.append(f"why:     {req.get('escalate_reason')}")
    if req.get("trace_id"):
        lines.append(f"trace:   {req.get('trace_id')}")
    if req.get("cwd"):
        lines.append(f"cwd:     {req.get('cwd')}")
    if req.get("session_id"):
        lines.append(f"session: {req.get('session_id')}")
    if req.get("turn_id"):
        lines.append(f"turn:    {req.get('turn_id')}")
    if req.get("tool_use_id"):
        lines.append(f"call_id: {req.get('tool_use_id')}")
    if req.get("diff"):
        lines.append(f"diff:    {req.get('diff')}")

    if req.get("said_summary") or req.get("done_summary"):
        lines.append("")
        lines.append("said (Gateway):")
        lines.append(as_text(req.get("said_summary") or "(none)", 2000))
        lines.append("")
        lines.append("done (Hook about to run):")
        lines.append(as_text(req.get("done_summary") or "(none)", 2000))

    detail = req.get("tool_input")
    if detail is not None:
        lines.append("")
        lines.append("tool_input:")
        lines.append(as_text(detail, 3000))
    return "\n".join(lines)


def _handle_one(req: dict[str, Any], auto: str | None) -> None:
    rid = str(req.get("request_id") or "")
    if not rid:
        return

    print(_format_request(req))
    print()
    print("> ", end="", flush=True)

    if auto is not None:
        answer = auto
        print(answer)
    else:
        try:
            answer = sys.stdin.readline()
        except KeyboardInterrupt:
            print("\n(watch still running — request stays pending)")
            raise
        answer = (answer or "").strip()

    if answer == "1":
        write_decision(rid, "allow", "user_confirmed_in_watch")
        print("→ ALLOWED\n")
    else:
        write_decision(rid, "deny", f"user_denied_in_watch:{answer!r}")
        print(f"→ DENIED ({answer!r})\n")

    try:
        pending_path(rid).unlink(missing_ok=True)
    except OSError:
        pass

    # Keep ArbiterOS TUI queue in sync when watch answers first.
    try:
        from common import ensure_kernel_on_path

        ensure_kernel_on_path()
        from arbiteros_kernel.tui_bridge import clear_pending_confirm

        clear_pending_confirm(rid)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="ArbiterOS defender sidecar watch")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Handle one pending request then exit",
    )
    parser.add_argument(
        "--auto",
        choices=("1", "deny"),
        help="Non-interactive: always allow (1) or deny",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print a line on each idle poll (debug)",
    )
    args = parser.parse_args()

    if not args.auto:
        _print_banner()

    try:
        while True:
            pending = list_open_pending()
            if pending:
                _handle_one(pending[0], args.auto)
                if args.once:
                    return 0
                continue

            if args.once:
                print("No pending requests.")
                return 0

            if args.auto:
                time.sleep(POLL_INTERVAL_SEC)
                continue

            if args.verbose:
                print(f"idle poll ({LOG_DIR / 'pending'})", flush=True)
            time.sleep(POLL_INTERVAL_SEC)
    except KeyboardInterrupt:
        print("\nwatch stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
