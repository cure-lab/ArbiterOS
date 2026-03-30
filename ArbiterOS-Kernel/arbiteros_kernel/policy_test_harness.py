"""
Offline policy replay: prior steps + current message -> instructions -> policy check.
Spec: prior[] only assistant | tool (response-shaped); instructions come from parsing.
Optional per-step ``tag`` (object) strips before parse, then deep-merges onto the last
instruction produced by that step (e.g. policy_confirmation_ask, policy_protected,
user_approved). Always runs apply_user_approval_preprocessing before check_response_policy.
"""

from __future__ import annotations

import argparse
import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from arbiteros_kernel.instruction_parsing.builder import InstructionBuilder
from arbiteros_kernel.litellm_callback import (
    _extract_tool_call_details_from_response,
    _ensure_reference_tool_id_in_arguments,
    _instruction_builders_by_trace,
    _instruction_builders_lock,
    _response_transform_content_only,
)
from arbiteros_kernel.policy_check import PolicyCheckResult, check_response_policy
from arbiteros_kernel.user_approval import apply_user_approval_preprocessing


def _parse_tool_arguments_json(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _validate_assistant_text_content_cognitive_fields(
    msg: Dict[str, Any],
    *,
    where: str,
) -> None:
    """Pure-text assistant: content must be JSON with category, topic, content."""
    tcs = msg.get("tool_calls")
    if isinstance(tcs, list) and len(tcs) > 0:
        return
    c = msg.get("content")
    if not isinstance(c, str) or not c.strip():
        raise ValueError(
            f"{where}: 纯文本 assistant 须有非空 content，且为 JSON 字符串，"
            '内含 "category"、"topic"、"content" 三项'
        )
    try:
        parsed = json.loads(c)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"{where}: 纯文本 assistant 的 content 须为合法 JSON 字符串（含 category、topic、content）"
        ) from e
    if not isinstance(parsed, dict):
        raise ValueError(f"{where}: content 解析后须为 JSON object")
    for key in ("category", "topic", "content"):
        if key not in parsed:
            raise ValueError(
                f'{where}: content 解析后的 JSON 必须包含 "{key}" 键'
            )


def _validate_assistant_message_not_mixed_text_and_tools(
    msg: Dict[str, Any],
    *,
    where: str,
) -> None:
    """Assistant message should be either text (content) or tool_calls, not both."""
    tcs = msg.get("tool_calls")
    has_tc = isinstance(tcs, list) and len(tcs) > 0
    c = msg.get("content")
    if isinstance(c, str):
        has_text = bool(c.strip())
    else:
        has_text = c is not None
    if has_tc and has_text:
        raise ValueError(
            f"{where}: assistant 请二选一——仅 `content`（纯文本）或仅 `tool_calls`（纯工具），"
            "不要同条混写；需要拆成两条 assistant"
        )


def _validate_assistant_message_tool_calls_reference_ids(
    msg: Dict[str, Any],
    *,
    where: str,
) -> None:
    """Each tool call in this assistant message must include reference_tool_id in arguments."""
    tcs = msg.get("tool_calls")
    if not isinstance(tcs, list) or not tcs:
        return
    for idx, tc in enumerate(tcs):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if not isinstance(fn, dict):
            continue
        args = _parse_tool_arguments_json(fn.get("arguments"))
        if args is None:
            raise ValueError(
                f'{where}: tool_calls[{idx}] 的 function.arguments 须为 JSON 对象，'
                '且含 "reference_tool_id"（无 tool call 的 response 不写 tool_calls 即可）'
            )
        if "reference_tool_id" not in args:
            raise ValueError(
                f'{where}: tool_calls[{idx}] 的 arguments 必须包含 "reference_tool_id"（无上游写 []）'
            )
        if not isinstance(args["reference_tool_id"], list):
            raise ValueError(
                f'{where}: tool_calls[{idx}] 的 reference_tool_id 须为数组'
            )


def _validate_current_tag_no_instruction_flags(tag: Dict[str, Any]) -> None:
    """current 的 tag 里不得出现 user_approved / policy_confirmation_ask / policy_protected。"""
    forbidden = frozenset(
        ("user_approved", "policy_confirmation_ask", "policy_protected")
    )
    bad = sorted(forbidden & tag.keys())
    if bad:
        raise ValueError(
            f"current 的 tag 不得包含 {bad}；这三项只能写在 prior 里对应步的 tag"
        )


def _validate_tool_step_arguments_dict(arguments: Dict[str, Any], *, where: str) -> None:
    if "reference_tool_id" not in arguments:
        raise ValueError(
            f'{where}: arguments 必须包含 "reference_tool_id"（无上游写 []）；'
            "无 tool call 时不要写 kind: tool 步骤"
        )
    if not isinstance(arguments["reference_tool_id"], list):
        raise ValueError(f"{where}: reference_tool_id 须为数组")


def _validate_policy_confirmation_ask_chain(instructions: List[Dict[str, Any]]) -> None:
    """
    Real order: first an instruction with policy_confirmation_ask (the ask);
    the immediately following instruction must be either user_approved or
    policy_protected (not on the same row as the ask).
    """
    n = len(instructions)
    if n == 0:
        return
    for i, instr in enumerate(instructions):
        if not instr.get("policy_confirmation_ask"):
            continue
        if instr.get("user_approved"):
            raise ValueError(
                f"instruction[{i}]：带 policy_confirmation_ask 的这条不能同时带 user_approved；"
                "放行应写在**下一条** instruction"
            )
        pp_ask = instr.get("policy_protected")
        if isinstance(pp_ask, str) and pp_ask.strip():
            raise ValueError(
                f"instruction[{i}]：带 policy_confirmation_ask 的这条不能同时带 policy_protected；"
                "保护结果应写在**下一条** instruction"
            )
    if instructions[-1].get("policy_confirmation_ask"):
        raise ValueError(
            "最后一条 instruction 不能单独带 policy_confirmation_ask："
            "其后须再有一条 instruction，且该条须为 user_approved（true）或 policy_protected（非空）"
        )
    for i in range(n - 1):
        if not instructions[i].get("policy_confirmation_ask"):
            continue
        nxt = instructions[i + 1]
        if nxt.get("user_approved"):
            continue
        pp = nxt.get("policy_protected")
        if isinstance(pp, str) and pp.strip():
            continue
        raise ValueError(
            f"instruction[{i}] 为 policy_confirmation_ask 问句后，instruction[{i + 1}] "
            "必须是 user_approved（true）或非空 policy_protected，二者择一"
        )


def _bind_builder_to_trace(trace_id: str, builder: InstructionBuilder) -> None:
    with _instruction_builders_lock:
        _instruction_builders_by_trace[trace_id] = builder


def _unbind_builder(trace_id: str) -> None:
    with _instruction_builders_lock:
        _instruction_builders_by_trace.pop(trace_id, None)


def _deep_merge_dict(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict):
            _deep_merge_dict(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)


def _merge_into_instruction(instr: Dict[str, Any], merge: Dict[str, Any]) -> None:
    _deep_merge_dict(instr, merge)


def _apply_tag_to_step_instructions(
    builder: InstructionBuilder,
    count_before: int,
    tag: Any,
) -> None:
    """Merge ``tag`` onto the last instruction created in this step (between count_before and now)."""
    if not isinstance(tag, dict) or not tag:
        return
    insts = builder.instructions
    new = insts[count_before:]
    if not new:
        return
    _merge_into_instruction(new[-1], tag)


def _strip_tag_from_assistant_dict(
    d: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Remove harness-only ``tag``; rest is passed to the parser."""
    if "tag" not in d:
        raise ValueError(
            '必须包含 "tag" 对象（无标记时写 {}），与 role / content / tool_calls 同级'
        )
    msg = copy.deepcopy(d)
    raw = msg.pop("tag")
    if not isinstance(raw, dict):
        raise ValueError('"tag" 必须是 object')
    return msg, raw


def _add_tool_calls_from_message(
    builder: InstructionBuilder,
    trace_id: str,
    assistant_msg: Dict[str, Any],
) -> None:
    for tc_detail in _extract_tool_call_details_from_response(assistant_msg):
        args = tc_detail.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        args = _ensure_reference_tool_id_in_arguments(
            args,
            tc_detail.get("tool_call_id"),
            trace_id,
        )
        builder.add_from_tool_call(
            tool_name=tc_detail["tool_name"],
            tool_call_id=tc_detail["tool_call_id"],
            arguments=args,
            result=None,
        )


def _apply_response_transform(
    trace_id: str,
    assistant_msg: Dict[str, Any],
) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "metadata": {"arbiteros_trace_id": trace_id},
    }
    msg = copy.deepcopy(assistant_msg)
    if msg.get("role") is None:
        msg["role"] = "assistant"
    out = _response_transform_content_only(data, msg)
    return out if isinstance(out, dict) else msg


def append_one_assistant_turn(
    builder: InstructionBuilder,
    trace_id: str,
    assistant_msg: Dict[str, Any],
) -> Dict[str, Any]:
    _add_tool_calls_from_message(builder, trace_id, assistant_msg)
    return _apply_response_transform(trace_id, assistant_msg)


def append_tool_result_turn(
    builder: InstructionBuilder,
    trace_id: str,
    *,
    tool_name: str,
    tool_call_id: str,
    arguments: Dict[str, Any],
    content: str,
) -> None:
    args = dict(arguments) if isinstance(arguments, dict) else {}
    args = _ensure_reference_tool_id_in_arguments(
        args,
        tool_call_id,
        trace_id,
    )
    builder.add_from_tool_call(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        arguments=args,
        result={"raw": content},
    )


def _parse_prior_step(step: Any) -> Dict[str, Any]:
    if not isinstance(step, dict):
        raise ValueError(f"prior 每一项必须是 object，得到 {type(step)}")
    kind = step.get("kind")
    if kind not in ("assistant", "tool"):
        raise ValueError(
            f'prior 每一项必须有 "kind": "assistant" | "tool"，得到 {kind!r}'
        )
    if kind == "assistant":
        msg = step.get("message")
        if not isinstance(msg, dict):
            raise ValueError('kind 为 "assistant" 时必须提供 object "message"')
        assistant_msg, tag = _strip_tag_from_assistant_dict(msg)
        _validate_assistant_message_not_mixed_text_and_tools(
            assistant_msg,
            where="prior 中 kind=assistant 的 message",
        )
        _validate_assistant_text_content_cognitive_fields(
            assistant_msg,
            where="prior 中 kind=assistant 的 message",
        )
        _validate_assistant_message_tool_calls_reference_ids(
            assistant_msg,
            where="prior 中 kind=assistant 的 message",
        )
        return {
            "kind": "assistant",
            "assistant": assistant_msg,
            "tag": tag,
        }
    if "tag" not in step:
        raise ValueError('kind 为 "tool" 时必须包含 "tag"（无标记时写 {}）')
    raw_tag = step["tag"]
    if not isinstance(raw_tag, dict):
        raise ValueError('"tag" 必须是 object')
    args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
    _validate_tool_step_arguments_dict(args, where="prior 中 kind=tool")
    if "result" in step:
        tool_result = step["result"]
    elif "content" in step:
        tool_result = step["content"]
    else:
        raise ValueError(
            'kind 为 "tool" 时必须包含 "result"（工具执行返回体；不要用 content，以免与 assistant 文本混淆）'
        )
    if not isinstance(tool_result, str):
        tool_result = str(tool_result) if tool_result is not None else ""
    return {
        "kind": "tool",
        "tool_call_id": str(step.get("tool_call_id") or ""),
        "tool_name": str(step.get("tool_name") or "unknown_tool"),
        "arguments": args,
        "result_body": tool_result,
        "tag": raw_tag,
    }


def _parse_current(spec: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    cur = spec.get("current")
    if not isinstance(cur, dict):
        raise ValueError('根上必须有 object "current"（一条 assistant 响应消息）')
    msg, tag = _strip_tag_from_assistant_dict(cur)
    _validate_current_tag_no_instruction_flags(tag)
    _validate_assistant_message_not_mixed_text_and_tools(msg, where="current")
    _validate_assistant_text_content_cognitive_fields(msg, where="current")
    _validate_assistant_message_tool_calls_reference_ids(msg, where="current")
    return msg, tag


@dataclass
class PolicyReplayOutcome:
    trace_id: str
    policy_result: PolicyCheckResult
    instructions: List[Dict[str, Any]]
    latest_instructions: List[Dict[str, Any]]
    instructions_for_policy: List[Dict[str, Any]]
    latest_instructions_for_policy: List[Dict[str, Any]]
    current_response_for_policy: Dict[str, Any]


def run_policy_replay_from_spec(spec: Dict[str, Any]) -> PolicyReplayOutcome:
    trace_id = (spec.get("trace_id") or "policy-test-trace").strip()
    prior = spec.get("prior") or []
    if not isinstance(prior, list):
        raise ValueError('"prior" 必须是数组')
    current_msg, current_tag = _parse_current(spec)

    builder = InstructionBuilder(trace_id=trace_id)
    _bind_builder_to_trace(trace_id, builder)
    try:
        outcome = _run_policy_replay_inner(
            trace_id, prior, current_msg, current_tag, builder
        )
    finally:
        _unbind_builder(trace_id)
    return outcome


def _run_policy_replay_inner(
    trace_id: str,
    prior: List[Any],
    current: Dict[str, Any],
    current_tag: Dict[str, Any],
    builder: InstructionBuilder,
) -> PolicyReplayOutcome:
    for step in prior:
        normalized = _parse_prior_step(step)
        count_before = len(builder.instructions)
        if normalized["kind"] == "assistant":
            append_one_assistant_turn(builder, trace_id, normalized["assistant"])
        else:
            append_tool_result_turn(
                builder,
                trace_id,
                tool_name=normalized["tool_name"],
                tool_call_id=normalized["tool_call_id"],
                arguments=normalized["arguments"],
                content=normalized["result_body"],
            )
        _apply_tag_to_step_instructions(
            builder, count_before, normalized.get("tag")
        )

    count_before = len(builder.instructions)
    final_current = append_one_assistant_turn(builder, trace_id, current)
    _apply_tag_to_step_instructions(builder, count_before, current_tag)

    instructions = list(builder.instructions)
    _validate_policy_confirmation_ask_chain(instructions)

    latest_instructions = instructions[count_before:]

    instructions_for_policy, latest_for_policy = apply_user_approval_preprocessing(
        instructions=instructions,
        latest_instructions=latest_instructions,
    )

    policy_result = check_response_policy(
        trace_id=trace_id,
        instructions=instructions_for_policy,
        current_response=final_current,
        latest_instructions=latest_for_policy,
    )

    return PolicyReplayOutcome(
        trace_id=trace_id,
        policy_result=policy_result,
        instructions=instructions,
        latest_instructions=latest_instructions,
        instructions_for_policy=instructions_for_policy,
        latest_instructions_for_policy=latest_for_policy,
        current_response_for_policy=final_current,
    )


def outcome_to_jsonable(outcome: PolicyReplayOutcome) -> Dict[str, Any]:
    pr = outcome.policy_result
    return {
        "trace_id": outcome.trace_id,
        "modified": pr.modified,
        "error_type": pr.error_type,
        "policy_names": pr.policy_names,
        "policy_sources": pr.policy_sources,
        "inactivate_error_type": pr.inactivate_error_type,
        "response_after_policy": pr.response,
        "current_response_input_to_policy": outcome.current_response_for_policy,
        "instruction_count_total": len(outcome.instructions_for_policy),
        "instruction_count_latest": len(outcome.latest_instructions_for_policy),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Policy replay from spec JSON (see docs/policy_test_harness.md).",
    )
    parser.add_argument("spec_file", type=Path)
    parser.add_argument(
        "--dump-instructions",
        action="store_true",
        help=(
            "Include instructions and latest_instructions in output "
            "(after apply_user_approval_preprocessing; same lists as check_response_policy)"
        ),
    )
    args = parser.parse_args(argv)

    spec = json.loads(args.spec_file.read_text(encoding="utf-8"))
    outcome = run_policy_replay_from_spec(spec)
    payload = outcome_to_jsonable(outcome)
    if args.dump_instructions:
        payload["instructions"] = outcome.instructions_for_policy
        payload["latest_instructions"] = outcome.latest_instructions_for_policy
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
