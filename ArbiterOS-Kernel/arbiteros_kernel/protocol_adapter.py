from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import json
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
    function_call_items: list[dict[str, Any]] = field(default_factory=list)
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
            if isinstance(part, dict):
                text = part.get("text")
            else:
                text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return ""


def _extract_tool_calls_from_anthropic_content(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    out: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "").strip() != "tool_use":
            continue
        tool_id = block.get("id")
        tool_name = block.get("name")
        if not isinstance(tool_id, str) or not tool_id.strip():
            continue
        if not isinstance(tool_name, str) or not tool_name.strip():
            continue
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        out.append(
            {
                "id": tool_id.strip(),
                "type": "function",
                "function": {
                    "name": tool_name.strip(),
                    "arguments": json.dumps(tool_input, ensure_ascii=False),
                },
            }
        )
    return out


def _is_anthropic_message_shape(response_obj: Any) -> bool:
    if not isinstance(response_obj, dict):
        return False
    if str(response_obj.get("type") or "").strip() != "message":
        return False
    if str(response_obj.get("role") or "").strip() != "assistant":
        return False
    return isinstance(response_obj.get("content"), list)


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


def extract_text_from_responses_output(response_dump: Any) -> str:
    if not isinstance(response_dump, dict):
        return ""
    output_text = response_dump.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    output = response_dump.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip() != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if str(part.get("type") or "").strip() not in {"output_text", "text"}:
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts)


def _normalize_responses_function_call_item(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Map OpenAI Responses ``function_call`` output item to chat ``tool_calls`` entry."""
    call_id = item.get("call_id")
    if not isinstance(call_id, str) or not call_id.strip():
        raw_id = item.get("id")
        call_id = raw_id if isinstance(raw_id, str) else None
    if not isinstance(call_id, str) or not call_id.strip():
        return None
    fn_name = item.get("name")
    if not isinstance(fn_name, str) or not fn_name.strip():
        return None
    raw_args = item.get("arguments")
    if isinstance(raw_args, dict):
        fn_args = json.dumps(raw_args, ensure_ascii=False)
    elif isinstance(raw_args, str):
        fn_args = raw_args
    else:
        fn_args = "{}"
    return {
        "id": call_id.strip(),
        "type": "function",
        "function": {"name": fn_name.strip(), "arguments": fn_args},
    }


def extract_tool_calls_from_responses_output(response_dump: Any) -> list[dict[str, Any]]:
    if not isinstance(response_dump, dict):
        return []
    output = response_dump.get("output")
    if not isinstance(output, list):
        return []
    tool_calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip() != "function_call":
            continue
        normalized = _normalize_responses_function_call_item(item)
        if normalized is not None:
            tool_calls.append(normalized)
    return tool_calls


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


def extract_stream_text_from_responses_chunk(
    chunk: Any, chunk_dump: Optional[dict]
) -> str:
    text_parts: list[str] = []
    try:
        parsed = litellm.get_response_string(response_obj=chunk)
        if isinstance(parsed, str) and parsed:
            text_parts.append(parsed)
    except Exception:
        pass
    if isinstance(chunk_dump, dict):
        delta = chunk_dump.get("delta")
        if isinstance(delta, str) and delta:
            text_parts.append(delta)
    return "".join(text_parts)


def _build_anthropic_content_blocks(
    msg_dict: dict[str, Any],
) -> list[Any]:
    """Build Anthropic content blocks (typed or dict) from canonical msg_dict."""
    new_content = msg_dict.get("content")
    text = extract_text_from_message_content(new_content)
    if not text and isinstance(new_content, str):
        text = new_content

    blocks: list[Any] = []
    if isinstance(text, str) and text.strip():
        try:
            from anthropic.types import TextBlock

            blocks.append(TextBlock(type="text", text=text))
        except Exception:
            blocks.append({"type": "text", "text": text})

    raw_tool_calls = msg_dict.get("tool_calls")
    if isinstance(raw_tool_calls, list):
        for tc in raw_tool_calls:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("id") or tc.get("tool_call_id")
            if not isinstance(tc_id, str) or not tc_id.strip():
                continue
            fn = tc.get("function")
            if not isinstance(fn, dict):
                continue
            fn_name = fn.get("name")
            if not isinstance(fn_name, str) or not fn_name.strip():
                continue
            raw_args = fn.get("arguments")
            parsed_args: Any = {}
            if isinstance(raw_args, str):
                try:
                    parsed_args = json.loads(raw_args)
                except Exception:
                    parsed_args = {}
            elif isinstance(raw_args, dict):
                parsed_args = raw_args
            if not isinstance(parsed_args, dict):
                parsed_args = {}
            try:
                from anthropic.types import ToolUseBlock

                blocks.append(
                    ToolUseBlock(
                        type="tool_use",
                        id=tc_id.strip(),
                        name=fn_name.strip(),
                        input=parsed_args,
                    )
                )
            except Exception:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc_id.strip(),
                        "name": fn_name.strip(),
                        "input": parsed_args,
                    }
                )
    return blocks


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

    if _is_anthropic_message_shape(response_dump):
        anth_content = response_dump.get("content")
        anth_text = extract_text_from_message_content(anth_content)
        anth_tool_calls = _extract_tool_calls_from_anthropic_content(anth_content)
        provider_fields: dict[str, Any] = {"format": "anthropic-messages-v1"}
        if isinstance(response_dump, dict):
            for key in ("id", "model", "type", "stop_reason"):
                value = response_dump.get(key)
                if isinstance(value, (str, int, float, bool)) and value is not None:
                    provider_fields[key] = value
        msg_dict = {
            "content": anth_text if anth_text else None,
            "role": "assistant",
            "tool_calls": anth_tool_calls if anth_tool_calls else None,
            "function_call": None,
            "provider_specific_fields": provider_fields,
            "annotations": [],
        }
        return CanonicalAssistantMessage(
            message=msg_dict, is_chat_completion=False, response_dump=response_dump
        )

    output_text = (
        extract_text_from_responses_output(response_dump)
        if isinstance(response_dump, dict)
        else ""
    )
    tool_calls = (
        extract_tool_calls_from_responses_output(response_dump)
        if isinstance(response_dump, dict)
        else []
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
        "tool_calls": tool_calls if tool_calls else None,
        "function_call": None,
        "provider_specific_fields": provider_fields,
        "annotations": [],
    }
    return CanonicalAssistantMessage(
        message=msg_dict, is_chat_completion=False, response_dump=response_dump
    )


def apply_canonical_message_to_response(
    response: Any, msg_dict: dict[str, Any], *, is_chat_completion: bool
) -> Any:
    """
    Write canonical msg_dict back onto the provider response object.

    Returns the object that should be returned to the client (may be a new
    Pydantic model instance when model_copy is required).
    """
    try:
        if is_chat_completion:
            response.choices[0].message = Message(**msg_dict)
            return response

        new_content = msg_dict.get("content")
        if isinstance(new_content, str) and hasattr(response, "output_text"):
            setattr(response, "output_text", new_content)

        content_blocks = _build_anthropic_content_blocks(msg_dict)
        if not content_blocks and not (
            isinstance(new_content, str) and new_content.strip()
        ):
            return response

        # Pydantic Anthropic Message: model_copy is the reliable path.
        if hasattr(response, "model_copy"):
            try:
                update: dict[str, Any] = {"content": content_blocks}
                if not msg_dict.get("tool_calls"):
                    update["stop_reason"] = "end_turn"
                return response.model_copy(update=update)
            except Exception:
                pass

        if isinstance(response, dict):
            out = dict(response)
            if content_blocks:
                out["content"] = content_blocks
            if not msg_dict.get("tool_calls"):
                out.pop("tool_calls", None)
            return out

        if hasattr(response, "content"):
            try:
                setattr(response, "content", content_blocks)
            except Exception:
                pass
    except Exception:
        return response
    return response


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


def _ingest_responses_output_item(tracker: ResponsesStreamTracker, item: Any) -> None:
    if not isinstance(item, dict):
        return
    if str(item.get("type") or "").strip() != "function_call":
        return
    call_id = item.get("call_id") or item.get("id")
    if not isinstance(call_id, str) or not call_id.strip():
        return
    key = call_id.strip()
    for existing in tracker.function_call_items:
        existing_id = existing.get("call_id") or existing.get("id")
        if isinstance(existing_id, str) and existing_id.strip() == key:
            if isinstance(item.get("arguments"), str) and item.get("arguments"):
                existing["arguments"] = item.get("arguments")
            if isinstance(item.get("name"), str) and item.get("name"):
                existing["name"] = item.get("name")
            return
    tracker.function_call_items.append(dict(item))


def update_responses_tracker_from_chunk(
    tracker: ResponsesStreamTracker,
    *,
    chunk_dump: Optional[dict[str, Any]],
    event_type: Optional[str],
) -> None:
    if not isinstance(chunk_dump, dict):
        return
    if event_type in {"response.output_item.done", "response.output_item.added"}:
        item = chunk_dump.get("item")
        _ingest_responses_output_item(tracker, item)
    if event_type == "response.function_call_arguments.done":
        item_id = chunk_dump.get("item_id")
        arguments = chunk_dump.get("arguments")
        if isinstance(item_id, str) and isinstance(arguments, str):
            for existing in tracker.function_call_items:
                raw_id = existing.get("id")
                call_id = existing.get("call_id")
                matched = item_id == raw_id or item_id == call_id
                if isinstance(call_id, str) and call_id.strip():
                    matched = matched or item_id == f"fc_{call_id.strip()}"
                if matched:
                    existing["arguments"] = arguments
                    break
    if event_type == "response.completed":
        response_obj = chunk_dump.get("response")
        if isinstance(response_obj, dict):
            tracker.completed_response_obj = response_obj
    response_obj = chunk_dump.get("response")
    if isinstance(response_obj, dict):
        rid = response_obj.get("id")
        if isinstance(rid, str) and rid.strip():
            tracker.response_id = rid.strip()
        model = response_obj.get("model")
        if isinstance(model, str) and model.strip():
            tracker.model_name = model.strip()


def collect_responses_stream_text(
    tracker: ResponsesStreamTracker,
    *,
    chunk: Any,
    chunk_dump: Optional[dict[str, Any]],
) -> None:
    if not isinstance(chunk_dump, dict):
        return
    event_type = chunk_dump.get("type")
    if event_type == "response.output_text.delta":
        delta = chunk_dump.get("delta")
        if isinstance(delta, str) and delta:
            tracker.text_parts.append(delta)
    if not tracker.text_parts:
        try:
            parsed = litellm.get_response_string(response_obj=chunk)
            if isinstance(parsed, str) and parsed:
                tracker.text_parts.append(parsed)
        except Exception:
            pass


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


def finalize_responses_stream(
    *,
    tracker: ResponsesStreamTracker,
    request_model: Optional[str],
    stream_error: Optional[Exception],
) -> ResponsesStreamFinalize:
    completed_response_obj = tracker.completed_response_obj
    completed_text = "".join(tracker.text_parts)
    tool_calls: list[dict[str, Any]] = []
    if isinstance(completed_response_obj, dict):
        dump_text = extract_text_from_responses_output(completed_response_obj)
        if dump_text:
            completed_text = dump_text
        tool_calls = extract_tool_calls_from_responses_output(completed_response_obj)
    if not tool_calls and tracker.function_call_items:
        for item in tracker.function_call_items:
            normalized = _normalize_responses_function_call_item(item)
            if normalized is not None:
                tool_calls.append(normalized)

    synthesized_response: dict[str, Any] = {
        "content": completed_text if completed_text else None,
        "role": "assistant",
        "tool_calls": tool_calls if tool_calls else None,
        "function_call": None,
        "provider_specific_fields": {"format": "openai-responses-v1"},
        "annotations": [],
    }
    if isinstance(completed_response_obj, dict):
        for key in ("id", "model", "status"):
            value = completed_response_obj.get(key)
            if isinstance(value, (str, int, float, bool)) and value is not None:
                synthesized_response.setdefault("provider_specific_fields", {})[
                    key
                ] = value

    response_summary_source = "responses.synthetic_completed"
    synthetic_completed_event: Optional[dict[str, Any]] = None
    if not isinstance(completed_response_obj, dict):
        synthetic_completed_event = build_synthetic_responses_completed_event(
            text=completed_text,
            response_id=tracker.response_id,
            model=tracker.model_name or request_model,
        )
        response_summary_source = "responses.synthetic_completed_fallback"
    elif stream_error is not None:
        response_summary_source = "responses.stream_error_with_partial"

    return ResponsesStreamFinalize(
        completed_response_obj=completed_response_obj,
        completed_text=completed_text,
        synthesized_response=synthesized_response,
        synthetic_completed_event=synthetic_completed_event,
        response_summary_source=response_summary_source,
    )
