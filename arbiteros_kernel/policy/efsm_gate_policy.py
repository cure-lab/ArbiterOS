from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy


def _split_history_and_latest(
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    你们的 builder 一般是 append；因此 latest_instructions 通常是 suffix。
    这里做 best-effort：
    - 若能 suffix match（按 id 或 runtime_step），就拆开
    - 否则：history=全部，latest=latest_instructions（不从 history 剔除）
    """
    if not instructions or not latest_instructions:
        return instructions, latest_instructions

    n = len(latest_instructions)
    tail = instructions[-n:]
    # try match by id
    tail_ids = [t.get("id") for t in tail]
    latest_ids = [t.get("id") for t in latest_instructions]
    if all(isinstance(x, str) for x in tail_ids) and tail_ids == latest_ids:
        return instructions[:-n], latest_instructions

    # try match by runtime_step
    tail_steps = [t.get("runtime_step") for t in tail]
    latest_steps = [t.get("runtime_step") for t in latest_instructions]
    if all(isinstance(x, int) for x in tail_steps) and tail_steps == latest_steps:
        return instructions[:-n], latest_instructions

    return instructions, latest_instructions


def _friendly_approval_message(
    tool_name: str,
    instruction_type: str,
    current_state: Any,
    msg: str,
) -> str:
    text = (msg or "").strip()
    state_text = str(current_state) if current_state is not None else "UNKNOWN"
    lines: List[str] = [
        f"我暂时没有执行工具 `{tool_name}`。",
        f"当前流程阶段：`{state_text}`。",
        f"你请求的动作类型：`{instruction_type}`。",
        "原因：按照当前流程规则，这一步在执行前需要先获得用户确认。",
        "这通常表示该操作可能带来实际副作用，或者必须在确认后才能继续。"
    ]
    if text:
        lines.append(f"补充说明：{text}")
    lines.append("如果你确认要继续，请先完成确认步骤，然后再重新发起这一步操作。")
    return "\n".join(lines)


def _friendly_efsm_block_message(
    tool_name: str,
    instruction_type: str,
    current_state: Any,
    reason: str | None,
) -> str:
    text = (reason or "").strip()
    state_text = str(current_state) if current_state is not None else "UNKNOWN"
    lines: List[str] = [
        f"我没有执行工具 `{tool_name}`。",
        f"当前流程阶段：`{state_text}`。",
        f"你请求的动作类型：`{instruction_type}`。",
        "原因：当前流程在这个阶段不允许执行这类操作。"
    ]
    if text:
        lines.append(f"补充说明：{text}")
    lines.append("通常需要先完成前一步、进入允许该操作的阶段，或改用当前阶段允许的操作后再试。")
    return "\n".join(lines)


class EfsmGatePolicy(Policy):
    """
    对 tool_calls 做 instruction-based gating：
    - 从历史 instructions 回放 EFSM 得到 state/vars/plan
    - 对当前 response 的 tool_calls 逐个执行 EFSM step
    - effect=REQUIRE_APPROVAL 且未批准 => 移除该 tool_call
    """

    def check(
        self,
        instructions: List[Dict[str, Any]],
        current_response: Dict[str, Any],
        latest_instructions: List[Dict[str, Any]],
        trace_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> PolicyCheckResult:
        if not RUNTIME.efsm_enabled():
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        response = dict(current_response)
        tool_calls = RUNTIME.extract_tool_calls(response)
        if not tool_calls:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        history, latest = _split_history_and_latest(instructions, latest_instructions)

        # base replay from history only
        state, vars_, plan = RUNTIME.efsm_replay_history(history)

        # tool_call_id -> latest instruction_type/category (optional)
        latest_by_tool_call_id: Dict[str, Dict[str, Any]] = {}
        for ins in latest or []:
            content = ins.get("content")
            if isinstance(content, dict):
                tcid = content.get("tool_call_id")
                if isinstance(tcid, str) and tcid:
                    latest_by_tool_call_id[tcid] = ins

        errors: List[str] = []
        kept: List[Dict[str, Any]] = []

        for tc in tool_calls:
            tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}
            args_dict = canonicalize_args(args_dict)

            # event type: ALWAYS derive from tool name (read/write/exec),
            # because upstream traces often label tool instructions as "EXEC".
            it = RUNTIME.tool_to_instruction_type(tool_name)

            # category: still best-effort from latest_instructions (optional)
            cat: Optional[str] = None
            if tool_call_id and tool_call_id in latest_by_tool_call_id:
                _cat = latest_by_tool_call_id[tool_call_id].get("instruction_category")
                cat = _cat if isinstance(_cat, str) else None
            if not cat:
                cat = RUNTIME.instruction_type_to_category(it)

            op_id = RUNTIME.compute_op_id(trace_id, tool_name, args_dict)

            payload: Dict[str, Any] = {
                "event": it,
                "tool": tool_name,
                "args": args_dict,
                "category": cat,
                "op_id": op_id,
            }

            current_state_before = state
            step = RUNTIME.efsm_step(current_state=state, vars_=vars_, plan=plan, event=it, payload=payload)
            # advance local state for next tool_call
            state = step.next_state

            if step.effect == "REQUIRE_APPROVAL":
                scope = tool_name
                if RUNTIME.approval_granted(op_id, scope):
                    kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                    continue
                msg = RUNTIME.approval_hint(op_id=op_id, scope=scope, base=f"efsm: action requires approval (event={it}, scope={scope})")
                friendly_msg = _friendly_approval_message(tool_name, it, current_state_before, msg)
                errors.append(friendly_msg)
                RUNTIME.audit(
                    phase="policy.efsm_gate",
                    trace_id=trace_id,
                    tool=tool_name,
                    decision="BLOCK",
                    reason=msg,
                    args=args_dict,
                    extra={"efsm_state": state, "efsm_transition": step.matched_transition, "instruction_type": it, "category": cat},
                )
                continue

            if not step.allow:
                friendly_msg = _friendly_efsm_block_message(tool_name, it, current_state_before, step.reason)
                errors.append(friendly_msg)
                RUNTIME.audit(
                    phase="policy.efsm_gate",
                    trace_id=trace_id,
                    tool=tool_name,
                    decision="BLOCK",
                    reason=step.reason,
                    args=args_dict,
                    extra={"efsm_state": state, "efsm_transition": step.matched_transition, "instruction_type": it, "category": cat},
                )
                continue

            kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))

        if errors:
            response["tool_calls"] = kept if kept else None
            if not kept:
                response["function_call"] = None
                if not isinstance(response.get("content"), str) or not response.get("content"):
                    response["content"] = "\n\n".join(errors[:3])
            return PolicyCheckResult(modified=True, response=response, error_type="\n\n".join(errors))

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)