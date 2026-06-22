"""Tests for response_format injection and strict content unwrap."""

import json

from arbiteros_kernel.instruction_parsing.builder import InstructionBuilder
from arbiteros_kernel.litellm_callback import (
    _ensure_kernel_response_format,
    _extract_strict_topic_category_payload,
    _inject_depends_on_schema_into_response_format,
    _inject_ref_markers_into_messages,
    _lookup_response_format_from_litellm_config,
)


def test_lookup_response_format_from_litellm_config():
    rf = _lookup_response_format_from_litellm_config("gpt-4o")
    assert isinstance(rf, dict)
    schema = rf.get("json_schema", {}).get("schema", {})
    assert "depends_on" in schema.get("properties", {})


def test_ensure_kernel_response_format_injects_from_config():
    data: dict = {"model": "gpt-4o"}
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
    assert dep["items"]["type"] == "string"


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


def test_extract_strict_topic_category_payload_with_thinking_prefix():
    content = (
        "<think>internal</think>"
        '{"topic":"t","category":"COGNITIVE_CORE__RESPOND","content":"ok","depends_on":[]}'
    )
    parsed = _extract_strict_topic_category_payload(content)
    assert isinstance(parsed, dict)
    assert parsed.get("content") == "ok"
    assert parsed.get("depends_on") == []
