"""Policy Rule IR v1 validation and unary-gate compilation.

V1 is intentionally narrow: user-authored policies are unary tool-call rules.
The IR may request extra low-dimensional metadata from kernel/parser lowering,
but executable predicates must only reference built-in tool-call fields or
declared metadata fields.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Set


IR_VERSION = 1

RULE_KIND = "unary_tool_call"
ALLOWED_EFFECTS = {"BLOCK"}
ALLOWED_ON_MISSING = {"validation_error", "no_match", "fail_closed"}
ALLOWED_METADATA_TYPES = {
    "string",
    "number",
    "integer",
    "boolean",
    "string_array",
    "number_array",
}
ALLOWED_SEVERITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
ALLOWED_SOURCE_KINDS = {
    "tool_arguments",
    "kernel_lowering",
    "llm_lowering",
    "parser_custom",
    "derived",
}
ALLOWED_INSTRUCTION_TYPES = {
    "READ",
    "WRITE",
    "EXEC",
    "WAIT",
    "ASK",
    "RESPOND",
    "USER_MESSAGE",
    "DELEGATE",
    "RETRIEVE",
    "STORE",
    "SUBSCRIBE",
    "RECEIVE",
    "REASON",
    "PLAN",
    "CRITIQUE",
}

# Fields produced by UnaryGatePolicy._build_tool_context for every tool call.
BUILTIN_TOOL_CALL_FIELDS: Set[str] = {
    "scope",
    "tool_name",
    "canonical_tool_name",
    "tool_call_id",
    "instruction_type",
    "instruction_category",
    "missing_instruction",
    "arg_total_str_len",
    "trustworthiness",
    "confidentiality",
    "prop_trustworthiness",
    "prop_confidentiality",
    "confidence",
    "authority",
    "reversible",
    "risk",
    "tags",
    "review_required",
    "approval_required",
    "destructive",
    "action",
    "path_hint",
    "path_basename",
    "path_dirname",
    "direct_target_basenames",
    "exec_path_tokens",
    "exec_write_targets",
    "exec_write_target_basenames",
    "arg_text_upper",
    "has_external_url",
    "custom_io_kind",
    "custom_flow_role",
    "custom_taint_role",
}

RESERVED_METADATA_FIELDS: Set[str] = set(BUILTIN_TOOL_CALL_FIELDS) | {
    "raw_args",
    "custom",
}

_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class PolicyRuleIRValidationError(ValueError):
    """Raised when a user-authored policy rule IR is not valid."""


@dataclass(frozen=True)
class ValidationResult:
    """Structured result for callers that prefer non-exception control flow."""

    ok: bool
    errors: List[str]


def validate_policy_rule_ir(document: Mapping[str, Any]) -> ValidationResult:
    """Validate a Policy Rule IR v1 document.

    The validator is strict by design. It accepts only V1 unary tool-call rules
    and requires all non-built-in predicate variables to be declared in
    ``required_metadata``.
    """

    errors: List[str] = []
    if not isinstance(document, Mapping):
        return ValidationResult(False, ["document must be a JSON object"])

    version = document.get("version")
    if version != IR_VERSION:
        errors.append(f"version must be {IR_VERSION}")

    required_metadata = document.get("required_metadata", [])
    if required_metadata is None:
        required_metadata = []
    declared = _validate_required_metadata(required_metadata, errors)

    rules = document.get("rules")
    if not isinstance(rules, list) or not rules:
        errors.append("rules must be a non-empty array")
        return ValidationResult(False, errors)

    seen_ids: Set[str] = set()
    for index, rule in enumerate(rules):
        _validate_rule(rule, index, seen_ids, declared, errors)

    return ValidationResult(not errors, errors)


def assert_valid_policy_rule_ir(document: Mapping[str, Any]) -> None:
    result = validate_policy_rule_ir(document)
    if not result.ok:
        raise PolicyRuleIRValidationError("; ".join(result.errors))


def compile_policy_rule_ir_to_unary_gate_rules(
    document: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Compile valid V1 IR rules into UnaryGatePolicy runtime rules."""

    assert_valid_policy_rule_ir(document)
    metadata = _metadata_by_field(document.get("required_metadata") or [])
    out: List[Dict[str, Any]] = []

    for rule in document["rules"]:
        body = rule["rule"]
        selector = body.get("selector") or {}
        predicate = _compile_predicate(body["predicate"])
        predicate = _wrap_predicate_for_missing_metadata(predicate, metadata)

        runtime_selector: Dict[str, Any] = {}
        if selector.get("tools"):
            runtime_selector["tool"] = selector["tools"]
        if selector.get("instruction_types"):
            runtime_selector["instruction_type"] = selector["instruction_types"]
        if selector.get("categories"):
            runtime_selector["category"] = selector["categories"]

        out.append(
            _drop_none(
                {
                    "id": rule["id"],
                    "enabled": rule["enabled"],
                    "title": rule["title"],
                    "description": rule["description"],
                    "scope": "tool",
                    "selector": runtime_selector,
                    "predicate": predicate,
                    "effect": rule.get("effect", "BLOCK"),
                    "message": rule["message"],
                    "source": "policy_rule_ir",
                    "severity": rule.get("severity"),
                }
            )
        )

    return out


def compile_policy_rule_ir_to_unary_gate_bundle(
    document: Mapping[str, Any],
    *,
    source: str = "policy_rule_ir",
) -> Dict[str, Any]:
    """Compile a V1 IR document into a complete unary gate rule bundle."""

    rules = compile_policy_rule_ir_to_unary_gate_rules(document)
    bundle: Dict[str, Any] = {
        "evaluation_mode": "first_match",
        "source": source,
        "rules": rules,
    }
    required_metadata = document.get("required_metadata") or []
    if required_metadata:
        bundle["required_metadata"] = copy.deepcopy(required_metadata)
    return bundle


def _validate_required_metadata(raw: Any, errors: List[str]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw, list):
        errors.append("required_metadata must be an array")
        return {}

    declared: Dict[str, Dict[str, Any]] = {}
    for index, item in enumerate(raw):
        prefix = f"required_metadata[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"{prefix} must be an object")
            continue

        field = item.get("field")
        if not _valid_field_name(field):
            errors.append(f"{prefix}.field must be lower_snake_case")
            continue
        field = str(field)
        if field in RESERVED_METADATA_FIELDS:
            errors.append(f"{prefix}.field must not override built-in field {field!r}")
            continue
        if field in declared:
            errors.append(f"{prefix}.field duplicates {field!r}")
            continue

        typ = item.get("type")
        if typ not in ALLOWED_METADATA_TYPES:
            errors.append(
                f"{prefix}.type must be one of {sorted(ALLOWED_METADATA_TYPES)}"
            )

        if not isinstance(item.get("description"), str) or not item.get(
            "description", ""
        ).strip():
            errors.append(f"{prefix}.description is required")

        required_for_rules = item.get("required_for_rules")
        if not isinstance(required_for_rules, list) or not all(
            isinstance(v, str) and v.strip() for v in required_for_rules
        ):
            errors.append(f"{prefix}.required_for_rules must be a string array")

        on_missing = item.get("on_missing", "validation_error")
        if on_missing not in ALLOWED_ON_MISSING:
            errors.append(
                f"{prefix}.on_missing must be one of {sorted(ALLOWED_ON_MISSING)}"
            )

        applies_to = item.get("applies_to", {})
        if applies_to is not None:
            _validate_selector_like(applies_to, f"{prefix}.applies_to", errors)

        source = item.get("source", {})
        if source is not None:
            _validate_metadata_source(source, f"{prefix}.source", errors)

        declared[field] = dict(item)

    return declared


def _validate_rule(
    raw: Any,
    index: int,
    seen_ids: Set[str],
    declared: Mapping[str, Mapping[str, Any]],
    errors: List[str],
) -> None:
    prefix = f"rules[{index}]"
    if not isinstance(raw, Mapping):
        errors.append(f"{prefix} must be an object")
        return

    rule_id = raw.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        errors.append(f"{prefix}.id is required")
    elif rule_id in seen_ids:
        errors.append(f"{prefix}.id duplicates {rule_id!r}")
    else:
        seen_ids.add(rule_id)

    if raw.get("kind") != RULE_KIND:
        errors.append(f"{prefix}.kind must be {RULE_KIND!r}")
    if raw.get("effect") not in ALLOWED_EFFECTS:
        errors.append(f"{prefix}.effect must be one of {sorted(ALLOWED_EFFECTS)}")

    severity = raw.get("severity")
    if severity is not None and severity not in ALLOWED_SEVERITIES:
        errors.append(f"{prefix}.severity must be one of {sorted(ALLOWED_SEVERITIES)}")

    for key in ("title", "description", "message"):
        if not isinstance(raw.get(key), str) or not raw.get(key, "").strip():
            errors.append(f"{prefix}.{key} is required")

    if not isinstance(raw.get("enabled"), bool):
        errors.append(f"{prefix}.enabled must be boolean")

    body = raw.get("rule")
    if not isinstance(body, Mapping):
        errors.append(f"{prefix}.rule must be an object")
        return

    selector = body.get("selector", {})
    if selector is not None:
        _validate_selector_like(selector, f"{prefix}.rule.selector", errors)

    predicate = body.get("predicate")
    _validate_predicate(predicate, f"{prefix}.rule.predicate", errors)

    vars_used = {_normalize_var_name(name) for name in _extract_vars(predicate)}
    declared_fields = set(declared.keys())
    unknown = sorted(vars_used - BUILTIN_TOOL_CALL_FIELDS - declared_fields)
    if unknown:
        errors.append(
            f"{prefix}.rule.predicate references undeclared metadata fields: "
            + ", ".join(unknown)
        )


def _validate_selector_like(raw: Any, prefix: str, errors: List[str]) -> None:
    if not isinstance(raw, Mapping):
        errors.append(f"{prefix} must be an object")
        return

    for key in ("tools", "instruction_types", "categories"):
        if key not in raw:
            continue
        values = raw.get(key)
        if not isinstance(values, list) or not all(
            isinstance(v, str) and v.strip() for v in values
        ):
            errors.append(f"{prefix}.{key} must be an array of non-empty strings")

    instruction_types = raw.get("instruction_types")
    if isinstance(instruction_types, list):
        bad = [
            str(v)
            for v in instruction_types
            if isinstance(v, str) and v.upper() not in ALLOWED_INSTRUCTION_TYPES
        ]
        if bad:
            errors.append(
                f"{prefix}.instruction_types contains unsupported values: "
                + ", ".join(bad)
            )


def _validate_metadata_source(raw: Any, prefix: str, errors: List[str]) -> None:
    if not isinstance(raw, Mapping):
        errors.append(f"{prefix} must be an object")
        return

    kind = raw.get("kind")
    if kind not in ALLOWED_SOURCE_KINDS:
        errors.append(f"{prefix}.kind must be one of {sorted(ALLOWED_SOURCE_KINDS)}")

    paths = raw.get("paths", [])
    if paths is not None and (
        not isinstance(paths, list)
        or not all(isinstance(path, str) and path.strip() for path in paths)
    ):
        errors.append(f"{prefix}.paths must be an array of non-empty strings")


def _validate_predicate(raw: Any, prefix: str, errors: List[str]) -> None:
    if not isinstance(raw, Mapping):
        errors.append(f"{prefix} must be an object")
        return

    if "var" in raw or "const" in raw:
        if "var" in raw and not isinstance(raw.get("var"), str):
            errors.append(f"{prefix}.var must be a string")
        if "var" in raw and isinstance(raw.get("var"), str):
            name = _normalize_var_name(str(raw["var"]))
            if not _valid_field_name(name) and name not in BUILTIN_TOOL_CALL_FIELDS:
                errors.append(f"{prefix}.var has invalid field name {raw['var']!r}")
        return

    if len(raw) != 1:
        errors.append(f"{prefix} must contain exactly one predicate operator")
        return

    op, value = next(iter(raw.items()))
    if op in {"all", "any"}:
        if not isinstance(value, list) or not value:
            errors.append(f"{prefix}.{op} must be a non-empty array")
            return
        for idx, item in enumerate(value):
            _validate_predicate(item, f"{prefix}.{op}[{idx}]", errors)
        return

    if op in {"not", "truthy", "falsy", "exists", "missing"}:
        _validate_predicate_or_value(value, f"{prefix}.{op}", errors)
        return

    if op in {"eq", "ne", "gt", "ge", "lt", "le", "in", "not_in", "contains", "intersects"}:
        if not isinstance(value, list) or len(value) != 2:
            errors.append(f"{prefix}.{op} must be a two-item array")
            return
        _validate_predicate_or_value(value[0], f"{prefix}.{op}[0]", errors)
        _validate_predicate_or_value(value[1], f"{prefix}.{op}[1]", errors)
        return

    errors.append(f"{prefix} uses unsupported operator {op!r}")


def _validate_predicate_or_value(raw: Any, prefix: str, errors: List[str]) -> None:
    if isinstance(raw, Mapping):
        _validate_predicate(raw, prefix, errors)
        return
    if isinstance(raw, list):
        for idx, item in enumerate(raw):
            _validate_predicate_or_value(item, f"{prefix}[{idx}]", errors)


def _compile_predicate(raw: Any) -> Any:
    if isinstance(raw, Mapping):
        if "var" in raw and isinstance(raw.get("var"), str):
            return {"var": _normalize_var_name(str(raw["var"]))}
        return {key: _compile_predicate(value) for key, value in raw.items()}
    if isinstance(raw, list):
        return [_compile_predicate(item) for item in raw]
    return copy.deepcopy(raw)


def _wrap_predicate_for_missing_metadata(
    predicate: Any,
    metadata: Mapping[str, Mapping[str, Any]],
) -> Any:
    vars_used = {_normalize_var_name(name) for name in _extract_vars(predicate)}
    custom_vars = sorted(vars_used & set(metadata.keys()))
    if not custom_vars:
        return predicate

    fail_closed_checks = [
        {"missing": {"var": field}}
        for field in custom_vars
        if metadata[field].get("on_missing") == "fail_closed"
    ]
    exists_checks = [
        {"exists": {"var": field}}
        for field in custom_vars
        if metadata[field].get("on_missing") != "fail_closed"
    ]

    guarded = predicate
    if exists_checks:
        guarded = {"all": [*exists_checks, guarded]}
    if fail_closed_checks:
        guarded = {"any": [*fail_closed_checks, guarded]}
    return guarded


def _metadata_by_field(raw: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in raw:
        if isinstance(item, Mapping) and isinstance(item.get("field"), str):
            out[item["field"]] = dict(item)
    return out


def _drop_none(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in value.items() if v is not None}


def _extract_vars(raw: Any) -> Set[str]:
    out: Set[str] = set()
    if isinstance(raw, Mapping):
        var = raw.get("var")
        if isinstance(var, str):
            out.add(var)
        for value in raw.values():
            out.update(_extract_vars(value))
    elif isinstance(raw, list):
        for item in raw:
            out.update(_extract_vars(item))
    return out


def _normalize_var_name(name: str) -> str:
    stripped = name.strip()
    for prefix in ("metadata.", "policy_metadata."):
        if stripped.startswith(prefix):
            return stripped[len(prefix) :]
    return stripped


def _valid_field_name(value: Any) -> bool:
    return isinstance(value, str) and bool(_FIELD_RE.match(value))
