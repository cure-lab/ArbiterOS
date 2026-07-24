"""Tests for said/done TUI confirm bridge (Y=deny, N=allow; policy-first)."""

from __future__ import annotations

import json

import pytest

from arbiteros_kernel import tui_bridge as tb


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    tui = tmp_path / "tui"
    hook = tmp_path / "hook"
    monkeypatch.setenv("ARBITEROS_TUI_DIR", str(tui))
    monkeypatch.setenv("ARBITEROS_DEFENDER_HOOK_DIR", str(hook))
    tb._TUI_DIR = None
    yield tui, hook
    tb._TUI_DIR = None


def test_said_done_sorts_after_policy(isolated_dirs):
    tb.enqueue_confirm(
        trace_id="t1",
        error_type="mismatch",
        policy_names=["said_done"],
        kind=tb.KIND_SAID_DONE,
        request_id="sd-1",
    )
    tb.enqueue_confirm(
        trace_id="t1",
        error_type="blocked",
        policy_names=["p1"],
        kind=tb.KIND_POLICY,
        request_id="pol-1",
    )
    ordered = tb.sort_pending_confirms(tb.list_pending_confirms())
    assert [tb.pending_kind(x) for x in ordered] == [tb.KIND_POLICY, tb.KIND_SAID_DONE]
    assert [x["request_id"] for x in ordered] == ["pol-1", "sd-1"]


def test_said_done_y_denies_n_allows(isolated_dirs):
    _, hook = isolated_dirs
    tb.enqueue_confirm(
        trace_id="t1",
        error_type="mismatch",
        policy_names=["said_done"],
        kind=tb.KIND_SAID_DONE,
        request_id="sd-y",
        extra={"diff": "cmd changed"},
    )
    assert tb.submit_confirm_answer(request_id="sd-y", keep_block=True)
    deny = json.loads((hook / "decisions" / "sd-y.json").read_text(encoding="utf-8"))
    assert deny["decision"] == "deny"

    tb.enqueue_confirm(
        trace_id="t1",
        error_type="mismatch",
        policy_names=["said_done"],
        kind=tb.KIND_SAID_DONE,
        request_id="sd-n",
    )
    assert tb.submit_confirm_answer(request_id="sd-n", keep_block=False)
    allow = json.loads((hook / "decisions" / "sd-n.json").read_text(encoding="utf-8"))
    assert allow["decision"] == "allow"
    assert tb.list_pending_confirms() == []


def test_policy_answer_does_not_write_defender_decision(isolated_dirs):
    _, hook = isolated_dirs
    tb.enqueue_confirm(
        trace_id="t1",
        error_type="blocked",
        policy_names=["p1"],
        request_id="pol-x",
    )
    assert tb.submit_confirm_answer(request_id="pol-x", keep_block=True)
    assert not (hook / "decisions" / "pol-x.json").exists()


def test_clear_pending_confirm(isolated_dirs):
    tb.enqueue_confirm(
        trace_id="t1",
        error_type="mismatch",
        policy_names=["said_done"],
        kind=tb.KIND_SAID_DONE,
        request_id="sd-clear",
    )
    assert tb.clear_pending_confirm("sd-clear") is True
    assert tb.list_pending_confirms() == []
    assert tb.clear_pending_confirm("sd-clear") is False
