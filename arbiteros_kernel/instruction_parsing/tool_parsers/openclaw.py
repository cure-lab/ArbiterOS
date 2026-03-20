"""
Per-tool instruction parsers and TOOL_PARSER_REGISTRY for the Openclaw toolset.

Each tool maps to a parser function:
    (arguments: Dict[str, Any]) -> ToolParseResult

that returns the instruction_type, security_type, and rule_types for that call.

To add a new tool:
  1. Write a _parse_<tool>() function below.
  2. Register it in TOOL_PARSER_REGISTRY at the bottom.
"""

import logging
import os
from typing import Any, Dict, Optional

from ..helpers.shell import (
    _ITYPE_PRIORITY,
    classify_segment,
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


# ---------------------------------------------------------------------------
# File-system tools
# ---------------------------------------------------------------------------

# Workspace files that represent the agent's persistent identity and memory.
# Reading  → RETRIEVE (recalling from memory)
# Writing  → STORE   (persisting experience)
#
# Sources (OpenClaw workspace docs):
#   SOUL.md      — Persona, tone, and boundaries; loaded every session.
#   MEMORY.md    — Curated long-term memory; loaded in private sessions.
#   AGENTS.md    — Operating instructions and rules; loaded every session.
#   USER.md      — Who the user is; loaded every session (contains PII).
#   IDENTITY.md  — Agent name, vibe, and emoji; updated by bootstrap ritual.
_MEMORY_FILE_NAMES = {
    "SOUL.md",
    "MEMORY.md",
    "AGENTS.md",
    "USER.md",  # user profile — PII
    "IDENTITY.md",  # agent identity
}

# The memory/ subdirectory holds daily memory logs: memory/YYYY-MM-DD.md.
_MEMORY_DIR_NAME = "memory"


def _get_path_basename(args: Dict[str, Any]) -> str:
    raw = args.get("path") or args.get("file_path") or ""
    return os.path.basename(str(raw))


def _is_memory_file(args: Dict[str, Any]) -> bool:
    """Return True for workspace identity files and daily memory logs."""
    raw = str(args.get("path") or args.get("file_path") or "")
    basename = os.path.basename(raw)
    if basename in _MEMORY_FILE_NAMES:
        return True
    # Daily memory log: any .md file whose immediate parent dir is named "memory"
    parent = os.path.basename(os.path.dirname(raw))
    return parent == _MEMORY_DIR_NAME and basename.endswith(".md")


def _make_write_result(
    args: Dict[str, Any], taint_status: Optional[TaintStatus]
) -> ToolParseResult:
    """Shared body for edit and write when not targeting a memory file.

    Registers the file path in the user registry (using taint_status or an
    UNKNOWN/UNKNOWN fallback when no taint context is available) and resolves
    confidentiality/trustworthiness via linux_registry.
    """
    raw = str(args.get("path") or args.get("file_path") or "")
    paths = [raw] if raw else []
    if raw:
        taint = taint_status or TaintStatus(trustworthiness="UNKNOWN", confidentiality="UNKNOWN")
        register_file_taint(raw, taint.trustworthiness, taint.confidentiality)
    confidentiality = classify_confidentiality(paths) if paths else "UNKNOWN"
    trustworthiness = classify_trustworthiness(paths) if paths else "UNKNOWN"
    logger.debug(
        "_make_write_result: path=%r → confidentiality=%s trustworthiness=%s",
        raw, confidentiality, trustworthiness,
    )
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


def _parse_read(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """read: workspace identity/memory files → RETRIEVE; others → READ.

    Confidentiality and trustworthiness are resolved via linux_registry so
    that e.g. reading /etc/shadow yields HIGH conf and reading a Downloads
    file yields LOW trust, without hardcoding these facts here.
    """
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
    logger.debug(
        "_parse_read: path=%r → confidentiality=%s trustworthiness=%s",
        raw, confidentiality, trustworthiness,
    )
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


def _parse_edit(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """edit: workspace identity/memory files → STORE; others → WRITE.

    Confidentiality is resolved via linux_registry (what sensitive region
    are we touching?).  Trustworthiness reflects whether the destination
    path is in a controlled zone.
    """
    if _is_memory_file(args):
        return ToolParseResult(
            "STORE",
            make_security_type(
                confidentiality="HIGH",
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=True,  # file edits can be reverted (e.g. via git)
                authority="UNKNOWN",
            ),
        )
    return _make_write_result(args, taint_status)


def _parse_write(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """write: workspace identity/memory files → STORE; others → WRITE.

    Same registry-based resolution as _parse_edit; write is a full
    overwrite so reversible=True only when the path supports version control.
    We conservatively keep reversible=True to match edit semantics (git etc.).
    """
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
    return _make_write_result(args, taint_status)

# ---------------------------------------------------------------------------
# Process / shell execution
# ---------------------------------------------------------------------------


def _parse_exec(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """
    exec: classify the shell command using linux_registry.

    In addition to the folded instruction_type/security_type, expose the
    coarse split result in security_type.custom["exec_parse"] so upper-layer
    policy can inspect per-segment classification without re-parsing.
    """
    command = str(args.get("command", ""))

    if not command.strip():
        logger.error(
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
        logger.error(
            "_parse_exec: multi-line command string received; newlines are treated "
            "as command separators: %r",
            command,
        )

    # Split on shell operators at string level first (quote-aware)
    seg_strings, operators = split_pipeline(command)
    if not seg_strings:
        seg_strings = [command]

    # Instruction type = maximum priority across all segments
    itypes = [classify_segment(s) for s in seg_strings]
    itype = max(itypes, key=lambda t: _ITYPE_PRIORITY.get(t, 0))

    # Collect file-path tokens and write targets from all pipeline segments.
    # operators is passed so shell.py can resolve relative paths under cd context.
    path_tokens, write_targets = collect_exec_path_tokens(seg_strings, itypes, operators)

    if path_tokens:
        confidentiality = classify_confidentiality(path_tokens)
        trustworthiness = classify_trustworthiness(path_tokens)
        logger.debug(
            "_parse_exec: path_tokens=%r → confidentiality=%s trustworthiness=%s",
            path_tokens, confidentiality, trustworthiness,
        )
    else:
        confidentiality = "LOW"
        trustworthiness = "MID" if itype == "WRITE" else "HIGH"
        logger.debug(
            "_parse_exec: no path tokens → confidentiality=%s trustworthiness=%s"
            " (itype=%s fallback)",
            confidentiality, trustworthiness, itype,
        )

    for write_target in write_targets:
        register_file_taint(write_target, trustworthiness, confidentiality)

    reversible = itype == "READ"

    return ToolParseResult(
        itype,
        make_security_type(
            confidentiality=confidentiality,
            trustworthiness=trustworthiness,
            confidence="UNKNOWN",
            reversible=reversible,
            authority="UNKNOWN",
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

def _parse_process(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """process: list/log → READ; poll → WAIT; others → EXEC."""
    action = args.get("action", "")
    if action in {"list", "log"}:
        itype = "READ"
        sec = make_security_type(
            confidentiality="MID",
            trustworthiness="HIGH",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        )
    elif action == "poll":
        itype = "WAIT"
        sec = make_security_type(
            confidentiality="LOW",
            trustworthiness="HIGH",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        )
    else:
        itype = "EXEC"
        sec = make_security_type(
            confidentiality="MID",
            trustworthiness="MID",
            confidence="UNKNOWN",
            reversible=False,
            authority="UNKNOWN",
        )
    return ToolParseResult(itype, sec)


# ---------------------------------------------------------------------------
# Browser control
# ---------------------------------------------------------------------------

_BROWSER_READ_ACTIONS = {
    "status",
    "profiles",
    "tabs",
    "snapshot",
    "screenshot",
    "console",
    "pdf",
}
_BROWSER_ASK_ACTIONS = {"dialog"}


def _parse_browser(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """browser: READ (snapshot/status/…), ASK (dialog), EXEC (navigate/click/…)."""
    action = args.get("action", "")
    if action in _BROWSER_READ_ACTIONS:
        itype = "READ"
        sec = make_security_type(
            confidentiality="MID",  # page content may include personal/session data
            trustworthiness="LOW",  # web content is external and may contain injections
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        )
    elif action in _BROWSER_ASK_ACTIONS:
        itype = "ASK"
        sec = make_security_type(
            confidentiality="LOW",
            trustworthiness="LOW",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        )
    else:
        itype = "EXEC"
        sec = make_security_type(
            confidentiality="LOW",
            trustworthiness="LOW",  # clicking/navigating based on untrusted page state
            confidence="UNKNOWN",
            reversible=False,
            authority="UNKNOWN",
        )
    return ToolParseResult(itype, sec)


# ---------------------------------------------------------------------------
# Canvas (node UI)
# ---------------------------------------------------------------------------


def _parse_canvas(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """canvas: snapshot → READ; everything else → EXEC."""
    action = args.get("action", "")
    if action == "snapshot":
        return ToolParseResult(
            "READ",
            make_security_type(
                confidentiality="LOW",
                trustworthiness="HIGH",  # local UI state
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


# ---------------------------------------------------------------------------
# Remote node control
# ---------------------------------------------------------------------------

# READ actions with moderate confidentiality (device metadata)
_NODES_READ_ACTIONS_MID = {"status", "describe", "pending", "camera_list"}
# READ actions capturing private sensor data — higher confidentiality
_NODES_READ_ACTIONS_HIGH = {
    "camera_snap",
    "camera_clip",
    "screen_record",
    "location_get",
}


def _parse_nodes(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """nodes: READ (sense/status) vs EXEC (approve/run/invoke/notify).

    Camera, screen-recording, and location actions are READ but carry HIGH
    confidentiality because captured data is inherently privacy-sensitive.
    """
    action = args.get("action", "")
    if action in _NODES_READ_ACTIONS_MID:
        return ToolParseResult(
            "READ",
            make_security_type(
                confidentiality="MID",  # device metadata
                trustworthiness="MID",  # remote, partially trusted device
                confidence="UNKNOWN",
                reversible=True,
                authority="UNKNOWN",
            ),
        )
    if action in _NODES_READ_ACTIONS_HIGH:
        return ToolParseResult(
            "READ",
            make_security_type(
                confidentiality="HIGH",  # camera / screen / location data is private
                trustworthiness="MID",  # remote device, partially trusted
                confidence="UNKNOWN",
                reversible=True,
                authority="UNKNOWN",
            ),
        )
    return ToolParseResult(
        "EXEC",
        make_security_type(
            confidentiality="LOW",
            trustworthiness="MID",
            confidence="UNKNOWN",
            reversible=False,
            authority="UNKNOWN",
        ),
    )


# ---------------------------------------------------------------------------
# Cron (scheduled tasks)
# ---------------------------------------------------------------------------


def _parse_cron(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """cron: READ (status/list/runs), WRITE (add/update), EXEC (remove/run/wake)."""
    action = args.get("action", "")
    if action in {"status", "list", "runs"}:
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
    if action in {"add", "update"}:
        return ToolParseResult(
            "WRITE",
            make_security_type(
                confidentiality="LOW",
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=True,  # cron entries can be removed/restored
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


# ---------------------------------------------------------------------------
# Message channels
# ---------------------------------------------------------------------------


def _parse_message(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """message: edit → WRITE; send/broadcast/react/delete → EXEC."""
    action = args.get("action", "")
    if action == "edit":
        return ToolParseResult(
            "WRITE",
            make_security_type(
                confidentiality="MID",  # message content may be sensitive
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=True,  # edits can be reverted
                authority="UNKNOWN",
            ),
        )
    return ToolParseResult(
        "EXEC",
        make_security_type(
            confidentiality="LOW",
            trustworthiness="HIGH",
            confidence="UNKNOWN",
            reversible=False,  # sent messages cannot be unsent
            authority="UNKNOWN",
        ),
    )


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------


def _parse_tts(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """tts: audio output → EXEC."""
    return ToolParseResult(
        "EXEC",
        make_security_type(
            confidentiality="LOW",
            trustworthiness="HIGH",
            confidence="UNKNOWN",
            reversible=False,  # played audio cannot be unplayed
            authority="UNKNOWN",
        ),
    )


# ---------------------------------------------------------------------------
# Gateway management
# ---------------------------------------------------------------------------


def _parse_gateway(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """gateway: READ (config.get/schema), WRITE (config.apply/patch), EXEC (restart/update.run)."""
    action = args.get("action", "")
    if action in {"config.get", "config.schema"}:
        return ToolParseResult(
            "READ",
            make_security_type(
                confidentiality="MID",  # config may contain keys/secrets
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=True,
                authority="UNKNOWN",
            ),
        )
    if action in {"config.apply", "config.patch"}:
        return ToolParseResult(
            "WRITE",
            make_security_type(
                confidentiality="LOW",
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=True,  # config can be rolled back
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


# ---------------------------------------------------------------------------
# Agent / session management
# ---------------------------------------------------------------------------


def _parse_agents_list(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """agents_list: enumerate available agents → RETRIEVE."""
    return ToolParseResult(
        "RETRIEVE",
        make_security_type(
            confidentiality="LOW",
            trustworthiness="HIGH",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


def _parse_sessions_list(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """sessions_list: enumerate sessions → RETRIEVE."""
    return ToolParseResult(
        "RETRIEVE",
        make_security_type(
            confidentiality="LOW",
            trustworthiness="HIGH",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


def _parse_sessions_history(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """sessions_history: fetch conversation history → RETRIEVE."""
    return ToolParseResult(
        "RETRIEVE",
        make_security_type(
            confidentiality="HIGH",  # conversation history is highly sensitive
            trustworthiness="HIGH",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


def _make_delegate_result() -> ToolParseResult:
    """Shared result for cross-session DELEGATE operations (send/spawn).

    Both actions dispatch a task to another agent session that is only
    partially trusted, hence MID trustworthiness.
    """
    return ToolParseResult(
        "DELEGATE",
        make_security_type(
            confidentiality="MID",
            trustworthiness="MID",  # another agent session, partially trusted
            confidence="UNKNOWN",
            reversible=False,
            authority="UNKNOWN",
        ),
    )


def _parse_sessions_send(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """sessions_send: send message to another session → DELEGATE."""
    return _make_delegate_result()


def _parse_sessions_spawn(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """sessions_spawn: launch a sub-agent → DELEGATE."""
    return _make_delegate_result()


def _parse_session_status(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """session_status: query current session state → RETRIEVE."""
    return ToolParseResult(
        "RETRIEVE",
        make_security_type(
            confidentiality="LOW",
            trustworthiness="HIGH",
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


# ---------------------------------------------------------------------------
# Web access
# ---------------------------------------------------------------------------


def _make_external_read_result() -> ToolParseResult:
    """Shared result for external web reads (search/fetch).

    Both return READ with LOW trustworthiness because external content may
    contain prompt injections or malicious instructions.
    """
    return ToolParseResult(
        "READ",
        make_security_type(
            confidentiality="LOW",
            trustworthiness="LOW",  # external content may contain prompt injections
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


def _parse_web_search(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """web_search: external search → READ (untrusted results)."""
    return _make_external_read_result()


def _parse_web_fetch(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """web_fetch: fetch page content → READ."""
    return _make_external_read_result()


# ---------------------------------------------------------------------------
# Image perception
# ---------------------------------------------------------------------------


def _parse_image(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """image: image analysis (perception) → READ.

    Trustworthiness is resolved via file_trustworthiness.yaml — external
    URLs (http://, https://, …) are classified LOW there, local paths MID.
    """
    image_src = str(args.get("image", ""))
    trustworthiness = classify_trustworthiness([image_src]) if image_src else "MID"
    return ToolParseResult(
        "READ",
        make_security_type(
            confidentiality="MID",  # images may contain sensitive visual info
            trustworthiness=trustworthiness,
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


# ---------------------------------------------------------------------------
# Memory management tools
# ---------------------------------------------------------------------------


def _make_memory_retrieve_result() -> ToolParseResult:
    """Shared result for agent memory retrieval (search/get).

    Both operations read from the agent's own memory store, which is
    inherently trusted (HIGH) and may contain sensitive experience (MID conf).
    """
    return ToolParseResult(
        "RETRIEVE",
        make_security_type(
            confidentiality="MID",  # agent memory may contain sensitive experience
            trustworthiness="HIGH",  # agent's own memory
            confidence="UNKNOWN",
            reversible=True,
            authority="UNKNOWN",
        ),
    )


def _parse_memory_search(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """memory_search: semantic search over MEMORY.md → RETRIEVE."""
    return _make_memory_retrieve_result()


def _parse_memory_get(
    args: Dict[str, Any], taint_status: Optional[TaintStatus] = None
) -> ToolParseResult:
    """memory_get: read a memory fragment by path → RETRIEVE."""
    return _make_memory_retrieve_result()


# ---------------------------------------------------------------------------
# Registry and unified entry point
# ---------------------------------------------------------------------------

TOOL_PARSER_REGISTRY: Dict[str, ToolParser] = {
    "read": _parse_read,
    "edit": _parse_edit,
    "write": _parse_write,
    "exec": _parse_exec,
    "process": _parse_process,
    "browser": _parse_browser,
    "canvas": _parse_canvas,
    "nodes": _parse_nodes,
    "cron": _parse_cron,
    "message": _parse_message,
    "tts": _parse_tts,
    "gateway": _parse_gateway,
    "agents_list": _parse_agents_list,
    "sessions_list": _parse_sessions_list,
    "sessions_history": _parse_sessions_history,
    "sessions_send": _parse_sessions_send,
    "sessions_spawn": _parse_sessions_spawn,
    "session_status": _parse_session_status,
    "web_search": _parse_web_search,
    "web_fetch": _parse_web_fetch,
    "image": _parse_image,
    "memory_search": _parse_memory_search,
    "memory_get": _parse_memory_get,
}
