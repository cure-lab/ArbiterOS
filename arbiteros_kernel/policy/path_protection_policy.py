"""Policy to protect tool calls from reading hard_code or .env paths.

Expected response structure (checked and modified directly):
{
    "content": null,
    "role": "assistant",
    "tool_calls": [{
        "function": {"arguments": "{\"path\":\"...\"}", "name": "read"},
        "id": "call_xxx",
        "type": "function"
    }],
    "function_call": null,
    "provider_specific_fields": null
}
"""

from __future__ import annotations

import json
from typing import Any

from arbiteros_kernel.policy_check import PolicyCheckResult

from .policy import Policy

READ_TOOL_NAMES = frozenset({"read"})
PROTECTED_PLACEHOLDER = "[PROTECTED]"


def _get_path_from_args(arguments: Any) -> str | None:
    """Extract path or file_path from tool arguments."""
    if not isinstance(arguments, dict):
        return None
    path = arguments.get("path") or arguments.get("file_path")
    return path if isinstance(path, str) else None


def _is_protected_path(path: str | None) -> tuple[bool, str]:
    """
    Check if path is protected (hard_code or .env).
    Returns (is_protected, error_message). error_message is empty when not protected.
    """
    if not path or not path.strip():
        return False, ""
    if "hard_code" in path:
        return True, f"Tool call blocked: read path contains hard_code ({path})"
    normalized = path.strip().replace("\\", "/").rstrip("/")
    filename = normalized.split("/")[-1] if normalized else ""
    if filename == ".env":
        return True, f"Tool call blocked: read path targets .env file ({path})"
    return False, ""


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    """Parse tool arguments from JSON string or dict."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


class ToolPathProtectionPolicy(Policy):
    """
    Blocks read tool calls that target hard_code or .env paths.
    Checks and modifies current_response tool_calls directly.
    """

    def check(
        self,
        instructions: list[dict[str, Any]],
        current_response: dict[str, Any],
        latest_instructions: list[dict[str, Any]],
        trace_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> PolicyCheckResult:
        tool_calls = current_response.get("tool_calls")
        if not isinstance(tool_calls, list):
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        errors: list[str] = []
        modified_tool_calls: list[dict[str, Any]] = []

        for tc in tool_calls:
            if not isinstance(tc, dict):
                modified_tool_calls.append(tc)
                continue

            func = tc.get("function")
            if not isinstance(func, dict):
                modified_tool_calls.append(tc)
                continue

            tool_name = func.get("name")
            if not (isinstance(tool_name, str) and tool_name.strip().lower() in READ_TOOL_NAMES):
                modified_tool_calls.append(tc)
                continue

            arguments = _parse_tool_arguments(func.get("arguments"))
            path = _get_path_from_args(arguments)
            is_protected, error_message = _is_protected_path(path)
            if not is_protected:
                modified_tool_calls.append(tc)
                continue

            new_args = dict(arguments)
            new_args["path"] = new_args["file_path"] = PROTECTED_PLACEHOLDER
            tc_copy = dict(tc)
            func_copy = dict(func)
            func_copy["arguments"] = json.dumps(new_args)
            tc_copy["function"] = func_copy
            modified_tool_calls.append(tc_copy)
            errors.append(error_message)

        if not errors:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        response = dict(current_response)
        response["tool_calls"] = modified_tool_calls
        return PolicyCheckResult(
            modified=True,
            response=response,
            error_type="\n".join(errors),
        )
