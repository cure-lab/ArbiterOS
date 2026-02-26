"""Policy to protect tool calls from reading hard_code or .env paths."""

from __future__ import annotations

from typing import Any, Optional

from .policy import Policy

# Tool names whose path/file_path arguments are protected (e.g. read).
READ_PATH_PROTECTED_TOOL_NAMES: frozenset[str] = frozenset({"read"})

# Replacement arguments when a protected path is detected.
READ_TOOL_PROTECTED_ARGS: dict[str, str] = {"path": "[PROTECTED]", "file_path": "[PROTECTED]"}


def _is_read_path_protected_tool(tool_name: Any) -> bool:
    if not isinstance(tool_name, str):
        return False
    return tool_name.strip().lower() in READ_PATH_PROTECTED_TOOL_NAMES


def _read_tool_path_contains_hard_code(arguments: Any) -> bool:
    """Check if read tool path argument contains hard_code (protected path)."""
    if not isinstance(arguments, dict):
        return False
    path = arguments.get("path") or arguments.get("file_path")
    if not isinstance(path, str):
        return False
    return "hard_code" in path


def _read_tool_path_is_dotenv_file(arguments: Any) -> bool:
    """Check if read tool path points to .env file (filename is .env)."""
    if not isinstance(arguments, dict):
        return False
    path = arguments.get("path") or arguments.get("file_path")
    if not isinstance(path, str) or not path.strip():
        return False
    normalized = path.strip().replace("\\", "/").rstrip("/")
    filename = normalized.split("/")[-1] if normalized else ""
    return filename == ".env"


def _read_tool_path_is_protected(arguments: Any) -> bool:
    """Read path protection rule: contains hard_code or reads .env filename."""
    return _read_tool_path_contains_hard_code(arguments) or _read_tool_path_is_dotenv_file(arguments)


def _apply_instruction_tool_path_protection(instruction: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """
    If instruction is a read tool with protected path, replace arguments.
    Returns (instruction, error_message). Always returns the instruction (modified or not).
    """
    content = instruction.get("content")
    if not isinstance(content, dict):
        return instruction, ""

    tool_name = content.get("tool_name")
    if not _is_read_path_protected_tool(tool_name):
        return instruction, ""

    arguments = content.get("arguments")
    if not _read_tool_path_is_protected(arguments):
        return instruction, ""

    # Preserve other keys (e.g. limit, offset) when replacing path/file_path
    new_args = dict(arguments) if isinstance(arguments, dict) else {}
    new_args["path"] = READ_TOOL_PROTECTED_ARGS["path"]
    new_args["file_path"] = READ_TOOL_PROTECTED_ARGS["file_path"]
    content["arguments"] = new_args

    path_val = (arguments or {}).get("path") or (arguments or {}).get("file_path") or ""
    if _read_tool_path_contains_hard_code(arguments):
        err = f"Tool call blocked: read path contains hard_code ({path_val})"
    else:
        err = f"Tool call blocked: read path targets .env file ({path_val})"
    return instruction, err


class ToolPathProtectionPolicy(Policy):
    """
    Policy that checks the last instruction for read tool calls with hard_code or .env paths.
    If found, modifies the tool call arguments to a protected placeholder.
    """

    def check(
        self, instructions: list[dict[str, Any]], *args: Any, **kwargs: Any
    ) -> tuple[dict[str, Any] | None, str]:
        """
        Check the last instruction. If it is a read tool with protected path,
        modify it in place and return (instruction, error_message).
        Otherwise return (instruction, "").
        """
        if len(instructions) == 0:
            return None, ""
        last_instruction = instructions[-1]
        
        instruction, error_message = _apply_instruction_tool_path_protection(last_instruction)
        return instruction, error_message
