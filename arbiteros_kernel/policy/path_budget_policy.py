from __future__ import annotations

from typing import Any, Dict, List

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy


def _friendly_path_budget_reason(tool_name: str, reason: str | None) -> str:
    text = (reason or "").strip()
    lower = text.lower()

    if "path" in lower or "prefix" in lower or "workspace" in lower:
        lines = [
            f"我没有执行工具 `{tool_name}`。",
            "原因：这次请求访问的路径不在当前策略允许的范围内。",
        ]
        if text:
            lines.append(f"补充说明：{text}")
        lines.append("请改用允许访问的目录或文件路径后再试。")
        return "\n".join(lines)

    if "length" in lower or "too long" in lower or "max_str_len" in lower:
        lines = [
            f"我没有执行工具 `{tool_name}`。",
            "原因：这次请求中的参数内容过长，超过了当前策略允许的长度限制。",
        ]
        if text:
            lines.append(f"补充说明：{text}")
        lines.append("请缩短输入内容，或把操作拆成更小的几步后再试。")
        return "\n".join(lines)

    lines = [
        f"我没有执行工具 `{tool_name}`。",
        "原因：这次请求的参数或路径未通过当前策略检查。"
    ]
    if text:
        lines.append(f"补充说明：{text}")
    lines.append("请检查路径是否可访问、参数是否过长，然后再重试。")
    return "\n".join(lines)


class PathBudgetPolicy(Policy):
    """
    - 路径 allow/deny 前缀
    - 参数长度预算（input_budget.max_str_len）
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
        if not tool_calls:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        errors: List[str] = []
        kept: List[Dict[str, Any]] = []
        changed = False

        for tc in tool_calls:
            tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}

            # IMPORTANT: use runtime-aware canonicalization (adds path, resolves relative->workspace)
            args_dict = RUNTIME.canonicalize_tool_args(args_dict)

            ok, reason = RUNTIME.check_path_and_budget(tool=tool_name, args=args_dict)
            new_tc = RUNTIME.write_back_tool_args(tc, args_dict, was_json_str)
            if new_tc != tc:
                changed = True

            if ok:
                kept.append(new_tc)
            else:
                errors.append(_friendly_path_budget_reason(tool_name, reason))
                RUNTIME.audit(
                    phase="policy.path_budget",
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

        # ✅ no errors, but we still want to write back canonicalized args
        if changed:
            response["tool_calls"] = kept
            return PolicyCheckResult(modified=True, response=response, error_type=None)

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)