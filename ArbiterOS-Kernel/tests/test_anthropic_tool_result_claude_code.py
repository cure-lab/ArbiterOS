"""Claude Code: Anthropic tool_result -> TOOLRESULT instruction (read-only pre_call emit)."""

from arbiteros_kernel.instruction_parsing.builder import InstructionBuilder
from arbiteros_kernel.litellm_callback import (
    _TraceState,
    _emitted_tool_result_call_ids_by_trace,
    _emit_tool_result_nodes_if_needed,
    _extract_anthropic_tool_results_from_messages,
    _extract_tool_call_details_by_call_id,
    _extract_tool_results,
)


def test_extract_anthropic_tool_results_from_user_content_blocks():
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_01ABC",
                    "content": "file contents here",
                }
            ],
        }
    ]
    results = _extract_anthropic_tool_results_from_messages(messages)
    assert len(results) == 1
    assert results[0]["tool_call_id"] == "toolu_01ABC"
    assert results[0]["content"] == "file contents here"


def test_extract_tool_results_openclaw_role_tool_unchanged():
    messages = [
        {
            "role": "tool",
            "tool_call_id": "call_openclaw",
            "content": "stdout",
        }
    ]
    assert _extract_anthropic_tool_results_from_messages(messages) == []
    results = _extract_tool_results(messages)
    assert len(results) == 1
    assert results[0]["tool_call_id"] == "call_openclaw"


def test_extract_tool_call_details_includes_anthropic_tool_use():
    messages = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_read",
                    "name": "Read",
                    "input": {"file_path": "/tmp/menu.txt"},
                }
            ],
        }
    ]
    details = _extract_tool_call_details_by_call_id(messages)
    assert details["toolu_read"]["tool_name"] == "Read"
    assert details["toolu_read"]["tool_arguments"]["file_path"] == "/tmp/menu.txt"


def test_emit_tool_result_nodes_if_needed_anthropic_creates_toolresult():
    trace_id = "trace-anthropic-tool-result"
    builder = InstructionBuilder(trace_id=trace_id)
    builder.add_from_tool_call(
        tool_name="Read",
        tool_call_id="toolu_01ABC",
        arguments={"file_path": "/tmp/menu.txt"},
    )

    from arbiteros_kernel import litellm_callback as cb

    original_get = cb._get_instruction_builder_for_trace
    original_save = cb._save_instructions_to_trace_file
    original_emit = cb._emit_langfuse_node
    cb._get_instruction_builder_for_trace = lambda _tid: builder
    cb._save_instructions_to_trace_file = lambda *_args, **_kwargs: None
    cb._emit_langfuse_node = lambda **_kwargs: None
    _emitted_tool_result_call_ids_by_trace.pop(trace_id, None)
    state = _TraceState(
        trace_id=trace_id,
        device_key="dev",
        channel="ch",
        user_id="u1",
    )
    request_data = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_01ABC",
                        "name": "Read",
                        "input": {"file_path": "/tmp/menu.txt"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_01ABC",
                        "content": "1\tbreakfast",
                    }
                ],
            },
        ]
    }
    try:
        _emit_tool_result_nodes_if_needed(request_data, state)
    finally:
        cb._get_instruction_builder_for_trace = original_get
        cb._save_instructions_to_trace_file = original_save
        cb._emit_langfuse_node = original_emit

    assert len(builder.instructions) == 2
    result_instr = builder.instructions[-1]
    assert result_instr["arbiteros_ref_kind"] == "TOOLRESULT"
    assert result_instr["instruction_type"] == "READ"
    assert result_instr["content"]["tool_call_id"] == "toolu_01ABC"
    assert result_instr["content"]["result"]["raw"] == "1\tbreakfast"


def test_emit_tool_result_resolves_tool_name_from_builder_when_history_stripped():
    trace_id = "trace-anthropic-tool-name"
    builder = InstructionBuilder(trace_id=trace_id)
    builder.add_from_tool_call(
        tool_name="Read",
        tool_call_id="toolu_01ABC",
        arguments={"file_path": "/tmp/menu.txt"},
    )

    from arbiteros_kernel import litellm_callback as cb

    original_get = cb._get_instruction_builder_for_trace
    original_save = cb._save_instructions_to_trace_file
    original_emit = cb._emit_langfuse_node
    cb._get_instruction_builder_for_trace = lambda _tid: builder
    cb._save_instructions_to_trace_file = lambda *_args, **_kwargs: None
    cb._emit_langfuse_node = lambda **_kwargs: None
    _emitted_tool_result_call_ids_by_trace.pop(trace_id, None)
    state = _TraceState(
        trace_id=trace_id,
        device_key="dev",
        channel="ch",
        user_id="u1",
    )
    request_data = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_01ABC",
                        "content": "menu text",
                    }
                ],
            }
        ]
    }
    try:
        _emit_tool_result_nodes_if_needed(request_data, state)
    finally:
        cb._get_instruction_builder_for_trace = original_get
        cb._save_instructions_to_trace_file = original_save
        cb._emit_langfuse_node = original_emit

    result_instr = builder.instructions[-1]
    assert result_instr["arbiteros_ref_kind"] == "TOOLRESULT"
    assert result_instr["instruction_type"] == "READ"
    assert result_instr["content"]["tool_name"] == "Read"
