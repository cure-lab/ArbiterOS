# arbiteros_kernel/policy/taint_policy.py
from __future__ import annotations

import re
from typing import Any, Dict, List

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy


def _text_contains_any_snippet(text: str, snippets: List[str]) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    hay = re.sub(r"\s+", " ", text).strip()
    hay_l = hay.lower()
    for sn in snippets or []:
        if not isinstance(sn, str) or not sn.strip():
            continue
        s = re.sub(r"\s+", " ", sn).strip()
        if not s:
            continue
        if s in hay:
            return True
        if s.lower() in hay_l:
            return True
    return False


class TaintPolicy(Policy):
    """
    Deterministic taint policy driven by tool outputs recorded in instruction history.

    - Build taint state from full instruction history (RUNTIME.build_taint_state()).
    - For high-risk sink tools (exec/write by config):
        - block if sink args contain tainted snippets (or within time window if ts is available).
    - Optional: treat RESPOND as sink:
        - block/replace if output content contains tainted snippets.
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
        ta_cfg = (RUNTIME.cfg.get("taint") or {})
        if not isinstance(ta_cfg, dict) or not bool(ta_cfg.get("enabled", False)):
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        taint_state = RUNTIME.build_taint_state(instructions)

        response = dict(current_response)
        tool_calls = RUNTIME.extract_tool_calls(response)

        errors: List[str] = []
        kept: List[Dict[str, Any]] = []

        # 1) tool sink gating (exec/write by config)
        for tc in tool_calls:
            tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}
            args_dict = canonicalize_args(args_dict)

            op_id = RUNTIME.compute_op_id(trace_id, tool_name, args_dict)
            ok, reason = RUNTIME.check_taint_sink_for_tool(
                tool=tool_name,
                args=args_dict,
                taint=taint_state,
                op_id=op_id,
            )

            if ok:
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                continue

            msg = f"POLICY_BLOCK tool={tool_name} reason={reason} op_id={op_id}"
            errors.append(msg)
            RUNTIME.audit(
                phase="policy.taint",
                trace_id=trace_id,
                tool=tool_name,
                decision="BLOCK",
                reason=reason,
                args=args_dict,
                extra={"op_id": op_id, "taint_source": taint_state.last_untrusted_source},
            )

        if errors:
            response["tool_calls"] = kept if kept else None
            if not kept:
                response["function_call"] = None
                if not isinstance(response.get("content"), str) or not response.get("content"):
                    response["content"] = "\n".join(errors[:3])
            return PolicyCheckResult(modified=True, response=response, error_type="\n".join(errors))

        # 2) optional: treat RESPOND as sink
        if bool(ta_cfg.get("treat_respond_as_sink", False)):
            content = response.get("content")
            if isinstance(content, str) and content.strip():
                snippets = getattr(taint_state, "snippets", []) or []
                if snippets and _text_contains_any_snippet(content, snippets):
                    op_id = RUNTIME.compute_op_id(trace_id, "@instruction", {"type": "RESPOND"})
                    if RUNTIME.approval_granted(op_id, "RESPOND"):
                        return PolicyCheckResult(modified=False, response=current_response, error_type=None)
                    reason = "taint: RESPOND content contains untrusted snippet from prior tool output"
                    msg = RUNTIME.approval_hint(op_id=op_id, scope="RESPOND", base=reason)
                    response["content"] = f"POLICY_BLOCK instruction=RESPOND reason={msg} op_id={op_id}"
                    RUNTIME.audit(
                        phase="policy.taint",
                        trace_id=trace_id,
                        tool="@instruction",
                        decision="BLOCK",
                        reason=reason,
                        args={"op_id": op_id},
                        extra={"taint_source": taint_state.last_untrusted_source},
                    )
                    return PolicyCheckResult(modified=True, response=response, error_type=reason)

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)