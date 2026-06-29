"""Policy-side direct metadata extraction for custom rules.

This module handles deterministic, low-level fields that can be read from the
current tool arguments without asking kernel/parser lowering for new metadata.
Semantic classification should still live in kernel/parser or LLM lowering.
"""

from __future__ import annotations

import re
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set
from urllib.parse import urlparse

from .tool_evidence_ledger import (
    text_contains_pipeline_lost_claim,
    text_contains_social_scoring_adverse,
)


_EMAIL_RE = re.compile(r"@([A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,63})")
_EMAIL_ADDR_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,63}\b"
)
_HOST_RE = re.compile(
    r"(?<![@A-Za-z0-9_-])"
    r"([A-Za-z0-9][A-Za-z0-9-]{0,62}(?:\.[A-Za-z0-9][A-Za-z0-9-]{0,62})+)"
    r"(?::\d+)?"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d[\d .()/-]{6,}\d)(?!\d)"
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

_INTERNAL_DOMAIN_SUFFIXES = (
    ".local",
    ".internal",
    ".localhost",
    ".lan",
)
_INTERNAL_DOMAIN_EXACT = {
    "localhost",
    # DTAP CRM uses company.com for internal employees and executives.
    "company.com",
    # Other CRM malicious suites register the victim/agent on these org domains.
    "crestpoint.io",
    "nexusflow.io",
    "clearpoint-tech.com",
    "acme.ai",
}

_FINANCIAL_OR_PAYMENT_RE = re.compile(
    r"(?i)\b("
    r"account\s+number|routing\s+number|wire|bank\s+transfer|ach|"
    r"payment\s+portal|payment\s+processing|pay-now|pay\s+now|"
    r"deposit|upfront|chargeback|dispute|transaction\s+id|"
    r"transaction\s+ref(?:erence)?|case\s+id|arn|acquirer|"
    r"refund\s+approved|exclusive\s+discount|expires?\s+in\s+\d+\s+hours"
    r")\b"
)
_COMPLETION_CLAIM_RE = re.compile(
    r"(?is)\b("
    r"(?:has|have|had|was|were|is|are|been)\s+(?:successfully\s+)?"
    r"(?:processed|updated|cancelled|canceled|removed|deleted|credited|refunded)|"
    r"(?:has|have)\s+been\s+(?:received|accepted)\s+and\s+"
    r"(?:processed|approved|cancelled|canceled)|"
    r"(?:will|should)\s+be\s+(?:credited|refunded|processed|cancelled|canceled)|"
    r"(?:cancellation|refund|contact\s+information|data\s+deletion)\s+"
    r"(?:request\s+)?(?:has\s+been\s+)?"
    r"(?:processed|completed|approved|updated|cancelled|canceled|removed)|"
    r"(?:request\s+to\s+cancel|cancellation\s+request|refund\s+request).{0,80}"
    r"(?:received|accepted|approved).{0,60}(?:processed|credited|cancelled|canceled)|"
    r"(?:technical\s+review\s+meeting|meeting).{0,80}"
    r"(?:cancellation|cancel).{0,80}(?:confirmation|processed)|"
    r"refund\s+of\s+\$?[\d,]+.{0,80}(?:approved|credited)|"
    r"(?:your\s+records|your\s+contact\s+information)\s+"
    r"(?:has\s+been\s+)?(?:updated|processed)"
    r")\b"
)
_MARKETING_OR_ONBOARDING_RE = re.compile(
    r"(?is)\b("
    r"newsletter|subscrib(?:e|ed|ing|tion)|onboarding|welcome\s+to|"
    r"sales\s+representative|enterprise\s+solution|healthcare\s+solutions?|"
    r"solutions?\s+team|product\s+features|"
    r"pricing|industry\s+insights|campaign|promotion(?:al)?|"
    r"outreach|demo|trial|quote|introductory\s+email|intro\s+call|"
    r"contact\s+you\s+soon"
    r")\b"
)
_PAYMENT_FALSE_STATUS_RE = re.compile(
    r"(?is)\b("
    r"payment|invoice|transaction|wire|ach|bank\s+transfer|remittance"
    r")\b.{0,140}\b("
    r"failed|declined|rejected|unsuccessful|not\s+received|missing|"
    r"could\s+not\s+be\s+processed|resubmit|re-submit|send\s+again|"
    r"wire\s+again|pay\s+again"
    r")\b"
)
_CALENDAR_CANCEL_CLAIM_RE = re.compile(
    r"(?is)\b("
    r"meeting\s+status\s*:\s*(?:cancelled|canceled|rescheduled)|"
    r"(?:request\s+to\s+cancel|cancellation\s+request).{0,120}"
    r"(?:meeting|calendar\s+event|call|demo|review|discussion).{0,120}"
    r"(?:received|accepted|approved).{0,80}(?:processed|completed|cancelled|canceled)|"
    r"(?:meeting|calendar\s+event|call|demo|review|discussion).{0,100}"
    r"(?:has\s+been\s+)?(?:cancelled|canceled|rescheduled|deleted)|"
    r"(?:cancelled|canceled|rescheduled).{0,100}"
    r"(?:meeting|calendar\s+event|call|demo|review|discussion)"
    r")\b"
)
_LICENSE_READY_CLAIM_RE = re.compile(
    r"(?is)\b("
    r"(?:license|licence|entitlement|provisioning|workspace|account).{0,120}"
    r"(?:active|operational|provisioned|enabled|ready|activated)|"
    r"(?:active|operational|provisioned|enabled|ready|activated).{0,120}"
    r"(?:license|licence|entitlement|workspace|account)"
    r")\b"
)
_CASE_TERMINAL_REJECTION_RE = re.compile(
    r"(?is)\b("
    r"closed[_ -]?rejected|rejected|invalid|do\s+not\s+support|"
    r"close(?:d)?\s+as\s+(?:rejected|invalid)|deny\s+the\s+request"
    r")\b"
)
_STALE_CLEANUP_RE = re.compile(
    r"(?is)\b("
    r"stale\s+lead|flagged\s+for\s+cleanup|data\s+cleanup|"
    r"cleanup\s+request|clean\s+up\s+stale"
    r")\b"
)


def _safe_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _safe_lower(value: Any) -> str:
    return _safe_str(value).lower()


def _csv_env_values(name: str) -> List[str]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _flatten_values(value: Any) -> List[Any]:
    if isinstance(value, list):
        out: List[Any] = []
        for item in value:
            out.extend(_flatten_values(item))
        return out
    if isinstance(value, tuple):
        out = []
        for item in value:
            out.extend(_flatten_values(item))
        return out
    return [value]


def _path_values(root: Any, path: str) -> List[Any]:
    raw = _safe_str(path)
    if not raw:
        return []
    if raw in {"$", "arguments"}:
        return [root]
    if raw.startswith("arguments."):
        raw = raw[len("arguments.") :]

    parts = [part for part in raw.split(".") if part]
    values = [root]
    for part in parts:
        next_values: List[Any] = []
        for value in values:
            if isinstance(value, Mapping) and part in value:
                next_values.append(value[part])
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, Mapping) and part in item:
                        next_values.append(item[part])
        values = next_values
        if not values:
            return []

    out: List[Any] = []
    for value in values:
        out.extend(_flatten_values(value))
    return out


def _paths(spec: Mapping[str, Any]) -> List[str]:
    source = spec.get("source")
    source = source if isinstance(source, Mapping) else {}
    raw = source.get("paths")
    if not isinstance(raw, list):
        return []
    return [path for path in raw if isinstance(path, str) and path.strip()]


def _values_for_spec(args: Mapping[str, Any], spec: Mapping[str, Any]) -> List[Any]:
    out: List[Any] = []
    for path in _paths(spec):
        out.extend(_path_values(args, path))
    return out


def _coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _coerce_generic(values: List[Any], typ: str) -> Any:
    present = [value for value in values if value is not None]
    if not present:
        return None

    if typ == "string":
        for value in present:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (int, float, bool)):
                return str(value)
        return None

    if typ == "boolean":
        for value in present:
            coerced = _coerce_bool(value)
            if coerced is not None:
                return coerced
        return None

    if typ == "number":
        for value in present:
            coerced = _coerce_number(value)
            if coerced is not None:
                return coerced
        return None

    if typ == "integer":
        for value in present:
            coerced = _coerce_number(value)
            if coerced is not None:
                return int(coerced)
        return None

    if typ == "string_array":
        out: List[str] = []
        for value in present:
            if isinstance(value, str) and value.strip():
                out.append(value.strip())
            elif isinstance(value, (int, float, bool)):
                out.append(str(value))
        return out

    if typ == "number_array":
        out_num: List[float] = []
        for value in present:
            coerced = _coerce_number(value)
            if coerced is not None:
                out_num.append(coerced)
        return out_num

    return None


def _text_values(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                out.append(value.strip())
            continue
        if isinstance(value, (int, float, bool)):
            out.append(str(value))
            continue
        if isinstance(value, (Mapping, list, tuple)):
            try:
                out.append(json.dumps(value, ensure_ascii=False, default=str))
            except Exception:
                out.append(str(value))
    return out


def _normalize_domain(value: str) -> str:
    text = value.strip().lower().strip(".,;:()[]{}<>\"'")
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"//{text}")
    host = parsed.hostname or text
    host = host.split("@")[-1].split(":")[0].strip(".").lower()
    return host


def _is_internal_domain(domain: str) -> bool:
    host = _normalize_domain(domain)
    if not host:
        return True
    exact = set(_INTERNAL_DOMAIN_EXACT)
    exact.update(_normalize_domain(item) for item in _csv_env_values("ARBITEROS_INTERNAL_DOMAINS"))
    exact.discard("")
    suffixes = set(_INTERNAL_DOMAIN_SUFFIXES)
    suffixes.update(
        item if item.startswith(".") else f".{item}"
        for item in _csv_env_values("ARBITEROS_INTERNAL_DOMAIN_SUFFIXES")
    )
    if host in exact:
        return True
    return any(host.endswith(suffix) for suffix in suffixes)


def _domains_from_text(text: str) -> List[str]:
    raw = _safe_str(text)
    if not raw:
        return []

    out: List[str] = []
    parsed = urlparse(raw)
    if parsed.hostname:
        out.append(parsed.hostname)

    out.extend(match.group(1) for match in _EMAIL_RE.finditer(raw))
    for match in _HOST_RE.finditer(raw):
        if match.end() < len(raw) and raw[match.end()] == "@":
            continue
        out.append(match.group(1))

    normalized: List[str] = []
    seen: Set[str] = set()
    for item in out:
        domain = _normalize_domain(item)
        if domain and domain not in seen:
            seen.add(domain)
            normalized.append(domain)
    return normalized


def _external_domains_from_values(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for value in values:
        if isinstance(value, Mapping):
            nested = []
            for key in ("email", "address", "to", "domain", "url", "host"):
                if key in value:
                    nested.append(value[key])
            domains = _external_domains_from_values(nested)
        elif isinstance(value, str):
            domains = _domains_from_text(value)
        else:
            domains = []
        for domain in domains:
            if not _is_internal_domain(domain) and domain not in seen:
                seen.add(domain)
                out.append(domain)
    return out


def _source(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = spec.get("source")
    return raw if isinstance(raw, Mapping) else {}


def _source_values(spec: Mapping[str, Any], key: str) -> List[str]:
    raw = _source(spec).get(key)
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if isinstance(raw, list):
        return [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return []


def _source_number(spec: Mapping[str, Any], key: str) -> Optional[float]:
    return _coerce_number(_source(spec).get(key))


def _string_set(raw: Any, *, lower: bool = False, upper: bool = False) -> Set[str]:
    values = raw if isinstance(raw, list) else [raw]
    out: Set[str] = set()
    for value in values:
        text = _safe_str(value)
        if not text:
            continue
        if lower:
            text = text.lower()
        elif upper:
            text = text.upper()
        out.add(text)
    return out


def _matches_allowed(value: Optional[str], allowed: Set[str], *, upper: bool = False) -> bool:
    if not allowed:
        return True
    text = _safe_str(value)
    if not text:
        return False
    return (text.upper() if upper else text.lower()) in allowed


def _applies_to_context(
    spec: Mapping[str, Any],
    *,
    tool_name: Optional[str],
    instruction_type: Optional[str],
    instruction_category: Optional[str],
) -> bool:
    applies_to = spec.get("applies_to")
    if not isinstance(applies_to, Mapping):
        return True

    tools = _string_set(applies_to.get("tools"), lower=True)
    if not _matches_allowed(tool_name, tools):
        return False

    instruction_types = _string_set(applies_to.get("instruction_types"), upper=True)
    if not _matches_allowed(instruction_type, instruction_types, upper=True):
        return False

    categories = _string_set(applies_to.get("categories"), lower=True)
    if not _matches_allowed(instruction_category, categories):
        return False

    return True


def _transform_external_domains(values: List[Any]) -> List[str]:
    return _external_domains_from_values(values)


def _transform_external_domain_count(values: List[Any]) -> int:
    count = 0
    for value in values:
        if _external_domains_from_values([value]):
            count += 1
    return count


def _transform_hostname(values: List[Any]) -> Optional[str]:
    for value in values:
        if isinstance(value, str):
            domains = _domains_from_text(value)
            if domains:
                return domains[0]
    return None


def _transform_has_external_domain(values: List[Any]) -> Optional[bool]:
    if not values:
        return None
    return bool(_external_domains_from_values(values))


def _transform_nonempty(values: List[Any]) -> Optional[bool]:
    if not values:
        return None
    return any(value is not None and str(value).strip() for value in values)


def _transform_equals(values: List[Any], spec: Mapping[str, Any]) -> Optional[bool]:
    expected = _safe_lower(_source(spec).get("value"))
    if not values or not expected:
        return None
    return any(_safe_lower(value) == expected for value in values)


def _transform_in_set(values: List[Any], spec: Mapping[str, Any]) -> Optional[bool]:
    allowed = {_safe_lower(value) for value in _source_values(spec, "values")}
    allowed.discard("")
    if not values or not allowed:
        return None
    return any(_safe_lower(value) in allowed for value in values)


def _transform_contains_any(values: List[Any], spec: Mapping[str, Any]) -> Optional[bool]:
    needles = {_safe_lower(value) for value in _source_values(spec, "values")}
    needles.discard("")
    if not values or not needles:
        return None
    return any(
        needle in _safe_lower(value)
        for value in values
        for needle in needles
    )


def _transform_contains_pii_like(values: List[Any]) -> Optional[bool]:
    if not values:
        return None
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        if _EMAIL_ADDR_RE.search(text) or _SSN_RE.search(text):
            return True
        for match in _PHONE_RE.finditer(text):
            digits = re.sub(r"\D", "", match.group(0))
            # Avoid treating dates, invoice IDs, and chargeback IDs such as
            # 2026-06-15 or INV-2024-0892 as phone-number PII.
            if len(digits) >= 10:
                return True
    return False


def _transform_contains_financial_or_payment(values: List[Any]) -> Optional[bool]:
    if not values:
        return None
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        if _FINANCIAL_OR_PAYMENT_RE.search(text):
            return True
        for domain in _domains_from_text(text):
            if any(part in domain for part in ("pay", "payment", "invoice")):
                return True
    return False


def _transform_contains_completion_claim(values: List[Any]) -> Optional[bool]:
    if not values:
        return None
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text and _COMPLETION_CLAIM_RE.search(text):
            return True
    return False


def _transform_contains_marketing_or_onboarding(values: List[Any]) -> Optional[bool]:
    if not values:
        return None
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text and _MARKETING_OR_ONBOARDING_RE.search(text):
            return True
    return False


def _transform_contains_payment_false_status(values: List[Any]) -> Optional[bool]:
    texts = _text_values(values)
    if not texts:
        return None
    return any(_PAYMENT_FALSE_STATUS_RE.search(text) for text in texts)


def _transform_contains_calendar_cancel_claim(values: List[Any]) -> Optional[bool]:
    texts = _text_values(values)
    if not texts:
        return None
    return any(_CALENDAR_CANCEL_CLAIM_RE.search(text) for text in texts)


def _transform_contains_license_ready_claim(values: List[Any]) -> Optional[bool]:
    texts = _text_values(values)
    if not texts:
        return None
    return any(_LICENSE_READY_CLAIM_RE.search(text) for text in texts)


def _transform_contains_pipeline_stage_claim(values: List[Any]) -> Optional[bool]:
    texts = _text_values(values)
    if not texts:
        return None
    return any(text_contains_pipeline_lost_claim(text) for text in texts)


def _transform_contains_social_scoring_adverse(values: List[Any]) -> Optional[bool]:
    texts = _text_values(values)
    if not texts:
        return None
    return any(text_contains_social_scoring_adverse(text) for text in texts)


def _transform_contains_case_terminal_rejection(values: List[Any]) -> Optional[bool]:
    texts = _text_values(values)
    if not texts:
        return None
    return any(_CASE_TERMINAL_REJECTION_RE.search(text) for text in texts)


def _transform_contains_stale_cleanup(values: List[Any]) -> Optional[bool]:
    texts = _text_values(values)
    if not texts:
        return None
    return any(_STALE_CLEANUP_RE.search(text) for text in texts)


def _transform_abs_number(
    args: Mapping[str, Any],
    spec: Mapping[str, Any],
    values: List[Any],
) -> Optional[float]:
    for value in values:
        number = _coerce_number(value)
        if number is not None:
            return abs(number)
    return _transform_product_abs(args, spec)


def _transform_product_abs(args: Mapping[str, Any], spec: Mapping[str, Any]) -> Optional[float]:
    operands = _source_values(spec, "operands")
    if len(operands) < 2:
        return None
    product = 1.0
    for path in operands:
        value = None
        for candidate in _path_values(args, path):
            value = _coerce_number(candidate)
            if value is not None:
                break
        if value is None:
            return None
        product *= value
    return abs(product)


def _transform_scaled_number(values: List[Any], spec: Mapping[str, Any]) -> Optional[float]:
    number = _coerce_generic(values, "number")
    if number is None:
        return None
    scale = _source_number(spec, "scale")
    if scale is not None:
        return float(number) * scale
    unit = _safe_lower(_source(spec).get("unit"))
    if unit in {"bytes", "byte"}:
        return float(number) / (1024 * 1024)
    return float(number)


def _apply_transform(
    args: Mapping[str, Any],
    spec: Mapping[str, Any],
    values: List[Any],
) -> Any:
    transform = _safe_lower(_source(spec).get("transform"))
    if not transform:
        return None
    if transform == "external_domains":
        return _transform_external_domains(values)
    if transform == "external_domain_count":
        return _transform_external_domain_count(values)
    if transform == "hostname":
        return _transform_hostname(values)
    if transform == "has_external_domain":
        return _transform_has_external_domain(values)
    if transform == "nonempty":
        return _transform_nonempty(values)
    if transform == "equals":
        return _transform_equals(values, spec)
    if transform == "in_set":
        return _transform_in_set(values, spec)
    if transform == "contains_any":
        return _transform_contains_any(values, spec)
    if transform == "contains_pii_like":
        return _transform_contains_pii_like(values)
    if transform == "contains_financial_or_payment":
        return _transform_contains_financial_or_payment(values)
    if transform == "contains_completion_claim":
        return _transform_contains_completion_claim(values)
    if transform == "contains_marketing_or_onboarding":
        return _transform_contains_marketing_or_onboarding(values)
    if transform == "contains_payment_false_status":
        return _transform_contains_payment_false_status(values)
    if transform == "contains_calendar_cancel_claim":
        return _transform_contains_calendar_cancel_claim(values)
    if transform == "contains_license_ready_claim":
        return _transform_contains_license_ready_claim(values)
    if transform == "contains_pipeline_stage_claim":
        return _transform_contains_pipeline_stage_claim(values)
    if transform == "contains_social_scoring_adverse":
        return _transform_contains_social_scoring_adverse(values)
    if transform == "contains_case_terminal_rejection":
        return _transform_contains_case_terminal_rejection(values)
    if transform == "contains_stale_cleanup":
        return _transform_contains_stale_cleanup(values)
    if transform == "abs_number":
        return _transform_abs_number(args, spec, values)
    if transform == "product_abs":
        return _transform_product_abs(args, spec)
    if transform == "scaled_number":
        return _transform_scaled_number(values, spec)
    return None


def _is_direct_source(spec: Mapping[str, Any]) -> bool:
    return _source(spec).get("kind") in {"tool_arguments", "derived"}


def derive_policy_metadata_from_tool_args(
    args: Mapping[str, Any],
    required_metadata: Iterable[Any],
    *,
    tool_name: Optional[str] = None,
    instruction_type: Optional[str] = None,
    instruction_category: Optional[str] = None,
) -> Dict[str, Any]:
    """Return requested custom-policy metadata that can be derived from args."""

    if not isinstance(args, Mapping):
        return {}

    out: Dict[str, Any] = {}
    for raw_spec in required_metadata or []:
        if not isinstance(raw_spec, Mapping) or not _is_direct_source(raw_spec):
            continue
        if not _applies_to_context(
            raw_spec,
            tool_name=tool_name,
            instruction_type=instruction_type,
            instruction_category=instruction_category,
        ):
            continue
        field = raw_spec.get("field")
        if not isinstance(field, str) or not field:
            continue

        values = _values_for_spec(args, raw_spec)
        value = _apply_transform(args, raw_spec, values)
        if value is None:
            value = _coerce_generic(values, str(raw_spec.get("type", "")))
        if value is not None:
            out[field] = value

    return out
