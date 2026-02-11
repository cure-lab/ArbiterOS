import asyncio
import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Optional, Union

import litellm
from langfuse import Langfuse
from dotenv import load_dotenv
from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_logger import CustomLogger, UserAPIKeyAuth
from litellm.types.utils import (
    CallTypesLiteral,
    Delta,
    LLMResponseTypes,
    Message,
    ModelResponse,
    ModelResponseStream,
    StreamingChoices,
)
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty

_console = Console()
_LOG_FILE = Path(__file__).resolve().parent.parent / "log" / "api_calls.jsonl"
_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
_LANGFUSE_NODE_LOG_FILE = (
    Path(__file__).resolve().parent.parent / "log" / "langfuse_nodes.jsonl"
)
_LANGFUSE_NODE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Ensure `.env` is loaded when LiteLLM imports this module via `litellm_config.yaml`.
# This makes Langfuse/MLflow callbacks work without manually exporting env vars.
load_dotenv(override=False)

# 剥去 assistant content 时记录的 category，只追加不因「包」而消耗；超 1000 时删最前（最早）的
_stripped_categories: list[str] = []
_MAX_STRIPPED_CATEGORIES = 1000


@dataclass
class _DeviceContext:
    device_key: str
    channel: str
    user_id: str
    has_explicit_user_id: bool
    latest_user_text: Optional[str]
    latest_user_fingerprint: Optional[str]
    reset_requested: bool


@dataclass
class _TraceState:
    trace_id: str
    device_key: str
    channel: str
    user_id: str
    sequence: int = 0
    last_user_fingerprint: Optional[str] = None
    last_reset_fingerprint: Optional[str] = None


_trace_state_lock = threading.Lock()
_trace_state_by_device: dict[str, _TraceState] = {}
_latest_user_id_by_channel: dict[str, str] = {}
_recent_response_keys: list[str] = []
_recent_response_key_set: set[str] = set()
_MAX_RECENT_RESPONSE_KEYS = 512
_recent_tool_result_keys: list[str] = []
_recent_tool_result_key_set: set[str] = set()
_MAX_RECENT_TOOL_RESULT_KEYS = 1024

_langfuse_client: Optional[Langfuse] = None
_langfuse_client_initialized = False

_CONVERSATION_LABEL_RE = re.compile(r'"conversation_label"\s*:\s*"([^"]+)"')
_CHANNEL_RE = re.compile(r'"channel"\s*:\s*"([^"]+)"')
_CURRENT_SESSION_HEADER_RE = re.compile(r"^\s*##\s*Current Session\s*$", re.MULTILINE)
_CURRENT_SESSION_CHANNEL_RE = re.compile(
    r"^\s*Channel\s*:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE
)
_CURRENT_SESSION_CHAT_ID_RE = re.compile(
    r"^\s*Chat ID\s*:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE
)
_RESET_PROMPT_RE = re.compile(
    r"^\s*a new session was started via /new or /reset\.",
    re.IGNORECASE,
)
_EXTERNAL_UNTRUSTED_BLOCK_RE = re.compile(
    r"<<<EXTERNAL_UNTRUSTED_CONTENT>>>.*?<<<END_EXTERNAL_UNTRUSTED_CONTENT>>>",
    re.DOTALL,
)
_SECURITY_NOTICE_RE = re.compile(
    r"SECURITY NOTICE:.*?(?=\n\n<<<EXTERNAL_UNTRUSTED_CONTENT>>>|$)",
    re.DOTALL,
)


def _to_json(obj: Any) -> Any:
    """转成可 JSON 序列化的结构"""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Exception):
        return {"_type": "Exception", "name": type(obj).__name__, "msg": str(obj)}
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if isinstance(obj, dict):
        return {k: _to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json(v) for v in obj]
    return str(obj)


def _save_json(hook: str, data: dict) -> None:
    """保存数据到 jsonl 文件"""
    entry = {
        "ts": datetime.now().isoformat(),
        "hook": hook,
        "data": _to_json(data),
    }
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, default=str)
        f.write("\n")
        f.flush()


def _save_langfuse_node_json(data: dict) -> None:
    entry = {
        "ts": datetime.now().isoformat(),
        "hook": "langfuse_node",
        "data": _to_json(data),
    }
    with open(_LANGFUSE_NODE_LOG_FILE, "a", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, default=str)
        f.write("\n")
        f.flush()


def _extract_text_from_message_content(content: Any) -> str:
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


def _extract_latest_message_text(messages: list[Any], *, role: str) -> Optional[str]:
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != role:
            continue
        text = _extract_text_from_message_content(msg.get("content"))
        if text.strip():
            return text
    return None


def _extract_first_message_text(messages: list[Any], *, role: str) -> Optional[str]:
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != role:
            continue
        text = _extract_text_from_message_content(msg.get("content"))
        if text.strip():
            return text
    return None


def _find_match_in_messages(messages: list[Any], pattern: re.Pattern[str]) -> Optional[str]:
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        text = _extract_text_from_message_content(msg.get("content"))
        if not text:
            continue
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


def _extract_current_session_from_text(text: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Parse nanobot-style system prompt section:
    '## Current Session\\nChannel: <channel>\\nChat ID: <chat_id>'
    """
    if not isinstance(text, str) or not text:
        return (None, None)
    header_match = _CURRENT_SESSION_HEADER_RE.search(text)
    if not header_match:
        return (None, None)

    # Only scan the tail starting at the header to avoid accidental matches
    # elsewhere in the system prompt.
    tail = text[header_match.start() :]
    channel_match = _CURRENT_SESSION_CHANNEL_RE.search(tail)
    chat_id_match = _CURRENT_SESSION_CHAT_ID_RE.search(tail)
    channel = channel_match.group(1).strip() if channel_match else None
    chat_id = chat_id_match.group(1).strip() if chat_id_match else None
    if channel:
        channel = _normalize_device_fragment(channel)
    if chat_id:
        chat_id = _normalize_device_fragment(chat_id)
    return (channel or None, chat_id or None)


def _extract_current_session_from_messages(messages: list[Any]) -> tuple[Optional[str], Optional[str]]:
    # Prefer the latest system prompt since it contains the current routing context.
    latest_system_text = _extract_latest_message_text(messages, role="system")
    channel, chat_id = _extract_current_session_from_text(latest_system_text)
    if channel or chat_id:
        return (channel, chat_id)

    # Fallback: scan other messages (rare but cheap for small histories).
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        text = _extract_text_from_message_content(msg.get("content"))
        ch, cid = _extract_current_session_from_text(text)
        if ch or cid:
            return (ch, cid)
    return (None, None)


def _normalize_device_fragment(fragment: str) -> str:
    normalized = re.sub(r"\s+", " ", fragment.strip())
    return normalized[:256]


def _is_reset_request_text(latest_user_text: Optional[str]) -> bool:
    if not latest_user_text:
        return False
    return bool(_RESET_PROMPT_RE.match(latest_user_text))


def _extract_device_key_hint_from_metadata(incoming: dict) -> Optional[str]:
    metadata = incoming.get("metadata")
    if not isinstance(metadata, dict):
        return None
    hinted_device_key = metadata.get("arbiteros_device_key")
    if not isinstance(hinted_device_key, str):
        return None
    hinted_device_key = hinted_device_key.strip()
    if not hinted_device_key:
        return None
    return _normalize_device_fragment(hinted_device_key)


def _parse_device_key(device_key: str) -> tuple[str, str]:
    raw_channel, _, raw_user_id = device_key.partition(":")
    channel = _normalize_device_fragment(raw_channel) if raw_channel else "unknown-channel"
    user_id = _normalize_device_fragment(raw_user_id) if raw_user_id else "unknown-user"
    return channel, user_id


def _get_latest_user_id_for_channel(channel: str) -> Optional[str]:
    if not channel or channel == "unknown-channel":
        return None
    with _trace_state_lock:
        hinted_user_id = _latest_user_id_by_channel.get(channel)
        if hinted_user_id:
            return hinted_user_id

        # Fallback for workers that only have trace state populated but not channel cache.
        latest_state: Optional[_TraceState] = None
        for state in _trace_state_by_device.values():
            if state.channel != channel or state.user_id.startswith("anonymous-"):
                continue
            if latest_state is None or state.sequence >= latest_state.sequence:
                latest_state = state

        if latest_state is not None:
            _latest_user_id_by_channel[channel] = latest_state.user_id
            return latest_state.user_id
        return None


def _build_device_context(incoming: dict) -> _DeviceContext:
    messages = incoming.get("messages")
    if not isinstance(messages, list):
        messages = []

    latest_user_text = _extract_latest_message_text(messages, role="user")
    latest_system_text = _extract_latest_message_text(messages, role="system")
    first_system_text = _extract_first_message_text(messages, role="system")

    channel_value = _find_match_in_messages(messages, _CHANNEL_RE)
    conversation_value = _find_match_in_messages(messages, _CONVERSATION_LABEL_RE)
    session_channel, session_chat_id = _extract_current_session_from_messages(messages)
    metadata_device_key_hint = _extract_device_key_hint_from_metadata(incoming)
    metadata_channel_hint: Optional[str] = None
    metadata_user_id_hint: Optional[str] = None
    if metadata_device_key_hint:
        parsed_channel, parsed_user_id = _parse_device_key(metadata_device_key_hint)
        metadata_channel_hint = parsed_channel
        metadata_user_id_hint = parsed_user_id

    channel = channel_value if channel_value else (session_channel or "unknown-channel")
    has_explicit_user_id = bool(
        (conversation_value and conversation_value.strip())
        or (session_chat_id and session_chat_id.strip())
    )
    raw_user_id = (
        conversation_value
        if (conversation_value and conversation_value.strip())
        else (session_chat_id if (session_chat_id and session_chat_id.strip()) else "unknown-user")
    )
    channel = _normalize_device_fragment(channel)
    if channel == "unknown-channel" and metadata_channel_hint and metadata_channel_hint != "unknown-channel":
        channel = metadata_channel_hint

    normalized_user_cmd = (latest_user_text or "").strip().lower()
    reset_requested = normalized_user_cmd in {"/new", "/reset"} or _is_reset_request_text(
        latest_user_text
    )

    if raw_user_id == "unknown-user" and reset_requested:
        # /new and /reset system turns often omit conversation_label; recover prior identity.
        if metadata_user_id_hint and not metadata_user_id_hint.startswith("anonymous-"):
            raw_user_id = metadata_user_id_hint
        else:
            hinted_user_id = _get_latest_user_id_for_channel(channel)
            if hinted_user_id:
                raw_user_id = hinted_user_id

    if raw_user_id == "unknown-user":
        fallback_source = first_system_text or latest_system_text or "openclaw-unknown-user"
        fallback_hash = hashlib.sha256(
            fallback_source.encode("utf-8", errors="ignore")
        ).hexdigest()[:12]
        raw_user_id = f"anonymous-{fallback_hash}"

    user_id = _normalize_device_fragment(raw_user_id)
    device_key = f"{channel}:{user_id}"

    latest_user_fingerprint = (
        hashlib.sha256(
            latest_user_text.encode("utf-8", errors="ignore")
        ).hexdigest()
        if latest_user_text
        else None
    )

    return _DeviceContext(
        device_key=device_key,
        channel=channel,
        user_id=user_id,
        has_explicit_user_id=has_explicit_user_id,
        latest_user_text=latest_user_text,
        latest_user_fingerprint=latest_user_fingerprint,
        reset_requested=reset_requested,
    )


def _new_trace_id(*, device_key: str, user_fingerprint: Optional[str]) -> str:
    seed = (
        f"{device_key}:{datetime.now().isoformat()}:{os.getpid()}:"
        f"{user_fingerprint or 'none'}"
    )
    return Langfuse.create_trace_id(seed=seed)


def _ensure_trace_state(context: _DeviceContext) -> tuple[_TraceState, bool]:
    with _trace_state_lock:
        current = _trace_state_by_device.get(context.device_key)
        should_rotate = False
        if current is None:
            should_rotate = True
        elif context.reset_requested:
            # Allow repeated resets with same synthetic text after conversation progressed,
            # while still de-duping immediate duplicate deliveries of the same reset turn.
            if not context.latest_user_fingerprint:
                should_rotate = True
            elif (
                current.last_reset_fingerprint != context.latest_user_fingerprint
                or current.last_user_fingerprint != context.latest_user_fingerprint
            ):
                should_rotate = True

        if should_rotate:
            current = _TraceState(
                trace_id=_new_trace_id(
                    device_key=context.device_key,
                    user_fingerprint=context.latest_user_fingerprint,
                ),
                device_key=context.device_key,
                channel=context.channel,
                user_id=context.user_id,
                sequence=0,
                last_user_fingerprint=None,
                last_reset_fingerprint=(
                    context.latest_user_fingerprint if context.reset_requested else None
                ),
            )
            _trace_state_by_device[context.device_key] = current
            if current.channel != "unknown-channel" and not current.user_id.startswith(
                "anonymous-"
            ):
                _latest_user_id_by_channel[current.channel] = current.user_id
            return current, True

        if context.reset_requested and context.latest_user_fingerprint:
            current.last_reset_fingerprint = context.latest_user_fingerprint
        if current.channel != "unknown-channel" and not current.user_id.startswith(
            "anonymous-"
        ):
            _latest_user_id_by_channel[current.channel] = current.user_id
        return current, False


def _resolve_trace_state_from_metadata(
    incoming: dict, *, context: _DeviceContext
) -> Optional[_TraceState]:
    metadata = incoming.get("metadata")
    if not isinstance(metadata, dict):
        return None

    trace_id = metadata.get("arbiteros_trace_id")
    device_key = metadata.get("arbiteros_device_key")
    if not isinstance(trace_id, str) or not trace_id:
        return None
    if not isinstance(device_key, str) or not device_key:
        return None

    with _trace_state_lock:
        current = _trace_state_by_device.get(device_key)
        if current is not None and current.trace_id == trace_id:
            if context.latest_user_fingerprint:
                current.last_user_fingerprint = context.latest_user_fingerprint
                if context.reset_requested:
                    current.last_reset_fingerprint = context.latest_user_fingerprint
            return current

        derived_channel, _, derived_user_id = device_key.partition(":")
        channel = (
            context.channel
            if context.channel != "unknown-channel"
            else (derived_channel or "unknown-channel")
        )
        user_id = context.user_id
        if user_id.startswith("anonymous-") and derived_user_id:
            user_id = derived_user_id

        restored_state = _TraceState(
            trace_id=trace_id,
            device_key=device_key,
            channel=channel,
            user_id=user_id,
            sequence=0,
            last_user_fingerprint=context.latest_user_fingerprint,
            last_reset_fingerprint=(
                context.latest_user_fingerprint if context.reset_requested else None
            ),
        )
        _trace_state_by_device[device_key] = restored_state
        if restored_state.channel != "unknown-channel" and not restored_state.user_id.startswith(
            "anonymous-"
        ):
            _latest_user_id_by_channel[restored_state.channel] = restored_state.user_id
        return restored_state


def _next_node_sequence(state: _TraceState) -> int:
    with _trace_state_lock:
        state.sequence += 1
        return state.sequence


def _should_emit_response_once(state: _TraceState, payload: dict) -> bool:
    dedupe_payload = {
        "trace_id": state.trace_id,
        "raw_content": payload.get("raw_content"),
        "raw_tool_calls": payload.get("raw_tool_calls"),
        "transformed_content": payload.get("transformed_content"),
    }
    key = hashlib.sha256(
        json.dumps(dedupe_payload, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8", errors="ignore"
        )
    ).hexdigest()
    with _trace_state_lock:
        if key in _recent_response_key_set:
            return False
        _recent_response_key_set.add(key)
        _recent_response_keys.append(key)
        if len(_recent_response_keys) > _MAX_RECENT_RESPONSE_KEYS:
            oldest = _recent_response_keys.pop(0)
            _recent_response_key_set.discard(oldest)
        return True


def _get_langfuse_client() -> Optional[Langfuse]:
    global _langfuse_client, _langfuse_client_initialized
    if _langfuse_client_initialized:
        return _langfuse_client

    _langfuse_client_initialized = True
    if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
        return None

    timeout = int(os.getenv("ARBITEROS_LANGFUSE_TIMEOUT", os.getenv("LANGFUSE_TIMEOUT", "15")))
    flush_at = int(os.getenv("ARBITEROS_LANGFUSE_FLUSH_AT", "1"))
    flush_interval = float(os.getenv("ARBITEROS_LANGFUSE_FLUSH_INTERVAL", "1"))

    try:
        _langfuse_client = Langfuse(
            timeout=timeout,
            flush_at=flush_at,
            flush_interval=flush_interval,
        )
    except Exception as exc:
        _save_json("langfuse_init_error", {"error": str(exc)})
        _langfuse_client = None
    return _langfuse_client


def _emit_langfuse_node(
    *,
    state: _TraceState,
    node_type: str,
    observation_type: str,
    name: str,
    input_payload: Any = None,
    output_payload: Any = None,
    metadata: Optional[dict] = None,
    model: Optional[str] = None,
    level: Optional[str] = None,
    status_message: Optional[str] = None,
) -> None:
    seq = _next_node_sequence(state)
    node_metadata = {
        "source": "arbiteros_kernel_callback",
        "node_type": node_type,
        "node_sequence": seq,
        "device_key": state.device_key,
        "channel": state.channel,
        "user_id": state.user_id,
        **(metadata or {}),
    }
    if isinstance(level, str) and level.strip():
        node_metadata["langfuse_level"] = level.strip().upper()
    if isinstance(status_message, str) and status_message.strip():
        node_metadata["langfuse_status_message"] = status_message.strip()

    node_log = {
        "trace_id": state.trace_id,
        "node_type": node_type,
        "observation_type": observation_type,
        "name": name,
        "input": input_payload,
        "output": output_payload,
        "metadata": node_metadata,
    }
    if isinstance(level, str) and level.strip():
        node_log["level"] = level.strip().upper()
    if isinstance(status_message, str) and status_message.strip():
        node_log["status_message"] = status_message.strip()
    _save_langfuse_node_json(node_log)

    lf = _get_langfuse_client()
    if lf is None:
        return

    try:
        start_kwargs: dict[str, Any] = {
            "trace_context": {"trace_id": state.trace_id},
            "name": name,
            "as_type": "generation" if observation_type == "generation" else observation_type,
            "input": input_payload,
            "output": output_payload,
            "metadata": node_metadata,
        }
        if observation_type == "generation":
            start_kwargs["model"] = model
        if isinstance(level, str) and level.strip():
            start_kwargs["level"] = level.strip().upper()
        if isinstance(status_message, str) and status_message.strip():
            start_kwargs["status_message"] = status_message.strip()

        try:
            obs = lf.start_observation(**start_kwargs)
        except TypeError:
            if "level" not in start_kwargs and "status_message" not in start_kwargs:
                raise
            fallback_kwargs = {
                k: v
                for k, v in start_kwargs.items()
                if k not in {"level", "status_message"}
            }
            obs = lf.start_observation(**fallback_kwargs)
            try:
                obs_update: dict[str, Any] = {}
                if isinstance(level, str) and level.strip():
                    obs_update["level"] = level.strip().upper()
                if isinstance(status_message, str) and status_message.strip():
                    obs_update["status_message"] = status_message.strip()
                if obs_update:
                    obs.update(**obs_update)
            except Exception:
                pass

        obs.update_trace(
            name=f"openclaw.session:{state.device_key}",
            user_id=state.user_id,
            session_id=state.device_key,
            metadata={
                "source": "arbiteros_kernel_callback",
                "channel": state.channel,
                "device_key": state.device_key,
            },
        )
        obs.end()
    except Exception as exc:
        _save_json(
            "langfuse_emit_error",
            {
                "trace_id": state.trace_id,
                "node_type": node_type,
                "name": name,
                "error": str(exc),
            },
        )


def _flush_langfuse() -> None:
    lf = _get_langfuse_client()
    if lf is None:
        return
    try:
        lf.flush()
    except Exception as exc:
        _save_json("langfuse_flush_error", {"error": str(exc)})


def _safe_json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_tool_calls(message_dict: Optional[dict]) -> list[dict]:
    if not isinstance(message_dict, dict):
        return []
    raw_tool_calls = message_dict.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return []
    out: list[dict] = []
    for tool_call in raw_tool_calls:
        if isinstance(tool_call, dict):
            out.append(tool_call)
    return out


def _extract_tool_results(messages: list[Any]) -> list[dict]:
    out: list[dict] = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        text_content = _extract_text_from_message_content(content)
        if not text_content and isinstance(content, str):
            text_content = content
        if not text_content:
            continue
        tool_call_id = msg.get("tool_call_id")
        out.append(
            {
                "tool_call_id": tool_call_id if isinstance(tool_call_id, str) else None,
                "content": text_content,
                "message_index": idx,
            }
        )
    return out


def _extract_tool_call_details_by_call_id(
    messages: list[Any],
) -> dict[str, dict[str, Any]]:
    tool_call_details_by_call_id: dict[str, dict[str, Any]] = {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = tool_call.get("id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                continue
            fn = tool_call.get("function")
            tool_name = (
                fn.get("name")
                if isinstance(fn, dict) and isinstance(fn.get("name"), str)
                else "unknown_tool"
            )
            raw_arguments = fn.get("arguments") if isinstance(fn, dict) else None
            parsed_arguments = (
                _safe_json_loads(raw_arguments)
                if isinstance(raw_arguments, str)
                else None
            )
            tool_call_details_by_call_id[tool_call_id] = {
                "tool_name": tool_name,
                "tool_arguments": (
                    parsed_arguments if parsed_arguments is not None else raw_arguments
                ),
            }
    return tool_call_details_by_call_id


def _extract_json_dict_from_text(text: str) -> Optional[dict]:
    parsed = _safe_json_loads(text)
    if isinstance(parsed, dict):
        return parsed

    stripped = text.strip()
    if stripped.startswith("```"):
        inner = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        inner = re.sub(r"\s*```$", "", inner)
        parsed = _safe_json_loads(inner.strip())
        if isinstance(parsed, dict):
            return parsed

    first_curly = stripped.find("{")
    last_curly = stripped.rfind("}")
    if first_curly != -1 and last_curly > first_curly:
        parsed = _safe_json_loads(stripped[first_curly : last_curly + 1])
        if isinstance(parsed, dict):
            return parsed
    return None


def _should_emit_tool_result_once(state: _TraceState, payload: dict) -> bool:
    dedupe_payload = {
        "trace_id": state.trace_id,
        "tool_call_id": payload.get("tool_call_id"),
        "tool_name": payload.get("tool_name"),
        "content": payload.get("content"),
    }
    key = hashlib.sha256(
        json.dumps(dedupe_payload, ensure_ascii=False, sort_keys=True, default=str).encode(
            "utf-8", errors="ignore"
        )
    ).hexdigest()
    with _trace_state_lock:
        if key in _recent_tool_result_key_set:
            return False
        _recent_tool_result_key_set.add(key)
        _recent_tool_result_keys.append(key)
        if len(_recent_tool_result_keys) > _MAX_RECENT_TOOL_RESULT_KEYS:
            oldest = _recent_tool_result_keys.pop(0)
            _recent_tool_result_key_set.discard(oldest)
        return True


def _sanitize_error_text_for_langfuse(text: str) -> str:
    # Remove prompt-injection wrapper blocks from tool error payloads.
    sanitized = _SECURITY_NOTICE_RE.sub("[security notice omitted]", text)
    sanitized = _EXTERNAL_UNTRUSTED_BLOCK_RE.sub("[external untrusted content omitted]", sanitized)
    sanitized = sanitized.replace("<<<EXTERNAL_UNTRUSTED_CONTENT>>>", "")
    sanitized = sanitized.replace("<<<END_EXTERNAL_UNTRUSTED_CONTENT>>>", "")
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
    max_len = 1200
    if len(sanitized) > max_len:
        sanitized = f"{sanitized[:max_len]} ... [truncated]"
    return sanitized


def _format_tool_result_output_for_langfuse(content: Any) -> dict:
    payload: Optional[dict]
    if isinstance(content, dict):
        payload = content
    elif isinstance(content, str):
        payload = _extract_json_dict_from_text(content)
    else:
        payload = None

    if isinstance(payload, dict) and isinstance(payload.get("content"), dict):
        payload = payload.get("content")

    level: Optional[str] = None
    status_message: Optional[str] = None

    if isinstance(payload, dict):
        payload = dict(payload)
        status = payload.get("status")
        tool = payload.get("tool")
        error = payload.get("error")
        warning = payload.get("warning")
        warnings = payload.get("warnings")

        if isinstance(error, str):
            payload["error"] = _sanitize_error_text_for_langfuse(error)
        if isinstance(warning, str):
            payload["warning"] = _sanitize_error_text_for_langfuse(warning)
        if isinstance(warnings, list):
            payload["warnings"] = [
                _sanitize_error_text_for_langfuse(item) if isinstance(item, str) else item
                for item in warnings
            ]

        raw_level = payload.get("level")
        if isinstance(raw_level, str):
            normalized_level = raw_level.strip().upper()
            if normalized_level in {"DEBUG", "DEFAULT", "WARNING", "ERROR"}:
                level = normalized_level

        if level is None and isinstance(status, str):
            lowered_status = status.strip().lower()
            if lowered_status in {"error", "failed", "failure"}:
                level = "ERROR"
            elif lowered_status in {"warning", "warn"}:
                level = "WARNING"

        if level is None and isinstance(error, str) and error.strip():
            level = "ERROR"
        if level is None and (
            (isinstance(warning, str) and warning.strip())
            or (
                isinstance(warnings, list)
                and any(isinstance(item, str) and item.strip() for item in warnings)
            )
        ):
            level = "WARNING"

        if isinstance(payload.get("status_message"), str):
            status_message = payload.get("status_message")
        elif level == "ERROR":
            status_message = (
                payload.get("error")
                if isinstance(payload.get("error"), str)
                else (
                    f"{tool if isinstance(tool, str) else 'tool'} returned error status"
                    if isinstance(status, str)
                    else "tool call failed"
                )
            )
        elif level == "WARNING":
            warning_text = payload.get("warning")
            if isinstance(warning_text, str):
                status_message = warning_text
            elif isinstance(payload.get("warnings"), list):
                first_warning = next(
                    (
                        item
                        for item in payload.get("warnings")
                        if isinstance(item, str) and item.strip()
                    ),
                    None,
                )
                if isinstance(first_warning, str):
                    status_message = first_warning
            if status_message is None:
                status_message = (
                    f"{tool if isinstance(tool, str) else 'tool'} returned warning status"
                )

        if isinstance(status_message, str):
            status_message = re.sub(r"\s+", " ", status_message).strip()
            if len(status_message) > 500:
                status_message = f"{status_message[:500]} ... [truncated]"
            if not status_message:
                status_message = None
        return {
            "output": {"content": payload},
            "level": level,
            "status_message": status_message,
        }

    if isinstance(content, str) and (
        "<<<EXTERNAL_UNTRUSTED_CONTENT>>>" in content
        or "<<<END_EXTERNAL_UNTRUSTED_CONTENT>>>" in content
        or "SECURITY NOTICE:" in content
    ):
        return {
            "output": {"content": _sanitize_error_text_for_langfuse(content)},
            "level": None,
            "status_message": None,
        }

    return {"output": {"content": content}, "level": None, "status_message": None}


def _emit_tool_result_nodes_if_needed(request_data: dict, state: _TraceState) -> None:
    incoming = request_data if isinstance(request_data, dict) else {}
    messages = incoming.get("messages")
    if not isinstance(messages, list):
        return

    tool_results = _extract_tool_results(messages)
    if not tool_results:
        return

    tool_call_details_by_call_id = _extract_tool_call_details_by_call_id(messages)
    emitted_any = False
    for tool_result in tool_results:
        tool_call_id = tool_result.get("tool_call_id")
        tool_details = (
            tool_call_details_by_call_id.get(tool_call_id, {})
            if isinstance(tool_call_id, str)
            else {}
        )
        tool_name = (
            tool_details.get("tool_name")
            if isinstance(tool_details, dict)
            and isinstance(tool_details.get("tool_name"), str)
            else "unknown_tool"
        )
        tool_arguments = (
            tool_details.get("tool_arguments")
            if isinstance(tool_details, dict)
            else None
        )
        content = tool_result.get("content")
        if not _should_emit_tool_result_once(
            state,
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "content": content,
            },
        ):
            continue

        formatted_result = _format_tool_result_output_for_langfuse(content)
        _emit_langfuse_node(
            state=state,
            node_type="tool_result",
            observation_type="tool",
            name=f"tool.{tool_name}.result",
            input_payload={
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": tool_arguments,
            },
            output_payload=formatted_result.get("output"),
            metadata={
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "message_index": tool_result.get("message_index"),
            },
            level=formatted_result.get("level"),
            status_message=formatted_result.get("status_message"),
        )
        emitted_any = True

    if emitted_any:
        _flush_langfuse()


def _extract_structured_category_content(message_dict: Optional[dict]) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(message_dict, dict):
        return (None, None)
    content = message_dict.get("content")
    parsed = _safe_json_loads(content)
    if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
        category = parsed.get("category")
        return (
            category if isinstance(category, str) else None,
            parsed.get("content"),
        )
    return (None, content if isinstance(content, str) else None)


def _emit_input_node_if_needed(context: _DeviceContext, state: _TraceState) -> None:
    if not context.latest_user_text or not context.latest_user_fingerprint:
        return

    should_emit = False
    with _trace_state_lock:
        if state.last_user_fingerprint != context.latest_user_fingerprint:
            state.last_user_fingerprint = context.latest_user_fingerprint
            should_emit = True

    if not should_emit:
        return

    _emit_langfuse_node(
        state=state,
        node_type="input",
        observation_type="span",
        name="openclaw.input",
        input_payload={"text": context.latest_user_text},
        metadata={
            "text_preview": context.latest_user_text[:300],
            "reset_requested": context.reset_requested,
        },
    )


def _inject_trace_metadata(data: dict, state: _TraceState) -> dict:
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = {
        **metadata,
        "arbiteros_trace_id": state.trace_id,
        "arbiteros_device_key": state.device_key,
    }
    return {**data, "metadata": metadata}


def _emit_response_nodes(
    *,
    request_data: dict,
    response_before_transform: Optional[dict],
    response_after_transform: Optional[dict],
) -> None:
    incoming = request_data if isinstance(request_data, dict) else {}
    context = _build_device_context(incoming)
    state = _resolve_trace_state_from_metadata(incoming, context=context)
    if state is None:
        state, _ = _ensure_trace_state(context)

    if not _should_emit_response_once(
        state,
        {
            "raw_content": (
                response_before_transform.get("content")
                if isinstance(response_before_transform, dict)
                else None
            ),
            "raw_tool_calls": (
                response_before_transform.get("tool_calls")
                if isinstance(response_before_transform, dict)
                else None
            ),
            "transformed_content": (
                response_after_transform.get("content")
                if isinstance(response_after_transform, dict)
                else None
            ),
        },
    ):
        return

    model_name = incoming.get("model")
    model = model_name if isinstance(model_name, str) else None

    tool_calls = _extract_tool_calls(response_before_transform)
    if tool_calls:
        for tool_call in tool_calls:
            fn = tool_call.get("function") if isinstance(tool_call, dict) else None
            tool_name = (
                fn.get("name")
                if isinstance(fn, dict) and isinstance(fn.get("name"), str)
                else "unknown_tool"
            )
            tool_args = fn.get("arguments") if isinstance(fn, dict) else None
            _emit_langfuse_node(
                state=state,
                node_type="tool_call",
                observation_type="tool",
                name=f"tool.{tool_name}",
                input_payload={"arguments": tool_args},
                output_payload=None,
                metadata={
                    "tool_call_id": tool_call.get("id"),
                    "tool_name": tool_name,
                },
            )
        _flush_langfuse()
        return

    category, structured_content = _extract_structured_category_content(response_before_transform)
    raw_output_content = (
        response_before_transform.get("content")
        if isinstance(response_before_transform, dict)
        else None
    )
    output_content = None
    if isinstance(response_after_transform, dict) and isinstance(
        response_after_transform.get("content"), str
    ):
        output_content = response_after_transform.get("content")
    elif structured_content is not None:
        output_content = structured_content

    if category and category != "EXECUTION_CORE__RESPOND":
        _emit_langfuse_node(
            state=state,
            node_type="kernel_step",
            observation_type="agent",
            name=f"kernel.{category.lower()}",
            input_payload={"content": structured_content},
            output_payload=None,
            metadata={"category": category},
        )

    if (
        isinstance(response_before_transform, dict)
        and isinstance(response_after_transform, dict)
        and response_before_transform.get("content") != response_after_transform.get("content")
    ):
        _emit_langfuse_node(
            state=state,
            node_type="transform",
            observation_type="span",
            name="kernel.transform.response_content",
            input_payload={"before": response_before_transform.get("content")},
            output_payload={"after": response_after_transform.get("content")},
            metadata={"transform": "strip_category_wrapper"},
        )

    _emit_langfuse_node(
        state=state,
        node_type="output",
        observation_type="generation",
        name="openclaw.output",
        input_payload={"content": raw_output_content, "category": category},
        output_payload={"content": output_content, "category": category},
        metadata={"category": category},
        model=model,
    )
    _flush_langfuse()


def _emit_failure_node(request_data: Optional[dict], original_exception: Exception) -> None:
    incoming = request_data if isinstance(request_data, dict) else {}
    context = _build_device_context(incoming)
    state = _resolve_trace_state_from_metadata(incoming, context=context)
    if state is None:
        state, _ = _ensure_trace_state(context)
    _emit_langfuse_node(
        state=state,
        node_type="failure",
        observation_type="span",
        name="openclaw.failure",
        input_payload=None,
        output_payload={"error": str(original_exception)},
        metadata={"error_type": type(original_exception).__name__},
    )
    _flush_langfuse()


# ---------------------------------------------------------------------------
# 响应修改规则（流式 + 非流式）：用于在 post_call_success 时改写返回给调用方的内容
# - 若有 tool_calls：不改动
# - 若为 content 且为 JSON 字符串（含 category/content）：只保留内层 content，去掉 category，
#   并正向记录剥去的 category 到 _stripped_categories，供 pre_call 时把 history 包回
# ---------------------------------------------------------------------------
def _response_transform_content_only(data: dict, message_dict: dict) -> Optional[dict]:
    global _stripped_categories
    if message_dict.get("tool_calls"):
        return message_dict
    content = message_dict.get("content")
    if not isinstance(content, str) or not content.strip():
        return message_dict
    try:
        inner = json.loads(content)
        if isinstance(inner, dict) and "content" in inner:
            category = inner.get("category", "")
            _stripped_categories.append(category)
            if len(_stripped_categories) > _MAX_STRIPPED_CATEGORIES:
                _stripped_categories.pop(0)
            out = {**message_dict, "content": inner["content"]}
            return out
    except (json.JSONDecodeError, TypeError):
        pass
    return message_dict


def _is_structured_content(s: str) -> bool:
    """判断 content 是否已经是带 category/content 的结构化 JSON 字符串"""
    if not s or not isinstance(s, str):
        return False
    try:
        obj = json.loads(s)
        return isinstance(obj, dict) and "content" in obj
    except (json.JSONDecodeError, TypeError):
        return False


def _extract_text_to_wrap(msg: dict) -> tuple[Optional[str], Optional[Any], Optional[int]]:
    """
    从一条 assistant 消息里取出需要包结构的纯文本。
    - content 为字符串：返回 (content, None, None)，由调用方替换整条 content。
    - content 为列表 [{"type":"text","text":"..."}]：返回 (part["text"], content_list, part_index)，由调用方替换 part["text"]。
    - 无需处理或已结构化：返回 (None, None, None)。
    """
    content = msg.get("content")
    # 格式1: content 是字符串
    if isinstance(content, str):
        if not content.strip():
            return (None, None, None)
        if _is_structured_content(content):
            return (None, None, None)
        return (content, None, None)
    # 格式2: content 是列表，如 [{"type": "text", "text": "..."}]
    if isinstance(content, list):
        for idx, part in enumerate(content):
            if not isinstance(part, dict):
                continue
            if part.get("type") != "text":
                continue
            text = part.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            if _is_structured_content(text):
                continue
            return (text, content, idx)
    return (None, None, None)


def _wrap_messages_with_categories(data: dict) -> dict:
    """在 pre_call 前把 incoming 里 role=assistant 且 content 有文本的 history 从后往前包回结构。
    包的时候不消耗 list：从 _stripped_categories 末尾往前按位置取（最后一个 assistant 对应 list[-1]，
    倒数第二个对应 list[-2]…），与剥去的 history 一一对应。list 只在剥时追加，超 1000 删最前的。
    """
    global _stripped_categories
    messages = data.get("messages")
    if not messages or not _stripped_categories:
        return data
    messages = list(messages)
    idx_from_end = 0  # 当前包的是「从末尾数第几个」assistant，0=最后一个
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        if msg.get("tool_calls"):
            continue
        text, content_list, part_idx = _extract_text_to_wrap(msg)
        if text is None:
            continue
        if idx_from_end >= len(_stripped_categories):
            break
        category = _stripped_categories[-(idx_from_end + 1)]
        idx_from_end += 1
        wrapped = json.dumps({"category": category, "content": text}, ensure_ascii=False)
        if content_list is not None and part_idx is not None:
            new_parts = list(content_list)
            new_parts[part_idx] = {**new_parts[part_idx], "text": wrapped}
            messages[i] = {**msg, "content": new_parts}
        else:
            messages[i] = {**msg, "content": wrapped}
    return {**data, "messages": messages}


response_transform: Optional[Any] = _response_transform_content_only
stream_chunk_transform: Optional[Any] = None


# This file includes the custom callbacks for LiteLLM Proxy
# Once defined, these can be passed in proxy_config.yaml
class MyCustomHandler(CustomLogger):
    #### CALL HOOKS - proxy only ####
    """
    Control the modify incoming / outgoung data before calling the model
    """

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: "DualCache",
        data: dict,
        call_type: CallTypesLiteral,
    ) -> Optional[
        Union[Exception, str, dict]
    ]:  # raise exception if invalid, return a str for the user to receive - if rejected, or return a modified dictionary for passing into litellm
        # 先把 history 里 assistant 的 content 按剥去时记录的 category 从后往前包回结构，再请求
        data = _wrap_messages_with_categories(data)
        context = _build_device_context(data)
        state, created_new_trace = _ensure_trace_state(context)
        if created_new_trace:
            _emit_langfuse_node(
                state=state,
                node_type="trace_start",
                observation_type="span",
                name="openclaw.trace.start",
                input_payload={
                    "reason": "reset_command" if context.reset_requested else "new_device_or_session"
                },
                metadata={"reset_requested": context.reset_requested},
            )
        _emit_input_node_if_needed(context, state)
        _emit_tool_result_nodes_if_needed(data, state)
        data = _inject_trace_metadata(data, state)
        filtered_data = {
            k: data[k] for k in ["model", "messages", "tools", "metadata"] if k in data
        }
        _console.print(
            Panel(
                Pretty(filtered_data),
                title="Pre Call Hook - Incoming Data",
            )
        )
        _save_json("pre_call", {"call_type": call_type, "incoming": filtered_data})
        return data

    async def async_post_call_failure_hook(
        self,
        request_data: dict,
        original_exception: Exception,
        user_api_key_dict: UserAPIKeyAuth,
        traceback_str: Optional[str] = None,
    ) -> Any:
        _console.print(
            Panel(
                Pretty(original_exception),
                title="Post Call Failure Hook - Original Exception",
            )
        )
        _console.print(
            Panel(
                Pretty(traceback_str),
                title="Post Call Failure Hook - Traceback String",
            )
        )
        _emit_failure_node(request_data, original_exception)

    async def async_post_call_success_hook(
        self,
        data: dict,
        user_api_key_dict: UserAPIKeyAuth,
        response: LLMResponseTypes,
    ) -> Any:
        # data is the original request data
        # response is the response from the LLM API
        # LiteLLM may return either a ChatCompletions-like ModelResponse (with `.choices`)
        # or an OpenAI Responses API response (no `.choices`, uses `output/output_text`).
        choices = getattr(response, "choices", None)
        is_chat_completion = isinstance(choices, list)

        msg: Any = None
        if is_chat_completion:
            msg = choices[0].message if choices else None
        else:
            # Synthesize a chat-style message dict from Responses API payload so
            # existing logging/Langfuse/transform logic can continue to work.
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
            if response_dump is None:
                response_dump = _to_json(response)

            output_text = (
                _extract_text_from_responses_output(response_dump)
                if isinstance(response_dump, dict)
                else ""
            )
            provider_fields: dict[str, Any] = {"format": "openai-responses-v1"}
            if isinstance(response_dump, dict):
                for k in ("id", "model", "status"):
                    v = response_dump.get(k)
                    if isinstance(v, (str, int, float, bool)) and v is not None:
                        provider_fields[k] = v
            msg = {
                "content": output_text if output_text else None,
                "role": "assistant",
                "tool_calls": None,
                "function_call": None,
                "provider_specific_fields": provider_fields,
                "annotations": [],
            }
        _console.print(
            Panel(
                Pretty(msg),
                title="Post Call Success Hook - Response",
            )
        )
        _save_json("post_call_success", {"response": msg})

        raw_msg_dict = (
            _to_json(msg)
            if isinstance(msg, dict)
            else (
                msg.model_dump()
                if hasattr(msg, "model_dump")
                else (msg.dict() if hasattr(msg, "dict") else None)
            )
        )
        final_msg_dict = raw_msg_dict

        # 若配置了 response_transform，用其返回值改写返回给调用方的内容
        if msg is not None and response_transform is not None:
            msg_dict = raw_msg_dict
            if msg_dict is not None:
                if asyncio.iscoroutinefunction(response_transform):
                    modified_dict = await response_transform(data, msg_dict)
                else:
                    modified_dict = response_transform(data, msg_dict)
                if modified_dict is not None and isinstance(modified_dict, dict):
                    final_msg_dict = modified_dict
                    try:
                        if is_chat_completion:
                            response.choices[0].message = Message(**modified_dict)
                        else:
                            # Best-effort for Responses API objects: update `output_text` when present.
                            new_content = modified_dict.get("content")
                            if isinstance(new_content, str) and hasattr(response, "output_text"):
                                setattr(response, "output_text", new_content)
                    except Exception:
                        pass
        _emit_response_nodes(
            request_data=data,
            response_before_transform=raw_msg_dict,
            response_after_transform=final_msg_dict,
        )
        return response

    async def async_post_call_streaming_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        response: str,
    ) -> Any:
        _console.print(
            Panel(
                Pretty(response),
                title="Streaming response received",
            )
        )

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        response: Any,
        request_data: dict,
    ) -> AsyncGenerator[Any, None]:
        """流式：若配置了 response_transform，则先收齐再改再流式输出；否则边收边 yield 并写 jsonl。"""
        collected: list = []
        apply_transform = response_transform is not None

        async for chunk in response:
            if isinstance(chunk, (ModelResponseStream, ModelResponse)):
                collected.append(chunk)
            if not apply_transform:
                out = chunk
                if stream_chunk_transform is not None:
                    if asyncio.iscoroutinefunction(stream_chunk_transform):
                        out = await stream_chunk_transform(request_data, chunk)
                    else:
                        out = stream_chunk_transform(request_data, chunk)
                    if out is None:
                        out = chunk
                yield out

        if not collected:
            return

        try:
            from litellm.main import stream_chunk_builder
            complete = stream_chunk_builder(chunks=collected)
        except Exception:
            complete = None

        if complete is None or not getattr(complete, "choices", None):
            # 合并失败：无 transform 时已逐 chunk yield；有 transform 时无法安全重放，不 yield
            if not apply_transform:
                full_content_parts = []
                for c in collected:
                    if isinstance(c, (ModelResponseStream, ModelResponse)):
                        part = litellm.get_response_string(response_obj=c)
                        if part:
                            full_content_parts.append(part)
                if full_content_parts:
                    _save_json("post_call_success", {"response": {"content": "".join(full_content_parts), "role": "assistant", "tool_calls": None, "function_call": None, "provider_specific_fields": {}, "annotations": []}})
            return

        msg = complete.choices[0].message
        msg_dict = _to_json(msg) if isinstance(msg, dict) else (msg.model_dump() if hasattr(msg, "model_dump") else (msg.dict() if hasattr(msg, "dict") else None))
        raw_msg_dict = msg_dict

        # 先存 modify 之前的版本（带 category/content 的原始结构）
        _save_json("post_call_success", {"response": msg_dict})

        if apply_transform and msg_dict is not None:
            if asyncio.iscoroutinefunction(response_transform):
                modified_dict = await response_transform(request_data, msg_dict)
            else:
                modified_dict = response_transform(request_data, msg_dict)
            if modified_dict is not None and isinstance(modified_dict, dict):
                msg_dict = modified_dict

        _emit_response_nodes(
            request_data=request_data,
            response_before_transform=raw_msg_dict,
            response_after_transform=msg_dict,
        )

        if apply_transform and msg_dict is not None:
            # 用修改后的内容重放为流式：拆成多个小 chunk 逐个 yield，避免下游按字符拆导致显示异常
            content = msg_dict.get("content") if isinstance(msg_dict.get("content"), str) else ""
            tool_calls = msg_dict.get("tool_calls")
            first = collected[0]
            stream_id = getattr(first, "id", None) or ""
            stream_created = getattr(first, "created", None) or 0
            stream_model = getattr(first, "model", None)
            _chunk_size = 64
            pieces = [content[i : i + _chunk_size] for i in range(0, len(content), _chunk_size)] if content else [""]
            for i, piece in enumerate(pieces):
                is_last = i == len(pieces) - 1
                delta = Delta(content=piece or None, tool_calls=tool_calls if is_last else None)
                choice = StreamingChoices(delta=delta, finish_reason="stop" if is_last else None, index=0)
                out_chunk = ModelResponseStream(choices=[choice], id=stream_id, created=stream_created, model=stream_model)
                yield out_chunk


proxy_handler_instance = MyCustomHandler()
