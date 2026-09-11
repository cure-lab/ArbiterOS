from __future__ import annotations

from typing import Any, Literal

from .config import ContentScanConfig
from .engine import ScanResult, is_skipped_context_text, scan_text


def _scan(
    text: str,
    cfg: ContentScanConfig,
    *,
    side: Literal["input", "output"],
    trace_id: str,
    skip_control: bool = True,
) -> ScanResult:
    return scan_text(
        text,
        cfg,
        side=side,
        trace_id=trace_id,
        skip_control=skip_control,
    )


def rewrite_content_field(
    content: Any,
    cfg: ContentScanConfig,
    *,
    side: Literal["input", "output"],
    trace_id: str,
    skip_control: bool = True,
) -> tuple[Any, bool, list[dict[str, Any]]]:
    if isinstance(content, str):
        result = _scan(content, cfg, side=side, trace_id=trace_id, skip_control=skip_control)
        return result.text, result.changed, list(result.hits)
    if isinstance(content, list):
        out: list[Any] = []
        changed = False
        hits: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, str):
                result = _scan(part, cfg, side=side, trace_id=trace_id, skip_control=skip_control)
                out.append(result.text)
                changed = changed or result.changed
                hits.extend(result.hits)
                continue
            if isinstance(part, dict):
                next_part = dict(part)
                part_changed = False
                for key in ("text", "content"):
                    value = next_part.get(key)
                    if not isinstance(value, str):
                        continue
                    result = _scan(
                        value, cfg, side=side, trace_id=trace_id, skip_control=skip_control
                    )
                    if result.changed:
                        next_part[key] = result.text
                        part_changed = True
                        hits.extend(result.hits)
                out.append(next_part)
                changed = changed or part_changed
                continue
            out.append(part)
        return out, changed, hits
    return content, False, []


def rewrite_json_value(
    value: Any,
    cfg: ContentScanConfig,
    *,
    side: Literal["input", "output"],
    trace_id: str,
    skip_control: bool = True,
) -> tuple[Any, bool, list[dict[str, Any]]]:
    if isinstance(value, str):
        result = _scan(value, cfg, side=side, trace_id=trace_id, skip_control=skip_control)
        return result.text, result.changed, list(result.hits)
    if isinstance(value, list):
        out: list[Any] = []
        changed = False
        hits: list[dict[str, Any]] = []
        for item in value:
            new_item, item_changed, item_hits = rewrite_json_value(
                item, cfg, side=side, trace_id=trace_id, skip_control=skip_control
            )
            out.append(new_item)
            changed = changed or item_changed
            hits.extend(item_hits)
        return out, changed, hits
    if isinstance(value, dict):
        out_dict: dict[str, Any] = {}
        changed = False
        hits: list[dict[str, Any]] = []
        for key, item in value.items():
            new_item, item_changed, item_hits = rewrite_json_value(
                item, cfg, side=side, trace_id=trace_id, skip_control=skip_control
            )
            out_dict[key] = new_item
            changed = changed or item_changed
            hits.extend(item_hits)
        return out_dict, changed, hits
    return value, False, []


def _rewrite_tool_calls(
    tool_calls: Any,
    cfg: ContentScanConfig,
    *,
    side: Literal["input", "output"],
    trace_id: str,
    skip_control: bool = True,
) -> tuple[Any, bool, list[dict[str, Any]]]:
    if not isinstance(tool_calls, list):
        return tool_calls, False, []
    from arbiteros_kernel.policy_runtime import RUNTIME

    out: list[Any] = []
    changed = False
    hits: list[dict[str, Any]] = []
    for raw_tc in tool_calls:
        if not isinstance(raw_tc, dict):
            out.append(raw_tc)
            continue
        _name, _tid, args, was_json = RUNTIME.parse_tool_call(raw_tc)
        new_args, args_changed, args_hits = rewrite_json_value(
            args, cfg, side=side, trace_id=trace_id, skip_control=skip_control
        )
        next_tc = raw_tc
        if args_changed and isinstance(new_args, dict):
            next_tc = RUNTIME.write_back_tool_args(raw_tc, new_args, was_json)
            changed = True
            hits.extend(args_hits)
        out.append(next_tc)
    return out, changed, hits


def redact_message(
    message: dict[str, Any],
    cfg: ContentScanConfig,
    *,
    side: Literal["input", "output"],
    trace_id: str,
    skip_control: bool = True,
) -> tuple[dict[str, Any], bool, list[dict[str, Any]]]:
    if not isinstance(message, dict):
        return message, False, []
    content = message.get("content")
    if skip_control and isinstance(content, str) and is_skipped_context_text(content):
        return message, False, []

    out = dict(message)
    changed = False
    hits: list[dict[str, Any]] = []

    new_content, content_changed, content_hits = rewrite_content_field(
        content, cfg, side=side, trace_id=trace_id, skip_control=skip_control
    )
    if content_changed:
        out["content"] = new_content
        changed = True
        hits.extend(content_hits)

    if "tool_calls" in out:
        new_tcs, tc_changed, tc_hits = _rewrite_tool_calls(
            out.get("tool_calls"),
            cfg,
            side=side,
            trace_id=trace_id,
            skip_control=skip_control,
        )
        if tc_changed:
            out["tool_calls"] = new_tcs
            changed = True
            hits.extend(tc_hits)

    fn_call = out.get("function_call")
    if isinstance(fn_call, dict) and isinstance(fn_call.get("arguments"), str):
        result = _scan(
            fn_call["arguments"],
            cfg,
            side=side,
            trace_id=trace_id,
            skip_control=skip_control,
        )
        if result.changed:
            next_fn = dict(fn_call)
            next_fn["arguments"] = result.text
            out["function_call"] = next_fn
            changed = True
            hits.extend(result.hits)

    return out, changed, hits


def _rewrite_item_field(
    out: dict[str, Any],
    key: str,
    cfg: ContentScanConfig,
    *,
    trace_id: str,
    skip_control: bool,
) -> tuple[bool, list[dict[str, Any]]]:
    value = out.get(key)
    if isinstance(value, (str, list)):
        new_value, changed, hits = rewrite_content_field(
            value, cfg, side="input", trace_id=trace_id, skip_control=skip_control
        )
        if changed:
            out[key] = new_value
        return changed, hits
    if isinstance(value, dict):
        new_value, changed, hits = rewrite_json_value(
            value, cfg, side="input", trace_id=trace_id, skip_control=skip_control
        )
        if changed:
            out[key] = new_value
        return changed, hits
    return False, []


def _rewrite_responses_input_item(
    item: Any,
    cfg: ContentScanConfig,
    *,
    trace_id: str,
    skip_control: bool,
) -> tuple[Any, bool, list[dict[str, Any]]]:
    if not isinstance(item, dict):
        return item, False, []
    out = dict(item)
    changed = False
    hits: list[dict[str, Any]] = []
    for key in ("content", "output", "arguments"):
        field_changed, field_hits = _rewrite_item_field(
            out,
            key,
            cfg,
            trace_id=trace_id,
            skip_control=skip_control,
        )
        if field_changed:
            changed = True
            hits.extend(field_hits)
    return out, changed, hits


def redact_request(
    request: dict[str, Any],
    cfg: ContentScanConfig,
    *,
    trace_id: str,
) -> tuple[dict[str, Any], bool, list[dict[str, Any]]]:
    if not isinstance(request, dict):
        return request, False, []
    out = dict(request)
    changed = False
    hits: list[dict[str, Any]] = []

    messages = out.get("messages")
    if isinstance(messages, list):
        new_messages: list[Any] = []
        for msg in messages:
            if isinstance(msg, dict):
                new_msg, msg_changed, msg_hits = redact_message(
                    msg, cfg, side="input", trace_id=trace_id, skip_control=False
                )
                new_messages.append(new_msg)
                changed = changed or msg_changed
                hits.extend(msg_hits)
            else:
                new_messages.append(msg)
        if changed:
            out["messages"] = new_messages

    if isinstance(out.get("instructions"), str):
        result = _scan(
            out["instructions"], cfg, side="input", trace_id=trace_id, skip_control=False
        )
        if result.changed:
            out["instructions"] = result.text
            changed = True
            hits.extend(result.hits)

    if isinstance(out.get("system"), (str, list)):
        new_system, sys_changed, sys_hits = rewrite_content_field(
            out.get("system"), cfg, side="input", trace_id=trace_id, skip_control=False
        )
        if sys_changed:
            out["system"] = new_system
            changed = True
            hits.extend(sys_hits)

    raw_input = out.get("input")
    if isinstance(raw_input, list):
        new_input: list[Any] = []
        input_changed = False
        for item in raw_input:
            new_item, item_changed, item_hits = _rewrite_responses_input_item(
                item,
                cfg,
                trace_id=trace_id,
                skip_control=False,
            )
            new_input.append(new_item)
            input_changed = input_changed or item_changed
            hits.extend(item_hits)
        if input_changed:
            out["input"] = new_input
            changed = True

    return out, changed, hits


def redact_response(
    response: dict[str, Any],
    cfg: ContentScanConfig,
    *,
    trace_id: str,
) -> tuple[dict[str, Any], bool, list[dict[str, Any]]]:
    if not isinstance(response, dict):
        return response, False, []
    return redact_message(response, cfg, side="output", trace_id=trace_id)
