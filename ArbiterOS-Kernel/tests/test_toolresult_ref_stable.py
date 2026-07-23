"""TOOLRESULT REF watermarks must be stable across turns (no TOOLCALL-id fallback)."""

from arbiteros_kernel.instruction_depends_on import (
    REF_KIND_TOOLRESULT,
    find_tool_result_instruction_for_call_id,
    strip_arbiteros_ref_marker,
)
from arbiteros_kernel.instruction_parsing.builder import InstructionBuilder
from arbiteros_kernel.litellm_callback import (
    _TraceState,
    _emitted_tool_result_call_ids_by_trace,
    _emit_tool_result_nodes_if_needed,
    _inject_ref_markers_into_messages,
    _inject_ref_markers_into_responses_input,
)


def _patch_builder(cb, builder):
    original_get = cb._get_instruction_builder_for_trace
    original_save = cb._save_instructions_to_trace_file
    cb._get_instruction_builder_for_trace = lambda _tid: builder
    cb._save_instructions_to_trace_file = lambda *_args, **_kwargs: None
    return original_get, original_save


def _restore_builder(cb, original_get, original_save):
    cb._get_instruction_builder_for_trace = original_get
    cb._save_instructions_to_trace_file = original_save


def test_inject_skips_tool_output_when_only_toolcall_exists_chat():
    """Hardening: never stamp kind=TOOLRESULT with a TOOLCALL id."""
    trace_id = "trace-ref-no-fallback-chat"
    builder = InstructionBuilder(trace_id=trace_id)
    call_instr = builder.add_from_tool_call(
        tool_name="exec_command",
        tool_call_id="call_stable_1",
        arguments={"cmd": "echo hi"},
    )
    assert call_instr["arbiteros_ref_kind"] != REF_KIND_TOOLRESULT

    data = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_stable_1",
                        "type": "function",
                        "function": {
                            "name": "exec_command",
                            "arguments": '{"cmd":"echo hi"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_stable_1",
                "content": "hi\n",
            },
        ]
    }
    from arbiteros_kernel import litellm_callback as cb

    original_get, original_save = _patch_builder(cb, builder)
    try:
        out = _inject_ref_markers_into_messages(data, trace_id=trace_id)
    finally:
        _restore_builder(cb, original_get, original_save)

    tool_content = out["messages"][1]["content"]
    assert not tool_content.startswith("[ARBITEROS_REF")
    assert tool_content == "hi\n"


def test_emit_then_inject_keeps_stable_toolresult_ref_across_turns_responses():
    """Correct precall order: emit TOOLRESULT, then inject — id must not flip."""
    trace_id = "trace-ref-stable-responses"
    builder = InstructionBuilder(trace_id=trace_id)
    call_instr = builder.add_from_tool_call(
        tool_name="exec_command",
        tool_call_id="call_LZgon_test",
        arguments={"cmd": "cat probe.env"},
    )
    call_only_id = call_instr["id"]

    request = {
        "model": "gpt-5.5",
        "instructions": "You are Codex.",
        "input": [
            {
                "type": "function_call",
                "call_id": "call_LZgon_test",
                "name": "exec_command",
                "arguments": '{"cmd":"cat probe.env"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call_LZgon_test",
                "output": (
                    "Chunk ID: 57c504\n"
                    "Process exited with code 0\n"
                    "Output:\nSANDBOX_PROBE=should_be_denied\n"
                ),
            },
        ],
    }
    state = _TraceState(
        trace_id=trace_id,
        device_key="dev",
        channel="codex",
        user_id="u1",
    )
    _emitted_tool_result_call_ids_by_trace.pop(trace_id, None)

    from arbiteros_kernel import litellm_callback as cb

    original_get, original_save = _patch_builder(cb, builder)
    try:
        # Turn A: first sight of tool output (emit then inject).
        _emit_tool_result_nodes_if_needed(request, state)
        out_a = _inject_ref_markers_into_responses_input(request, trace_id=trace_id)
        result_instr = find_tool_result_instruction_for_call_id(
            builder.instructions, "call_LZgon_test"
        )
        assert result_instr is not None
        result_id = result_instr["id"]
        assert result_id != call_only_id
        marker_a = out_a["input"][1]["output"]
        assert marker_a.startswith(f"[ARBITEROS_REF id={result_id} kind=TOOLRESULT]")
        assert call_only_id not in marker_a.split("\n", 1)[0]
        # Stored result body must stay clean (no premature REF baked in).
        stored_raw = result_instr["content"]["result"]["raw"]
        assert not stored_raw.startswith("[ARBITEROS_REF")
        assert "SANDBOX_PROBE=should_be_denied" in stored_raw

        # Turn B: same history replayed — REF id must be identical.
        request_b = {
            "model": "gpt-5.5",
            "instructions": "You are Codex.",
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_LZgon_test",
                    "name": "exec_command",
                    "arguments": '{"cmd":"cat probe.env"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_LZgon_test",
                    "output": (
                        "Chunk ID: 57c504\n"
                        "Process exited with code 0\n"
                        "Output:\nSANDBOX_PROBE=should_be_denied\n"
                    ),
                },
            ],
        }
        _emit_tool_result_nodes_if_needed(request_b, state)
        out_b = _inject_ref_markers_into_responses_input(request_b, trace_id=trace_id)
        marker_b = out_b["input"][1]["output"]
        assert marker_b.startswith(f"[ARBITEROS_REF id={result_id} kind=TOOLRESULT]")
        assert strip_arbiteros_ref_marker(marker_a) == strip_arbiteros_ref_marker(
            marker_b
        )
        assert len(
            [
                i
                for i in builder.instructions
                if isinstance(i.get("content"), dict)
                and i["content"].get("tool_call_id") == "call_LZgon_test"
                and i["content"].get("result") is not None
            ]
        ) == 1
    finally:
        _restore_builder(cb, original_get, original_save)
        _emitted_tool_result_call_ids_by_trace.pop(trace_id, None)


def test_emit_then_inject_keeps_stable_toolresult_ref_chat():
    trace_id = "trace-ref-stable-chat"
    builder = InstructionBuilder(trace_id=trace_id)
    call_instr = builder.add_from_tool_call(
        tool_name="read",
        tool_call_id="call_chat_stable",
        arguments={"path": "/tmp/a"},
    )
    call_only_id = call_instr["id"]
    request = {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_chat_stable",
                        "type": "function",
                        "function": {
                            "name": "read",
                            "arguments": '{"path":"/tmp/a"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_chat_stable",
                "content": "file body",
            },
        ]
    }
    state = _TraceState(
        trace_id=trace_id,
        device_key="dev",
        channel="openclaw",
        user_id="u1",
    )
    _emitted_tool_result_call_ids_by_trace.pop(trace_id, None)
    from arbiteros_kernel import litellm_callback as cb

    original_get, original_save = _patch_builder(cb, builder)
    try:
        _emit_tool_result_nodes_if_needed(request, state)
        out = _inject_ref_markers_into_messages(request, trace_id=trace_id)
        result_instr = find_tool_result_instruction_for_call_id(
            builder.instructions, "call_chat_stable"
        )
        assert result_instr is not None
        result_id = result_instr["id"]
        assert result_id != call_only_id
        content = out["messages"][1]["content"]
        assert content.startswith(f"[ARBITEROS_REF id={result_id} kind=TOOLRESULT]")
        assert call_only_id not in content.split("\n", 1)[0]
    finally:
        _restore_builder(cb, original_get, original_save)
        _emitted_tool_result_call_ids_by_trace.pop(trace_id, None)
