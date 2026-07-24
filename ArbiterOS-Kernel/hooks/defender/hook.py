#!/usr/bin/env python3
"""
ArbiterOS defender PreToolUse gate.

1) Ask Kernel said/done matcher (file-backed, no LLM).
2) Auto-allow when action matches a Gateway-declared TOOLCALL on the same trace
   and execution details (command/args) match.
3) Otherwise escalate to sidecar watch (type 1 to allow) — including same
   tool_call_id with different command/args.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Optional

from common import (
    EVENTS_FILE,
    POLL_INTERVAL_SEC,
    PROMPT_TIMEOUT_SEC,
    as_text,
    cleanup_request,
    decision_path,
    ensure_dirs,
    ensure_kernel_on_path,
    new_request_id,
    now_iso,
    summarize_action,
    write_pending,
)


def _read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"_raw": data}
    except json.JSONDecodeError:
        return {"_raw_text": raw[:8000]}


def _append_event(event: dict[str, Any]) -> None:
    ensure_dirs()
    with EVENTS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        f.flush()


def _deny_json(reason: str) -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(out, ensure_ascii=False))


def _kernel_check(payload: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
    """Return (allowed, reason, details). Fail-closed → not allowed."""
    try:
        ensure_kernel_on_path()
        from arbiteros_kernel.execution_check import check_pretool_use

        result = check_pretool_use(payload)
        details = {
            "decision": result.decision,
            "reason": result.reason,
            "trace_id": result.trace_id,
            "matched_tool_call_id": result.matched_tool_call_id,
            "said_summary": result.said_summary,
            "done_summary": result.done_summary,
            "diff": result.diff,
        }
        return result.allowed, result.reason, details
    except Exception as exc:
        return False, f"kernel_check_error:{exc}", {"error": str(exc)}


def _wait_for_watch(request_id: str) -> tuple[bool, str]:
    deadline = None if PROMPT_TIMEOUT_SEC <= 0 else time.monotonic() + PROMPT_TIMEOUT_SEC

    while True:
        decision_file = decision_path(request_id)
        if decision_file.exists():
            try:
                data = json.loads(decision_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(POLL_INTERVAL_SEC)
                continue
            decision = str(data.get("decision") or "").lower()
            reason = str(data.get("reason") or decision)
            cleanup_request(request_id)
            if decision == "allow":
                return True, reason or "watch_allowed"
            return False, reason or "watch_denied"

        if deadline is not None and time.monotonic() >= deadline:
            cleanup_request(request_id)
            return (
                False,
                f"timeout after {PROMPT_TIMEOUT_SEC}s "
                "(attach trace in ArbiterOS TUI and answer Y/N, or run watch.py)",
            )

        time.sleep(POLL_INTERVAL_SEC)


def _request_watch_approval(
    payload: dict[str, Any],
    summary: str,
    escalate_reason: str,
    *,
    kernel_details: Optional[dict[str, Any]] = None,
) -> tuple[bool, str]:
    request_id = new_request_id()
    details = kernel_details if isinstance(kernel_details, dict) else {}
    request = {
        "request_id": request_id,
        "created_at": now_iso(),
        "hook_event_name": payload.get("hook_event_name"),
        "session_id": payload.get("session_id"),
        "turn_id": payload.get("turn_id"),
        "cwd": payload.get("cwd"),
        "model": payload.get("model"),
        "permission_mode": payload.get("permission_mode"),
        "tool_name": payload.get("tool_name"),
        "tool_use_id": payload.get("tool_use_id"),
        "tool_input": payload.get("tool_input"),
        "summary": summary,
        "escalate_reason": escalate_reason,
        "trace_id": details.get("trace_id"),
        "matched_tool_call_id": details.get("matched_tool_call_id"),
        "said_summary": details.get("said_summary"),
        "done_summary": details.get("done_summary"),
        "diff": details.get("diff"),
    }
    write_pending(request)

    # Primary UX: ArbiterOS TUI attach Y/N. watch.py remains as fallback.
    try:
        ensure_kernel_on_path()
        from arbiteros_kernel.tui_bridge import KIND_SAID_DONE, enqueue_confirm

        trace_id = details.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id.strip():
            session_id = payload.get("session_id")
            trace_id = (
                str(session_id).strip()
                if isinstance(session_id, str) and session_id.strip()
                else "unknown"
            )
        enqueue_confirm(
            trace_id=str(trace_id).strip(),
            error_type=str(escalate_reason or "said_done_mismatch"),
            policy_names=["said_done"],
            kind=KIND_SAID_DONE,
            request_id=request_id,
            extra={
                "summary": summary,
                "said_summary": details.get("said_summary"),
                "done_summary": details.get("done_summary"),
                "diff": details.get("diff"),
                "tool_name": payload.get("tool_name"),
                "tool_use_id": payload.get("tool_use_id"),
                "session_id": payload.get("session_id"),
            },
        )
    except Exception as exc:
        sys.stderr.write(f"arbiteros-defender tui enqueue failed: {exc}\n")

    return _wait_for_watch(request_id)


def main() -> int:
    payload = _read_stdin_json()
    event_name = str(payload.get("hook_event_name") or "Unknown")
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input")

    summary = summarize_action(tool_name, tool_input)
    record: dict[str, Any] = {
        "ts": now_iso(),
        "hook_event_name": event_name,
        "session_id": payload.get("session_id"),
        "turn_id": payload.get("turn_id"),
        "cwd": payload.get("cwd"),
        "model": payload.get("model"),
        "permission_mode": payload.get("permission_mode"),
        "tool_name": tool_name,
        "tool_use_id": payload.get("tool_use_id"),
        "tool_input": tool_input,
        "summary": summary,
    }

    if event_name == "PreToolUse":
        allowed, reason, details = _kernel_check(payload)
        record["kernel_check"] = details

        if allowed:
            record["decision"] = "allow"
            record["decision_reason"] = reason
            try:
                _append_event(record)
            except Exception as exc:
                sys.stderr.write(f"arbiteros-defender log failed: {exc}\n")
            return 0

        # Escalate unknown / mismatch / detail drift to human watch.
        allowed, reason = _request_watch_approval(
            payload, summary, reason, kernel_details=details
        )
        record["decision"] = "allow" if allowed else "deny"
        record["decision_reason"] = reason
        try:
            _append_event(record)
        except Exception as exc:
            sys.stderr.write(f"arbiteros-defender log failed: {exc}\n")

        if not allowed:
            # Deny via stdout JSON + exit 0. Exit 2 is a separate Codex path
            # (reason on stderr); mixing exit 2 with stdout JSON often shows as
            # hook "Failed" and Codex continues the tool anyway.
            _deny_json(record["decision_reason"])
            return 0
        # User allowed a previously unmatched / detail-mismatched action.
        matched_id = details.get("matched_tool_call_id")
        if isinstance(matched_id, str) and matched_id.strip():
            try:
                ensure_kernel_on_path()
                from arbiteros_kernel.execution_check import mark_consumed

                mark_consumed(matched_id)
            except Exception as exc:
                sys.stderr.write(f"arbiteros-defender consume failed: {exc}\n")
        return 0

    if event_name == "PostToolUse":
        record["tool_response_preview"] = as_text(payload.get("tool_response"), 2000)

    if event_name == "PermissionRequest":
        record["permission_request"] = tool_input

    try:
        _append_event(record)
    except Exception as exc:
        sys.stderr.write(f"arbiteros-defender log failed: {exc}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
