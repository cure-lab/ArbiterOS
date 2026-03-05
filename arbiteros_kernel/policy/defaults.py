"""Policy registry: default policy classes and their descriptions for Langfuse/metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

from .allow_deny_policy import AllowDenyPolicy
from .efsm_gate_policy import EfsmGatePolicy
from .output_budget_policy import OutputBudgetPolicy
from .path_budget_policy import PathBudgetPolicy
from .rate_limit_policy import RateLimitPolicy
from .security_label_policy import SecurityLabelPolicy
from .taint_policy import TaintPolicy

if TYPE_CHECKING:
    from .policy import Policy


@dataclass(frozen=True)
class PolicyEntry:
    """Registered policy with name and one-sentence description."""

    policy: type["Policy"]
    description: str

    @property
    def name(self) -> str:
        return self.policy.__name__


def _registry() -> list[PolicyEntry]:
    return [
        PolicyEntry(
            PathBudgetPolicy,
            "Enforces path allow/deny prefixes and input string length budget for tool calls.",
        ),
        PolicyEntry(
            AllowDenyPolicy,
            "Allows or denies tool calls and instruction types by allow/deny lists.",
        ),
        PolicyEntry(
            EfsmGatePolicy,
            "Gates tool execution based on EFSM state and plan alignment.",
        ),
        PolicyEntry(
            TaintPolicy,
            "Blocks high-risk tools when args contain untrusted snippets from prior tool output.",
        ),
        PolicyEntry(
            RateLimitPolicy,
            "Limits consecutive repeated tool calls per config.",
        ),
        PolicyEntry(
            OutputBudgetPolicy,
            "Truncates assistant content when it exceeds output_budget.max_chars.",
        ),
        PolicyEntry(
            SecurityLabelPolicy,
            "Gates tool calls and RESPOND by security_type (confidence, authority_label, etc.).",
        ),
    ]


POLICY_REGISTRY: list[PolicyEntry] = _registry()
"""Registry of policies with name and description. Traverse for pre-generation or metadata."""

DEFAULT_POLICY_CLASSES: list[type["Policy"]] = [e.policy for e in POLICY_REGISTRY]
"""Policy classes used by check_response_policy when policy_classes is None."""

POLICY_DESCRIPTIONS: dict[str, str] = {e.name: e.description for e in POLICY_REGISTRY}
"""Map policy name -> description for lookup when emitting to Langfuse."""


def iter_policy_registry() -> Iterator[PolicyEntry]:
    """Iterate over all registered policies (name + description)."""
    yield from POLICY_REGISTRY


__all__ = [
    "DEFAULT_POLICY_CLASSES",
    "POLICY_DESCRIPTIONS",
    "POLICY_REGISTRY",
    "PolicyEntry",
    "iter_policy_registry",
]
