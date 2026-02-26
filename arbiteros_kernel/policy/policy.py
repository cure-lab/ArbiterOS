"""Base policy class for ArbiterOS Kernel."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arbiteros_kernel.instruction_parsing.instruction_parser import Instruction


class Policy(ABC):
    """Base class for policy implementations. Subclasses must implement check()."""

    @abstractmethod
    def check(self, instructions: list[dict[str, Any]], *args: Any, **kwargs: Any) -> tuple[dict[str, Any] | None, str]:
        """
        Run the policy check. Override in subclasses with specific logic.
        Returns (last_instruction, error_message) where:
        - last_instruction: the instruction on the last step (modified or unmodified)
        - error_message: non-empty string if modification was made, empty otherwise
        """
        ...
