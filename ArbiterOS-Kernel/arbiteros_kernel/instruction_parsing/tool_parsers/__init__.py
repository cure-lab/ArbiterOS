"""Tool parser registry package — DSL-driven.

Registries are loaded from YAML definitions in the dsl/ subdirectory via the
DSL engine.  The three agent-specific registries are built at import time and
exposed for backwards-compatible access.

Agent selection: ``arbiteros_config.tool_agent`` in ``litellm_config.yaml``
(``openclaw`` | ``nanobot`` | ``hermes`` | ``codex``), or env ``ARBITEROS_TOOL_AGENT``. Default: openclaw.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from ..tool_agent_config import get_tool_agent
from ..types import TaintStatus, ToolParseResult, make_security_type
from .engine import load_registry

logger = logging.getLogger(__name__)

_DSL_DIR = Path(__file__).parent / "dsl"

TOOL_PARSER_REGISTRY = load_registry(_DSL_DIR / "openclaw.yaml")
NANOBOT_TOOL_PARSER_REGISTRY = load_registry(_DSL_DIR / "nanobot.yaml")
HERMES_TOOL_PARSER_REGISTRY = load_registry(_DSL_DIR / "hermes.yaml")

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
}


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
<<<<<<< HEAD
    if agent == "hermes":
        args = arguments or {}
        parser = HERMES_TOOL_PARSER_REGISTRY.get(tool_name)
        if not parser:
            logger.warning(
                "No hermes parser for tool %r; falling back to EXEC", tool_name
            )
            return ToolParseResult(
                "EXEC",
                make_security_type(
                    confidentiality="UNKNOWN",
                    trustworthiness="UNKNOWN",
                    confidence="UNKNOWN",
                    reversible=False,
                    authority="UNKNOWN",
                ),
            )
        result = parser(args, taint_status)
        logger.debug("Parsed (hermes) tool call %r(%r): %r", tool_name, args, result)
        return result

    if agent == "nanobot":
        args = arguments or {}
        parser = NANOBOT_TOOL_PARSER_REGISTRY.get(tool_name)
        if not parser:
            logger.warning(
                "No nanobot parser for tool %r; falling back to EXEC", tool_name
            )
            return ToolParseResult(
                "EXEC",
                make_security_type(
                    confidentiality="UNKNOWN",
                    trustworthiness="UNKNOWN",
                    confidence="UNKNOWN",
                    reversible=False,
                    authority="UNKNOWN",
                ),
            )
        result = parser(args, taint_status)
        logger.debug("Parsed (nanobot) tool call %r(%r): %r", tool_name, args, result)
        return result

    if agent == "codex":
        args = arguments or {}
        parser = CODEX_TOOL_PARSER_REGISTRY.get(tool_name) or CODEX_TOOL_PARSER_REGISTRY.get(
            tool_name.lower() if isinstance(tool_name, str) else ""
        )
        if not parser:
            logger.warning(
                "No codex parser for tool %r; falling back to EXEC", tool_name
            )
            return ToolParseResult(
                "EXEC",
                make_security_type(
                    confidentiality="UNKNOWN",
                    trustworthiness="UNKNOWN",
                    confidence="UNKNOWN",
                    reversible=False,
                    authority="UNKNOWN",
                ),
            )
        result = parser(args, taint_status)
        logger.debug("Parsed (codex) tool call %r(%r): %r", tool_name, args, result)
        return result

=======
    registry = _REGISTRIES.get(agent, TOOL_PARSER_REGISTRY)
>>>>>>> main
    args = arguments or {}
    parser = registry.get(tool_name)
    if not parser:
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
    "parse_tool_instruction",
]
