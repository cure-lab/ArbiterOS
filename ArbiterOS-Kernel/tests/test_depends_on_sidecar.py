"""Tests for depends_on sidecar."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from arbiteros_kernel.depends_on_sidecar import (
    build_sidecar_messages,
    build_sidecar_response_format,
    invoke_depends_on_sidecar,
    is_respond_text_instruction,
    parse_sidecar_depends_on_payload,
    read_depends_on_sidecar_enabled,
)
from arbiteros_kernel.instruction_depends_on import SOURCE_SIDECAR


def test_is_respond_text_instruction():
    assert is_respond_text_instruction(
        {"instruction_type": "RESPOND", "content": "hello"}
    )
    assert not is_respond_text_instruction(
        {"instruction_type": "REASON", "content": "hello"}
    )
    assert not is_respond_text_instruction(
        {"instruction_type": "RESPOND", "content": {"tool_name": "x"}}
    )


def test_parse_sidecar_depends_on_payload():
    payload = json.dumps(
        {
            "depends_on": [
                {
                    "instruction_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "confidence": 0.9,
                    "counterfactual": "Would miss prior context.",
                }
            ]
        }
    )
    parsed = parse_sidecar_depends_on_payload(payload)
    assert len(parsed) == 1
    assert parsed[0]["instruction_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_build_sidecar_response_format_has_depends_on_array():
    schema = build_sidecar_response_format([], current_runtime_step=1)
    props = schema["json_schema"]["schema"]["properties"]
    assert props["depends_on"]["type"] == "array"
    confidence = props["depends_on"]["items"]["properties"]["confidence"]
    assert "minimum" not in confidence
    assert "maximum" not in confidence


def test_build_sidecar_messages_includes_content():
    messages = build_sidecar_messages(
        instructions=[],
        respond_content="final answer",
        current_runtime_step=2,
    )
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "final answer" in messages[1]["content"]


def test_invoke_depends_on_sidecar_success():
    prior_id = "11111111-2222-3333-4444-555555555555"
    instructions = [
        {
            "id": prior_id,
            "runtime_step": 1,
            "instruction_type": "USERINPUT",
            "content": "question",
        }
    ]

    def fake_completion(**_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "depends_on": [
                                    {
                                        "instruction_id": prior_id,
                                        "confidence": 0.8,
                                        "counterfactual": "Would lack user question.",
                                    }
                                ]
                            }
                        )
                    )
                )
            ]
        )

    raw = invoke_depends_on_sidecar(
        model="gpt-test",
        instructions=instructions,
        respond_content="answer",
        current_runtime_step=2,
        completion_fn=fake_completion,
    )
    assert len(raw) == 1
    assert raw[0]["instruction_id"] == prior_id


def test_invoke_depends_on_sidecar_uses_request_model_name():
    seen: dict[str, Any] = {}

    def fake_completion(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"depends_on": []}))
                )
            ]
        )

    invoke_depends_on_sidecar(
        model="gpt-5.2-chat-latest",
        instructions=[],
        respond_content="answer",
        completion_fn=fake_completion,
    )
    assert seen.get("model") == "gpt-5.2-chat-latest"


def test_invoke_depends_on_sidecar_failure_returns_empty():
    def boom(**_kwargs):
        raise RuntimeError("upstream down")

    raw = invoke_depends_on_sidecar(
        model="gpt-test",
        instructions=[],
        respond_content="answer",
        completion_fn=boom,
    )
    assert raw == []


@patch(
    "arbiteros_kernel.depends_on_sidecar._read_litellm_config_yaml",
    return_value={"arbiteros_config": {"depends_on_sidecar": {"enabled": True}}},
)
def test_read_depends_on_sidecar_enabled_true(_mock_cfg):
    assert read_depends_on_sidecar_enabled() is True


@patch(
    "arbiteros_kernel.depends_on_sidecar._read_litellm_config_yaml",
    return_value={"arbiteros_config": {"depends_on_sidecar": {"enabled": False}}},
)
def test_read_depends_on_sidecar_enabled_false(_mock_cfg):
    assert read_depends_on_sidecar_enabled() is False


def test_apply_respond_text_depends_on_sidecar_override(monkeypatch):
    from arbiteros_kernel import litellm_callback as cb

    prior_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    builder = SimpleNamespace(
        instructions=[
            {
                "id": prior_id,
                "runtime_step": 1,
                "instruction_type": "USERINPUT",
                "content": "hi",
            }
        ],
        trace_id="trace-1",
    )
    instr = {
        "id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "runtime_step": 2,
        "instruction_type": "RESPOND",
        "content": "hello back",
    }

    monkeypatch.setattr(cb, "read_depends_on_sidecar_enabled", lambda: True)
    monkeypatch.setattr(
        cb,
        "invoke_depends_on_sidecar",
        lambda **kwargs: [
            {
                "instruction_id": prior_id,
                "confidence": 0.7,
                "counterfactual": "Would not know user said hi.",
            }
        ],
    )

    cb._apply_respond_text_depends_on(
        builder,
        instr,
        "trace-1",
        request_data={"model": "gpt-test"},
    )
    assert len(instr["depends_on"]) == 1
    assert instr["depends_on"][0]["instruction_id"] == prior_id
    assert instr["depends_on"][0]["source"] == SOURCE_SIDECAR


def test_apply_respond_text_depends_on_disabled_uses_pending(monkeypatch):
    from arbiteros_kernel import litellm_callback as cb

    prior_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    builder = SimpleNamespace(
        instructions=[
            {
                "id": prior_id,
                "runtime_step": 1,
                "instruction_type": "USERINPUT",
                "content": "hi",
            }
        ],
        trace_id="trace-2",
    )
    instr = {
        "id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "runtime_step": 2,
        "instruction_type": "RESPOND",
        "content": "hello back",
    }

    monkeypatch.setattr(cb, "read_depends_on_sidecar_enabled", lambda: False)
    cb._set_pending_text_depends_on(
        "trace-2",
        [
            {
                "instruction_id": prior_id,
                "confidence": 0.5,
                "counterfactual": "Model declared.",
            }
        ],
    )

    cb._apply_respond_text_depends_on(
        builder,
        instr,
        "trace-2",
        request_data={"model": "gpt-test"},
    )
    assert len(instr["depends_on"]) == 1
    assert instr["depends_on"][0]["source"] == "model"


def test_should_skip_depends_on_sidecar_ignores_non_stream_proxy_body(monkeypatch):
    from arbiteros_kernel import litellm_callback as cb

    monkeypatch.setattr(
        cb, "_read_tool_agent_from_litellm_config", lambda: "claude_code"
    )
    request_data = {
        "messages": [{"role": "user", "content": "你是谁"}],
        "proxy_server_request": {"body": {"stream": False}},
    }
    assert cb._should_skip_depends_on_sidecar_for_request(request_data) is False


def test_should_skip_depends_on_sidecar_title_turn(monkeypatch):
    from arbiteros_kernel import litellm_callback as cb

    monkeypatch.setattr(
        cb, "_read_tool_agent_from_litellm_config", lambda: "claude_code"
    )
    request_data = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "<session>hello</session>\n\n"
                    "Write the title in the language the user wrote in."
                ),
            }
        ],
        "proxy_server_request": {"body": {"stream": True}},
    }
    assert cb._should_skip_depends_on_sidecar_for_request(request_data) is True
    assert cb._depends_on_sidecar_skip_reason(request_data) == "claude_title"


def test_should_skip_depends_on_sidecar_not_duplicate_shadow_retry(monkeypatch):
    from arbiteros_kernel import litellm_callback as cb

    monkeypatch.setattr(
        cb, "_read_tool_agent_from_litellm_config", lambda: "claude_code"
    )
    request_data = {
        "model": "claude-sonnet-4-5-20250929",
        "messages": [{"role": "user", "content": "你是谁"}],
        "proxy_server_request": {"body": {"stream": True}},
    }
    assert cb._is_claude_code_duplicate_request(request_data) is False
    cb._is_claude_code_duplicate_request(request_data)
    assert cb._should_skip_depends_on_sidecar_for_request(request_data) is False


def test_apply_respond_text_depends_on_non_stream_claude_code_invokes_sidecar(
    monkeypatch,
):
    from arbiteros_kernel import litellm_callback as cb

    prior_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    builder = SimpleNamespace(
        instructions=[
            {
                "id": prior_id,
                "runtime_step": 1,
                "instruction_type": "USERINPUT",
                "content": "hi",
            }
        ],
        trace_id="trace-3",
    )
    instr = {
        "id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        "runtime_step": 2,
        "instruction_type": "RESPOND",
        "content": "hello back",
    }

    monkeypatch.setattr(cb, "read_depends_on_sidecar_enabled", lambda: True)
    monkeypatch.setattr(
        cb, "_read_tool_agent_from_litellm_config", lambda: "claude_code"
    )
    monkeypatch.setattr(
        cb,
        "invoke_depends_on_sidecar",
        lambda **kwargs: [
            {
                "instruction_id": prior_id,
                "confidence": 0.7,
                "counterfactual": "Would not know user said hi.",
            }
        ],
    )

    cb._apply_respond_text_depends_on(
        builder,
        instr,
        "trace-3",
        request_data={
            "model": "claude-sonnet-4-5-20250929",
            "messages": [{"role": "user", "content": "hi"}],
            "proxy_server_request": {"body": {"stream": False}},
        },
    )
    assert len(instr["depends_on"]) == 1
    assert instr["depends_on"][0]["source"] == SOURCE_SIDECAR
