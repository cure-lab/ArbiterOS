"""Policy checks for ArbiterOS Kernel - validate/modify responses before returning to agent."""

from __future__ import annotations

import copy
import inspect
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from arbiteros_kernel.policy import Policy

__all__ = [
    "PolicyCheckResult",
    "check_response_policy",
    "apply_policy_enforcement_mode",
    "resolve_role_policy_enabled_override",
    "split_model_and_role",
]

_ROLE_POLICY_SETS_PATH = (
    Path(__file__).resolve().parent / "role_policy_sets.json"
)
_ROLE_POLICY_SETS_LOCK = threading.Lock()
_ROLE_POLICY_SETS_CACHE_MTIME_NS: Optional[int] = None
_ROLE_POLICY_SETS_CACHE: list[dict[str, Any]] = []


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

    inactivate_error_type: Optional[str] = None
    """Inactive error type string; None when not applicable."""


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


def apply_policy_enforcement_mode(
    enforce: bool,
    response_snapshot: dict[str, Any],
    result: PolicyCheckResult,
) -> PolicyCheckResult:
    """
    When a registry entry has ``enabled: false`` (observe-only), policies still
    run their full ``check()`` logic. If the policy would have modified the
    response (``modified=True``), restore the pre-policy snapshot and move the
    would-be ``error_type`` text into ``inactivate_error_type`` instead.
    """
    if enforce:
        return result
    if not result.modified:
        return result
    msg = (result.error_type or "").strip() or "policy would have modified the response"
    return PolicyCheckResult(
        modified=False,
        response=copy.deepcopy(response_snapshot),
        error_type=None,
        inactivate_error_type=msg,
    )


def split_model_and_role(model_value: Any) -> tuple[Optional[str], Optional[str]]:
    """
    Parse ``model;role`` from request model field.

    Returns ``(base_model, role_name)``.
    If no valid role suffix exists, role_name is None and base_model keeps legacy behavior.
    """
    if not isinstance(model_value, str):
        return None, None
    raw = model_value.strip()
    if not raw:
        return None, None
    if ";" not in raw:
        return raw, None

    left, right = raw.split(";", 1)
    base_model = left.strip()
    role_name = right.strip()
    if not base_model:
        base_model = raw
    if not role_name:
        return base_model, None
    return base_model, role_name


def _load_role_policy_sets() -> list[dict[str, Any]]:
    if not _ROLE_POLICY_SETS_PATH.exists():
        return []
    try:
        mtime_ns = _ROLE_POLICY_SETS_PATH.stat().st_mtime_ns
    except Exception:
        return []

    with _ROLE_POLICY_SETS_LOCK:
        global _ROLE_POLICY_SETS_CACHE_MTIME_NS, _ROLE_POLICY_SETS_CACHE
        if _ROLE_POLICY_SETS_CACHE_MTIME_NS == mtime_ns:
            return list(_ROLE_POLICY_SETS_CACHE)

        try:
            parsed = json.loads(_ROLE_POLICY_SETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            parsed = []
        if isinstance(parsed, dict):
            parsed = parsed.get("roles", [])
        if not isinstance(parsed, list):
            parsed = []
        normalized = [x for x in parsed if isinstance(x, dict)]
        _ROLE_POLICY_SETS_CACHE_MTIME_NS = mtime_ns
        _ROLE_POLICY_SETS_CACHE = list(normalized)
        return list(normalized)


def resolve_role_policy_enabled_override(
    role_name: Optional[str],
) -> tuple[Optional[dict[str, bool]], Optional[str]]:
    """
    Resolve per-request policy enabled overrides for a role.

    Returns:
      - override map (policy_name -> enforce bool), or None when fallback to defaults
      - warning reason string when fallback happened
    """
    normalized_role = role_name.strip() if isinstance(role_name, str) else ""
    if not normalized_role:
        return None, None

    from arbiteros_kernel.policy.defaults import get_policy_registry

    registry_entries = list(get_policy_registry(force_reload=False))
    known_policy_names = {
        entry.policy.__name__ for entry in registry_entries if hasattr(entry, "policy")
    }
    role_defs = _load_role_policy_sets()
    matched: Optional[dict[str, Any]] = None
    for row in role_defs:
        if str(row.get("name") or "").strip() == normalized_role:
            matched = row
            break

    if not isinstance(matched, dict):
        return None, f"role_not_found:{normalized_role}"

    enabled_raw = matched.get("enabled_policies")
    if not isinstance(enabled_raw, list):
        return None, f"invalid_enabled_policies:{normalized_role}"

    enabled_set = {
        str(item).strip()
        for item in enabled_raw
        if isinstance(item, str) and str(item).strip()
    }
    unknown_policies = sorted(enabled_set - known_policy_names)
    if unknown_policies:
        return None, (
            f"unknown_policies:{normalized_role}:"
            + ",".join(unknown_policies)
        )

    override = {name: (name in enabled_set) for name in known_policy_names}
    return override, None


def check_response_policy(
    *,
    trace_id: str,
    instructions: list[dict[str, Any]],
    current_response: dict[str, Any],
    latest_instructions: list[dict[str, Any]] | None = None,
    policy_classes: Optional[list[type["Policy"]]] = None,
    user_messages: list[str] | None = None,
    policy_enabled_override: Optional[dict[str, bool]] = None,
) -> PolicyCheckResult:
    """
    Policy check on post_call_success response before returning to agent.

    Input:
        trace_id: Trace ID.
        instructions: Full instruction history from {trace_id}.json. (include the latest_instructions)
        current_response: Current post_call_success response (after strip/transform).
        latest_instructions: Instructions from this response (content + tool_calls 等，current_response 里有的都有).
        policy_classes: If set, run exactly these classes as if registry
            ``enabled: true``. If None, load **all** entries from
            ``policy_registry.json`` via ``get_policy_registry()``; each entry's
            ``enabled`` controls observe-only vs enforce **outside** policies via
            :func:`apply_policy_enforcement_mode` (no kwargs passed into
            ``Policy.check``).
        user_messages: Optional full user-message history from current precall payload.
            Passed through to policy.check via kwargs for policies that need it.

    Output:
        PolicyCheckResult: modified, response, error_type (when modified).
    """
    if latest_instructions is None:
        latest_instructions = []

    # Policy interface: optional taint ablation (prop_* := base *), same layer as
    # user_approval — copies only when enabled; does not mutate caller's lists.
    from arbiteros_kernel.taint_ablation import (
        apply_taint_inheritance_ablation_for_policy,
    )

    instructions, latest_instructions = apply_taint_inheritance_ablation_for_policy(
        instructions=instructions,
        latest_instructions=latest_instructions,
    )

    if policy_classes is None:
        # Dynamic lookup so policy_registry.json changes can take effect
        # without restarting the process. All registry rows run; ``enabled``
        # selects enforce vs observe-only in apply_policy_enforcement_mode only.
        from arbiteros_kernel.policy.defaults import PolicyEntry, get_policy_registry

        registry_entries = list(get_policy_registry(force_reload=False))
    else:
        from arbiteros_kernel.policy.defaults import PolicyEntry

        registry_entries = [
            PolicyEntry(policy=cls, description="", enabled=True) for cls in policy_classes
        ]

    response = current_response
    errors: list[str] = []
    inactivate_errors: list[str] = []
    policy_names: list[str] = []
    policy_sources: dict[str, str] = {}

    for entry in registry_entries:
        policy_cls = entry.policy
        policy = policy_cls()
        response_before = copy.deepcopy(response)
        result = policy.check(
            instructions=instructions,
            current_response=response,
            latest_instructions=latest_instructions,
            trace_id=trace_id,
            user_messages=user_messages or [],
        )
        enforce = entry.enabled
        if (
            isinstance(policy_enabled_override, dict)
            and policy_cls.__name__ in policy_enabled_override
        ):
            enforce = bool(policy_enabled_override.get(policy_cls.__name__))
        result = apply_policy_enforcement_mode(enforce, response_before, result)

        if result.modified:
            response = result.response
            if result.error_type:
                errors.append(result.error_type)

            name = policy_cls.__name__
            if name not in policy_sources:
                policy_names.append(name)
                policy_sources[name] = _policy_source_location(policy_cls)

        if result.inactivate_error_type:
            inactivate_errors.append(result.inactivate_error_type)

    return PolicyCheckResult(
        modified=len(errors) > 0,
        response=response,
        error_type="\n".join(errors) if errors else None,
        policy_names=policy_names,
        policy_sources=policy_sources,
        inactivate_error_type="\n".join(inactivate_errors) if inactivate_errors else None,
    )
