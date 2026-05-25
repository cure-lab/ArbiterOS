from __future__ import annotations

from typing import Any, Dict, List

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy


def _friendly_rate_limit_reason(tool_name: str, reason: str | None) -> str:
    text = (reason or "").strip()
    lines: List[str] = [
        f"我没有执行工具 `{tool_name}`。",
        "原因：当前对话的工具调用额度已经达到策略允许的硬上限。"
    ]
    if text:
        lines.append(f"补充说明：{text}")
    lines.append("请停止继续调用工具，整理已有结果并尽快输出最终答复。")
    return "\n".join(lines)


def _policy_enabled() -> bool:
    limit_cfg = RUNTIME.cfg.get("tool_call_limit", {}) or {}
    rl = RUNTIME.cfg.get("rate_limit", {}) or {}
    if not isinstance(limit_cfg, dict):
        limit_cfg = {}
    if not isinstance(rl, dict):
        rl = {}
    numeric_keys = (
        "max_total_tool_calls",
        "max_calls_per_tool",
        "max_consecutive_same_tool",
    )
    for cfg in (limit_cfg, rl):
        for key in numeric_keys:
            try:
                if int(cfg.get(key, 0) or 0) > 0:
                    return True
            except Exception:
                continue
        overrides = cfg.get("per_tool_max_calls")
        if isinstance(overrides, dict):
            for value in overrides.values():
                try:
                    if int(value) > 0:
                        return True
                except Exception:
                    continue
    return False


def _prior_history(
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Normal kernel policy input includes latest_instructions inside instructions.
    Rate limits must count completed prior calls, then simulate current calls
    from current_response one by one.
    """
    if not latest_instructions:
        return list(instructions)
    prior_len = max(0, len(instructions) - len(latest_instructions))
    return list(instructions[:prior_len])


class RateLimitPolicy(Policy):
    """
    Deterministic tool-call budget guard.

    Supported hard limits:
    - tool_call_limit.max_total_tool_calls / rate_limit.max_total_tool_calls
    - tool_call_limit.max_calls_per_tool / rate_limit.max_calls_per_tool
    - tool_call_limit.per_tool_max_calls / rate_limit.per_tool_max_calls
    - rate_limit.max_consecutive_same_tool
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
        if not _policy_enabled():
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        response = dict(current_response)
        tool_calls = RUNTIME.extract_tool_calls(response)
        if not tool_calls:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        errors: List[str] = []
        kept: List[Dict[str, Any]] = []

        history = _prior_history(instructions, latest_instructions)

        for tc in tool_calls:
            tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}
            args_dict = canonicalize_args(args_dict)

            budget_ok, budget_reason = RUNTIME.check_tool_call_budget(
                history_instructions=history,
                tool=tool_name,
            )
            consecutive_ok, consecutive_reason = RUNTIME.check_consecutive_same_tool(
                history_instructions=history,
                tool=tool_name,
            )
            ok = budget_ok and consecutive_ok
            reason = budget_reason if not budget_ok else consecutive_reason
            if ok:
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                # append a synthetic tool-instruction into history for subsequent checks
                history.append({
                    "instruction_type": RUNTIME.tool_to_instruction_type(tool_name),
                    "content": {
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "arguments": args_dict,
                    },
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
                    extra={
                        "tool_call_id": tool_call_id,
                        "limit_type": "tool_call_budget"
                        if not budget_ok
                        else "consecutive_same_tool",
                    },
                )

        if errors:
            response["tool_calls"] = kept if kept else None
            if not kept:
                response["function_call"] = None
                if not isinstance(response.get("content"), str) or not response.get("content"):
                    response["content"] = "\n\n".join(errors[:3])
            return PolicyCheckResult(modified=True, response=response, error_type="\n\n".join(errors))

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)
