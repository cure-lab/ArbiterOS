"""Tests for depends_on on Claude Code (Anthropic input_schema + tool_use history)."""

import json

import pytest

from arbiteros_kernel import litellm_callback as lc
from arbiteros_kernel.litellm_callback import (
    _collect_prior_tool_call_ids_from_messages,
    _inject_tool_depends_on_into_tools,
    _normalize_reference_tool_id_list,
    _resolve_tool_parameters_container,
    _strip_and_record_tool_depends_on_in_arguments,
    _strip_and_record_tool_depends_on_from_message,
    _wrap_reference_tool_ids_into_request,
    _stripped_reference_tool_ids_by_trace,
    _stripped_categories_lock,
)


def _claude_code_tool(name: str = "Read") -> dict:
    return {
        "name": name,
        "description": "Read a file",
        "input_schema": {
            "type": "object",
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
    }


@pytest.fixture
def claude_code_agent(monkeypatch):
    monkeypatch.setattr(
        lc, "_read_tool_agent_from_litellm_config", lambda: "claude_code"
    )


def test_inject_depends_on_claude_code_input_schema(claude_code_agent):
    data = {
        "model": "claude-sonnet-4-5-20250929",
        "messages": [],
        "tools": [_claude_code_tool()],
    }
    _inject_tool_depends_on_into_tools(data)
    schema = data["tools"][0]["input_schema"]
    assert "depends_on" in schema["properties"]
    assert "depends_on" in schema["required"]
    assert "file_path" in schema["properties"]
    desc = schema["properties"]["depends_on"]["description"]
    assert "tool_use" in desc
    assert "tool_result" in desc
    assert "role='tool'" not in desc


def test_inject_depends_on_claude_code_valid_ids_from_anthropic_history(
    claude_code_agent,
):
    data = {
        "model": "claude-sonnet-4-5-20250929",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Reading file"},
                    {
                        "type": "tool_use",
                        "id": "tooluse_read_1",
                        "name": "Read",
                        "input": {"file_path": "/tmp/a.txt"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tooluse_read_1",
                        "content": "hello",
                    }
                ],
            },
        ],
        "tools": [_claude_code_tool("Write")],
    }
    _inject_tool_depends_on_into_tools(data)
    desc = data["tools"][0]["input_schema"]["properties"]["depends_on"][
        "description"
    ]
    assert "tooluse_read_1 (Read)" in desc


def test_collect_prior_tool_ids_from_anthropic_messages():
    messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tooluse_read_1",
                    "name": "Read",
                    "input": {"file_path": "/tmp/a.txt"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tooluse_read_1",
                    "content": "hello",
                }
            ],
        },
    ]
    collected = _collect_prior_tool_call_ids_from_messages(messages)
    assert collected == [("tooluse_read_1", "Read")]


def test_wrap_depends_ons_into_anthropic_tool_use_history():
    trace_id = "trace-wrap-claude-code-test"
    with _stripped_categories_lock:
        _stripped_reference_tool_ids_by_trace[trace_id] = {
            "tooluse_read_1": [],
            "tooluse_write_1": ["tooluse_read_1"],
        }
    try:
        data = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tooluse_read_1",
                            "name": "Read",
                            "input": {"file_path": "/tmp/a.txt"},
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tooluse_write_1",
                            "name": "Write",
                            "input": {
                                "file_path": "/tmp/b.txt",
                                "content": "hello",
                            },
                        }
                    ],
                },
            ]
        }
        wrapped = _wrap_reference_tool_ids_into_request(data, trace_id=trace_id)
        read_input = wrapped["messages"][0]["content"][0]["input"]
        write_input = wrapped["messages"][1]["content"][0]["input"]
        assert read_input["depends_on"] == []
        assert write_input["depends_on"] == ["tooluse_read_1"]
    finally:
        with _stripped_categories_lock:
            _stripped_reference_tool_ids_by_trace.pop(trace_id, None)


def test_strip_and_record_depends_ons_from_anthropic_message():
    trace_id = "trace-strip-claude-code-test"
    with _stripped_categories_lock:
        _stripped_reference_tool_ids_by_trace.pop(trace_id, None)
    message = {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "tooluse_write_1",
                "name": "Write",
                "input": {
                    "file_path": "/tmp/b.txt",
                    "content": "hello",
                    "depends_on": ["tooluse_read_1"],
                },
            }
        ],
    }
    request_data = {"metadata": {"arbiteros_trace_id": trace_id}}
    _strip_and_record_tool_depends_on_from_message(message, request_data)
    stripped_input = message["content"][0]["input"]
    assert "depends_on" not in stripped_input
    with _stripped_categories_lock:
        assert _stripped_reference_tool_ids_by_trace[trace_id]["tooluse_write_1"] == [
            "tooluse_read_1"
        ]
        _stripped_reference_tool_ids_by_trace.pop(trace_id, None)


def test_normalize_depends_on_list_parses_json_string():
    assert _normalize_reference_tool_id_list('["tooluse_read_1"]') == [
        "tooluse_read_1"
    ]
    assert _normalize_reference_tool_id_list(["call_a", "call_b"]) == [
        "call_a",
        "call_b",
    ]
    assert _normalize_reference_tool_id_list("") == []


def test_strip_malformed_string_depends_on(claude_code_agent):
    trace_id = "trace-malformed-ref-test"
    with _stripped_categories_lock:
        _stripped_reference_tool_ids_by_trace.pop(trace_id, None)
    cleaned = _strip_and_record_tool_depends_on_in_arguments(
        {
            "file_path": "/tmp/b.txt",
            "depends_on": '["tooluse_read_1"]',
        },
        tool_call_id="tooluse_write_1",
        trace_id=trace_id,
    )
    assert "depends_on" not in cleaned
    with _stripped_categories_lock:
        assert _stripped_reference_tool_ids_by_trace[trace_id]["tooluse_write_1"] == [
            "tooluse_read_1"
        ]
        _stripped_reference_tool_ids_by_trace.pop(trace_id, None)


def test_resolve_tool_parameters_container_openclaw_unaffected(monkeypatch):
    monkeypatch.setattr(lc, "_read_tool_agent_from_litellm_config", lambda: "openclaw")
    openclaw_tool = {
        "type": "function",
        "function": {
            "name": "read",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }
    anthropic_shaped = {
        "name": "Read",
        "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}},
    }
    assert _resolve_tool_parameters_container(openclaw_tool) is openclaw_tool["function"]["parameters"]
    assert _resolve_tool_parameters_container(anthropic_shaped) is None


def test_resolve_tool_parameters_container_claude_code_uses_input_schema(claude_code_agent):
    tool = _claude_code_tool()
    assert _resolve_tool_parameters_container(tool) is tool["input_schema"]
