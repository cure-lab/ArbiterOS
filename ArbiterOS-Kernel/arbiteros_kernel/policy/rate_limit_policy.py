from __future__ import annotations

from typing import Any, Dict, List

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy


def _friendly_rate_limit_reason(tool_name: str, reason: str | None) -> str:
    text = (reason or "").strip()
    lines: List[str] = [
        f"我没有执行工具 `{tool_name}`。",
        "原因：同一个工具在最近的流程里被连续调用的次数过多，已经达到当前策略允许的上限。"
    ]
    if text:
        lines.append(f"补充说明：{text}")
    lines.append("请稍后再试，改用其他工具，或把操作拆成不同类型的步骤后再继续。")
    return "\n".join(lines)


class RateLimitPolicy(Policy):
    """
    仅实现“连续同一 tool 次数上限”（确定性，依赖 instruction history）
    - config: rate_limit.max_consecutive_same_tool
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
        rl = RUNTIME.cfg.get("rate_limit", {}) or {}
        if int(rl.get("max_consecutive_same_tool", 0) or 0) <= 0:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        response = dict(current_response)
        tool_calls = RUNTIME.extract_tool_calls(response)
        if not tool_calls:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        errors: List[str] = []
        kept: List[Dict[str, Any]] = []

        # history = instructions (already includes previous tool actions). We check sequentially as if these tool_calls will be executed now.
        history = list(instructions)

        for tc in tool_calls:
            tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}
            args_dict = canonicalize_args(args_dict)

            ok, reason = RUNTIME.check_consecutive_same_tool(history_instructions=history, tool=tool_name)
            if ok:
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                # append a synthetic tool-instruction into history for subsequent checks
                history.append({
                    "instruction_type": RUNTIME.tool_to_instruction_type(tool_name),
                    "content": {"tool_name": tool_name, "arguments": args_dict},
                })
            else:
                errors.append(_friendly_rate_limit_reason(tool_name, reason))
                RUNTIME.audit(
                    phase="policy.rate_limit",
                    trace_id=trace_id,
                    tool=tool_name,
                    decision="BLOCK",
                    reason=reason,
                    args=args_dict,
                )

        if errors:
            response["tool_calls"] = kept if kept else None
            if not kept:
                response["function_call"] = None
                if not isinstance(response.get("content"), str) or not response.get("content"):
                    response["content"] = "\n\n".join(errors[:3])
            return PolicyCheckResult(modified=True, response=response, error_type="\n\n".join(errors))

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)