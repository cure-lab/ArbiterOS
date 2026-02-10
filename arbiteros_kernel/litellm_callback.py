import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Optional, Union

import litellm
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

# 剥去 assistant content 时记录的 category，只追加不因「包」而消耗；超 1000 时删最前（最早）的
_stripped_categories: list[str] = []
_MAX_STRIPPED_CATEGORIES = 1000


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
        filtered_data = {
            k: data[k] for k in ["model", "messages", "tools"] if k in data
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

    async def async_post_call_success_hook(
        self,
        data: dict,
        user_api_key_dict: UserAPIKeyAuth,
        response: LLMResponseTypes,
    ) -> Any:
        # data is the original request data
        # response is the response from the LLM API
        msg = response.choices[0].message if response.choices else None
        _console.print(
            Panel(
                Pretty(msg),
                title="Post Call Success Hook - Response",
            )
        )
        _save_json("post_call_success", {"response": msg})

        # 若配置了 response_transform，用其返回值改写返回给调用方的内容
        if msg is not None and response_transform is not None:
            msg_dict = (
                _to_json(msg)
                if isinstance(msg, dict)
                else (msg.model_dump() if hasattr(msg, "model_dump") else (msg.dict() if hasattr(msg, "dict") else None))
            )
            if msg_dict is not None:
                if asyncio.iscoroutinefunction(response_transform):
                    modified_dict = await response_transform(data, msg_dict)
                else:
                    modified_dict = response_transform(data, msg_dict)
                if modified_dict is not None and isinstance(modified_dict, dict):
                    try:
                        response.choices[0].message = Message(**modified_dict)
                    except Exception:
                        pass
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

        # 先存 modify 之前的版本（带 category/content 的原始结构）
        _save_json("post_call_success", {"response": msg_dict})

        if apply_transform and msg_dict is not None:
            if asyncio.iscoroutinefunction(response_transform):
                modified_dict = await response_transform(request_data, msg_dict)
            else:
                modified_dict = response_transform(request_data, msg_dict)
            if modified_dict is not None and isinstance(modified_dict, dict):
                msg_dict = modified_dict

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
