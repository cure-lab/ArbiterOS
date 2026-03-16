"""Taint analysis policy: gate tool calls by trustworthiness vs confidentiality levels."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from arbiteros_kernel.instruction_parsing.types import LEVEL_ORDER
from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME

from .policy import Policy

# Built-in defaults; overridden by taint.taint_policy in policy.json
_DEFAULT_INPUT_TOOLS = frozenset({
    "read", "web_fetch", "web_search", "session_status", "sessions_list",
    "sessions_history", "memory_search", "memory_get", "agents_list", "image",
})
_DEFAULT_OUTPUT_TOOLS = frozenset({
    "edit", "write", "exec", "message", "sessions_send", "sessions_spawn",
    "tts", "gateway",
})
_DEFAULT_TOOLS_BY_ACTION: Dict[str, Dict[str, List[str]]] = {
    "browser": {
        "input_actions": ["status", "start", "stop", "profiles", "tabs", "snapshot", "screenshot", "console", "pdf"],
        "output_actions": ["open", "focus", "close", "navigate", "upload", "dialog", "act"],
    },
    "process": {
        "input_actions": ["list", "poll", "log"],
        "output_actions": ["write", "send-keys", "submit", "paste", "kill"],
    },
    "canvas": {
        "input_actions": ["snapshot"],
        "output_actions": ["present", "hide", "navigate", "eval", "a2ui_push", "a2ui_reset"],
    },
    "nodes": {
        "input_actions": ["status", "describe", "camera_snap", "camera_list", "camera_clip", "screen_record", "location_get"],
        "output_actions": ["pending", "approve", "reject", "notify", "run", "invoke"],
    },
    "cron": {
        "input_actions": ["status", "list", "runs"],
        "output_actions": ["add", "update", "remove", "run", "wake"],
    },
}


def _level_at_least(a: Optional[str], b: Optional[str]) -> bool:
    """Return True iff level a >= level b. Ordering: LOW < UNKNOWN < MID < HIGH."""
    rank_a = LEVEL_ORDER.get((a or "UNKNOWN").strip(), 0.5)
    rank_b = LEVEL_ORDER.get((b or "UNKNOWN").strip(), 0.5)
    return rank_a >= rank_b


def _get_taint_policy_config() -> Tuple[frozenset[str], frozenset[str], Dict[str, Dict[str, List[str]]]]:
    """Return (input_tools, output_tools, tools_by_action) from taint.taint_policy config."""
    ta = (RUNTIME.cfg.get("taint") or {})
    if not isinstance(ta, dict):
        return _DEFAULT_INPUT_TOOLS, _DEFAULT_OUTPUT_TOOLS, _DEFAULT_TOOLS_BY_ACTION

    tp = ta.get("taint_policy")
    if not isinstance(tp, dict):
        return _DEFAULT_INPUT_TOOLS, _DEFAULT_OUTPUT_TOOLS, _DEFAULT_TOOLS_BY_ACTION

    input_tools = _DEFAULT_INPUT_TOOLS
    output_tools = _DEFAULT_OUTPUT_TOOLS
    tools_by_action = dict(_DEFAULT_TOOLS_BY_ACTION)

    inp = tp.get("input_tools")
    if isinstance(inp, list) and inp:
        input_tools = frozenset(str(x).strip().lower() for x in inp if str(x).strip())

    out = tp.get("output_tools")
    if isinstance(out, list) and out:
        output_tools = frozenset(str(x).strip().lower() for x in out if str(x).strip())

    tba = tp.get("tools_by_action")
    if isinstance(tba, dict) and tba:
        for tool, actions in tba.items():
            if isinstance(actions, dict):
                inp_acts = actions.get("input_actions")
                out_acts = actions.get("output_actions")
                tools_by_action[tool.strip().lower()] = {
                    "input_actions": [str(a).strip().lower() for a in inp_acts] if isinstance(inp_acts, list) else [],
                    "output_actions": [str(a).strip().lower() for a in out_acts] if isinstance(out_acts, list) else [],
                }

    return input_tools, output_tools, tools_by_action


def _classify_tool(tool_name: str, args_dict: Dict[str, Any]) -> str:
    """
    Return "input", "output", or "none".
    For tools with action param (browser, process, canvas, nodes, cron), classify by action.
    """
    name = (tool_name or "").strip().lower()
    if not name:
        return "none"

    input_tools, output_tools, tools_by_action = _get_taint_policy_config()

    # Action-dependent tools
    if name in tools_by_action:
        action_cfg = tools_by_action[name]
        action = (args_dict.get("action") or "").strip().lower()
        if action:
            out_acts = set(action_cfg.get("output_actions") or [])
            inp_acts = set(action_cfg.get("input_actions") or [])
            if action in out_acts:
                return "output"
            if action in inp_acts:
                return "input"
        # Unknown action: treat as output (stricter)
        return "output"

    if name in output_tools:
        return "output"
    if name in input_tools:
        return "input"
    return "none"


class TaintPolicy(Policy):
    """
    Taint analysis policy: gates tool calls based on trustworthiness vs confidentiality.
    - Input tools: pass iff trustworthiness >= confidentiality
    - Output tools: pass iff trustworthiness >= prop_confidentiality
    - Level ordering: LOW < UNKNOWN < MID < HIGH
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

        # Build tool_call_id -> instruction (with security_type)
        instr_by_tool_call_id: Dict[str, Dict[str, Any]] = {}
        for ins in latest_instructions or []:
            content = ins.get("content")
            if not isinstance(content, dict):
                continue
            tcid = content.get("tool_call_id")
            if isinstance(tcid, str) and tcid:
                instr_by_tool_call_id[tcid] = ins

        errors: List[str] = []
        kept: List[Dict[str, Any]] = []

        for tc in tool_calls:
            tool_name, tool_call_id, args_dict, was_json_str = RUNTIME.parse_tool_call(tc)
            ins = instr_by_tool_call_id.get(tool_call_id or "", {})
            security_type = ins.get("security_type") if isinstance(ins, dict) else {}
            if not isinstance(security_type, dict):
                security_type = {}

            kind = _classify_tool(tool_name, args_dict)
            if kind == "none":
                kept.append(tc)
                continue

            def _safe_level(v: Any) -> str:
                s = v if isinstance(v, str) else "UNKNOWN"
                return (s or "UNKNOWN").strip() or "UNKNOWN"

            trust = _safe_level(security_type.get("trustworthiness"))
            conf = _safe_level(security_type.get("confidentiality"))
            # Fallback to confidentiality when prop_confidentiality is missing
            prop_conf = _safe_level(
                security_type.get("prop_confidentiality") or security_type.get("confidentiality")
            )

            if kind == "input":
                ok = _level_at_least(trust, conf)
                reason = (
                    f"trustworthiness < confidentiality ({trust} < {conf})"
                    if not ok
                    else None
                )
            else:
                ok = _level_at_least(trust, prop_conf)
                reason = (
                    f"trustworthiness < prop_confidentiality ({trust} < {prop_conf})"
                    if not ok
                    else None
                )

            if ok:
                kept.append(tc)
            else:
                errors.append(f"POLICY_BLOCK tool={tool_name} reason={reason}")
                RUNTIME.audit(
                    phase="policy.taint",
                    trace_id=trace_id,
                    tool=tool_name,
                    decision="BLOCK",
                    reason=reason or "taint check failed",
                    args=args_dict,
                )

        if errors:
            response["tool_calls"] = kept if kept else None
            if not kept:
                response["function_call"] = None
                if not isinstance(response.get("content"), str) or not response.get("content"):
                    response["content"] = "\n".join(errors[:3])
            return PolicyCheckResult(
                modified=True, response=response, error_type="\n".join(errors)
            )

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)
