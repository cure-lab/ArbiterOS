"""Tests for precall_policy_check.check_precall_policy."""

from arbiteros_kernel.precall_policy_check import (
    PreCallPolicyCheckResult,
    check_precall_policy,
)


def test_check_precall_policy_chat_payload_passthrough():
    data = {
        "model": "gpt-5.5",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ],
    }
    result = check_precall_policy(trace_id="trace-test", current_request=data)
    assert isinstance(result, PreCallPolicyCheckResult)
    assert result.modified is False
    assert result.request == data
    assert result.request is not data
    assert result.policy_names == []


def test_check_precall_policy_responses_payload_passthrough():
    data = {
        "model": "gpt-5.5",
        "instructions": "You are Codex.",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "hi"}],
            }
        ],
    }
    result = check_precall_policy(trace_id="trace-test", current_request=data)
    assert result.modified is False
    assert result.request == data
    assert result.request is not data


def test_check_precall_policy_non_dict_passthrough():
    result = check_precall_policy(trace_id="x", current_request=[])  # type: ignore[arg-type]
    assert result.modified is False
    assert result.request == {}


def test_check_precall_policy_default_registry_passthrough_for_codex():
    data = {
        "model": "gpt-5.5",
        "instructions": "You are Codex.",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "long history"}],
            }
        ],
    }
    result = check_precall_policy(
        trace_id="trace-codex",
        current_request=data,
        tool_agent="codex",
    )
    assert result.modified is False
    assert result.request == data
    assert result.policy_names == []
