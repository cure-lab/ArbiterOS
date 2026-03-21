from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from .allow_deny_policy import AllowDenyPolicy
from .efsm_gate_policy import EfsmGatePolicy
from .output_budget_policy import OutputBudgetPolicy
from .path_budget_policy import PathBudgetPolicy
from .rate_limit_policy import RateLimitPolicy
from .security_label_policy import SecurityLabelPolicy
from .taint_policy import TaintPolicy
from .exec_composite_policy import ExecCompositePolicy
from .delete_policy import DeletePolicy
if TYPE_CHECKING:
    from .policy import Policy


@dataclass(frozen=True)
class PolicyEntry:
    """Registered policy with name, description, and enabled flag."""

    policy: type["Policy"]
    description: str
    enabled: bool = True

    @property
    def name(self) -> str:
        return self.policy.__name__


# -------------------------
# Built-in policy class map
# -------------------------

POLICY_CLASS_MAP: dict[str, type["Policy"]] = {
    "PathBudgetPolicy": PathBudgetPolicy,
    "AllowDenyPolicy": AllowDenyPolicy,
    "EfsmGatePolicy": EfsmGatePolicy,
    "TaintPolicy": TaintPolicy,
    "RateLimitPolicy": RateLimitPolicy,
    "OutputBudgetPolicy": OutputBudgetPolicy,
    "SecurityLabelPolicy": SecurityLabelPolicy,
    "ExecCompositePolicy": ExecCompositePolicy,
    "DeletePolicy": DeletePolicy,
}


def _default_registry_data() -> list[dict[str, object]]:
    """
    Fallback registry when external file is missing or invalid.

    Users can override this via:
      1) ARBITEROS_POLICY_REGISTRY
      2) arbiteros_kernel/policy_registry.json
    """
    return [
        {
            "name": "PathBudgetPolicy",
            "enabled": True,
            "description": (
                "Enforces path allow/deny prefixes and input string length budget for tool calls."
            ),
        },
        {
            "name": "AllowDenyPolicy",
            "enabled": True,
            "description": (
                "Allows or denies tool calls and instruction types by allow/deny lists."
            ),
        },
        {
            "name": "EfsmGatePolicy",
            "enabled": True,
            "description": "Gates tool execution based on EFSM state and plan alignment.",
        },
        {
            "name": "TaintPolicy",
            "enabled": True,
            "description": (
                "Blocks high-risk tools when args contain untrusted snippets from prior tool output."
            ),
        },
        {
            "name": "RateLimitPolicy",
            "enabled": True,
            "description": "Limits consecutive repeated tool calls per config.",
        },
        {
            "name": "OutputBudgetPolicy",
            "enabled": True,
            "description": (
                "Truncates assistant content when it exceeds output_budget.max_chars."
            ),
        },
        {
            "name": "SecurityLabelPolicy",
            "enabled": True,
            "description": (
                "Gates tool calls and RESPOND by security_type (confidence, authority_label, etc.)."
            ),
        },
        {
            "name": "ExecCompositePolicy",
            "enabled": True,
            "description": "Handles multi-segment exec commands using Kernel coarse parse metadata.",
        },
        {
            "name": "DeletePolicy",
            "enabled": True,
            "description": "Blocks delete-like operations and leaves further continuation to kernel approval flow.",
        },
    ]


def _registry_config_path() -> Path:
    """
    Default registry file:
        arbiteros_kernel/policy_registry.json

    Optional override:
        ARBITEROS_POLICY_REGISTRY=/path/to/policy_registry.json
    """
    env_path = os.getenv("ARBITEROS_POLICY_REGISTRY", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()

    # defaults.py is under arbiteros_kernel/policy/
    # parent.parent => arbiteros_kernel/
    return (Path(__file__).resolve().parent.parent / "policy_registry.json").resolve()


def _load_registry_data_from_file() -> list[dict[str, object]]:
    path = _registry_config_path()
    if not path.exists():
        return _default_registry_data()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _default_registry_data()

    if not isinstance(raw, list):
        return _default_registry_data()

    cleaned: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        cleaned.append(
            {
                "name": name.strip(),
                "enabled": bool(item.get("enabled", True)),
                "description": str(item.get("description", "") or "").strip(),
            }
        )

    return cleaned or _default_registry_data()


def _build_registry_from_data(data: list[dict[str, object]]) -> list[PolicyEntry]:
    """
    Build PolicyEntry list from raw JSON data.

    Unknown policy names are ignored silently.
    Disabled policies remain visible in registry, but callers can filter them.
    """
    entries: list[PolicyEntry] = []

    for item in data:
        name = str(item.get("name", "")).strip()
        if not name:
            continue

        policy_cls = POLICY_CLASS_MAP.get(name)
        if policy_cls is None:
            # Unknown class name in JSON: ignore silently
            continue

        entries.append(
            PolicyEntry(
                policy=policy_cls,
                description=str(item.get("description", "") or "").strip(),
                enabled=bool(item.get("enabled", True)),
            )
        )

    return entries


# -------------------------
# Dynamic cache
# -------------------------

_POLICY_REGISTRY_LOCK = threading.Lock()
_POLICY_REGISTRY_CACHE_KEY: str | None = None
_POLICY_REGISTRY_CACHE: list[PolicyEntry] | None = None


def _registry_cache_key() -> str:
    """
    Build a cache key from registry path + file metadata.

    If file content changes on disk, mtime_ns / size should change,
    and the next getter call will trigger a reload.
    """
    path = _registry_config_path()
    if not path.exists():
        return f"missing::{str(path)}"

    try:
        stat = path.stat()
        return f"{str(path)}::{stat.st_mtime_ns}::{stat.st_size}"
    except Exception:
        return f"error::{str(path)}"


def _get_cached_registry(*, force_reload: bool = False) -> list[PolicyEntry]:
    """
    Return the current policy registry with lightweight auto-reload.

    Thread-safe.
    """
    global _POLICY_REGISTRY_CACHE_KEY, _POLICY_REGISTRY_CACHE

    key = _registry_cache_key()

    with _POLICY_REGISTRY_LOCK:
        if (
            not force_reload
            and _POLICY_REGISTRY_CACHE is not None
            and _POLICY_REGISTRY_CACHE_KEY == key
        ):
            return list(_POLICY_REGISTRY_CACHE)

        raw_data = _load_registry_data_from_file()
        registry = _build_registry_from_data(raw_data)

        _POLICY_REGISTRY_CACHE_KEY = key
        _POLICY_REGISTRY_CACHE = list(registry)
        return list(registry)


# -------------------------
# Public dynamic accessors
# -------------------------

def get_policy_registry(*, force_reload: bool = False) -> list[PolicyEntry]:
    """
    Return the current registry.

    Use this instead of POLICY_REGISTRY if you want runtime updates
    from policy_registry.json to take effect without restarting.
    """
    return _get_cached_registry(force_reload=force_reload)


def iter_policy_registry(*, force_reload: bool = False) -> Iterator[PolicyEntry]:
    """Iterate over the current registry (dynamic)."""
    yield from get_policy_registry(force_reload=force_reload)


def get_default_policy_classes(*, force_reload: bool = False) -> list[type["Policy"]]:
    """
    Return currently enabled policy classes.

    This should be used by policy_check.py so that editing
    policy_registry.json takes effect on the next request.
    """
    return [
        entry.policy
        for entry in get_policy_registry(force_reload=force_reload)
        if entry.enabled
    ]


def get_policy_descriptions(*, force_reload: bool = False) -> dict[str, str]:
    """Return current policy name -> description mapping."""
    return {
        entry.name: entry.description
        for entry in get_policy_registry(force_reload=force_reload)
    }


def get_policy_enabled(*, force_reload: bool = False) -> dict[str, bool]:
    """Return current policy name -> enabled mapping."""
    return {
        entry.name: entry.enabled
        for entry in get_policy_registry(force_reload=force_reload)
    }


# -------------------------
# Backward-compatible snapshots
# -------------------------
# These are module-import-time snapshots for legacy code.
# New code should prefer the dynamic getters above.

POLICY_REGISTRY: list[PolicyEntry] = get_policy_registry()
"""Import-time snapshot of the registry. Prefer get_policy_registry()."""

DEFAULT_POLICY_CLASSES: list[type["Policy"]] = get_default_policy_classes()
"""Import-time snapshot of enabled policies. Prefer get_default_policy_classes()."""

POLICY_DESCRIPTIONS: dict[str, str] = get_policy_descriptions()
"""Import-time snapshot of policy descriptions. Prefer get_policy_descriptions()."""

POLICY_ENABLED: dict[str, bool] = get_policy_enabled()
"""Import-time snapshot of enabled flags. Prefer get_policy_enabled()."""


__all__ = [
    "DEFAULT_POLICY_CLASSES",
    "POLICY_CLASS_MAP",
    "POLICY_DESCRIPTIONS",
    "POLICY_ENABLED",
    "POLICY_REGISTRY",
    "PolicyEntry",
    "get_default_policy_classes",
    "get_policy_descriptions",
    "get_policy_enabled",
    "get_policy_registry",
    "iter_policy_registry",
]