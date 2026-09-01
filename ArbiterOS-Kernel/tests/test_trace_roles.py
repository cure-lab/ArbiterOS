"""Tests for OS-assignable trace roles and role_policy_sets schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arbiteros_kernel.policy.defaults import PolicyEntry
from arbiteros_kernel.policy_check import (
    DEFAULT_ROLE_NAME,
    apply_policy_enforcement_mode,
    is_registered_role,
    list_registered_roles,
    resolve_role_policy_entries,
    PolicyCheckResult,
)
from arbiteros_kernel.trace_roles import (
    display_role_name,
    get_trace_role,
    resolve_effective_role_for_request,
    set_trace_role,
)


def test_list_registered_roles_has_policies_schema() -> None:
    roles = list_registered_roles()
    assert roles
    semantic = next(r for r in roles if r["name"] == "semantic_protected")
    assert semantic["description"]
    assert isinstance(semantic["policies"], list)
    assert semantic["policies"]
    for item in semantic["policies"]:
        assert "name" in item
        assert "description" in item
        assert "enabled" in item
        assert isinstance(item["enabled"], bool)


def test_resolve_role_policy_entries_named_role() -> None:
    entries, override, reason = resolve_role_policy_entries("semantic_protected")
    assert reason is None
    assert isinstance(entries, list)
    assert entries
    assert all(isinstance(e, PolicyEntry) for e in entries)
    assert isinstance(override, dict)
    assert "RateLimitPolicy" in override
    assert override["RateLimitPolicy"] is True
    # Named role does not include ResourceGuard unless listed.
    assert "ResourceGuardPolicy" not in override


def test_resolve_role_policy_entries_default_and_unknown() -> None:
    entries, override, reason = resolve_role_policy_entries(DEFAULT_ROLE_NAME)
    assert entries is None and override is None and reason is None

    entries, override, reason = resolve_role_policy_entries("no_such_role_zzz")
    assert entries is None
    assert override is None
    assert reason and reason.startswith("role_not_found:")


def test_apply_enforcement_observe_only() -> None:
    snapshot = {"ok": True}
    modified = PolicyCheckResult(
        modified=True,
        response={"blocked": True},
        error_type="would block",
    )
    observed = apply_policy_enforcement_mode(False, snapshot, modified)
    assert observed.modified is False
    assert observed.response == snapshot
    assert observed.inactivate_error_type == "would block"


def test_trace_role_os_lock_and_init_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "trace_state.json"
    monkeypatch.setattr(
        "arbiteros_kernel.trace_roles.trace_state_path",
        lambda: state_file,
    )

    tid = "trace-role-test-001"
    # Unregistered request → default + warning
    effective, source, locked, warning = resolve_effective_role_for_request(
        trace_id=tid,
        requested_role="not_a_real_role",
    )
    assert effective is None
    assert source == "init"
    assert locked is False
    assert warning and warning.startswith("role_not_registered:")
    assert display_role_name(effective) == DEFAULT_ROLE_NAME

    # Registered init refresh
    effective, source, locked, warning = resolve_effective_role_for_request(
        trace_id=tid,
        requested_role="semantic_protected",
    )
    assert effective == "semantic_protected"
    assert source == "init"
    assert locked is False
    assert warning is None
    assert get_trace_role(tid)["role_name"] == "semantic_protected"

    # OS lock
    set_trace_role(
        tid,
        role_name=DEFAULT_ROLE_NAME,
        source="os",
        locked_by_os=True,
    )
    effective, source, locked, warning = resolve_effective_role_for_request(
        trace_id=tid,
        requested_role="semantic_protected",
    )
    assert effective is None  # default
    assert locked is True
    assert source == "os"
    assert warning is None

    # Switch OS role to semantic_protected again
    set_trace_role(
        tid,
        role_name="semantic_protected",
        source="os",
        locked_by_os=True,
    )
    effective, source, locked, warning = resolve_effective_role_for_request(
        trace_id=tid,
        requested_role=None,
    )
    assert effective == "semantic_protected"
    assert locked is True


def test_is_registered_role() -> None:
    assert is_registered_role("semantic_protected")
    assert is_registered_role("bank_demo")
    assert not is_registered_role(DEFAULT_ROLE_NAME)
    assert not is_registered_role(None)
    assert not is_registered_role("missing")


def test_persisted_role_survives_missing_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-call must honor OS role even when request metadata was stripped."""
    state_file = tmp_path / "trace_state.json"
    monkeypatch.setattr(
        "arbiteros_kernel.trace_roles.trace_state_path",
        lambda: state_file,
    )
    # Keep litellm_callback's get_trace_role pointed at the same temp file.
    import arbiteros_kernel.litellm_callback as cb

    monkeypatch.setattr(
        "arbiteros_kernel.litellm_callback.get_trace_role",
        get_trace_role,
    )

    tid = "trace-strip-meta-001"
    set_trace_role(
        tid,
        role_name="semantic_protected",
        source="os",
        locked_by_os=True,
    )
    role, mode = cb._resolve_effective_role_for_policy(
        trace_id=tid,
        request_data={"model": "gpt-5.5"},  # no metadata
    )
    assert mode == "named"
    assert role == "semantic_protected"
    entries, override, reason = resolve_role_policy_entries(role)
    assert reason is None
    assert override is not None
    assert "ResourceGuardPolicy" not in override
    assert "RateLimitPolicy" in override

