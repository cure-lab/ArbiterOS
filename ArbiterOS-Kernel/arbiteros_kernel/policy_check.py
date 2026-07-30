"""Policy checks for ArbiterOS Kernel - validate/modify responses before returning to agent."""

from __future__ import annotations

import copy
import inspect
import json
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dep
    yaml = None

if TYPE_CHECKING:
    from arbiteros_kernel.policy import Policy

__all__ = [
    "PolicyCheckResult",
    "check_response_policy",
    "apply_policy_enforcement_mode",
    "is_local_policy_confirm_enabled",
    "list_registered_roles",
    "is_registered_role",
    "resolve_role_policy_entries",
    "resolve_role_policy_enabled_override",
    "split_model_and_role",
    "split_model_agent_role",
    "DEFAULT_ROLE_NAME",
]

DEFAULT_ROLE_NAME = "default"

_ROLE_POLICY_SETS_PATH = (
    Path(__file__).resolve().parent / "role_policy_sets.json"
)
_ROLE_POLICY_SETS_LOCK = threading.Lock()
_ROLE_POLICY_SETS_CACHE_MTIME_NS: Optional[int] = None
_ROLE_POLICY_SETS_CACHE: list[dict[str, Any]] = []
_LITELLM_CFG_PATH = Path(__file__).resolve().parent.parent / "litellm_config.yaml"
_LOCAL_CONFIRM_CFG_LOCK = threading.Lock()
_LOCAL_CONFIRM_CFG_MTIME_NS: Optional[int] = None
_LOCAL_CONFIRM_CFG_ENABLED: bool = False
_LOCAL_CONFIRM_CFG_TEST_MODE: bool = False
_LOCAL_CONFIRM_INPUT_LOCK = threading.Lock()


def is_local_policy_confirm_enabled() -> bool:
    """Public wrapper for litellm streaming / post-call gating."""
    return _is_local_policy_confirm_enabled()


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

    local_confirmation_resolved: bool = False
    """Whether local manual confirmation has been performed."""

    local_confirmation_decision: Optional[str] = None
    """Local decision: keep_block | allow_original."""


def _is_local_policy_confirm_enabled() -> bool:
    if yaml is None:
        return False
    p = _LITELLM_CFG_PATH
    if not p.exists():
        return False
    try:
        mtime_ns = p.stat().st_mtime_ns
    except Exception:
        return False
    with _LOCAL_CONFIRM_CFG_LOCK:
        global _LOCAL_CONFIRM_CFG_MTIME_NS, _LOCAL_CONFIRM_CFG_ENABLED, _LOCAL_CONFIRM_CFG_TEST_MODE
        if _LOCAL_CONFIRM_CFG_MTIME_NS == mtime_ns:
            return _LOCAL_CONFIRM_CFG_ENABLED
        enabled = False
        test_mode = False
        try:
            parsed = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                enabled = bool(parsed.get("arbiteros_local_policy_confirm", False))
                test_mode = bool(
                    parsed.get("arbiteros_local_policy_confirm_test_mode", False)
                )
        except Exception:
            enabled = False
            test_mode = False
        _LOCAL_CONFIRM_CFG_MTIME_NS = mtime_ns
        _LOCAL_CONFIRM_CFG_ENABLED = enabled
        _LOCAL_CONFIRM_CFG_TEST_MODE = test_mode
        return enabled


def _is_local_policy_confirm_test_mode() -> bool:
    _is_local_policy_confirm_enabled()
    with _LOCAL_CONFIRM_CFG_LOCK:
        return _LOCAL_CONFIRM_CFG_TEST_MODE


def _prompt_local_policy_confirmation(
    *,
    trace_id: str,
    error_type: str,
    policy_names: list[str],
) -> bool:
    """
    Return True to keep block, False to allow original response.
    """
    try:
        from arbiteros_kernel.tui_bridge import is_tui_mode, request_confirm_via_tui

        if is_tui_mode():
            return request_confirm_via_tui(
                trace_id=trace_id,
                error_type=error_type,
                policy_names=policy_names,
            )
    except Exception:
        pass
    with _LOCAL_CONFIRM_INPUT_LOCK:
        if _is_local_policy_confirm_test_mode():
            print(
                "[ArbiterOS][LocalConfirm] test mode enabled; auto keep block (Y).",
                file=sys.stderr,
            )
            return True
        if not sys.stdin or not hasattr(sys.stdin, "isatty") or not sys.stdin.isatty():
            print(
                "[ArbiterOS][LocalConfirm] stdin is not interactive; default to keep block.",
                file=sys.stderr,
            )
            return True
        header = (
            "\n[ArbiterOS][LocalConfirm] Policy block detected.\n"
            f"trace_id={trace_id}\n"
            f"policies={', '.join(policy_names) if policy_names else '(unknown)'}\n"
            "Choose action: [Y] keep block / [N] allow original response"
        )
        print(header, file=sys.stderr)
        reason_preview = (error_type or "").strip()
        if reason_preview:
            print(reason_preview, file=sys.stderr)
        while True:
            try:
                choice = input("[ArbiterOS][LocalConfirm] Enter Y or N: ").strip().lower()
            except EOFError:
                print(
                    "[ArbiterOS][LocalConfirm] EOF on stdin; default to keep block.",
                    file=sys.stderr,
                )
                return True
            if choice in {"y", "yes", ""}:
                return True
            if choice in {"n", "no"}:
                return False
            print("Please enter Y or N.", file=sys.stderr)


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


def split_model_agent_role(model_value: Any) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Parse ``route_model;agent_name;role`` from request model field.

    Returns ``(route_model, agent_name, role_name)``.
    When no ``;`` is present, returns ``(raw_model, None, None)``.
    With one ``;``, treats the suffix as ``agent_name``.
    With two or more ``;``, the third segment is ``role_name``.
    """
    if not isinstance(model_value, str):
        return None, None, None
    raw = model_value.strip()
    if not raw:
        return None, None, None
    if ";" not in raw:
        return raw, None, None

    parts = [part.strip() for part in raw.split(";")]
    route_model = parts[0] if parts else raw
    agent_name = parts[1] if len(parts) > 1 and parts[1] else None
    role_name = parts[2] if len(parts) > 2 and parts[2] else None
    if not route_model:
        route_model = raw
    return route_model, agent_name, role_name


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


def list_registered_roles() -> list[dict[str, Any]]:
    """
    Return registered roles from ``role_policy_sets.json``.

    Each item: ``{name, description, policies}`` where policies are
    ``[{name, description, enabled}, ...]``.
    """
    out: list[dict[str, Any]] = []
    for row in _load_role_policy_sets():
        name = str(row.get("name") or "").strip()
        if not name or name.lower() == DEFAULT_ROLE_NAME:
            continue
        description = str(row.get("description") or "").strip()
        policies_raw = row.get("policies")
        # Legacy: enabled_policies: ["FooPolicy", ...]
        if not isinstance(policies_raw, list):
            enabled_raw = row.get("enabled_policies")
            if isinstance(enabled_raw, list):
                policies_raw = [
                    {"name": str(item).strip(), "enabled": True, "description": ""}
                    for item in enabled_raw
                    if isinstance(item, str) and str(item).strip()
                ]
            else:
                policies_raw = []
        policies: list[dict[str, Any]] = []
        for item in policies_raw:
            if not isinstance(item, dict):
                continue
            pname = str(item.get("name") or "").strip()
            if not pname:
                continue
            policies.append(
                {
                    "name": pname,
                    "description": str(item.get("description") or "").strip(),
                    "enabled": bool(item.get("enabled", True)),
                }
            )
        out.append(
            {
                "name": name,
                "description": description,
                "policies": policies,
            }
        )
    return out


def is_registered_role(role_name: Optional[str]) -> bool:
    normalized = role_name.strip() if isinstance(role_name, str) else ""
    if not normalized or normalized.lower() == DEFAULT_ROLE_NAME:
        return False
    return any(r["name"] == normalized for r in list_registered_roles())


def resolve_role_policy_entries(
    role_name: Optional[str],
) -> tuple[Optional[list[Any]], Optional[dict[str, bool]], Optional[str]]:
    """
    Resolve named-role policy entries from ``role_policy_sets.json``.

    Returns:
      - list of ``PolicyEntry`` for policies listed on the role (may be empty)
      - enabled override map for those policies
      - warning/error reason when role cannot be applied (caller should fall back
        to default / global registry)
    """
    from arbiteros_kernel.policy.defaults import POLICY_CLASS_MAP, PolicyEntry

    normalized_role = role_name.strip() if isinstance(role_name, str) else ""
    if not normalized_role or normalized_role.lower() == DEFAULT_ROLE_NAME:
        return None, None, None

    matched: Optional[dict[str, Any]] = None
    for row in list_registered_roles():
        if row["name"] == normalized_role:
            matched = row
            break
    if matched is None:
        # Also try raw load for legacy rows that failed list normalization.
        for row in _load_role_policy_sets():
            if str(row.get("name") or "").strip() == normalized_role:
                matched = {
                    "name": normalized_role,
                    "description": str(row.get("description") or "").strip(),
                    "policies": [],
                }
                policies_raw = row.get("policies")
                if isinstance(policies_raw, list):
                    matched["policies"] = [
                        {
                            "name": str(p.get("name") or "").strip(),
                            "description": str(p.get("description") or "").strip(),
                            "enabled": bool(p.get("enabled", True)),
                        }
                        for p in policies_raw
                        if isinstance(p, dict) and str(p.get("name") or "").strip()
                    ]
                elif isinstance(row.get("enabled_policies"), list):
                    matched["policies"] = [
                        {
                            "name": str(item).strip(),
                            "description": "",
                            "enabled": True,
                        }
                        for item in row["enabled_policies"]
                        if isinstance(item, str) and str(item).strip()
                    ]
                break

    if matched is None:
        return None, None, f"role_not_found:{normalized_role}"

    policies = matched.get("policies")
    if not isinstance(policies, list):
        return None, None, f"invalid_policies:{normalized_role}"

    entries: list[Any] = []
    override: dict[str, bool] = {}
    unknown: list[str] = []
    seen: set[str] = set()
    for item in policies:
        if not isinstance(item, dict):
            continue
        pname = str(item.get("name") or "").strip()
        if not pname or pname in seen:
            continue
        seen.add(pname)
        policy_cls = POLICY_CLASS_MAP.get(pname)
        if policy_cls is None:
            unknown.append(pname)
            continue
        enabled = bool(item.get("enabled", True))
        description = str(item.get("description") or "").strip()
        entries.append(
            PolicyEntry(policy=policy_cls, description=description, enabled=enabled)
        )
        override[pname] = enabled

    if unknown:
        return None, None, (
            f"unknown_policies:{normalized_role}:" + ",".join(sorted(unknown))
        )

    return entries, override, None


def resolve_role_policy_enabled_override(
    role_name: Optional[str],
) -> tuple[Optional[dict[str, bool]], Optional[str]]:
    """
    Resolve per-request policy enabled overrides for a named role.

    Named roles use ``role_policy_sets.json`` as the authority for which
    policies run (via :func:`resolve_role_policy_entries`). This helper returns
    only the enabled map for callers that still pass overrides.
    """
    _entries, override, reason = resolve_role_policy_entries(role_name)
    if reason:
        return None, reason
    if override is None:
        return None, None
    return override, None


def check_response_policy(
    *,
    trace_id: str,
    instructions: list[dict[str, Any]],
    current_response: dict[str, Any],
    latest_instructions: list[dict[str, Any]] | None = None,
    policy_classes: Optional[list[type["Policy"]]] = None,
    policy_entries: Optional[list[Any]] = None,
    user_messages: list[str] | None = None,
    policy_enabled_override: Optional[dict[str, bool]] = None,
    policy_runtime_context: Optional[dict[str, Any]] = None,
) -> PolicyCheckResult:
    """
    Policy check on post_call_success response before returning to agent.

    Input:
        trace_id: Trace ID.
        instructions: Full instruction history from {trace_id}.json. (include the latest_instructions)
        current_response: Current post_call_success response (after strip/transform).
        latest_instructions: Instructions from this response (content + tool_calls 等，current_response 里有的都有).
        policy_classes: If set, run exactly these classes as if registry
            ``enabled: true``. Ignored when ``policy_entries`` is provided.
        policy_entries: If set, run exactly these ``PolicyEntry`` rows (named-role
            authority). If None and ``policy_classes`` is None, load **all** entries
            from ``policy_registry.json`` via ``get_policy_registry()``; each entry's
            ``enabled`` controls observe-only vs enforce **outside** policies via
            :func:`apply_policy_enforcement_mode` (no kwargs passed into
            ``Policy.check``).
        user_messages: Optional full user-message history from current precall payload.
            Passed through to policy.check via kwargs for policies that need it.
        policy_runtime_context: Optional runtime metrics/context passed through to
            policy.check via kwargs (e.g., token/time/instruction budgets).

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

    if policy_entries is not None:
        registry_entries = list(policy_entries)
    elif policy_classes is None:
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

    original_response_snapshot = copy.deepcopy(current_response)
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
            policy_runtime_context=(
                policy_runtime_context if isinstance(policy_runtime_context, dict) else {}
            ),
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

    aggregated_result = PolicyCheckResult(
        modified=len(errors) > 0,
        response=response,
        error_type="\n".join(errors) if errors else None,
        policy_names=policy_names,
        policy_sources=policy_sources,
        inactivate_error_type="\n".join(inactivate_errors) if inactivate_errors else None,
    )
    if not aggregated_result.modified:
        return aggregated_result

    if not _is_local_policy_confirm_enabled():
        return aggregated_result

    keep_block = _prompt_local_policy_confirmation(
        trace_id=trace_id,
        error_type=aggregated_result.error_type or "",
        policy_names=aggregated_result.policy_names,
    )
    if keep_block:
        return PolicyCheckResult(
            modified=True,
            response=aggregated_result.response,
            error_type=aggregated_result.error_type,
            policy_names=aggregated_result.policy_names,
            policy_sources=aggregated_result.policy_sources,
            inactivate_error_type=aggregated_result.inactivate_error_type,
            local_confirmation_resolved=True,
            local_confirmation_decision="keep_block",
        )

    return PolicyCheckResult(
        modified=False,
        response=original_response_snapshot,
        error_type=None,
        policy_names=aggregated_result.policy_names,
        policy_sources=aggregated_result.policy_sources,
        inactivate_error_type=aggregated_result.inactivate_error_type,
        local_confirmation_resolved=True,
        local_confirmation_decision="allow_original",
    )
