"""Stable prompt-prefix mode: turn-dynamic catalogs go in trailing turn_context."""

from arbiteros_kernel.instruction_depends_on import (
    TURN_CONTEXT_MARKER,
    build_depends_on_items_schema,
    build_depends_on_schema_description,
    build_tool_depends_on_description,
    is_kernel_control_plane_text,
)
from arbiteros_kernel.instruction_parsing.builder import InstructionBuilder
from arbiteros_kernel.litellm_callback import (
    _TraceState,
    _DeviceContext,
    _inject_depends_on_schema_into_response_format,
    _inject_tool_depends_on_into_tools,
    _inject_topic_summary_hint,
    _inject_turn_context_trailer,
    _prompt_cache_stable_prefix_enabled,
)
from arbiteros_kernel.protocol_adapter import (
    append_trailing_control_message,
    extract_all_user_messages_from_request,
)


def _tool_instr(instr_id: str, step: int) -> dict:
    return {
        "id": instr_id,
        "runtime_step": step,
        "instruction_type": "USERINPUT",
        "arbiteros_ref_kind": "USERINPUT",
        "content": "hi",
    }


def test_stable_prefix_enabled_by_default(monkeypatch):
    monkeypatch.delenv("ARBITEROS_PROMPT_CACHE_STABLE_PREFIX", raising=False)
    assert _prompt_cache_stable_prefix_enabled() is True
    monkeypatch.setenv("ARBITEROS_PROMPT_CACHE_STABLE_PREFIX", "0")
    assert _prompt_cache_stable_prefix_enabled() is False


def test_schema_builders_can_omit_turn_varying_enum():
    instructions = [_tool_instr("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", 1)]
    with_enum = build_depends_on_items_schema(instructions, current_runtime_step=2)
    without = build_depends_on_items_schema(
        instructions, current_runtime_step=2, include_allowed_id_enum=False
    )
    assert "enum" in with_enum["properties"]["instruction_id"]
    assert "enum" not in without["properties"]["instruction_id"]
    desc = build_depends_on_schema_description(
        instructions, current_runtime_step=2, include_allowed_id_catalog=False
    )
    assert TURN_CONTEXT_MARKER in desc
    assert "Allowed instruction ids for this turn:" not in desc


def test_extract_user_messages_skips_kernel_control_plane():
    data = {
        "messages": [
            {"role": "user", "content": "real user"},
            {
                "role": "user",
                "content": f"{TURN_CONTEXT_MARKER}\nshould not count",
            },
            {"role": "system", "content": f"{TURN_CONTEXT_MARKER}\nsystem ctrl"},
        ]
    }
    assert extract_all_user_messages_from_request(data) == ["real user"]
    assert is_kernel_control_plane_text(f"{TURN_CONTEXT_MARKER}\nx") is True


def test_append_trailing_control_message_chat_and_responses():
    chat = {
        "messages": [
            {"role": "system", "content": "base"},
            {"role": "user", "content": "hello"},
        ]
    }
    out = append_trailing_control_message(
        chat, content=f"{TURN_CONTEXT_MARKER}\nbody", marker=TURN_CONTEXT_MARKER
    )
    assert out["messages"][-1]["role"] == "system"
    assert out["messages"][-1]["content"].startswith(TURN_CONTEXT_MARKER)
    assert out["messages"][1]["role"] == "user"

    responses = {
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
    out2 = append_trailing_control_message(
        responses, content=f"{TURN_CONTEXT_MARKER}\nbody", marker=TURN_CONTEXT_MARKER
    )
    assert out2["instructions"] == "You are Codex."
    assert out2["input"][-1]["role"] == "developer"
    assert TURN_CONTEXT_MARKER in out2["input"][-1]["content"][0]["text"]
    # Idempotent replace
    out3 = append_trailing_control_message(
        out2, content=f"{TURN_CONTEXT_MARKER}\nbody2", marker=TURN_CONTEXT_MARKER
    )
    assert sum(
        1
        for i in out3["input"]
        if isinstance(i, dict)
        and i.get("role") == "developer"
        and TURN_CONTEXT_MARKER in str(i.get("content"))
    ) == 1


def test_stable_mode_tools_and_format_have_no_enum(monkeypatch):
    monkeypatch.setenv("ARBITEROS_PROMPT_CACHE_STABLE_PREFIX", "1")
    trace_id = "trace-stable-schema"
    builder = InstructionBuilder(trace_id=trace_id)
    builder.add_from_context_message(
        ref_kind="USERINPUT", content="hello", context_key="user:0"
    )
    import arbiteros_kernel.litellm_callback as cb

    original_get = cb._get_instruction_builder_for_trace
    cb._get_instruction_builder_for_trace = lambda _tid: builder
    try:
        data = {
            "model": "gpt-5.5",
            "input": [],
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "description": "run",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                }
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "instruction_output",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "object"},
                                "description": "placeholder",
                            }
                        },
                    },
                },
            },
        }
        _inject_tool_depends_on_into_tools(data, trace_id=trace_id)
        dep = data["tools"][0]["parameters"]["properties"]["depends_on"]
        assert "enum" not in dep["items"]["properties"]["instruction_id"]
        assert "Allowed ids for this turn:" not in dep["description"]
        assert TURN_CONTEXT_MARKER in dep["description"]

        _inject_depends_on_schema_into_response_format(data, trace_id=trace_id)
        rf_dep = data["response_format"]["json_schema"]["schema"]["properties"][
            "depends_on"
        ]
        assert "enum" not in rf_dep["items"]["properties"]["instruction_id"]
        assert "Allowed instruction ids for this turn:" not in rf_dep["description"]
    finally:
        cb._get_instruction_builder_for_trace = original_get


def test_turn_context_trailer_not_in_instructions_and_skips_legacy_topic(monkeypatch):
    monkeypatch.setenv("ARBITEROS_PROMPT_CACHE_STABLE_PREFIX", "1")
    trace_id = "trace-stable-trailer"
    builder = InstructionBuilder(trace_id=trace_id)
    builder.add_from_context_message(
        ref_kind="USERINPUT", content="probe", context_key="user:0"
    )
    state = _TraceState(
        trace_id=trace_id,
        device_key="dev",
        channel="codex",
        user_id="u1",
        latest_topic_summary="sandbox",
    )
    context = _DeviceContext(
        device_key="dev",
        channel="codex",
        user_id="u1",
        has_explicit_user_id=True,
        latest_user_text="cat probe.env",
        latest_user_fingerprint="fp",
        latest_user_message_count=1,
        reset_requested=False,
    )
    import arbiteros_kernel.litellm_callback as cb

    original_get = cb._get_instruction_builder_for_trace
    cb._get_instruction_builder_for_trace = lambda _tid: builder
    try:
        data = {
            "model": "gpt-5.5",
            "instructions": "You are Codex.",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "cat probe.env"}],
                }
            ],
        }
        # Legacy topic inject is a no-op in stable mode.
        after_topic = _inject_topic_summary_hint(data, state=state, context=context)
        assert after_topic["instructions"] == "You are Codex."
        assert "[arbiteros_topic_hint]" not in after_topic["instructions"]

        out = _inject_turn_context_trailer(
            after_topic, state=state, context=context, trace_id=trace_id
        )
        assert out["instructions"] == "You are Codex."
        trailer = out["input"][-1]
        assert trailer["role"] == "developer"
        text = trailer["content"][0]["text"]
        assert text.startswith(TURN_CONTEXT_MARKER)
        assert "Current summarized topic: sandbox" in text
        assert "Latest user turn: cat probe.env" in text
        assert builder.instructions[0]["id"] in text
        # Policy user extraction ignores trailer (developer) and control markers.
        users = extract_all_user_messages_from_request(out)
        assert users == ["cat probe.env"]
    finally:
        cb._get_instruction_builder_for_trace = original_get


def test_legacy_mode_still_puts_allowed_ids_in_tool_description(monkeypatch):
    monkeypatch.setenv("ARBITEROS_PROMPT_CACHE_STABLE_PREFIX", "0")
    instructions = [_tool_instr("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 1)]
    desc = build_tool_depends_on_description(
        [], instructions, current_runtime_step=2, include_allowed_id_catalog=True
    )
    assert "Allowed ids for this turn:" in desc
