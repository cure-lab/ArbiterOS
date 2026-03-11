"""Base policy class for ArbiterOS Kernel."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from arbiteros_kernel.instruction_parsing.mock import TaintStatus
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
        *,
        current_taint_status: Optional["TaintStatus"] = None,
        **kwargs: Any,
    ) -> "PolicyCheckResult":
        """
        Run the policy check. Override in subclasses with specific logic.
        Returns PolicyCheckResult with modified, response, and error_type.

        current_taint_status: trustworthiness 和 confidentiality 在 instruction history 中的最小值。
        """
        ...
