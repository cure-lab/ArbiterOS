from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from arbiteros_kernel.instruction_parsing.types import LEVEL_ORDER
from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy


# ---------------------------------------------------------------------------
# Kernel-aligned action groups
# ---------------------------------------------------------------------------

_BROWSER_READ_ACTIONS = {
    "status",
    "profiles",
    "tabs",
    "snapshot",
    "screenshot",
    "console",
    "pdf",
}
_BROWSER_LOW_RISK_ACTIONS = {"dialog"}
_BROWSER_SIDE_EFFECT_ACTIONS = {
    "open",
    "focus",
    "close",
    "navigate",
    "upload",
    "act",
}

_PROCESS_READ_ACTIONS = {"list", "poll", "log"}

_CRON_READ_ACTIONS = {"status", "list", "runs"}
_CRON_PERSIST_ACTIONS = {"add", "update", "remove", "run", "wake"}

_GATEWAY_READ_ACTIONS = {"config.get", "config.schema"}
_GATEWAY_WRITE_ACTIONS = {"config.apply", "config.patch"}

_CANVAS_READ_ACTIONS = {"snapshot"}

_NODES_READ_ACTIONS = {
    "status",
    "describe",
    "camera_snap",
    "camera_list",
    "camera_clip",
    "screen_record",
    "location_get",
}

_MESSAGE_EDIT_ACTIONS = {"edit"}

# Path-like sinks that are no longer "purely local private workspace materialization"
_SHARED_OR_EXPORTED_PATH_HINTS = (
    "/shared/",
    "/public/",
    "/publish/",
    "/published/",
    "/export/",
    "/exports/",
    "/outbox/",
    "/upload/",
    "/uploads/",
    "/dist/",
    "/artifacts/",
    "/release/",
    "/releases/",
    "/www/",
    "/tmp/",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_levelish(v: Any) -> Any:
    if hasattr(v, "name") and isinstance(getattr(v, "name"), str):
        return getattr(v, "name")
    return v


def _safe_str(v: Any, default: str = "") -> str:
    return v.strip() if isinstance(v, str) and v.strip() else default


def _safe_upper(v: Any, default: str = "") -> str:
    s = _safe_str(_coerce_levelish(v), default)
    return s.upper() if s else default


def _safe_level(v: Any, default: str = "UNKNOWN") -> str:
    s = _safe_upper(v, default)
    return s if s in LEVEL_ORDER else default


def _level_rank(v: Any) -> float:
    return LEVEL_ORDER.get(_safe_level(v), 0.5)


def _level_at_least(actual: Any, required: Any) -> bool:
    return _level_rank(actual) >= _level_rank(required)


def _level_max(a: Any, b: Any) -> str:
    return _safe_level(a) if _level_rank(a) >= _level_rank(b) else _safe_level(b)


def _soft_source_conf(level: str) -> str:
    """
    For some outward sinks, treating UNKNOWN as HIGH is too aggressive and
    recreates the old false-positive pattern. We soften UNKNOWN -> LOW here.
    """
    lv = _safe_level(level)
    return "LOW" if lv == "UNKNOWN" else lv


def _looks_external_ref(v: str) -> bool:
    s = _safe_str(v).lower()
    return s.startswith("http://") or s.startswith("https://")


def _extract_instruction_security(ins: Dict[str, Any]) -> Dict[str, Any]:
    """
    Only consume kernel-lowered metadata.
    No deep semantic parsing in the policy itself.
    """
    st = ins.get("security_type") if isinstance(ins, dict) else {}
    st = st if isinstance(st, dict) else {}
    custom = st.get("custom")
    custom = custom if isinstance(custom, dict) else {}

    return {
        "instruction_type": _safe_upper(ins.get("instruction_type")),
        "instruction_category": _safe_str(ins.get("instruction_category")),
        "trustworthiness": _safe_level(st.get("trustworthiness")),
        "confidentiality": _safe_level(st.get("confidentiality")),
        "prop_confidentiality": _safe_level(
            st.get("prop_confidentiality") or st.get("confidentiality")
        ),
        "prop_trustworthiness": _safe_level(
            st.get("prop_trustworthiness") or st.get("trustworthiness")
        ),
        "authority": _safe_upper(st.get("authority"), "UNKNOWN"),
        "confidence": _safe_level(st.get("confidence")),
        "reversible": bool(st.get("reversible", False)),
        "risk": _safe_upper(st.get("risk"), "UNKNOWN"),
        "custom": custom,
    }


def _source_levels(
    sec: Dict[str, Any],
    current_taint_status: Any = None,
) -> Tuple[str, str]:
    """
    Source-side levels used for flow decisions.
    Prefer session taint if present, because many sink tools carry static
    sink-side metadata that is too coarse to reflect the payload being sent.
    """
    default_conf = sec.get("prop_confidentiality") or sec.get("confidentiality") or "UNKNOWN"
    default_trust = sec.get("prop_trustworthiness") or sec.get("trustworthiness") or "UNKNOWN"

    if current_taint_status is None:
        return _safe_level(default_trust), _safe_level(default_conf)

    sess_trust = _safe_level(
        _coerce_levelish(getattr(current_taint_status, "trustworthiness", None)),
        _safe_level(default_trust),
    )
    sess_conf = _safe_level(
        _coerce_levelish(getattr(current_taint_status, "confidentiality", None)),
        _safe_level(default_conf),
    )
    return sess_trust, sess_conf


def _get_primary_path_hint(args_dict: Dict[str, Any]) -> str:
    for key in (
        "path",
        "file_path",
        "target_path",
        "destination_path",
        "dest_path",
        "output_path",
        "path_out",
        "dst",
    ):
        val = args_dict.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _looks_shared_or_exported_sink(path_hint: str) -> bool:
    p = _safe_str(path_hint).lower()
    if not p:
        return False
    return any(h in p for h in _SHARED_OR_EXPORTED_PATH_HINTS)


def _flow_kind(
    tool_name: str,
    args_dict: Dict[str, Any],
    sec: Dict[str, Any],
) -> str:
    """
    Finer-grained flow classification.

    Key idea:
    - READ of external/untrusted content should not be treated the same as
      READ of local sensitive state.
    - Local writes should not be treated the same as outward / delegated /
      human-visible sinks.
    - Side-effecting control actions should be gated on source trust, not just
      sink confidentiality.
    """
    name = _safe_str(tool_name).lower()
    action = _safe_str(args_dict.get("action")).lower()
    itype = _safe_upper(sec.get("instruction_type"))

    if not name:
        return "none"

    # Pure state / status reads
    if name in {"session_status", "sessions_list", "agents_list"}:
        return "read_state"
    if name == "process" and action in _PROCESS_READ_ACTIONS:
        return "read_state"
    if name == "cron" and action in _CRON_READ_ACTIONS:
        return "read_state"
    if name == "canvas" and action in _CANVAS_READ_ACTIONS:
        return "read_state"

    # Browser / web / external content reads
    if name == "browser":
        if action in _BROWSER_READ_ACTIONS:
            return "read_external"
        if action in _BROWSER_LOW_RISK_ACTIONS:
            return "read_state"
        if action in _BROWSER_SIDE_EFFECT_ACTIONS:
            return "ui_side_effect"
    if name in {"web_search", "web_fetch"}:
        return "read_external"
    if name == "image":
        image_src = _safe_str(args_dict.get("image") or args_dict.get("path"))
        return "read_external" if _looks_external_ref(image_src) else "read_sensitive"

    # Sensor / remote data reads
    if name == "nodes":
        if action in _NODES_READ_ACTIONS:
            return "read_sensitive"
        return "exec_side_effect"

    # Gateway
    if name == "gateway":
        if action in _GATEWAY_READ_ACTIONS:
            return "read_sensitive"
        if action in _GATEWAY_WRITE_ACTIONS:
            # Keep gateway config patch/apply in a local-write bucket here.
            # Semantic proxy/attacker rules belong in unary/protected-target rules.
            return "write_local"
        return "exec_side_effect"

    # Delegate / outward task dispatch
    if name in {"sessions_send", "sessions_spawn"} or itype == "DELEGATE":
        return "delegate_sink"

    # Message / voice channels are outward human-visible sinks
    if name == "message":
        if action in _MESSAGE_EDIT_ACTIONS:
            return "write_local"
        return "comm_sink"
    if name == "tts":
        return "voice_sink"

    # Scheduler persistence
    if name == "cron" and action in _CRON_PERSIST_ACTIONS:
        return "persist_side_effect"

    # File-like reads
    if name in {"read", "memory_search", "memory_get", "sessions_history"}:
        return "read_sensitive"
    if itype in {"READ", "RETRIEVE"}:
        return "read_sensitive"

    # File-like writes
    if name in {"write", "edit"} or itype in {"WRITE", "STORE"}:
        path_hint = _get_primary_path_hint(args_dict)
        if _looks_shared_or_exported_sink(path_hint):
            return "write_shared"
        return "write_local"

    # Ask/wait is not a sink
    if itype in {"WAIT", "ASK"}:
        return "read_state"

    # Shell / process / generic side effects
    if name in {"exec", "process"} or itype == "EXEC":
        return "exec_side_effect"

    return "none"


def _friendly_message(
    tool_name: str,
    flow_kind: str,
    source_trust: str,
    source_conf: str,
    required: str,
    reason: Optional[str] = None,
) -> str:
    if flow_kind in {"read_external", "read_sensitive", "read_state"}:
        head = [
            f"我没有执行工具 `{tool_name}`。",
            "这一步属于“读取/获取信息”类操作。",
            f"当前来源的可信级别为 `{source_trust}`，当前数据/动作要求至少 `{required}`。",
        ]
    elif flow_kind in {"write_local", "write_shared"}:
        head = [
            f"我没有执行工具 `{tool_name}`。",
            "这一步属于“写入/落盘”类操作。",
            f"当前来源的可信级别为 `{source_trust}`，当前数据保密级别为 `{source_conf}`，要求至少 `{required}`。",
        ]
    elif flow_kind in {"delegate_sink", "comm_sink", "voice_sink"}:
        head = [
            f"我没有执行工具 `{tool_name}`。",
            "这一步属于“对外发送 / 委托 / 可见输出”类操作。",
            f"当前来源的可信级别为 `{source_trust}`，当前数据保密级别为 `{source_conf}`，要求至少 `{required}`。",
        ]
    else:
        head = [
            f"我没有执行工具 `{tool_name}`。",
            "这一步属于“有副作用的控制/执行”类操作。",
            f"当前来源的可信级别为 `{source_trust}`，而执行这一步至少需要 `{required}`。",
        ]

    if isinstance(reason, str) and reason.strip():
        head.append(f"补充说明：{reason.strip()}")

    head.append("如果你希望继续，请提高来源可信度，降低敏感度，或补充确认/审批流程。")
    return "\n".join(head)


def _friendly_respond_message(
    source_trust: str,
    source_conf: str,
    required: str,
    reason: Optional[str] = None,
) -> str:
    lines = [
        "我没有直接输出这条回复。",
        f"当前来源的可信级别为 `{source_trust}`，当前内容保密级别为 `{source_conf}`，而直接输出至少需要 `{required}`。",
        "原因：按照 relational flow 规则，这条内容当前不适合作为直接对外可见输出返回。",
    ]
    if isinstance(reason, str) and reason.strip():
        lines.append(f"补充说明：{reason.strip()}")
    lines.append("如果你希望继续，请降低敏感度，或先经过更高权限/确认流程。")
    return "\n".join(lines)


def _should_treat_respond_as_sink(tp_cfg: Dict[str, Any]) -> bool:
    return bool(tp_cfg.get("treat_respond_as_sink", False) or tp_cfg.get("respond_as_output", False))


def _respond_instruction_enabled(tp_cfg: Dict[str, Any]) -> bool:
    sinks = tp_cfg.get("instruction_sinks")
    if isinstance(sinks, list) and sinks:
        return "RESPOND" in {_safe_upper(x) for x in sinks if isinstance(x, str)}
    return True


def _fail_closed_on_missing_metadata(tp_cfg: Dict[str, Any]) -> bool:
    return bool(tp_cfg.get("fail_closed_on_missing_instruction_metadata", False))


def _get_taint_cfg() -> Dict[str, Any]:
    ta = RUNTIME.cfg.get("taint") or {}
    return ta if isinstance(ta, dict) else {}


def _get_taint_policy_cfg() -> Dict[str, Any]:
    ta = _get_taint_cfg()
    tp = ta.get("taint_policy")
    return tp if isinstance(tp, dict) else {}


def _evaluate_flow(
    flow_kind: str,
    sec: Dict[str, Any],
    args_dict: Dict[str, Any],
    current_taint_status: Any = None,
) -> Tuple[bool, str, str, Dict[str, Any]]:
    """
    Return:
      (ok, actual_level, required_level, extra_audit_fields)
    """
    source_trust, source_conf = _source_levels(sec, current_taint_status=current_taint_status)
    sink_trust = _safe_level(sec.get("trustworthiness"))
    conf = _safe_level(sec.get("confidentiality"))
    risk = _safe_upper(sec.get("risk"), "UNKNOWN")

    extra = {
        "flow_kind": flow_kind,
        "source_trustworthiness": source_trust,
        "source_confidentiality": source_conf,
        "sink_trustworthiness": sink_trust,
        "instruction_confidentiality": conf,
        "prop_confidentiality": _safe_level(sec.get("prop_confidentiality")),
        "risk": risk,
        "instruction_type": _safe_upper(sec.get("instruction_type")),
        "instruction_category": _safe_str(sec.get("instruction_category")),
        "authority": _safe_upper(sec.get("authority"), "UNKNOWN"),
        "confidence": _safe_level(sec.get("confidence")),
        "reversible": bool(sec.get("reversible", False)),
        "custom": sec.get("custom", {}),
        "action": _safe_str(args_dict.get("action")),
        "path_hint": _get_primary_path_hint(args_dict),
    }

    # 1) External/browser/web reads:
    # allow low-trust reads of external content so they can be analyzed later;
    # don't recreate the old "LOW < UNKNOWN => block read of public page state" issue.
    if flow_kind == "read_external":
        actual = _level_max(sink_trust, "LOW")
        required = "LOW"
        return _level_at_least(actual, required), actual, required, extra

    # 2) Local/sensitive reads: keep classical trust >= confidentiality.
    if flow_kind == "read_sensitive":
        actual = sink_trust
        required = conf
        return _level_at_least(actual, required), actual, required, extra

    # 3) Pure state/status reads: always allow.
    if flow_kind == "read_state":
        return True, source_trust, "LOW", extra

    # 4) Local writes/materialization:
    # explicit local/private workspace materialization should not be treated
    # the same as shared/exported/public sinks.
    if flow_kind == "write_local":
        path_hint = _get_primary_path_hint(args_dict)

        if (
            path_hint
            and not _looks_shared_or_exported_sink(path_hint)
            and not _looks_external_ref(path_hint)
        ):
            actual = _level_max(sink_trust, "HIGH")
            required = _soft_source_conf(source_conf)
            return _level_at_least(actual, required), actual, required, extra

        actual = sink_trust
        required = source_conf
        return _level_at_least(actual, required), actual, required, extra

    # 5) Writes to shared/export-ish sinks:
    # treat sink trust as at most UNKNOWN even if the file path itself lives locally.
    if flow_kind == "write_shared":
        actual = "UNKNOWN"
        required = _level_max(_soft_source_conf(source_conf), "UNKNOWN")
        return _level_at_least(actual, required), actual, required, extra

    # 6) Cross-session delegation:
    # another agent session is only partially trusted; use LOW sink trust.
    if flow_kind == "delegate_sink":
        actual = "LOW"
        required = _soft_source_conf(source_conf)
        return _level_at_least(actual, required), actual, required, extra

    # 7) Human-visible outbound communication:
    # treat generic message/mail-like sinks as UNKNOWN trust.
    if flow_kind == "comm_sink":
        actual = "UNKNOWN"
        required = _soft_source_conf(source_conf)
        return _level_at_least(actual, required), actual, required, extra

    # 8) Voice/audio output is even harder to retract/control.
    if flow_kind == "voice_sink":
        actual = "LOW"
        required = _soft_source_conf(source_conf)
        return _level_at_least(actual, required), actual, required, extra

    # 9) Browser/UI side effects:
    # gate mainly on source trust, because the risk is "acting on low-trust state".
    if flow_kind == "ui_side_effect":
        actual = source_trust
        required = "MID"
        if source_conf == "HIGH" or risk in {"HIGH", "CRITICAL"}:
            required = "HIGH"
        return _level_at_least(actual, required), actual, required, extra

    # 10) Generic execution/process side effects:
    # also source-trust driven.
    if flow_kind == "exec_side_effect":
        actual = source_trust
        required = "MID"
        if source_conf == "HIGH" or risk in {"HIGH", "CRITICAL"}:
            required = "HIGH"
        return _level_at_least(actual, required), actual, required, extra

    # 11) Persistence (cron/reminder-like scheduled side effects):
    # storing medium/high-conf content into persistent jobs is disallowed here.
    if flow_kind == "persist_side_effect":
        actual = source_trust
        if source_conf in {"MID", "HIGH"}:
            return False, actual, source_conf, extra
        required = "MID"
        return _level_at_least(actual, required), actual, required, extra

    # Fallback: allow
    return True, source_trust, "LOW", extra


class RelationalPolicy(Policy):
    """
    Flow-aware relational policy.

    Design goals:
    - still only consume kernel-lowered metadata + shallow tool/action/path hints
    - avoid the old coarse input/output split
    - use session/source taint for content-carrying sinks (delegate/comm/persist)
    - use source trust for side-effecting control actions (browser/exec/process)
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
        response = dict(current_response)
        tool_calls = RUNTIME.extract_tool_calls(response)
        tp_cfg = _get_taint_policy_cfg()

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

        # -------------------------------------------------------------------
        # Tool-call flow checks
        # -------------------------------------------------------------------
        for tc in tool_calls:
            tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}
            args_dict = canonicalize_args(args_dict)

            ins = instr_by_tool_call_id.get(tool_call_id or "")
            sec = _extract_instruction_security(ins or {})
            flow_kind = _flow_kind(tool_name, args_dict, sec)

            if flow_kind == "none":
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                continue

            if not ins and _fail_closed_on_missing_metadata(tp_cfg):
                source_trust, source_conf = _source_levels(sec, current_taint_status=current_taint_status)
                required = "UNKNOWN"
                reason = "missing lowered instruction metadata for relational flow check"
                user_message = _friendly_message(
                    tool_name,
                    flow_kind,
                    source_trust,
                    source_conf,
                    required,
                    reason,
                )
                RUNTIME.audit(
                    phase="policy.relational",
                    trace_id=trace_id,
                    tool=tool_name,
                    decision="BLOCK",
                    reason=reason,
                    args=args_dict,
                    extra={
                        "flow_kind": flow_kind,
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
                errors.append(user_message)
                continue

            ok, actual, required, extra = _evaluate_flow(
                flow_kind,
                sec,
                args_dict,
                current_taint_status=current_taint_status,
            )
            source_trust = extra["source_trustworthiness"]
            source_conf = extra["source_confidentiality"]

            if ok:
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                continue

            if flow_kind == "read_sensitive":
                reason = (
                    f"sink trustworthiness < confidentiality "
                    f"({actual} < {required})"
                )
            elif flow_kind in {"write_local", "write_shared", "delegate_sink", "comm_sink", "voice_sink"}:
                reason = (
                    f"sink trustworthiness < source confidentiality "
                    f"({actual} < {required})"
                )
            elif flow_kind in {"ui_side_effect", "exec_side_effect", "persist_side_effect"}:
                reason = (
                    f"source trustworthiness < required level for side-effecting action "
                    f"({actual} < {required})"
                )
            else:
                reason = f"relational flow check failed ({actual} < {required})"

            user_message = _friendly_message(
                tool_name,
                flow_kind,
                source_trust,
                source_conf,
                required,
                reason,
            )

            RUNTIME.audit(
                phase="policy.relational",
                trace_id=trace_id,
                tool=tool_name,
                decision="BLOCK",
                reason=reason,
                args=args_dict,
                extra=extra,
            )
            errors.append(user_message)

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
        # RESPOND-as-sink check
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

            sec = _extract_instruction_security(respond_ins or {})
            source_trust, source_conf = _source_levels(
                sec,
                current_taint_status=current_taint_status,
            )

            if respond_ins is None and _fail_closed_on_missing_metadata(tp_cfg):
                required = "UNKNOWN"
                reason = "missing lowered RESPOND instruction metadata for relational flow check"
                user_msg = _friendly_respond_message(source_trust, source_conf, required, reason)
                RUNTIME.audit(
                    phase="policy.relational",
                    trace_id=trace_id,
                    tool="@instruction",
                    decision="BLOCK",
                    reason=reason,
                    args={},
                    extra={
                        "flow_kind": "respond_sink",
                        "source_trustworthiness": source_trust,
                        "source_confidentiality": source_conf,
                        "instruction_type": "RESPOND",
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
                response["content"] = user_msg
                return PolicyCheckResult(
                    modified=True,
                    response=response,
                    error_type=user_msg,
                    inactivate_error_type=None,
                )

            # Treat direct response as UNKNOWN-trust human-visible sink.
            actual = "UNKNOWN"
            required = _soft_source_conf(source_conf)

            if not _level_at_least(actual, required):
                reason = f"respond sink trustworthiness < source confidentiality ({actual} < {required})"
                user_msg = _friendly_respond_message(source_trust, source_conf, required, reason)
                RUNTIME.audit(
                    phase="policy.relational",
                    trace_id=trace_id,
                    tool="@instruction",
                    decision="BLOCK",
                    reason=reason,
                    args={},
                    extra={
                        "flow_kind": "respond_sink",
                        "source_trustworthiness": source_trust,
                        "source_confidentiality": source_conf,
                        "sink_trustworthiness": actual,
                        "instruction_type": "RESPOND",
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
                response["content"] = user_msg
                return PolicyCheckResult(
                    modified=True,
                    response=response,
                    error_type=user_msg,
                    inactivate_error_type=None,
                )

        return PolicyCheckResult(
            modified=False,
            response=current_response,
            error_type=None,
            inactivate_error_type=None,
        )