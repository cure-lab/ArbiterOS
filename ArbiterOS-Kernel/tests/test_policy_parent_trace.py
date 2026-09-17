"""Parent trace id is appended onto policy BLOCK narratives for any agent."""

from __future__ import annotations

from arbiteros_kernel.policy import Policy
from arbiteros_kernel.policy_check import (
    PolicyCheckResult,
    annotate_block_text_with_parent,
    annotate_policy_result_with_parent,
    check_response_policy,
)


class _BlockingPolicy(Policy):
    def check(self, instructions, current_response, latest_instructions, trace_id, **kwargs):
        _ = instructions, latest_instructions, trace_id, kwargs
        response = dict(current_response)
        response["content"] = "## ⚠️ test policy block\n- exec was blocked"
        response["tool_calls"] = []
        return PolicyCheckResult(
            modified=True,
            response=response,
            error_type=response["content"],
        )


def test_annotate_block_text_appends_parent_once():
    text = "## ⚠️ block\n- exec"
    once = annotate_block_text_with_parent(
        text, parent_trace_id="parent-tid", child_trace_id="child-tid"
    )
    assert once.endswith("parent_trace_id: parent-tid")
    twice = annotate_block_text_with_parent(
        once, parent_trace_id="parent-tid", child_trace_id="child-tid"
    )
    assert twice == once
    assert annotate_block_text_with_parent(
        text, parent_trace_id="child-tid", child_trace_id="child-tid"
    ) == text


def test_annotate_policy_result_skips_without_parent(monkeypatch):
    monkeypatch.setattr(
        "arbiteros_kernel.policy_check.resolve_block_parent_trace_id",
        lambda trace_id: None,
    )
    result = PolicyCheckResult(
        modified=True,
        response={"content": "blocked"},
        error_type="blocked",
    )
    out = annotate_policy_result_with_parent(result, trace_id="child-tid")
    assert out.error_type == "blocked"
    assert out.response["content"] == "blocked"


def test_check_response_policy_appends_parent_for_any_agent(monkeypatch):
    monkeypatch.setattr(
        "arbiteros_kernel.policy_check._is_local_policy_confirm_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "arbiteros_kernel.policy_check.resolve_block_parent_trace_id",
        lambda trace_id: "34f13f6bc2fa258d34165c90c46ae6f2"
        if trace_id == "dda40711b38f680d6949c2119aeaf6ac"
        else None,
    )
    result = check_response_policy(
        trace_id="dda40711b38f680d6949c2119aeaf6ac",
        instructions=[],
        current_response={
            "role": "assistant",
            "content": "will be replaced",
            "tool_calls": [{"id": "call_1"}],
        },
        latest_instructions=[],
        policy_classes=[_BlockingPolicy],
    )
    assert result.modified is True
    assert "parent_trace_id: 34f13f6bc2fa258d34165c90c46ae6f2" in (result.error_type or "")
    assert "parent_trace_id: 34f13f6bc2fa258d34165c90c46ae6f2" in result.response["content"]

    top = check_response_policy(
        trace_id="34f13f6bc2fa258d34165c90c46ae6f2",
        instructions=[],
        current_response={"role": "assistant", "content": "x", "tool_calls": []},
        latest_instructions=[],
        policy_classes=[_BlockingPolicy],
    )
    assert "parent_trace_id:" not in (top.error_type or "")
