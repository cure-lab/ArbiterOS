"""Built-in pre-call policy registry (minimal; no JSON file yet)."""

from __future__ import annotations

from typing import Optional

from arbiteros_kernel.precall_policy.policy import PreCallPolicy

PRECALL_POLICY_CLASS_MAP: dict[str, type[PreCallPolicy]] = {}


def get_precall_policy_classes(*, tool_agent: Optional[str] = None) -> list[type[PreCallPolicy]]:
    """Return enabled pre-call policy classes for the active tool agent."""
    _ = tool_agent
    return []
