# arbiteros_kernel/policy/security_label_policy.py
from __future__ import annotations

from typing import Any, Dict, List

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME

from .policy import Policy


class SecurityLabelPolicy(Policy):
    """
    依据 latest_instructions[*].security_type 做 label-aware gating：
    - 对 tool_calls：不满足则移除
    - 对 RESPOND：不满足则替换输出
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
        # fast path: no security config
        sec_cfg = (RUNTIME.cfg.get("security") or {})
        if not isinstance(sec_cfg, dict) or not sec_cfg:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        response = dict(current_response)
        tool_calls = RUNTIME.extract_tool_calls(response)

        # build tool_call_id -> security_type
        sec_by_tool_call_id: Dict[str, Dict[str, Any]] = {}
        for ins in latest_instructions or []:
            content = ins.get("content")
            if not isinstance(content, dict):
                continue
            tcid = content.get("tool_call_id")
            if isinstance(tcid, str) and tcid:
                st = ins.get("security_type")
                if isinstance(st, dict):
                    sec_by_tool_call_id[tcid] = st

        errors: List[str] = []
        kept: List[Dict[str, Any]] = []

        for tc in tool_calls:
            tool_name, tool_call_id, args_dict, was_json_str = RUNTIME.parse_tool_call(tc)
            st = sec_by_tool_call_id.get(tool_call_id or "", None)
            ok, reason = RUNTIME.check_security(st)
            if ok:
                kept.append(tc)
            else:
                errors.append(f"POLICY_BLOCK tool={tool_name} reason={reason}")
                RUNTIME.audit(
                    phase="policy.security_label",
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

        # RESPOND label check (best-effort: find RESPOND in latest_instructions; else skip)
        content = response.get("content")
        if isinstance(content, str) and content.strip():
            respond_st = None
            for ins in latest_instructions or []:
                if (ins.get("instruction_type") or "").strip().upper() == "RESPOND":
                    st = ins.get("security_type")
                    if isinstance(st, dict):
                        respond_st = st
                        break
            ok, reason = RUNTIME.check_security(respond_st)
            if not ok:
                msg = f"POLICY_BLOCK instruction=RESPOND reason={reason}"
                response["content"] = msg
                return PolicyCheckResult(modified=True, response=response, error_type=msg)

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)