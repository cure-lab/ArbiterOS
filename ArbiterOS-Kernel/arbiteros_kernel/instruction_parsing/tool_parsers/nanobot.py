"""
Per-tool instruction parsers for the nanobot toolset.

Semantics mirror OpenClaw equivalents (see docs/nanobot-tool-parsers-spec.md) but
implementations are independent — do not import parser callables from ``openclaw``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from ..helpers.shell import (
    _ITYPE_PRIORITY,
    classify_segment,
    classify_segment_risk,
    collect_exec_path_tokens,
    split_pipeline,
)
from ..types import (
    TaintStatus,
    ToolParser,
    ToolParseResult,
    make_security_type,
)
from .linux_registry import (
    classify_confidentiality,
    classify_trustworthiness,
    register_file_taint,
)

logger = logging.getLogger(__name__)

# --- Workspace memory files (aligned with openclaw.py) ---------------------------------

_MEMORY_FILE_NAMES = {
    "SOUL.md",
    "MEMORY.md",
    "AGENTS.md",
    "USER.md",
    "IDENTITY.md",
}
_MEMORY_DIR_NAME = "memory"


def _is_memory_file(args: Dict[str, Any]) -> bool:
    raw = str(args.get("path") or args.get("file_path") or "")
    basename = os.path.basename(raw)
    if basename in _MEMORY_FILE_NAMES:
        return True
    parent = os.path.basename(os.path.dirname(raw))
    return parent == _MEMORY_DIR_NAME and basename.endswith(".md")


def _make_write_result(args: Dict[str, Any]) -> ToolParseResult:
    raw = str(args.get("path") or args.get("file_path") or "")
    paths = [raw] if raw else []
    confidentiality = classify_confidentiality(paths) if paths else "UNKNOWN"
    trustworthiness = classify_trustworthiness(paths) if paths else "UNKNOWN"
    if raw:
        register_file_taint(raw, trustworthiness, confidentiality)
    return ToolParseResult(
        "WRITE",
        make_security_type(
            confidentiality=confidentiality,
            trustworthiness=trustworthiness,
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


def _parse_read_file(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """nanobot read_file → same classes as OpenClaw ``read``."""
    if _is_memory_file(args):
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
    raw = str(args.get("path") or args.get("file_path") or "")
    paths = [raw] if raw else []
    confidentiality = classify_confidentiality(paths) if paths else "UNKNOWN"
    trustworthiness = classify_trustworthiness(paths) if paths else "UNKNOWN"
    return ToolParseResult(
        "READ",
        make_security_type(
            confidentiality=confidentiality,
            trustworthiness=trustworthiness,
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


def _parse_list_dir(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """nanobot-only; path classification aligned with non-memory ``read``."""
    if _is_memory_file(args):
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
    raw = str(args.get("path") or "")
    paths = [raw] if raw else []
    confidentiality = classify_confidentiality(paths) if paths else "UNKNOWN"
    trustworthiness = classify_trustworthiness(paths) if paths else "UNKNOWN"
    return ToolParseResult(
        "READ",
        make_security_type(
            confidentiality=confidentiality,
            trustworthiness=trustworthiness,
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


def _parse_write_file(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """nanobot write_file → OpenClaw ``write``."""
    if _is_memory_file(args):
        return ToolParseResult(
            "STORE",
            make_security_type(
                confidentiality="HIGH",
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=True,
                authority="UNKNOWN",
            ),
        )
    return _make_write_result(args)


def _parse_edit_file(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """nanobot edit_file → OpenClaw ``edit`` (args use old_text/new_text)."""
    if _is_memory_file(args):
        return ToolParseResult(
            "STORE",
            make_security_type(
                confidentiality="HIGH",
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=True,
                authority="UNKNOWN",
            ),
        )
    return _make_write_result(args)


def _parse_exec(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """nanobot exec — shell classification aligned with OpenClaw ``exec`` (standalone copy)."""
    command = str(args.get("command", ""))

    if not command.strip():
        logger.warning(
            "Empty command string in exec; defaulting to EXEC with UNKNOWN security"
        )
        return ToolParseResult(
            "EXEC",
            make_security_type(
                confidentiality="UNKNOWN",
                trustworthiness="UNKNOWN",
                confidence="UNKNOWN",
                reversible=False,
                authority="UNKNOWN",
                risk="UNKNOWN",
                custom={
                    "exec_parse": {
                        "command": command,
                        "segments": [],
                        "operators": [],
                        "segment_instruction_types": [],
                        "path_tokens": [],
                        "write_targets": [],
                        "parser_kind": "coarse_shell_split",
                        "parse_error": "empty_command",
                    }
                },
            ),
        )

    if "\n" in command:
        logger.warning(
            "_parse_exec: multi-line command string received; newlines are treated "
            "as command separators: %r",
            command,
        )

    seg_strings, operators = split_pipeline(command)
    if not seg_strings:
        seg_strings = [command]

    itypes = [classify_segment(s) for s in seg_strings]
    itype = max(itypes, key=lambda t: _ITYPE_PRIORITY.get(t, 0))

    risks = [classify_segment_risk(s) for s in seg_strings]
    risk: str = "HIGH" if "HIGH" in risks else "UNKNOWN" if "UNKNOWN" in risks else "LOW"
    logger.debug(
        "_parse_exec: segment_risks=%r → risk=%s",
        risks,
        risk,
    )

    path_tokens, write_targets = collect_exec_path_tokens(seg_strings, itypes, operators)

    if path_tokens:
        confidentiality = classify_confidentiality(path_tokens)
        trustworthiness = classify_trustworthiness(path_tokens)
        logger.debug(
            "_parse_exec: path_tokens=%r → confidentiality=%s trustworthiness=%s",
            path_tokens,
            confidentiality,
            trustworthiness,
        )
    else:
        confidentiality = "LOW"
        trustworthiness = "HIGH"
        logger.debug(
            "_parse_exec: no path tokens → confidentiality=%s trustworthiness=%s"
            " (itype=%s fallback)",
            confidentiality,
            trustworthiness,
            itype,
        )

    for write_target in write_targets:
        register_file_taint(write_target, trustworthiness, confidentiality)

    reversible = itype != "EXEC"

    return ToolParseResult(
        itype,
        make_security_type(
            confidentiality=confidentiality,
            trustworthiness=trustworthiness,
            confidence="UNKNOWN",
            reversible=reversible,
            authority="UNKNOWN",
            risk=risk,
            custom={
                "exec_parse": {
                    "command": command,
                    "segments": seg_strings,
                    "operators": operators,
                    "segment_instruction_types": itypes,
                    "path_tokens": path_tokens,
                    "write_targets": write_targets,
                    "parser_kind": "coarse_shell_split",
                }
            },
        ),
    )


def _make_external_read_result() -> ToolParseResult:
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


def _parse_web_search(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return _make_external_read_result()


def _parse_web_fetch(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    return _make_external_read_result()


def _parse_message(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """nanobot message has no ``action``; default to outbound send → EXEC (OpenClaw non-edit)."""
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


def _parse_spawn(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """nanobot spawn → OpenClaw ``sessions_spawn`` (DELEGATE)."""
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


NANOBOT_TOOL_PARSER_REGISTRY: Dict[str, ToolParser] = {
    "read_file": _parse_read_file,
    "write_file": _parse_write_file,
    "edit_file": _parse_edit_file,
    "list_dir": _parse_list_dir,
    "exec": _parse_exec,
    "web_search": _parse_web_search,
    "web_fetch": _parse_web_fetch,
    "message": _parse_message,
    "spawn": _parse_spawn,
}

__all__ = ["NANOBOT_TOOL_PARSER_REGISTRY"]
