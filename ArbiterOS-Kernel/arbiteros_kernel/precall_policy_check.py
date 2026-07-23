"""Pre-call policy checks for ArbiterOS Kernel - validate/modify requests before upstream dispatch.

Symmetric counterpart to :mod:`arbiteros_kernel.policy_check` (post-call).

Concrete policy classes live under ``arbiteros_kernel/precall_policy/`` (parallel
to ``arbiteros_kernel/policy/``).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "PreCallPolicyCheckResult",
    "check_precall_policy",
]


@dataclass
class PreCallPolicyCheckResult:
    """Pre-call policy check result."""

    modified: bool
    """Whether the request was modified."""

    request: dict[str, Any]
    """The request to forward upstream (original or modified)."""

    policy_names: list[str] = field(default_factory=list)
    """Names of pre-call policies that modified the request."""

    policy_sources: dict[str, str] = field(default_factory=dict)
    """Map policy_name -> source location."""


def check_precall_policy(
    *,
    trace_id: str,
    current_request: dict[str, Any],
    instructions: list[dict[str, Any]] | None = None,
    policy_classes: Optional[list[type[Any]]] = None,
    **kwargs: Any,
) -> PreCallPolicyCheckResult:
    """
    Run pre-call policies on the final request payload before logging / upstream.

    Symmetric to :func:`arbiteros_kernel.policy_check.check_response_policy`.

    Input:
        trace_id: Active trace id for this call.
        current_request: Final pre-call payload after kernel ref injection, hints, etc.
        instructions: Optional instruction history for this trace.
        policy_classes: When set, run exactly these pre-call policy classes. When
            ``None``, load defaults from ``precall_policy.defaults`` (may be empty).
        **kwargs: Forwarded to each policy (e.g. ``tool_agent`` from litellm config).

    Output:
        PreCallPolicyCheckResult with ``request`` to forward and ``modified`` flag.
    """
    if not isinstance(current_request, dict):
        return PreCallPolicyCheckResult(
            modified=False,
            request=current_request if isinstance(current_request, dict) else {},
        )

    request = copy.deepcopy(current_request)

    if policy_classes is None:
        from arbiteros_kernel.precall_policy.defaults import get_precall_policy_classes

        policy_classes = get_precall_policy_classes(
            tool_agent=kwargs.get("tool_agent")
        )

    if not policy_classes:
        return PreCallPolicyCheckResult(modified=False, request=request)

    policy_names: list[str] = []
    policy_sources: dict[str, str] = {}
    modified = False

    for policy_cls in policy_classes:
        policy = policy_cls()
        result = policy.check(
            instructions=instructions or [],
            current_request=request,
            trace_id=trace_id,
            **kwargs,
        )
        if result.modified:
            request = result.request
            modified = True
            name = policy_cls.__name__
            if name not in policy_sources:
                policy_names.append(name)
                policy_sources[name] = _precall_policy_source_location(policy_cls)

    return PreCallPolicyCheckResult(
        modified=modified,
        request=request,
        policy_names=policy_names,
        policy_sources=policy_sources,
    )


def _precall_policy_source_location(policy_cls: type[Any]) -> str:
    try:
        path = policy_cls.__module__
        return path
    except Exception:
        return policy_cls.__name__
