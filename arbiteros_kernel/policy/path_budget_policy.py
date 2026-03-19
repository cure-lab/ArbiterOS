from __future__ import annotations

from typing import Any, Dict, List

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy


def _friendly_path_budget_reason(tool_name: str, reason: str | None) -> str:
    text = (reason or "").strip()
    lower = text.lower()

    if "path" in lower or "prefix" in lower or "workspace" in lower:
        base = f"已拦截工具 `{tool_name}`：访问路径不在当前策略允许的范围内。"
    elif "length" in lower or "too long" in lower or "max_str_len" in lower:
        base = f"已拦截工具 `{tool_name}`：参数内容过长，超过了当前策略允许的长度。"
    else:
        base = f"已拦截工具 `{tool_name}`：参数或路径未通过检查。"

    if text:
        return f"{base} 详情：{text}"
    return base


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
                    response["content"] = "\n".join(errors[:3])
            return PolicyCheckResult(modified=True, response=response, error_type="\n".join(errors))

        # ✅ no errors, but we still want to write back canonicalized args
        if changed:
            response["tool_calls"] = kept
            return PolicyCheckResult(modified=True, response=response, error_type=None)

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)