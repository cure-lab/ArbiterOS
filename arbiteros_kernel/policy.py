"""Policy checks for ArbiterOS Kernel - validate/modify responses before returning to agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

__all__ = ["PolicyCheckResult", "check_response_policy"]


@dataclass
class PolicyCheckResult:
    """Policy check result."""

    modified: bool
    """Whether the response was modified."""
    response: dict[str, Any]
    """The response to return (original or modified)."""
    error_type: Optional[str] = None
    """Error type string when modified; None when not modified."""


def check_response_policy(
    *,
    trace_id: str,
    instructions: list[dict[str, Any]],
    current_response: dict[str, Any],
    latest_instructions: list[dict[str, Any]] | None = None,
) -> PolicyCheckResult:
    """
    Policy check on post_call_success response before returning to agent.

    Input:
        trace_id: Trace ID.
        instructions: Full instruction history from {trace_id}.json. (include the latest_instructions)
        current_response: Current post_call_success response (after strip/transform).
        latest_instructions: Instructions from this response (content + tool_calls 等，current_response 里有的都有).

    Output:
        PolicyCheckResult: modified, response, error_type (when modified).
    """
    if latest_instructions is None:
        latest_instructions = []

    # Simple policy: no change
    return PolicyCheckResult(
        modified=False,
        response=current_response,
        error_type=None,
    )
