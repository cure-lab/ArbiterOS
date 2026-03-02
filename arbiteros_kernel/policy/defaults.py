from __future__ import annotations

from .policy import Policy
from .allow_deny_policy import AllowDenyPolicy
from .efsm_gate_policy import EfsmGatePolicy
from .path_budget_policy import PathBudgetPolicy
from .rate_limit_policy import RateLimitPolicy
from .output_budget_policy import OutputBudgetPolicy
from .security_label_policy import SecurityLabelPolicy
from .taint_policy import TaintPolicy
DEFAULT_POLICY_CLASSES: list[type[Policy]] = [
    PathBudgetPolicy,
    AllowDenyPolicy,
    EfsmGatePolicy,
    TaintPolicy,
    RateLimitPolicy,
    OutputBudgetPolicy,
    SecurityLabelPolicy,
]

__all__ = ["DEFAULT_POLICY_CLASSES"]