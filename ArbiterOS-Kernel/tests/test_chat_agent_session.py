"""Tests for OpenClaw/Hermes chat-gateway session identity extraction."""

from arbiteros_kernel import chat_agent_session as cas


def _openclaw_system_prompt() -> str:
    return (
        "You are a personal assistant running inside OpenClaw.\n"
        "## Runtime\n"
        "Runtime: agent=main | host=test | channel=webchat | thinking=off\n"
    )


def test_extract_session_anchor_prefers_reset_message_id():
    messages = [
        {"role": "system", "content": _openclaw_system_prompt()},
        {
            "role": "user",
            "content": (
                "A new session was started via /new or /reset. Greet the user.\n"
                "[message_id: 81032c14-cf91-4a03-a6d1-9857c2fd63b1]"
            ),
        },
        {
            "role": "user",
            "content": "[Mon 2026-07-13 11:38 GMT+8] OK的\n[message_id: 2c22ec36-b755-4614-8d15-f1dc08859d2b]",
        },
    ]
    assert (
        cas.extract_session_anchor_from_messages(messages)
        == "81032c14-cf91-4a03-a6d1-9857c2fd63b1"
    )


def test_extract_session_anchor_uses_first_user_without_reset():
    messages = [
        {"role": "system", "content": _openclaw_system_prompt()},
        {
            "role": "user",
            "content": "hello\n[message_id: first-user-id-1111]",
        },
        {
            "role": "user",
            "content": "follow up\n[message_id: second-user-id-2222]",
        },
    ]
    assert cas.extract_session_anchor_from_messages(messages) == "first-user-id-1111"


def test_extract_session_anchor_from_list_content():
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "A new session was started via /new or /reset.\n"
                        "[message_id: 45ac2220-cda5-4987-8b48-c570b06dfcf0]"
                    ),
                }
            ],
        }
    ]
    assert (
        cas.extract_session_anchor_from_messages(messages)
        == "45ac2220-cda5-4987-8b48-c570b06dfcf0"
    )


def test_extract_runtime_channel_from_system_prompt():
    messages = [{"role": "system", "content": _openclaw_system_prompt()}]
    assert cas.extract_runtime_channel_from_messages(messages) == "webchat"


def test_build_user_id_from_session_anchor():
    assert (
        cas.build_user_id_from_session_anchor("81032c14-cf91-4a03-a6d1-9857c2fd63b1")
        == "msg-81032c14-cf91-4a03-a6d1-9857c2fd63b1"
    )


def test_is_chat_gateway_tool_agent():
    assert cas.is_chat_gateway_tool_agent("openclaw")
    assert cas.is_chat_gateway_tool_agent("hermes")
    assert not cas.is_chat_gateway_tool_agent("codex")


def test_build_device_context_uses_message_id_for_openclaw(monkeypatch):
    from arbiteros_kernel import litellm_callback as lc

    monkeypatch.setattr(lc, "_get_request_agent_name", lambda incoming=None: "openclaw")
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda: None)

    incoming = {
        "messages": [
            {"role": "system", "content": _openclaw_system_prompt()},
            {
                "role": "user",
                "content": (
                    "A new session was started via /new or /reset. Greet the user.\n"
                    "[message_id: 81032c14-cf91-4a03-a6d1-9857c2fd63b1]"
                ),
            },
        ]
    }
    context = lc._build_device_context(incoming)
    assert context.channel == "webchat"
    assert context.user_id == "msg-81032c14-cf91-4a03-a6d1-9857c2fd63b1"
    assert context.device_key == (
        "webchat:msg-81032c14-cf91-4a03-a6d1-9857c2fd63b1"
    )
    assert context.has_explicit_user_id is True


def test_build_device_context_distinguishes_two_openclaw_sessions(monkeypatch):
    from arbiteros_kernel import litellm_callback as lc

    monkeypatch.setattr(lc, "_get_request_agent_name", lambda incoming=None: "openclaw")
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda: None)

    first = lc._build_device_context(
        {
            "messages": [
                {"role": "system", "content": _openclaw_system_prompt()},
                {
                    "role": "user",
                    "content": (
                        "A new session was started via /new or /reset.\n"
                        "[message_id: 81032c14-cf91-4a03-a6d1-9857c2fd63b1]"
                    ),
                },
            ]
        }
    )
    second = lc._build_device_context(
        {
            "messages": [
                {"role": "system", "content": _openclaw_system_prompt()},
                {
                    "role": "user",
                    "content": (
                        "A new session was started via /new or /reset.\n"
                        "[message_id: 45ac2220-cda5-4987-8b48-c570b06dfcf0]"
                    ),
                },
            ]
        }
    )
    assert first.device_key != second.device_key
