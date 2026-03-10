"""Tool parser registry package.

Each sub-module implements parsers for a specific toolset.
The default toolset is Openclaw.
"""

from .openclaw import TOOL_PARSER_REGISTRY, parse_tool_instruction

__all__ = ["TOOL_PARSER_REGISTRY", "parse_tool_instruction"]
