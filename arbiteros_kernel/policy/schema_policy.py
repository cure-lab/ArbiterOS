from __future__ import annotations

import re
from typing import Any, Dict, List

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy


def _friendly_schema_reason(tool_name: str, reason: str) -> str:
    text = (reason or "").strip()

    if not text:
        return f"工具 `{tool_name}` 的参数格式不符合要求。请检查必填字段、字段类型和取值范围。"

    m = re.search(r"'([^']+)' is a required property", text)
    if m:
        field = m.group(1)
        return f"工具 `{tool_name}` 缺少必填参数 `{field}`。"

    m = re.search(r"Additional properties are not allowed \('([^']+)' was unexpected\)", text)
    if m:
        field = m.group(1)
        return f"工具 `{tool_name}` 包含不支持的参数 `{field}`。"

    if "is not of type" in text:
        return f"工具 `{tool_name}` 的某个参数类型不正确。请检查参数类型是否填写正确。"

    if "is not one of" in text:
        return f"工具 `{tool_name}` 的某个参数取值不合法。请使用允许的选项。"

    if "too short" in text:
        return f"工具 `{tool_name}` 的某个参数内容过短。"

    if "too long" in text:
        return f"工具 `{tool_name}` 的某个参数内容过长。"

    return f"工具 `{tool_name}` 的参数未通过格式校验。详情：{text}"


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
                    response["content"] = "\n".join(errors[:3])
            return PolicyCheckResult(modified=True, response=response, error_type="\n".join(errors))

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)