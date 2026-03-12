"""Unit tests for arbiteros_kernel.instruction_parsing.builder.InstructionBuilder."""

import json
import uuid

from arbiteros_kernel.instruction_parsing.builder import InstructionBuilder
from arbiteros_kernel.instruction_parsing.types import make_security_type

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def _simple_structured(intent: str = "REASON", content: str = "thinking") -> dict:
    return {"intent": intent, "content": content}


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestInstructionBuilderInit:
    def test_auto_trace_id_is_uuid(self):
        builder = InstructionBuilder()
        assert _is_uuid(builder.trace_id)

    def test_explicit_trace_id(self):
        builder = InstructionBuilder(trace_id="my-trace-123")
        assert builder.trace_id == "my-trace-123"

    def test_starts_with_empty_instructions(self):
        builder = InstructionBuilder()
        assert builder.instructions == []

    def test_runtime_step_starts_at_zero(self):
        builder = InstructionBuilder()
        assert builder._runtime_step == 0


# ---------------------------------------------------------------------------
# add_from_structured_output
# ---------------------------------------------------------------------------


class TestAddFromStructuredOutput:
    def test_returns_instruction_dict(self):
        builder = InstructionBuilder()
        instr = builder.add_from_structured_output(structured=_simple_structured())
        assert isinstance(instr, dict)

    def test_instruction_appended(self):
        builder = InstructionBuilder()
        builder.add_from_structured_output(structured=_simple_structured())
        assert len(builder.instructions) == 1

    def test_instruction_type_from_intent(self):
        builder = InstructionBuilder()
        instr = builder.add_from_structured_output(
            structured={"intent": "READ", "content": "reading file"}
        )
        assert instr["instruction_type"] == "READ"

    def test_instruction_type_defaults_to_reason_when_no_intent(self):
        builder = InstructionBuilder()
        instr = builder.add_from_structured_output(
            structured={"content": "no intent here"}
        )
        assert instr["instruction_type"] == "REASON"

    def test_instruction_category_from_type_map(self):
        builder = InstructionBuilder()
        instr = builder.add_from_structured_output(
            structured={"intent": "READ", "content": "..."}
        )
        assert instr["instruction_category"] == "EXECUTION.Env"

    def test_category_for_memory_store(self):
        builder = InstructionBuilder()
        instr = builder.add_from_structured_output(
            structured={"intent": "STORE", "content": "saving memory"}
        )
        assert instr["instruction_category"] == "MEMORY.Management"

    def test_category_for_cognitive_plan(self):
        builder = InstructionBuilder()
        instr = builder.add_from_structured_output(
            structured={"intent": "PLAN", "content": "planning"}
        )
        assert instr["instruction_category"] == "COGNITIVE.Reasoning"

    def test_runtime_step_auto_increments(self):
        builder = InstructionBuilder()
        i1 = builder.add_from_structured_output(structured=_simple_structured())
        i2 = builder.add_from_structured_output(structured=_simple_structured())
        assert i1["runtime_step"] == 1
        assert i2["runtime_step"] == 2

    def test_explicit_runtime_step(self):
        builder = InstructionBuilder()
        instr = builder.add_from_structured_output(
            structured=_simple_structured(), runtime_step=42
        )
        assert instr["runtime_step"] == 42

    def test_id_is_uuid(self):
        builder = InstructionBuilder()
        instr = builder.add_from_structured_output(structured=_simple_structured())
        assert _is_uuid(instr["id"])

    def test_each_instruction_has_unique_id(self):
        builder = InstructionBuilder()
        i1 = builder.add_from_structured_output(structured=_simple_structured())
        i2 = builder.add_from_structured_output(structured=_simple_structured())
        assert i1["id"] != i2["id"]

    def test_content_stored_verbatim(self):
        builder = InstructionBuilder()
        payload = {"intent": "REASON", "content": "hello world"}
        instr = builder.add_from_structured_output(structured=payload)
        assert instr["content"] == "hello world"

    def test_explicit_security_type(self):
        builder = InstructionBuilder()
        sec = make_security_type(
            confidentiality="HIGH",
            trustworthiness="LOW",
            confidence="UNKNOWN",
            reversible=False,
            authority="UNKNOWN",
        )
        instr = builder.add_from_structured_output(
            structured=_simple_structured(), security_type=sec
        )
        assert instr["security_type"] == sec

    def test_rule_types_stored(self):
        builder = InstructionBuilder()
        rules = [{"type": "allow"}]
        instr = builder.add_from_structured_output(
            structured=_simple_structured(), rule_types=rules
        )
        assert instr["rule_types"] == rules

    def test_rule_types_default_empty(self):
        builder = InstructionBuilder()
        instr = builder.add_from_structured_output(structured=_simple_structured())
        assert instr["rule_types"] == []


# ---------------------------------------------------------------------------
# Auto-linking: source_message_id and parent_id
# ---------------------------------------------------------------------------


class TestAutoLinking:
    def test_first_instruction_source_message_id_equals_own_id(self):
        builder = InstructionBuilder()
        i1 = builder.add_from_structured_output(structured=_simple_structured())
        assert i1["source_message_id"] == i1["id"]

    def test_second_instruction_source_message_id_equals_first_id(self):
        builder = InstructionBuilder()
        i1 = builder.add_from_structured_output(structured=_simple_structured())
        i2 = builder.add_from_structured_output(structured=_simple_structured())
        assert i2["source_message_id"] == i1["id"]

    def test_first_instruction_parent_id_is_none(self):
        builder = InstructionBuilder()
        i1 = builder.add_from_structured_output(structured=_simple_structured())
        assert i1["parent_id"] is None

    def test_second_instruction_auto_parent_is_first(self):
        builder = InstructionBuilder()
        i1 = builder.add_from_structured_output(structured=_simple_structured())
        i2 = builder.add_from_structured_output(structured=_simple_structured())
        assert i2["parent_id"] == i1["id"]

    def test_explicit_parent_id_overrides_auto(self):
        builder = InstructionBuilder()
        i1 = builder.add_from_structured_output(structured=_simple_structured())
        builder.add_from_structured_output(structured=_simple_structured())
        i3 = builder.add_from_structured_output(
            structured=_simple_structured(), parent_id=i1["id"]
        )
        assert i3["parent_id"] == i1["id"]

    def test_explicit_source_message_id(self):
        builder = InstructionBuilder()
        i1 = builder.add_from_structured_output(
            structured=_simple_structured(), source_message_id="custom-root"
        )
        assert i1["source_message_id"] == "custom-root"


# ---------------------------------------------------------------------------
# add_from_tool_call
# ---------------------------------------------------------------------------


class TestAddFromToolCall:
    def test_returns_instruction_dict(self):
        builder = InstructionBuilder()
        instr = builder.add_from_tool_call(
            tool_name="read",
            tool_call_id="tc-001",
            arguments={"path": "/tmp/file.txt"},
        )
        assert isinstance(instr, dict)

    def test_instruction_type_from_parser(self):
        builder = InstructionBuilder()
        instr = builder.add_from_tool_call(
            tool_name="read",
            tool_call_id="tc-001",
            arguments={"path": "/tmp/file.txt"},
        )
        assert instr["instruction_type"] == "READ"

    def test_exec_tool_call(self):
        builder = InstructionBuilder()
        instr = builder.add_from_tool_call(
            tool_name="exec",
            tool_call_id="tc-002",
            arguments={"command": "python run.py"},
        )
        assert instr["instruction_type"] == "EXEC"

    def test_content_has_tool_name(self):
        builder = InstructionBuilder()
        instr = builder.add_from_tool_call(
            tool_name="web_search",
            tool_call_id="tc-003",
            arguments={"query": "test"},
        )
        assert instr["content"]["tool_name"] == "web_search"

    def test_content_has_tool_call_id(self):
        builder = InstructionBuilder()
        instr = builder.add_from_tool_call(
            tool_name="read",
            tool_call_id="my-call-id",
            arguments={"path": "/tmp/x"},
        )
        assert instr["content"]["tool_call_id"] == "my-call-id"

    def test_content_has_arguments(self):
        builder = InstructionBuilder()
        args = {"command": "ls /tmp"}
        instr = builder.add_from_tool_call(
            tool_name="exec",
            tool_call_id="tc-004",
            arguments=args,
        )
        assert instr["content"]["arguments"] == args

    def test_result_not_in_content_when_none(self):
        builder = InstructionBuilder()
        instr = builder.add_from_tool_call(
            tool_name="read",
            tool_call_id="tc-005",
            arguments={"path": "/tmp/f"},
        )
        assert "result" not in instr["content"]

    def test_result_stored_in_content(self):
        builder = InstructionBuilder()
        result = {"output": "file contents here"}
        instr = builder.add_from_tool_call(
            tool_name="read",
            tool_call_id="tc-006",
            arguments={"path": "/tmp/f"},
            result=result,
        )
        assert instr["content"]["result"] == result

    def test_security_type_populated_by_parser(self):
        builder = InstructionBuilder()
        instr = builder.add_from_tool_call(
            tool_name="read",
            tool_call_id="tc-007",
            arguments={"path": "/etc/shadow"},
        )
        assert instr["security_type"] is not None
        assert instr["security_type"]["confidentiality"] == "HIGH"

    def test_instruction_category_from_instruction_type(self):
        builder = InstructionBuilder()
        instr = builder.add_from_tool_call(
            tool_name="exec",
            tool_call_id="tc-008",
            arguments={"command": "python run.py"},
        )
        assert instr["instruction_category"] == "EXECUTION.Env"

    def test_unknown_tool_category_defaults(self):
        builder = InstructionBuilder()
        instr = builder.add_from_tool_call(
            tool_name="no_such_tool",
            tool_call_id="tc-009",
            arguments={},
        )
        assert instr["instruction_type"] == "EXEC"
        assert instr["instruction_category"] == "EXECUTION.Env"

    def test_runtime_step_increments_across_methods(self):
        builder = InstructionBuilder()
        i1 = builder.add_from_structured_output(structured=_simple_structured())
        i2 = builder.add_from_tool_call(
            tool_name="read", tool_call_id="x", arguments={"path": "/tmp/f"}
        )
        assert i1["runtime_step"] == 1
        assert i2["runtime_step"] == 2

    def test_parent_id_chains_from_previous_instruction(self):
        builder = InstructionBuilder()
        i1 = builder.add_from_structured_output(structured=_simple_structured())
        i2 = builder.add_from_tool_call(
            tool_name="read", tool_call_id="x", arguments={"path": "/tmp/f"}
        )
        assert i2["parent_id"] == i1["id"]

    def test_delegate_tool_category(self):
        builder = InstructionBuilder()
        instr = builder.add_from_tool_call(
            tool_name="sessions_send",
            tool_call_id="tc-010",
            arguments={"message": "do X"},
        )
        assert instr["instruction_type"] == "DELEGATE"
        assert instr["instruction_category"] == "EXECUTION.Agent"


# ---------------------------------------------------------------------------
# to_json
# ---------------------------------------------------------------------------


class TestToJson:
    def test_returns_valid_json(self):
        builder = InstructionBuilder()
        builder.add_from_structured_output(structured=_simple_structured())
        raw = builder.to_json()
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)

    def test_has_trace_id(self):
        builder = InstructionBuilder(trace_id="trace-abc")
        raw = builder.to_json()
        data = json.loads(raw)
        assert data["trace_id"] == "trace-abc"

    def test_has_created_at(self):
        builder = InstructionBuilder()
        raw = builder.to_json()
        data = json.loads(raw)
        assert "created_at" in data
        assert data["created_at"]  # non-empty

    def test_has_instructions_list(self):
        builder = InstructionBuilder()
        builder.add_from_structured_output(structured=_simple_structured())
        builder.add_from_structured_output(structured=_simple_structured())
        data = json.loads(builder.to_json())
        assert isinstance(data["instructions"], list)
        assert len(data["instructions"]) == 2

    def test_empty_instructions(self):
        builder = InstructionBuilder()
        data = json.loads(builder.to_json())
        assert data["instructions"] == []

    def test_instruction_ids_preserved(self):
        builder = InstructionBuilder()
        i1 = builder.add_from_structured_output(structured=_simple_structured())
        data = json.loads(builder.to_json())
        assert data["instructions"][0]["id"] == i1["id"]

    def test_indent_parameter(self):
        builder = InstructionBuilder()
        raw_indent2 = builder.to_json(indent=2)
        raw_indent4 = builder.to_json(indent=4)
        # Both should parse to valid JSON
        d2 = json.loads(raw_indent2)
        d4 = json.loads(raw_indent4)
        # Structural fields (excluding created_at which changes between calls) match
        assert d2["trace_id"] == d4["trace_id"]
        assert d2["instructions"] == d4["instructions"]
        # Indented output should have newlines
        assert "\n" in raw_indent2
