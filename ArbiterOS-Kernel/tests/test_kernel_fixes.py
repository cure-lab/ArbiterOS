"""Tests for response_format injection and strict content unwrap."""

import json

from arbiteros_kernel.instruction_parsing.builder import InstructionBuilder
from arbiteros_kernel.litellm_callback import (
    _add_instructions_from_modified_response,
    _ensure_kernel_response_format,
    _extract_strict_topic_category_payload,
    _inject_depends_on_schema_into_response_format,
    _inject_ref_markers_into_messages,
    _inject_ref_markers_into_responses_input,
    _lookup_response_format_from_litellm_config,
)


def test_lookup_response_format_from_litellm_config():
    rf = _lookup_response_format_from_litellm_config("gpt-5.5")
    assert isinstance(rf, dict)
    schema = rf.get("json_schema", {}).get("schema", {})
    assert "depends_on" in schema.get("properties", {})


def test_ensure_kernel_response_format_injects_from_config():
    data: dict = {"model": "gpt-5.5"}
    _ensure_kernel_response_format(data)
    assert isinstance(data.get("response_format"), dict)
    props = (
        data["response_format"]
        .get("json_schema", {})
        .get("schema", {})
        .get("properties", {})
    )
    assert "depends_on" in props


def test_inject_depends_on_schema_into_response_format():
    data = {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "instruction_output",
                "schema": {
                    "type": "object",
                    "properties": {
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "placeholder",
                        }
                    },
                },
            },
        }
    }
    _inject_depends_on_schema_into_response_format(data, trace_id="test-trace")
    dep = data["response_format"]["json_schema"]["schema"]["properties"]["depends_on"]
    assert "ARBITEROS_REF" in dep["description"]
    assert dep["items"]["type"] == "object"
    assert "confidence" in dep["items"]["properties"]
    assert "counterfactual" in dep["items"]["properties"]


def test_inject_responses_toolresult_ref_uses_toolresult_instruction_id():
    """function_call_output ARBITEROS_REF id must be TOOLRESULT uuid, not TOOLCALL."""
    from arbiteros_kernel.litellm_callback import (
        _TraceState,
        _emitted_tool_result_call_ids_by_trace,
        _emit_tool_result_nodes_if_needed,
        _inject_ref_markers_into_responses_input,
    )

    trace_id = "trace-toolresult-ref"
    builder = InstructionBuilder(trace_id=trace_id)
    builder.add_from_tool_call(
        tool_name="terminal",
        tool_call_id="call_abc",
        arguments={"command": "ls"},
        result=None,
    )
    call_instr_id = builder.instructions[0]["id"]
    assert builder.instructions[0]["arbiteros_ref_kind"] == "TOOLCALL"

    data = {
        "model": "gpt-5",
        "input": [
            {
                "type": "function_call",
                "call_id": "call_abc",
                "name": "terminal",
                "arguments": '{"command":"ls"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_abc",
                "output": "file_a.py\nfile_b.py",
            },
        ],
    }
    from arbiteros_kernel import litellm_callback as cb

    original_get = cb._get_instruction_builder_for_trace
    original_save = cb._save_instructions_to_trace_file
    cb._get_instruction_builder_for_trace = lambda _tid: builder
    cb._save_instructions_to_trace_file = lambda *_args, **_kwargs: None
    _emitted_tool_result_call_ids_by_trace.pop(trace_id, None)
    state = _TraceState(trace_id=trace_id, device_key="dev", channel="ch", user_id="u1")
    try:
        _emit_tool_result_nodes_if_needed(data, state)
        out = _inject_ref_markers_into_responses_input(data, trace_id=trace_id)
    finally:
        cb._get_instruction_builder_for_trace = original_get
        cb._save_instructions_to_trace_file = original_save

    result_instrs = [
        i for i in builder.instructions if i.get("arbiteros_ref_kind") == "TOOLRESULT"
    ]
    assert len(result_instrs) == 1
    result_id = result_instrs[0]["id"]
    assert result_id != call_instr_id
    output = out["input"][1]["output"]
    assert output.startswith(f"[ARBITEROS_REF id={result_id} kind=TOOLRESULT]")
    assert call_instr_id not in output.split("\n", 1)[0]
    # Stored result body stays clean; REF is wire-only (prompt-cache stable prefix).
    raw = result_instrs[0]["content"]["result"]["raw"]
    assert not raw.startswith("[ARBITEROS_REF")
    assert "file_a.py" in raw
    assert call_instr_id not in raw.split("\n", 1)[0]


def test_inject_ref_markers_into_messages_adds_system_and_user_refs():
    builder = InstructionBuilder(trace_id="trace-ref-test")
    data = {
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ],
    }
    from arbiteros_kernel import litellm_callback as cb

    original_get = cb._get_instruction_builder_for_trace
    cb._get_instruction_builder_for_trace = lambda _tid: builder
    try:
        out = _inject_ref_markers_into_messages(data, trace_id="trace-ref-test")
    finally:
        cb._get_instruction_builder_for_trace = original_get

    system_content = out["messages"][0]["content"]
    user_content = out["messages"][1]["content"]
    assert system_content.startswith("[ARBITEROS_REF id=")
    assert "kind=SYSTEMPROMPT]" in system_content
    assert user_content.startswith("[ARBITEROS_REF id=")
    assert "kind=USERINPUT]" in user_content
    assert len(builder.instructions) == 2


def test_inject_ref_markers_into_responses_input_adds_system_and_user_refs():
    builder = InstructionBuilder(trace_id="trace-responses-ref-test")
    data = {
        "model": "gpt-5",
        "instructions": "You are Codex, a coding agent.",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Hello"}],
            },
        ],
    }
    from arbiteros_kernel import litellm_callback as cb

    original_get = cb._get_instruction_builder_for_trace
    cb._get_instruction_builder_for_trace = lambda _tid: builder
    try:
        out = _inject_ref_markers_into_responses_input(
            data, trace_id="trace-responses-ref-test"
        )
    finally:
        cb._get_instruction_builder_for_trace = original_get

    instructions_text = out["instructions"]
    user_text = out["input"][0]["content"][0]["text"]
    assert instructions_text.startswith("[ARBITEROS_REF id=")
    assert "kind=SYSTEMPROMPT]" in instructions_text
    assert "You are Codex, a coding agent." in instructions_text
    assert user_text.startswith("[ARBITEROS_REF id=")
    assert "kind=USERINPUT]" in user_text
    assert len(builder.instructions) == 2
    assert builder.instructions[0]["instruction_type"] == "SYSTEMPROMPT"
    assert builder.instructions[0]["context_key"] == "system:0"
    assert builder.instructions[1]["instruction_type"] == "USERINPUT"


def test_add_instructions_from_modified_response_orders_respond_before_toolcall():
    """Post-transform payload: unwrapped text + tool_calls (policy commit path)."""
    builder = InstructionBuilder(trace_id="order-test")
    response = {
        "content": "I will inspect the repository next.",
        "tool_calls": [
            {
                "id": "call_order_test",
                "type": "function",
                "function": {
                    "name": "terminal",
                    "arguments": json.dumps(
                        {
                            "command": "pwd",
                            "security_risk": "LOW",
                            "summary": "Print working directory",
                        }
                    ),
                },
            }
        ],
    }
    _add_instructions_from_modified_response(
        builder,
        response,
        resolve_text_depends_on=False,
    )
    assert len(builder.instructions) == 2
    assert builder.instructions[0]["arbiteros_ref_kind"] == "LLMOUTPUT"
    assert builder.instructions[0]["instruction_type"] == "RESPOND"
    assert builder.instructions[1]["arbiteros_ref_kind"] == "TOOLCALL"
    assert builder.instructions[1]["parent_id"] == builder.instructions[0]["id"]


def test_extract_strict_topic_category_payload_with_thinking_prefix():
    content = (
        "<think>internal</think>"
        '{"topic":"t","category":"COGNITIVE_CORE__RESPOND","content":"ok","depends_on":[]}'
    )
    parsed = _extract_strict_topic_category_payload(content)
    assert isinstance(parsed, dict)
    assert parsed.get("content") == "ok"
    assert parsed.get("depends_on") == []
