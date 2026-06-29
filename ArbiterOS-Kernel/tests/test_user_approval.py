from arbiteros_kernel.instruction_parsing.types import compute_prop_taint_for_instruction
from arbiteros_kernel.user_approval import (
    _get_reference_tool_ids,
    apply_user_approval_preprocessing,
)


def _tool_instruction(tool_call_id: str, arguments, *, approved: bool = False) -> dict:
    instr = {
        "instruction_type": "WRITE",
        "content": {
            "tool_name": "gmail__send_email",
            "tool_call_id": tool_call_id,
            "arguments": arguments,
        },
        "security_type": {
            "trustworthiness": "HIGH",
            "confidentiality": "LOW",
            "prop_trustworthiness": "HIGH",
            "prop_confidentiality": "LOW",
        },
    }
    if approved:
        instr["user_approved"] = True
    return instr


def test_get_reference_tool_ids_accepts_json_string_arguments():
    instr = _tool_instruction(
        "call-1",
        '{"reference_tool_id":["read-1"," read-2 ",""]}',
    )

    assert _get_reference_tool_ids(instr) == ["read-1", "read-2"]


def test_get_reference_tool_ids_ignores_non_dict_arguments():
    assert _get_reference_tool_ids(_tool_instruction("call-1", "not-json")) == []
    assert _get_reference_tool_ids(_tool_instruction("call-1", None)) == []
    assert _get_reference_tool_ids(_tool_instruction("call-1", ["bad"])) == []


def test_user_approval_preprocessing_handles_json_string_arguments():
    source = _tool_instruction("read-1", {})
    current = _tool_instruction(
        "call-1",
        '{"to":"vp.sales@company.com","reference_tool_id":["read-1"]}',
    )

    instructions_for_policy, latest_for_policy = apply_user_approval_preprocessing(
        instructions=[source, current],
        latest_instructions=[current],
    )

    assert len(instructions_for_policy) == 2
    assert len(latest_for_policy) == 1


def test_compute_prop_taint_handles_json_string_arguments():
    source = _tool_instruction("read-1", {})
    source["security_type"]["prop_trustworthiness"] = "LOW"
    source["security_type"]["prop_confidentiality"] = "HIGH"
    current = _tool_instruction(
        "call-1",
        '{"reference_tool_id":["read-1"]}',
    )

    taint = compute_prop_taint_for_instruction([source, current], current)

    assert taint.trustworthiness == "LOW"
    assert taint.confidentiality == "HIGH"
