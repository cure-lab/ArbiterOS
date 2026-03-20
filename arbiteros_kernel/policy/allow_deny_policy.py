from __future__ import annotations

from typing import Any, Dict, List, Optional

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy


def _latest_tool_instr_index(latest_instructions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    map tool_call_id -> instruction (best-effort)
    """
    out: Dict[str, Dict[str, Any]] = {}
    for ins in latest_instructions or []:
        content = ins.get("content")
        if not isinstance(content, dict):
            continue
        tcid = content.get("tool_call_id")
        if isinstance(tcid, str) and tcid:
            out[tcid] = ins
    return out


def _friendly_allow_deny_reason(
    tool_name: str,
    instruction_type: str,
    category: Optional[str],
    reason: str | None,
) -> str:
    text = (reason or "").strip()
    lines: List[str] = [
        f"我没有执行工具 `{tool_name}`。",
        f"这一步属于 `{instruction_type}` 类型的操作。"
    ]
    if category:
        lines.append(f"当前操作类别为 `{category}`。")
    lines.append("原因：当前策略不允许在这一类场景下使用这个工具。")
    if text:
        lines.append(f"补充说明：{text}")
    lines.append("如果你希望继续，请改用当前阶段允许的工具，或调整到允许该操作的流程/权限后再试。")
    return "\n".join(lines)


def _friendly_respond_allow_deny_reason(reason: str | None) -> str:
    text = (reason or "").strip()
    lines: List[str] = [
        "我没有直接输出这条回复。",
        "原因：当前策略不允许在这一阶段直接向用户返回这类内容。"
    ]
    if text:
        lines.append(f"补充说明：{text}")
    lines.append("如果你希望继续，请先完成当前流程要求的步骤，或改成当前策略允许的输出方式。")
    return "\n".join(lines)


class AllowDenyPolicy(Policy):
    """
    - 对 tool_calls 做 allow/deny（tool + instruction_type + category）
    - 对 content (RESPOND) 做 instruction allow/deny（可选）
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
        response = dict(current_response)
        tool_calls = RUNTIME.extract_tool_calls(response)
        tool_idx = _latest_tool_instr_index(latest_instructions)

        errors: List[str] = []
        kept: List[Dict[str, Any]] = []

        for tc in tool_calls:
            tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}
            args_dict = canonicalize_args(args_dict)

            # instruction_type/category: prefer latest_instructions; fallback to tool mapping
            it: str = "EXEC"
            cat: Optional[str] = None
            if tool_call_id and tool_call_id in tool_idx:
                ins = tool_idx[tool_call_id]
                it = (ins.get("instruction_type") or RUNTIME.tool_to_instruction_type(tool_name)).strip().upper()
                cat = ins.get("instruction_category") if isinstance(ins.get("instruction_category"), str) else None
            else:
                it = RUNTIME.tool_to_instruction_type(tool_name)
                cat = RUNTIME.instruction_type_to_category(it)

            ok, reason = RUNTIME.check_allow_deny(tool=tool_name, instruction_type=it, category=cat)
            if ok:
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
            else:
                errors.append(_friendly_allow_deny_reason(tool_name, it, cat, reason))

                RUNTIME.audit(
                    phase="policy.allow_deny",
                    trace_id=trace_id,
                    tool=tool_name,
                    decision="BLOCK",
                    reason=reason,
                    args=args_dict,
                    extra={"instruction_type": it, "category": cat},
                )

        if errors:
            response["tool_calls"] = kept if kept else None
            if not kept:
                response["function_call"] = None
                if not isinstance(response.get("content"), str) or not response.get("content"):
                    response["content"] = "\n\n".join(errors[:3])
            return PolicyCheckResult(modified=True, response=response, error_type="\n\n".join(errors))

        # optional: instruction allow/deny on RESPOND
        content = response.get("content")
        if isinstance(content, str) and content.strip():
            ok, reason = RUNTIME.check_allow_deny(tool="@instruction", instruction_type="RESPOND", category="EXECUTION.Human")
            if not ok:
                msg = _friendly_respond_allow_deny_reason(reason)
                response["content"] = msg
                return PolicyCheckResult(modified=True, response=response, error_type=msg)

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)