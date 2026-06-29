from __future__ import annotations

import json
import os
import re

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from arbiteros_kernel.instruction_parsing.types import LEVEL_ORDER
from arbiteros_kernel.mcp_tool_classification import classify_mcp_tool_flow
from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .tool_evidence_ledger import (
    build_tool_evidence_ledger,
    infer_workflow_candidate,
    summarize_ledger,
    verify_claims_for_tool_args,
)
from .direct_metadata import derive_policy_metadata_from_tool_args
from .policy import Policy
from .unary_gate_policy import _eval_predicate, _render_predicate


RULE_DETAILS_URL = "http://43.161.233.143:5173/"


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
_SECRET_LIKE_TEXT_RE = re.compile(
    r"(?is)\b(api[_-]?key|token|password|passwd|credential|credentials|"
    r"secret|openclaw(?:\.json| config| configuration)?|/etc/shadow)\b|"
    r"(?:sk|tok)-[a-z0-9][a-z0-9._-]*"
)
_SYSTEM_SECRET_PATH_RE = re.compile(
    r"(?i)(?:^|/)(?:shadow|passwd|password\.txt|openclaw\.json)$"
)

_GDPR_DELETE_CONTEXT_PATTERNS = (
    "gdpr",
    "right to erasure",
    "right-to-erasure",
    "data deletion",
    "data removal",
    "personal data deletion",
    "personal data removal",
    "delete my personal data",
    "delete all my personal data",
    "personal information has been removed",
    "removed from our systems",
)
_CRM_CONTACT_DELETE_TOOLS = {
    "salesforce__delete_contact",
    "salesforce__delete_record",
}
_BUSINESS_CONTEXT_READ_SERVICES = {
    "atlassian",
    "calendar",
    "crm",
    "customer_service",
    "email",
    "freshdesk",
    "gmail",
    "intercom",
    "jira",
    "mail",
    "salesforce",
    "servicenow",
    "slack",
    "zendesk",
}

_NO_CONSENT_PHRASES = (
    "do not contact",
    "do not add",
    "do not create",
    "don't contact",
    "don't add",
    "must not be contacted",
    "must not be added",
    "must not contact",
    "must not add",
    "not to contact",
    "not to add",
    "did not consent",
    "has not consented",
    "never consented",
    "without consent",
    "no consent",
    "not consented",
    "no opt-in",
    "no opt in",
    "opted out",
    "opt-out",
    "opt out",
    "never expressed any interest",
)
_SELF_OPT_IN_PHRASES = (
    "i am interested",
    "i'm interested",
    "i’m interested",
    "i am reaching out",
    "i'm reaching out",
    "i’m reaching out",
    "my name is",
    "please contact me",
    "you can reach me",
    "i would like",
    "i'd like",
    "we are interested",
)
_THIRD_PARTY_REFERRAL_PHRASES = (
    "on behalf of",
    "my colleague",
    "our colleague",
    "mentioned",
    "primary point of contact",
    "primary business sponsor",
    "key decision-maker",
    "handles enterprise",
    "evaluation team",
    "copied on any communications",
)
_THIRD_PARTY_OPT_IN_CLAIM_PHRASES = (
    "system records show",
    "completed the enterprise opt-in form",
    "completed the opt-in form",
    "opt-in form",
    "opt in form",
    "overrides the previous note",
    "authorizes direct communication",
    "authorizing direct communication",
    "authorized direct communication",
    "independently",
)
_PERSON_NAME_RE = r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3}"
_THIRD_PARTY_ORIGIN_RE = re.compile(
    rf"\b("
    rf"[Cc]olleagues?\s+of\s+(?P<colleague>{_PERSON_NAME_RE})|"
    rf"[Rr]eferr(?:al|ed)\s+(?:lead\s+)?(?:by|from|through)\s+(?P<referral>{_PERSON_NAME_RE})|"
    rf"(?:[Ii]ntroduced|[Cc]onnected)\s+(?:by|through)\s+(?P<introduced>{_PERSON_NAME_RE})|"
    rf"\([Ff]rom\s+(?P<paren_from>{_PERSON_NAME_RE})\)|"
    rf"\b[Tt]hrough\s+(?P<through>{_PERSON_NAME_RE})"
    rf")\b"
)

_CRM_STATE_CHANGE_PREFIXES = (
    "salesforce__assign_",
    "salesforce__convert_",
    "salesforce__create_",
    "salesforce__delete_",
    "salesforce__duplicate_",
    "salesforce__link_",
    "salesforce__merge_",
    "salesforce__relationship_create",
    "salesforce__relationship_delete",
    "salesforce__set_",
    "salesforce__update_",
)
_CALENDAR_STATE_CHANGE_TOOLS = {
    "calendar__create_event",
    "calendar__delete_event",
    "calendar__update_event",
    "calendar__accept_invitation",
}
_PAYMENT_STATE_CHANGE_PREFIXES = (
    "paypal__accept_",
    "paypal__approve_",
    "paypal__cancel_",
    "paypal__checkout_",
    "paypal__create_",
    "paypal__pay_",
    "paypal__send_",
    "paypal__trade_",
    "paypal__update_",
)
_CALENDAR_CANCELLATION_CONTEXT_PATTERNS = (
    "meeting cancellation request",
    "meeting cancellation requests",
    "meeting cancellation guidelines",
    "calendar cancellation request",
    "calendar cancellation guidelines",
    "request to cancel",
    "requested cancellation",
    "cancellation request",
    "cancel the meeting",
    "cancel this meeting",
    "cancel my meeting",
    "meeting has been cancelled",
    "meeting has been canceled",
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


def _safe_lower(v: Any, default: str = "") -> str:
    s = _safe_str(v, default)
    return s.lower() if s else default


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


def _safe_policy_metadata(custom: Dict[str, Any], reserved: Set[str]) -> Dict[str, Any]:
    raw = custom.get("policy_metadata")
    if not isinstance(raw, dict):
        return {}

    out: Dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        name = key.strip()
        if not name or name in reserved:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[name] = value
        elif isinstance(value, list) and all(
            isinstance(item, (str, int, float, bool)) or item is None
            for item in value
        ):
            out[name] = value
    return out


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


def _instruction_tool_name(ins: Optional[Dict[str, Any]]) -> str:
    content = ins.get("content") if isinstance(ins, dict) else {}
    content = content if isinstance(content, dict) else {}
    return _safe_str(content.get("tool_name"))


def _instruction_tool_call_id(ins: Optional[Dict[str, Any]]) -> str:
    content = ins.get("content") if isinstance(ins, dict) else {}
    content = content if isinstance(content, dict) else {}
    return _safe_str(content.get("tool_call_id"))


def _instruction_id(ins: Optional[Dict[str, Any]]) -> str:
    return _safe_str(ins.get("id")) if isinstance(ins, dict) else ""


def _history_without_latest(
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not instructions or not latest_instructions:
        return list(instructions or [])

    n_latest = len(latest_instructions)
    if n_latest and len(instructions) >= n_latest:
        if instructions[-n_latest:] == latest_instructions:
            return list(instructions[:-n_latest])

    latest_ids = {_instruction_id(ins) for ins in latest_instructions}
    latest_ids.discard("")
    latest_tool_call_ids = {
        _instruction_tool_call_id(ins) for ins in latest_instructions
    }
    latest_tool_call_ids.discard("")

    out: List[Dict[str, Any]] = []
    for ins in instructions:
        ins_id = _instruction_id(ins)
        tcid = _instruction_tool_call_id(ins)
        if ins_id and ins_id in latest_ids:
            continue
        if tcid and tcid in latest_tool_call_ids:
            continue
        out.append(ins)
    return out


def _latest_prior_source_instruction(
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    history = _history_without_latest(instructions or [], latest_instructions or [])
    for ins in reversed(history):
        if not isinstance(ins, dict):
            continue
        if not isinstance(ins.get("security_type"), dict):
            continue
        itype = _safe_upper(ins.get("instruction_type"))
        if itype in {
            "READ",
            "RETRIEVE",
            "RECEIVE",
            "USER_MESSAGE",
            "RESPOND",
            "WRITE",
            "STORE",
            "EXEC",
            "DELEGATE",
        }:
            return ins
    return None


def _history_source_data_labels(
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> List[str]:
    labels: List[str] = []
    seen: Set[str] = set()
    for ins in _history_without_latest(instructions or [], latest_instructions or []):
        if not isinstance(ins, dict):
            continue
        sec = _extract_instruction_security(ins)
        custom = sec.get("custom")
        if not isinstance(custom, dict):
            continue
        metadata = _safe_policy_metadata(custom, set())
        raw_labels = metadata.get("data_labels")
        values = raw_labels if isinstance(raw_labels, list) else [raw_labels]
        for value in values:
            if not isinstance(value, str) or not value.strip():
                continue
            label = value.strip()
            key = label.upper()
            if key in seen:
                continue
            seen.add(key)
            labels.append(label)
    return labels


def _iter_strings(value: Any, *, max_items: int = 256) -> List[str]:
    out: List[str] = []

    def visit(item: Any) -> None:
        if len(out) >= max_items:
            return
        if isinstance(item, str):
            text = item.strip()
            if text:
                out.append(text[:2000])
            return
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested)
                if len(out) >= max_items:
                    break
            return
        if isinstance(item, list):
            for nested in item:
                visit(nested)
                if len(out) >= max_items:
                    break

    visit(value)
    return out


def _content_dict(ins: Any) -> Dict[str, Any]:
    content = ins.get("content") if isinstance(ins, dict) else {}
    return content if isinstance(content, dict) else {}


def _instruction_arguments(ins: Any) -> Dict[str, Any]:
    content = _content_dict(ins)
    args = content.get("arguments")
    return args if isinstance(args, dict) else {}


def _history_context_text(
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
    *,
    max_items: int = 220,
    include_latest: bool = False,
) -> str:
    history = _history_without_latest(instructions or [], latest_instructions or [])
    items = list(history)
    if include_latest:
        items.extend(latest_instructions or [])
    return "\n".join(_iter_strings(items, max_items=max_items)).lower()


def _history_mentions_no_consent(
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> bool:
    text = _history_context_text(
        instructions,
        latest_instructions,
        max_items=320,
        include_latest=True,
    )
    if not text:
        return False
    if any(phrase in text for phrase in _NO_CONSENT_PHRASES):
        return True
    return bool(
        re.search(
            r"\b(?:no|without|never)\b.{0,60}\b(?:consent|opt[- ]?in|permission|interest)\b",
            text,
        )
    )


def _emails_from_text(text: str) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for match in re.finditer(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,63}\b",
        text or "",
    ):
        email = match.group(0).strip().lower()
        if email and email not in seen:
            seen.add(email)
            out.append(email)
    return out


def _history_sender_emails(
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> List[str]:
    senders: List[str] = []
    seen: Set[str] = set()
    for ins in _history_without_latest(instructions or [], latest_instructions or []):
        args = _instruction_arguments(ins)
        values: List[Any] = []
        for key in ("from", "from_email", "sender", "return_path"):
            values.append(args.get(key))
        for key in ("From", "ReturnPath"):
            values.append(args.get(key))
        for email in _emails_from_text("\n".join(_iter_strings(values, max_items=20))):
            if email not in seen:
                seen.add(email)
                senders.append(email)
    return senders


def _current_target_emails(tool_name: str, args_dict: Dict[str, Any]) -> List[str]:
    name = _safe_lower(tool_name)
    keys = (
        ("email", "email1", "to", "recipient", "address")
        if name == "salesforce__create_lead"
        else ("to", "cc", "bcc", "email", "recipient", "address")
    )
    values = [args_dict.get(key) for key in keys]
    return _emails_from_text("\n".join(_iter_strings(values, max_items=20)))


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _current_target_names(args_dict: Dict[str, Any]) -> List[str]:
    raw_names: List[str] = []
    for key in ("full_name", "name", "contact_name", "lead_name"):
        value = args_dict.get(key)
        if isinstance(value, str) and value.strip():
            raw_names.append(value.strip())

    first = _safe_str(args_dict.get("first_name"))
    last = _safe_str(args_dict.get("last_name"))
    if first and last:
        raw_names.append(f"{first} {last}")

    seen: Set[str] = set()
    out: List[str] = []
    for name in raw_names:
        normalized = _normalize_name(name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(name)
    return out


def _windows_around_needles(text: str, needles: List[str], *, radius: int = 420) -> List[str]:
    if not text or not needles:
        return []
    lower_text = text.lower()
    windows: List[str] = []
    seen: Set[Tuple[int, int]] = set()
    for needle in needles:
        lowered = needle.lower().strip()
        if not lowered:
            continue
        start = 0
        while True:
            idx = lower_text.find(lowered, start)
            if idx < 0:
                break
            begin = max(0, idx - radius)
            end = min(len(text), idx + len(needle) + radius)
            span = (begin, end)
            if span not in seen:
                seen.add(span)
                windows.append(text[begin:end])
            start = idx + len(lowered)
    return windows


def _named_third_party_origin_in_text(text: str, target_names: List[str]) -> bool:
    normalized_targets = {_normalize_name(name) for name in target_names if name}
    for match in _THIRD_PARTY_ORIGIN_RE.finditer(text or ""):
        origin = ""
        for group in ("colleague", "referral", "introduced", "paren_from", "through"):
            origin = match.groupdict().get(group) or ""
            if origin:
                break
        if origin and _normalize_name(origin) in normalized_targets:
            continue
        return True
    return False


def _target_context_mentions_named_third_party_origin(
    *,
    tool_name: str,
    args_dict: Dict[str, Any],
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> bool:
    target_emails = _current_target_emails(tool_name, args_dict)
    target_names = _current_target_names(args_dict)
    needles = target_emails + target_names
    if not needles:
        return False

    text = "\n".join(
        _iter_strings(args_dict, max_items=80)
        + _iter_strings(instructions or [], max_items=320)
        + _iter_strings(latest_instructions or [], max_items=120)
    )
    for window in _windows_around_needles(text, needles):
        if _named_third_party_origin_in_text(window, target_names):
            return True
    return False


def _history_indicates_third_party_referral(
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> bool:
    text = _history_context_text(
        instructions,
        latest_instructions,
        max_items=320,
        include_latest=True,
    )
    if not text:
        return False
    if any(phrase in text for phrase in _THIRD_PARTY_REFERRAL_PHRASES):
        return True
    if any(phrase in text for phrase in _THIRD_PARTY_OPT_IN_CLAIM_PHRASES):
        return True
    return False


def _target_differs_from_recent_sender(
    *,
    tool_name: str,
    args_dict: Dict[str, Any],
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> bool:
    targets = _current_target_emails(tool_name, args_dict)
    if not targets:
        return False
    senders = _history_sender_emails(instructions, latest_instructions)
    if not senders:
        return False
    return all(target not in senders for target in targets)


def _current_args_indicate_unverified_opt_in_claim(
    *,
    tool_name: str,
    args_dict: Dict[str, Any],
) -> bool:
    if not _current_target_emails(tool_name, args_dict):
        return False
    text = "\n".join(_iter_strings(args_dict, max_items=80)).lower()
    if not text:
        return False
    if any(phrase in text for phrase in _THIRD_PARTY_OPT_IN_CLAIM_PHRASES):
        return True
    return bool(
        re.search(
            r"\bopt[- ]?in\b.{0,120}\b(?:independent(?:ly)?|authori[sz](?:e|ed|ing)|override[sd]?)\b",
            text,
        )
    )


def _context_indicates_third_party_consent_risk(
    *,
    tool_name: str,
    args_dict: Dict[str, Any],
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> bool:
    if _current_args_indicate_unverified_opt_in_claim(
        tool_name=tool_name,
        args_dict=args_dict,
    ):
        return True
    if _target_context_mentions_named_third_party_origin(
        tool_name=tool_name,
        args_dict=args_dict,
        instructions=instructions,
        latest_instructions=latest_instructions,
    ):
        return True
    if not _history_indicates_third_party_referral(instructions, latest_instructions):
        return False
    return _target_differs_from_recent_sender(
        tool_name=tool_name,
        args_dict=args_dict,
        instructions=instructions,
        latest_instructions=latest_instructions,
    )


def _is_non_comm_state_change_tool(tool_name: str) -> bool:
    name = _safe_lower(tool_name)
    if not name:
        return False
    if name.startswith(_CRM_STATE_CHANGE_PREFIXES):
        return True
    if name in _CALENDAR_STATE_CHANGE_TOOLS:
        return True
    if name.startswith(_PAYMENT_STATE_CHANGE_PREFIXES):
        return True
    return False


def _is_calendar_state_change_tool(tool_name: str) -> bool:
    return _safe_lower(tool_name) in _CALENDAR_STATE_CHANGE_TOOLS


def _is_crm_state_change_tool(tool_name: str) -> bool:
    name = _safe_lower(tool_name)
    return bool(name) and name.startswith(_CRM_STATE_CHANGE_PREFIXES)


def _is_payment_state_change_tool(tool_name: str) -> bool:
    name = _safe_lower(tool_name)
    return bool(name) and name.startswith(_PAYMENT_STATE_CHANGE_PREFIXES)


def _is_opportunity_state_change_tool(tool_name: str) -> bool:
    name = _safe_lower(tool_name)
    if not name:
        return False
    if "opportunit" in name and _is_crm_state_change_tool(name):
        return True
    return name in {
        "salesforce__update_opportunity",
        "salesforce__update_opportunity_stage",
    }


def _is_license_state_change_tool(tool_name: str) -> bool:
    name = _safe_lower(tool_name)
    if not name:
        return False
    return _is_crm_state_change_tool(name) and any(
        part in name for part in ("license", "licence", "entitlement", "provision")
    )


def _is_case_terminal_state_change_tool(tool_name: str) -> bool:
    name = _safe_lower(tool_name)
    if not name:
        return False
    return _is_crm_state_change_tool(name) and (
        "case" in name or name == "salesforce__update_record"
    )


def _history_has_tool_matching(
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
    predicate: Any,
) -> bool:
    for hist in _history_without_latest(instructions or [], latest_instructions or []):
        tool = _instruction_tool_name(hist)
        try:
            if predicate(tool):
                return True
        except Exception:
            continue
    return False


def _tool_names_match(tool_names: List[str], predicate: Any) -> bool:
    for tool in tool_names or []:
        try:
            if predicate(tool):
                return True
        except Exception:
            continue
    return False


def _history_has_non_comm_state_change(
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> bool:
    return _history_has_tool_matching(
        instructions,
        latest_instructions,
        _is_non_comm_state_change_tool,
    )


def _history_mentions_calendar_cancellation_request(
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> bool:
    text = _history_context_text(
        instructions,
        latest_instructions,
        max_items=360,
        include_latest=True,
    )
    if not text:
        return False
    if any(phrase in text for phrase in _CALENDAR_CANCELLATION_CONTEXT_PATTERNS):
        return True
    return bool(
        re.search(
            r"\b(meeting|calendar\s+event|call|demo|review)\b.{0,120}"
            r"\b(cancel|cancellation|cancelled|canceled)\b",
            text,
        )
    )


def _configured_gdpr_delete_phrases() -> List[str]:
    cfg = _get_relational_policy_cfg()
    raw = cfg.get("allow_crm_contact_delete_when_context_contains")
    if isinstance(raw, list) and raw:
        return [str(item).strip().lower() for item in raw if str(item).strip()]
    return list(_GDPR_DELETE_CONTEXT_PATTERNS)


def _is_allowed_gdpr_contact_delete(
    *,
    tool_name: str,
    args_dict: Dict[str, Any],
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> bool:
    name = _safe_lower(tool_name)
    if name not in _CRM_CONTACT_DELETE_TOOLS:
        return False
    if name == "salesforce__delete_record":
        module_name = _safe_lower(
            args_dict.get("module_name") or args_dict.get("module") or args_dict.get("type")
        )
        if module_name and module_name not in {"contact", "contacts"}:
            return False

    text = "\n".join(
        _iter_strings(instructions or [], max_items=220)
        + _iter_strings(latest_instructions or [], max_items=80)
    ).lower()
    if not text:
        return False
    phrases = [phrase for phrase in _configured_gdpr_delete_phrases() if phrase]
    if any(phrase in text for phrase in phrases):
        return True
    return bool(re.search(r"\bdelete\b.{0,80}\b(personal data|personal information)\b", text))


def _is_allowed_calendar_cancellation_change(
    *,
    tool_name: str,
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
) -> bool:
    name = _safe_lower(tool_name)
    if name not in {"calendar__delete_event", "calendar__update_event"}:
        return False
    return _history_mentions_calendar_cancellation_request(
        instructions,
        latest_instructions,
    )


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


def _iter_arg_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.strip():
            yield value.strip()
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_arg_strings(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_arg_strings(item)
        return
    if isinstance(value, (int, float, bool)):
        yield str(value)


def _args_text(args_dict: Dict[str, Any]) -> str:
    return "\n".join(_iter_arg_strings(args_dict))


def _args_contain_secret_like(args_dict: Dict[str, Any]) -> bool:
    return bool(_SECRET_LIKE_TEXT_RE.search(_args_text(args_dict)))


def _has_reference_tool(args_dict: Dict[str, Any]) -> bool:
    refs = args_dict.get("reference_tool_id")
    if isinstance(refs, list):
        return any(isinstance(item, str) and item.strip() for item in refs)
    return isinstance(refs, str) and bool(refs.strip())


def _is_business_context_read_tool(tool_name: str) -> bool:
    service, sep, _ = _safe_str(tool_name).lower().partition("__")
    return bool(sep and service in _BUSINESS_CONTEXT_READ_SERVICES)


def _is_sensitive_read_requiring_trust(
    tool_name: str,
    args_dict: Dict[str, Any],
    sec: Dict[str, Any],
) -> bool:
    if _safe_level(sec.get("confidentiality")) != "HIGH":
        return False
    if _is_business_context_read_tool(tool_name):
        return False
    prop_trust = _safe_level(sec.get("prop_trustworthiness"))
    path_hint = _get_primary_path_hint(args_dict)
    if not _level_at_least(prop_trust, "HIGH"):
        return True
    return bool(_has_reference_tool(args_dict) and _SYSTEM_SECRET_PATH_RE.search(path_hint))


def _is_public_delegate_payload(args_dict: Dict[str, Any]) -> bool:
    text = _args_text(args_dict)
    lowered = text.lower()
    if _SECRET_LIKE_TEXT_RE.search(text):
        return False
    return "public" in lowered and any(
        marker in lowered
        for marker in ("bug summary", "docs", "documentation", "getting-started")
    )


def _is_calendar_ui_action(args_dict: Dict[str, Any]) -> bool:
    text = _args_text(args_dict).lower()
    if _SECRET_LIKE_TEXT_RE.search(text):
        return False
    return "calendar" in text and any(
        marker in text
        for marker in ("meeting", "release sync", "reschedule", "edit-meeting")
    )


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

    mcp_flow_kind = classify_mcp_tool_flow(name)
    if mcp_flow_kind != "none":
        return mcp_flow_kind

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


def _flow_label(flow_kind: str) -> str:
    labels = {
        "read_external": "读取外部信息",
        "read_sensitive": "读取敏感信息",
        "read_state": "读取状态信息",
        "write_local": "本地写入/落盘",
        "write_shared": "共享/导出写入",
        "delegate_sink": "委托/跨会话发送",
        "comm_sink": "对外发送/可见输出",
        "voice_sink": "语音输出",
        "ui_side_effect": "UI 控制副作用",
        "exec_side_effect": "执行类副作用",
        "business_side_effect": "业务系统写入",
        "persist_side_effect": "持久化副作用",
        "respond_sink": "直接回复输出",
    }
    return labels.get(flow_kind, flow_kind or "unknown")


def _render_tool_instruction_block(tool_name: str, args_dict: Dict[str, Any]) -> str:
    if tool_name in {"exec", "process"}:
        command = _safe_str(
            args_dict.get("command")
            or args_dict.get("cmd")
            or args_dict.get("script")
        )
        if command:
            return f"```bash\n{command}\n```"
    return "```json\n" + json.dumps(
        args_dict,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n```"


def _relational_policy_title(flow_kind: str) -> str:
    mapping = {
        "read_external": "外部内容读取",
        "read_sensitive": "敏感信息读取",
        "read_state": "状态读取",
        "write_local": "本地写入",
        "write_shared": "共享或导出写入",
        "delegate_sink": "跨会话委托",
        "comm_sink": "对外发送",
        "voice_sink": "语音输出",
        "ui_side_effect": "界面操作",
        "exec_side_effect": "执行类动作",
        "business_side_effect": "业务系统写入",
        "persist_side_effect": "持久化任务",
        "respond_sink": "直接回复输出",
    }
    return mapping.get(flow_kind, "关系型安全保护")


def _relational_policy_function_text(
    flow_kind: str,
    actual: Optional[str],
    required: str,
    source_trust: str,
    source_conf: str,
    *,
    direct_output: bool = False,
) -> str:
    mapping = {
        "read_external": "这一步会读取外部内容，但当前条件下系统无法确认这种读取是安全的，因此先拦截。",
        "read_sensitive": "这一步会读取较敏感的信息，但当前读取方暂不满足安全要求，因此先拦截。",
        "read_state": "这一步会读取系统状态，但当前依据还不足以支持继续读取，因此先拦截。",
        "write_local": "这一步会把内容写入本地位置，但当前内容不适合直接落盘，因此先拦截。",
        "write_shared": "这一步会把内容写入更容易扩散的位置，因此先拦截。",
        "delegate_sink": "这一步会把内容或任务交给其他会话或代理，但当前承接对象暂不满足安全要求，因此先拦截。",
        "comm_sink": "这一步会把内容发送到对外可见的渠道，但当前接收对象可能不够可信，因此先拦截。",
        "voice_sink": "这一步会把内容直接以语音形式输出，内容一旦播出更难控制，因此先拦截。",
        "ui_side_effect": "这一步会直接影响界面或交互环境，但驱动它的依据还不够可靠，因此先拦截。",
        "exec_side_effect": "这一步会直接影响系统或运行环境，但驱动它的依据还不够可靠，因此先拦截。",
        "business_side_effect": "这一步会修改业务系统状态，但当前条件下不适合直接执行，因此先拦截。",
        "persist_side_effect": "这一步会把动作固化为持续生效的任务，但当前条件下不适合直接持久化，因此先拦截。",
        "respond_sink": "当前内容不适合直接展示给用户，因此先拦截。",
    }
    return mapping.get(flow_kind, "当前信息流转方式存在安全风险，因此先拦截。")


def _relational_rule_reference(
    flow_kind: str,
    actual: Optional[str],
    required: str,
    source_trust: str,
    source_conf: str,
    *,
    reason: Optional[str] = None,
) -> str:
    lines = [
        f"- 规则类型：{_relational_policy_title(flow_kind)}",
        "- 保护目标：避免不够可信或不适合外发的内容继续流向当前目标。",
        f"- 详情见：{RULE_DETAILS_URL}",
    ]
    return "\n".join(lines)


def _friendly_message(
    tool_name: str,
    flow_kind: str,
    source_trust: str,
    source_conf: str,
    required: str,
    reason: Optional[str] = None,
    *,
    actual: Optional[str] = None,
    phase: str = "tool-call",
    args_dict: Optional[Dict[str, Any]] = None,
) -> str:
    args_dict = args_dict if isinstance(args_dict, dict) else {}
    return "\n".join(
        [
            "## ⚠️ 安全策略拦截确认",
            "",
            "### 1. 触发指令",
            _render_tool_instruction_block(tool_name, args_dict),
            "",
            "### 2. 拦截说明",
            (
                f"[{_relational_policy_title(flow_kind)}]："
                f"{_relational_policy_function_text(flow_kind, actual, required, source_trust, source_conf)}"
            ),
            "",
            "### 3. 规则说明",
            _relational_rule_reference(
                flow_kind,
                actual,
                required,
                source_trust,
                source_conf,
                reason=reason,
            ),
        ]
    )


def _friendly_respond_message(
    source_trust: str,
    source_conf: str,
    required: str,
    reason: Optional[str] = None,
    *,
    actual: Optional[str] = None,
    phase: str = "respond",
) -> str:
    flow_kind = "respond_sink"
    return "\n".join(
        [
            "## ⚠️ 安全策略拦截确认",
            "",
            "### 1. 触发指令",
            "```text\nRESPOND\n```",
            "",
            "### 2. 拦截说明",
            (
                f"[{_relational_policy_title(flow_kind)}]："
                f"{_relational_policy_function_text(flow_kind, actual, required, source_trust, source_conf, direct_output=True)}"
            ),
            "",
            "### 3. 规则说明",
            _relational_rule_reference(
                flow_kind,
                actual,
                required,
                source_trust,
                source_conf,
                reason=reason,
            ),
        ]
    )


def _should_treat_respond_as_sink(tp_cfg: Dict[str, Any]) -> bool:
    return bool(
        tp_cfg.get("treat_respond_as_sink", False)
        or tp_cfg.get("respond_as_output", False)
    )


def _respond_instruction_enabled(tp_cfg: Dict[str, Any]) -> bool:
    sinks = tp_cfg.get("instruction_sinks")
    if isinstance(sinks, list) and sinks:
        return "RESPOND" in {_safe_upper(x) for x in sinks if isinstance(x, str)}
    return True


def _fail_closed_on_missing_metadata(tp_cfg: Dict[str, Any]) -> bool:
    return bool(tp_cfg.get("fail_closed_on_missing_instruction_metadata", False))


def _append_unique_error(errors: List[str], seen: set[str], message: str) -> None:
    if message not in seen:
        errors.append(message)
        seen.add(message)


def _get_taint_cfg() -> Dict[str, Any]:
    ta = RUNTIME.cfg.get("taint") or {}
    return ta if isinstance(ta, dict) else {}


def _get_taint_policy_cfg() -> Dict[str, Any]:
    ta = _get_taint_cfg()
    tp = ta.get("taint_policy")
    return tp if isinstance(tp, dict) else {}


def _get_relational_policy_cfg() -> Dict[str, Any]:
    cfg = RUNTIME.cfg.get("relational_policy")
    if isinstance(cfg, dict):
        return cfg
    tp_cfg = _get_taint_policy_cfg()
    nested = tp_cfg.get("relational_policy")
    return nested if isinstance(nested, dict) else {}


def _resolve_rule_file_path(path: str) -> str:
    p = os.path.expandvars(os.path.expanduser(path))
    if os.path.isabs(p):
        return p

    candidates = [
        p,
        os.path.join(os.getcwd(), p),
        os.path.join(os.path.dirname(__file__), p),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), p),
    ]
    for item in candidates:
        if os.path.exists(item):
            return item
    return candidates[0]


def _configured_rule_files(value: Any) -> List[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _read_relational_rule_bundle(path: str) -> Dict[str, Any]:
    resolved = _resolve_rule_file_path(path)
    with open(resolved, "r", encoding="utf-8") as f:
        data = json.loads(f.read())
    return data if isinstance(data, dict) else {}


def _optional_relational_rule_bundle(path: str) -> Dict[str, Any]:
    resolved = _resolve_rule_file_path(path)
    if not os.path.exists(resolved):
        return {}
    return _read_relational_rule_bundle(path)


def _rules_with_source(rules: Any, source: str) -> List[Dict[str, Any]]:
    if not isinstance(rules, list):
        return []
    out: List[Dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        copied = dict(rule)
        copied.setdefault("source", source)
        out.append(copied)
    return out


def _load_custom_relational_bundle() -> Dict[str, Any]:
    cfg = _get_relational_policy_cfg()
    if not bool(cfg.get("user_rules_enabled", False)):
        return {"rules": [], "required_metadata": []}

    rule_files = _configured_rule_files(
        cfg.get("user_rule_files") or cfg.get("user_rule_file")
    )
    out: List[Dict[str, Any]] = []
    required_metadata: List[Any] = []
    seen_ids: Set[str] = set()
    for path in rule_files:
        bundle = _optional_relational_rule_bundle(path)
        source = _safe_str(bundle.get("source"), os.path.basename(path))
        if isinstance(bundle.get("required_metadata"), list):
            required_metadata.extend(bundle["required_metadata"])
        for rule in _rules_with_source(bundle.get("rules"), source):
            rule_id = _safe_str(rule.get("id"))
            if rule_id and rule_id in seen_ids:
                continue
            if rule_id:
                seen_ids.add(rule_id)
            out.append(rule)
    return {"rules": out, "required_metadata": required_metadata}


def _load_custom_relational_rules() -> List[Dict[str, Any]]:
    bundle = _load_custom_relational_bundle()
    rules = bundle.get("rules")
    return rules if isinstance(rules, list) else []


def _selector_values(raw: Any) -> Optional[Set[str]]:
    if raw is None:
        return None
    values = raw if isinstance(raw, list) else [raw]
    out = {
        _safe_upper(value)
        for value in values
        if isinstance(value, str) and value.strip()
    }
    return out or None


def _selector_matches_context(
    selector: Dict[str, Any],
    ctx: Dict[str, Any],
    *,
    prefix: str,
) -> bool:
    if not isinstance(selector, dict):
        return True

    key_map = {
        "tool": f"{prefix}_tool_name",
        "instruction_type": f"{prefix}_instruction_type",
        "category": f"{prefix}_instruction_category",
    }
    for selector_key, ctx_key in key_map.items():
        allowed = _selector_values(selector.get(selector_key))
        if allowed is None:
            continue
        actual = _safe_upper(ctx.get(ctx_key))
        if actual not in allowed:
            return False

    allowed_flow = _selector_values(selector.get("flow_kind"))
    if allowed_flow is not None and _safe_upper(ctx.get("flow_kind")) not in allowed_flow:
        return False
    return True


def _extract_vars(value: Any) -> Set[str]:
    out: Set[str] = set()
    if isinstance(value, dict):
        var = value.get("var")
        if isinstance(var, str):
            out.add(var)
        for item in value.values():
            out.update(_extract_vars(item))
    elif isinstance(value, list):
        for item in value:
            out.update(_extract_vars(item))
    return out


def _actual_snapshot(ctx: Dict[str, Any], pred: Any) -> Dict[str, Any]:
    return {
        name: ctx.get(name)
        for name in sorted(_extract_vars(pred))
        if isinstance(name, str)
    }


def _custom_relational_rule_matches(
    rule: Dict[str, Any],
    ctx: Dict[str, Any],
) -> bool:
    if rule.get("enabled") is False:
        return False
    if _safe_lower(rule.get("scope"), "relational") != "relational":
        return False
    if not _selector_matches_context(
        rule.get("source_selector") or {},
        ctx,
        prefix="source",
    ):
        return False
    if not _selector_matches_context(rule.get("selector") or {}, ctx, prefix="sink"):
        return False
    pred = rule.get("predicate")
    if pred is None:
        return False
    return bool(_eval_predicate(pred, ctx))


def _evaluate_custom_relational_rules(
    rules: List[Dict[str, Any]],
    ctx: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    for rule in rules:
        if _custom_relational_rule_matches(rule, ctx):
            return rule
    return None


def _custom_relational_block_message(
    rule: Dict[str, Any],
    ctx: Dict[str, Any],
    *,
    args_dict: Optional[Dict[str, Any]] = None,
) -> str:
    title = _safe_str(rule.get("title"), "custom relational policy")
    message = _safe_str(rule.get("message"), "当前关系型策略命中，已拦截。")
    predicate = _render_predicate(rule.get("predicate"))
    actual = _actual_snapshot(ctx, rule.get("predicate"))
    actual_text = json.dumps(actual, ensure_ascii=False, sort_keys=True)
    tool_name = _safe_str(ctx.get("sink_tool_name"), "@instruction")
    return "\n".join(
        [
            "## 安全策略拦截确认",
            "",
            "### 1. 触发指令",
            _render_tool_instruction_block(tool_name, args_dict or {}),
            "",
            "### 2. 拦截说明",
            f"[{title}]：{message}",
            "",
            "### 3. 规则说明",
            f"- 规则 ID：{_safe_str(rule.get('id'), '<unknown>')}",
            f"- 信息流：{ctx.get('source_instruction_type')} -> {ctx.get('flow_kind')}",
            f"- 判断条件：{predicate or '<custom predicate>'}",
            f"- 实际值：{actual_text}",
        ]
    )


def _evaluate_flow(
    flow_kind: str,
    sec: Dict[str, Any],
    args_dict: Dict[str, Any],
    tool_name: str = "",
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
        if bool(_get_relational_policy_cfg().get("allow_sensitive_reads", False)):
            extra["read_sensitive_allowed_by_config"] = True
            if _is_sensitive_read_requiring_trust(tool_name, args_dict, sec):
                actual = _safe_level(sec.get("prop_trustworthiness"))
                extra["read_sensitive_blocked_by_propagated_trust"] = True
                return False, actual, "HIGH", extra
            return True, actual, required, extra
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
            source_conf == "HIGH"
            and _args_contain_secret_like(args_dict)
            and (
                _looks_shared_or_exported_sink(path_hint)
                or "/knowledge/" in _safe_str(path_hint).lower()
            )
        ):
            extra["secret_like_shared_materialization"] = True
            return False, "UNKNOWN", "HIGH", extra

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
        if _is_public_delegate_payload(args_dict):
            extra["public_delegate_allowed"] = True
            return True, "HIGH", _soft_source_conf(source_conf), extra
        actual = "LOW"
        required = _soft_source_conf(source_conf)
        return _level_at_least(actual, required), actual, required, extra

    # 7) Human-visible outbound communication:
    # Default comm sinks are allowed so benign customer replies can complete.
    # User-authored relational rules below handle high-risk external sends,
    # including direct PII/SECRET exfiltration to external recipients.
    if flow_kind == "comm_sink":
        return True, "UNKNOWN", _soft_source_conf(source_conf), extra

    # 8) Sandbox business-system writes (CRM create/update/convert/merge).
    # DTAP benign tasks require these after reading customer CRM/mail context.
    # Destructive CRM actions remain persist_side_effect.
    if flow_kind == "business_side_effect":
        return True, source_trust, "LOW", extra

    # 8) Voice/audio output is even harder to retract/control.
    if flow_kind == "voice_sink":
        actual = "LOW"
        required = _soft_source_conf(source_conf)
        return _level_at_least(actual, required), actual, required, extra

    # 9) Browser/UI side effects:
    # gate mainly on source trust, because the risk is "acting on low-trust state".
    if flow_kind == "ui_side_effect":
        if _is_calendar_ui_action(args_dict):
            extra["calendar_ui_action_allowed"] = True
            return True, "HIGH", "MID", extra
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


def _build_relational_context(
    *,
    tool_name: str,
    tool_call_id: str,
    ins: Optional[Dict[str, Any]],
    flow_kind: str,
    sec: Dict[str, Any],
    instructions: List[Dict[str, Any]],
    latest_instructions: List[Dict[str, Any]],
    current_taint_status: Any = None,
    args_dict: Optional[Dict[str, Any]] = None,
    respond_content_present: bool = False,
    required_metadata: Optional[List[Any]] = None,
    current_accepted_tool_names: Optional[List[str]] = None,
    current_accepted_ops: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    args_dict = args_dict if isinstance(args_dict, dict) else {}
    ok, actual, required, flow_extra = _evaluate_flow(
        flow_kind,
        sec,
        args_dict,
        tool_name=tool_name,
        current_taint_status=current_taint_status,
    )
    del ok

    source_ins = _latest_prior_source_instruction(instructions, latest_instructions)
    source_sec = _extract_instruction_security(source_ins or {})
    source_trust = flow_extra.get("source_trustworthiness")
    source_conf = flow_extra.get("source_confidentiality")
    if current_taint_status is None and source_ins is not None:
        source_trust = source_sec.get("prop_trustworthiness")
        source_conf = source_sec.get("prop_confidentiality")

    evidence_events = build_tool_evidence_ledger(
        instructions or [],
        latest_instructions or [],
        current_ops=current_accepted_ops or [],
        max_events=80,
    )
    claim_verification = verify_claims_for_tool_args(
        tool_name=tool_name,
        args=args_dict,
        events=evidence_events,
    )
    workflow_candidate = infer_workflow_candidate(
        planned_ops=current_accepted_ops or [],
        instructions=instructions or [],
        latest_instructions=latest_instructions or [],
        events=evidence_events,
    )

    ctx: Dict[str, Any] = {
        "scope": "relational",
        "flow_kind": flow_kind,
        "missing_instruction": ins is None,
        "tool_name": tool_name,
        "canonical_tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "instruction_type": sec.get("instruction_type"),
        "instruction_category": sec.get("instruction_category"),
        "trustworthiness": sec.get("trustworthiness"),
        "confidentiality": sec.get("confidentiality"),
        "reversible": sec.get("reversible"),
        "risk": sec.get("risk"),
        "source_tool_name": _instruction_tool_name(source_ins),
        "source_tool_call_id": _instruction_tool_call_id(source_ins),
        "source_instruction_type": _safe_upper(
            source_ins.get("instruction_type") if isinstance(source_ins, dict) else ""
        ),
        "source_instruction_category": _safe_str(
            source_ins.get("instruction_category")
            if isinstance(source_ins, dict)
            else ""
        ),
        "source_trustworthiness": _safe_level(source_trust),
        "source_confidentiality": _safe_level(source_conf),
        "sink_tool_name": tool_name,
        "sink_tool_call_id": tool_call_id,
        "sink_instruction_type": sec.get("instruction_type"),
        "sink_instruction_category": sec.get("instruction_category"),
        "sink_trustworthiness": sec.get("trustworthiness"),
        "sink_confidentiality": sec.get("confidentiality"),
        "sink_prop_trustworthiness": sec.get("prop_trustworthiness"),
        "sink_prop_confidentiality": sec.get("prop_confidentiality"),
        "sink_risk": sec.get("risk"),
        "sink_reversible": sec.get("reversible"),
        "respond_content_present": respond_content_present,
        "relational_actual_level": actual,
        "relational_required_level": required,
        "prior_non_comm_state_change": _history_has_non_comm_state_change(
            instructions,
            latest_instructions,
        ),
        "current_non_comm_state_change": any(
            event.get("planned")
            and event.get("event_type") == "business_mutation"
            and event.get("flow_kind") in {"business_side_effect", "persist_side_effect"}
            for event in evidence_events
        ),
        "prior_calendar_state_change": _history_has_tool_matching(
            instructions,
            latest_instructions,
            _is_calendar_state_change_tool,
        ),
        "current_calendar_state_change": _tool_names_match(
            current_accepted_tool_names or [],
            _is_calendar_state_change_tool,
        ),
        "prior_crm_state_change": _history_has_tool_matching(
            instructions,
            latest_instructions,
            _is_crm_state_change_tool,
        ),
        "current_crm_state_change": _tool_names_match(
            current_accepted_tool_names or [],
            _is_crm_state_change_tool,
        ),
        "prior_payment_state_change": _history_has_tool_matching(
            instructions,
            latest_instructions,
            _is_payment_state_change_tool,
        ),
        "current_payment_state_change": _tool_names_match(
            current_accepted_tool_names or [],
            _is_payment_state_change_tool,
        ),
        "prior_opportunity_state_change": _history_has_tool_matching(
            instructions,
            latest_instructions,
            _is_opportunity_state_change_tool,
        ),
        "current_opportunity_state_change": _tool_names_match(
            current_accepted_tool_names or [],
            _is_opportunity_state_change_tool,
        ),
        "prior_license_state_change": _history_has_tool_matching(
            instructions,
            latest_instructions,
            _is_license_state_change_tool,
        ),
        "current_license_state_change": _tool_names_match(
            current_accepted_tool_names or [],
            _is_license_state_change_tool,
        ),
        "prior_case_terminal_state_change": _history_has_tool_matching(
            instructions,
            latest_instructions,
            _is_case_terminal_state_change_tool,
        ),
        "current_case_terminal_state_change": _tool_names_match(
            current_accepted_tool_names or [],
            _is_case_terminal_state_change_tool,
        ),
        "context_mentions_no_consent": _history_mentions_no_consent(
            instructions,
            latest_instructions,
        ),
        "context_indicates_third_party_consent_risk": _context_indicates_third_party_consent_risk(
            tool_name=tool_name,
            args_dict=args_dict,
            instructions=instructions,
            latest_instructions=latest_instructions,
        ),
        "claim_verification_status": claim_verification.get("status"),
        "claim_verification_reason": claim_verification.get("reason"),
        "outbound_claim_types": claim_verification.get("claim_types") or [],
        "outbound_claims_verified": claim_verification.get("status") == "verified",
        "outbound_claims_missing_evidence": claim_verification.get("status") == "missing_evidence",
        "outbound_claims_contradicted": claim_verification.get("status") == "contradicted",
        "workflow_candidate_name": workflow_candidate.get("name"),
        "workflow_verified": bool(workflow_candidate.get("verified")),
        "workflow_sentinel_can_skip": bool(workflow_candidate.get("sentinel_can_skip")),
        "workflow_candidate_reason": workflow_candidate.get("reason"),
        "evidence_ledger_digest": summarize_ledger(evidence_events, max_events=12),
        "calendar_ui_action": _is_calendar_ui_action(args_dict),
        "public_delegate_payload": _is_public_delegate_payload(args_dict),
    }

    sink_custom = sec.get("custom")
    if isinstance(sink_custom, dict):
        ctx.update(_safe_policy_metadata(sink_custom, set(ctx.keys())))
    for key, value in derive_policy_metadata_from_tool_args(
        args_dict,
        required_metadata or [],
        tool_name=tool_name,
        instruction_type=ctx.get("sink_instruction_type"),
        instruction_category=ctx.get("sink_instruction_category"),
    ).items():
        ctx.setdefault(key, value)

    source_custom = source_sec.get("custom")
    if isinstance(source_custom, dict):
        for key, value in _safe_policy_metadata(source_custom, set()).items():
            prefixed = f"source_{key}"
            if prefixed not in ctx:
                ctx[prefixed] = value

    if flow_kind in {"comm_sink", "delegate_sink", "voice_sink", "respond_sink"}:
        history_labels = _history_source_data_labels(instructions, latest_instructions)
        if history_labels:
            existing = ctx.get("source_data_labels")
            combined: List[str] = []
            seen_labels: Set[str] = set()
            for value in (
                (existing if isinstance(existing, list) else [existing])
                + history_labels
            ):
                if not isinstance(value, str) or not value.strip():
                    continue
                key = value.strip().upper()
                if key in seen_labels:
                    continue
                seen_labels.add(key)
                combined.append(value.strip())
            ctx["source_data_labels"] = combined

    return ctx


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
        custom_bundle = _load_custom_relational_bundle()
        custom_rules = custom_bundle.get("rules")
        custom_rules = custom_rules if isinstance(custom_rules, list) else []
        custom_required_metadata = custom_bundle.get("required_metadata")
        custom_required_metadata = (
            custom_required_metadata
            if isinstance(custom_required_metadata, list)
            else []
        )

        instr_by_tool_call_id: Dict[str, Dict[str, Any]] = {}
        for ins in latest_instructions or []:
            content = ins.get("content")
            if not isinstance(content, dict):
                continue
            tcid = content.get("tool_call_id")
            if isinstance(tcid, str) and tcid:
                instr_by_tool_call_id[tcid] = ins

        errors: List[str] = []
        seen_errors: set[str] = set()
        kept: List[Dict[str, Any]] = []
        accepted_tool_names: List[str] = []
        accepted_tool_ops: List[Dict[str, Any]] = []

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
                accepted_tool_names.append(tool_name)
                accepted_tool_ops.append(
                    {
                        "name": tool_name,
                        "tool_call_id": tool_call_id or "",
                        "args": args_dict,
                    }
                )
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
                    actual=source_trust,
                    phase="tool-call",
                    args_dict=args_dict,
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
                _append_unique_error(errors, seen_errors, user_message)
                continue

            rel_ctx = _build_relational_context(
                tool_name=tool_name,
                tool_call_id=tool_call_id or "",
                ins=ins,
                flow_kind=flow_kind,
                sec=sec,
                instructions=instructions or [],
                latest_instructions=latest_instructions or [],
                current_taint_status=current_taint_status,
                args_dict=args_dict,
                required_metadata=custom_required_metadata,
                current_accepted_tool_names=accepted_tool_names,
                current_accepted_ops=accepted_tool_ops,
            )
            custom_rule = _evaluate_custom_relational_rules(custom_rules, rel_ctx)
            if custom_rule is not None:
                user_message = _custom_relational_block_message(
                    custom_rule,
                    rel_ctx,
                    args_dict=args_dict,
                )
                RUNTIME.audit(
                    phase="policy.relational.custom",
                    trace_id=trace_id,
                    tool=tool_name,
                    decision="BLOCK",
                    reason=_safe_str(custom_rule.get("id"), "custom relational rule"),
                    args=args_dict,
                    extra={
                        "rule_id": custom_rule.get("id"),
                        "rule_source": custom_rule.get("source"),
                        "rule_predicate": custom_rule.get("predicate"),
                        "rule_actual": _actual_snapshot(
                            rel_ctx,
                            custom_rule.get("predicate"),
                        ),
                        "flow_kind": flow_kind,
                        "source_confidentiality": rel_ctx.get(
                            "source_confidentiality"
                        ),
                        "source_trustworthiness": rel_ctx.get(
                            "source_trustworthiness"
                        ),
                        "sink_tool_name": rel_ctx.get("sink_tool_name"),
                        "sink_instruction_type": rel_ctx.get(
                            "sink_instruction_type"
                        ),
                    },
                )
                _append_unique_error(errors, seen_errors, user_message)
                continue

            if (
                flow_kind == "persist_side_effect"
                and _is_allowed_gdpr_contact_delete(
                    tool_name=tool_name,
                    args_dict=args_dict,
                    instructions=instructions or [],
                    latest_instructions=latest_instructions or [],
                )
            ):
                RUNTIME.audit(
                    phase="policy.relational",
                    trace_id=trace_id,
                    tool=tool_name,
                    decision="ALLOW",
                    reason="CRM contact delete allowed in explicit GDPR/personal-data deletion context",
                    args=args_dict,
                    extra={
                        "flow_kind": flow_kind,
                        "gdpr_contact_delete_allowed": True,
                    },
                )
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                accepted_tool_names.append(tool_name)
                accepted_tool_ops.append(
                    {
                        "name": tool_name,
                        "tool_call_id": tool_call_id or "",
                        "args": args_dict,
                    }
                )
                continue

            if (
                flow_kind == "persist_side_effect"
                and _is_allowed_calendar_cancellation_change(
                    tool_name=tool_name,
                    instructions=instructions or [],
                    latest_instructions=latest_instructions or [],
                )
            ):
                RUNTIME.audit(
                    phase="policy.relational",
                    trace_id=trace_id,
                    tool=tool_name,
                    decision="ALLOW",
                    reason="calendar cancellation change allowed in explicit meeting-cancellation context",
                    args=args_dict,
                    extra={
                        "flow_kind": flow_kind,
                        "calendar_cancellation_allowed": True,
                    },
                )
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                accepted_tool_names.append(tool_name)
                accepted_tool_ops.append(
                    {
                        "name": tool_name,
                        "tool_call_id": tool_call_id or "",
                        "args": args_dict,
                    }
                )
                continue

            ok, actual, required, extra = _evaluate_flow(
                flow_kind,
                sec,
                args_dict,
                tool_name=tool_name,
                current_taint_status=current_taint_status,
            )
            source_trust = extra["source_trustworthiness"]
            source_conf = extra["source_confidentiality"]

            if ok:
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                accepted_tool_names.append(tool_name)
                accepted_tool_ops.append(
                    {
                        "name": tool_name,
                        "tool_call_id": tool_call_id or "",
                        "args": args_dict,
                    }
                )
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
            elif flow_kind in {"ui_side_effect", "exec_side_effect", "business_side_effect", "persist_side_effect"}:
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
                actual=actual,
                phase="tool-call",
                args_dict=args_dict,
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
            _append_unique_error(errors, seen_errors, user_message)

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
                user_msg = _friendly_respond_message(
                    source_trust,
                    source_conf,
                    required,
                    reason,
                    actual=source_trust,
                    phase="respond",
                )
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

            rel_ctx = _build_relational_context(
                tool_name="@respond",
                tool_call_id="",
                ins=respond_ins,
                flow_kind="respond_sink",
                sec=sec,
                instructions=instructions or [],
                latest_instructions=latest_instructions or [],
                current_taint_status=current_taint_status,
                args_dict={},
                respond_content_present=True,
                required_metadata=custom_required_metadata,
                current_accepted_tool_names=accepted_tool_names,
                current_accepted_ops=accepted_tool_ops,
            )
            custom_rule = _evaluate_custom_relational_rules(custom_rules, rel_ctx)
            if custom_rule is not None:
                user_msg = _custom_relational_block_message(
                    custom_rule,
                    rel_ctx,
                    args_dict={},
                )
                RUNTIME.audit(
                    phase="policy.relational.custom",
                    trace_id=trace_id,
                    tool="@instruction",
                    decision="BLOCK",
                    reason=_safe_str(custom_rule.get("id"), "custom relational rule"),
                    args={},
                    extra={
                        "rule_id": custom_rule.get("id"),
                        "rule_source": custom_rule.get("source"),
                        "rule_predicate": custom_rule.get("predicate"),
                        "rule_actual": _actual_snapshot(
                            rel_ctx,
                            custom_rule.get("predicate"),
                        ),
                        "flow_kind": "respond_sink",
                        "source_confidentiality": rel_ctx.get(
                            "source_confidentiality"
                        ),
                        "source_trustworthiness": rel_ctx.get(
                            "source_trustworthiness"
                        ),
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
                user_msg = _friendly_respond_message(
                    source_trust,
                    source_conf,
                    required,
                    reason,
                    actual=actual,
                    phase="respond",
                )
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
