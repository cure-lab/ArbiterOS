"""Unit tests for instruction depends_on resolution helpers."""

from arbiteros_kernel.instruction_depends_on import (
    REF_TYPE_RUNTIME_STEP,
    REF_TYPE_TOOL_CALL_ID,
    SOURCE_KERNEL,
    SOURCE_MODEL,
    build_depends_on_schema_description,
    build_step_catalog_with_previews,
    kernel_depends_on_tool_call,
    resolve_runtime_steps_to_depends_on,
    resolve_tool_call_ids_to_depends_on,
)


def _text_instr(instr_id: str, step: int, content: str = "hello") -> dict:
    return {
        "id": instr_id,
        "runtime_step": step,
        "instruction_type": "RESPOND",
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
        "content": content,
    }


def test_resolve_runtime_steps_skips_self_and_future():
    instructions = [_text_instr("i1", 1), _text_instr("i2", 2)]
    resolved = resolve_runtime_steps_to_depends_on(
        instructions, [1, 2, 3], current_runtime_step=2, trace_id="t1"
    )
    assert resolved == [
        {
            "instruction_id": "i1",
            "ref": 1,
            "ref_type": REF_TYPE_RUNTIME_STEP,
            "source": SOURCE_MODEL,
        }
    ]


def test_resolve_tool_call_ids_prefers_result_instruction():
    instructions = [
        _tool_instr("call-only", 1, "tc1"),
        _tool_instr("call-with-result", 2, "tc1", with_result=True),
    ]
    resolved = resolve_tool_call_ids_to_depends_on(instructions, ["tc1"])
    assert resolved == [
        {
            "instruction_id": "call-with-result",
            "ref": "tc1",
            "ref_type": REF_TYPE_TOOL_CALL_ID,
            "source": SOURCE_MODEL,
        }
    ]


def test_kernel_depends_on_tool_call_targets_call_without_result():
    instructions = [
        _tool_instr("call-only", 1, "tc1"),
        _tool_instr("call-with-result", 2, "tc1", with_result=True),
    ]
    resolved = kernel_depends_on_tool_call(instructions, "tc1")
    assert resolved == [
        {
            "instruction_id": "call-only",
            "ref": "tc1",
            "ref_type": REF_TYPE_TOOL_CALL_ID,
            "source": SOURCE_KERNEL,
        }
    ]


def test_build_step_catalog_with_previews():
    instructions = [
        _text_instr("i1", 1, "Hello world this is a long preview that should truncate"),
        _tool_instr("i2", 2, "call_abc123456789"),
    ]
    catalog = build_step_catalog_with_previews(instructions, text_preview_chars=20)
    assert "Step catalog" in catalog
    assert "1 TEXT" in catalog
    assert "Hello world this is…" in catalog
    assert "2 TOOL read | call_abc1234" in catalog


def test_build_depends_on_schema_description_includes_next_step():
    instructions = [_text_instr("i1", 1), _tool_instr("i2", 2, "tc1")]
    desc = build_depends_on_schema_description(instructions)
    assert "Next step number starts at: 3" in desc
    assert "Step catalog" in desc
