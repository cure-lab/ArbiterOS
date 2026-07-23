"""Regression tests for Claude Code / proxy chat-completion response parsing."""

import json

from arbiteros_kernel.protocol_adapter import (
    extract_text_from_message_content,
    inject_system_hint_into_request,
    normalize_anthropic_system_layout,
    normalize_assistant_message_dict,
    response_has_chat_completion_choices,
    to_canonical_assistant_message,
)


class _ProxyChatResponseDumpOnly:
    """Mimics zhizengzeng-style payloads: choices only in model_dump(), not on object."""

    def model_dump(self):
        return {
            "id": "msg_01QqMDHg4pG3VGqPetxHvaXq",
            "model": "claude-sonnet-4-5-20250929",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "topic": "问候",
                                "category": "COGNITIVE_CORE__RESPOND",
                                "content": "Hi! How can I help?",
                                "depends_on": [],
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
        }


def test_to_canonical_reads_choices_from_model_dump_only():
    canonical = to_canonical_assistant_message(_ProxyChatResponseDumpOnly())
    assert canonical.is_chat_completion is True
    content = canonical.message.get("content")
    assert isinstance(content, str) and content.strip()
    parsed = json.loads(content)
    assert parsed["content"] == "Hi! How can I help?"


def test_response_has_chat_completion_choices_from_dump():
    assert response_has_chat_completion_choices(_ProxyChatResponseDumpOnly()) is True


def test_extract_text_from_anthropic_block_list():
    blocks = [
        {"type": "text", "text": "hello"},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "Read",
            "input": {"file_path": "/tmp/a"},
        },
    ]
    assert extract_text_from_message_content(blocks) == "hello"


def test_normalize_anthropic_system_layout_hoists_messages_system():
    data = {
        "model": "claude-sonnet-4-5-20250929",
        "system": [{"type": "text", "text": "You are Claude Code."}],
        "messages": [
            {"role": "system", "content": "[arbiteros_topic_hint]\nPick a topic."},
            {"role": "user", "content": "hi"},
        ],
    }
    out = normalize_anthropic_system_layout(data)
    assert all(
        not (isinstance(m, dict) and m.get("role") == "system")
        for m in out["messages"]
    )
    system_text = extract_text_from_message_content(out["system"])
    assert "You are Claude Code." in system_text
    assert "[arbiteros_topic_hint]" in system_text


def test_inject_system_hint_uses_top_level_system_for_claude_code():
    data = {
        "model": "claude-sonnet-4-5-20250929",
        "system": [{"type": "text", "text": "base system"}],
        "messages": [{"role": "user", "content": "hi"}],
    }
    out = inject_system_hint_into_request(
        data,
        hint_content="[arbiteros_topic_hint]\nTopic rules",
        marker="[arbiteros_topic_hint]",
    )
    assert all(
        not (isinstance(m, dict) and m.get("role") == "system")
        for m in out["messages"]
    )
    system_text = extract_text_from_message_content(out["system"])
    assert "base system" in system_text
    assert "[arbiteros_topic_hint]" in system_text


def test_normalize_assistant_message_dict_extracts_tool_calls():
    msg = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Reading"},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "Read",
                "input": {"file_path": "/tmp/a"},
            },
        ],
    }
    normalized = normalize_assistant_message_dict(msg)
    assert normalized["content"] == "Reading"
    assert normalized["tool_calls"][0]["id"] == "toolu_1"
