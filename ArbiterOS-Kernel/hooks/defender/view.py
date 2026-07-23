#!/usr/bin/env python3
"""Print recent ArbiterOS defender hook events."""

from __future__ import annotations

import argparse
import json

from common import EVENTS_FILE, as_text, ensure_dirs


def main() -> int:
    parser = argparse.ArgumentParser(description="View defender hook event log")
    parser.add_argument("-n", type=int, default=20, help="Last N events (default 20)")
    args = parser.parse_args()

    ensure_dirs()
    if not EVENTS_FILE.exists():
        print(f"no events yet ({EVENTS_FILE})")
        return 0

    lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines()
    for line in lines[-max(args.n, 1) :]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            print(line)
            continue
        ts = data.get("ts")
        event = data.get("hook_event_name")
        decision = data.get("decision")
        reason = data.get("decision_reason")
        summary = data.get("summary")
        kernel = data.get("kernel_check")
        print("-" * 60)
        print(f"{ts}  {event}  decision={decision}  reason={reason}")
        print(f"  {summary}")
        if kernel:
            print(f"  kernel: {as_text(kernel, 500)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
