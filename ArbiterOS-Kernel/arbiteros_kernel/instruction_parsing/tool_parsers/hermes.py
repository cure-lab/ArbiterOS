"""
Per-tool instruction parsers for the hermes toolset.

Only tools that can be mapped to OpenClaw semantics are registered here.
Hermes-only tools can remain unregistered and will fall back to EXEC.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..types import (
    TaintStatus,
    ToolParser,
    ToolParseResult,
    make_security_type,
)
from .openclaw import (
    _parse_exec,
    _parse_process,
    _parse_read,
    _parse_write,
)


def _parse_patch(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    # Hermes patch is the closest equivalent to OpenClaw edit/write semantics.
    return _parse_write(args, taint_status)


def _parse_terminal(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return _parse_exec(args, taint_status)


def _parse_cronjob(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    action = str(args.get("action") or "").lower()
    if action == "list":
        return ToolParseResult(
            "READ",
            make_security_type(
                confidentiality="LOW",
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=True,
                authority="UNKNOWN",
            ),
        )
    if action in {"create", "update"}:
        return ToolParseResult(
            "SUBSCRIBE",
            make_security_type(
                confidentiality="LOW",
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=True,
                authority="UNKNOWN",
            ),
        )
    return ToolParseResult(
        "EXEC",
        make_security_type(
            confidentiality="LOW",
            trustworthiness="HIGH",
            confidence="UNKNOWN",
            reversible=False,
            authority="UNKNOWN",
        ),
    )


def _parse_browser_read(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return ToolParseResult(
        "READ",
        make_security_type(
            confidentiality="UNKNOWN",
            trustworthiness="LOW",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


def _parse_browser_exec(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return ToolParseResult(
        "EXEC",
        make_security_type(
            confidentiality="LOW",
            trustworthiness="LOW",
            confidence="UNKNOWN",
            reversible=False,
            authority="UNKNOWN",
        ),
    )


def _parse_text_to_speech(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return ToolParseResult(
        "EXEC",
        make_security_type(
            confidentiality="LOW",
            trustworthiness="HIGH",
            confidence="UNKNOWN",
            reversible=False,
            authority="UNKNOWN",
        ),
    )


def _parse_session_search(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return ToolParseResult(
        "RETRIEVE",
        make_security_type(
            confidentiality="HIGH",
            trustworthiness="HIGH",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


def _parse_delegate_task(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return ToolParseResult(
        "DELEGATE",
        make_security_type(
            confidentiality="HIGH",
            trustworthiness="LOW",
            confidence="UNKNOWN",
            reversible=False,
            authority="UNKNOWN",
        ),
    )


def _parse_web_search(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return ToolParseResult(
        "READ",
        make_security_type(
            confidentiality="LOW",
            trustworthiness="LOW",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


def _parse_vision_analyze(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return ToolParseResult(
        "READ",
        make_security_type(
            confidentiality="HIGH",
            trustworthiness="LOW",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


HERMES_TOOL_PARSER_REGISTRY: Dict[str, ToolParser] = {
    "read_file": _parse_read,
    "write_file": _parse_write,
    "patch": _parse_patch,
    "terminal": _parse_terminal,
    "process": _parse_process,
    "cronjob": _parse_cronjob,
    "text_to_speech": _parse_text_to_speech,
    "session_search": _parse_session_search,
    "delegate_task": _parse_delegate_task,
    "web_search": _parse_web_search,
    "vision_analyze": _parse_vision_analyze,
    # OpenClaw browser(action=...) split across Hermes browser_* tools.
    "browser_navigate": _parse_browser_exec,
    "browser_click": _parse_browser_exec,
    "browser_type": _parse_browser_exec,
    "browser_press": _parse_browser_exec,
    "browser_scroll": _parse_browser_exec,
    "browser_back": _parse_browser_exec,
    "browser_snapshot": _parse_browser_read,
    "browser_console": _parse_browser_read,
    "browser_get_images": _parse_browser_read,
    "browser_vision": _parse_browser_read,
}

__all__ = ["HERMES_TOOL_PARSER_REGISTRY"]
