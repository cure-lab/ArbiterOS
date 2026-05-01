from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from arbiteros_kernel.policy_runtime import RUNTIME

_DEFAULT_PROMPT_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "override your earlier rules",
    "override previous rules",
    "assistant directive",
    "<urgent>",
    "system prompt",
    "developer message",
    "you are chatgpt",
    "you are an ai agent",
    "follow these steps",
)

_DEFAULT_PREEXEC_HIGH_SIDE_EFFECT_TOOLS = frozenset(
    {
        "exec",
        "message",
        "sessions_send",
        "sessions_spawn",
        "tts",
        "gateway",
        "cron",
        "process",
        "browser",
        "write",
        "edit",
    }
)

_DEFAULT_PREEXEC_HIGH_SIDE_EFFECT_BROWSER_ACTIONS = frozenset(
    {"act", "upload", "dialog", "navigate"}
)
_DEFAULT_PREEXEC_HIGH_SIDE_EFFECT_PROCESS_ACTIONS = frozenset(
    {"submit", "send-keys", "paste", "kill", "write"}
)
_DEFAULT_PREEXEC_HIGH_SIDE_EFFECT_CRON_ACTIONS = frozenset(
    {"add", "update", "remove", "run", "wake"}
)
_DEFAULT_PREEXEC_HIGH_SIDE_EFFECT_WRITE_HINTS = (
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
)

_DEFAULT_INGRESS_TOOLS = frozenset({"web_fetch", "web_search", "image", "browser"})
_DEFAULT_BROWSER_INGRESS_ACTIONS = frozenset(
    {"snapshot", "console", "pdf", "screenshot"}
)

_DEFAULT_SOURCE_EXCERPT_CHARS = 400
_DEFAULT_PREEXEC_ARG_TEXT_CHARS = 1000
_DEFAULT_POSTEXEC_FORCE_TRIGGER_CHARS = 8000
_DEFAULT_POSTEXEC_UNKNOWN_SOURCE_CHARS = 2000
_DEFAULT_POSTEXEC_STRUCTURED_TEXT_CHARS = 1200

_HTML_OR_MARKUP_RE = re.compile(
    r"(?i)<(?:html|body|script|style|meta|iframe|form|input)\b|```|^#\s|\[[^\]]+\]\([^)]+\)"
)
_CONSOLE_OR_LOG_RE = re.compile(
    r"(?im)^(?:error|warn|traceback|exception|console\.|at\s+\S+\s+\(|INFO|DEBUG|WARNING|CRITICAL)\b"
)
_SCRIPTISH_RE = re.compile(
    r"(?i)\b(?:curl|wget|bash|sh|powershell|cmd\.exe|javascript:|fetch\(|document\.|window\.)\b"
)


@dataclass
class SentinelSourceContext:
    tool_call_id: str
    source_tool: str
    source_trustworthiness: str
    source_confidentiality: str
    excerpt: str
    ingress_like: bool


@dataclass
class PreexecTriggerDecision:
    run: bool
    reasons: list[str] = field(default_factory=list)
    reviewed_tool_call_ids: list[str] = field(default_factory=list)
    reviewed_ops: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PostexecTriggerDecision:
    run: bool
    reasons: list[str] = field(default_factory=list)
    ingress_like: bool = False
    prompt_injection_marker_hit: bool = False
    oversized: bool = False
    unknown_source: bool = False
    semi_structured: bool = False


def _cfg_block() -> dict[str, Any]:
    cfg = getattr(RUNTIME, "cfg", {}) or {}
    block = cfg.get("alignment_sentinel_trigger")
    return block if isinstance(block, dict) else {}


def _preexec_cfg() -> dict[str, Any]:
    root = _cfg_block()
    block = root.get("preexec")
    if not isinstance(block, dict):
        block = {}
    return {
        "enabled": bool(root.get("enabled", True)) and bool(block.get("enabled", True)),
        "high_side_effect_tools": frozenset(
            str(x).strip().lower()
            for x in block.get("high_side_effect_tools", _DEFAULT_PREEXEC_HIGH_SIDE_EFFECT_TOOLS)
            if isinstance(x, str) and x.strip()
        ),
        "high_side_effect_browser_actions": frozenset(
            str(x).strip().lower()
            for x in block.get(
                "high_side_effect_browser_actions",
                _DEFAULT_PREEXEC_HIGH_SIDE_EFFECT_BROWSER_ACTIONS,
            )
            if isinstance(x, str) and x.strip()
        ),
        "high_side_effect_process_actions": frozenset(
            str(x).strip().lower()
            for x in block.get(
                "high_side_effect_process_actions",
                _DEFAULT_PREEXEC_HIGH_SIDE_EFFECT_PROCESS_ACTIONS,
            )
            if isinstance(x, str) and x.strip()
        ),
        "high_side_effect_cron_actions": frozenset(
            str(x).strip().lower()
            for x in block.get(
                "high_side_effect_cron_actions",
                _DEFAULT_PREEXEC_HIGH_SIDE_EFFECT_CRON_ACTIONS,
            )
            if isinstance(x, str) and x.strip()
        ),
        "write_path_hints": tuple(
            str(x).strip().lower()
            for x in block.get(
                "write_path_hints",
                _DEFAULT_PREEXEC_HIGH_SIDE_EFFECT_WRITE_HINTS,
            )
            if isinstance(x, str) and x.strip()
        ),
        "prompt_injection_markers": tuple(
            str(x).strip().lower()
            for x in block.get("prompt_injection_markers", _DEFAULT_PROMPT_INJECTION_MARKERS)
            if isinstance(x, str) and x.strip()
        ),
        "max_source_excerpt_chars": int(
            block.get("max_source_excerpt_chars", _DEFAULT_SOURCE_EXCERPT_CHARS)
        ),
        "arg_text_trigger_chars": int(
            block.get("arg_text_trigger_chars", _DEFAULT_PREEXEC_ARG_TEXT_CHARS)
        ),
    }


def _postexec_cfg() -> dict[str, Any]:
    root = _cfg_block()
    block = root.get("postexec")
    if not isinstance(block, dict):
        block = {}
    return {
        "enabled": bool(root.get("enabled", True)) and bool(block.get("enabled", True)),
        "ingress_tools": frozenset(
            str(x).strip().lower()
            for x in block.get("ingress_tools", _DEFAULT_INGRESS_TOOLS)
            if isinstance(x, str) and x.strip()
        ),
        "browser_ingress_actions": frozenset(
            str(x).strip().lower()
            for x in block.get(
                "browser_ingress_actions",
                _DEFAULT_BROWSER_INGRESS_ACTIONS,
            )
            if isinstance(x, str) and x.strip()
        ),
        "prompt_injection_markers": tuple(
            str(x).strip().lower()
            for x in block.get("prompt_injection_markers", _DEFAULT_PROMPT_INJECTION_MARKERS)
            if isinstance(x, str) and x.strip()
        ),
        "max_body_chars_before_force_trigger": int(
            block.get(
                "max_body_chars_before_force_trigger",
                _DEFAULT_POSTEXEC_FORCE_TRIGGER_CHARS,
            )
        ),
        "unknown_source_min_chars": int(
            block.get("unknown_source_min_chars", _DEFAULT_POSTEXEC_UNKNOWN_SOURCE_CHARS)
        ),
        "structured_text_min_chars": int(
            block.get(
                "structured_text_min_chars",
                _DEFAULT_POSTEXEC_STRUCTURED_TEXT_CHARS,
            )
        ),
    }


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _safe_upper(value: Any, default: str = "UNKNOWN") -> str:
    s = _safe_str(value)
    if not s:
        return default
    return s.upper()


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _collect_text_fragments(value: Any, *, limit: int = 4096) -> str:
    parts: list[str] = []

    def _walk(node: Any) -> None:
        if sum(len(p) for p in parts) >= limit:
            return
        if isinstance(node, str):
            if node.strip():
                parts.append(node.strip())
            return
        if isinstance(node, list):
            for item in node:
                _walk(item)
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if str(k).lower() in {"reference_tool_id", "tool_call_id", "_arbiteros_raw_tool_name"}:
                    continue
                _walk(v)

    _walk(value)
    out = "\n".join(parts)
    if len(out) > limit:
        return out[:limit]
    return out


def _contains_prompt_injection_markers(text: str, markers: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in markers)


def _looks_semi_structured_untrusted_text(text: str) -> bool:
    if not text.strip():
        return False
    if _HTML_OR_MARKUP_RE.search(text):
        return True
    if _CONSOLE_OR_LOG_RE.search(text):
        return True
    if _SCRIPTISH_RE.search(text):
        return True
    return False


def _extract_instruction_for_tool_call(
    instructions: list[dict[str, Any]],
    tool_call_id: str,
) -> Optional[dict[str, Any]]:
    target = _safe_str(tool_call_id)
    if not target:
        return None
    for ins in reversed(instructions or []):
        if not isinstance(ins, dict):
            continue
        content = ins.get("content")
        if not isinstance(content, dict):
            continue
        tc_id = _safe_str(content.get("tool_call_id"))
        if tc_id == target:
            return ins
    return None


def _extract_result_payload(instr: dict[str, Any]) -> Any:
    content = instr.get("content")
    if isinstance(content, dict):
        for key in ("result", "tool_result", "output", "response", "value", "content"):
            if key in content:
                return content.get(key)
    for key in ("result", "tool_result", "output", "response", "value"):
        if key in instr:
            return instr.get(key)
    return None


def _excerpt_payload(value: Any, *, max_chars: int) -> str:
    text = _coerce_text(value).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _is_browser_ingress(tool_name: str, args_dict: dict[str, Any], cfg: dict[str, Any]) -> bool:
    if tool_name != "browser":
        return False
    action = _safe_str(args_dict.get("action")).lower()
    return action in cfg["browser_ingress_actions"]


def _is_ingress_source(
    tool_name: str,
    args_dict: dict[str, Any],
    *,
    instruction_type: str,
    trustworthiness: str,
    cfg: dict[str, Any],
) -> bool:
    if tool_name in cfg["ingress_tools"]:
        if tool_name == "browser":
            return _is_browser_ingress(tool_name, args_dict, cfg)
        return True
    if instruction_type in {"READ", "RETRIEVE"} and trustworthiness in {"LOW", "UNKNOWN"}:
        return True
    return False


def _is_high_side_effect_sink(
    tool_name: str,
    args_dict: dict[str, Any],
    *,
    instruction_type: str,
    cfg: dict[str, Any],
) -> bool:
    name = tool_name.lower()
    if name not in cfg["high_side_effect_tools"]:
        return False
    if name == "browser":
        action = _safe_str(args_dict.get("action")).lower()
        return action in cfg["high_side_effect_browser_actions"]
    if name == "process":
        action = _safe_str(args_dict.get("action")).lower()
        return action in cfg["high_side_effect_process_actions"]
    if name == "cron":
        action = _safe_str(args_dict.get("action")).lower()
        return action in cfg["high_side_effect_cron_actions"]
    if name in {"write", "edit"} or instruction_type in {"WRITE", "STORE"}:
        path_hint = _safe_str(
            args_dict.get("path")
            or args_dict.get("file_path")
            or args_dict.get("target_path")
            or args_dict.get("destination_path")
            or args_dict.get("output_path")
            or args_dict.get("dst")
        ).lower()
        return any(hint in path_hint for hint in cfg["write_path_hints"])
    return True


def _source_contexts_for_op(
    *,
    op_args: dict[str, Any],
    instructions: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> list[SentinelSourceContext]:
    ref_ids = op_args.get("reference_tool_id")
    if not isinstance(ref_ids, list):
        return []
    out: list[SentinelSourceContext] = []
    for ref_id in ref_ids:
        target = _safe_str(ref_id)
        if not target:
            continue
        source_instr = _extract_instruction_for_tool_call(instructions, target)
        if not isinstance(source_instr, dict):
            continue
        content = _safe_dict(source_instr.get("content"))
        st = _safe_dict(source_instr.get("security_type"))
        source_tool = _safe_str(content.get("tool_name")) or "unknown_tool"
        source_args = _safe_dict(content.get("arguments"))
        source_trust = _safe_upper(
            st.get("prop_trustworthiness") or st.get("trustworthiness"),
            "UNKNOWN",
        )
        source_conf = _safe_upper(
            st.get("prop_confidentiality") or st.get("confidentiality"),
            "UNKNOWN",
        )
        instruction_type = _safe_upper(source_instr.get("instruction_type"), "EXEC")
        payload = _extract_result_payload(source_instr)
        ingress_like = _is_ingress_source(
            source_tool.lower(),
            source_args,
            instruction_type=instruction_type,
            trustworthiness=source_trust,
            cfg=_postexec_cfg(),
        )
        out.append(
            SentinelSourceContext(
                tool_call_id=target,
                source_tool=source_tool,
                source_trustworthiness=source_trust,
                source_confidentiality=source_conf,
                excerpt=_excerpt_payload(
                    payload if payload is not None else source_args,
                    max_chars=max(64, cfg["max_source_excerpt_chars"]),
                ),
                ingress_like=ingress_like,
            )
        )
    return out


def should_trigger_preexec_sentinel(
    *,
    instructions: list[dict[str, Any]],
    latest_instructions: list[dict[str, Any]],
    current_response: dict[str, Any],
    planned_ops: list[dict[str, Any]],
) -> PreexecTriggerDecision:
    cfg = _preexec_cfg()
    if not cfg["enabled"]:
        return PreexecTriggerDecision(
            run=bool(planned_ops),
            reasons=["trigger_disabled_legacy_full_review"],
            reviewed_tool_call_ids=[
                _safe_str(op.get("tool_call_id")) for op in planned_ops if _safe_str(op.get("tool_call_id"))
            ],
            reviewed_ops=list(planned_ops),
        )

    full_instructions = list(instructions or [])
    current_tail = list(latest_instructions or [])
    reviewed_ops: list[dict[str, Any]] = []
    reviewed_ids: list[str] = []
    decision_reasons: list[str] = []

    for op in planned_ops or []:
        tool_name = _safe_str(op.get("name")).lower()
        tool_call_id = _safe_str(op.get("tool_call_id"))
        args_dict = _safe_dict(op.get("args"))
        current_instr = _extract_instruction_for_tool_call(current_tail, tool_call_id) or _extract_instruction_for_tool_call(full_instructions, tool_call_id)

        instruction_type = _safe_upper(
            current_instr.get("instruction_type") if isinstance(current_instr, dict) else None,
            RUNTIME.tool_to_instruction_type(tool_name),
        )
        st = _safe_dict(current_instr.get("security_type") if isinstance(current_instr, dict) else {})
        if not _is_high_side_effect_sink(tool_name, args_dict, instruction_type=instruction_type, cfg=cfg):
            continue

        source_contexts = _source_contexts_for_op(
            op_args=args_dict,
            instructions=full_instructions,
            cfg=cfg,
        )
        has_untrusted_ingress_source = any(
            ctx.ingress_like and ctx.source_trustworthiness in {"LOW", "UNKNOWN"}
            for ctx in source_contexts
        )

        arg_text = _collect_text_fragments(args_dict, limit=max(1024, cfg["arg_text_trigger_chars"] * 2))
        marker_hit = _contains_prompt_injection_markers(
            arg_text,
            cfg["prompt_injection_markers"],
        )
        long_unstructured_text = len(arg_text) >= cfg["arg_text_trigger_chars"]
        current_prop_trust = _safe_upper(
            st.get("prop_trustworthiness") or st.get("trustworthiness"),
            "UNKNOWN",
        )
        prop_untrusted = current_prop_trust in {"LOW", "UNKNOWN"}

        op_reasons: list[str] = []
        if has_untrusted_ingress_source:
            op_reasons.append("untrusted_ingress_source")
        if marker_hit:
            op_reasons.append("prompt_injection_marker_in_args")
        if long_unstructured_text and prop_untrusted:
            op_reasons.append("large_low_trust_arg_text")
        if not op_reasons:
            continue

        reviewed = dict(op)
        if source_contexts:
            reviewed["source_context"] = [asdict(ctx) for ctx in source_contexts]
        reviewed["trigger_reasons"] = list(op_reasons)
        reviewed_ops.append(reviewed)
        if tool_call_id:
            reviewed_ids.append(tool_call_id)
        for reason in op_reasons:
            if reason not in decision_reasons:
                decision_reasons.append(reason)

    return PreexecTriggerDecision(
        run=bool(reviewed_ops),
        reasons=decision_reasons,
        reviewed_tool_call_ids=reviewed_ids,
        reviewed_ops=reviewed_ops,
    )


def should_trigger_postexec_sentinel(
    *,
    tool_name: str,
    args_dict: Optional[dict[str, Any]],
    body: Any,
    trustworthiness: str,
    instruction_type: str,
) -> PostexecTriggerDecision:
    cfg = _postexec_cfg()
    if not cfg["enabled"]:
        return PostexecTriggerDecision(
            run=True,
            reasons=["trigger_disabled_legacy_full_review"],
        )

    args_dict = _safe_dict(args_dict)
    normalized_tool = _safe_str(tool_name).lower()
    trust = _safe_upper(trustworthiness, "UNKNOWN")
    itype = _safe_upper(instruction_type, "EXEC")
    ingress_like = _is_ingress_source(
        normalized_tool,
        args_dict,
        instruction_type=itype,
        trustworthiness=trust,
        cfg=cfg,
    )

    text = _collect_text_fragments(body, limit=12000)
    marker_hit = _contains_prompt_injection_markers(text, cfg["prompt_injection_markers"])
    oversized = len(text) >= cfg["max_body_chars_before_force_trigger"]
    semi_structured = len(text) >= cfg["structured_text_min_chars"] and _looks_semi_structured_untrusted_text(text)
    unknown_source = trust in {"LOW", "UNKNOWN"}

    reasons: list[str] = []
    if marker_hit:
        reasons.append("prompt_injection_marker_in_tool_result")
    if oversized:
        reasons.append("oversized_tool_result")
    if ingress_like and unknown_source and semi_structured:
        reasons.append("semi_structured_low_trust_ingress_result")
    if ingress_like and unknown_source and len(text) >= cfg["unknown_source_min_chars"]:
        reasons.append("large_unknown_source_ingress_result")

    return PostexecTriggerDecision(
        run=bool(reasons),
        reasons=reasons,
        ingress_like=ingress_like,
        prompt_injection_marker_hit=marker_hit,
        oversized=oversized,
        unknown_source=unknown_source,
        semi_structured=semi_structured,
    )
