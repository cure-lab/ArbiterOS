"""Unit tests for instruction depends_on resolution helpers."""

from arbiteros_kernel.instruction_depends_on import (
    DEFAULT_LEGACY_CONFIDENCE,
    DEFAULT_LEGACY_COUNTERFACTUAL,
    DEPENDS_ON_CAUSAL_RULES,
    DEPENDS_ON_COUNTERFACTUAL_RULES,
    KERNEL_TOOL_RESULT_CONFIDENCE,
    KERNEL_TOOL_RESULT_COUNTERFACTUAL,
    REF_KIND_LLMOUTPUT,
    REF_KIND_SYSTEMPROMPT,
    REF_KIND_TOOLCALL,
    REF_KIND_TOOLRESULT,
    REF_KIND_USERINPUT,
    REF_TYPE_INSTRUCTION_ID,
    REF_TYPE_RUNTIME_STEP,
    REF_TYPE_TOOL_CALL_ID,
    SOURCE_KERNEL,
    SOURCE_MODEL,
    build_allowed_depends_on_instruction_ids,
    build_depends_on_items_schema,
    build_depends_on_schema_description,
    build_step_catalog_with_previews,
    build_tool_depends_on_description,
    format_arbiteros_ref_marker,
    kernel_depends_on_tool_call,
    normalize_depends_on_declarations,
    resolve_depends_on_refs,
    strip_arbiteros_ref_markers,
    resolve_instruction_ids_to_depends_on,
    resolve_mixed_depends_on_refs,
    resolve_runtime_steps_to_depends_on,
    resolve_tool_call_ids_to_depends_on,
    strip_arbiteros_ref_marker,
)


def _expected_entry(**kwargs):
    base = {
        "confidence": DEFAULT_LEGACY_CONFIDENCE,
        "counterfactual": DEFAULT_LEGACY_COUNTERFACTUAL,
    }
    base.update(kwargs)
    return base


def _text_instr(instr_id: str, step: int, content: str = "hello") -> dict:
    return {
        "id": instr_id,
        "runtime_step": step,
        "instruction_type": "RESPOND",
        "arbiteros_ref_kind": REF_KIND_LLMOUTPUT,
        "content": content,
    }


def _tool_instr(instr_id: str, step: int, tc_id: str, *, with_result: bool = False) -> dict:
    content: dict = {
        "tool_name": "read",
        "tool_call_id": tc_id,
        "arguments": {"path": "/tmp/a"},
    }
    if with_result:
        content["result"] = {"ok": True}
    return {
        "id": instr_id,
        "runtime_step": step,
        "instruction_type": "EXEC",
        "arbiteros_ref_kind": REF_KIND_TOOLRESULT if with_result else REF_KIND_TOOLCALL,
        "content": content,
    }


def test_format_and_strip_arbiteros_ref_marker():
    marker = format_arbiteros_ref_marker("11111111-1111-1111-1111-111111111111", "USERINPUT")
    assert marker.startswith("[ARBITEROS_REF id=")
    assert strip_arbiteros_ref_marker(marker + "hello") == "hello"


def test_normalize_depends_on_declarations_accepts_objects_and_legacy_strings():
    raw = [
        {
            "instruction_id": "i1",
            "confidence": 0.9,
            "counterfactual": "Without i1, this step would not know the file path.",
        },
        "i2",
    ]
    decls = normalize_depends_on_declarations(raw)
    assert decls[0]["instruction_id"] == "i1"
    assert decls[0]["confidence"] == 0.9
    assert decls[1]["instruction_id"] == "i2"
    assert decls[1]["confidence"] == 0.0


def test_resolve_instruction_ids_preserves_confidence_and_counterfactual():
    instructions = [_text_instr("i1", 1), _text_instr("i2", 2)]
    resolved = resolve_instruction_ids_to_depends_on(
        instructions,
        [
            {
                "instruction_id": "i1",
                "confidence": 0.85,
                "counterfactual": "Without i1's error trace, this step would not target sanitization.",
            }
        ],
        current_runtime_step=3,
        trace_id="t1",
    )
    assert resolved == [
        _expected_entry(
            instruction_id="i1",
            ref="i1",
            ref_type=REF_TYPE_INSTRUCTION_ID,
            source=SOURCE_MODEL,
            confidence=0.85,
            counterfactual="Without i1's error trace, this step would not target sanitization.",
        )
    ]


def test_resolve_instruction_ids_skips_self_and_future():
    instructions = [_text_instr("i1", 1), _text_instr("i2", 2)]
    resolved = resolve_instruction_ids_to_depends_on(
        instructions,
        ["i1", "i2"],
        current_runtime_step=2,
        trace_id="t1",
    )
    assert resolved == [
        _expected_entry(
            instruction_id="i1",
            ref="i1",
            ref_type=REF_TYPE_INSTRUCTION_ID,
            source=SOURCE_MODEL,
        )
    ]


def test_resolve_runtime_steps_skips_self_and_future():
    instructions = [_text_instr("i1", 1), _text_instr("i2", 2)]
    resolved = resolve_runtime_steps_to_depends_on(
        instructions, [1, 2, 3], current_runtime_step=2, trace_id="t1"
    )
    assert resolved == [
        _expected_entry(
            instruction_id="i1",
            ref=1,
            ref_type=REF_TYPE_RUNTIME_STEP,
            source=SOURCE_MODEL,
        )
    ]


def test_resolve_tool_call_ids_prefers_result_instruction():
    instructions = [
        _tool_instr("call-only", 1, "tc1"),
        _tool_instr("call-with-result", 2, "tc1", with_result=True),
    ]
    resolved = resolve_tool_call_ids_to_depends_on(instructions, ["tc1"])
    assert resolved == [
        _expected_entry(
            instruction_id="call-with-result",
            ref="tc1",
            ref_type=REF_TYPE_TOOL_CALL_ID,
            source=SOURCE_MODEL,
        )
    ]


def test_kernel_depends_on_tool_call_targets_call_without_result():
    instructions = [
        _tool_instr("call-only", 1, "tc1"),
        _tool_instr("call-with-result", 2, "tc1", with_result=True),
    ]
    resolved = kernel_depends_on_tool_call(instructions, "tc1")
    assert resolved == [
        _expected_entry(
            instruction_id="call-only",
            ref="call-only",
            ref_type=REF_TYPE_INSTRUCTION_ID,
            source=SOURCE_KERNEL,
            confidence=KERNEL_TOOL_RESULT_CONFIDENCE,
            counterfactual=KERNEL_TOOL_RESULT_COUNTERFACTUAL,
        )
    ]


def test_build_step_catalog_with_previews():
    instructions = [
        _text_instr("i1", 1, "Hello world this is a long preview that should truncate"),
        _tool_instr("i2", 2, "call_abc123456789"),
    ]
    catalog = build_step_catalog_with_previews(instructions, text_preview_chars=20)
    assert "Prior steps" in catalog
    assert "LLMOUTPUT" in catalog
    assert "Hello world this is…" in catalog
    assert "TOOLCALL" in catalog


def test_build_depends_on_schema_description_lists_allowed_ids():
    instructions = [_text_instr("i1", 1), _tool_instr("i2", 2, "tc1")]
    desc = build_depends_on_schema_description(instructions, current_runtime_step=3)
    assert "Allowed instruction ids" in desc
    assert "i1" in desc
    assert DEPENDS_ON_CAUSAL_RULES.split(".")[0] in desc
    assert "confidence" in desc.lower() or "counterfactual" in desc.lower()


def test_build_allowed_depends_on_instruction_ids_excludes_future():
    instructions = [_text_instr("i1", 1), _text_instr("i2", 2)]
    allowed = build_allowed_depends_on_instruction_ids(
        instructions, current_runtime_step=2
    )
    assert allowed == ["i1"]


def test_build_depends_on_items_schema_object_with_enum():
    instructions = [_text_instr("i1", 1), _text_instr("i2", 2)]
    items = build_depends_on_items_schema(instructions, current_runtime_step=3)
    assert items["type"] == "object"
    assert items["properties"]["instruction_id"]["enum"] == ["i1", "i2"]
    assert "confidence" in items["properties"]
    assert "counterfactual" in items["properties"]
    assert items["required"] == ["instruction_id", "confidence", "counterfactual"]


def test_resolve_depends_on_refs_accepts_instruction_ids_and_legacy():
    instructions = [
        _text_instr("i1", 1),
        _tool_instr("call-only", 2, "tc1"),
        _tool_instr("call-with-result", 3, "tc1", with_result=True),
    ]
    resolved = resolve_depends_on_refs(
        instructions,
        ["i1", "step:1", "tc1"],
        current_runtime_step=4,
        trace_id="t1",
    )
    assert len(resolved) == 2
    refs = {(e["ref_type"], e["instruction_id"]) for e in resolved}
    assert (REF_TYPE_INSTRUCTION_ID, "i1") in refs
    assert (REF_TYPE_TOOL_CALL_ID, "call-with-result") in refs


def test_resolve_mixed_depends_on_refs_accepts_step_prefix_and_tool_ids():
    instructions = [
        _text_instr("i1", 1),
        _tool_instr("call-only", 2, "tc1"),
        _tool_instr("call-with-result", 3, "tc1", with_result=True),
    ]
    resolved = resolve_mixed_depends_on_refs(
        instructions,
        ["step:1", "tc1"],
        current_runtime_step=4,
        trace_id="t1",
    )
    assert len(resolved) == 2
    refs = {(e["ref_type"], e["ref"]) for e in resolved}
    assert (REF_TYPE_RUNTIME_STEP, 1) in refs
    assert (REF_TYPE_TOOL_CALL_ID, "tc1") in refs


def test_build_tool_depends_on_description_mentions_ref_markers():
    instructions = [_text_instr("i1", 1, "Plan the fix")]
    desc = build_tool_depends_on_description([], instructions, current_runtime_step=2)
    assert "ARBITEROS_REF" in desc
    assert "Allowed ids" in desc
    assert "DIRECT causal" in desc
    assert DEPENDS_ON_COUNTERFACTUAL_RULES.split(".")[0] in desc


def test_context_instruction_kind_constants():
    system = {
        "id": "s1",
        "runtime_step": 1,
        "instruction_type": REF_KIND_SYSTEMPROMPT,
        "arbiteros_ref_kind": REF_KIND_SYSTEMPROMPT,
        "content": "You are helpful.",
    }
    assert system["arbiteros_ref_kind"] == REF_KIND_SYSTEMPROMPT


def test_set_instruction_depends_on_strips_raw_depends_on_from_tool_arguments():
    from arbiteros_kernel.instruction_parsing.builder import InstructionBuilder
    from arbiteros_kernel.litellm_callback import _set_instruction_depends_on

    builder = InstructionBuilder(trace_id="depends-on-strip")
    system = builder.add_from_context_message(
        ref_kind=REF_KIND_SYSTEMPROMPT,
        content="system",
        context_key="system:0",
    )
    decl = {
        "instruction_id": system["id"],
        "confidence": 0.95,
        "counterfactual": "Without the system prompt, this tool call would lack scope constraints.",
    }
    instr = builder.add_from_tool_call(
        tool_name="terminal",
        tool_call_id="call_1",
        arguments={
            "command": "ls",
            "depends_on": [decl],
        },
    )
    _set_instruction_depends_on(
        builder,
        instr,
        tool_depends_on_raw=[decl],
        trace_id="depends-on-strip",
    )
    assert instr["depends_on"] == [
        _expected_entry(
            instruction_id=system["id"],
            ref=system["id"],
            ref_type=REF_TYPE_INSTRUCTION_ID,
            source=SOURCE_MODEL,
            confidence=0.95,
            counterfactual=decl["counterfactual"],
        )
    ]
    assert "depends_on" not in instr["content"]["arguments"]


def test_strip_arbiteros_ref_markers_removes_leading_watermarks():
    marker = format_arbiteros_ref_marker(
        "2803a5d1-671d-438b-8c5f-3c5d8bc15be4", REF_KIND_LLMOUTPUT
    )
    tool_marker = format_arbiteros_ref_marker(
        "b9179655-b172-46a8-9c17-abd17c79408b", "TOOLRESULT"
    )
    assert strip_arbiteros_ref_markers(marker) == ""
    assert (
        strip_arbiteros_ref_markers(
            tool_marker + "\n\n已读取文件内容。"
        )
        == "已读取文件内容。"
    )
    assert (
        strip_arbiteros_ref_markers(marker + "已完成！")
        == "已完成！"
    )

