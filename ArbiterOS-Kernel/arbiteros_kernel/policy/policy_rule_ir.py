"""Policy Rule IR v1 validation and runtime-rule compilation.

V1 supports two policy shapes:
- unary_tool_call: rules over the current tool call / response instruction.
- relational_flow: rules over the relation between source taint/history and a
  current sink instruction.

The IR may request extra low-dimensional metadata from kernel/parser lowering
or deterministic policy-side argument extraction, but executable predicates
must only reference instruction/security fields or declared metadata fields.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Set


IR_VERSION = 1

UNARY_RULE_KIND = "unary_tool_call"
RELATIONAL_RULE_KIND = "relational_flow"
RULE_KIND = UNARY_RULE_KIND  # Backward-compatible alias used by older callers.
ALLOWED_RULE_KINDS = {UNARY_RULE_KIND, RELATIONAL_RULE_KIND}
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
ALLOWED_FLOW_KINDS = {
    "read_external",
    "read_sensitive",
    "read_state",
    "write_local",
    "write_shared",
    "delegate_sink",
    "comm_sink",
    "voice_sink",
    "ui_side_effect",
    "exec_side_effect",
    "business_side_effect",
    "persist_side_effect",
    "respond_sink",
    "none",
}

VALUE_OPERATOR_ARITY = {
    "len": 1,
    "count_intersections": 2,
}
VALUE_OPERATORS = set(VALUE_OPERATOR_ARITY.keys())
UNARY_PREDICATE_OPERATORS = {"not", "truthy", "falsy", "exists", "missing"}
BINARY_PREDICATE_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "ge",
    "lt",
    "le",
    "in",
    "not_in",
    "contains",
    "intersects",
    "starts_with",
    "ends_with",
    "contains_all",
    "subset_of",
    "matches",
}
TERNARY_PREDICATE_OPERATORS = {"between"}

# Fields that user-authored IR may reference without declaring
# required_metadata. Keep this set aligned with the lowered instruction and
# security metadata contract, not with every convenience field that the legacy
# UnaryGatePolicy runtime context happens to expose.
BUILTIN_TOOL_CALL_FIELDS: Set[str] = {
    "scope",
    "tool_name",
    "canonical_tool_name",
    "tool_call_id",
    "instruction_type",
    "instruction_category",
    "missing_instruction",
    "trustworthiness",
    "confidentiality",
    "reversible",
    "risk",
}

BUILTIN_RELATIONAL_FIELDS: Set[str] = {
    # Current sink aliases. These mirror unary built-ins so unary predicates can
    # be lifted into relational sink rules when appropriate.
    *BUILTIN_TOOL_CALL_FIELDS,
    "flow_kind",
    "source_tool_name",
    "source_tool_call_id",
    "source_instruction_type",
    "source_instruction_category",
    "source_trustworthiness",
    "source_confidentiality",
    "sink_tool_name",
    "sink_tool_call_id",
    "sink_instruction_type",
    "sink_instruction_category",
    "sink_trustworthiness",
    "sink_confidentiality",
    "sink_prop_trustworthiness",
    "sink_prop_confidentiality",
    "sink_risk",
    "sink_reversible",
    "respond_content_present",
}
BUILTIN_FIELDS_BY_KIND: Dict[str, Set[str]] = {
    UNARY_RULE_KIND: BUILTIN_TOOL_CALL_FIELDS,
    RELATIONAL_RULE_KIND: BUILTIN_RELATIONAL_FIELDS,
}
ALL_BUILTIN_FIELDS: Set[str] = set().union(*BUILTIN_FIELDS_BY_KIND.values())

# Fields that still exist in the UnaryGatePolicy runtime context for built-in
# and legacy rules, but are not part of the Policy Rule IR v1 built-in surface.
# Keep them reserved so custom policy_metadata cannot collide with values the
# runtime already supplies under the same key.
INTERNAL_RUNTIME_CONTEXT_FIELDS: Set[str] = {
    "prop_trustworthiness",
    "prop_confidentiality",
    "confidence",
    "authority",
    "tags",
    "review_required",
    "approval_required",
    "destructive",
    "custom_io_kind",
    "custom_flow_role",
    "custom_taint_role",
}

# Runtime-only fields retained by UnaryGatePolicy for legacy built-in rules and
# user_unary_gate_rules.json compatibility. Policy Rule IR must not rely on
# these fields because they are derived from raw tool arguments inside policy
# evaluation rather than supplied by instruction parsing metadata.
LEGACY_RUNTIME_DERIVED_FIELDS: Set[str] = {
    "arg_total_str_len",
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
}

RESERVED_METADATA_FIELDS: Set[str] = set(ALL_BUILTIN_FIELDS) | {
    *INTERNAL_RUNTIME_CONTEXT_FIELDS,
    *LEGACY_RUNTIME_DERIVED_FIELDS,
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

    rules = document.get("rules")
    if not isinstance(rules, list) or not rules:
        errors.append("rules must be a non-empty array")
        return ValidationResult(False, errors)

    required_metadata = document.get("required_metadata", [])
    if required_metadata is None:
        required_metadata = []
    declared = _validate_required_metadata(required_metadata, errors)

    seen_ids: Set[str] = set()
    for index, rule in enumerate(rules):
        _validate_rule(rule, index, seen_ids, declared, errors)

    _validate_required_metadata_rule_refs(declared, seen_ids, rules, errors)

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
    _assert_rule_kinds(document, {UNARY_RULE_KIND}, "unary gate")
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


def compile_policy_rule_ir_to_relational_flow_rules(
    document: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Compile valid V1 IR rules into RelationalPolicy runtime rules."""

    assert_valid_policy_rule_ir(document)
    _assert_rule_kinds(document, {RELATIONAL_RULE_KIND}, "relational flow")
    metadata = _metadata_by_field(document.get("required_metadata") or [])
    out: List[Dict[str, Any]] = []

    for rule in document["rules"]:
        body = rule["rule"]
        sink_selector = body.get("sink") or {}
        source_selector = body.get("source") or {}
        predicate = _compile_predicate(body["predicate"])
        predicate = _wrap_predicate_for_missing_metadata(predicate, metadata)

        out.append(
            _drop_none(
                {
                    "id": rule["id"],
                    "enabled": rule["enabled"],
                    "title": rule["title"],
                    "description": rule["description"],
                    "scope": "relational",
                    "source_selector": _compile_relational_selector(source_selector),
                    "selector": _compile_relational_selector(sink_selector),
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
        "description": (
            "User-authored unary tool-call policy rules compiled from "
            "Policy Rule IR with declared metadata contracts."
        ),
        "rules": rules,
    }
    required_metadata = document.get("required_metadata") or []
    if required_metadata:
        bundle["required_metadata"] = copy.deepcopy(required_metadata)
    return bundle


def compile_policy_rule_ir_to_relational_flow_bundle(
    document: Mapping[str, Any],
    *,
    source: str = "policy_rule_ir",
) -> Dict[str, Any]:
    """Compile a V1 IR document into a complete relational rule bundle."""

    rules = compile_policy_rule_ir_to_relational_flow_rules(document)
    bundle: Dict[str, Any] = {
        "evaluation_mode": "first_match",
        "source": source,
        "description": (
            "User-authored relational flow policy rules compiled from "
            "Policy Rule IR with declared metadata contracts."
        ),
        "rules": rules,
    }
    required_metadata = document.get("required_metadata") or []
    if required_metadata:
        bundle["required_metadata"] = copy.deepcopy(required_metadata)
    return bundle


def _assert_rule_kinds(
    document: Mapping[str, Any],
    allowed: Set[str],
    target_name: str,
) -> None:
    bad = sorted(
        {
            str(rule.get("kind"))
            for rule in document.get("rules", [])
            if isinstance(rule, Mapping) and rule.get("kind") not in allowed
        }
    )
    if bad:
        raise PolicyRuleIRValidationError(
            f"{target_name} compiler only accepts rule kinds "
            f"{sorted(allowed)}; got {bad}"
        )


def _compile_relational_selector(raw: Mapping[str, Any]) -> Dict[str, Any]:
    selector: Dict[str, Any] = {}
    if raw.get("tools"):
        selector["tool"] = raw["tools"]
    if raw.get("instruction_types"):
        selector["instruction_type"] = raw["instruction_types"]
    if raw.get("categories"):
        selector["category"] = raw["categories"]
    if raw.get("flow_kinds"):
        selector["flow_kind"] = raw["flow_kinds"]
    return selector


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

        if "on_missing" not in item:
            errors.append(f"{prefix}.on_missing is required")
        on_missing = item.get("on_missing")
        if on_missing not in ALLOWED_ON_MISSING:
            errors.append(
                f"{prefix}.on_missing must be one of {sorted(ALLOWED_ON_MISSING)}"
            )

        applies_to = item.get("applies_to", {})
        if applies_to is not None:
            _validate_selector_like(applies_to, f"{prefix}.applies_to", errors)

        if "source" not in item:
            errors.append(f"{prefix}.source is required")
        else:
            _validate_metadata_source(item.get("source"), f"{prefix}.source", errors)

        declared[field] = dict(item)

    return declared


def _validate_required_metadata_rule_refs(
    declared: Mapping[str, Mapping[str, Any]],
    rule_ids: Set[str],
    rules: Any,
    errors: List[str],
) -> None:
    if not declared:
        return

    vars_by_rule: Dict[str, Set[str]] = {}
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, Mapping):
                continue
            rule_id = rule.get("id")
            if not isinstance(rule_id, str) or not rule_id.strip():
                continue
            body = rule.get("rule")
            pred = body.get("predicate") if isinstance(body, Mapping) else None
            vars_by_rule[rule_id] = {
                _normalize_var_name(name) for name in _extract_vars(pred)
            }

    for index, (field, item) in enumerate(declared.items()):
        prefix = f"required_metadata[{index}]"
        refs = item.get("required_for_rules")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if ref not in rule_ids:
                errors.append(
                    f"{prefix}.required_for_rules references unknown rule {ref!r}"
                )
                continue
            if field not in vars_by_rule.get(ref, set()):
                errors.append(
                    f"{prefix}.required_for_rules references {ref!r}, "
                    f"but rule does not use metadata field {field!r}"
                )


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

    kind = raw.get("kind")
    if kind not in ALLOWED_RULE_KINDS:
        errors.append(f"{prefix}.kind must be one of {sorted(ALLOWED_RULE_KINDS)}")
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

    if kind == UNARY_RULE_KIND:
        selector = body.get("selector", {})
        if selector is not None:
            _validate_selector_like(selector, f"{prefix}.rule.selector", errors)
    elif kind == RELATIONAL_RULE_KIND:
        source = body.get("source", {})
        if source is not None:
            _validate_selector_like(source, f"{prefix}.rule.source", errors)
        sink = body.get("sink", {})
        if sink is not None:
            _validate_selector_like(
                sink,
                f"{prefix}.rule.sink",
                errors,
                allow_flow_kinds=True,
            )

    predicate = body.get("predicate")
    _validate_predicate(predicate, f"{prefix}.rule.predicate", errors)

    vars_used = {_normalize_var_name(name) for name in _extract_vars(predicate)}
    declared_fields = set(declared.keys())
    builtin_fields = BUILTIN_FIELDS_BY_KIND.get(str(kind), set())
    unknown = sorted(vars_used - builtin_fields - declared_fields)
    if unknown:
        errors.append(
            f"{prefix}.rule.predicate references undeclared metadata fields: "
            + ", ".join(unknown)
        )


def _validate_selector_like(
    raw: Any,
    prefix: str,
    errors: List[str],
    *,
    allow_flow_kinds: bool = False,
) -> None:
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

    if "flow_kinds" in raw:
        if not allow_flow_kinds:
            errors.append(f"{prefix}.flow_kinds is only valid for relational sinks")
            return
        values = raw.get("flow_kinds")
        if not isinstance(values, list) or not all(
            isinstance(v, str) and v.strip() for v in values
        ):
            errors.append(f"{prefix}.flow_kinds must be an array of non-empty strings")
            return
        bad = [str(v) for v in values if str(v) not in ALLOWED_FLOW_KINDS]
        if bad:
            errors.append(
                f"{prefix}.flow_kinds contains unsupported values: "
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

    expr_keys = set(raw.keys()) & ({"var", "const"} | VALUE_OPERATORS)
    if expr_keys:
        if len(raw) != 1 or len(expr_keys) != 1:
            errors.append(f"{prefix} value expression must contain exactly one key")
            return

        key = next(iter(expr_keys))
        if key == "var":
            if not isinstance(raw.get("var"), str):
                errors.append(f"{prefix}.var must be a string")
                return
            name = _normalize_var_name(str(raw["var"]))
            if not _valid_field_name(name) and name not in ALL_BUILTIN_FIELDS:
                errors.append(f"{prefix}.var has invalid field name {raw['var']!r}")
            return
        if key == "const":
            return
        _validate_value_operator(key, raw.get(key), prefix, errors)
        return

    if len(raw) != 1:
        errors.append(f"{prefix} must contain exactly one predicate operator")
        return

    op, value = next(iter(raw.items()))
    if op in VALUE_OPERATORS:
        _validate_value_operator(op, value, prefix, errors)
        return

    if op in {"all", "any"}:
        if not isinstance(value, list) or not value:
            errors.append(f"{prefix}.{op} must be a non-empty array")
            return
        for idx, item in enumerate(value):
            _validate_predicate(item, f"{prefix}.{op}[{idx}]", errors)
        return

    if op in UNARY_PREDICATE_OPERATORS:
        _validate_predicate_or_value(value, f"{prefix}.{op}", errors)
        return

    if op in BINARY_PREDICATE_OPERATORS:
        if not isinstance(value, list) or len(value) != 2:
            errors.append(f"{prefix}.{op} must be a two-item array")
            return
        _validate_predicate_or_value(value[0], f"{prefix}.{op}[0]", errors)
        _validate_predicate_or_value(value[1], f"{prefix}.{op}[1]", errors)
        return

    if op in TERNARY_PREDICATE_OPERATORS:
        if not isinstance(value, list) or len(value) != 3:
            errors.append(f"{prefix}.{op} must be a three-item array")
            return
        for idx, item in enumerate(value):
            _validate_predicate_or_value(item, f"{prefix}.{op}[{idx}]", errors)
        return

    errors.append(f"{prefix} uses unsupported operator {op!r}")


def _validate_value_operator(
    op: str, value: Any, prefix: str, errors: List[str]
) -> None:
    arity = VALUE_OPERATOR_ARITY[op]
    if arity == 1:
        _validate_predicate_or_value(value, f"{prefix}.{op}", errors)
        return
    if not isinstance(value, list) or len(value) != arity:
        errors.append(f"{prefix}.{op} must be a {arity}-item array")
        return
    for idx, item in enumerate(value):
        _validate_predicate_or_value(item, f"{prefix}.{op}[{idx}]", errors)


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
        if metadata[field].get("on_missing") in {"fail_closed", "validation_error"}
    ]
    exists_checks = [
        {"exists": {"var": field}}
        for field in custom_vars
        if metadata[field].get("on_missing") == "no_match"
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


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and compile Policy Rule IR v1 to UnaryGate rules."
    )
    parser.add_argument("--input", required=True, help="Path to Policy Rule IR JSON")
    parser.add_argument("--output", help="Path to write compiled UnaryGate bundle")
    parser.add_argument(
        "--source",
        default="user_unary_gate_rules.json",
        help="source value for the compiled bundle",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate only; do not write output",
    )
    parser.add_argument(
        "--target",
        choices=["auto", "unary", "relational"],
        default="auto",
        help="Runtime bundle target. auto requires all rules to share one kind.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    document = json.loads(input_path.read_text(encoding="utf-8"))
    result = validate_policy_rule_ir(document)
    if not result.ok:
        for error in result.errors:
            print(error)
        return 1

    if args.check:
        print("Policy Rule IR is valid")
        return 0

    target = args.target
    if target == "auto":
        kinds = {
            rule.get("kind")
            for rule in document.get("rules", [])
            if isinstance(rule, Mapping)
        }
        if kinds == {UNARY_RULE_KIND}:
            target = "unary"
        elif kinds == {RELATIONAL_RULE_KIND}:
            target = "relational"
        else:
            print(
                "--target auto requires all rules to be unary_tool_call or all "
                "rules to be relational_flow"
            )
            return 1

    if target == "unary":
        bundle = compile_policy_rule_ir_to_unary_gate_bundle(
            document,
            source=args.source,
        )
    else:
        bundle = compile_policy_rule_ir_to_relational_flow_bundle(
            document,
            source=args.source,
        )
    text = json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
