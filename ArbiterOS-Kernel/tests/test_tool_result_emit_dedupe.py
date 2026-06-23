"""Tests for tool-result instruction emit deduplication."""

from arbiteros_kernel.instruction_depends_on import (
    builder_has_tool_result_for_call_id,
    find_tool_result_instruction_for_call_id,
    format_arbiteros_ref_marker,
)
from arbiteros_kernel.instruction_parsing.builder import InstructionBuilder
from arbiteros_kernel.litellm_callback import (
    _TraceState,
    _build_ref_marker_maps,
    _emitted_tool_result_call_ids_by_trace,
    _emit_tool_result_nodes_if_needed,
    _normalize_tool_result_content_for_dedupe,
    _should_emit_tool_result_once,
)


def _tool_instr(instr_id: str, step: int, tc_id: str, *, with_result: bool = False) -> dict:
    content: dict = {
        "tool_name": "read",
        "tool_call_id": tc_id,
        "arguments": {"path": "/tmp/a"},
    }
    if with_result:
        content["result"] = {"raw": "menu data"}
    return {
        "id": instr_id,
        "runtime_step": step,
        "instruction_type": "READ",
        "arbiteros_ref_kind": "TOOLRESULT" if with_result else "TOOLCALL",
        "content": content,
    }


def test_find_tool_result_instruction_for_call_id_returns_first_with_result():
    instructions = [
        _tool_instr("call-only", 1, "tc1"),
        _tool_instr("result-one", 2, "tc1", with_result=True),
        _tool_instr("result-dup", 3, "tc1", with_result=True),
    ]
    found = find_tool_result_instruction_for_call_id(instructions, "tc1")
    assert found is not None
    assert found["id"] == "result-one"
    assert builder_has_tool_result_for_call_id(instructions, "tc1")


def test_normalize_tool_result_content_strips_marker_and_taint():
    marker = format_arbiteros_ref_marker("11111111-1111-1111-1111-111111111111", "TOOLRESULT")
    raw = (
        "[ARBITEROS_TAINT trustworthiness=UNKNOWN confidentiality=UNKNOWN]\n"
        f"{marker}hello world"
    )
    assert _normalize_tool_result_content_for_dedupe(raw) == "hello world"


def test_should_emit_tool_result_once_dedupes_by_tool_call_id_despite_marker_change():
    state = _TraceState(
        trace_id="trace-dedupe-test",
        device_key="dev",
        channel="ch",
        user_id="u1",
    )
    _emitted_tool_result_call_ids_by_trace.pop("trace-dedupe-test", None)
    marker_a = format_arbiteros_ref_marker(
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "TOOLCALL"
    )
    marker_b = format_arbiteros_ref_marker(
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "TOOLRESULT"
    )
    payload_a = {
        "tool_call_id": "call_123",
        "tool_name": "read",
        "content": marker_a + "same body",
    }
    payload_b = {
        "tool_call_id": "call_123",
        "tool_name": "read",
        "content": marker_b + "same body",
    }
    assert _should_emit_tool_result_once(state, payload_a) is True
    assert _should_emit_tool_result_once(state, payload_b) is False


def test_build_ref_marker_maps_prefers_first_tool_result_instruction():
    instructions = [
        _tool_instr("call-only", 1, "tc1"),
        _tool_instr("result-one", 2, "tc1", with_result=True),
        _tool_instr("result-two", 3, "tc1", with_result=True),
    ]
    _, _, _, tool_result_id_to_instr_id = _build_ref_marker_maps(instructions)
    assert tool_result_id_to_instr_id["tc1"] == "result-one"


def test_emit_tool_result_nodes_skips_when_builder_already_has_result():
    trace_id = "trace-emit-skip"
    builder = InstructionBuilder(trace_id=trace_id)
    builder.add_from_tool_call(
        tool_name="read",
        tool_call_id="call_FPqalZI",
        arguments={"path": "/tmp/menu.txt"},
        result={"raw": "menu"},
    )
    assert len(builder.instructions) == 1

    from arbiteros_kernel import litellm_callback as cb

    original_get = cb._get_instruction_builder_for_trace
    original_save = cb._save_instructions_to_trace_file
    cb._get_instruction_builder_for_trace = lambda _tid: builder
    cb._save_instructions_to_trace_file = lambda *_args, **_kwargs: None
    _emitted_tool_result_call_ids_by_trace.pop(trace_id, None)
    state = _TraceState(
        trace_id=trace_id,
        device_key="dev",
        channel="ch",
        user_id="u1",
    )
    marker = format_arbiteros_ref_marker(builder.instructions[0]["id"], "TOOLRESULT")
    request_data = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_FPqalZI",
                        "type": "function",
                        "function": {
                            "name": "read",
                            "arguments": '{"path":"/tmp/menu.txt"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_FPqalZI",
                "content": marker + "menu",
            },
        ]
    }
    try:
        _emit_tool_result_nodes_if_needed(request_data, state)
    finally:
        cb._get_instruction_builder_for_trace = original_get
        cb._save_instructions_to_trace_file = original_save

    assert len(builder.instructions) == 1
