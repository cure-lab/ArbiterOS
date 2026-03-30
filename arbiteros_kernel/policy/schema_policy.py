from __future__ import annotations

import re
from typing import Any, Dict, List

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy


def _friendly_schema_reason(tool_name: str, reason: str) -> str:
    text = (reason or "").strip()

    if not text:
        return (
            f"我没有执行工具 `{tool_name}`。\n"
            "原因：参数格式不符合要求。\n"
            "请检查必填字段、字段类型和取值范围后再试。"
        )

    m = re.search(r"'([^']+)' is a required property", text)
    if m:
        field = m.group(1)
        return (
            f"我没有执行工具 `{tool_name}`。\n"
            f"原因：缺少必填参数 `{field}`。\n"
            f"请补全 `{field}` 后再试。"
        )

    m = re.search(r"Additional properties are not allowed \('([^']+)' was unexpected\)", text)
    if m:
        field = m.group(1)
        return (
            f"我没有执行工具 `{tool_name}`。\n"
            f"原因：参数中包含不支持的字段 `{field}`。\n"
            "请删除不被支持的字段后再试。"
        )

    if "is not of type" in text:
        return (
            f"我没有执行工具 `{tool_name}`。\n"
            "原因：某个参数的类型不正确。\n"
            "请检查是否把字符串、数字、布尔值等类型填对后再试。"
        )

    if "is not one of" in text:
        return (
            f"我没有执行工具 `{tool_name}`。\n"
            "原因：某个参数的取值不在允许范围内。\n"
            "请改成该工具支持的取值后再试。"
        )

    if "too short" in text:
        return (
            f"我没有执行工具 `{tool_name}`。\n"
            "原因：某个参数内容过短，不满足最小长度要求。\n"
            "请补充更完整的参数内容后再试。"
        )

    if "too long" in text:
        return (
            f"我没有执行工具 `{tool_name}`。\n"
            "原因：某个参数内容过长，超过了允许的长度。\n"
            "请缩短参数内容后再试。"
        )

    return (
        f"我没有执行工具 `{tool_name}`。\n"
        "原因：参数未通过格式校验。\n"
        f"补充说明：{text}\n"
        "请根据这条说明修正参数后再试。"
    )


class SchemaValidationPolicy(Policy):
    """
    对 tool_calls 做 JSON schema 校验，不通过就移除。
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

        for tc in tool_calls:
            tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}
            args_dict = canonicalize_args(args_dict)

            ok, reason = RUNTIME.check_schema(tool=tool_name, args=args_dict)
            if ok:
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
            else:
                errors.append(_friendly_schema_reason(tool_name, reason))
                RUNTIME.audit(
                    phase="policy.schema",
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