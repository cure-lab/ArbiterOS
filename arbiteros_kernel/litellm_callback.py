import asyncio
import copy
import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Optional, Union

try:
    from arbiteros_kernel.instruction_parsing import InstructionBuilder
except ImportError:
    InstructionBuilder = None  # type: ignore[misc, assignment]

try:
    from arbiteros_kernel.instruction_parsing.instruction_security_registry import (
        get_instruction_security,
    )
except ImportError:
    get_instruction_security = None  # type: ignore[misc, assignment]

import litellm
from langfuse import Langfuse
from dotenv import load_dotenv
from arbiteros_kernel.langfuse_env import ensure_langfuse_env_compat
from arbiteros_kernel.policy_check import check_response_policy
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
_TRACE_STATE_FILE = Path(__file__).resolve().parent.parent / "log" / "trace_state.json"
_PRECALL_LOG_FILE = Path(__file__).resolve().parent.parent / "log" / "precall.jsonl"
_INSTRUCTION_LOG_DIR = Path(__file__).resolve().parent.parent / "log"
_INSTRUCTION_LOG_DIR.mkdir(parents=True, exist_ok=True)

# Ensure `.env` is loaded when LiteLLM imports this module via `litellm_config.yaml`.
# This makes Langfuse/MLflow callbacks work without manually exporting env vars.
load_dotenv(override=False)

# 剥去 assistant content 时记录的 category 与 topic。
# 以 device_key 维度隔离，避免不同会话互相污染。
# 当 response 有 content 但非严格 topic/category/content 格式时，记录此 label，request 时遇到则不包。
_NO_WRAP_SENTINEL = "__arbiteros_no_wrap__"
_stripped_categories_by_device: dict[str, list[str]] = {}
_stripped_topics_by_device: dict[str, list[Optional[str]]] = {}
_stripped_categories_lock = threading.Lock()
_MAX_STRIPPED_CATEGORIES = 1000

# instruction_parsing: per-trace InstructionBuilder cache
_instruction_builders_by_trace: dict[str, Any] = {}
_instruction_builders_lock = threading.Lock()
_MAX_INSTRUCTION_BUILDERS = 256

# 旧 litellm category -> instruction_parser instruction_type 映射（向后兼容）
_CATEGORY_TO_INSTRUCTION_TYPE: dict[str, str] = {
    "COGNITIVE_CORE__GENERATE": "REASON",
    "COGNITIVE_CORE__DECOMPOSE": "PLAN",
    "COGNITIVE_CORE__REFLECT": "CRITIQUE",
    "EXECUTION_CORE__TOOL_CALL": "EXEC",
    "EXECUTION_CORE__TOOL_BUILD": "EXEC",
    "EXECUTION_CORE__DELEGATE": "HANDOFF",
    "EXECUTION_CORE__RESPOND": "RESPOND",
}

# 极短且不完整的内容通常意味着结构化输出异常（例如只返回 "{"）。
_MALFORMED_PLACEHOLDER_CONTENTS = {"{", "}", "[", "]", "{}", "[]"}


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
    root_observation_id: Optional[str] = None
    current_turn_observation_id: Optional[str] = None
    turn_index: int = 0
    latest_user_preview: Optional[str] = None
    latest_topic_summary: Optional[str] = None
    # Per-trace monotonically increasing tool result indices per tool name.
    tool_result_counter_by_tool: dict[str, int] = field(default_factory=dict)
    # tool_call_id -> parser/tool node reservation (ephemeral, in-memory only)
    pending_tool_call_nodes_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Ephemeral handle to the current turn observation, for in-process updates.
    current_turn_handle: Any = None


_trace_state_lock = threading.Lock()
_trace_state_by_device: dict[str, _TraceState] = {}
_latest_user_id_by_channel: dict[str, str] = {}
_trace_state_file_mtime_ns: Optional[int] = None
_recent_response_keys: list[str] = []
_recent_response_key_set: set[str] = set()
_MAX_RECENT_RESPONSE_KEYS = 512
_recent_tool_result_keys: list[str] = []
_recent_tool_result_key_set: set[str] = set()
_MAX_RECENT_TOOL_RESULT_KEYS = 1024
# trace_id -> {tool_call_id: error_type}：policy 保护后待 tool result 时加 policy_protected
_policy_protected_tool_call_ids: dict[str, dict[str, str]] = {}
_TOOL_RESULT_NAME_INDEX_RE = re.compile(
    r"^(?P<tool_name>.+)\.(?P<index>\d+)$"
)
_TOOL_RESULT_LEGACY_NAME_INDEX_RE = re.compile(
    r"^tool\.(?P<tool_name>.+)\.result\.call_(?P<index>\d+)$"
)

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
    re.IGNORECASE | re.MULTILINE,
)
_RESET_CONTROL_TOPIC_RE = re.compile(
    r"^(?:"
    r"/new|/reset|"
    r"new session|start (?:a )?new session|begin (?:a )?new session|"
    r"reset (?:the )?(?:session|conversation)|restart (?:the )?(?:session|conversation)|"
    r"session reset|conversation reset|"
    r"重[置制](?:会话|对话)|新会话|开启新会话|开始新会话|重新开始(?:会话|对话)|重启(?:会话|对话)|重开(?:会话|对话)"
    r")$",
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

_TRACE_SESSION_LABEL_PREFIX = "trace"
_NODE_NAMESPACE_PREFIX = "session"


def _trace_state_to_dict(state: _TraceState) -> dict[str, Any]:
    return {
        "trace_id": state.trace_id,
        "device_key": state.device_key,
        "channel": state.channel,
        "user_id": state.user_id,
        "sequence": state.sequence,
        "last_user_fingerprint": state.last_user_fingerprint,
        "last_reset_fingerprint": state.last_reset_fingerprint,
        "root_observation_id": state.root_observation_id,
        "current_turn_observation_id": state.current_turn_observation_id,
        "turn_index": state.turn_index,
        "latest_user_preview": state.latest_user_preview,
        "latest_topic_summary": state.latest_topic_summary,
        "tool_result_counter_by_tool": state.tool_result_counter_by_tool,
    }


def _trace_state_from_dict(device_key: str, payload: Any) -> Optional[_TraceState]:
    if not isinstance(payload, dict):
        return None

    trace_id = payload.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id:
        return None

    stored_device_key = payload.get("device_key")
    if isinstance(stored_device_key, str) and stored_device_key.strip():
        device_key = stored_device_key.strip()
    else:
        device_key = device_key.strip()
    if not device_key:
        return None

    derived_channel, _, derived_user_id = device_key.partition(":")
    channel = payload.get("channel")
    if not isinstance(channel, str) or not channel.strip():
        channel = derived_channel or "unknown-channel"
    channel = _normalize_device_fragment(channel)

    user_id = payload.get("user_id")
    if not isinstance(user_id, str) or not user_id.strip():
        user_id = derived_user_id or "unknown-user"
    user_id = _normalize_device_fragment(user_id)

    sequence = payload.get("sequence")
    if not isinstance(sequence, int) or sequence < 0:
        sequence = 0

    last_user_fingerprint = payload.get("last_user_fingerprint")
    if not isinstance(last_user_fingerprint, str) or not last_user_fingerprint:
        last_user_fingerprint = None

    last_reset_fingerprint = payload.get("last_reset_fingerprint")
    if not isinstance(last_reset_fingerprint, str) or not last_reset_fingerprint:
        last_reset_fingerprint = None

    root_observation_id = payload.get("root_observation_id")
    if not isinstance(root_observation_id, str) or not root_observation_id:
        root_observation_id = None

    current_turn_observation_id = payload.get("current_turn_observation_id")
    if not isinstance(current_turn_observation_id, str) or not current_turn_observation_id:
        current_turn_observation_id = None

    turn_index = payload.get("turn_index")
    if not isinstance(turn_index, int) or turn_index < 0:
        turn_index = 0

    latest_user_preview = payload.get("latest_user_preview")
    if not isinstance(latest_user_preview, str) or not latest_user_preview:
        latest_user_preview = None

    latest_topic_summary = payload.get("latest_topic_summary")
    if not isinstance(latest_topic_summary, str) or not latest_topic_summary:
        latest_topic_summary = None

    tool_result_counter_by_tool = payload.get("tool_result_counter_by_tool")
    if not isinstance(tool_result_counter_by_tool, dict):
        tool_result_counter_by_tool = {}
    cleaned_counters: dict[str, int] = {}
    for k, v in tool_result_counter_by_tool.items():
        if not isinstance(k, str) or not k.strip():
            continue
        if isinstance(v, int) and v >= 0:
            cleaned_counters[k.strip()] = v

    return _TraceState(
        trace_id=trace_id,
        device_key=device_key,
        channel=channel,
        user_id=user_id,
        sequence=sequence,
        last_user_fingerprint=last_user_fingerprint,
        last_reset_fingerprint=last_reset_fingerprint,
        root_observation_id=root_observation_id,
        current_turn_observation_id=current_turn_observation_id,
        turn_index=turn_index,
        latest_user_preview=latest_user_preview,
        latest_topic_summary=latest_topic_summary,
        tool_result_counter_by_tool=cleaned_counters,
    )


def _next_tool_result_index(state: _TraceState, tool_name: str) -> int:
    normalized_tool_name = tool_name.strip() if isinstance(tool_name, str) else ""
    if not normalized_tool_name:
        normalized_tool_name = "unknown_tool"
    recovered_floor = _max_emitted_tool_result_index(
        trace_id=state.trace_id,
        tool_name=normalized_tool_name,
    )
    with _trace_state_lock:
        current_floor = state.tool_result_counter_by_tool.get(normalized_tool_name, 0)
        if recovered_floor > current_floor:
            current_floor = recovered_floor
        current = current_floor + 1
        state.tool_result_counter_by_tool[normalized_tool_name] = current
        return current


def _reserve_tool_result_index_for_call(
    state: _TraceState, *, tool_call_id: Optional[str], tool_name: str
) -> int:
    normalized_tool_name = tool_name.strip() if isinstance(tool_name, str) else ""
    if not normalized_tool_name:
        normalized_tool_name = "unknown_tool"
    if isinstance(tool_call_id, str) and tool_call_id.strip():
        with _trace_state_lock:
            existing = state.pending_tool_call_nodes_by_id.get(tool_call_id)
            if isinstance(existing, dict):
                idx = existing.get("index")
                if isinstance(idx, int) and idx > 0:
                    return idx
    return _next_tool_result_index(state, normalized_tool_name)


def _set_pending_tool_call_node(
    state: _TraceState, *, tool_call_id: Optional[str], payload: dict[str, Any]
) -> None:
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        return
    with _trace_state_lock:
        state.pending_tool_call_nodes_by_id[tool_call_id] = payload


def _pop_pending_tool_call_node(
    state: _TraceState, *, tool_call_id: Optional[str]
) -> Optional[dict[str, Any]]:
    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        return None
    with _trace_state_lock:
        payload = state.pending_tool_call_nodes_by_id.pop(tool_call_id, None)
    return payload if isinstance(payload, dict) else None


def _max_emitted_tool_result_index(
    *, trace_id: Optional[str], tool_name: Optional[str], scan_last_lines: int = 5000
) -> int:
    if not isinstance(trace_id, str) or not trace_id:
        return 0
    if not isinstance(tool_name, str):
        return 0
    tool_name = tool_name.strip()
    if not tool_name:
        return 0

    prefixes = (f"{tool_name}.", f"tool.{tool_name}.result.call_")
    try:
        raw = _LANGFUSE_NODE_LOG_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    except Exception:
        return 0

    lines = raw.splitlines()
    if scan_last_lines > 0 and len(lines) > scan_last_lines:
        lines = lines[-scan_last_lines:]

    max_index = 0
    for line in lines:
        if trace_id not in line or not any(prefix in line for prefix in prefixes):
            continue
        parsed = _safe_json_loads(line)
        if not isinstance(parsed, dict):
            continue
        data = parsed.get("data")
        if not isinstance(data, dict):
            continue
        if data.get("trace_id") != trace_id:
            continue
        if data.get("node_type") != "tool_result":
            continue
        name = data.get("name")
        if not isinstance(name, str):
            continue
        match = _TOOL_RESULT_NAME_INDEX_RE.match(name)
        if not match:
            match = _TOOL_RESULT_LEGACY_NAME_INDEX_RE.match(name)
        if not match:
            continue
        if match.group("tool_name") != tool_name:
            continue
        try:
            index = int(match.group("index"))
        except ValueError:
            continue
        if index > max_index:
            max_index = index

    return max_index


def _load_trace_state_snapshot_from_disk() -> tuple[dict[str, _TraceState], dict[str, str], Optional[int]]:
    try:
        stat = _TRACE_STATE_FILE.stat()
    except FileNotFoundError:
        return {}, {}, None
    except Exception as exc:
        _save_json("trace_state_read_error", {"error": str(exc)})
        return {}, {}, None

    try:
        raw = json.loads(_TRACE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        _save_json("trace_state_parse_error", {"error": str(exc)})
        return {}, {}, stat.st_mtime_ns

    states_out: dict[str, _TraceState] = {}
    latest_out: dict[str, str] = {}

    states_payload = raw.get("states") if isinstance(raw, dict) else {}
    if isinstance(states_payload, dict):
        for key, value in states_payload.items():
            if not isinstance(key, str) or not key.strip():
                continue
            parsed = _trace_state_from_dict(key, value)
            if parsed is not None:
                states_out[parsed.device_key] = parsed

    latest_payload = (
        raw.get("latest_user_id_by_channel") if isinstance(raw, dict) else {}
    )
    if isinstance(latest_payload, dict):
        for channel, user_id in latest_payload.items():
            if not isinstance(channel, str) or not isinstance(user_id, str):
                continue
            channel_norm = _normalize_device_fragment(channel)
            user_norm = _normalize_device_fragment(user_id)
            if channel_norm and user_norm:
                latest_out[channel_norm] = user_norm

    return states_out, latest_out, stat.st_mtime_ns


def _sync_trace_state_from_disk(force: bool = False) -> None:
    global _trace_state_file_mtime_ns

    states_snapshot, latest_snapshot, mtime_ns = _load_trace_state_snapshot_from_disk()
    if mtime_ns is None:
        return

    with _trace_state_lock:
        if not force and _trace_state_file_mtime_ns == mtime_ns:
            return

        for device_key, restored in states_snapshot.items():
            current = _trace_state_by_device.get(device_key)
            if current is None or restored.sequence >= current.sequence:
                _trace_state_by_device[device_key] = restored

        for channel, user_id in latest_snapshot.items():
            if channel and user_id:
                _latest_user_id_by_channel[channel] = user_id

        _trace_state_file_mtime_ns = mtime_ns


def _persist_trace_state_to_disk() -> None:
    global _trace_state_file_mtime_ns

    with _trace_state_lock:
        states_payload = {
            device_key: _trace_state_to_dict(state)
            for device_key, state in _trace_state_by_device.items()
        }
        latest_payload = dict(_latest_user_id_by_channel)

    payload = {
        "version": 1,
        "updated_at": datetime.now().isoformat(),
        "states": states_payload,
        "latest_user_id_by_channel": latest_payload,
    }
    tmp_path = _TRACE_STATE_FILE.with_suffix(".tmp")

    try:
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        tmp_path.replace(_TRACE_STATE_FILE)
        mtime_ns = _TRACE_STATE_FILE.stat().st_mtime_ns
    except Exception as exc:
        _save_json("trace_state_persist_error", {"error": str(exc)})
        return

    with _trace_state_lock:
        _trace_state_file_mtime_ns = mtime_ns


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


def _save_precall_to_log(data: dict) -> None:
    """将 pre_call 最终发给 LLM 的 payload 追加到 log/precall.jsonl"""
    try:
        entry = {
            "ts": datetime.now().isoformat(),
            "payload": _to_json(data),
        }
        with open(_PRECALL_LOG_FILE, "a", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, default=str)
            f.write("\n")
            f.flush()
    except Exception:
        pass  # Best-effort; don't fail the main flow


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


def _extract_text_from_responses_output(response_obj: Any) -> str:
    """Best-effort text extraction from OpenAI Responses API payload (dict)."""
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

        # Some SDK dumps may include a direct "text" field.
        item_text = item.get("text")
        if isinstance(item_text, str) and item_text.strip():
            texts.append(item_text.strip())

        # Standard Responses API: items often contain "content" parts with "text".
        content = item.get("content")
        content_text = _extract_text_from_message_content(content)
        if content_text.strip():
            texts.append(content_text.strip())

    return "\n".join(texts).strip()


def _is_responses_api_request(request_data: Any) -> bool:
    """Detect OpenAI Responses API style requests in LiteLLM hooks."""
    if not isinstance(request_data, dict):
        return False
    # Responses API requests are centered around `input`, while chat-completions
    # use `messages`. Accept str/list/dict input to support old/new callers.
    has_input = "input" in request_data and isinstance(
        request_data.get("input"), (str, list, dict)
    )
    has_chat_messages = isinstance(request_data.get("messages"), list)
    return has_input and not has_chat_messages


def _extract_text_from_responses_input(input_payload: Any) -> str:
    """Best-effort extraction of latest user text from Responses API `input`."""
    if isinstance(input_payload, str):
        return input_payload.strip()
    if isinstance(input_payload, dict):
        role = input_payload.get("role")
        if isinstance(role, str) and role != "user":
            return ""
        return _extract_text_from_message_content(input_payload.get("content")).strip()
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
            text = _extract_text_from_message_content(item.get("content")).strip()
            if text:
                user_texts.append(text)
        if user_texts:
            return user_texts[-1]
    return ""


def _extract_stream_text_from_responses_chunk(chunk: Any, chunk_dump: Optional[dict]) -> str:
    """Extract delta text from a Responses API stream chunk."""
    text_parts: list[str] = []
    # LiteLLM helper can parse ModelResponseStream/ModelResponse chunks.
    try:
        if isinstance(chunk, (ModelResponseStream, ModelResponse)):
            parsed = litellm.get_response_string(response_obj=chunk)
            if isinstance(parsed, str) and parsed:
                text_parts.append(parsed)
    except Exception:
        pass
    # Native Responses events may expose `delta`.
    if isinstance(chunk_dump, dict):
        delta = chunk_dump.get("delta")
        if isinstance(delta, str) and delta:
            text_parts.append(delta)
    return "".join(text_parts)


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


def _is_reset_marker_text(text: str) -> bool:
    if not text:
        return False
    cleaned = text.strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered in {"/new", "/reset"}:
        return True
    # Some clients inject a synthetic "reset" user message.
    # Keep this strict to avoid accidental matches inside long system prompts.
    if len(cleaned) <= 200 and _RESET_PROMPT_RE.match(cleaned):
        return True
    return False


def _truncate_messages_after_last_reset(messages: list[Any]) -> list[Any]:
    """If a reset marker exists in the provided history, drop everything before it.

    This makes `/reset` act like a real session reset even if the caller keeps sending
    the full prior conversation history back to the proxy.
    """
    if not isinstance(messages, list) or not messages:
        return messages

    last_reset_idx: Optional[int] = None
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in {"user", "system"}:
            continue
        text = _extract_text_from_message_content(msg.get("content"))
        if _is_reset_marker_text(text):
            last_reset_idx = i

    if last_reset_idx is None:
        return messages

    # If the reset marker is the last message, keep it so pre_call fast-path triggers.
    if last_reset_idx == len(messages) - 1:
        return messages

    # Preserve the base system prompt (first system message) if it exists.
    base_system: Optional[dict] = None
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            base_system = msg
            break

    tail = messages[last_reset_idx + 1 :]
    if base_system is not None:
        # Avoid duplicating base_system if it already appears in the tail.
        if not (tail and tail[0] is base_system):
            return [base_system, *tail]
    return tail


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
    # OpenClaw sometimes forwards a synthetic "new session" instruction as the
    # user turn (may be prefixed by other status text). Treat that as a reset
    # marker for trace rotation.
    cleaned = latest_user_text.strip()
    if not cleaned:
        return False
    if len(cleaned) > 4000:
        return False
    return bool(_RESET_PROMPT_RE.search(cleaned))


def _is_reset_control_topic(text: Optional[str]) -> bool:
    """Whether text is a reset/new-session control phrase (not a semantic user topic)."""
    if not isinstance(text, str):
        return False
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return False
    cleaned = cleaned.strip(" -_/,:;，。；|：")
    if not cleaned:
        return False
    if _is_reset_marker_text(cleaned) or _is_reset_request_text(cleaned):
        return True
    return bool(_RESET_CONTROL_TOPIC_RE.match(cleaned))


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


def _extract_reset_requested_from_metadata(incoming: dict) -> bool:
    """Reset/renew should be controlled by the caller (e.g. nanobot/openclaw).

    Note: We still honor explicit `/reset` and `/new` commands in user text for
    backwards compatibility with clients that don't pass metadata.
    """
    metadata = incoming.get("metadata")
    if not isinstance(metadata, dict):
        return False
    for key in ("arbiteros_reset_requested", "reset_requested"):
        v = metadata.get(key)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            if v.strip().lower() in {"1", "true", "yes", "y"}:
                return True
    return False


def _parse_device_key(device_key: str) -> tuple[str, str]:
    raw_channel, _, raw_user_id = device_key.partition(":")
    channel = _normalize_device_fragment(raw_channel) if raw_channel else "unknown-channel"
    user_id = _normalize_device_fragment(raw_user_id) if raw_user_id else "unknown-user"
    return channel, user_id


def _get_latest_user_id_for_channel(channel: str) -> Optional[str]:
    if not channel or channel == "unknown-channel":
        return None
    _sync_trace_state_from_disk()
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


def _get_latest_user_id_for_channel_including_anonymous(channel: str) -> Optional[str]:
    """Best-effort identity recovery when gateways omit conversation labels.

    To avoid accidentally mixing multiple users, only return a value when there is
    exactly one known user_id for the channel in trace state.
    """
    if not channel or channel == "unknown-channel":
        return None
    _sync_trace_state_from_disk()
    with _trace_state_lock:
        states = [s for s in _trace_state_by_device.values() if s.channel == channel]
        user_ids = {s.user_id for s in states if isinstance(s.user_id, str) and s.user_id}
        non_anonymous = {u for u in user_ids if not u.startswith("anonymous-")}
        # If we have real user ids, only proceed when there's exactly one.
        if non_anonymous and len(non_anonymous) != 1:
            return None
        # If we only have anonymous ids, treat them as one identity and return the latest.
        if not non_anonymous and not user_ids:
            return None
        # Choose the latest state for stability across restarts.
        latest_state: Optional[_TraceState] = None
        for s in states:
            if latest_state is None or s.sequence >= latest_state.sequence:
                latest_state = s
        if latest_state is None:
            return None
        return latest_state.user_id


def _build_device_context(incoming: dict) -> _DeviceContext:
    messages = incoming.get("messages")
    if not isinstance(messages, list):
        messages = []

    latest_user_text = _extract_latest_message_text(messages, role="user")
    if not latest_user_text and _is_responses_api_request(incoming):
        latest_user_text = _extract_text_from_responses_input(incoming.get("input")) or None
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

    channel = channel_value or session_channel or metadata_channel_hint or "unknown-channel"
    has_explicit_user_id = bool(
        (conversation_value and conversation_value.strip())
        or (session_chat_id and session_chat_id.strip())
    )
    raw_user_id = (
        conversation_value
        if (conversation_value and conversation_value.strip())
        else (
            session_chat_id
            if (session_chat_id and session_chat_id.strip())
            else (
                metadata_user_id_hint
                if (metadata_user_id_hint and metadata_user_id_hint.strip())
                else "unknown-user"
            )
        )
    )
    channel = _normalize_device_fragment(channel)
    if metadata_channel_hint and metadata_channel_hint != "unknown-channel":
        channel = metadata_channel_hint

    normalized_user_cmd = (latest_user_text or "").strip().lower()
    reset_requested = _extract_reset_requested_from_metadata(incoming) or normalized_user_cmd in {
        "/new",
        "/reset",
    } or _is_reset_request_text(latest_user_text)

    if raw_user_id == "unknown-user":
        # If this turn doesn't include a conversation_label / chat id (common for some
        # gateways), fall back to the last known non-anonymous user id on this channel.
        hinted_user_id = _get_latest_user_id_for_channel(channel)
        if hinted_user_id:
            raw_user_id = hinted_user_id
        else:
            # If the channel has only one known identity (even if anonymous),
            # keep it stable so turns don't split across traces.
            hinted_any = _get_latest_user_id_for_channel_including_anonymous(channel)
            if hinted_any:
                raw_user_id = hinted_any

    if raw_user_id == "unknown-user" and reset_requested:
        # Reset/new-session turns often omit conversation_label; recover prior identity.
        if metadata_user_id_hint and not metadata_user_id_hint.startswith("anonymous-"):
            raw_user_id = metadata_user_id_hint
        else:
            hinted_user_id = _get_latest_user_id_for_channel(channel)
            if hinted_user_id:
                raw_user_id = hinted_user_id

    if raw_user_id == "unknown-user":
        fallback_source = first_system_text or latest_system_text or "unknown-user-seed"
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
    _sync_trace_state_from_disk()
    persist_needed = False
    created_new_trace = False
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
                root_observation_id=None,
                current_turn_observation_id=None,
                turn_index=0,
                latest_user_preview=None,
            )
            _trace_state_by_device[context.device_key] = current
            if current.channel != "unknown-channel" and not current.user_id.startswith(
                "anonymous-"
            ):
                if _latest_user_id_by_channel.get(current.channel) != current.user_id:
                    _latest_user_id_by_channel[current.channel] = current.user_id
            persist_needed = True
            created_new_trace = True
        else:
            if context.reset_requested and context.latest_user_fingerprint:
                current.last_reset_fingerprint = context.latest_user_fingerprint
            if current.channel != "unknown-channel" and not current.user_id.startswith(
                "anonymous-"
            ):
                if _latest_user_id_by_channel.get(current.channel) != current.user_id:
                    _latest_user_id_by_channel[current.channel] = current.user_id
                    persist_needed = True

    if persist_needed:
        _persist_trace_state_to_disk()
    return current, created_new_trace


def _resolve_trace_state_from_metadata(
    incoming: dict, *, context: _DeviceContext
) -> Optional[_TraceState]:
    _sync_trace_state_from_disk()
    metadata = incoming.get("metadata")
    if not isinstance(metadata, dict):
        return None

    trace_id = metadata.get("arbiteros_trace_id")
    device_key = metadata.get("arbiteros_device_key")
    if not isinstance(trace_id, str) or not trace_id:
        return None
    if not isinstance(device_key, str) or not device_key:
        return None

    persist_needed = False
    with _trace_state_lock:
        current = _trace_state_by_device.get(device_key)
        if current is not None:
            if current.trace_id == trace_id:
                if context.latest_user_fingerprint:
                    current.last_user_fingerprint = context.latest_user_fingerprint
                    if context.reset_requested:
                        current.last_reset_fingerprint = context.latest_user_fingerprint
                result = current
            else:
                # Keep the in-memory state when incoming metadata carries a stale trace id.
                # This prevents /new or /reset from being rolled back by delayed retries
                # that still include the previous arbiteros_trace_id.
                if context.latest_user_fingerprint:
                    current.last_user_fingerprint = context.latest_user_fingerprint
                    if context.reset_requested:
                        current.last_reset_fingerprint = context.latest_user_fingerprint
                result = current
        else:
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
                root_observation_id=None,
                current_turn_observation_id=None,
                turn_index=0,
                latest_user_preview=None,
            )
            _trace_state_by_device[device_key] = restored_state
            result = restored_state
            persist_needed = True

        if result.channel != "unknown-channel" and not result.user_id.startswith(
            "anonymous-"
        ):
            if _latest_user_id_by_channel.get(result.channel) != result.user_id:
                _latest_user_id_by_channel[result.channel] = result.user_id
                persist_needed = True

    if persist_needed:
        _persist_trace_state_to_disk()
    return result


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

    ensure_langfuse_env_compat()

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


def _short_text_preview(text: Optional[str], max_chars: int = 72) -> Optional[str]:
    if not isinstance(text, str):
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return None
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[:max_chars].rstrip()}..."


def _sanitize_topic_preview(
    text: Optional[str],
    *,
    max_chars: int = 72,
    allow_reset_control_topic: bool = False,
) -> Optional[str]:
    preview = _short_text_preview(text, max_chars=max_chars)
    if not preview:
        return None
    if not allow_reset_control_topic and _is_reset_control_topic(preview):
        return None
    return preview


def _build_trace_display_name(state: _TraceState) -> str:
    base_name = f"{_TRACE_SESSION_LABEL_PREFIX}:{state.device_key}"
    allow_reset_control_topic = (
        state.turn_index <= 1
        and _is_reset_control_topic(state.latest_user_preview)
        and (
            state.latest_topic_summary is None
            or _is_reset_control_topic(state.latest_topic_summary)
        )
    )
    topic_preview = _sanitize_topic_preview(
        state.latest_topic_summary,
        allow_reset_control_topic=allow_reset_control_topic,
    )
    if topic_preview:
        return f"{topic_preview} - {base_name}"
    preview = _sanitize_topic_preview(
        state.latest_user_preview,
        allow_reset_control_topic=allow_reset_control_topic,
    )
    if preview:
        return f"{preview} - {base_name}"
    return base_name


def _build_kernel_step_name(
    *, category: str, context: _DeviceContext, state: _TraceState
) -> str:
    kernel_name = f"kernel.{category.lower()}"
    topic_preview = _short_text_preview(context.latest_user_text) or _short_text_preview(
        state.latest_user_preview
    )
    if topic_preview:
        return f"{topic_preview} - {kernel_name}"
    return kernel_name


def _extract_quoted_topic(text: Optional[str]) -> Optional[str]:
    """Extract a likely topic from Chinese/English quotation marks.

    Examples:
    - 「美国今天有什么新闻」 -> 美国今天有什么新闻
    - “美国今天有什么新闻” -> 美国今天有什么新闻
    """
    if not isinstance(text, str):
        return None
    candidates: list[str] = []
    for left, right in [("「", "」"), ("“", "”"), ('"', '"')]:
        try:
            start = text.index(left)
            end = text.index(right, start + 1)
        except ValueError:
            continue
        inner = text[start + 1 : end].strip()
        if 4 <= len(inner) <= 120:
            candidates.append(inner)
    if not candidates:
        return None
    # Prefer the first candidate (usually the main topic).
    return candidates[0]


def _extract_quoted_topics(text: Optional[str]) -> list[str]:
    """Extract all likely topics from Chinese/English quotation marks."""
    if not isinstance(text, str):
        return []
    out: list[str] = []
    for left, right in [("「", "」"), ("“", "”"), ('"', '"')]:
        start = 0
        while True:
            try:
                i = text.index(left, start)
                j = text.index(right, i + 1)
            except ValueError:
                break
            inner = text[i + 1 : j].strip()
            if 4 <= len(inner) <= 120:
                out.append(inner)
            start = j + 1
    return out


_TOPIC_NOISE_PATTERNS: list[re.Pattern[str]] = [
    # Process/flow words that are common in agent traces but not user topics.
    re.compile(r"\bnew session\b", re.IGNORECASE),
    re.compile(r"\bgreet\b", re.IGNORECASE),
    re.compile(r"\bnext\b", re.IGNORECASE),
    re.compile(r"\bplease wait\b", re.IGNORECASE),
    # Internal labels/implementation details.
    re.compile(r"\bexecution_core\b", re.IGNORECASE),
    re.compile(r"\bkernel\.[a-z0-9_]+\b", re.IGNORECASE),
]


def _clean_topic_point(text: str) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return ""
    for p in _TOPIC_NOISE_PATTERNS:
        t = p.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Trim common separators left behind by removals.
    t = t.strip(" -_/,:;，。；|")
    return t


def _is_noisy_topic_point(text: str) -> bool:
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return True
    lowered = t.lower()
    if lowered in {
        "new session",
        "greet user",
        "next",
        "continue",
        "unknown",
        "none",
        "(none)",
    }:
        return True
    return any(p.search(t) for p in _TOPIC_NOISE_PATTERNS)


def _normalize_topic_summary(
    topic_text: Optional[str],
    *,
    max_points: int = 3,
    max_point_chars: int = 24,
    max_total_chars: int = 72,
) -> Optional[str]:
    """Normalize one-or-multi-point topic text into a concise summary."""
    if not isinstance(topic_text, str):
        return None
    cleaned = re.sub(r"\s+", " ", topic_text).strip()
    if not cleaned:
        return None

    parts: list[str] = []
    for segment in re.split(r"[\n;；|]+", cleaned):
        if not segment:
            continue
        for sub in re.split(r"\s*/\s*", segment):
            normalized = re.sub(r"^(?:[-*•]\s*|\d+[.)、]\s*)", "", sub).strip()
            if normalized:
                parts.append(normalized)

    if not parts:
        parts = [cleaned]

    out: list[str] = []
    dedupe: set[str] = set()
    for part in parts:
        part_clean = _clean_topic_point(re.sub(r"\s+", " ", part).strip())
        if len(part_clean) < 2:
            continue
        if part_clean.startswith(("我", "我们", "I ", "We ")):
            continue
        if _is_noisy_topic_point(part_clean):
            continue
        lowered = part_clean.lower()
        if lowered in dedupe:
            continue
        dedupe.add(lowered)
        out.append(_short_text_preview(part_clean, max_chars=max_point_chars) or part_clean)
        if len(out) >= max_points:
            break

    if not out:
        return None
    return _short_text_preview(" / ".join(out), max_chars=max_total_chars)


def _tokenize_for_topic_overlap(text: Optional[str]) -> set[str]:
    """Tokenize for fuzzy topic overlap across Chinese/English."""
    if not isinstance(text, str) or not text.strip():
        return set()
    s = text.strip().lower()
    tokens: set[str] = set()

    # Collect CJK bigrams (reduces spurious overlap like just "今天").
    cjk_chars = [ch for ch in s if "\u4e00" <= ch <= "\u9fff"]
    for i in range(len(cjk_chars) - 1):
        tokens.add(cjk_chars[i] + cjk_chars[i + 1])

    # Collect ASCII word tokens.
    word: list[str] = []
    for ch in s:
        if ch.isalnum() or ch in {"_", "-"}:
            word.append(ch)
        else:
            if word:
                tokens.add("".join(word))
                word = []
    if word:
        tokens.add("".join(word))

    return {t for t in tokens if len(t) >= 2}


def _topic_overlap_score(candidate: str, user_text: Optional[str]) -> float:
    cand_tokens = _tokenize_for_topic_overlap(candidate)
    user_tokens = _tokenize_for_topic_overlap(user_text)
    if not cand_tokens or not user_tokens:
        return 0.0
    inter = cand_tokens.intersection(user_tokens)
    # Require the candidate to cover a meaningful portion of the user's topic tokens.
    return len(inter) / max(1, min(len(cand_tokens), len(user_tokens)))


def _should_accept_llm_topic_summary(
    candidate: Optional[str],
    *,
    previous_topic: Optional[str],
    latest_user_turn: Optional[str],
    allow_reset_control_topic: bool = False,
) -> bool:
    """Heuristic guardrail: reject obviously noisy/unrelated LLM topic summaries."""
    if not isinstance(candidate, str) or not candidate.strip():
        return False
    cand = candidate.strip()
    if _is_reset_control_topic(cand):
        return allow_reset_control_topic
    # Reject if the whole thing still looks like a process step label.
    if _is_noisy_topic_point(cand):
        return False

    user_preview = _short_text_preview(latest_user_turn, max_chars=200)
    if user_preview:
        normalized = user_preview.strip().lower()
        if normalized in {"你好", "hello", "hi", "/reset", "/new"}:
            return True
        if _topic_overlap_score(cand, user_preview) >= 0.20:
            return True

    prev_preview = _short_text_preview(previous_topic, max_chars=120)
    if prev_preview and _topic_overlap_score(cand, prev_preview) >= 0.20:
        return True

    # Otherwise, candidate is likely hallucinated/unrelated; prefer fallbacks.
    return False


def _enforce_topic_point_count_on_shift(
    candidate: Optional[str],
    *,
    previous_topic: Optional[str],
    latest_user_turn: Optional[str],
    max_total_chars: int,
) -> Optional[str]:
    """Ensure multi-point topics appear only when the user topic shifts."""
    if not isinstance(candidate, str):
        return None
    cand = candidate.strip()
    if not cand or " / " not in cand:
        return candidate

    points = [p.strip() for p in cand.split(" / ") if p.strip()]
    if len(points) <= 1:
        return points[0] if points else None

    prev_preview = _short_text_preview(previous_topic, max_chars=120)
    user_preview = _short_text_preview(latest_user_turn, max_chars=200)

    # If user is still on the same topic, collapse to a single best-matching point.
    if prev_preview and user_preview and _topic_overlap_score(prev_preview, user_preview) >= 0.20:
        best = max(points, key=lambda p: _topic_overlap_score(p, prev_preview))
        return _short_text_preview(best, max_chars=max_total_chars) or best

    # If shifted, keep at most: [previous-related] / [new-related].
    if prev_preview and user_preview:
        prev_best = max(points, key=lambda p: _topic_overlap_score(p, prev_preview))
        user_best = max(points, key=lambda p: _topic_overlap_score(p, user_preview))
        kept: list[str] = []
        for p in [prev_best, user_best]:
            if p and p not in kept:
                kept.append(p)
        if kept:
            joined = " / ".join(kept[:2])
            return _short_text_preview(joined, max_chars=max_total_chars) or joined

    # No reliable context: keep the first point only.
    first = points[0]
    return _short_text_preview(first, max_chars=max_total_chars) or first


def _summarize_turn_topic(
    *, user_text: Optional[str], output_text: Optional[str], max_chars: int = 72
) -> Optional[str]:
    """Summarize the turn topic from input + output (lightweight heuristic)."""
    user_preview = _short_text_preview(user_text, max_chars=max_chars)
    output_preview = _short_text_preview(output_text, max_chars=max_chars)

    # Prefer output-derived topics only when they strongly overlap with the user's request.
    if user_preview and user_preview.strip().lower() not in {"/reset", "/new"}:
        best_candidate: Optional[str] = None
        best_score = 0.0
        for quoted in _extract_quoted_topics(output_text):
            score = _topic_overlap_score(quoted, user_preview)
            if score > best_score:
                best_score = score
                best_candidate = quoted

        # Also consider the first non-empty line as a candidate "topic sentence".
        if isinstance(output_text, str):
            for line in output_text.splitlines():
                cleaned = re.sub(r"\s+", " ", line).strip()
                if cleaned:
                    score = _topic_overlap_score(cleaned, user_preview)
                    if score > best_score:
                        best_score = score
                        best_candidate = cleaned
                    break

        # Only accept output-derived topics when overlap is high enough; otherwise fallback to input.
        if best_candidate is not None and best_score >= 0.34:
            return _short_text_preview(best_candidate, max_chars=max_chars)

    # Commands like /reset should not become the "topic".
    if user_preview and user_preview.strip().lower() in {"/reset", "/new"}:
        return output_preview or user_preview

    # For normal turns, prefer user text, but fall back to output when user is empty/generic.
    if user_preview and len(user_preview) >= 2:
        if user_preview in {"你好", "hello", "hi"} and output_preview:
            return output_preview
        return user_preview
    return output_preview or user_preview


def _current_parent_observation_id(state: _TraceState) -> Optional[str]:
    return state.current_turn_observation_id or state.root_observation_id


def _emit_langfuse_node(
    *,
    state: _TraceState,
    node_type: str,
    observation_type: str,
    name: str,
    input_payload: Any = None,
    output_payload: Any = None,
    include_output: bool = True,
    capture_handle: Optional[dict[str, Any]] = None,
    end_observation: bool = True,
    metadata: Optional[dict] = None,
    model: Optional[str] = None,
    level: Optional[str] = None,
    status_message: Optional[str] = None,
    parent_observation_id: Optional[str] = None,
    trace_name: Optional[str] = None,
) -> Optional[str]:
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
        "metadata": node_metadata,
    }
    if include_output:
        node_log["output"] = output_payload
    if isinstance(level, str) and level.strip():
        node_log["level"] = level.strip().upper()
    if isinstance(status_message, str) and status_message.strip():
        node_log["status_message"] = status_message.strip()
    _save_langfuse_node_json(node_log)

    lf = _get_langfuse_client()
    if lf is None:
        return None

    try:
        start_kwargs: dict[str, Any] = {
            "trace_context": {"trace_id": state.trace_id},
            "name": name,
            "as_type": "generation" if observation_type == "generation" else observation_type,
            "input": input_payload,
            "metadata": node_metadata,
        }
        if include_output:
            start_kwargs["output"] = output_payload
        if isinstance(parent_observation_id, str) and parent_observation_id.strip():
            start_kwargs["parent_observation_id"] = parent_observation_id.strip()
        if observation_type == "generation":
            start_kwargs["model"] = model
        if isinstance(level, str) and level.strip():
            start_kwargs["level"] = level.strip().upper()
        if isinstance(status_message, str) and status_message.strip():
            start_kwargs["status_message"] = status_message.strip()

        try:
            obs = lf.start_observation(**start_kwargs)
        except Exception as exc:
            # Langfuse SDK versions differ in accepted kwargs (e.g. parent_observation_id).
            # Older SDKs may raise TypeError or custom exception wrappers.
            if "unexpected keyword argument" not in str(exc):
                raise
            fallback_kwargs = {
                k: v
                for k, v in start_kwargs.items()
                if k not in {"level", "status_message", "parent_observation_id"}
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

        if isinstance(capture_handle, dict):
            capture_handle["handle"] = obs

        obs.update_trace(
            name=trace_name or _build_trace_display_name(state),
            user_id=state.user_id,
            session_id=state.device_key,
            metadata={
                "source": "arbiteros_kernel_callback",
                "channel": state.channel,
                "device_key": state.device_key,
            },
        )
        emitted_observation_id = getattr(obs, "id", None)
        if end_observation:
            obs.end()
        return emitted_observation_id if isinstance(emitted_observation_id, str) else None
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
        return None


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


def _replace_instructions_from_modified_response(
    builder: Any,
    modified_response: dict,
    instruction_start_index: int,
) -> None:
    """
    Policy 修改 response 后，用修改后的 response 重新生成 instructions 并替换。
    删除本次添加的 instructions，再根据 modified_response 重新解析并 add 进去。
    """
    if InstructionBuilder is None or builder is None:
        return
    instructions = getattr(builder, "instructions", None)
    if not isinstance(instructions, list) or instruction_start_index >= len(instructions):
        return

    # 1. 删除本次添加的 instructions
    del instructions[instruction_start_index:]
    # 2. 恢复 builder 状态
    builder._runtime_step = len(instructions)
    builder._last_instruction_id = instructions[-1]["id"] if instructions else None

    # 3. 根据 modified_response 重新添加 instructions（tool_calls 先，再 content）
    tc_details = _extract_tool_call_details_from_response(modified_response)
    for tc_detail in tc_details:
        try:
            builder.add_from_tool_call(
                tool_name=tc_detail["tool_name"],
                tool_call_id=tc_detail["tool_call_id"],
                arguments=tc_detail.get("arguments") or {},
                result=None,
            )
        except Exception:
            pass

    content = modified_response.get("content")
    if isinstance(content, str) and content.strip():
        try:
            builder.add_from_structured_output(
                structured={"intent": "RESPOND", "content": content},
            )
        except Exception:
            pass


def _extract_tool_call_details_from_response(response_dict: Optional[dict]) -> list[dict[str, Any]]:
    """从 LLM 响应中提取 tool_calls 的 (id, name, arguments)，用于 post_call_success 时立即存储。"""
    out: list[dict[str, Any]] = []
    raw_tool_calls = (
        response_dict.get("tool_calls")
        if isinstance(response_dict, dict)
        else None
    )
    if not isinstance(raw_tool_calls, list):
        return out
    for tc in raw_tool_calls:
        if not isinstance(tc, dict):
            continue
        tool_call_id = tc.get("id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            continue
        fn = tc.get("function")
        tool_name = (
            fn.get("name")
            if isinstance(fn, dict) and isinstance(fn.get("name"), str)
            else "unknown_tool"
        )
        raw_args = fn.get("arguments") if isinstance(fn, dict) else None
        parsed_args = (
            _safe_json_loads(raw_args)
            if isinstance(raw_args, str)
            else None
        )
        out.append({
            "tool_call_id": tool_call_id,
            "tool_name": tool_name.strip() or "unknown_tool",
            "arguments": parsed_args if isinstance(parsed_args, dict) else (raw_args or {}),
        })
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
    def _looks_like_error_text(text: str) -> bool:
        if not isinstance(text, str):
            return False
        t = text.strip()
        if not t:
            return False
        lowered = t.lower()
        # Common structured error prefixes
        if lowered.startswith(("error:", "exception:", "traceback (most recent call last):")):
            return True

        # HTTP-ish errors
        if (
            "client error" in lowered
            or "server error" in lowered
            or "bad request" in lowered
            or "unauthorized" in lowered
            or "forbidden" in lowered
            or "not found" in lowered
            or "too many requests" in lowered
            or "internal server error" in lowered
            or "bad gateway" in lowered
            or "service unavailable" in lowered
            or "gateway timeout" in lowered
            or "http/1.1" in lowered
            or "status code:" in lowered
            or "error code:" in lowered
            or "developer.mozilla.org/en-us/docs/web/http/status/" in lowered
        ):
            return True

        # Rate limiting / quotas
        if (
            "rate limit" in lowered
            or "rate_limited" in lowered
            or "quota" in lowered
            or "too many requests" in lowered
            or " 429" in lowered
        ):
            return True

        # Network / timeout / connectivity
        if (
            "timed out" in lowered
            or "timeout" in lowered
            or "connection refused" in lowered
            or "connection reset" in lowered
            or "connection error" in lowered
            or "name or service not known" in lowered
            or "temporary failure in name resolution" in lowered
            or "dns" in lowered
            or "econnrefused" in lowered
            or "enotfound" in lowered
            or "eai_again" in lowered
            or "ssl" in lowered and "error" in lowered
        ):
            return True

        # Light CN error indicators (keep strict to avoid false positives)
        if lowered.startswith(("错误:", "异常:", "失败:")):
            return True

        return False

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
        inner_content = payload.get("content")

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

        if level is None:
            # status can be string ("error") or numeric HTTP-like status (>=400)
            if isinstance(status, str):
                lowered_status = status.strip().lower()
                if lowered_status in {"error", "failed", "failure"}:
                    level = "ERROR"
                elif lowered_status in {"warning", "warn"}:
                    level = "WARNING"
            elif isinstance(status, int):
                if status >= 400:
                    level = "ERROR"

        # Any explicit error field is an error (string or object)
        if level is None and error is not None and str(error).strip():
            level = "ERROR"
        # Some tools return errors as plain strings in payload["content"] (e.g. "Error: Client error '429 ...'").
        if (
            level is None
            and isinstance(inner_content, str)
            and _looks_like_error_text(inner_content)
        ):
            level = "ERROR"
            sanitized = _sanitize_error_text_for_langfuse(inner_content)
            # Normalize into a structured error shape for easier UI rendering.
            payload.setdefault("status", "error")
            payload.setdefault("error", sanitized)
        # Some tools return ok/success flags.
        if level is None:
            ok_flag = payload.get("ok")
            success_flag = payload.get("success")
            if ok_flag is False or success_flag is False:
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

    # Plain-text error strings should show as ERROR in Langfuse (e.g. proxy/tool wrapper errors).
    if isinstance(content, str) and _looks_like_error_text(content):
        sanitized = _sanitize_error_text_for_langfuse(content)
        return {
            "output": {"content": {"status": "error", "error": sanitized}},
            "level": "ERROR",
            "status_message": sanitized,
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
        pending_tool_call = _pop_pending_tool_call_node(
            state, tool_call_id=tool_call_id
        )
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
        tool_name = tool_name.strip() or "unknown_tool"
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

        next_index = (
            pending_tool_call.get("index")
            if isinstance(pending_tool_call, dict)
            and isinstance(pending_tool_call.get("index"), int)
            and pending_tool_call.get("index") > 0
            else _next_tool_result_index(state, tool_name)
        )
        tool_result_name = f"{tool_name}.{next_index}"
        parser_stage = (
            pending_tool_call.get("parser_stage")
            if isinstance(pending_tool_call, dict)
            and isinstance(pending_tool_call.get("parser_stage"), str)
            else f"pre_{tool_name}.{next_index}"
        )
        parser_parent_observation_id = (
            pending_tool_call.get("parser_observation_id")
            if isinstance(pending_tool_call, dict)
            and isinstance(pending_tool_call.get("parser_observation_id"), str)
            else None
        )
        parser_metadata_from_pre = (
            pending_tool_call.get("parser_metadata")
            if isinstance(pending_tool_call, dict)
            and isinstance(pending_tool_call.get("parser_metadata"), dict)
            else {}
        )
        parsed_result: Optional[dict[str, Any]] = None
        if isinstance(content, str) and content.strip():
            parsed = _safe_json_loads(content)
            parsed_result = parsed if isinstance(parsed, dict) else {"raw": content}

        parser_snapshot: dict[str, Any] = {}
        instruction_for_metadata: Optional[dict[str, Any]] = None
        if InstructionBuilder is not None and state.trace_id:
            builder = _get_instruction_builder_for_trace(state.trace_id)
            if builder is not None:
                try:
                    instr = builder.add_from_tool_call(
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        arguments=tool_arguments or {},
                        result=parsed_result,
                    )
                    instruction_for_metadata = instr if isinstance(instr, dict) else None
                    # tool call 第二次记录（含 result）：若该 tool_call_id 曾被 policy 保护，加 policy_protected
                    by_trace = _policy_protected_tool_call_ids.get(state.trace_id, {})
                    error_type = by_trace.pop(tool_call_id, None) if isinstance(tool_call_id, str) else None
                    if isinstance(error_type, str) and error_type.strip():
                        builder.instructions[-1]["policy_protected"] = error_type
                    _save_instructions_to_trace_file(state.trace_id, builder)
                    parser_snapshot = _build_instruction_parser_snapshot(
                        state.trace_id,
                        builder,
                    )
                except Exception:
                    parser_snapshot = {}

        formatted_result = _format_tool_result_output_for_langfuse(content)
        output_payload = (
            {"content": parsed_result}
            if isinstance(parsed_result, dict)
            else formatted_result.get("output")
        )
        tool_instruction_type = (
            (instruction_for_metadata or {}).get("instruction_type")
            if isinstance(instruction_for_metadata, dict)
            else None
        )
        tool_instruction_category = (
            (instruction_for_metadata or {}).get("instruction_category")
            if isinstance(instruction_for_metadata, dict)
            else None
        )
        policy_metadata = _build_policy_metadata(
            instruction_type=tool_instruction_type,
            instruction_category=tool_instruction_category,
            instruction=instruction_for_metadata,
        )
        _emit_langfuse_node(
            state=state,
            node_type="tool_result",
            observation_type="tool",
            name=tool_result_name,
            input_payload={
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": tool_arguments,
            },
            output_payload=output_payload,
            metadata={
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "message_index": tool_result.get("message_index"),
                "turn_index": state.turn_index,
                "agent_graph_node": tool_result_name,
                "agent_graph_step": max(state.turn_index, 1) * 10 + 1,
                "parser_stage": parser_stage,
                "parser_trace_id": parser_snapshot.get("parser_trace_id"),
                "trace_id_consistent": parser_snapshot.get("trace_id_consistent"),
                "instruction_count": parser_snapshot.get("instruction_count"),
                **policy_metadata,
                **parser_metadata_from_pre,
            },
            level=formatted_result.get("level"),
            status_message=formatted_result.get("status_message"),
            parent_observation_id=(
                parser_parent_observation_id or _current_parent_observation_id(state)
            ),
            trace_name=_build_trace_display_name(state),
        )
        emitted_any = True

    if emitted_any:
        # Persist counters so tool result numbering stays monotonic across restarts.
        _persist_trace_state_to_disk()
        _flush_langfuse()


def _extract_structured_category_content(
    message_dict: Optional[dict],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """提取 category、content、topic。非严格格式时赋予 topic:其他，category: COGNITIVE_CORE__RESPOND。"""
    if not isinstance(message_dict, dict):
        return (None, None, None)
    content = message_dict.get("content")
    parsed = _safe_json_loads(content)
    if isinstance(parsed, dict) and _is_strict_topic_category_content(parsed):
        category = parsed.get("category")
        topic = parsed.get("topic")
        return (
            category if isinstance(category, str) else None,
            parsed.get("content"),
            topic if isinstance(topic, str) else None,
        )
    # 非严格格式：人为赋予 topic:其他，category: COGNITIVE_CORE__RESPOND
    if isinstance(content, str) and content.strip():
        return ("COGNITIVE_CORE__RESPOND", content, "其他")
    return (None, content if isinstance(content, str) else None, None)


def _ensure_turn_node_if_needed(context: _DeviceContext, state: _TraceState) -> None:
    if not context.latest_user_text or not context.latest_user_fingerprint:
        return

    should_emit = False
    next_turn_index = 0
    preview = _short_text_preview(context.latest_user_text)
    with _trace_state_lock:
        if state.last_user_fingerprint != context.latest_user_fingerprint:
            state.last_user_fingerprint = context.latest_user_fingerprint
            state.latest_user_preview = preview
            state.current_turn_observation_id = None
            state.turn_index += 1
            next_turn_index = state.turn_index
            should_emit = True

    if not should_emit:
        return

    # Close any prior turn handle defensively (e.g., if a previous request crashed mid-turn).
    try:
        with _trace_state_lock:
            prev_handle = state.current_turn_handle
            state.current_turn_handle = None
        if prev_handle is not None:
            prev_handle.end()
    except Exception:
        pass

    turn_name = f"{_NODE_NAMESPACE_PREFIX}.turn.{next_turn_index:03d}"
    # Keep `output` field (explicitly null) for consistency across nodes.
    handle_box: dict[str, Any] = {}
    turn_observation_id = _emit_langfuse_node(
        state=state,
        node_type="turn",
        observation_type="chain",
        name=turn_name,
        # Start with raw user input; we'll refine to "<topic> - kernel.<category>" post-call.
        input_payload=context.latest_user_text,
        output_payload=None,
        capture_handle=handle_box,
        end_observation=False,
        metadata={
            "text_preview": context.latest_user_text[:300],
            "reset_requested": context.reset_requested,
            "turn_index": next_turn_index,
            "agent_graph_node": turn_name,
            "agent_graph_step": next_turn_index * 10,
        },
        parent_observation_id=state.root_observation_id,
        trace_name=_build_trace_display_name(state),
    )
    if isinstance(turn_observation_id, str):
        with _trace_state_lock:
            state.current_turn_observation_id = turn_observation_id
            state.current_turn_handle = handle_box.get("handle")


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
    _ensure_turn_node_if_needed(context, state)

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
        # Before actual tool execution, emit parser.pre_{tool}.{n} and reserve
        # the same {tool}.{n} index for the later tool result node.
        parser_snapshot = _build_instruction_parser_snapshot(
            state.trace_id,
            _peek_instruction_builder_for_trace(state.trace_id),
        )
        parsed_tool_calls = _extract_tool_call_details_from_response(
            response_before_transform
        )
        for tc_detail in parsed_tool_calls:
            tool_call_id = tc_detail.get("tool_call_id")
            tool_name = tc_detail.get("tool_name")
            if not isinstance(tool_name, str) or not tool_name.strip():
                tool_name = "unknown_tool"
            next_index = _reserve_tool_result_index_for_call(
                state,
                tool_call_id=tool_call_id if isinstance(tool_call_id, str) else None,
                tool_name=tool_name,
            )
            parser_stage = f"pre_{tool_name}.{next_index}"
            parser_metadata = {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_call_node": f"{tool_name}.{next_index}",
                "tool_call_count": len(tool_calls),
                "trace_id_consistent": parser_snapshot.get("trace_id_consistent"),
                "parser_trace_id": parser_snapshot.get("parser_trace_id"),
                "instruction_count": parser_snapshot.get("instruction_count"),
            }
            parser_observation_id = _emit_instruction_parser_node(
                state=state,
                parser_stage=parser_stage,
                input_payload={
                    "raw_tool_call": tc_detail,
                    "raw_tool_calls": tool_calls,
                },
                output_payload={
                    "parsed_tool_call": tc_detail,
                    "instruction_snapshot": parser_snapshot,
                },
                metadata=parser_metadata,
                parent_observation_id=_current_parent_observation_id(state),
            )
            _set_pending_tool_call_node(
                state,
                tool_call_id=tool_call_id if isinstance(tool_call_id, str) else None,
                payload={
                    "index": next_index,
                    "tool_name": tool_name,
                    "parser_stage": parser_stage,
                    "parser_observation_id": parser_observation_id,
                    "parser_metadata": parser_metadata,
                },
            )
        _flush_langfuse()
        return

    category, structured_content, llm_topic = _extract_structured_category_content(
        response_before_transform
    )
    effective_category = (
        category
        if isinstance(category, str) and category.strip()
        else "NOT_CLASSIFIED_RESPOND"
    )
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

    generation_input_payload = {
        "user_text": context.latest_user_text,
        "category": effective_category,
    }
    max_topic_chars = int(os.getenv("ARBITEROS_LANGFUSE_TOPIC_MAX_CHARS", "40"))
    max_topic_points = int(os.getenv("ARBITEROS_LANGFUSE_TOPIC_MAX_POINTS", "3"))
    max_topic_point_chars = int(
        os.getenv("ARBITEROS_LANGFUSE_TOPIC_POINT_MAX_CHARS", "24")
    )
    with _trace_state_lock:
        previous_topic_summary = state.latest_topic_summary
    previous_topic_clean = _normalize_topic_summary(
        previous_topic_summary,
        max_points=max_topic_points,
        max_point_chars=max_topic_point_chars,
        max_total_chars=max_topic_chars,
    )
    previous_topic_for_fallback = _sanitize_topic_preview(
        previous_topic_clean or previous_topic_summary,
        max_chars=max_topic_chars,
        allow_reset_control_topic=False,
    )
    allow_reset_control_topic = (
        context.reset_requested
        and max(state.turn_index, 1) <= 1
        and not previous_topic_for_fallback
    )

    llm_topic_raw = llm_topic if isinstance(llm_topic, str) else None
    llm_topic_reuse_previous = bool(
        isinstance(llm_topic_raw, str) and not llm_topic_raw.strip()
    )
    llm_topic_clean = _normalize_topic_summary(
        llm_topic_raw,
        max_points=max_topic_points,
        max_point_chars=max_topic_point_chars,
        max_total_chars=max_topic_chars,
    ) or _short_text_preview(llm_topic_raw, max_chars=max_topic_chars)
    llm_topic_candidate = _sanitize_topic_preview(
        llm_topic_clean,
        max_chars=max_topic_chars,
        allow_reset_control_topic=allow_reset_control_topic,
    )
    fallback_user_topic = _sanitize_topic_preview(
        context.latest_user_text,
        max_chars=max_topic_chars,
        allow_reset_control_topic=allow_reset_control_topic,
    ) or _sanitize_topic_preview(
        state.latest_user_preview,
        max_chars=max_topic_chars,
        allow_reset_control_topic=allow_reset_control_topic,
    )
    kernel_turn_topic = (
        llm_topic_candidate
        or (previous_topic_for_fallback if llm_topic_reuse_previous else None)
        or fallback_user_topic
        or previous_topic_for_fallback
    )
    trace_topic = kernel_turn_topic or previous_topic_for_fallback
    persist_topic_needed = False
    if isinstance(trace_topic, str) and trace_topic.strip():
        with _trace_state_lock:
            if state.latest_topic_summary != trace_topic:
                state.latest_topic_summary = trace_topic
                persist_topic_needed = True
    if persist_topic_needed:
        _persist_trace_state_to_disk()

    output_name = f"{_NODE_NAMESPACE_PREFIX}.output.turn_{max(state.turn_index, 1):03d}"
    _emit_langfuse_node(
        state=state,
        node_type="output",
        observation_type="generation",
        name=output_name,
        input_payload=generation_input_payload,
        output_payload={"content": output_content, "category": effective_category},
        metadata={
            "category": effective_category,
            "raw_output_content": raw_output_content,
            "turn_index": state.turn_index,
            "agent_graph_node": output_name,
            "agent_graph_step": max(state.turn_index, 1) * 10 + 1,
            **_build_policy_metadata(
                instruction_type=_normalize_category_to_instruction_type(effective_category),
                instruction_category=effective_category,
            ),
        },
        model=model,
        parent_observation_id=_current_parent_observation_id(state),
        trace_name=_build_trace_display_name(state),
    )
    # Emit one kernel_step per turn for consistent Langfuse graphs.
    # Topic fallback order:
    #   1) LLM topic candidate
    #   2) previous topic when LLM returns empty topic ("reuse previous")
    #   3) latest user text preview
    #   4) previous topic summary
    kernel_step_name = (
        f"{kernel_turn_topic} - kernel.{effective_category.lower()}"
        if kernel_turn_topic
        else f"kernel.{effective_category.lower()}"
    )

    # Update the corresponding turn node's input rendering to match the kernel label.
    # This keeps the graph consistent: turn input shows "<topic> - kernel.<category>".
    _emit_langfuse_node(
        state=state,
        node_type="kernel_step",
        observation_type="agent",
        name=kernel_step_name,
        input_payload={"content": structured_content},
        output_payload=None,
        metadata={
            "category": effective_category,
            "turn_index": state.turn_index,
            "agent_graph_node": kernel_step_name,
            "agent_graph_step": max(state.turn_index, 1) * 10 + 2,
            **_build_policy_metadata(
                instruction_type=_normalize_category_to_instruction_type(effective_category),
                instruction_category=effective_category,
            ),
        },
        parent_observation_id=_current_parent_observation_id(state),
        trace_name=_build_trace_display_name(state),
    )

    raw_structured_payload = (
        _safe_json_loads(raw_output_content)
        if isinstance(raw_output_content, str)
        else None
    )
    if isinstance(raw_structured_payload, dict) and "content" in raw_structured_payload:
        parser_snapshot = _build_instruction_parser_snapshot(
            state.trace_id,
            _peek_instruction_builder_for_trace(state.trace_id),
        )
        _emit_instruction_parser_node(
            state=state,
            parser_stage="structured_output",
            input_payload={
                "raw_content": raw_output_content,
                "category": raw_structured_payload.get("category"),
            },
            output_payload={
                "parsed_content": structured_content,
                "instruction_snapshot": parser_snapshot,
            },
            metadata={
                "category": effective_category,
                "trace_id_consistent": parser_snapshot.get("trace_id_consistent"),
                "parser_trace_id": parser_snapshot.get("parser_trace_id"),
            },
            parent_observation_id=_current_parent_observation_id(state),
        )

    # Close the turn span after the final response nodes are emitted.
    try:
        with _trace_state_lock:
            turn_handle = state.current_turn_handle
            state.current_turn_handle = None
        if turn_handle is not None:
            turn_handle.end()
    except Exception:
        pass
    _flush_langfuse()


def _ensure_non_empty_assistant_message(
    message_dict: Optional[dict],
    *,
    fallback_text: str,
) -> Optional[dict]:
    """Guardrail: never return/emit an assistant message with empty textual content."""
    if not isinstance(message_dict, dict):
        return message_dict
    # Tool calls (or legacy function_call) legitimately have no content.
    if message_dict.get("tool_calls") or message_dict.get("function_call"):
        return message_dict

    def _is_valid_text_content(text: str) -> bool:
        normalized = text.strip()
        if not normalized:
            return False
        return normalized not in _MALFORMED_PLACEHOLDER_CONTENTS

    content = message_dict.get("content")
    if isinstance(content, str) and _is_valid_text_content(content):
        return message_dict
    if isinstance(content, list):
        extracted = _extract_text_from_message_content(content)
        if _is_valid_text_content(extracted):
            return message_dict
    if not fallback_text or not isinstance(fallback_text, str):
        fallback_text = "抱歉，我这次没有生成有效回复，请重试。"
    return {**message_dict, "content": fallback_text}


def _emit_failure_node(request_data: Optional[dict], original_exception: Exception) -> None:
    incoming = request_data if isinstance(request_data, dict) else {}
    context = _build_device_context(incoming)
    state = _resolve_trace_state_from_metadata(incoming, context=context)
    if state is None:
        state, _ = _ensure_trace_state(context)
    _ensure_turn_node_if_needed(context, state)
    error_text = _sanitize_error_text_for_langfuse(str(original_exception))
    error_preview = _short_text_preview(error_text, max_chars=180) or "LLM call failed"
    _emit_langfuse_node(
        state=state,
        node_type="failure",
        observation_type="span",
        name=f"{_NODE_NAMESPACE_PREFIX}.failure",
        input_payload=None,
        output_payload={"error": error_text},
        metadata={"error_type": type(original_exception).__name__},
        level="ERROR",
        status_message=error_preview,
        parent_observation_id=_current_parent_observation_id(state),
        trace_name=_build_trace_display_name(state),
    )
    # Close any open turn handle on failure.
    try:
        with _trace_state_lock:
            turn_handle = state.current_turn_handle
            state.current_turn_handle = None
        if turn_handle is not None:
            turn_handle.end()
    except Exception:
        pass
    _flush_langfuse()


# ---------------------------------------------------------------------------
# 响应修改规则（流式 + 非流式）：用于在 post_call_success 时改写返回给调用方的内容
# - 若有 tool_calls：不改动
# - 若为 content 且为 JSON 字符串（含 category/content）：只保留内层 content，去掉 category，
#   并按 device_key 记录剥去的 category，供 pre_call 时把 history 包回
# ---------------------------------------------------------------------------
def _resolve_category_cache_device_key(data: dict) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        explicit = metadata.get("arbiteros_device_key")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
    context = _build_device_context(data)
    return context.device_key if isinstance(context.device_key, str) else None


def _get_instruction_builder_for_trace(trace_id: str) -> Optional[Any]:
    """Get or create InstructionBuilder for a trace_id. Returns None if instruction_parsing unavailable."""
    if InstructionBuilder is None or not isinstance(trace_id, str) or not trace_id.strip():
        return None
    with _instruction_builders_lock:
        builder = _instruction_builders_by_trace.get(trace_id)
        if builder is None:
            builder = InstructionBuilder(trace_id=trace_id)
            _instruction_builders_by_trace[trace_id] = builder
            # Evict oldest if over limit (simple FIFO by trace_id order)
            if len(_instruction_builders_by_trace) > _MAX_INSTRUCTION_BUILDERS:
                for k in list(_instruction_builders_by_trace.keys()):
                    if k != trace_id:
                        del _instruction_builders_by_trace[k]
                        break
        return builder


def _save_instructions_to_trace_file(trace_id: str, builder: Any) -> None:
    """Persist InstructionBuilder to log/{trace_id}.json"""
    if not trace_id or not builder:
        return
    try:
        path = _INSTRUCTION_LOG_DIR / f"{trace_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            f.write(builder.to_json())
    except Exception:
        pass  # Best-effort; don't fail the main flow


def _peek_instruction_builder_for_trace(trace_id: str) -> Optional[Any]:
    """Get existing InstructionBuilder for trace_id without creating a new one."""
    if not isinstance(trace_id, str) or not trace_id.strip():
        return None
    with _instruction_builders_lock:
        return _instruction_builders_by_trace.get(trace_id)


def _build_instruction_parser_snapshot(trace_id: str, builder: Optional[Any]) -> dict[str, Any]:
    trace_file = _INSTRUCTION_LOG_DIR / f"{trace_id}.json"
    snapshot: dict[str, Any] = {
        "instruction_file": str(trace_file),
        "instruction_file_exists": trace_file.exists(),
        "instruction_count": 0,
        "latest_instruction": None,
        "parser_trace_id": None,
        "trace_id_consistent": None,
    }
    if not isinstance(trace_id, str) or not trace_id.strip():
        return snapshot
    if builder is None:
        return snapshot

    parser_trace_id = getattr(builder, "trace_id", None)
    if isinstance(parser_trace_id, str):
        snapshot["parser_trace_id"] = parser_trace_id
        snapshot["trace_id_consistent"] = parser_trace_id == trace_id
        if parser_trace_id != trace_id:
            _save_json(
                "instruction_parser_trace_id_mismatch",
                {
                    "trace_id": trace_id,
                    "parser_trace_id": parser_trace_id,
                },
            )

    instructions = getattr(builder, "instructions", None)
    if isinstance(instructions, list):
        snapshot["instruction_count"] = len(instructions)
        if instructions:
            snapshot["latest_instruction"] = instructions[-1]

    return snapshot


def _count_rule_effects(rule_types: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not isinstance(rule_types, list):
        return counts
    for rule in rule_types:
        if not isinstance(rule, dict):
            continue
        action = rule.get("action")
        if not isinstance(action, dict):
            continue
        effect = action.get("effect")
        if not isinstance(effect, str) or not effect.strip():
            continue
        normalized = effect.strip().upper()
        counts[normalized] = counts.get(normalized, 0) + 1
    return counts


def _build_policy_metadata(
    *,
    instruction_type: Optional[str],
    instruction_category: Optional[str] = None,
    instruction: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Build compact policy metadata for Langfuse observation metadata.
    This is intentionally small/stable so UIs can render high-level policy nodes.
    """
    out: dict[str, Any] = {}
    itype = instruction_type.strip().upper() if isinstance(instruction_type, str) else None
    if itype:
        out["instruction_type"] = itype
    if isinstance(instruction_category, str) and instruction_category.strip():
        out["instruction_category"] = instruction_category.strip()

    security_type: Any = None
    rule_types: Any = None
    if isinstance(instruction, dict):
        security_type = instruction.get("security_type")
        rule_types = instruction.get("rule_types")

    if security_type is None and get_instruction_security is not None and itype:
        try:
            sec, rules = get_instruction_security(itype, instruction_category)
            security_type = sec
            rule_types = rules
        except Exception:
            security_type = None
            rule_types = None

    if isinstance(security_type, dict):
        out["policy_security_type"] = security_type
        # Also mirror the most-used fields at top-level for easier querying.
        for k in (
            "authority_label",
            "confidentiality",
            "integrity",
            "trustworthiness",
            "confidence",
            "reversible",
            "confidentiality_label",
        ):
            if k in security_type:
                out[f"policy_{k}"] = security_type.get(k)

    effect_counts = _count_rule_effects(rule_types)
    if effect_counts:
        out["policy_rule_effect_counts"] = effect_counts
        out["policy_has_block"] = effect_counts.get("BLOCK", 0) > 0

    return out


def _emit_instruction_parser_node(
    *,
    state: _TraceState,
    parser_stage: str,
    input_payload: Any,
    output_payload: Any,
    metadata: Optional[dict[str, Any]] = None,
    parent_observation_id: Optional[str] = None,
) -> Optional[str]:
    turn_idx = max(state.turn_index, 1)
    parser_node_name = f"{_NODE_NAMESPACE_PREFIX}.parser.turn_{turn_idx:03d}.{parser_stage}"
    return _emit_langfuse_node(
        state=state,
        node_type="parser",
        observation_type="span",
        name=parser_node_name,
        input_payload=input_payload,
        output_payload=output_payload,
        metadata={
            "parser_stage": parser_stage,
            "turn_index": state.turn_index,
            "agent_graph_node": parser_node_name,
            "agent_graph_step": turn_idx * 10 + 3,
            **(metadata or {}),
        },
        parent_observation_id=parent_observation_id or _current_parent_observation_id(state),
        trace_name=_build_trace_display_name(state),
    )


def _normalize_category_to_instruction_type(category: Any) -> str:
    """Map category (PREFIX__TYPE format or legacy) to instruction_parser instruction_type."""
    if not isinstance(category, str) or not category.strip():
        return "REASON"
    c = category.strip()
    # 先查显式映射（兼容旧格式如 COGNITIVE_CORE__GENERATE -> REASON）
    if c in _CATEGORY_TO_INSTRUCTION_TYPE:
        return _CATEGORY_TO_INSTRUCTION_TYPE[c]
    # 带前缀格式（如 COGNITIVE_CORE__REASON）：去掉前缀，取后半部分
    if "__" in c:
        return c.split("__")[-1]
    return c


def _record_stripped_category(
    data: dict, category: Any, topic: Optional[str] = None
) -> None:
    device_key = _resolve_category_cache_device_key(data)
    if not device_key:
        return
    normalized_category = category if isinstance(category, str) else ""
    normalized_topic = (
        topic if isinstance(topic, str) and topic.strip() else None
    )
    with _stripped_categories_lock:
        categories = _stripped_categories_by_device.setdefault(device_key, [])
        categories.append(normalized_category)
        if len(categories) > _MAX_STRIPPED_CATEGORIES:
            del categories[: len(categories) - _MAX_STRIPPED_CATEGORIES]
        topics = _stripped_topics_by_device.setdefault(device_key, [])
        topics.append(normalized_topic)
        if len(topics) > _MAX_STRIPPED_CATEGORIES:
            del topics[: len(topics) - _MAX_STRIPPED_CATEGORIES]


def _get_stripped_categories_for_device(device_key: Optional[str]) -> list[str]:
    if not isinstance(device_key, str) or not device_key.strip():
        return []
    with _stripped_categories_lock:
        categories = _stripped_categories_by_device.get(device_key.strip(), [])
        return list(categories)


def _get_stripped_topics_for_device(device_key: Optional[str]) -> list[Optional[str]]:
    if not isinstance(device_key, str) or not device_key.strip():
        return []
    with _stripped_categories_lock:
        topics = _stripped_topics_by_device.get(device_key.strip(), [])
        return list(topics)


def _clear_stripped_categories_for_device(device_key: Optional[str]) -> None:
    if not isinstance(device_key, str) or not device_key.strip():
        return
    with _stripped_categories_lock:
        _stripped_categories_by_device.pop(device_key.strip(), None)
        _stripped_topics_by_device.pop(device_key.strip(), None)


def _add_instruction_for_non_strict(data: dict, content: str) -> None:
    """非严格格式时，为 instruction_parsing 等赋予 topic:其他，category: COGNITIVE_CORE__RESPOND。"""
    if not isinstance(content, str) or not content.strip():
        return
    metadata = data.get("metadata") if isinstance(data, dict) else {}
    trace_id = (
        metadata.get("arbiteros_trace_id")
        if isinstance(metadata, dict)
        else None
    )
    if not isinstance(trace_id, str) or not trace_id.strip() or InstructionBuilder is None:
        return
    builder = _get_instruction_builder_for_trace(trace_id)
    if builder is None:
        return
    try:
        builder.add_from_structured_output(
            structured={
                "intent": "RESPOND",
                "content": content,
            }
        )
        _save_instructions_to_trace_file(trace_id, builder)
    except Exception:
        pass


def _is_strict_topic_category_content(obj: dict) -> bool:
    """严格 topic/category/content 三字段结构：仅此三 key，content 类型不限。"""
    if not isinstance(obj, dict):
        return False
    return set(obj.keys()) == {"topic", "category", "content"}


def _response_transform_content_only(data: dict, message_dict: dict) -> Optional[dict]:
    """没 content 才忽略；有 content 且为严格的 topic/category/content 结构则剥 structure，否则不操作但记录 NO_WRAP。"""
    content = message_dict.get("content")
    if not isinstance(content, str) or not content.strip():
        return message_dict
    try:
        inner = json.loads(content)
        if isinstance(inner, dict) and _is_strict_topic_category_content(inner):
            category = inner.get("category", "")
            topic = inner.get("topic") if isinstance(inner.get("topic"), str) else None
            _record_stripped_category(data, category, topic=topic)

            # instruction_parsing: content 类型不限，该是啥就是啥
            inner_content = inner.get("content")
            metadata = data.get("metadata") if isinstance(data, dict) else {}
            trace_id = (
                metadata.get("arbiteros_trace_id")
                if isinstance(metadata, dict)
                else None
            )
            if isinstance(trace_id, str) and trace_id.strip() and InstructionBuilder is not None:
                builder = _get_instruction_builder_for_trace(trace_id)
                if builder is not None:
                    instruction_type = _normalize_category_to_instruction_type(category)
                    try:
                        builder.add_from_structured_output(
                            structured={"intent": instruction_type, "content": inner_content}
                        )
                        _save_instructions_to_trace_file(trace_id, builder)
                    except Exception:
                        pass  # Best-effort; don't fail the main flow

            out = {**message_dict, "content": inner_content}
            return out
        # 有 content 但非严格格式：不剥，记录 NO_WRAP。request 时遇到则不包。
        # 其他处（如 instruction_parsing、Langfuse）需用时，人为赋予 topic:其他，category: COGNITIVE_CORE__RESPOND
        _record_stripped_category(data, _NO_WRAP_SENTINEL, topic=None)
        _add_instruction_for_non_strict(data, content)
    except (json.JSONDecodeError, TypeError):
        # 非 JSON（如纯文本）：不剥，记录 NO_WRAP
        _record_stripped_category(data, _NO_WRAP_SENTINEL, topic=None)
        _add_instruction_for_non_strict(data, content)
    return message_dict


def _extract_text_to_wrap(msg: dict) -> tuple[Optional[str], Optional[Any], Optional[int]]:
    """
    从一条 assistant 消息里取出需要包结构的纯文本。
    是否包由 category list 严格回溯：剥了才记录，没剥不记录。包时严格按 list 来，无需额外判断。
    - content 为字符串：有内容则返回 (content, None, None)。
    - content 为列表：返回 (part["text"], content_list, part_index)。
    - content 为空：返回 (None, None, None)。
    """
    content = msg.get("content")
    # 格式1: content 是字符串
    if isinstance(content, str):
        if not content.strip():
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
            return (text, content, idx)
    return (None, None, None)


def _inject_topic_summary_hint(
    data: dict, *, state: _TraceState, context: _DeviceContext
) -> dict:
    messages = data.get("messages")

    previous_topic_raw = state.latest_topic_summary if isinstance(state.latest_topic_summary, str) else None
    previous_topic = (
        _normalize_topic_summary(
            previous_topic_raw,
            max_points=3,
            max_point_chars=32,
            max_total_chars=120,
        )
        or _short_text_preview(previous_topic_raw, max_chars=120)
        or "(none)"
    )
    latest_user_turn = (
        _short_text_preview(context.latest_user_text, max_chars=200)
        if isinstance(context.latest_user_text, str)
        else None
    ) or "(none)"

    marker = "[arbiteros_topic_hint]"
    hint_content = (
        f"{marker}\n"
        "Generate JSON string field `topic` (for trace naming) using BOTH:\n"
        "1) current summarized topic\n"
        "2) latest user turn\n"
        "\n"
        "Requirements for `topic`:\n"
        "- Output a single short topic phrase by default.\n"
        "- Keep it concise: usually <= 16 Chinese chars or <= 32 chars.\n"
        "- Match the latest turn intent and language (Chinese/English).\n"
        "- Only output multiple topic points (separated by ` / `) when the user's topic clearly shifts.\n"
        "- If shifted, include the previous topic + the new topic (only as needed).\n"
        "- Use short noun-phrase style topics (no sentences, no steps, no process words).\n"
        "- Do NOT include detailed facts in topic: numbers, timestamps, percentages, URLs, markdown formatting.\n"
        "- Do NOT include control/reset words: new session, /new, /reset, reset session, 重置会话, 重制对话, greet user, next, please wait, kernel.*, execution_core.\n"
        "- If latest user turn is only reset/new-session control text, return an empty topic unless this is turn.001 with no other topic context.\n"
        "- If latest user turn is generic/greeting, keep the current summarized topic.\n"
        "- If the best topic is the same as current summarized topic, return an empty string \"\" to reuse previous topic.\n"
        "- If latest turn is follow-up that changes time/scope (example: from 今日天气 to 明天呢), generate a new topic instead of \"\".\n"
        "Fallback order for `topic`: current summarized topic -> latest user turn.\n"
        f"Current summarized topic: {previous_topic}\n"
        f"Latest user turn: {latest_user_turn}"
    )
    hint_message = {"role": "system", "content": hint_content}

    # Chat Completions style requests.
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
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            if isinstance(msg, dict) and msg.get("role") == "user":
                insert_at = i
                break

        new_messages = list(messages)
        new_messages.insert(insert_at, hint_message)
        return {**data, "messages": new_messages}

    # Responses API style requests: append hint into `instructions`.
    if _is_responses_api_request(data):
        instructions = data.get("instructions")
        if isinstance(instructions, str) and marker in instructions:
            return data
        if isinstance(instructions, str) and instructions.strip():
            new_instructions = f"{instructions.rstrip()}\n\n{hint_content}"
        else:
            new_instructions = hint_content
        return {**data, "instructions": new_instructions}

    return data


# 与 litellm_config.yaml 中 instruction_output 一致的 base schema，content 将被 agent 的 schema 替换
_ARBITEROS_BASE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topic": {
            "type": "string",
            "description": "A concise topic/title for this turn (keep it short, e.g. 8-20 Chinese chars or <= 40 chars).",
            "maxLength": 60,
        },
        "category": {
            "type": "string",
            "enum": [
                "COGNITIVE_CORE__REASON",
                "COGNITIVE_CORE__PLAN",
                "COGNITIVE_CORE__CRITIQUE",
                "COGNITIVE_CORE__ASK",
                "COGNITIVE_CORE__RESPOND",
            ],
            "description": "The instruction category type. Use the most Accurate category possible, rather than only choose 'COGNITIVE_CORE__RESPOND'. ",
        },
        "content": {
            "type": "string",
            "description": "The actual content of the instruction. It may take any form and contain whatever you need to generate.",
        },
    },
    "required": ["topic", "category", "content"],
    "additionalProperties": False,
}


def _merge_agent_response_format_into_content(data: dict) -> None:
    """
    若 agent 请求带了 response_format，将其作为子结构塞入我们的 topic/category/content 的 content 字段。
    原地修改 data["response_format"]。
    """
    agent_rf = data.get("response_format")
    if not isinstance(agent_rf, dict):
        return
    # 提取 agent 的 schema：支持 json_schema.schema 或 schema
    agent_schema = None
    js = agent_rf.get("json_schema")
    if isinstance(js, dict):
        agent_schema = js.get("schema")
    if agent_schema is None:
        agent_schema = agent_rf.get("schema")
    if agent_schema is None or not isinstance(agent_schema, dict):
        return
    # 合并：我们的 base schema，content 替换为 agent 的 schema
    merged_schema = copy.deepcopy(_ARBITEROS_BASE_RESPONSE_SCHEMA)
    merged_schema["properties"]["content"] = copy.deepcopy(agent_schema)
    data["response_format"] = {
        "type": "json_schema",
        "json_schema": {
            "name": "instruction_output",
            "schema": merged_schema,
            "strict": True,
        },
    }


def _wrap_messages_with_categories(data: dict, *, device_key: Optional[str] = None) -> dict:
    """在 pre_call 前把 incoming 里 role=assistant 且 content 有文本的 history 从后往前包回结构。
    包的时候从 category/topic 列表末尾往前按位置取，与 history 一一对应。
    遇到 NO_WRAP label 则不包，保持原样。
    content 为 null/空 的消息（如 tool_calls-only）不包、不消耗槽位，避免错位。
    """
    resolved_device_key = device_key or _resolve_category_cache_device_key(data)
    stripped_categories = _get_stripped_categories_for_device(resolved_device_key)
    stripped_topics = _get_stripped_topics_for_device(resolved_device_key)
    messages = data.get("messages")
    if not messages or not stripped_categories:
        return data
    messages = list(messages)
    idx_from_end = 0  # 当前包的是「从末尾数第几个」有 content 的 assistant，0=最后一个
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        # 先检查 content 是否可包：content 为 null/空 则不包、不消耗槽位（tool_calls-only 不 strip 故无 record）
        text, content_list, part_idx = _extract_text_to_wrap(msg)
        if text is None:
            continue
        if idx_from_end >= len(stripped_categories):
            break
        category = stripped_categories[-(idx_from_end + 1)]
        topic = (
            stripped_topics[-(idx_from_end + 1)]
            if idx_from_end < len(stripped_topics)
            else None
        )
        idx_from_end += 1
        if category == _NO_WRAP_SENTINEL:
            continue
        wrap_obj: dict[str, Any] = {"category": category, "content": text}
        if isinstance(topic, str) and topic.strip():
            wrap_obj["topic"] = topic
        wrapped = json.dumps(wrap_obj, ensure_ascii=False)
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
        # If reset marker exists in history, drop prior stale turns first.
        messages = data.get("messages")
        if isinstance(messages, list):
            truncated = _truncate_messages_after_last_reset(messages)
            if truncated is not messages:
                data = {**data, "messages": truncated}

        # 若 agent 带了 response_format，将其作为子结构塞入我们的 content 字段
        _merge_agent_response_format_into_content(data)

        context = _build_device_context(data)
        if context.reset_requested:
            # Reset should start with a clean category cache for this device.
            _clear_stripped_categories_for_device(context.device_key)
        else:
            # 把 history 里 assistant 的 content 按当前会话记录的 category 从后往前包回结构，再请求
            data = _wrap_messages_with_categories(data, device_key=context.device_key)
        # Prefer explicit trace binding from caller metadata when provided.
        metadata = data.get("metadata") if isinstance(data, dict) else None
        bound_trace_id = (
            metadata.get("arbiteros_trace_id")
            if isinstance(metadata, dict) and isinstance(metadata.get("arbiteros_trace_id"), str)
            else None
        )
        bound_device_key = (
            metadata.get("arbiteros_device_key")
            if isinstance(metadata, dict) and isinstance(metadata.get("arbiteros_device_key"), str)
            else None
        )
        created_new_trace = False
        state = None
        # If a reset/new-session is explicitly requested, rotate via kernel state so the
        # next call is guaranteed to have a fresh trace_id (even if caller replays old metadata).
        if context.reset_requested:
            state, created_new_trace = _ensure_trace_state(context)
        elif bound_trace_id and bound_device_key:
            _sync_trace_state_from_disk()
            previous_trace_id: Optional[str] = None
            with _trace_state_lock:
                current = _trace_state_by_device.get(bound_device_key)
                if current is not None:
                    previous_trace_id = current.trace_id
            state = _resolve_trace_state_from_metadata(data, context=context)
            if state is not None:
                created_new_trace = previous_trace_id != state.trace_id
        if state is None:
            state, created_new_trace = _ensure_trace_state(context)

        # Clean up any previously persisted noisy topic summaries so future traces
        # don't inherit "process step" labels (e.g., "new session / greet user / next").
        max_topic_chars = int(os.getenv("ARBITEROS_LANGFUSE_TOPIC_MAX_CHARS", "40"))
        max_topic_points = int(os.getenv("ARBITEROS_LANGFUSE_TOPIC_MAX_POINTS", "3"))
        max_topic_point_chars = int(
            os.getenv("ARBITEROS_LANGFUSE_TOPIC_POINT_MAX_CHARS", "24")
        )
        with _trace_state_lock:
            previous_topic_summary = state.latest_topic_summary
        cleaned_previous_topic = _normalize_topic_summary(
            previous_topic_summary,
            max_points=max_topic_points,
            max_point_chars=max_topic_point_chars,
            max_total_chars=max_topic_chars,
        )
        if _is_reset_control_topic(cleaned_previous_topic):
            cleaned_previous_topic = None
        if cleaned_previous_topic != previous_topic_summary:
            with _trace_state_lock:
                state.latest_topic_summary = cleaned_previous_topic
            _persist_trace_state_to_disk()
        data = _inject_topic_summary_hint(data, state=state, context=context)

        if created_new_trace:
            root_observation_id = _emit_langfuse_node(
                state=state,
                node_type="trace_start",
                observation_type="span",
                name=f"{_NODE_NAMESPACE_PREFIX}.trace.start",
                input_payload={
                    "reason": (
                        "reset_requested"
                        if context.reset_requested
                        else ("external_trace_binding" if bound_trace_id else "new_device_or_session")
                    )
                },
                metadata={
                    "reset_requested": context.reset_requested,
                    "agent_graph_node": f"{_NODE_NAMESPACE_PREFIX}.trace.start",
                    "agent_graph_step": 0,
                },
                trace_name=_build_trace_display_name(state),
            )
            if isinstance(root_observation_id, str):
                with _trace_state_lock:
                    state.root_observation_id = root_observation_id
        _ensure_turn_node_if_needed(context, state)
        _emit_tool_result_nodes_if_needed(data, state)
        data = _inject_trace_metadata(data, state)
        filtered_data = {
            k: data[k] for k in ["model", "messages", "tools", "metadata"] if k in data
        }
        if os.getenv("ARBITEROS_LITELLM_CALLBACK_DEBUG", "").strip() == "1":
            _console.print(
                Panel(
                    Pretty(filtered_data),
                    title="Pre Call Hook - Incoming Data",
                )
            )
            _save_json("pre_call", {"call_type": call_type, "incoming": filtered_data})
        _save_precall_to_log(data)
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
        if os.getenv("ARBITEROS_LITELLM_CALLBACK_DEBUG", "").strip() == "1":
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

        # 记录 instruction 数量，供 policy 保护时标记本次添加的 instructions
        _policy_instruction_count_before = 0
        _policy_trace_id_for_block: Optional[str] = None
        if InstructionBuilder is not None:
            metadata = data.get("metadata") if isinstance(data, dict) else {}
            _policy_trace_id_for_block = (
                metadata.get("arbiteros_trace_id")
                if isinstance(metadata, dict) else None
            )
            if isinstance(_policy_trace_id_for_block, str) and _policy_trace_id_for_block.strip():
                builder_pre = _get_instruction_builder_for_trace(_policy_trace_id_for_block)
                _policy_instruction_count_before = (
                    len(getattr(builder_pre, "instructions", [])) if builder_pre else 0
                )

        # instruction_parsing: 在 post_call_success 时立即截获 tool_calls（name+arguments），单独存一条
        if raw_msg_dict is not None and InstructionBuilder is not None:
            metadata = data.get("metadata") if isinstance(data, dict) else {}
            trace_id = (
                metadata.get("arbiteros_trace_id")
                if isinstance(metadata, dict)
                else None
            )
            if isinstance(trace_id, str) and trace_id.strip():
                for tc_detail in _extract_tool_call_details_from_response(raw_msg_dict):
                    try:
                        builder = _get_instruction_builder_for_trace(trace_id)
                        if builder is not None:
                            builder.add_from_tool_call(
                                tool_name=tc_detail["tool_name"],
                                tool_call_id=tc_detail["tool_call_id"],
                                arguments=tc_detail.get("arguments") or {},
                                result=None,
                            )
                            _save_instructions_to_trace_file(trace_id, builder)
                    except Exception:
                        pass

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

        # Policy check: 剥完 category/topic 后，在回复 agent 前检查
        if isinstance(final_msg_dict, dict):
            metadata = data.get("metadata") if isinstance(data, dict) else {}
            trace_id = (
                metadata.get("arbiteros_trace_id")
                if isinstance(metadata, dict) else None
            )
            if isinstance(trace_id, str) and trace_id.strip():
                builder = _get_instruction_builder_for_trace(trace_id)
                instructions = list(getattr(builder, "instructions", [])) if builder else []
                latest_instructions = instructions[_policy_instruction_count_before:]
                policy_result = check_response_policy(
                    trace_id=trace_id,
                    instructions=instructions,
                    current_response=final_msg_dict,
                    latest_instructions=latest_instructions,
                )
                if policy_result.modified:
                    final_msg_dict = policy_result.response
                    error_type_str = policy_result.error_type or ""
                    if builder is not None:
                        # 用修改后的 response 重新生成 instructions 并替换
                        _replace_instructions_from_modified_response(
                            builder, final_msg_dict, _policy_instruction_count_before
                        )
                        if error_type_str:
                            # 本次 post_call_success 相关的每条 instruction 都加 policy_protected
                            for instr in builder.instructions[_policy_instruction_count_before:]:
                                instr["policy_protected"] = error_type_str
                        _save_instructions_to_trace_file(trace_id, builder)
                        # 若有 tool_calls，存 tool_call_id 供后续 tool result 时加 policy_protected
                        tool_calls = raw_msg_dict.get("tool_calls") if isinstance(raw_msg_dict, dict) else None
                        if isinstance(tool_calls, list):
                            by_trace = _policy_protected_tool_call_ids.setdefault(trace_id, {})
                            for tc in tool_calls:
                                if isinstance(tc, dict):
                                    tc_id = tc.get("id") or tc.get("tool_call_id")
                                    if isinstance(tc_id, str) and tc_id.strip():
                                        by_trace[tc_id] = error_type_str
                    try:
                        if is_chat_completion:
                            response.choices[0].message = Message(**final_msg_dict)
                        else:
                            if isinstance(final_msg_dict.get("content"), str) and hasattr(response, "output_text"):
                                setattr(response, "output_text", final_msg_dict.get("content"))
                    except Exception:
                        pass

        fallback_text = os.getenv(
            "ARBITEROS_EMPTY_ASSISTANT_FALLBACK",
            "抱歉，我这次没有生成有效回复，请重试。",
        )
        final_msg_dict = _ensure_non_empty_assistant_message(
            final_msg_dict, fallback_text=fallback_text
        )
        # If we injected fallback, keep the returned response object consistent.
        if (
            isinstance(final_msg_dict, dict)
            and isinstance(final_msg_dict.get("content"), str)
            and not (final_msg_dict.get("tool_calls") or final_msg_dict.get("function_call"))
        ):
            try:
                if is_chat_completion:
                    response.choices[0].message = Message(**final_msg_dict)
                else:
                    if hasattr(response, "output_text"):
                        setattr(response, "output_text", final_msg_dict.get("content"))
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
        if os.getenv("ARBITEROS_LITELLM_CALLBACK_DEBUG", "").strip() == "1":
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
        is_responses_input_request = _is_responses_api_request(request_data)
        # Transform logic is chat-completions oriented; Responses API streaming should pass through.
        apply_transform = response_transform is not None and not is_responses_input_request
        completed_response_obj: Optional[dict] = None
        responses_text_parts: list[str] = []

        async for chunk in response:
            chunk_dump: Optional[dict] = None
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
                    if event_type is None and isinstance(chunk_dump.get("type"), str):
                        event_type = chunk_dump.get("type")
            elif isinstance(chunk, dict):
                chunk_dump = chunk

            if event_type in {"response.completed", "response.failed"}:
                response_obj = chunk_dump.get("response") if isinstance(chunk_dump, dict) else None
                if isinstance(response_obj, dict):
                    completed_response_obj = response_obj

            if is_responses_input_request:
                part = _extract_stream_text_from_responses_chunk(chunk, chunk_dump)
                if part:
                    responses_text_parts.append(part)
            if isinstance(chunk, (ModelResponseStream, ModelResponse)) and not is_responses_input_request:
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

        if is_responses_input_request:
            completed_text = (
                _extract_text_from_responses_output(completed_response_obj)
                if isinstance(completed_response_obj, dict)
                else ""
            )
            if not completed_text and responses_text_parts:
                completed_text = "".join(responses_text_parts).strip()
            synthesized_response = {
                "content": completed_text if completed_text else None,
                "role": "assistant",
                "tool_calls": None,
                "function_call": None,
                "provider_specific_fields": {"format": "openai-responses-v1"},
                "annotations": [],
            }
            fallback_text = os.getenv(
                "ARBITEROS_EMPTY_ASSISTANT_FALLBACK",
                "抱歉，我这次没有生成有效回复，请重试。",
            )
            synthesized_response = _ensure_non_empty_assistant_message(
                synthesized_response, fallback_text=fallback_text
            )
            _save_json(
                "post_call_success",
                {
                    "response": synthesized_response,
                    "response_summary": {
                        "source": (
                            "responses.completed_event"
                            if isinstance(completed_response_obj, dict)
                            else "responses.stream_delta_fallback"
                        ),
                        "status": (
                            completed_response_obj.get("status")
                            if isinstance(completed_response_obj, dict)
                            else None
                        ),
                        "output_items": (
                            len(completed_response_obj.get("output"))
                            if isinstance(completed_response_obj, dict)
                            and isinstance(completed_response_obj.get("output"), list)
                            else None
                        ),
                    },
                },
            )
            _emit_response_nodes(
                request_data=request_data,
                response_before_transform=synthesized_response,
                response_after_transform=synthesized_response,
            )
            return

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

        # 记录 instruction 数量，供 policy 保护时标记本次添加的 instructions
        _policy_instruction_count_before_stream = 0
        if InstructionBuilder is not None:
            metadata = request_data.get("metadata") if isinstance(request_data, dict) else {}
            _policy_trace_id_stream = (
                metadata.get("arbiteros_trace_id")
                if isinstance(metadata, dict) else None
            )
            if isinstance(_policy_trace_id_stream, str) and _policy_trace_id_stream.strip():
                builder_pre = _get_instruction_builder_for_trace(_policy_trace_id_stream)
                _policy_instruction_count_before_stream = (
                    len(getattr(builder_pre, "instructions", [])) if builder_pre else 0
                )

        # instruction_parsing: 流式场景下同样在 post_call 时立即截获 tool_calls
        if msg_dict is not None and InstructionBuilder is not None:
            metadata = request_data.get("metadata") if isinstance(request_data, dict) else {}
            trace_id = (
                metadata.get("arbiteros_trace_id")
                if isinstance(metadata, dict)
                else None
            )
            if isinstance(trace_id, str) and trace_id.strip():
                for tc_detail in _extract_tool_call_details_from_response(msg_dict):
                    try:
                        builder = _get_instruction_builder_for_trace(trace_id)
                        if builder is not None:
                            builder.add_from_tool_call(
                                tool_name=tc_detail["tool_name"],
                                tool_call_id=tc_detail["tool_call_id"],
                                arguments=tc_detail.get("arguments") or {},
                                result=None,
                            )
                            _save_instructions_to_trace_file(trace_id, builder)
                    except Exception:
                        pass

        if apply_transform and msg_dict is not None:
            if asyncio.iscoroutinefunction(response_transform):
                modified_dict = await response_transform(request_data, msg_dict)
            else:
                modified_dict = response_transform(request_data, msg_dict)
            if modified_dict is not None and isinstance(modified_dict, dict):
                msg_dict = modified_dict

        # Policy check: 剥完 category/topic 后，在回复 agent 前检查
        if isinstance(msg_dict, dict):
            metadata = request_data.get("metadata") if isinstance(request_data, dict) else {}
            trace_id = (
                metadata.get("arbiteros_trace_id")
                if isinstance(metadata, dict) else None
            )
            if isinstance(trace_id, str) and trace_id.strip():
                builder = _get_instruction_builder_for_trace(trace_id)
                instructions = list(getattr(builder, "instructions", [])) if builder else []
                latest_instructions = instructions[_policy_instruction_count_before_stream:]
                policy_result = check_response_policy(
                    trace_id=trace_id,
                    instructions=instructions,
                    current_response=msg_dict,
                    latest_instructions=latest_instructions,
                )
                if policy_result.modified:
                    msg_dict = policy_result.response
                    error_type_str = policy_result.error_type or ""
                    if builder is not None:
                        _replace_instructions_from_modified_response(
                            builder, msg_dict, _policy_instruction_count_before_stream
                        )
                        if error_type_str:
                            for instr in builder.instructions[_policy_instruction_count_before_stream:]:
                                instr["policy_protected"] = error_type_str
                        _save_instructions_to_trace_file(trace_id, builder)
                        tool_calls = raw_msg_dict.get("tool_calls") if isinstance(raw_msg_dict, dict) else None
                        if isinstance(tool_calls, list):
                            by_trace = _policy_protected_tool_call_ids.setdefault(trace_id, {})
                            for tc in tool_calls:
                                if isinstance(tc, dict):
                                    tc_id = tc.get("id") or tc.get("tool_call_id")
                                    if isinstance(tc_id, str) and tc_id.strip():
                                        by_trace[tc_id] = error_type_str

        fallback_text = os.getenv(
            "ARBITEROS_EMPTY_ASSISTANT_FALLBACK",
            "抱歉，我这次没有生成有效回复，请重试。",
        )
        msg_dict = _ensure_non_empty_assistant_message(msg_dict, fallback_text=fallback_text)

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
