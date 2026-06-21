"""Tests for response_format injection and strict content unwrap."""

import json

from arbiteros_kernel.litellm_callback import (
    _ensure_kernel_response_format,
    _extract_strict_topic_category_payload,
    _inject_runtime_step_catalog_into_response_format,
    _lookup_response_format_from_litellm_config,
)


def test_lookup_response_format_from_litellm_config():
    rf = _lookup_response_format_from_litellm_config("gpt-5.2-chat-latest")
    assert isinstance(rf, dict)
    schema = rf.get("json_schema", {}).get("schema", {})
    assert "depends_on" in schema.get("properties", {})


def test_ensure_kernel_response_format_injects_from_config():
    data: dict = {"model": "gpt-5.2-chat-latest"}
    _ensure_kernel_response_format(data)
    assert isinstance(data.get("response_format"), dict)
    props = (
        data["response_format"]
        .get("json_schema", {})
        .get("schema", {})
        .get("properties", {})
    )
    assert "depends_on" in props


def test_inject_runtime_step_catalog_into_response_format():
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
                            "items": {"type": "integer"},
                            "description": "placeholder",
                        }
                    },
                },
            },
        }
    }
    _inject_runtime_step_catalog_into_response_format(data, trace_id="test-trace")
    desc = (
        data["response_format"]["json_schema"]["schema"]["properties"]["depends_on"][
            "description"
        ]
    )
    assert "Step catalog" in desc
    assert "Next step number starts at:" in desc


def test_extract_strict_topic_category_payload_strips_thinking():
    raw = (
        "<think>**Confirming answer format**</think>\n\n"
        '{"category":"COGNITIVE_CORE__RESPOND","content":"Hey Chen","depends_on":[],"topic":""}'
    )
    parsed = _extract_strict_topic_category_payload(raw)
    assert parsed is not None
    assert parsed["content"] == "Hey Chen"
    assert parsed["depends_on"] == []


def test_combine_thinking_with_unwrapped_content():
    from arbiteros_kernel.litellm_callback import (
        _combine_thinking_with_unwrapped_content,
        _extract_leading_thinking_prefix,
    )

    raw = (
        "<think>plan</think>\n\n"
        '{"category":"COGNITIVE_CORE__RESPOND","content":"Hello","depends_on":[],"topic":""}'
    )
    prefix = _extract_leading_thinking_prefix(raw)
    assert prefix.startswith("<think>")
    combined = _combine_thinking_with_unwrapped_content(prefix, "Hello")
    assert combined.startswith("<think>plan</think>")
    assert combined.endswith("Hello")
    assert "{" not in combined


def test_response_transform_preserves_thinking_with_strict_json():
    from arbiteros_kernel.litellm_callback import _response_transform_content_only

    raw = (
        "<think>internal</think>\n\n"
        '{"category":"COGNITIVE_CORE__RESPOND","content":"对外正文","depends_on":[1],"topic":"t"}'
    )
    data = {"metadata": {"arbiteros_trace_id": "t-thinking"}, "_skip_instruction_adding": True}
    out = _response_transform_content_only(
        data,
        {"role": "assistant", "content": raw},
    )
    assert out is not None
    assert out["content"].startswith("<think>internal</think>")
    assert "对外正文" in out["content"]
    assert "COGNITIVE_CORE__RESPOND" not in out["content"]


def test_wrap_messages_with_categories_aligns_depends_on_slots():
    from arbiteros_kernel.litellm_callback import (
        _stripped_categories_by_trace,
        _stripped_categories_lock,
        _stripped_text_depends_on_by_trace,
        _stripped_topics_by_trace,
        _wrap_messages_with_categories,
    )

    trace_id = "wrap-test-trace"
    with _stripped_categories_lock:
        _stripped_categories_by_trace[trace_id] = [
            "COGNITIVE_CORE__RESPOND",
            "COGNITIVE_CORE__RESPOND",
            "COGNITIVE_CORE__RESPOND",
        ]
        _stripped_topics_by_trace[trace_id] = [None, "topic-b", "topic-c"]
        _stripped_text_depends_on_by_trace[trace_id] = [[], [1], [2, 3]]

    data = {
        "metadata": {"arbiteros_trace_id": trace_id},
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "oldest text"},
            {"role": "assistant", "content": "middle text"},
            {"role": "assistant", "content": "latest text"},
        ],
    }
    wrapped = _wrap_messages_with_categories(data, trace_id=trace_id)
    latest = json.loads(wrapped["messages"][-1]["content"])
    middle = json.loads(wrapped["messages"][-2]["content"])
    oldest = json.loads(wrapped["messages"][1]["content"])

    assert latest["depends_on"] == [2, 3]
    assert latest["topic"] == "topic-c"
    assert middle["depends_on"] == [1]
    assert middle["topic"] == "topic-b"
    assert oldest["depends_on"] == []

    with _stripped_categories_lock:
        _stripped_categories_by_trace.pop(trace_id, None)
        _stripped_topics_by_trace.pop(trace_id, None)
        _stripped_text_depends_on_by_trace.pop(trace_id, None)
