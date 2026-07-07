"""Base pre-call policy class for ArbiterOS Kernel."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arbiteros_kernel.precall_policy_check import PreCallPolicyCheckResult


class PreCallPolicy(ABC):
    """Base class for pre-call policy implementations."""

    @abstractmethod
    def check(
        self,
        instructions: list[dict[str, Any]],
        current_request: dict[str, Any],
        trace_id: str,
        **kwargs: Any,
    ) -> "PreCallPolicyCheckResult":
        """Inspect/modify the pre-call request payload."""
