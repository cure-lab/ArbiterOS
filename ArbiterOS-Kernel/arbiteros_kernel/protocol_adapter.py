from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import litellm
from litellm.types.utils import Message


@dataclass
class CanonicalAssistantMessage:
    message: dict[str, Any]
    is_chat_completion: bool
    response_dump: Optional[dict[str, Any]]


@dataclass
class ResponsesStreamTracker:
    completed_response_obj: Optional[dict[str, Any]] = None
    text_parts: list[str] = field(default_factory=list)
    response_id: Optional[str] = None
    model_name: Optional[str] = None


@dataclass
class ResponsesStreamFinalize:
    completed_response_obj: Optional[dict[str, Any]]
    completed_text: str
    synthesized_response: dict[str, Any]
    synthetic_completed_event: Optional[dict[str, Any]]
    response_summary_source: str


def extract_text_from_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return ""


def is_responses_api_request(request_data: Any) -> bool:
    if not isinstance(request_data, dict):
        return False
    has_input = "input" in request_data and isinstance(
        request_data.get("input"), (str, list, dict)
    )
    has_chat_messages = isinstance(request_data.get("messages"), list)
    return has_input and not has_chat_messages


def extract_text_from_responses_input(input_payload: Any) -> str:
    if isinstance(input_payload, str):
        return input_payload.strip()
    if isinstance(input_payload, dict):
        role = input_payload.get("role")
        if isinstance(role, str) and role != "user":
            return ""
        return extract_text_from_message_content(input_payload.get("content")).strip()
    if isinstance(input_payload, list):
        user_texts: list[str] = []
        for item in input_payload:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    user_texts.append(text)
                continue
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if isinstance(role, str) and role != "user":
                continue
            text = extract_text_from_message_content(item.get("content")).strip()
            if text:
                user_texts.append(text)
        if user_texts:
            return user_texts[-1]
    return ""


def inject_system_hint_into_request(
    data: dict[str, Any], *, hint_content: str, marker: str
) -> dict[str, Any]:
    hint_message = {"role": "system", "content": hint_content}
    messages = data.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("role") != "system":
                continue
            content = message.get("content")
            if isinstance(content, str) and marker in content:
                return data

        insert_at = 0
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if isinstance(msg, dict) and msg.get("role") == "user":
                insert_at = idx
                break

        new_messages = list(messages)
        new_messages.insert(insert_at, hint_message)
        return {**data, "messages": new_messages}

    if is_responses_api_request(data):
        instructions = data.get("instructions")
        if isinstance(instructions, str) and marker in instructions:
            return data
        if isinstance(instructions, str) and instructions.strip():
            new_instructions = f"{instructions.rstrip()}\n\n{hint_content}"
        else:
            new_instructions = hint_content
        return {**data, "instructions": new_instructions}

    return data


def extract_all_user_messages_from_request(request_data: Any) -> list[str]:
    if not isinstance(request_data, dict):
        return []

    out: list[str] = []
    messages = request_data.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "user":
                continue
            text = extract_text_from_message_content(msg.get("content")).strip()
            if text:
                out.append(text)
        return out

    if is_responses_api_request(request_data):
        input_payload = request_data.get("input")
        if isinstance(input_payload, str):
            text = input_payload.strip()
            if text:
                out.append(text)
            return out
        if isinstance(input_payload, dict):
            role = input_payload.get("role")
            if isinstance(role, str) and role != "user":
                return out
            text = extract_text_from_message_content(input_payload.get("content")).strip()
            if text:
                out.append(text)
            return out
        if isinstance(input_payload, list):
            for item in input_payload:
                if isinstance(item, str):
                    text = item.strip()
                    if text:
                        out.append(text)
                    continue
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                if isinstance(role, str) and role != "user":
                    continue
                text = extract_text_from_message_content(item.get("content")).strip()
                if text:
                    out.append(text)
    return out


def extract_text_from_responses_output(response_obj: Any) -> str:
    if not isinstance(response_obj, dict):
        return ""

    direct_output_text = response_obj.get("output_text")
    if isinstance(direct_output_text, str) and direct_output_text.strip():
        return direct_output_text.strip()

    texts: list[str] = []
    output_items = response_obj.get("output")
    if not isinstance(output_items, list):
        return ""

    for item in output_items:
        if not isinstance(item, dict):
            continue
        item_text = item.get("text")
        if isinstance(item_text, str) and item_text.strip():
            texts.append(item_text.strip())
        content = item.get("content")
        content_text = extract_text_from_message_content(content)
        if content_text.strip():
            texts.append(content_text.strip())

    return "\n".join(texts).strip()


def extract_stream_text_from_responses_chunk(chunk: Any, chunk_dump: Optional[dict]) -> str:
    text_parts: list[str] = []
    if isinstance(chunk_dump, dict):
        delta = chunk_dump.get("delta")
        if isinstance(delta, str) and delta:
            text_parts.append(delta)
    # Some providers/chunks do not expose `delta` but LiteLLM can still parse text.
    if not text_parts:
        try:
            parsed = litellm.get_response_string(response_obj=chunk)
            if isinstance(parsed, str) and parsed:
                text_parts.append(parsed)
        except Exception:
            pass
    return "".join(text_parts)


def build_synthetic_responses_completed_event(
    *, text: str, response_id: Optional[str], model: Optional[str]
) -> dict[str, Any]:
    now_token = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    rid = (
        response_id.strip()
        if isinstance(response_id, str) and response_id.strip()
        else f"resp_arbiteros_{now_token}"
    )
    mid = f"msg_arbiteros_{now_token}"
    out_text = text if isinstance(text, str) else ""
    return {
        "type": "response.completed",
        "response": {
            "id": rid,
            "object": "response",
            "status": "completed",
            "model": model or "",
            "metadata": {},
            "output": [
                {
                    "type": "message",
                    "id": mid,
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": out_text,
                            "annotations": [],
                        }
                    ],
                }
            ],
        },
    }


def to_canonical_assistant_message(response: Any) -> CanonicalAssistantMessage:
    choices = getattr(response, "choices", None)
    is_chat_completion = isinstance(choices, list)
    if is_chat_completion:
        msg = choices[0].message if choices else None
        msg_dict = (
            msg
            if isinstance(msg, dict)
            else (
                msg.model_dump()
                if hasattr(msg, "model_dump")
                else (msg.dict() if hasattr(msg, "dict") else None)
            )
        )
        if not isinstance(msg_dict, dict):
            msg_dict = {
                "content": None,
                "role": "assistant",
                "tool_calls": None,
                "function_call": None,
                "provider_specific_fields": {},
                "annotations": [],
            }
        return CanonicalAssistantMessage(
            message=msg_dict, is_chat_completion=True, response_dump=None
        )

    response_dump: Any = None
    if hasattr(response, "model_dump"):
        try:
            response_dump = response.model_dump()
        except Exception:
            response_dump = None
    if response_dump is None and hasattr(response, "dict"):
        try:
            response_dump = response.dict()
        except Exception:
            response_dump = None
    if response_dump is None and isinstance(response, dict):
        response_dump = response
    if response_dump is None:
        response_dump = {}

    output_text = (
        extract_text_from_responses_output(response_dump)
        if isinstance(response_dump, dict)
        else ""
    )
    provider_fields: dict[str, Any] = {"format": "openai-responses-v1"}
    if isinstance(response_dump, dict):
        for key in ("id", "model", "status"):
            value = response_dump.get(key)
            if isinstance(value, (str, int, float, bool)) and value is not None:
                provider_fields[key] = value
    msg_dict = {
        "content": output_text if output_text else None,
        "role": "assistant",
        "tool_calls": None,
        "function_call": None,
        "provider_specific_fields": provider_fields,
        "annotations": [],
    }
    return CanonicalAssistantMessage(
        message=msg_dict, is_chat_completion=False, response_dump=response_dump
    )


def apply_canonical_message_to_response(
    response: Any, msg_dict: dict[str, Any], *, is_chat_completion: bool
) -> None:
    try:
        if is_chat_completion:
            response.choices[0].message = Message(**msg_dict)
            return
        new_content = msg_dict.get("content")
        if isinstance(new_content, str) and hasattr(response, "output_text"):
            setattr(response, "output_text", new_content)
    except Exception:
        return


def extract_stream_chunk_dump_and_event_type(
    chunk: Any,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    chunk_dump: Optional[dict[str, Any]] = None
    event_type: Optional[str] = None

    if hasattr(chunk, "type") and isinstance(getattr(chunk, "type"), str):
        event_type = getattr(chunk, "type")
    elif isinstance(chunk, dict) and isinstance(chunk.get("type"), str):
        event_type = chunk.get("type")

    if hasattr(chunk, "model_dump"):
        try:
            maybe_dump = chunk.model_dump()
        except Exception:
            maybe_dump = None
        if isinstance(maybe_dump, dict):
            chunk_dump = maybe_dump
    elif isinstance(chunk, dict):
        chunk_dump = chunk

    if event_type is None and isinstance(chunk_dump, dict):
        maybe_type = chunk_dump.get("type")
        if isinstance(maybe_type, str):
            event_type = maybe_type

    return chunk_dump, event_type


def update_responses_tracker_from_chunk(
    tracker: ResponsesStreamTracker,
    *,
    chunk_dump: Optional[dict[str, Any]],
    event_type: Optional[str],
) -> None:
    if not isinstance(chunk_dump, dict):
        return

    chunk_response = chunk_dump.get("response")
    if isinstance(chunk_response, dict):
        rid = chunk_response.get("id")
        if isinstance(rid, str) and rid.strip():
            tracker.response_id = rid.strip()
        model_name = chunk_response.get("model")
        if isinstance(model_name, str) and model_name.strip():
            tracker.model_name = model_name.strip()

    raw_rid = chunk_dump.get("response_id")
    if (
        not tracker.response_id
        and isinstance(raw_rid, str)
        and raw_rid.strip()
    ):
        tracker.response_id = raw_rid.strip()

    if event_type in {"response.completed", "response.failed"}:
        response_obj = chunk_dump.get("response")
        if isinstance(response_obj, dict):
            tracker.completed_response_obj = response_obj


def collect_responses_stream_text(
    tracker: ResponsesStreamTracker, *, chunk: Any, chunk_dump: Optional[dict[str, Any]]
) -> None:
    part = extract_stream_text_from_responses_chunk(chunk, chunk_dump)
    if part:
        tracker.text_parts.append(part)


def finalize_responses_stream(
    *,
    tracker: ResponsesStreamTracker,
    request_model: Optional[str],
    stream_error: Optional[Exception],
) -> ResponsesStreamFinalize:
    def _has_valid_completed_shape(response_obj: Any) -> bool:
        if not isinstance(response_obj, dict):
            return False
        output = response_obj.get("output")
        return isinstance(output, list)

    completed_text = (
        extract_text_from_responses_output(tracker.completed_response_obj)
        if isinstance(tracker.completed_response_obj, dict)
        else ""
    )
    if not completed_text and tracker.text_parts:
        completed_text = "".join(tracker.text_parts).strip()

    synthetic_completed_event: Optional[dict[str, Any]] = None
    completed_response_obj = tracker.completed_response_obj
    completed_shape_valid = _has_valid_completed_shape(completed_response_obj)
    response_summary_source = (
        "responses.completed_event"
        if completed_shape_valid
        else "responses.stream_delta_fallback"
    )
    if (
        stream_error is not None
        and not completed_shape_valid
        and completed_text
    ):
        synthetic_completed_event = build_synthetic_responses_completed_event(
            text=completed_text,
            response_id=tracker.response_id,
            model=tracker.model_name or request_model,
        )
        completed_response_obj = synthetic_completed_event.get("response")
        response_summary_source = "responses.synthetic_completed"

    synthesized_response = {
        "content": completed_text if completed_text else None,
        "role": "assistant",
        "tool_calls": None,
        "function_call": None,
        "provider_specific_fields": {"format": "openai-responses-v1"},
        "annotations": [],
    }

    return ResponsesStreamFinalize(
        completed_response_obj=completed_response_obj,
        completed_text=completed_text,
        synthesized_response=synthesized_response,
        synthetic_completed_event=synthetic_completed_event,
        response_summary_source=response_summary_source,
    )
