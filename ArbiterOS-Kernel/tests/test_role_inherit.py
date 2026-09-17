"""Child inherits parent role only when it has no role of its own."""

from __future__ import annotations

from pathlib import Path

import pytest

import arbiteros_kernel.agent_graph as ag
from arbiteros_kernel.trace_roles import (
    get_trace_role,
    resolve_effective_role_for_request,
    set_trace_role,
)


PARENT = "parent-role-tid"
CHILD = "child-role-tid"
GRANDCHILD = "grandchild-role-tid"
AGENT = "agent-role-inherit-1"


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARBITEROS_AGENT_GRAPH_FILE", str(tmp_path / "agent_graph.json"))
    ag.reset_graph_file_cache()
    monkeypatch.setattr(
        "arbiteros_kernel.trace_roles.trace_state_path",
        lambda: tmp_path / "trace_state.json",
    )


def _link_child(child: str = CHILD, parent: str = PARENT) -> None:
    ag.record_spawn(
        parent_trace_id=parent,
        session_id="sess-role-inherit",
        agent_id=AGENT,
        subagent_type="Explore",
    )
    linked = ag.link_child(
        child_trace_id=child,
        session_id="sess-role-inherit",
        agent_id=AGENT,
        parent_trace_id=parent,
    )
    assert linked == parent
    assert ag.parent_of(child) == parent


def test_child_inherits_parent_named_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    _link_child()
    set_trace_role(PARENT, role_name="semantic_protected", source="os", locked_by_os=True)

    role, source, locked, warning = resolve_effective_role_for_request(
        trace_id=CHILD,
        requested_role=None,
    )
    assert role == "semantic_protected"
    assert source == "inherit"
    assert locked is False
    assert warning is None
    assert get_trace_role(CHILD)["role_name"] == "semantic_protected"
    assert get_trace_role(CHILD)["role_source"] == "inherit"


def test_requested_role_wins_over_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    _link_child()
    set_trace_role(PARENT, role_name="semantic_protected", source="os", locked_by_os=True)

    role, source, locked, warning = resolve_effective_role_for_request(
        trace_id=CHILD,
        requested_role="bank_demo",
    )
    assert role == "bank_demo"
    assert source == "init"
    assert locked is False
    assert warning is None


def test_stored_role_wins_over_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    _link_child()
    set_trace_role(PARENT, role_name="semantic_protected", source="os", locked_by_os=True)
    set_trace_role(CHILD, role_name="bank_demo", source="init", locked_by_os=False)

    role, source, locked, warning = resolve_effective_role_for_request(
        trace_id=CHILD,
        requested_role=None,
    )
    assert role == "bank_demo"
    assert source == "init"
    assert locked is False
    assert warning is None


def test_os_lock_default_does_not_inherit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    _link_child()
    set_trace_role(PARENT, role_name="semantic_protected", source="os", locked_by_os=True)
    set_trace_role(CHILD, role_name=None, source="os", locked_by_os=True)

    role, source, locked, warning = resolve_effective_role_for_request(
        trace_id=CHILD,
        requested_role="semantic_protected",
    )
    assert role is None
    assert source == "os"
    assert locked is True
    assert warning is None


def test_root_role_unchanged_without_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    role, source, locked, warning = resolve_effective_role_for_request(
        trace_id=PARENT,
        requested_role=None,
    )
    assert role is None
    assert locked is False
    assert warning is None
    assert source == "init"


def test_grandchild_inherits_through_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    _link_child()
    ag.record_spawn(
        parent_trace_id=CHILD,
        session_id="sess-role-inherit",
        agent_id="agent-role-inherit-2",
        subagent_type="Plan",
    )
    ag.link_child(
        child_trace_id=GRANDCHILD,
        session_id="sess-role-inherit",
        agent_id="agent-role-inherit-2",
        parent_trace_id=CHILD,
    )
    set_trace_role(PARENT, role_name="semantic_protected", source="os", locked_by_os=True)
    resolve_effective_role_for_request(trace_id=CHILD, requested_role=None)
    role, source, _locked, warning = resolve_effective_role_for_request(
        trace_id=GRANDCHILD,
        requested_role=None,
    )
    assert role == "semantic_protected"
    assert source == "inherit"
    assert warning is None


def test_post_call_role_resolver_inherits_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(tmp_path, monkeypatch)
    _link_child()
    set_trace_role(PARENT, role_name="semantic_protected", source="os", locked_by_os=True)

    import arbiteros_kernel.litellm_callback as cb

    role, mode = cb._resolve_effective_role_for_policy(
        trace_id=CHILD,
        request_data={"model": "gpt-5.5"},
    )
    assert mode == "named"
    assert role == "semantic_protected"
