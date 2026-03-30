from __future__ import annotations

from typing import Any, Dict, List

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME

from .policy import Policy


class OutputBudgetPolicy(Policy):
    """
    对最终返回的 assistant content 做 budget（截断），不影响 tool_calls。
    config: output_budget.max_chars
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
        out_budget = RUNTIME.cfg.get("output_budget", {}) or {}
        max_chars = int(out_budget.get("max_chars", 0) or 0)
        if max_chars <= 0:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        content = current_response.get("content")
        if not isinstance(content, str) or not content:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        if len(content) <= max_chars:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        trimmed = content[:max_chars]
        response = dict(current_response)
        response["content"] = trimmed
        audit_msg = f"POLICY_TRANSFORM output truncated to {max_chars} chars"
        user_msg = (
            f"这条回复原本长度为 {len(content)} 个字符，超过了当前策略允许的上限 {max_chars} 个字符。"
            f"我已只保留前 {max_chars} 个字符。"
            "如果你需要完整内容，请缩小查询范围，或分段获取结果。"
        )
        RUNTIME.audit(
            phase="policy.output_budget",
            trace_id=trace_id,
            tool="@instruction",
            decision="TRANSFORM",
            reason=audit_msg,
            args={"orig_len": len(content), "max_chars": max_chars},
        )
        return PolicyCheckResult(modified=True, response=response, error_type=user_msg)