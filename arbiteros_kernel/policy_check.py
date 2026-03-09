"""Policy checks for ArbiterOS Kernel - validate/modify responses before returning to agent."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
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

    policy_names: list[str] = field(default_factory=list)
    """Names of policies that modified the response (e.g. ['PathBudgetPolicy'])."""

    policy_sources: dict[str, str] = field(default_factory=dict)
    """Map policy_name -> source location (e.g. 'path/to/policy.py:66')."""


def _policy_source_location(policy_cls: type) -> str:
    """Return 'filepath:lineno: source_line' for the policy's check method."""
    try:
        check_method = getattr(policy_cls, "check", None)
        if check_method is not None:
            path = inspect.getfile(check_method)
            try:
                lines, start = inspect.getsourcelines(check_method)
                # Find the return PolicyCheckResult(modified=True,...) block
                source_line = ""
                lineno = start
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    if "PolicyCheckResult" in stripped and "modified=True" in stripped:
                        source_line = stripped
                        lineno = start + i
                        break

                    # Multi-line return: "return PolicyCheckResult(" without ")" on same line
                    if "return PolicyCheckResult(" in stripped and ")" not in stripped:
                        block = [stripped]
                        has_modified = False
                        for j in range(i + 1, min(i + 8, len(lines))):
                            block.append(lines[j].rstrip())
                            if "modified=True" in lines[j]:
                                has_modified = True
                            if ")" in lines[j] and has_modified:
                                source_line = " ".join(block).replace("  ", " ").strip()
                                lineno = start + i
                                break
                        if source_line:
                            break

                if not source_line and lines:
                    source_line = lines[0].strip()

                if source_line:
                    return f"{path}:{lineno}: {source_line}"
                return f"{path}:{start}"
            except (TypeError, OSError):
                return path

        return inspect.getfile(policy_cls)
    except (TypeError, OSError):
        return f"{policy_cls.__module__}.{policy_cls.__name__}"


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
        policy_classes: List of policy classes to run. If None, load enabled policies dynamically
                        from arbiteros_kernel.policy.defaults.get_default_policy_classes().

    Output:
        PolicyCheckResult: modified, response, error_type (when modified).
    """
    if latest_instructions is None:
        latest_instructions = []

    if policy_classes is None:
        # Dynamic lookup so policy_registry.json changes can take effect
        # without restarting the process.
        from arbiteros_kernel.policy.defaults import get_default_policy_classes

        policy_classes = list(get_default_policy_classes())

    response = current_response
    errors: list[str] = []
    policy_names: list[str] = []
    policy_sources: dict[str, str] = {}

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

            name = policy_cls.__name__
            if name not in policy_sources:
                policy_names.append(name)
                policy_sources[name] = _policy_source_location(policy_cls)

    return PolicyCheckResult(
        modified=len(errors) > 0,
        response=response,
        error_type="\n".join(errors) if errors else None,
        policy_names=policy_names,
        policy_sources=policy_sources,
    )