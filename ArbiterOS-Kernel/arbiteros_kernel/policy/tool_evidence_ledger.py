"""Tool evidence ledger utilities.

This module converts tool calls into structured evidence events that policies can
use without depending on raw tool arguments alone. The current built-in adapters
cover the existing CRM/calendar/messaging/payment tool families while keeping the
ledger interface independent of any single benchmark or protocol.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from arbiteros_kernel.mcp_tool_classification import classify_mcp_tool_flow


_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,63}\b"
)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d .()/-]{6,}\d)(?!\d)")

_INTERNAL_DOMAIN_EXACT = {
    "company.com",
    "localhost",
}
_INTERNAL_DOMAIN_SUFFIXES = (
    ".internal",
    ".local",
    ".localhost",
    ".lan",
)

_CLAIM_PATTERNS: Dict[str, re.Pattern[str]] = {
    "calendar_cancelled": re.compile(
        r"(?is)\b("
        r"meeting\s+status\s*:\s*(?:cancelled|canceled|rescheduled)|"
        r"(?:meeting|calendar\s+event|call|demo|review|discussion).{0,120}"
        r"(?:has\s+been\s+)?(?:cancelled|canceled|rescheduled|deleted)|"
        r"(?:request\s+to\s+cancel|cancellation\s+request).{0,160}"
        r"(?:processed|completed|cancelled|canceled)"
        r")\b"
    ),
    "payment_failed": re.compile(
        r"(?is)\b(payment|invoice|transaction|wire|ach|bank\s+transfer|remittance)"
        r"\b.{0,160}\b(failed|declined|rejected|unsuccessful|not\s+received|"
        r"missing|resubmit|re-submit|send\s+again|wire\s+again|pay\s+again)\b"
    ),
    "payment_received": re.compile(
        r"(?is)\b(payment|invoice|transaction|wire|ach|bank\s+transfer|remittance)"
        r"\b.{0,160}\b(received|reconciled|settled|confirmed|successful|paid)\b"
    ),
    "license_ready": re.compile(
        r"(?is)\b(license|licence|entitlement|provisioning|workspace|account)"
        r"\b.{0,160}\b(active|operational|provisioned|enabled|ready|activated)\b|"
        r"\b(active|operational|provisioned|enabled|ready|activated)"
        r"\b.{0,160}\b(license|licence|entitlement|workspace)\b"
    ),
    "case_created": re.compile(
        r"(?is)\b(case|support\s+case|ticket)\b.{0,100}\b(created|opened|filed)\b|"
        r"\b(created|opened|filed)\b.{0,100}\b(case|support\s+case|ticket)\b"
    ),
    "case_updated": re.compile(
        r"(?is)\b(case|support\s+case|ticket)\b.{0,100}"
        r"\b(updated|assigned|escalated|resolved|closed)\b"
    ),
    "contact_updated": re.compile(
        r"(?is)\b(contact|phone|email|records?|information)\b.{0,120}"
        r"\b(updated|processed|changed|corrected)\b"
    ),
    "duplicate_consolidated": re.compile(
        r"(?is)\b(duplicate|duplicates)\b.{0,120}\b(consolidated|merged|deduplicated)\b|"
        r"\b(consolidated|merged|deduplicated)\b.{0,120}\b(duplicate|duplicates)\b"
    ),
    "stale_cleanup": re.compile(
        r"(?is)\b(stale\s+lead|stale\s+leads|flagged\s+for\s+cleanup|"
        r"data\s+cleanup|cleanup\s+request)\b"
    ),
    "data_quality_report": re.compile(
        r"(?is)\b(data\s+quality|missing\s+phone|phone\s+numbers?\s+updated|"
        r"industry\s+standardi[sz](?:ed|ation)|crm\s+data\s+audit|"
        r"records?\s+updated|contacts?\s+updated)\b"
    ),
}

_PIPELINE_LOST_STATUS_RE = re.compile(
    r"(?is)\b(closed\s+lost|closed-lost|lost\s+to\s+competitor|lost\s+pipeline)\b"
)
_PIPELINE_LOST_ACTION_RE = re.compile(
    r"(?is)("
    r"\b(?:mark(?:ed)?|chang(?:ed)?|updat(?:ed)?|mov(?:ed)?|set|"
    r"classif(?:y|ied)|clos(?:e|ed)(?!\s+(?:lost|won))|convert(?:ed)?|stage|status)\b"
    r".{0,140}\b(?:closed\s+lost|closed-lost|lost\s+to\s+competitor|lost\s+pipeline)\b|"
    r"\b(?:closed\s+lost|closed-lost|lost\s+to\s+competitor|lost\s+pipeline)\b"
    r".{0,140}\b(?:processed|completed|updated|changed|moved|marked|set|closed|classified)\b|"
    r"\b(?:stage|status)\s*:\s*(?:closed\s+lost|closed-lost|lost\s+to\s+competitor)\b"
    r")"
)
_PIPELINE_REPORT_CONTEXT_RE = re.compile(
    r"(?is)\b("
    r"exclud(?:e|es|ed|ing)|forecast|summary|report|breakdown|count|counts|"
    r"total\s+active|active\s+opportunities|by\s+stage|stage\s+distribution"
    r")\b"
)
_SOCIAL_SCORING_PROXY_RE = re.compile(
    r"(?is)\b("
    r"geograph(?:y|ic|ical)|region(?:al)?|country|zip|postal|postcode|"
    r"location|territory|area|neighbou?rhood|rural|developing\s+market|"
    r"regional\s+assessment|region-based|location-based|low-value\s+segment"
    r")\b"
)
_SOCIAL_SCORING_ADVERSE_RE = re.compile(
    r"(?is)\b("
    r"closed\s+lost|abandon(?:ed)?|deprioriti[sz](?:e|ed|ation)?|"
    r"do\s+not\s+pursue|reject(?:ed)?|disqualif(?:y|ied)|"
    r"probability\s*(?:to|=|:)?\s*0|low\s+(?:priority|conversion\s+potential)|"
    r"low\s+conversion|flagged|low-value\s+segment|extended\s+sla|"
    r"72[- ]?hour\s+response|delayed\s+response|reduced\s+support"
    r")\b"
)

_DESTRUCTIVE_TEXT_RE = re.compile(
    r"(?is)\b(delete|purge|erase|account\s+closure|close\s+account|"
    r"refund|chargeback|subscription\s+cancel|terminate|deactivate)\b"
)

_GENERIC_READ_ACTIONS = {
    "cat",
    "describe",
    "download",
    "fetch",
    "find",
    "get",
    "grep",
    "list",
    "lookup",
    "read",
    "search",
    "show",
    "status",
    "view",
}
_GENERIC_SEND_ACTIONS = {
    "call",
    "chat",
    "email",
    "forward",
    "invite",
    "message",
    "notify",
    "post",
    "publish",
    "reply",
    "send",
    "share",
}
_GENERIC_MUTATION_ACTIONS = {
    "add",
    "apply",
    "approve",
    "assign",
    "book",
    "cancel",
    "clear",
    "close",
    "create",
    "delete",
    "disable",
    "edit",
    "enable",
    "exec",
    "execute",
    "grant",
    "install",
    "join",
    "launch",
    "merge",
    "modify",
    "move",
    "patch",
    "pay",
    "refund",
    "remove",
    "revoke",
    "run",
    "save",
    "set",
    "shell",
    "submit",
    "transfer",
    "update",
    "upload",
    "upsert",
    "write",
}
_FILE_TOOL_NAMES = {"apply_patch", "edit", "read", "write"}
_PROCESS_TOOL_NAMES = {"bash", "exec", "process", "shell"}
_WEB_TOOL_NAMES = {"browser", "web_fetch", "web_search", "websearch"}


def _safe_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _safe_lower(value: Any) -> str:
    return _safe_str(value).lower()


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_text(value: Any, *, max_chars: int = 24000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            text = str(value)
    text = text.strip()
    if len(text) > max_chars:
        return text[:max_chars]
    return text


def _iter_text_values(value: Any, *, max_items: int = 300) -> Iterable[str]:
    seen = 0

    def walk(node: Any) -> Iterable[str]:
        nonlocal seen
        if seen >= max_items:
            return
        if isinstance(node, str):
            text = node.strip()
            if text:
                seen += 1
                yield text
            return
        if isinstance(node, Mapping):
            for item in node.values():
                yield from walk(item)
            return
        if isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            for item in node:
                yield from walk(item)
            return
        if node is not None and not isinstance(node, (bool, int, float)):
            text = str(node).strip()
            if text:
                seen += 1
                yield text

    yield from walk(value)


def _text_blob(value: Any, *, max_items: int = 300) -> str:
    return "\n".join(_iter_text_values(value, max_items=max_items))


def _domain(email: str) -> str:
    if "@" not in email:
        return ""
    return email.rsplit("@", 1)[1].strip().lower()


def is_internal_email(email: str) -> bool:
    domain = _domain(email)
    if not domain:
        return False
    if domain in _INTERNAL_DOMAIN_EXACT:
        return True
    return any(domain.endswith(suffix) for suffix in _INTERNAL_DOMAIN_SUFFIXES)


def extract_emails(value: Any) -> List[str]:
    text = _coerce_text(value)
    out: List[str] = []
    seen: Set[str] = set()
    for match in _EMAIL_RE.finditer(text):
        email = match.group(0).lower()
        if email in seen:
            continue
        seen.add(email)
        out.append(email)
    return out


def _extract_recipients(args: Mapping[str, Any]) -> List[str]:
    values = {
        "to": args.get("to"),
        "cc": args.get("cc"),
        "bcc": args.get("bcc"),
        "recipient": args.get("recipient"),
        "recipients": args.get("recipients"),
        "email": args.get("email"),
    }
    return extract_emails(values)


def _external_recipients(recipients: Sequence[str]) -> List[str]:
    return [email for email in recipients if not is_internal_email(email)]


def _extract_channel(args: Mapping[str, Any]) -> str:
    return _safe_str(
        args.get("channel_name")
        or args.get("channel")
        or args.get("channel_id")
        or args.get("room")
    ).lstrip("#")


def _extract_message_text(args: Mapping[str, Any]) -> str:
    return _text_blob(
        {
            "subject": args.get("subject"),
            "body": args.get("body"),
            "text": args.get("text"),
            "message": args.get("message"),
            "content": args.get("content"),
            "description": args.get("description"),
        },
        max_items=80,
    )


def extract_claim_types_from_text(text: str) -> List[str]:
    out: List[str] = []
    for claim_type, pattern in _CLAIM_PATTERNS.items():
        if pattern.search(text or ""):
            out.append(claim_type)
    if text_contains_pipeline_lost_claim(text or ""):
        out.append("pipeline_lost")
    if text_contains_social_scoring_adverse(text or ""):
        out.append("social_scoring_adverse")
    return out


def text_contains_pipeline_lost_claim(text: str) -> bool:
    value = text or ""
    if not _PIPELINE_LOST_STATUS_RE.search(value):
        return False
    if _PIPELINE_REPORT_CONTEXT_RE.search(value):
        return False
    if _PIPELINE_LOST_ACTION_RE.search(value):
        return True
    return False


def text_contains_social_scoring_adverse(text: str) -> bool:
    value = text or ""
    proxy_matches = list(_SOCIAL_SCORING_PROXY_RE.finditer(value))
    adverse_matches = list(_SOCIAL_SCORING_ADVERSE_RE.finditer(value))
    if not proxy_matches or not adverse_matches:
        return False
    for proxy_match in proxy_matches:
        for adverse_match in adverse_matches:
            if abs(proxy_match.start() - adverse_match.start()) <= 360:
                return True
    return False


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = _safe_str(value)
        if text:
            return text
    return ""


def _module_to_object_type(module_name: str, tool_name: str) -> str:
    module = _safe_lower(module_name).replace("_", " ")
    mapping = {
        "accounts": "Account",
        "account": "Account",
        "contacts": "Contact",
        "contact": "Contact",
        "leads": "Lead",
        "lead": "Lead",
        "opportunities": "Opportunity",
        "opportunity": "Opportunity",
        "cases": "Case",
        "case": "Case",
        "tasks": "Task",
        "task": "Task",
        "notes": "Note",
        "note": "Note",
    }
    if module in mapping:
        return mapping[module]
    name = tool_name.lower()
    if "opportunit" in name:
        return "Opportunity"
    if "contact" in name:
        return "Contact"
    if "lead" in name:
        return "Lead"
    if "case" in name:
        return "Case"
    if "account" in name:
        return "Account"
    return "CRMRecord"


def _extract_record_id(args: Mapping[str, Any]) -> str:
    for key in (
        "record_id",
        "id",
        "lead_id",
        "contact_id",
        "account_id",
        "opportunity_id",
        "case_id",
        "ticket_id",
        "issue_id",
        "task_id",
        "invoice_id",
        "payment_id",
        "resource_id",
        "parent_id",
        "event_id",
        "meeting_id",
    ):
        value = _safe_str(args.get(key))
        if value:
            return value
    return ""


def _extract_fields(args: Mapping[str, Any]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for key in ("attributes", "fields", "values", "data", "record"):
        block = args.get(key)
        if isinstance(block, Mapping):
            fields.update({str(k): v for k, v in block.items()})
    for key, value in args.items():
        if key in {
            "attributes",
            "fields",
            "values",
            "data",
            "record",
            "reference_tool_id",
            "_arbiteros_raw_tool_name",
        }:
            continue
        lowered = str(key).lower()
        if lowered.endswith("_id") or lowered in {"id", "module", "module_name", "type"}:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            fields.setdefault(str(key), value)
    return fields


def _success_from_result(result: Any) -> Optional[bool]:
    if result is None:
        return None
    if isinstance(result, Mapping):
        for key in ("ok", "success", "succeeded", "created", "updated", "deleted"):
            if isinstance(result.get(key), bool):
                return bool(result.get(key))
        raw = result.get("raw")
        if isinstance(raw, str):
            lowered = raw.lower()
            if any(token in lowered for token in ("error", "failed", "unauthorized")):
                return False
            if any(token in lowered for token in ("success", '"ok":true', "'ok': true")):
                return True
        return True
    if isinstance(result, str):
        lowered = result.lower()
        if any(token in lowered for token in ("error", "failed", "unauthorized")):
            return False
        if lowered.strip():
            return True
    return None


def _tool_action(tool_name: str) -> str:
    name = tool_name.lower()
    if "__" in name:
        action = name.split("__", 1)[1]
    else:
        action = name
    for prefix in ("create", "update", "delete", "send", "post", "reply", "forward", "join"):
        if action == prefix or action.startswith(f"{prefix}_"):
            return prefix
    if "create" in action:
        return "create"
    if "update" in action or "set" in action:
        return "update"
    if "delete" in action or "remove" in action or "cancel" in action:
        return "delete"
    if "send" in action or "post" in action or "reply" in action:
        return "send"
    return action


def _action_tokens(action: str) -> Set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", action.lower()) if part}


def _generic_event_type(
    *,
    tool_name: str,
    flow_kind: str,
    action: str,
    planned: bool,
    result: Any,
) -> str:
    name = tool_name.lower()
    tokens = _action_tokens(action) | _action_tokens(name)
    if flow_kind == "comm_sink" or tokens & _GENERIC_SEND_ACTIONS:
        return "comm_sink"
    if flow_kind in {"business_side_effect", "persist_side_effect"}:
        return "business_mutation"
    if tokens & _GENERIC_MUTATION_ACTIONS:
        return "business_mutation"
    if flow_kind == "read_sensitive" or tokens & _GENERIC_READ_ACTIONS:
        return "read_evidence"
    if planned or result is not None:
        return "tool_event"
    return ""


def _generic_object_type(tool_name: str, event_type: str) -> str:
    name = tool_name.lower()
    if name in _FILE_TOOL_NAMES or any(token in name for token in ("file", "path", "patch")):
        return "File"
    if name in _PROCESS_TOOL_NAMES or any(token in name for token in ("bash", "shell", "process", "exec")):
        return "Process"
    if name in _WEB_TOOL_NAMES or "browser" in name or "web" in name:
        return "WebResource"
    if event_type == "comm_sink":
        return "Message"
    if event_type == "read_evidence":
        return "InformationSource"
    return "ToolResource"


def _resource_hint(args: Mapping[str, Any]) -> str:
    for key in (
        "path",
        "file_path",
        "target_path",
        "destination_path",
        "output_path",
        "url",
        "uri",
        "name",
        "title",
        "subject",
        "query",
        "command",
        "cmd",
    ):
        text = _safe_str(args.get(key))
        if text:
            return text[:240]
    return ""


def event_from_tool_call(
    *,
    tool_name: str,
    tool_call_id: str = "",
    args: Optional[Mapping[str, Any]] = None,
    result: Any = None,
    source_trustworthiness: str = "UNKNOWN",
    source_tool_call_id: str = "",
    planned: bool = False,
) -> Optional[Dict[str, Any]]:
    args = args if isinstance(args, Mapping) else {}
    name = _safe_str(tool_name)
    if not name:
        return None
    lowered = name.lower()
    flow_kind = classify_mcp_tool_flow(lowered)
    action = _tool_action(lowered)
    success = None if planned else _success_from_result(result)
    if planned:
        success = None

    event: Dict[str, Any] = {
        "tool_name": name,
        "tool_call_id": _safe_str(tool_call_id),
        "flow_kind": flow_kind,
        "action": action,
        "success": success,
        "planned": bool(planned),
        "source_trustworthiness": source_trustworthiness or "UNKNOWN",
        "source_tool_call_id": source_tool_call_id,
    }

    if lowered.startswith("salesforce__"):
        module = _first_nonempty(args.get("module_name"), args.get("module"), args.get("type"))
        object_type = _module_to_object_type(module, lowered)
        fields = _extract_fields(args)
        event.update(
            {
                "event_type": "business_mutation" if flow_kind != "read_sensitive" else "read_evidence",
                "object_type": object_type,
                "object_id": _extract_record_id(args),
                "object_name": _first_nonempty(
                    fields.get("name"),
                    fields.get("Name"),
                    args.get("name"),
                    args.get("subject"),
                    args.get("title"),
                ),
                "fields": fields,
                "text": _text_blob({"args": args, "result": result}, max_items=120),
            }
        )
        return event

    if lowered.startswith("calendar__"):
        event.update(
            {
                "event_type": "business_mutation" if action in {"create", "update", "delete"} else "read_evidence",
                "object_type": "CalendarEvent",
                "object_id": _extract_record_id(args),
                "object_name": _first_nonempty(
                    args.get("summary"),
                    args.get("title"),
                    args.get("subject"),
                    args.get("name"),
                ),
                "fields": {
                    "summary": args.get("summary") or args.get("title") or args.get("subject"),
                    "start": args.get("start_datetime") or args.get("start_time") or args.get("start"),
                    "attendees": args.get("attendees") or args.get("participants") or args.get("emails"),
                },
                "text": _text_blob({"args": args, "result": result}, max_items=120),
            }
        )
        return event

    if lowered.startswith("zoom__"):
        event.update(
            {
                "event_type": "business_mutation" if action in {"create", "update", "delete", "join"} else "read_evidence",
                "object_type": "Meeting",
                "object_id": _extract_record_id(args),
                "object_name": _first_nonempty(args.get("topic"), args.get("title"), args.get("name")),
                "fields": {
                    "topic": args.get("topic"),
                    "start_time": args.get("start_time"),
                    "duration": args.get("duration"),
                },
                "text": _text_blob({"args": args, "result": result}, max_items=120),
            }
        )
        return event

    if lowered.startswith("gmail__") or lowered.startswith("email__") or lowered == "message":
        recipients = _extract_recipients(args)
        message_text = _extract_message_text(args)
        event.update(
            {
                "event_type": "comm_sink",
                "object_type": "Message",
                "recipients": recipients,
                "external_recipients": _external_recipients(recipients),
                "message_text": message_text,
                "claim_types": extract_claim_types_from_text(message_text),
                "text": message_text,
            }
        )
        return event

    if lowered.startswith("slack__") or lowered.startswith("telegram__"):
        channel = _extract_channel(args)
        message_text = _extract_message_text(args)
        event.update(
            {
                "event_type": "comm_sink",
                "object_type": "Message",
                "channel": channel,
                "recipients": _extract_recipients(args),
                "external_recipients": [],
                "message_text": message_text,
                "claim_types": extract_claim_types_from_text(message_text),
                "text": message_text,
            }
        )
        return event

    if any(
        lowered.startswith(prefix)
        for prefix in ("payment__", "payments__", "stripe__", "paypal__", "billing__", "bank__")
    ):
        event.update(
            {
                "event_type": "business_mutation" if flow_kind in {"business_side_effect", "persist_side_effect"} else "read_evidence",
                "object_type": "Payment",
                "object_id": _extract_record_id(args),
                "fields": _extract_fields(args),
                "text": _text_blob({"args": args, "result": result}, max_items=120),
            }
        )
        return event

    generic_event_type = _generic_event_type(
        tool_name=lowered,
        flow_kind=flow_kind,
        action=action,
        planned=planned,
        result=result,
    )
    if not generic_event_type:
        return None

    recipients = _extract_recipients(args)
    message_text = _extract_message_text(args)
    generic_text = message_text or _text_blob({"args": args, "result": result}, max_items=120)
    fields = _extract_fields(args)
    event.update(
        {
            "event_type": generic_event_type,
            "object_type": _generic_object_type(lowered, generic_event_type),
            "object_id": _extract_record_id(args),
            "object_name": _resource_hint(args),
            "fields": fields,
            "text": generic_text,
        }
    )
    if generic_event_type == "comm_sink":
        event.update(
            {
                "recipients": recipients,
                "external_recipients": _external_recipients(recipients),
                "message_text": message_text,
                "claim_types": extract_claim_types_from_text(message_text),
            }
        )
    return event


def _instruction_content(ins: Mapping[str, Any]) -> Mapping[str, Any]:
    content = ins.get("content")
    return content if isinstance(content, Mapping) else {}


def _instruction_tool_name(ins: Mapping[str, Any]) -> str:
    content = _instruction_content(ins)
    return _safe_str(content.get("tool_name") or ins.get("tool_name") or content.get("name") or ins.get("name"))


def _instruction_tool_call_id(ins: Mapping[str, Any]) -> str:
    content = _instruction_content(ins)
    return _safe_str(
        content.get("tool_call_id") or ins.get("tool_call_id") or content.get("id") or ins.get("id")
    )


def _instruction_args(ins: Mapping[str, Any]) -> Dict[str, Any]:
    content = _instruction_content(ins)
    for key in ("arguments", "args", "input", "params"):
        value = content.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    for key in ("arguments", "args", "input", "params"):
        value = ins.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _instruction_result(ins: Mapping[str, Any]) -> Any:
    content = _instruction_content(ins)
    for key in ("result", "tool_result", "output", "response", "value"):
        if key in content:
            return content.get(key)
    for key in ("result", "tool_result", "output", "response", "value"):
        if key in ins:
            return ins.get(key)
    return None


def _instruction_source_trust(ins: Mapping[str, Any]) -> str:
    sec = ins.get("security_type")
    if isinstance(sec, Mapping):
        return _safe_str(sec.get("prop_trustworthiness") or sec.get("trustworthiness")) or "UNKNOWN"
    return "UNKNOWN"


def build_tool_evidence_ledger(
    instructions: Sequence[Mapping[str, Any]] | None,
    latest_instructions: Sequence[Mapping[str, Any]] | None = None,
    *,
    current_ops: Sequence[Mapping[str, Any]] | None = None,
    max_events: int = 80,
) -> List[Dict[str, Any]]:
    seen_instruction_ids: Set[str] = set()
    events: List[Dict[str, Any]] = []

    def add_event(event: Optional[Dict[str, Any]]) -> None:
        if not event:
            return
        events.append(event)

    for seq in (instructions or [], latest_instructions or []):
        for ins in seq:
            if not isinstance(ins, Mapping):
                continue
            identity = _safe_str(ins.get("id")) or f"{id(ins)}"
            if identity in seen_instruction_ids:
                continue
            seen_instruction_ids.add(identity)
            tool_name = _instruction_tool_name(ins)
            if not tool_name:
                continue
            add_event(
                event_from_tool_call(
                    tool_name=tool_name,
                    tool_call_id=_instruction_tool_call_id(ins),
                    args=_instruction_args(ins),
                    result=_instruction_result(ins),
                    source_trustworthiness=_instruction_source_trust(ins),
                    planned=False,
                )
            )

    for op in current_ops or []:
        if not isinstance(op, Mapping):
            continue
        add_event(
            event_from_tool_call(
                tool_name=_safe_str(op.get("name") or op.get("tool_name")),
                tool_call_id=_safe_str(op.get("tool_call_id")),
                args=op.get("args") if isinstance(op.get("args"), Mapping) else {},
                result=None,
                planned=True,
            )
        )

    if max_events > 0 and len(events) > max_events:
        return events[-max_events:]
    return events


def _event_text(event: Mapping[str, Any]) -> str:
    return _coerce_text(
        {
            "object_type": event.get("object_type"),
            "object_name": event.get("object_name"),
            "fields": event.get("fields"),
            "text": event.get("text") or event.get("message_text"),
        },
        max_chars=4000,
    )


def _event_has_success(event: Mapping[str, Any]) -> bool:
    return event.get("success") is not False


def _has_event(
    events: Sequence[Mapping[str, Any]],
    *,
    object_types: Set[str] | None = None,
    actions: Set[str] | None = None,
    text_pattern: re.Pattern[str] | None = None,
) -> bool:
    for event in events:
        if not _event_has_success(event):
            continue
        if object_types and _safe_str(event.get("object_type")) not in object_types:
            continue
        if actions and _safe_str(event.get("action")) not in actions:
            continue
        if text_pattern and not text_pattern.search(_event_text(event)):
            continue
        return True
    return False


def verify_claims_for_text(
    text: str,
    events: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    claim_types = extract_claim_types_from_text(text or "")
    if not claim_types:
        return {
            "status": "none",
            "claim_types": [],
            "verified_claim_types": [],
            "missing_claim_types": [],
            "contradicted_claim_types": [],
            "reason": "no completion or fact claim detected",
        }

    verified: List[str] = []
    missing: List[str] = []
    contradicted: List[str] = []

    for claim_type in claim_types:
        if claim_type == "calendar_cancelled":
            if _has_event(events, object_types={"CalendarEvent"}, actions={"delete", "update"}):
                verified.append(claim_type)
            else:
                missing.append(claim_type)
            continue
        if claim_type == "pipeline_lost":
            if _has_event(
                events,
                object_types={"Opportunity"},
                text_pattern=re.compile(r"(?is)\b(closed\s+lost|lost\s+to\s+competitor)\b"),
            ):
                verified.append(claim_type)
            else:
                missing.append(claim_type)
            continue
        if claim_type in {"payment_failed", "payment_received"}:
            if _has_event(
                events,
                object_types={"Payment", "Opportunity", "Account", "Note"},
                text_pattern=re.compile(r"(?is)\b(payment|invoice|transaction|wire|ach|reconciled)\b"),
            ):
                verified.append(claim_type)
            else:
                missing.append(claim_type)
            continue
        if claim_type == "license_ready":
            if _has_event(
                events,
                object_types={"License", "Entitlement", "Account", "CRMRecord", "Note"},
                text_pattern=re.compile(r"(?is)\b(license|licence|entitlement|provision|workspace)\b"),
            ):
                verified.append(claim_type)
            else:
                missing.append(claim_type)
            continue
        if claim_type == "case_created":
            if _has_event(events, object_types={"Case"}, actions={"create"}):
                verified.append(claim_type)
            else:
                missing.append(claim_type)
            continue
        if claim_type == "case_updated":
            if _has_event(events, object_types={"Case"}, actions={"create", "update"}):
                verified.append(claim_type)
            else:
                missing.append(claim_type)
            continue
        if claim_type == "contact_updated":
            if _has_event(events, object_types={"Contact", "Lead", "Account"}, actions={"update"}):
                verified.append(claim_type)
            else:
                missing.append(claim_type)
            continue
        if claim_type == "duplicate_consolidated":
            if _has_event(
                events,
                object_types={"Lead"},
                actions={"update"},
                text_pattern=re.compile(r"(?is)\b(duplicate|consolidated|dead)\b"),
            ):
                verified.append(claim_type)
            else:
                missing.append(claim_type)
            continue
        if claim_type == "stale_cleanup":
            if _has_event(
                events,
                object_types={"Lead"},
                actions={"update"},
                text_pattern=re.compile(r"(?is)\b(stale|cleanup|dead)\b"),
            ):
                verified.append(claim_type)
            else:
                missing.append(claim_type)
            continue
        if claim_type == "data_quality_report":
            if _has_event(events, object_types={"Contact", "Lead", "Account", "Opportunity"}, actions={"update"}):
                verified.append(claim_type)
            else:
                missing.append(claim_type)
            continue
        if claim_type == "social_scoring_adverse":
            contradicted.append(claim_type)
            continue
        missing.append(claim_type)

    if contradicted:
        status = "contradicted"
    elif missing:
        status = "missing_evidence"
    else:
        status = "verified"

    return {
        "status": status,
        "claim_types": claim_types,
        "verified_claim_types": verified,
        "missing_claim_types": missing,
        "contradicted_claim_types": contradicted,
        "reason": f"verified={verified}; missing={missing}; contradicted={contradicted}",
    }


def verify_claims_for_tool_args(
    *,
    tool_name: str,
    args: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    reviewed_event = event_from_tool_call(
        tool_name=tool_name,
        args=args,
        planned=True,
    )
    if not reviewed_event or reviewed_event.get("event_type") != "comm_sink":
        return {
            "status": "none",
            "claim_types": [],
            "verified_claim_types": [],
            "missing_claim_types": [],
            "contradicted_claim_types": [],
            "reason": "tool is not a communication sink",
        }
    return verify_claims_for_text(
        str(reviewed_event.get("message_text") or reviewed_event.get("text") or ""),
        events,
    )


def summarize_ledger(events: Sequence[Mapping[str, Any]], *, max_events: int = 12) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for event in list(events)[-max_events:]:
        fields = event.get("fields")
        field_names = sorted(str(key) for key in fields.keys()) if isinstance(fields, Mapping) else []
        text = _safe_str(event.get("text") or event.get("message_text"))
        item: Dict[str, Any] = {
            "tool_name": event.get("tool_name"),
            "flow_kind": event.get("flow_kind"),
            "action": event.get("action"),
            "event_type": event.get("event_type"),
            "object_type": event.get("object_type"),
            "object_id": event.get("object_id"),
            "object_name": event.get("object_name"),
            "success": event.get("success"),
            "planned": event.get("planned"),
            "field_names": field_names[:12],
        }
        if event.get("recipients"):
            item["recipients"] = event.get("recipients")
        if event.get("external_recipients"):
            item["external_recipients"] = event.get("external_recipients")
        if event.get("channel"):
            item["channel"] = event.get("channel")
        if event.get("claim_types"):
            item["claim_types"] = event.get("claim_types")
        if text:
            item["text_excerpt"] = re.sub(r"\s+", " ", text)[:320]
        out.append({k: v for k, v in item.items() if v not in (None, "", [], {})})
    return out


def _events_have_only_internal_comm(events: Sequence[Mapping[str, Any]]) -> bool:
    for event in events:
        if event.get("event_type") != "comm_sink":
            continue
        if event.get("external_recipients"):
            return False
    return True


def infer_workflow_candidate(
    *,
    planned_ops: Sequence[Mapping[str, Any]] | None,
    instructions: Sequence[Mapping[str, Any]] | None,
    latest_instructions: Sequence[Mapping[str, Any]] | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    events = list(events or build_tool_evidence_ledger(instructions, latest_instructions, current_ops=planned_ops))
    text = "\n".join(
        [
            _text_blob([ins for ins in (instructions or [])[-20:]], max_items=240),
            _text_blob([ins for ins in (latest_instructions or [])[-10:]], max_items=120),
            _text_blob([op for op in (planned_ops or [])], max_items=120),
        ]
    )
    lowered = text.lower()
    planned_names = {_safe_lower(op.get("name") or op.get("tool_name")) for op in (planned_ops or [])}
    has_destructive_text = bool(_DESTRUCTIVE_TEXT_RE.search(text))
    has_salesforce_update = any(
        event.get("object_type") in {"Contact", "Lead", "Account", "Opportunity"}
        and event.get("action") == "update"
        and event.get("success") is not False
        for event in events
    )
    has_case_event = any(
        event.get("object_type") == "Case"
        and event.get("action") in {"create", "update"}
        and event.get("success") is not False
        for event in events
    )
    has_calendar_event = any(
        event.get("object_type") in {"CalendarEvent", "Meeting"}
        and event.get("action") in {"create", "update", "delete"}
        and event.get("success") is not False
        for event in events
    )
    internal_comm_only = _events_have_only_internal_comm(events)

    if (
        has_salesforce_update
        and internal_comm_only
        and not has_destructive_text
        and any(
            token in lowered
            for token in (
                "data-quality",
                "data quality",
                "missing phone",
                "phone numbers updated",
                "duplicate lead",
                "duplicate leads",
                "stale lead",
                "industry standard",
                "crm data audit",
            )
        )
    ):
        return {
            "name": "data_quality_workflow",
            "verified": True,
            "sentinel_can_skip": False,
            "reason": "bounded internal data-quality workflow with internal communications only",
        }

    if has_case_event and any(token in lowered for token in ("support", "case", "ticket")):
        return {
            "name": "support_case_workflow",
            "verified": not bool(re.search(r"(?is)\b(rejected|invalid|deny\s+the\s+request)\b", text)),
            "sentinel_can_skip": False,
            "reason": "support case workflow; terminal rejection still requires policy review",
        }

    if has_calendar_event and any(token in lowered for token in ("meeting", "calendar", "schedule", "reschedule")):
        return {
            "name": "calendar_workflow",
            "verified": "zoom__meetings_join" not in planned_names,
            "sentinel_can_skip": False,
            "reason": "calendar scheduling workflow; meeting join is not part of scheduling",
        }

    if internal_comm_only and any(token in lowered for token in ("pipeline", "report", "summary", "lost", "payment", "license")):
        return {
            "name": "internal_management_report",
            "verified": False,
            "sentinel_can_skip": False,
            "reason": "internal report candidate requires fact-check verification before allow",
        }

    return {
        "name": "unknown",
        "verified": False,
        "sentinel_can_skip": False,
        "reason": "no verified workflow contract matched",
    }
