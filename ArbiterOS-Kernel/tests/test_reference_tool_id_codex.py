"""Tests for depends_on injection/wrap on Codex (Responses API) and OpenClaw paths."""

import json

import pytest

import arbiteros_kernel.litellm_callback as lc
from arbiteros_kernel.litellm_callback import (
    _collect_prior_tool_call_ids_from_request,
    _collect_prior_tool_call_ids_from_responses_input,
    _inject_tool_depends_on_into_tools,
    _resolve_tool_parameters_container,
    _strip_and_record_tool_depends_on_from_message,
    _wrap_reference_tool_ids_into_request,
    _stripped_reference_tool_ids_by_trace,
    _stripped_categories_lock,
)


def _codex_tool(name: str = "exec_command") -> dict:
    return {
        "type": "function",
        "name": name,
        "description": "run command",
        "strict": False,
        "parameters": {
            "type": "object",
            "properties": {"cmd": {"type": "string"}},
            "required": ["cmd"],
        },
    }


def _openclaw_tool(name: str = "read") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "read file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }


def test_inject_depends_on_codex_description_uses_responses_wording():
    data = {
        "model": "gpt-5.5",
        "input": [
            {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call_abc",
                "arguments": "{}",
            }
        ],
        "tools": [_codex_tool()],
    }
    _inject_tool_depends_on_into_tools(data)
    desc = data["tools"][0]["parameters"]["properties"]["depends_on"]["description"]
    assert "function_call_output" in desc
    assert "call_id" in desc
    assert "role='tool'" not in desc
    assert "call_abc (exec_command)" in desc


def test_inject_depends_on_openclaw_description_uses_chat_wording(monkeypatch):
    monkeypatch.setattr(
        lc, "_read_tool_agent_from_litellm_config", lambda: "openclaw"
    )
    data = {
        "model": "gpt-4",
        "messages": [{"role": "tool", "tool_call_id": "call_chat", "content": "ok"}],
        "tools": [_openclaw_tool()],
    }
    _inject_tool_depends_on_into_tools(data)
    desc = data["tools"][0]["function"]["parameters"]["properties"]["depends_on"]["description"]
    assert "role='tool'" in desc
    assert "tool_call_id" in desc
    assert "function_call_output" not in desc


def test_inject_depends_on_codex_flat_tool_schema():
    data = {
        "model": "gpt-5.5",
        "input": [],
        "tools": [_codex_tool()],
    }
    _inject_tool_depends_on_into_tools(data)
    params = data["tools"][0]["parameters"]
    assert "depends_on" in params["properties"]
    assert "depends_on" in params["required"]


def test_inject_depends_on_openclaw_nested_tool_schema_unchanged(monkeypatch):
    monkeypatch.setattr(
        lc, "_read_tool_agent_from_litellm_config", lambda: "openclaw"
    )
    data = {
        "model": "gpt-4",
        "messages": [],
        "tools": [_openclaw_tool()],
    }
    _inject_tool_depends_on_into_tools(data)
    params = data["tools"][0]["function"]["parameters"]
    assert "depends_on" in params["properties"]
    assert "depends_on" in params["required"]
    assert "path" in params["properties"]


def test_collect_prior_tool_ids_from_responses_input():
    input_items = [
        {"type": "message", "role": "user", "content": "hi"},
        {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call_abc",
            "arguments": '{"cmd":"ls"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_abc",
            "output": "ok",
        },
    ]
    collected = _collect_prior_tool_call_ids_from_responses_input(input_items)
    assert collected == [("call_abc", "exec_command")]


def test_collect_prior_tool_ids_merges_messages_and_input():
    data = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_chat",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
            }
        ],
        "input": [
            {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call_resp",
                "arguments": "{}",
            }
        ],
    }
    collected = _collect_prior_tool_call_ids_from_request(data)
    assert ("call_chat", "read") in collected
    assert ("call_resp", "exec_command") in collected


def _legacy_depends_on_entry(instruction_id: str) -> dict:
    return {
        "instruction_id": instruction_id,
        "confidence": 0.0,
        "counterfactual": "",
    }


def test_wrap_depends_ons_into_responses_input():
    trace_id = "trace-wrap-codex-test"
    with _stripped_categories_lock:
        _stripped_reference_tool_ids_by_trace[trace_id] = {
            "call_abc": [_legacy_depends_on_entry("call_prev")],
        }
    try:
        data = {
            "input": [
                {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call_abc",
                    "arguments": '{"cmd":"ls"}',
                }
            ]
        }
        wrapped = _wrap_reference_tool_ids_into_request(data, trace_id=trace_id)
        args = json.loads(wrapped["input"][0]["arguments"])
        assert args["depends_on"] == [_legacy_depends_on_entry("call_prev")]
        assert args["cmd"] == "ls"
    finally:
        with _stripped_categories_lock:
            _stripped_reference_tool_ids_by_trace.pop(trace_id, None)


def test_strip_and_record_depends_ons_from_message():
    trace_id = "trace-strip-codex-test"
    with _stripped_categories_lock:
        _stripped_reference_tool_ids_by_trace.pop(trace_id, None)
    message = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_xyz",
                "type": "function",
                "function": {
                    "name": "exec_command",
                    "arguments": json.dumps(
                        {
                            "cmd": "ls",
                            "depends_on": ["call_prev"],
                        }
                    ),
                },
            }
        ],
    }
    request_data = {"metadata": {"arbiteros_trace_id": trace_id}}
    _strip_and_record_tool_depends_on_from_message(message, request_data)
    stripped_args = json.loads(message["tool_calls"][0]["function"]["arguments"])
    assert "depends_on" not in stripped_args
    assert stripped_args["cmd"] == "ls"
    with _stripped_categories_lock:
        assert _stripped_reference_tool_ids_by_trace[trace_id]["call_xyz"] == [
            _legacy_depends_on_entry("call_prev")
        ]
        _stripped_reference_tool_ids_by_trace.pop(trace_id, None)


def _tool_schema_has_depends_on(tool: dict) -> bool:
    params = _resolve_tool_parameters_container(tool)
    if not isinstance(params, dict):
        return False
    props = params.get("properties")
    if not isinstance(props, dict):
        return False
    return "depends_on" in props


def test_inject_depends_on_codex_non_function_tools():
    data = {
        "model": "gpt-5.5",
        "input": [],
        "tools": [
            {
                "type": "tool_search",
                "execution": "client",
                "description": "search tools",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "custom",
                "name": "apply_patch",
                "description": "patch files",
                "format": {"type": "grammar", "syntax": "lark", "definition": "start: /x/"},
            },
            {
                "type": "web_search",
                "external_web_access": False,
                "search_content_types": ["text"],
            },
            {
                "type": "image_generation",
                "output_format": "png",
            },
        ],
    }
    _inject_tool_depends_on_into_tools(data)
    tools = data["tools"]

    tool_search_params = tools[0]["parameters"]
    assert "depends_on" in tool_search_params["properties"]
    assert "depends_on" in tool_search_params["required"]
    assert "query" in tool_search_params["properties"]

    assert "parameters" not in tools[1]
    assert "[arbiteros_depends_on]" in tools[1]["description"]
    assert "function_call_output" in tools[1]["description"]
    assert tools[1].get("format") is not None

    assert "parameters" not in tools[2]
    assert "description" not in tools[2]

    assert "parameters" not in tools[3]
    assert "description" not in tools[3]

    assert "[arbiteros_depends_on]" in data["instructions"]
    assert "function_call_output" in data["instructions"]


def test_inject_depends_on_all_codex_tools_from_precall_fixture():
    fixture = {
        "type": "function",
        "name": "exec_command",
        "parameters": {
            "type": "object",
            "properties": {"cmd": {"type": "string"}},
            "required": ["cmd"],
        },
    }
    custom = {
        "type": "custom",
        "name": "apply_patch",
        "description": "patch files",
        "format": {"type": "grammar", "syntax": "lark", "definition": "start: /x/"},
    }
    tool_search = {
        "type": "tool_search",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }
    web = {"type": "web_search", "search_content_types": ["text"]}
    image = {"type": "image_generation", "output_format": "png"}
    data = {"input": [], "tools": [fixture, custom, tool_search, web, image]}
    _inject_tool_depends_on_into_tools(data)

    assert _tool_schema_has_depends_on(data["tools"][0])
    assert _tool_schema_has_depends_on(data["tools"][2])
    assert "parameters" not in data["tools"][1]
    assert "parameters" not in data["tools"][3]
    assert "parameters" not in data["tools"][4]
    assert "[arbiteros_depends_on]" in data["tools"][1]["description"]
    assert "description" not in data["tools"][3]
    assert "description" not in data["tools"][4]
    assert "[arbiteros_depends_on]" in data["instructions"]


def test_strip_depends_on_uses_internal_trace_id_when_metadata_stripped():
    from arbiteros_kernel.litellm_callback import (
        _strip_and_record_tool_depends_on_from_message,
        _stripped_categories_lock,
        _stripped_reference_tool_ids_by_trace,
    )

    trace_id = "trace-strip-internal-id"
    with _stripped_categories_lock:
        _stripped_reference_tool_ids_by_trace.pop(trace_id, None)
    message = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_xyz",
                "type": "function",
                "function": {
                    "name": "exec_command",
                    "arguments": json.dumps(
                        {
                            "cmd": "ls",
                            "depends_on": [
                                {
                                    "instruction_id": "prev-id",
                                    "confidence": 0.9,
                                    "counterfactual": "needed",
                                }
                            ],
                        }
                    ),
                },
            }
        ],
    }
    request_data = {"_arbiteros_trace_id": trace_id}
    _strip_and_record_tool_depends_on_from_message(message, request_data)
    stripped_args = json.loads(message["tool_calls"][0]["function"]["arguments"])
    assert "depends_on" not in stripped_args
    with _stripped_categories_lock:
        stored = _stripped_reference_tool_ids_by_trace[trace_id]["call_xyz"]
        assert stored[0]["instruction_id"] == "prev-id"
        _stripped_reference_tool_ids_by_trace.pop(trace_id, None)


def test_extract_tool_results_from_responses_input():
    from arbiteros_kernel.litellm_callback import (
        _extract_tool_call_details_from_responses_input,
        _extract_tool_results_from_responses_input,
    )

    input_items = [
        {
            "type": "function_call",
            "name": "exec_command",
            "call_id": "call_read",
            "arguments": '{"cmd":"cat file.txt"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_read",
            "output": "menu content",
        },
    ]
    results = _extract_tool_results_from_responses_input(input_items)
    assert len(results) == 1
    assert results[0]["tool_call_id"] == "call_read"
    assert results[0]["content"] == "menu content"

    details = _extract_tool_call_details_from_responses_input(input_items)
    assert details["call_read"]["tool_name"] == "exec_command"
    assert details["call_read"]["tool_arguments"]["cmd"] == "cat file.txt"


def test_inject_responses_api_text_format_maps_schema():
    from arbiteros_kernel.litellm_callback import _inject_responses_api_text_format

    data = {
        "model": "gpt-5.5",
        "input": [{"type": "message", "role": "user", "content": "hi"}],
        "text": {"verbosity": "low"},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "instruction_output",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "category": {"type": "string"},
                        "content": {"type": "string"},
                        "depends_on": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "instruction_id": {"type": "string"},
                                    "confidence": {"type": "number"},
                                    "counterfactual": {"type": "string"},
                                },
                            },
                            "description": "catalog here",
                        },
                    },
                    "required": ["topic", "category", "content", "depends_on"],
                    "additionalProperties": False,
                },
            },
        },
    }
    _inject_responses_api_text_format(data)
    assert "response_format" not in data
    assert data["text"]["verbosity"] == "low"
    fmt = data["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["name"] == "instruction_output"
    assert fmt["strict"] is True
    assert fmt["schema"]["properties"]["depends_on"]["description"] == "catalog here"


def test_inject_responses_api_text_format_ignores_chat_completions():
    from arbiteros_kernel.litellm_callback import _inject_responses_api_text_format

    data = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": {"type": "json_schema", "json_schema": {"schema": {}}},
    }
    _inject_responses_api_text_format(data)
    assert "response_format" in data
    assert "format" not in (data.get("text") or {})


def test_wrap_responses_input_with_categories():
    from arbiteros_kernel.litellm_callback import (
        _stripped_categories_by_trace,
        _stripped_categories_lock,
        _stripped_text_depends_on_by_trace,
        _stripped_topics_by_trace,
        _wrap_responses_input_with_categories,
    )

    trace_id = "trace-wrap-responses-text"
    dep_slot = [
        {
            "instruction_id": "aaaa",
            "confidence": 0.8,
            "counterfactual": "needed",
        }
    ]
    with _stripped_categories_lock:
        _stripped_categories_by_trace[trace_id] = ["COGNITIVE_CORE__RESPOND"]
        _stripped_topics_by_trace[trace_id] = ["读取菜单"]
        _stripped_text_depends_on_by_trace[trace_id] = [dep_slot]
    try:
        data = {
            "model": "gpt-5.5",
            "input": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "文件内容是菜单"}],
                }
            ],
        }
        wrapped = _wrap_responses_input_with_categories(data, trace_id=trace_id)
        text = wrapped["input"][0]["content"][0]["text"]
        parsed = json.loads(text)
        assert parsed["category"] == "COGNITIVE_CORE__RESPOND"
        assert parsed["topic"] == "读取菜单"
        assert parsed["content"] == "文件内容是菜单"
        assert parsed["depends_on"] == dep_slot
    finally:
        with _stripped_categories_lock:
            _stripped_categories_by_trace.pop(trace_id, None)
            _stripped_topics_by_trace.pop(trace_id, None)
            _stripped_text_depends_on_by_trace.pop(trace_id, None)


def test_strip_and_record_depends_on_from_responses_output():
    trace_id = "trace-responses-output-strip"
    with _stripped_categories_lock:
        _stripped_reference_tool_ids_by_trace.pop(trace_id, None)
    message = {
        "id": "resp_123",
        "output": [
            {
                "type": "function_call",
                "call_id": "call_abc",
                "id": "fc_call_abc",
                "name": "terminal",
                "arguments": json.dumps(
                    {
                        "command": "pwd",
                        "depends_on": [
                            {
                                "instruction_id": "instr-prev",
                                "confidence": 0.9,
                                "counterfactual": "test",
                            }
                        ],
                    }
                ),
            }
        ],
    }
    request_data = {"metadata": {"arbiteros_trace_id": trace_id}}
    _strip_and_record_tool_depends_on_from_message(message, request_data)
    stripped_args = json.loads(message["output"][0]["arguments"])
    assert "depends_on" not in stripped_args
    assert stripped_args["command"] == "pwd"
    with _stripped_categories_lock:
        stored = _stripped_reference_tool_ids_by_trace[trace_id]["call_abc"]
        assert stored[0]["instruction_id"] == "instr-prev"
        _stripped_reference_tool_ids_by_trace.pop(trace_id, None)


def test_apply_canonical_writes_stripped_tool_args_to_responses_output():
    from arbiteros_kernel.protocol_adapter import apply_canonical_message_to_response

    response = {
        "id": "resp_1",
        "object": "response",
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "call_id": "call_abc",
                "id": "fc_call_abc",
                "name": "terminal",
                "arguments": json.dumps(
                    {
                        "command": "pwd",
                        "depends_on": [
                            {
                                "instruction_id": "instr-prev",
                                "confidence": 0.9,
                                "counterfactual": "test",
                            }
                        ],
                    }
                ),
            }
        ],
    }
    msg_dict = {
        "role": "assistant",
        "content": "running command",
        "tool_calls": [
            {
                "id": "call_abc",
                "type": "function",
                "function": {
                    "name": "terminal",
                    "arguments": json.dumps({"command": "pwd"}),
                },
            }
        ],
    }
    result = apply_canonical_message_to_response(
        response, msg_dict, is_chat_completion=False
    )
    assert isinstance(result, dict)
    stripped_args = json.loads(result["output"][0]["arguments"])
    assert stripped_args == {"command": "pwd"}
