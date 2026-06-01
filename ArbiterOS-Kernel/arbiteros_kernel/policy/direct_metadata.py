"""Policy-side direct metadata extraction for custom rules.

This module handles deterministic, low-level fields that can be read from the
current tool arguments without asking kernel/parser lowering for new metadata.
Semantic classification should still live in kernel/parser or LLM lowering.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set
from urllib.parse import urlparse


_EMAIL_RE = re.compile(r"@([A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,63})")
_HOST_RE = re.compile(
    r"(?<![@A-Za-z0-9_-])"
    r"([A-Za-z0-9][A-Za-z0-9-]{0,62}(?:\.[A-Za-z0-9][A-Za-z0-9-]{0,62})+)"
    r"(?::\d+)?"
)

_INTERNAL_DOMAIN_SUFFIXES = (
    ".local",
    ".internal",
    ".localhost",
    ".lan",
)
_INTERNAL_DOMAIN_EXACT = {"localhost"}


def _safe_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _safe_lower(value: Any) -> str:
    return _safe_str(value).lower()


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
    if host in _INTERNAL_DOMAIN_EXACT:
        return True
    return any(host.endswith(suffix) for suffix in _INTERNAL_DOMAIN_SUFFIXES)


def _domains_from_text(text: str) -> List[str]:
    raw = _safe_str(text)
    if not raw:
        return []

    out: List[str] = []
    parsed = urlparse(raw)
    if parsed.hostname:
        out.append(parsed.hostname)

    out.extend(match.group(1) for match in _EMAIL_RE.finditer(raw))
    out.extend(match.group(1) for match in _HOST_RE.finditer(raw))

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
