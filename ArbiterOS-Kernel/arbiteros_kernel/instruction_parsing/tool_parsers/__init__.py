"""Tool parser registry package.

Each sub-module implements parsers for a specific toolset.
The default toolset is Openclaw.
"""

import logging
from typing import Any, Dict, Optional

from ..types import TaintStatus, ToolParseResult, make_security_type
from .openclaw import TOOL_PARSER_REGISTRY

logger = logging.getLogger(__name__)


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
    """
    args = arguments or {}
    parser = TOOL_PARSER_REGISTRY.get(tool_name)
    if not parser:
        logger.warning("No parser registered for tool %r; falling back to EXEC", tool_name)
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
    logger.debug("Parsed tool call %r(%r): %r", tool_name, args, result)
    return result


__all__ = ["TOOL_PARSER_REGISTRY", "parse_tool_instruction"]
