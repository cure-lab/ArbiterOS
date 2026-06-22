"""Tool parser registry package — DSL-driven.

Registries are loaded from YAML definitions in the dsl/ subdirectory via the
DSL engine.  The three agent-specific registries are built at import time and
exposed for backwards-compatible access.

Agent selection: ``arbiteros_config.tool_agent`` in ``litellm_config.yaml``
(``openclaw`` | ``nanobot`` | ``hermes`` | ``codex`` | ``claude_code``), or env ``ARBITEROS_TOOL_AGENT``. Default: openclaw.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from ..tool_agent_config import get_tool_agent
from ..types import TaintStatus, ToolParseResult, make_security_type
from .engine import load_registry
from arbiteros_kernel.mcp_tool_classification import (
    classify_mcp_tool_flow,
    is_mcp_tool_name,
    is_unknown_mcp_tool_allowlisted,
    unknown_mcp_allowlist_path,
)

logger = logging.getLogger(__name__)

_DSL_DIR = Path(__file__).parent / "dsl"

TOOL_PARSER_REGISTRY = load_registry(_DSL_DIR / "openclaw.yaml")
NANOBOT_TOOL_PARSER_REGISTRY = load_registry(_DSL_DIR / "nanobot.yaml")
HERMES_TOOL_PARSER_REGISTRY = load_registry(_DSL_DIR / "hermes.yaml")
CODEX_TOOL_PARSER_REGISTRY = load_registry(_DSL_DIR / "codex.yaml")
CLAUDE_CODE_TOOL_PARSER_REGISTRY = load_registry(_DSL_DIR / "claude_code.yaml")

_FALLBACK = ToolParseResult(
    "EXEC",
    make_security_type(
        confidentiality="UNKNOWN",
        trustworthiness="UNKNOWN",
        confidence="UNKNOWN",
        reversible=False,
        authority="UNKNOWN",
    ),
)

_REGISTRIES = {
    "openclaw": TOOL_PARSER_REGISTRY,
    "nanobot": NANOBOT_TOOL_PARSER_REGISTRY,
    "hermes": HERMES_TOOL_PARSER_REGISTRY,
    "codex": CODEX_TOOL_PARSER_REGISTRY,
    "claude_code": CLAUDE_CODE_TOOL_PARSER_REGISTRY,
}


_SECRET_READ_HINTS = (
    "verification",
    "verify",
    "code",
    "otp",
    "one-time",
    "one time",
    "password",
    "passcode",
    "token",
    "secret",
)


def _args_text_contains_secret_hint(arguments: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(arguments, dict):
        return False
    stack = list(arguments.values())
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
            continue
        if isinstance(value, list):
            stack.extend(value)
            continue
        if not isinstance(value, str):
            continue
        lowered = value.lower()
        if any(hint in lowered for hint in _SECRET_READ_HINTS):
            return True
    return False


def _parse_mcp_tool_fallback(
    tool_name: str, arguments: Optional[Dict[str, Any]] = None
) -> Optional[ToolParseResult]:
    flow = classify_mcp_tool_flow(tool_name)
    if flow == "read_sensitive":
        lower_name = (tool_name or "").strip().lower()
        source_trust = (
            "LOW"
            if lower_name.startswith(
                ("gmail__", "email__", "mail__", "slack__", "telegram__")
            )
            else "UNKNOWN"
        )
        data_labels = ["CUSTOMER_DATA"]
        if _args_text_contains_secret_hint(arguments):
            data_labels.append("SECRET")
        return ToolParseResult(
            "READ",
            make_security_type(
                confidentiality="HIGH",
                trustworthiness=source_trust,
                confidence="UNKNOWN",
                reversible=True,
                authority="UNKNOWN",
                custom={
                    "policy_metadata": {
                        "data_labels": data_labels,
                        "mcp_flow_kind": flow,
                    }
                },
            ),
        )
    if flow == "comm_sink":
        return ToolParseResult(
            "EXEC",
            make_security_type(
                confidentiality="LOW",
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=False,
                authority="UNKNOWN",
                risk="HIGH",
                custom={"policy_metadata": {"mcp_flow_kind": flow}},
            ),
        )
    if flow == "persist_side_effect":
        return ToolParseResult(
            "WRITE",
            make_security_type(
                confidentiality="LOW",
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=False,
                authority="UNKNOWN",
                risk="HIGH",
                custom={"policy_metadata": {"mcp_flow_kind": flow}},
            ),
        )
    if flow == "business_side_effect":
        return ToolParseResult(
            "WRITE",
            make_security_type(
                confidentiality="LOW",
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=True,
                authority="UNKNOWN",
                risk="LOW",
                custom={"policy_metadata": {"mcp_flow_kind": flow}},
            ),
        )
    if is_mcp_tool_name(tool_name):
        allowlisted = is_unknown_mcp_tool_allowlisted(tool_name)
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
                    "review_required": not allowlisted,
                    "policy_metadata": {
                        "mcp_flow_kind": "unknown_allowed" if allowlisted else "unknown",
                        "mcp_tool_supported": False,
                        "unknown_mcp_tool": not allowlisted,
                        "unknown_mcp_tool_name": tool_name,
                        "unknown_mcp_allowlisted": allowlisted,
                        "unknown_mcp_allowlist_file": str(unknown_mcp_allowlist_path()),
                    },
                },
            ),
        )
    return None


def parse_tool_instruction(
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    taint_status: Optional[TaintStatus] = None,
) -> ToolParseResult:
    """
    Look up the parser for tool_name and invoke it with arguments.

    Returns a ToolParseResult with all attributes set by the parser.
    Unregistered tools fall back to EXEC with all-UNKNOWN security_type.

    ``arguments`` are passed through unchanged (including ``reference_tool_id``);
    parsers ignore fields they do not need.
    """
    agent = get_tool_agent()
    registry = _REGISTRIES.get(agent, TOOL_PARSER_REGISTRY)
    args = arguments or {}
    tool_key = tool_name if isinstance(tool_name, str) else ""
    parser = registry.get(tool_key) or registry.get(tool_key.lower())
    if not parser:
        mcp_result = _parse_mcp_tool_fallback(tool_name, args)
        if mcp_result is not None:
            return mcp_result
        logger.warning(
            "No %s parser for tool %r; falling back to EXEC", agent, tool_name
        )
        return _FALLBACK
    result = parser(args, taint_status)
    logger.debug("Parsed (%s) tool call %r(%r): %r", agent, tool_name, args, result)
    return result


__all__ = [
    "TOOL_PARSER_REGISTRY",
    "NANOBOT_TOOL_PARSER_REGISTRY",
    "HERMES_TOOL_PARSER_REGISTRY",
    "CODEX_TOOL_PARSER_REGISTRY",
    "CLAUDE_CODE_TOOL_PARSER_REGISTRY",
    "parse_tool_instruction",
]
