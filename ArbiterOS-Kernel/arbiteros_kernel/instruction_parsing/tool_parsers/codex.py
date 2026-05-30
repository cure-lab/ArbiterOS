"""
Per-tool instruction parsers for the codex toolset.

Strategy:
- Reuse OpenClaw parser semantics whenever possible.
- Normalize Codex-shaped arguments to OpenClaw-shaped arguments.
- Keep unmapped tools unregistered so they fall back to conservative EXEC/UNKNOWN.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..types import TaintStatus, ToolParser, ToolParseResult
from .openclaw import (
    _parse_exec as _parse_openclaw_exec,
    _parse_gateway as _parse_openclaw_gateway,
    _parse_image as _parse_openclaw_image,
    _parse_message as _parse_openclaw_message,
    _parse_process as _parse_openclaw_process,
    _parse_read as _parse_openclaw_read,
    _parse_sessions_spawn as _parse_openclaw_sessions_spawn,
    _parse_web_fetch as _parse_openclaw_web_fetch,
    _parse_web_search as _parse_openclaw_web_search,
    _parse_write as _parse_openclaw_write,
)


def _normalize_exec_args(args: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(args or {})
    if not isinstance(out.get("command"), str) or not out.get("command", "").strip():
        cmd = out.get("cmd")
        if isinstance(cmd, str) and cmd.strip():
            out["command"] = cmd
    return out


def _normalize_read_args(args: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(args or {})
    if not isinstance(out.get("path"), str) or not out.get("path", "").strip():
        for key in ("file_path", "target_directory", "target_notebook"):
            value = out.get(key)
            if isinstance(value, str) and value.strip():
                out["path"] = value
                break
    return out


def _normalize_write_args(args: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(args or {})
    if not isinstance(out.get("path"), str) or not out.get("path", "").strip():
        for key in ("file_path", "target_notebook"):
            value = out.get(key)
            if isinstance(value, str) and value.strip():
                out["path"] = value
                break
    return out


def _parse_exec(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return _parse_openclaw_exec(_normalize_exec_args(args), taint_status)


def _parse_process(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return _parse_openclaw_process(args, taint_status)


def _parse_read(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return _parse_openclaw_read(_normalize_read_args(args), taint_status)


def _parse_write(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return _parse_openclaw_write(_normalize_write_args(args), taint_status)


def _parse_gateway(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return _parse_openclaw_gateway(args, taint_status)


def _parse_message(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return _parse_openclaw_message(args, taint_status)


def _parse_delegate(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return _parse_openclaw_sessions_spawn(args, taint_status)


def _parse_web_search(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return _parse_openclaw_web_search(args, taint_status)


def _parse_web_fetch(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return _parse_openclaw_web_fetch(args, taint_status)


def _parse_image(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    normalized = dict(args or {})
    if not isinstance(normalized.get("image"), str):
        candidate = normalized.get("path")
        if isinstance(candidate, str) and candidate.strip():
            normalized["image"] = candidate
    return _parse_openclaw_image(normalized, taint_status)


CODEX_TOOL_PARSER_REGISTRY: Dict[str, ToolParser] = {
    # Terminal / command
    "exec": _parse_exec,
    "exec_command": _parse_exec,
    "shell": _parse_exec,
    "terminal": _parse_exec,
    "run_command": _parse_exec,
    "run_terminal_command": _parse_exec,
    # Filesystem read-ish
    "read": _parse_read,
    "read_file": _parse_read,
    "readfile": _parse_read,
    "glob": _parse_read,
    "rg": _parse_read,
    "list_dir": _parse_read,
    # Filesystem write-ish
    "write": _parse_write,
    "write_file": _parse_write,
    "edit_file": _parse_write,
    "delete_file": _parse_write,
    "add_file": _parse_write,
    "update_file": _parse_write,
    "edit_notebook": _parse_write,
    # MCP / gateway
    "gateway": _parse_gateway,
    "call_mcp_tool": _parse_gateway,
    "fetch_mcp_resource": _parse_gateway,
    "list_mcp_resources": _parse_gateway,
    # Web / image
    "web_search": _parse_web_search,
    "web_fetch": _parse_web_fetch,
    "image": _parse_image,
    "generate_image": _parse_image,
    # Interaction / delegation
    "message": _parse_message,
    "ask_question": _parse_message,
    "subagent": _parse_delegate,
    "process": _parse_process,
}

__all__ = ["CODEX_TOOL_PARSER_REGISTRY"]
