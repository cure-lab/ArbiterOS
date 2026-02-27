"""Base policy class for ArbiterOS Kernel."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arbiteros_kernel.policy_check import PolicyCheckResult


class Policy(ABC):
    """Base class for policy implementations. Subclasses must implement check()."""

    @abstractmethod
    def check(
        self,
        instructions: list[dict[str, Any]],
        current_response: dict[str, Any],
        latest_instructions: list[dict[str, Any]],
        trace_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> "PolicyCheckResult":
        """
        Run the policy check. Override in subclasses with specific logic.
        Returns PolicyCheckResult with modified, response, and error_type.
        """
        ...
