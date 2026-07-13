"""Session identity helpers for OpenClaw / Hermes chat-gateway traffic."""

from __future__ import annotations

import re
from typing import Any, Optional

CHAT_GATEWAY_TOOL_AGENTS = frozenset({"openclaw", "hermes"})

_MESSAGE_ID_RE = re.compile(r"\[message_id:\s*([^\]]+)\]", re.IGNORECASE)
_RUNTIME_CHANNEL_RE = re.compile(r"channel=([^\s|]+)", re.IGNORECASE)
_RESET_PROMPT_RE = re.compile(
    r"^\s*a new session was started via /new or /reset\.",
    re.IGNORECASE | re.MULTILINE,
)


def is_chat_gateway_tool_agent(tool_agent: Optional[str]) -> bool:
    if not isinstance(tool_agent, str):
        return False
    return tool_agent.strip().lower() in CHAT_GATEWAY_TOOL_AGENTS


def extract_text_from_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                    continue
                nested = item.get("content")
                if isinstance(nested, str):
                    parts.append(nested)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def extract_message_id_from_text(text: str) -> Optional[str]:
    if not isinstance(text, str) or not text.strip():
        return None
    match = _MESSAGE_ID_RE.search(text)
    if not match:
        return None
    message_id = match.group(1).strip()
    return message_id or None


def _is_reset_marker_text(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False
    if cleaned.lower() in {"/new", "/reset"}:
        return True
    if len(cleaned) <= 4000 and _RESET_PROMPT_RE.search(cleaned):
        return True
    return False


def extract_runtime_channel_from_messages(messages: list[Any]) -> Optional[str]:
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "system":
            continue
        text = extract_text_from_message_content(msg.get("content"))
        match = _RUNTIME_CHANNEL_RE.search(text)
        if match:
            channel = match.group(1).strip()
            if channel:
                return channel
    return None


def extract_session_anchor_from_messages(messages: list[Any]) -> Optional[str]:
    """Return a stable OpenClaw/Hermes session anchor from user message ids.

    Priority:
    1. ``[message_id]`` on the latest reset/bootstrap user turn
    2. ``[message_id]`` on the earliest user turn
    """
    if not isinstance(messages, list):
        return None

    first_user_anchor: Optional[str] = None
    reset_anchor: Optional[str] = None

    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        text = extract_text_from_message_content(msg.get("content"))
        message_id = extract_message_id_from_text(text)
        if not message_id:
            continue
        if first_user_anchor is None:
            first_user_anchor = message_id
        if _is_reset_marker_text(text):
            reset_anchor = message_id

    return reset_anchor or first_user_anchor


def build_user_id_from_session_anchor(session_anchor: str) -> str:
    return f"msg-{session_anchor.strip()}"
