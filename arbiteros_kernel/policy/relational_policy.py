from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from arbiteros_kernel.instruction_parsing.types import LEVEL_ORDER
from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy


# ---------------------------------------------------------------------------
# Built-in defaults; overridden by taint.taint_policy in policy.json
# ---------------------------------------------------------------------------

_DEFAULT_INPUT_TOOLS = frozenset(
    {
        "read",
        "web_fetch",
        "web_search",
        "session_status",
        "sessions_list",
        "sessions_history",
        "memory_search",
        "memory_get",
        "agents_list",
        "image",
    }
)

_DEFAULT_OUTPUT_TOOLS = frozenset(
    {
        "edit",
        "write",
        "exec",
        "message",
        "sessions_send",
        "sessions_spawn",
        "tts",
        "gateway",
    }
)

_DEFAULT_TOOLS_BY_ACTION: Dict[str, Dict[str, List[str]]] = {
    "browser": {
        "input_actions": [
            "status",
            "start",
            "stop",
            "profiles",
            "tabs",
            "snapshot",
            "screenshot",
            "console",
            "pdf",
        ],
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
        "input_actions": [
            "status",
            "describe",
            "camera_snap",
            "camera_list",
            "camera_clip",
            "screen_record",
            "location_get",
        ],
        "output_actions": ["pending", "approve", "reject", "notify", "run", "invoke"],
    },
    "cron": {
        "input_actions": ["status", "list", "runs"],
        "output_actions": ["add", "update", "remove", "run", "wake"],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_str(v: Any, default: str = "") -> str:
    return v.strip() if isinstance(v, str) and v.strip() else default


def _safe_upper(v: Any, default: str = "") -> str:
    s = _safe_str(v, default)
    return s.upper() if s else default


def _safe_level(v: Any, default: str = "UNKNOWN") -> str:
    s = _safe_upper(v, default)
    return s if s in LEVEL_ORDER else default


def _level_at_least(a: Optional[str], b: Optional[str]) -> bool:
    """
    Return True iff level a >= level b.
    Ordering: LOW < UNKNOWN < MID < HIGH.
    """
    rank_a = LEVEL_ORDER.get(_safe_level(a), 0.5)
    rank_b = LEVEL_ORDER.get(_safe_level(b), 0.5)
    return rank_a >= rank_b


def _get_taint_cfg() -> Dict[str, Any]:
    ta = RUNTIME.cfg.get("taint") or {}
    return ta if isinstance(ta, dict) else {}


def _get_taint_policy_config() -> Tuple[
    frozenset[str], frozenset[str], Dict[str, Dict[str, List[str]]], Dict[str, Any]
]:
    """
    Return:
      (input_tools, output_tools, tools_by_action, taint_policy_cfg)
    """
    ta = _get_taint_cfg()
    tp = ta.get("taint_policy")
    if not isinstance(tp, dict):
        return _DEFAULT_INPUT_TOOLS, _DEFAULT_OUTPUT_TOOLS, _DEFAULT_TOOLS_BY_ACTION, {}

    input_tools = _DEFAULT_INPUT_TOOLS
    output_tools = _DEFAULT_OUTPUT_TOOLS
    tools_by_action = dict(_DEFAULT_TOOLS_BY_ACTION)

    inp = tp.get("input_tools")
    if isinstance(inp, list) and inp:
        input_tools = frozenset(
            str(x).strip().lower()
            for x in inp
            if isinstance(x, str) and x.strip()
        )

    out = tp.get("output_tools")
    if isinstance(out, list) and out:
        output_tools = frozenset(
            str(x).strip().lower()
            for x in out
            if isinstance(x, str) and x.strip()
        )

    tba = tp.get("tools_by_action")
    if isinstance(tba, dict) and tba:
        for tool, actions in tba.items():
            if isinstance(actions, dict) and isinstance(tool, str) and tool.strip():
                inp_acts = actions.get("input_actions")
                out_acts = actions.get("output_actions")
                tools_by_action[tool.strip().lower()] = {
                    "input_actions": [
                        str(a).strip().lower()
                        for a in inp_acts
                        if isinstance(a, str) and a.strip()
                    ] if isinstance(inp_acts, list) else [],
                    "output_actions": [
                        str(a).strip().lower()
                        for a in out_acts
                        if isinstance(a, str) and a.strip()
                    ] if isinstance(out_acts, list) else [],
                }

    return input_tools, output_tools, tools_by_action, tp


def _extract_instruction_security(
    ins: Dict[str, Any],
    *,
    current_taint_status: Any = None,
) -> Dict[str, Any]:
    """
    Only consume kernel-lowered metadata.
    """
    st = ins.get("security_type") if isinstance(ins, dict) else {}
    st = st if isinstance(st, dict) else {}
    custom = st.get("custom")
    custom = custom if isinstance(custom, dict) else {}

    trust = _safe_level(st.get("trustworthiness"))
    conf = _safe_level(st.get("confidentiality"))
    prop_conf = _safe_level(st.get("prop_confidentiality") or st.get("confidentiality"))
    prop_trust = _safe_level(st.get("prop_trustworthiness") or st.get("trustworthiness"))

    if current_taint_status is not None:
        if trust == "UNKNOWN":
            trust = _safe_level(getattr(current_taint_status, "trustworthiness", "UNKNOWN"))
        if conf == "UNKNOWN":
            conf = _safe_level(getattr(current_taint_status, "confidentiality", "UNKNOWN"))
        if prop_conf == "UNKNOWN":
            prop_conf = conf
        if prop_trust == "UNKNOWN":
            prop_trust = trust

    return {
        "instruction_type": _safe_upper(ins.get("instruction_type")),
        "instruction_category": _safe_str(ins.get("instruction_category")),
        "trustworthiness": trust,
        "confidentiality": conf,
        "prop_confidentiality": prop_conf,
        "prop_trustworthiness": prop_trust,
        "authority": _safe_upper(st.get("authority"), "UNKNOWN"),
        "confidence": _safe_level(st.get("confidence")),
        "reversible": bool(st.get("reversible", False)),
        "risk": _safe_upper(st.get("risk"), "UNKNOWN"),
        "custom": custom,
    }


def _classify_tool(
    tool_name: str,
    args_dict: Dict[str, Any],
    sec: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Return "input", "output", or "none".

    Precedence:
    1) kernel-lowered custom metadata override
    2) action-based config
    3) direct tool list config
    """
    name = _safe_str(tool_name).lower()
    if not name:
        return "none"

    # 1) metadata override from kernel lowering
    custom = sec.get("custom", {}) if isinstance(sec, dict) else {}
    io_kind = _safe_str(
        custom.get("io_kind")
        or custom.get("flow_role")
        or custom.get("taint_role")
    ).lower()
    if io_kind in {"input", "output"}:
        return io_kind

    input_tools, output_tools, tools_by_action, _ = _get_taint_policy_config()

    # 2) action-based classification
    if name in tools_by_action:
        action_cfg = tools_by_action[name]
        action = _safe_str(args_dict.get("action")).lower()
        if action:
            out_acts = set(action_cfg.get("output_actions") or [])
            inp_acts = set(action_cfg.get("input_actions") or [])
            if action in out_acts:
                return "output"
            if action in inp_acts:
                return "input"
        return "output"  # unknown action => fail-closed / stricter

    # 3) direct tool-classification
    if name in output_tools:
        return "output"
    if name in input_tools:
        return "input"
    return "none"


def _friendly_taint_message(
    tool_name: str,
    kind: str,
    trust: str,
    required_level: str,
    reason: Optional[str] = None,
) -> str:
    if kind == "input":
        lines = [
            f"我没有执行工具 `{tool_name}`。",
            f"这一步属于“读取/获取信息”类操作。",
            f"当前请求的可信级别为 `{trust}`，而执行这一步至少需要 `{required_level}`。",
            "原因：当前请求的可信级别不足以读取这类信息。",
        ]
    else:
        lines = [
            f"我没有执行工具 `{tool_name}`。",
            f"这一步属于“输出/修改/外发”类操作。",
            f"当前请求的可信级别为 `{trust}`，而执行这一步至少需要 `{required_level}`。",
            "原因：当前请求的可信级别不足以进行这类输出或副作用操作。",
        ]

    if isinstance(reason, str) and reason.strip():
        lines.append(f"补充说明：{reason.strip()}")

    lines.append("如果你希望继续，请降低敏感度，或先完成所需的确认/授权步骤。")
    return "\n".join(lines)


def _friendly_respond_taint_message(
    trust: str,
    required_level: str,
    reason: Optional[str] = None,
) -> str:
    lines = [
        "我没有直接输出这条回复。",
        f"当前请求的可信级别为 `{trust}`，而当前响应至少需要 `{required_level}`。",
        "原因：按照 taint / propagated-security 规则，当前响应不能直接作为输出返回给用户。",
    ]
    if isinstance(reason, str) and reason.strip():
        lines.append(f"补充说明：{reason.strip()}")
    lines.append("如果你希望继续，请降低敏感度，或先经过更高权限/确认流程。")
    return "\n".join(lines)


def _should_treat_respond_as_sink(tp_cfg: Dict[str, Any]) -> bool:
    """
    Compatible with a couple of config styles:
    - taint.taint_policy.treat_respond_as_sink = true
    - taint.taint_policy.respond_as_output = true
    """
    return bool(tp_cfg.get("treat_respond_as_sink", False) or tp_cfg.get("respond_as_output", False))


def _respond_instruction_enabled(tp_cfg: Dict[str, Any]) -> bool:
    sinks = tp_cfg.get("instruction_sinks")
    if isinstance(sinks, list) and sinks:
        return "RESPOND" in {_safe_upper(x) for x in sinks if isinstance(x, str)}
    return True


def _fail_closed_on_missing_metadata(tp_cfg: Dict[str, Any]) -> bool:
    return bool(tp_cfg.get("fail_closed_on_missing_instruction_metadata", False))


class RelationalPolicy(Policy):
    """
    Relational taint / propagated-security policy.

    Boundary:
    - only reads kernel-lowered metadata
    - does NOT parse shell/path semantics itself

    Main logic:
    - Input-like actions:  trustworthiness >= confidentiality
    - Output-like actions: trustworthiness >= prop_confidentiality

    Optional extension:
    - RESPOND can also be treated as an output sink.
    """

    def check(
        self,
        instructions: List[Dict[str, Any]],
        current_response: Dict[str, Any],
        latest_instructions: List[Dict[str, Any]],
        trace_id: str,
        *,
        current_taint_status: Any = None,
        **kwargs: Any,
    ) -> PolicyCheckResult:
        policy_enabled = bool(kwargs.get("policy_enabled", True))

        response = dict(current_response)
        tool_calls = RUNTIME.extract_tool_calls(response)

        _, _, _, tp_cfg = _get_taint_policy_config()

        instr_by_tool_call_id: Dict[str, Dict[str, Any]] = {}
        for ins in latest_instructions or []:
            content = ins.get("content")
            if not isinstance(content, dict):
                continue
            tcid = content.get("tool_call_id")
            if isinstance(tcid, str) and tcid:
                instr_by_tool_call_id[tcid] = ins

        errors: List[str] = []
        inactive_errors: List[str] = []
        kept: List[Dict[str, Any]] = []

        # -------------------------------------------------------------------
        # Tool-call relational checks
        # -------------------------------------------------------------------
        for tc in tool_calls:
            tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}
            args_dict = canonicalize_args(args_dict)

            ins = instr_by_tool_call_id.get(tool_call_id or "")
            sec = _extract_instruction_security(ins or {}, current_taint_status=current_taint_status)

            kind = _classify_tool(tool_name, args_dict, sec)
            if kind == "none":
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                continue

            if not ins and _fail_closed_on_missing_metadata(tp_cfg):
                reason = "missing lowered instruction metadata for relational check"
                user_message = _friendly_taint_message(
                    tool_name,
                    kind,
                    sec["trustworthiness"],
                    sec["prop_confidentiality"] if kind == "output" else sec["confidentiality"],
                    reason,
                )
                RUNTIME.audit(
                    phase="policy.relational",
                    trace_id=trace_id,
                    tool=tool_name,
                    decision="BLOCK" if policy_enabled else "INACTIVE",
                    reason=reason,
                    args=args_dict,
                    extra={
                        "kind": kind,
                        "instruction_type": sec["instruction_type"],
                        "instruction_category": sec["instruction_category"],
                        "trustworthiness": sec["trustworthiness"],
                        "confidentiality": sec["confidentiality"],
                        "prop_confidentiality": sec["prop_confidentiality"],
                        "prop_trustworthiness": sec["prop_trustworthiness"],
                        "authority": sec["authority"],
                        "confidence": sec["confidence"],
                        "reversible": sec["reversible"],
                        "risk": sec["risk"],
                        "custom": sec["custom"],
                    },
                )
                if policy_enabled:
                    errors.append(user_message)
                else:
                    inactive_errors.append(user_message)
                    kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                continue

            trust = sec["trustworthiness"]
            conf = sec["confidentiality"]
            prop_conf = sec["prop_confidentiality"]

            if kind == "input":
                ok = _level_at_least(trust, conf)
                reason = (
                    f"trustworthiness < confidentiality ({trust} < {conf})"
                    if not ok
                    else None
                )
                user_message = _friendly_taint_message(tool_name, kind, trust, conf, reason)
            else:
                ok = _level_at_least(trust, prop_conf)
                reason = (
                    f"trustworthiness < prop_confidentiality ({trust} < {prop_conf})"
                    if not ok
                    else None
                )
                user_message = _friendly_taint_message(tool_name, kind, trust, prop_conf, reason)

            if ok:
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                continue

            RUNTIME.audit(
                phase="policy.relational",
                trace_id=trace_id,
                tool=tool_name,
                decision="BLOCK" if policy_enabled else "INACTIVE",
                reason=reason or "relational taint check failed",
                args=args_dict,
                extra={
                    "kind": kind,
                    "instruction_type": sec["instruction_type"],
                    "instruction_category": sec["instruction_category"],
                    "trustworthiness": trust,
                    "confidentiality": conf,
                    "prop_confidentiality": prop_conf,
                    "prop_trustworthiness": sec["prop_trustworthiness"],
                    "authority": sec["authority"],
                    "confidence": sec["confidence"],
                    "reversible": sec["reversible"],
                    "risk": sec["risk"],
                    "custom": sec["custom"],
                },
            )
            if policy_enabled:
                errors.append(user_message)
            else:
                inactive_errors.append(user_message)
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))

        if errors:
            response["tool_calls"] = kept if kept else None
            if not kept:
                response["function_call"] = None
                if not isinstance(response.get("content"), str) or not response.get("content"):
                    response["content"] = "\n\n".join(errors[:3])
            return PolicyCheckResult(
                modified=True,
                response=response,
                error_type="\n\n".join(errors),
                inactivate_error_type=None,
            )

        # -------------------------------------------------------------------
        # RESPOND-as-sink relational check
        # -------------------------------------------------------------------
        content = response.get("content")
        if (
            isinstance(content, str)
            and content.strip()
            and _should_treat_respond_as_sink(tp_cfg)
            and _respond_instruction_enabled(tp_cfg)
        ):
            respond_ins: Optional[Dict[str, Any]] = None
            for ins in reversed(latest_instructions or []):
                if _safe_upper(ins.get("instruction_type")) == "RESPOND":
                    respond_ins = ins
                    break

            sec = _extract_instruction_security(
                respond_ins or {},
                current_taint_status=current_taint_status,
            )
            trust = sec["trustworthiness"]
            prop_conf = sec["prop_confidentiality"]

            if respond_ins is None and _fail_closed_on_missing_metadata(tp_cfg):
                reason = "missing lowered RESPOND instruction metadata for relational check"
                user_msg = _friendly_respond_taint_message(trust, prop_conf, reason)
                RUNTIME.audit(
                    phase="policy.relational",
                    trace_id=trace_id,
                    tool="@instruction",
                    decision="BLOCK" if policy_enabled else "INACTIVE",
                    reason=reason,
                    args={},
                    extra={
                        "instruction_type": "RESPOND",
                        "instruction_category": sec["instruction_category"],
                        "trustworthiness": trust,
                        "confidentiality": sec["confidentiality"],
                        "prop_confidentiality": prop_conf,
                        "prop_trustworthiness": sec["prop_trustworthiness"],
                        "authority": sec["authority"],
                        "confidence": sec["confidence"],
                        "reversible": sec["reversible"],
                        "risk": sec["risk"],
                        "custom": sec["custom"],
                    },
                )
                if policy_enabled:
                    response["content"] = user_msg
                    return PolicyCheckResult(
                        modified=True,
                        response=response,
                        error_type=user_msg,
                        inactivate_error_type=None,
                    )
                inactive_errors.append(user_msg)
            else:
                ok = _level_at_least(trust, prop_conf)
                if not ok:
                    reason = f"RESPOND trustworthiness < prop_confidentiality ({trust} < {prop_conf})"
                    user_msg = _friendly_respond_taint_message(trust, prop_conf, reason)
                    RUNTIME.audit(
                        phase="policy.relational",
                        trace_id=trace_id,
                        tool="@instruction",
                        decision="BLOCK" if policy_enabled else "INACTIVE",
                        reason=reason,
                        args={},
                        extra={
                            "instruction_type": "RESPOND",
                            "instruction_category": sec["instruction_category"],
                            "trustworthiness": trust,
                            "confidentiality": sec["confidentiality"],
                            "prop_confidentiality": prop_conf,
                            "prop_trustworthiness": sec["prop_trustworthiness"],
                            "authority": sec["authority"],
                            "confidence": sec["confidence"],
                            "reversible": sec["reversible"],
                            "risk": sec["risk"],
                            "custom": sec["custom"],
                        },
                    )
                    if policy_enabled:
                        response["content"] = user_msg
                        return PolicyCheckResult(
                            modified=True,
                            response=response,
                            error_type=user_msg,
                            inactivate_error_type=None,
                        )
                    inactive_errors.append(user_msg)

        if inactive_errors:
            return PolicyCheckResult(
                modified=False,
                response=current_response,
                error_type=None,
                inactivate_error_type="\n\n".join(inactive_errors),
            )

        return PolicyCheckResult(
            modified=False,
            response=current_response,
            error_type=None,
            inactivate_error_type=None,
        )