import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from langfuse import Langfuse


@dataclass
class KernelLogEntry:
    ts: Optional[str]
    hook: Optional[str]
    data: dict[str, Any]
    line_no: int


@dataclass
class ReplayCounters:
    total_lines: int = 0
    parsed_lines: int = 0
    malformed_lines: int = 0
    traces_created: int = 0
    emitted_nodes: int = 0
    input_nodes: int = 0
    tool_nodes: int = 0
    kernel_nodes: int = 0
    transform_nodes: int = 0
    output_nodes: int = 0
    failure_nodes: int = 0
    passthrough_nodes: int = 0
    paired_calls: int = 0
    orphan_pre_calls: int = 0
    orphan_post_calls: int = 0


@dataclass
class DeviceContext:
    device_key: str
    channel: str
    user_id: str
    latest_user_text: Optional[str]
    latest_user_fingerprint: Optional[str]
    reset_requested: bool


@dataclass
class TraceState:
    trace_id: str
    device_key: str
    channel: str
    user_id: str
    sequence: int = 0
    last_user_fingerprint: Optional[str] = None
    last_reset_fingerprint: Optional[str] = None


@dataclass
class PendingPreCall:
    entry: KernelLogEntry
    context: DeviceContext
    trace_state: TraceState
    model: Optional[str]


_CONVERSATION_LABEL_RE = re.compile(r'"conversation_label"\s*:\s*"([^"]+)"')
_CHANNEL_RE = re.compile(r'"channel"\s*:\s*"([^"]+)"')


def _read_jsonl(path: Path) -> tuple[list[KernelLogEntry], int, int]:
    entries: list[KernelLogEntry] = []
    malformed = 0
    total = 0

    with path.open("r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            total += 1
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue

            data = payload.get("data")
            if not isinstance(data, dict):
                data = {}
            entries.append(
                KernelLogEntry(
                    ts=payload.get("ts"),
                    hook=payload.get("hook"),
                    data=data,
                    line_no=line_no,
                )
            )
    return entries, malformed, total


def _extract_text_from_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
        return "\n".join(chunks)
    return ""


def _extract_latest_message_text(messages: list[Any], *, role: str) -> Optional[str]:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") != role:
            continue
        text = _extract_text_from_message_content(message.get("content"))
        if text.strip():
            return text
    return None


def _extract_first_message_text(messages: list[Any], *, role: str) -> Optional[str]:
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") != role:
            continue
        text = _extract_text_from_message_content(message.get("content"))
        if text.strip():
            return text
    return None


def _find_match_in_messages(messages: list[Any], pattern: re.Pattern[str]) -> Optional[str]:
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        text = _extract_text_from_message_content(message.get("content"))
        if not text:
            continue
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


def _normalize_fragment(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())[:256]


def _build_device_context(incoming: dict) -> DeviceContext:
    messages = incoming.get("messages")
    if not isinstance(messages, list):
        messages = []

    latest_user_text = _extract_latest_message_text(messages, role="user")
    latest_system_text = _extract_latest_message_text(messages, role="system")
    first_system_text = _extract_first_message_text(messages, role="system")

    channel_value = _find_match_in_messages(messages, _CHANNEL_RE)
    user_value = _find_match_in_messages(messages, _CONVERSATION_LABEL_RE)

    channel = _normalize_fragment(channel_value) if channel_value else "unknown-channel"
    raw_user_id = _normalize_fragment(user_value) if user_value else "unknown-user"
    if raw_user_id == "unknown-user":
        fallback_source = first_system_text or latest_system_text or "openclaw-unknown-user"
        fallback_hash = hashlib.sha256(
            fallback_source.encode("utf-8", errors="ignore")
        ).hexdigest()[:12]
        raw_user_id = f"anonymous-{fallback_hash}"

    latest_user_fingerprint = (
        hashlib.sha256(
            latest_user_text.encode("utf-8", errors="ignore")
        ).hexdigest()
        if latest_user_text
        else None
    )
    normalized_cmd = (latest_user_text or "").strip().lower()
    reset_requested = normalized_cmd in {"/new", "/reset"}

    return DeviceContext(
        device_key=f"{channel}:{raw_user_id}",
        channel=channel,
        user_id=raw_user_id,
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


def _ensure_trace_state(
    trace_state_by_device: dict[str, TraceState], context: DeviceContext
) -> tuple[TraceState, bool]:
    current = trace_state_by_device.get(context.device_key)
    rotate = False
    if current is None:
        rotate = True
    elif (
        context.reset_requested
        and context.latest_user_fingerprint
        and current.last_reset_fingerprint != context.latest_user_fingerprint
    ):
        rotate = True

    if rotate:
        current = TraceState(
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
        trace_state_by_device[context.device_key] = current
        return current, True

    if context.reset_requested and context.latest_user_fingerprint:
        current.last_reset_fingerprint = context.latest_user_fingerprint
    return current, False


def _next_sequence(state: TraceState) -> int:
    state.sequence += 1
    return state.sequence


def _safe_json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def _extract_tool_calls(response_dict: dict) -> list[dict]:
    tool_calls = response_dict.get("tool_calls")
    if not isinstance(tool_calls, list):
        return []
    out: list[dict] = []
    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            out.append(tool_call)
    return out


def _extract_structured_content(response_dict: dict) -> tuple[Optional[str], Optional[str]]:
    content = response_dict.get("content")
    parsed = _safe_json_loads(content)
    if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
        category = parsed.get("category")
        return (category if isinstance(category, str) else None, parsed.get("content"))
    return (None, content if isinstance(content, str) else None)


def _transform_response_content_only(response_dict: dict) -> dict:
    tool_calls = response_dict.get("tool_calls")
    if tool_calls:
        return dict(response_dict)
    content = response_dict.get("content")
    parsed = _safe_json_loads(content)
    if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
        return {**response_dict, "content": parsed.get("content")}
    return dict(response_dict)


def _increment_node_counter(counters: ReplayCounters, node_type: str) -> None:
    counters.emitted_nodes += 1
    if node_type == "input":
        counters.input_nodes += 1
    elif node_type == "tool_call":
        counters.tool_nodes += 1
    elif node_type == "kernel_step":
        counters.kernel_nodes += 1
    elif node_type == "transform":
        counters.transform_nodes += 1
    elif node_type == "output":
        counters.output_nodes += 1
    elif node_type == "failure":
        counters.failure_nodes += 1
    else:
        counters.passthrough_nodes += 1


def _emit_node(
    *,
    lf: Optional[Langfuse],
    dry_run: bool,
    counters: ReplayCounters,
    trace_state: TraceState,
    node_type: str,
    observation_type: str,
    name: str,
    input_payload: Any,
    output_payload: Any,
    metadata: dict[str, Any],
    model: Optional[str] = None,
) -> None:
    _increment_node_counter(counters, node_type)
    if dry_run or lf is None:
        return

    if observation_type == "generation":
        observation = lf.start_observation(
            trace_context={"trace_id": trace_state.trace_id},
            name=name,
            as_type="generation",
            input=input_payload,
            output=output_payload,
            metadata=metadata,
            model=model,
        )
    else:
        observation = lf.start_observation(
            trace_context={"trace_id": trace_state.trace_id},
            name=name,
            as_type=observation_type,
            input=input_payload,
            output=output_payload,
            metadata=metadata,
        )

    observation.update_trace(
        name=f"openclaw.session:{trace_state.device_key}",
        user_id=trace_state.user_id,
        session_id=trace_state.device_key,
        metadata={
            "source": "arbiteros_kernel_replay_v2",
            "channel": trace_state.channel,
            "device_key": trace_state.device_key,
        },
    )
    observation.end()


def _emit_input_node_if_needed(
    *,
    lf: Optional[Langfuse],
    dry_run: bool,
    counters: ReplayCounters,
    trace_state: TraceState,
    context: DeviceContext,
    pre_entry: KernelLogEntry,
) -> None:
    if not context.latest_user_text or not context.latest_user_fingerprint:
        return
    if trace_state.last_user_fingerprint == context.latest_user_fingerprint:
        return

    trace_state.last_user_fingerprint = context.latest_user_fingerprint
    _emit_node(
        lf=lf,
        dry_run=dry_run,
        counters=counters,
        trace_state=trace_state,
        node_type="input",
        observation_type="span",
        name="openclaw.input",
        input_payload={"text": context.latest_user_text},
        output_payload=None,
        metadata={
            "source": "arbiteros_kernel_replay_v2",
            "line": pre_entry.line_no,
            "ts": pre_entry.ts,
            "node_sequence": _next_sequence(trace_state),
            "reset_requested": context.reset_requested,
            "text_preview": context.latest_user_text[:300],
        },
    )


def _emit_response_nodes_from_pair(
    *,
    lf: Optional[Langfuse],
    dry_run: bool,
    counters: ReplayCounters,
    pre: PendingPreCall,
    post_entry: KernelLogEntry,
) -> None:
    response = post_entry.data.get("response")
    if not isinstance(response, dict):
        response = {}
    transformed = _transform_response_content_only(response)

    tool_calls = _extract_tool_calls(response)
    if tool_calls:
        for tool_call in tool_calls:
            fn = tool_call.get("function") if isinstance(tool_call, dict) else None
            tool_name = (
                fn.get("name")
                if isinstance(fn, dict) and isinstance(fn.get("name"), str)
                else "unknown_tool"
            )
            arguments = fn.get("arguments") if isinstance(fn, dict) else None
            _emit_node(
                lf=lf,
                dry_run=dry_run,
                counters=counters,
                trace_state=pre.trace_state,
                node_type="tool_call",
                observation_type="tool",
                name=f"tool.{tool_name}",
                input_payload={"arguments": arguments},
                output_payload=None,
                metadata={
                    "source": "arbiteros_kernel_replay_v2",
                    "line": post_entry.line_no,
                    "ts": post_entry.ts,
                    "node_sequence": _next_sequence(pre.trace_state),
                    "tool_name": tool_name,
                    "tool_call_id": tool_call.get("id"),
                },
            )
        return

    category, raw_structured_content = _extract_structured_content(response)
    transformed_content = (
        transformed.get("content")
        if isinstance(transformed.get("content"), str)
        else raw_structured_content
    )

    if category and category != "EXECUTION_CORE__RESPOND":
        _emit_node(
            lf=lf,
            dry_run=dry_run,
            counters=counters,
            trace_state=pre.trace_state,
            node_type="kernel_step",
            observation_type="agent",
            name=f"kernel.{category.lower()}",
            input_payload={"content": raw_structured_content},
            output_payload=None,
            metadata={
                "source": "arbiteros_kernel_replay_v2",
                "line": post_entry.line_no,
                "ts": post_entry.ts,
                "node_sequence": _next_sequence(pre.trace_state),
                "category": category,
            },
        )

    raw_content = response.get("content")
    transformed_output = transformed.get("content")
    if raw_content != transformed_output:
        _emit_node(
            lf=lf,
            dry_run=dry_run,
            counters=counters,
            trace_state=pre.trace_state,
            node_type="transform",
            observation_type="span",
            name="kernel.transform.response_content",
            input_payload={"before": raw_content},
            output_payload={"after": transformed_output},
            metadata={
                "source": "arbiteros_kernel_replay_v2",
                "line": post_entry.line_no,
                "ts": post_entry.ts,
                "node_sequence": _next_sequence(pre.trace_state),
                "transform": "strip_category_wrapper",
            },
        )

    _emit_node(
        lf=lf,
        dry_run=dry_run,
        counters=counters,
        trace_state=pre.trace_state,
        node_type="output",
        observation_type="generation",
        name="openclaw.output",
        input_payload=None,
        output_payload={"content": transformed_content, "category": category},
        metadata={
            "source": "arbiteros_kernel_replay_v2",
            "line": post_entry.line_no,
            "ts": post_entry.ts,
            "node_sequence": _next_sequence(pre.trace_state),
            "category": category,
            "pre_line": pre.entry.line_no,
            "pre_ts": pre.entry.ts,
        },
        model=pre.model,
    )


def _emit_normalized_langfuse_node(
    *,
    lf: Optional[Langfuse],
    dry_run: bool,
    counters: ReplayCounters,
    entry: KernelLogEntry,
) -> None:
    data = entry.data if isinstance(entry.data, dict) else {}
    trace_id = data.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id:
        trace_id = Langfuse.create_trace_id(seed=f"line:{entry.line_no}")

    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    channel = metadata.get("channel")
    user_id = metadata.get("user_id")
    device_key = metadata.get("device_key")
    trace_state = TraceState(
        trace_id=trace_id,
        channel=channel if isinstance(channel, str) else "unknown-channel",
        user_id=user_id if isinstance(user_id, str) else "unknown-user",
        device_key=device_key if isinstance(device_key, str) else "unknown-channel:unknown-user",
    )

    node_type = data.get("node_type")
    if not isinstance(node_type, str):
        node_type = "passthrough"
    observation_type = data.get("observation_type")
    if not isinstance(observation_type, str):
        observation_type = "span"
    name = data.get("name")
    if not isinstance(name, str):
        name = "openclaw.node"
    model = data.get("model")

    merged_metadata = {
        "source": "arbiteros_kernel_replay_v2",
        "line": entry.line_no,
        "ts": entry.ts,
        **metadata,
    }
    _emit_node(
        lf=lf,
        dry_run=dry_run,
        counters=counters,
        trace_state=trace_state,
        node_type=node_type,
        observation_type=observation_type,
        name=name,
        input_payload=data.get("input"),
        output_payload=data.get("output"),
        metadata=merged_metadata,
        model=model if isinstance(model, str) else None,
    )


def _build_langfuse_client() -> Langfuse:
    timeout = int(os.getenv("ARBITEROS_LANGFUSE_TIMEOUT", os.getenv("LANGFUSE_TIMEOUT", "15")))
    flush_at = int(os.getenv("ARBITEROS_LANGFUSE_FLUSH_AT", "1"))
    flush_interval = float(os.getenv("ARBITEROS_LANGFUSE_FLUSH_INTERVAL", "1"))
    return Langfuse(timeout=timeout, flush_at=flush_at, flush_interval=flush_interval)


def replay_jsonl_to_langfuse(input_path: Path, dry_run: bool = False) -> ReplayCounters:
    entries, malformed, total_lines = _read_jsonl(input_path)
    counters = ReplayCounters(
        total_lines=total_lines,
        parsed_lines=len(entries),
        malformed_lines=malformed,
    )

    if not dry_run and (not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY")):
        raise RuntimeError(
            "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set for replay."
        )

    lf = None if dry_run else _build_langfuse_client()
    pending_pre: Optional[PendingPreCall] = None
    trace_state_by_device: dict[str, TraceState] = {}

    for entry in entries:
        if entry.hook == "langfuse_node":
            _emit_normalized_langfuse_node(
                lf=lf,
                dry_run=dry_run,
                counters=counters,
                entry=entry,
            )
            continue

        if entry.hook == "pre_call":
            incoming = entry.data.get("incoming")
            if not isinstance(incoming, dict):
                incoming = {}
            context = _build_device_context(incoming)
            trace_state, created = _ensure_trace_state(trace_state_by_device, context)
            if created:
                counters.traces_created += 1

            if pending_pre is not None:
                counters.orphan_pre_calls += 1

            _emit_input_node_if_needed(
                lf=lf,
                dry_run=dry_run,
                counters=counters,
                trace_state=trace_state,
                context=context,
                pre_entry=entry,
            )
            model = incoming.get("model")
            pending_pre = PendingPreCall(
                entry=entry,
                context=context,
                trace_state=trace_state,
                model=model if isinstance(model, str) else None,
            )
            continue

        if entry.hook == "post_call_failure":
            if pending_pre is None:
                counters.orphan_post_calls += 1
                continue
            _emit_node(
                lf=lf,
                dry_run=dry_run,
                counters=counters,
                trace_state=pending_pre.trace_state,
                node_type="failure",
                observation_type="span",
                name="openclaw.failure",
                input_payload=None,
                output_payload=entry.data,
                metadata={
                    "source": "arbiteros_kernel_replay_v2",
                    "line": entry.line_no,
                    "ts": entry.ts,
                    "node_sequence": _next_sequence(pending_pre.trace_state),
                },
            )
            pending_pre = None
            continue

        if entry.hook != "post_call_success":
            continue

        if pending_pre is None:
            counters.orphan_post_calls += 1
            continue

        counters.paired_calls += 1
        _emit_response_nodes_from_pair(
            lf=lf,
            dry_run=dry_run,
            counters=counters,
            pre=pending_pre,
            post_entry=entry,
        )
        pending_pre = None

    if pending_pre is not None:
        counters.orphan_pre_calls += 1

    if lf is not None:
        lf.flush()
    return counters


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay ArbiterOS-Kernel logs into Langfuse grouped by session trace."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("log/api_calls.jsonl"),
        help="Path to ArbiterOS-Kernel jsonl log (api_calls.jsonl or langfuse_nodes.jsonl).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and build node plan without sending data to Langfuse.",
    )
    return parser


def main() -> None:
    # Auto-load local env file (e.g. LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST)
    # so users don't need to `export` them manually before running replay.
    load_dotenv(override=False)

    args = _build_arg_parser().parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Input file not found: {args.input}")

    counters = replay_jsonl_to_langfuse(args.input, dry_run=args.dry_run)
    print(
        json.dumps(
            {
                "input": str(args.input),
                "dry_run": args.dry_run,
                "total_lines": counters.total_lines,
                "parsed_lines": counters.parsed_lines,
                "malformed_lines": counters.malformed_lines,
                "traces_created": counters.traces_created,
                "emitted_nodes": counters.emitted_nodes,
                "input_nodes": counters.input_nodes,
                "tool_nodes": counters.tool_nodes,
                "kernel_nodes": counters.kernel_nodes,
                "transform_nodes": counters.transform_nodes,
                "output_nodes": counters.output_nodes,
                "failure_nodes": counters.failure_nodes,
                "passthrough_nodes": counters.passthrough_nodes,
                "paired_calls": counters.paired_calls,
                "orphan_pre_calls": counters.orphan_pre_calls,
                "orphan_post_calls": counters.orphan_post_calls,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
