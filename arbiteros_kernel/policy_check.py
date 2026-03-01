"""Policy checks for ArbiterOS Kernel - validate/modify responses before returning to agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from arbiteros_kernel.policy import Policy

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
    policy_classes: Optional[list[type["Policy"]]] = None,
) -> PolicyCheckResult:
    """
    Policy check on post_call_success response before returning to agent.

    Input:
        trace_id: Trace ID.
        instructions: Full instruction history from {trace_id}.json. (include the latest_instructions)
        current_response: Current post_call_success response (after strip/transform).
        latest_instructions: Instructions from this response (content + tool_calls 等，current_response 里有的都有).
        policy_classes: List of policy classes to run. Defaults to all children policies in arbiteros_kernel/policy/.

    Output:
        PolicyCheckResult: modified, response, error_type (when modified).
    """
    if latest_instructions is None:
        latest_instructions = []

    if policy_classes is None:
        from arbiteros_kernel.policy.defaults import DEFAULT_POLICY_CLASSES
        policy_classes = DEFAULT_POLICY_CLASSES

    response = current_response
    errors: list[str] = []

    for policy_cls in policy_classes:
        policy = policy_cls()
        result = policy.check(
            instructions=instructions,
            current_response=response,
            latest_instructions=latest_instructions,
            trace_id=trace_id,
        )
        if result.modified:
            response = result.response
            if result.error_type:
                errors.append(result.error_type)

    return PolicyCheckResult(
        modified=len(errors) > 0,
        response=response,
        error_type="\n".join(errors) if errors else None,
    )
