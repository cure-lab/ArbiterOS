
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from arbiteros_kernel.instruction_parsing.types import LEVEL_ORDER
from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


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


def _level_rank(v: Any) -> float:
    return LEVEL_ORDER.get(_safe_level(v), 0.5)


def _level_at_least(actual: Any, required: Any) -> bool:
    return _level_rank(actual) >= _level_rank(required)


def _level_at_most(actual: Any, limit: Any) -> bool:
    return _level_rank(actual) <= _level_rank(limit)


def _norm_list(v: Any) -> List[str]:
    if isinstance(v, (set, tuple)):
        v = list(v)
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for x in v:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _norm_set(v: Any) -> Set[str]:
    return {x.upper() for x in _norm_list(v)}


def _safe_dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _latest_tool_instr_index(
    latest_instructions: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    Map tool_call_id -> latest instruction (best effort).
    """
    out: Dict[str, Dict[str, Any]] = {}
    for ins in latest_instructions or []:
        content = ins.get("content")
        if not isinstance(content, dict):
            continue
        tcid = content.get("tool_call_id")
        if isinstance(tcid, str) and tcid:
            out[tcid] = ins
    return out


def _find_latest_respond_instruction(
    latest_instructions: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for ins in reversed(latest_instructions or []):
        if _safe_upper(ins.get("instruction_type")) == "RESPOND":
            return ins
    return None


def _extract_security_type(ins: Dict[str, Any]) -> Dict[str, Any]:
    st = ins.get("security_type")
    return st if isinstance(st, dict) else {}


def _extract_custom(ins: Dict[str, Any]) -> Dict[str, Any]:
    st = _extract_security_type(ins)
    custom = st.get("custom")
    return custom if isinstance(custom, dict) else {}


def _extract_instruction_tags(ins: Dict[str, Any]) -> Set[str]:
    """
    Only consume tags already lowered by kernel.

    Supported sources:
    - rule_types: ["TAG_A", ...] or [{"tag": "..."}]
    - security_type.custom.tags / labels / risk_tags
    - security_type.custom boolean flags -> normalized tags
    """
    tags: Set[str] = set()

    rule_types = ins.get("rule_types")
    if isinstance(rule_types, list):
        for item in rule_types:
            if isinstance(item, str) and item.strip():
                tags.add(item.strip().upper())
            elif isinstance(item, dict):
                for k in ("name", "type", "tag", "label"):
                    val = item.get(k)
                    if isinstance(val, str) and val.strip():
                        tags.add(val.strip().upper())

    custom = _extract_custom(ins)
    for key in ("tags", "labels", "risk_tags"):
        tags.update(_norm_set(custom.get(key)))

    bool_flag_to_tag = {
        "destructive": "DESTRUCTIVE",
        "delete_like": "DELETE",
        "review_required": "REVIEW_REQUIRED",
        "approval_required": "APPROVAL_REQUIRED",
        "high_risk": "HIGH_RISK",
        "secret_like": "SECRET_LIKE",
    }
    for k, tag in bool_flag_to_tag.items():
        if bool(custom.get(k)):
            tags.add(tag)

    return tags


def _extract_metadata_view(ins: Dict[str, Any]) -> Dict[str, Any]:
    """
    Canonical read-only metadata view used by UnaryGatePolicy.

    Important:
    This policy only consumes kernel-lowered metadata.
    It does NOT parse command/path semantics itself.
    """
    st = _extract_security_type(ins)
    custom = _extract_custom(ins)

    return {
        "instruction_type": _safe_upper(ins.get("instruction_type")),
        "instruction_category": _safe_str(ins.get("instruction_category")),
        "trustworthiness": _safe_level(st.get("trustworthiness")),
        "confidentiality": _safe_level(st.get("confidentiality")),
        "prop_trustworthiness": _safe_level(
            st.get("prop_trustworthiness") or st.get("trustworthiness")
        ),
        "prop_confidentiality": _safe_level(
            st.get("prop_confidentiality") or st.get("confidentiality")
        ),
        "confidence": _safe_level(st.get("confidence")),
        "authority": _safe_upper(st.get("authority"), "UNKNOWN"),
        "reversible": bool(st.get("reversible", False)),
        "risk": _safe_upper(st.get("risk"), "UNKNOWN"),
        "custom": custom,
        "tags": _extract_instruction_tags(ins),
        "review_required": bool(custom.get("review_required")),
        "approval_required": bool(custom.get("approval_required")),
        "destructive": bool(custom.get("destructive") or custom.get("delete_like")),
    }


def _estimate_argument_string_budget(args_dict: Dict[str, Any]) -> int:
    """
    Keep only a unary/string-size budget.
    No semantic parsing.
    """
    total = 0
    stack: List[Any] = [args_dict]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            total += len(cur)
        elif isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return total


def _get_unary_cfg() -> Dict[str, Any]:
    cfg = RUNTIME.cfg.get("unary_gate")
    return cfg if isinstance(cfg, dict) else {}


def _get_security_cfg() -> Dict[str, Any]:
    unary = _safe_dict(_get_unary_cfg().get("security"))
    if unary:
        return unary
    old = RUNTIME.cfg.get("security_label")
    return old if isinstance(old, dict) else {}


def _get_risk_cfg() -> Dict[str, Any]:
    return _safe_dict(_get_unary_cfg().get("risk"))


def _get_tag_cfg() -> Dict[str, Any]:
    return _safe_dict(_get_unary_cfg().get("tags"))


def _get_input_budget_cfg() -> Dict[str, Any]:
    cfg = RUNTIME.cfg.get("input_budget")
    return cfg if isinstance(cfg, dict) else {}


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


def _read_rule_bundle_from_file(path: str) -> Dict[str, Any]:
    resolved = _resolve_rule_file_path(path)
    with open(resolved, "r", encoding="utf-8") as f:
        text = f.read()

    lower = resolved.lower()
    if lower.endswith(".json"):
        data = json.loads(text)
    elif lower.endswith((".yml", ".yaml")):
        if yaml is None:
            raise RuntimeError(
                f"rule file `{resolved}` is YAML but PyYAML is not installed"
            )
        parsed = yaml.safe_load(text)
        data = parsed if isinstance(parsed, dict) else {}
    else:
        try:
            data = json.loads(text)
        except Exception:
            if yaml is None:
                raise RuntimeError(
                    f"unable to parse rule file `{resolved}` as JSON; "
                    "install PyYAML or use .json"
                )
            parsed = yaml.safe_load(text)
            data = parsed if isinstance(parsed, dict) else {}

    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Declarative rule engine
# ---------------------------------------------------------------------------


@dataclass
class RuleDecision:
    index: int
    rule_id: str
    title: str
    description: str
    effect: str
    scope: str
    message: str
    predicate: Any
    selector: Dict[str, Any]
    actual: Dict[str, Any]
    source: str = ""


def _ensure_list(v: Any) -> List[Any]:
    if isinstance(v, list):
        return v
    if isinstance(v, tuple):
        return list(v)
    return [v]


def _is_value_expr(v: Any) -> bool:
    return isinstance(v, dict) and ("var" in v or "const" in v)


def _rule_scope(rule: Dict[str, Any]) -> str:
    return _safe_lower(rule.get("scope"), "tool")


def _safe_lower(v: Any, default: str = "") -> str:
    s = _safe_str(v, default)
    return s.lower() if s else default


def _normalize_scalar_for_membership(v: Any) -> Any:
    if isinstance(v, str):
        up = _safe_upper(v, "")
        if up:
            return up
    return v


def _compare_key(v: Any) -> Tuple[int, Any]:
    if isinstance(v, str):
        up = _safe_upper(v, "")
        if up in LEVEL_ORDER:
            return (0, LEVEL_ORDER[up])
        return (1, up)
    if isinstance(v, bool):
        return (2, int(v))
    if isinstance(v, (int, float)):
        return (3, v)
    return (4, str(v))


def _compare_values(left: Any, right: Any) -> int:
    lk = _compare_key(left)
    rk = _compare_key(right)
    if lk < rk:
        return -1
    if lk > rk:
        return 1
    return 0


def _extract_vars(value: Any) -> Set[str]:
    names: Set[str] = set()
    if isinstance(value, dict):
        if "var" in value and isinstance(value.get("var"), str):
            names.add(value["var"])
        for v in value.values():
            names.update(_extract_vars(v))
    elif isinstance(value, list):
        for item in value:
            names.update(_extract_vars(item))
    return names


def _render_predicate(value: Any) -> str:
    if isinstance(value, dict):
        if "var" in value:
            return str(value["var"])
        if "const" in value:
            return repr(value["const"])
        if len(value) == 1:
            op, raw = next(iter(value.items()))
            if op == "all":
                return "(" + " AND ".join(_render_predicate(x) for x in _ensure_list(raw)) + ")"
            if op == "any":
                return "(" + " OR ".join(_render_predicate(x) for x in _ensure_list(raw)) + ")"
            if op == "not":
                return f"NOT {_render_predicate(raw)}"
            if op in {"eq", "ne", "gt", "ge", "lt", "le", "in", "not_in", "contains", "intersects"}:
                arr = _ensure_list(raw)
                if len(arr) == 2:
                    symbol = {
                        "eq": "==",
                        "ne": "!=",
                        "gt": ">",
                        "ge": ">=",
                        "lt": "<",
                        "le": "<=",
                        "in": "IN",
                        "not_in": "NOT IN",
                        "contains": "CONTAINS",
                        "intersects": "INTERSECTS",
                    }[op]
                    return f"{_render_predicate(arr[0])} {symbol} {_render_predicate(arr[1])}"
            if op in {"exists", "missing", "truthy", "falsy"}:
                return f"{op.upper()}({_render_predicate(raw)})"
            if op == "runtime_allow_deny":
                return "RUNTIME_ALLOW_DENY"
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        return "[" + ", ".join(_render_predicate(x) for x in value) + "]"
    return repr(value)


def _resolve_value(value: Any, ctx: Dict[str, Any]) -> Any:
    if _is_value_expr(value):
        if "var" in value:
            return ctx.get(str(value["var"]))
        return value.get("const")
    if isinstance(value, list):
        return [_resolve_value(x, ctx) for x in value]
    return value


def _as_iterable(value: Any) -> List[Any]:
    if isinstance(value, set):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        return value
    return []


def _eval_predicate(pred: Any, ctx: Dict[str, Any]) -> bool:
    if pred is None:
        return True

    if isinstance(pred, bool):
        return pred

    if isinstance(pred, list):
        return all(_eval_predicate(p, ctx) for p in pred)

    if not isinstance(pred, dict):
        return bool(pred)

    if _is_value_expr(pred):
        return bool(_resolve_value(pred, ctx))

    if len(pred) != 1:
        return all(_eval_predicate({k: v}, ctx) for k, v in pred.items())

    op, raw = next(iter(pred.items()))

    if op == "all":
        return all(_eval_predicate(p, ctx) for p in _ensure_list(raw))
    if op == "any":
        return any(_eval_predicate(p, ctx) for p in _ensure_list(raw))
    if op == "not":
        return not _eval_predicate(raw, ctx)
    if op == "truthy":
        return bool(_resolve_value(raw, ctx))
    if op == "falsy":
        return not bool(_resolve_value(raw, ctx))
    if op == "exists":
        return _resolve_value(raw, ctx) is not None
    if op == "missing":
        return _resolve_value(raw, ctx) is None
    if op == "runtime_allow_deny":
        ok, _reason = RUNTIME.check_allow_deny(
            tool=str(ctx.get("tool_name", "")),
            instruction_type=str(ctx.get("instruction_type", "")),
            category=ctx.get("instruction_category"),
        )
        return ok

    arr = _ensure_list(raw)
    if op in {"eq", "ne", "gt", "ge", "lt", "le", "in", "not_in", "contains", "intersects"} and len(arr) != 2:
        return False

    if op == "eq":
        return _compare_values(_resolve_value(arr[0], ctx), _resolve_value(arr[1], ctx)) == 0
    if op == "ne":
        return _compare_values(_resolve_value(arr[0], ctx), _resolve_value(arr[1], ctx)) != 0
    if op == "gt":
        return _compare_values(_resolve_value(arr[0], ctx), _resolve_value(arr[1], ctx)) > 0
    if op == "ge":
        return _compare_values(_resolve_value(arr[0], ctx), _resolve_value(arr[1], ctx)) >= 0
    if op == "lt":
        return _compare_values(_resolve_value(arr[0], ctx), _resolve_value(arr[1], ctx)) < 0
    if op == "le":
        return _compare_values(_resolve_value(arr[0], ctx), _resolve_value(arr[1], ctx)) <= 0
    if op == "in":
        lhs = _normalize_scalar_for_membership(_resolve_value(arr[0], ctx))
        rhs = [_normalize_scalar_for_membership(x) for x in _as_iterable(_resolve_value(arr[1], ctx))]
        return lhs in rhs
    if op == "not_in":
        lhs = _normalize_scalar_for_membership(_resolve_value(arr[0], ctx))
        rhs = [_normalize_scalar_for_membership(x) for x in _as_iterable(_resolve_value(arr[1], ctx))]
        return lhs not in rhs
    if op == "contains":
        container = _resolve_value(arr[0], ctx)
        item = _normalize_scalar_for_membership(_resolve_value(arr[1], ctx))
        if isinstance(container, set):
            return item in {_normalize_scalar_for_membership(x) for x in container}
        if isinstance(container, list):
            return item in [_normalize_scalar_for_membership(x) for x in container]
        if isinstance(container, str) and isinstance(item, str):
            return item in _safe_upper(container)
        return False
    if op == "intersects":
        left = {_normalize_scalar_for_membership(x) for x in _as_iterable(_resolve_value(arr[0], ctx))}
        right = {_normalize_scalar_for_membership(x) for x in _as_iterable(_resolve_value(arr[1], ctx))}
        return bool(left & right)

    return False


def _selector_values(raw: Any) -> Optional[Set[str]]:
    if raw is None:
        return None
    vals = _norm_set(raw)
    return vals if vals else None


def _selector_matches(rule: Dict[str, Any], ctx: Dict[str, Any]) -> bool:
    selector = _safe_dict(rule.get("selector"))

    scope = _safe_lower(rule.get("scope"), "")
    if scope and scope not in {"any", _safe_lower(ctx.get("scope"), "")}:
        return False

    tool_values = _selector_values(selector.get("tool") or selector.get("tools"))
    if tool_values and "*" not in tool_values:
        cur = _safe_upper(ctx.get("tool_name"))
        if cur not in tool_values:
            return False

    ins_values = _selector_values(
        selector.get("instruction_type") or selector.get("instruction_types")
    )
    if ins_values and "*" not in ins_values:
        cur = _safe_upper(ctx.get("instruction_type"))
        if cur not in ins_values:
            return False

    cat_values = _selector_values(
        selector.get("category") or selector.get("categories")
    )
    if cat_values and "*" not in cat_values:
        cur = _safe_upper(ctx.get("instruction_category"))
        if cur not in cat_values:
            return False

    return True


def _actual_snapshot(ctx: Dict[str, Any], pred: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name in sorted(_extract_vars(pred)):
        if name in ctx:
            val = ctx.get(name)
            if isinstance(val, set):
                out[name] = sorted(val)
            else:
                out[name] = val
    for key in ("tool_name", "instruction_type", "instruction_category", "scope"):
        if key not in out and key in ctx:
            out[key] = ctx.get(key)
    return out


def _build_rule_decision(rule: Dict[str, Any], index: int, ctx: Dict[str, Any]) -> RuleDecision:
    rule_id = _safe_str(rule.get("id"), f"UG-{index:03d}")
    title = _safe_str(rule.get("title"), rule_id)
    description = _safe_str(rule.get("description"), title)
    effect = _safe_upper(rule.get("effect"), "BLOCK") or "BLOCK"
    message = _safe_str(rule.get("message"))
    if not message:
        message = description or title or rule_id
    return RuleDecision(
        index=index,
        rule_id=rule_id,
        title=title,
        description=description,
        effect=effect,
        scope=_safe_lower(rule.get("scope"), "tool"),
        message=message,
        predicate=rule.get("predicate"),
        selector=_safe_dict(rule.get("selector")),
        actual=_actual_snapshot(ctx, rule.get("predicate")),
        source=_safe_str(rule.get("source")),
    )


def _evaluate_rules(
    *,
    rules: List[Dict[str, Any]],
    ctx: Dict[str, Any],
) -> Optional[RuleDecision]:
    for idx, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            continue
        if not bool(rule.get("enabled", True)):
            continue
        if not _selector_matches(rule, ctx):
            continue
        pred = rule.get("predicate")
        if _eval_predicate(pred, ctx):
            return _build_rule_decision(rule, idx, ctx)
    return None


# ---------------------------------------------------------------------------
# Legacy config -> declarative rules
# ---------------------------------------------------------------------------


def _append_rule(
    out: List[Dict[str, Any]],
    *,
    rule_id: str,
    title: str,
    description: str,
    scope: str,
    selector: Optional[Dict[str, Any]],
    predicate: Any,
    effect: str = "BLOCK",
    message: Optional[str] = None,
    source: str = "legacy",
) -> None:
    out.append(
        {
            "id": rule_id,
            "title": title,
            "description": description,
            "scope": scope,
            "selector": selector or {},
            "predicate": predicate,
            "effect": effect,
            "message": message or description,
            "enabled": True,
            "source": source,
        }
    )


def _selector_for_level(kind: str, name: str) -> Dict[str, Any]:
    if kind == "tool":
        return {"tool": [name]}
    if kind == "instruction":
        return {"instruction_type": [name]}
    if kind == "category":
        return {"category": [name]}
    return {}


def _legacy_map_rules(
    *,
    out: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    base_key: str,
    ctx_var: str,
    compare_op: str,
    rule_prefix: str,
    title_prefix: str,
    description_template: str,
    message_template: str,
) -> None:
    default_val = cfg.get(base_key)
    if default_val not in (None, "", [], {}):
        _append_rule(
            out,
            rule_id=f"{rule_prefix}-DEFAULT",
            title=f"{title_prefix} default",
            description=description_template.format(scope="default", target="*", value=default_val),
            scope="any",
            selector={},
            predicate={compare_op: [{"var": ctx_var}, {"const": default_val}]},
            effect="BLOCK",
            message=message_template.format(var=ctx_var, value=default_val),
        )

    for kind, suffix in (("tool", "by_tool"), ("instruction", "by_instruction"), ("category", "by_category")):
        mapping = cfg.get(f"{base_key}_{suffix}")
        if not isinstance(mapping, dict):
            continue
        for name, value in mapping.items():
            if value in (None, "", [], {}):
                continue
            selector = _selector_for_level(kind, str(name))
            _append_rule(
                out,
                rule_id=f"{rule_prefix}-{kind.upper()}-{_safe_upper(name, str(name))}",
                title=f"{title_prefix} {kind}:{name}",
                description=description_template.format(scope=kind, target=name, value=value),
                scope="any",
                selector=selector,
                predicate={compare_op: [{"var": ctx_var}, {"const": value}]},
                effect="BLOCK",
                message=message_template.format(var=ctx_var, value=value),
            )


def _legacy_bool_rules(
    *,
    out: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    base_key: str,
    ctx_var: str,
    rule_prefix: str,
    title_prefix: str,
    description_template: str,
    message_template: str,
) -> None:
    default_val = cfg.get(base_key)
    if bool(default_val):
        _append_rule(
            out,
            rule_id=f"{rule_prefix}-DEFAULT",
            title=f"{title_prefix} default",
            description=description_template.format(scope="default", target="*", value=True),
            scope="any",
            selector={},
            predicate={"truthy": {"var": ctx_var}},
            effect="BLOCK",
            message=message_template.format(var=ctx_var),
        )

    for kind, suffix in (("tool", "by_tool"), ("instruction", "by_instruction"), ("category", "by_category")):
        mapping = cfg.get(f"{base_key}_{suffix}")
        if not isinstance(mapping, dict):
            continue
        for name, value in mapping.items():
            if not bool(value):
                continue
            selector = _selector_for_level(kind, str(name))
            _append_rule(
                out,
                rule_id=f"{rule_prefix}-{kind.upper()}-{_safe_upper(name, str(name))}",
                title=f"{title_prefix} {kind}:{name}",
                description=description_template.format(scope=kind, target=name, value=True),
                scope="any",
                selector=selector,
                predicate={"truthy": {"var": ctx_var}},
                effect="BLOCK",
                message=message_template.format(var=ctx_var),
            )


def _legacy_membership_rules(
    *,
    out: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    base_key: str,
    ctx_var: str,
    positive: bool,
    rule_prefix: str,
    title_prefix: str,
    description_template: str,
    message_template: str,
) -> None:
    default_val = cfg.get(base_key)
    if default_val not in (None, "", [], {}):
        pred = {"not_in" if positive else "in": [{"var": ctx_var}, {"const": list(_norm_set(default_val))}]}
        _append_rule(
            out,
            rule_id=f"{rule_prefix}-DEFAULT",
            title=f"{title_prefix} default",
            description=description_template.format(scope="default", target="*", value=sorted(_norm_set(default_val))),
            scope="any",
            selector={},
            predicate=pred,
            effect="BLOCK",
            message=message_template.format(var=ctx_var, value=sorted(_norm_set(default_val))),
        )

    for kind, suffix in (("tool", "by_tool"), ("instruction", "by_instruction"), ("category", "by_category")):
        mapping = cfg.get(f"{base_key}_{suffix}")
        if not isinstance(mapping, dict):
            continue
        for name, value in mapping.items():
            normalized = sorted(_norm_set(value))
            if not normalized:
                continue
            selector = _selector_for_level(kind, str(name))
            pred = {"not_in" if positive else "in": [{"var": ctx_var}, {"const": normalized}]}
            _append_rule(
                out,
                rule_id=f"{rule_prefix}-{kind.upper()}-{_safe_upper(name, str(name))}",
                title=f"{title_prefix} {kind}:{name}",
                description=description_template.format(scope=kind, target=name, value=normalized),
                scope="any",
                selector=selector,
                predicate=pred,
                effect="BLOCK",
                message=message_template.format(var=ctx_var, value=normalized),
            )


def _legacy_required_tag_rules(
    *,
    out: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    base_key: str,
    rule_prefix: str,
) -> None:
    default_val = cfg.get(base_key)
    if default_val not in (None, "", [], {}):
        required = sorted(_norm_set(default_val))
        _append_rule(
            out,
            rule_id=f"{rule_prefix}-DEFAULT",
            title="required tags default",
            description=f"default requires at least one tag from {required}",
            scope="any",
            selector={},
            predicate={"not": {"intersects": [{"var": "tags"}, {"const": required}]}},
            effect="BLOCK",
            message=f"required tag(s) missing: one of {required}",
        )

    for kind, suffix in (("tool", "by_tool"), ("instruction", "by_instruction"), ("category", "by_category")):
        mapping = cfg.get(f"{base_key}_{suffix}")
        if not isinstance(mapping, dict):
            continue
        for name, value in mapping.items():
            required = sorted(_norm_set(value))
            if not required:
                continue
            selector = _selector_for_level(kind, str(name))
            _append_rule(
                out,
                rule_id=f"{rule_prefix}-{kind.upper()}-{_safe_upper(name, str(name))}",
                title=f"required tags {kind}:{name}",
                description=f"{kind}:{name} requires at least one tag from {required}",
                scope="any",
                selector=selector,
                predicate={"not": {"intersects": [{"var": "tags"}, {"const": required}]}},
                effect="BLOCK",
                message=f"required tag(s) missing: one of {required}",
            )


def _legacy_rule_bundle() -> Dict[str, Any]:
    rules: List[Dict[str, Any]] = []
    unary_cfg = _get_unary_cfg()

    if bool(unary_cfg.get("fail_closed_on_missing_instruction", False)):
        _append_rule(
            rules,
            rule_id="UG-LEGACY-MISSING-INSTRUCTION",
            title="missing instruction metadata",
            description="block tool call when kernel did not attach instruction metadata",
            scope="tool",
            selector={},
            predicate={"truthy": {"var": "missing_instruction"}},
            effect="BLOCK",
            message="当前 tool_call 没有找到对应的 instruction metadata，策略无法安全判定。",
        )

    if bool(unary_cfg.get("include_runtime_allow_deny", True)):
        _append_rule(
            rules,
            rule_id="UG-LEGACY-ALLOW-DENY",
            title="runtime allow/deny compatibility",
            description="respect existing runtime allow/deny decision",
            scope="any",
            selector={},
            predicate={"not": {"runtime_allow_deny": True}},
            effect="BLOCK",
            message="allow/deny policy does not permit this action.",
        )

    input_budget = _get_input_budget_cfg()
    max_str_len = int(input_budget.get("max_str_len", 0) or 0)
    if max_str_len > 0:
        _append_rule(
            rules,
            rule_id="UG-LEGACY-ARG-BUDGET",
            title="argument string budget",
            description=f"block when total argument string length exceeds {max_str_len}",
            scope="tool",
            selector={},
            predicate={"gt": [{"var": "arg_total_str_len"}, {"const": max_str_len}]},
            effect="BLOCK",
            message=f"total argument string length > max_str_len {max_str_len}",
        )

    sec = _get_security_cfg()
    _legacy_map_rules(
        out=rules,
        cfg=sec,
        base_key="min_confidence",
        ctx_var="confidence",
        compare_op="lt",
        rule_prefix="UG-SEC-MIN-CONFIDENCE",
        title_prefix="min confidence",
        description_template="{scope}:{target} blocks when confidence < {value}",
        message_template="{var} < required ({var} < {value})",
    )
    _legacy_map_rules(
        out=rules,
        cfg=sec,
        base_key="min_trustworthiness",
        ctx_var="trustworthiness",
        compare_op="lt",
        rule_prefix="UG-SEC-MIN-TRUST",
        title_prefix="min trustworthiness",
        description_template="{scope}:{target} blocks when trustworthiness < {value}",
        message_template="{var} < required ({var} < {value})",
    )
    _legacy_map_rules(
        out=rules,
        cfg=sec,
        base_key="min_prop_trustworthiness",
        ctx_var="prop_trustworthiness",
        compare_op="lt",
        rule_prefix="UG-SEC-MIN-PROP-TRUST",
        title_prefix="min prop trustworthiness",
        description_template="{scope}:{target} blocks when prop_trustworthiness < {value}",
        message_template="{var} < required ({var} < {value})",
    )
    _legacy_map_rules(
        out=rules,
        cfg=sec,
        base_key="max_confidentiality",
        ctx_var="confidentiality",
        compare_op="gt",
        rule_prefix="UG-SEC-MAX-CONF",
        title_prefix="max confidentiality",
        description_template="{scope}:{target} blocks when confidentiality > {value}",
        message_template="{var} > allowed ({var} > {value})",
    )
    _legacy_map_rules(
        out=rules,
        cfg=sec,
        base_key="max_prop_confidentiality",
        ctx_var="prop_confidentiality",
        compare_op="gt",
        rule_prefix="UG-SEC-MAX-PROP-CONF",
        title_prefix="max propagated confidentiality",
        description_template="{scope}:{target} blocks when prop_confidentiality > {value}",
        message_template="{var} > allowed ({var} > {value})",
    )
    _legacy_membership_rules(
        out=rules,
        cfg=sec,
        base_key="allowed_authorities",
        ctx_var="authority",
        positive=True,
        rule_prefix="UG-SEC-AUTHORITY",
        title_prefix="allowed authorities",
        description_template="{scope}:{target} blocks when authority not in {value}",
        message_template="{var} is not allowed; allowed={value}",
    )
    _legacy_bool_rules(
        out=rules,
        cfg=sec,
        base_key="require_reversible",
        ctx_var="reversible",
        rule_prefix="UG-SEC-REVERSIBLE",
        title_prefix="require reversible",
        description_template="{scope}:{target} requires reversible=true",
        message_template="reversible=true is required",
    )

    risk = _get_risk_cfg()
    _legacy_membership_rules(
        out=rules,
        cfg=risk,
        base_key="blocked_risks",
        ctx_var="risk",
        positive=False,
        rule_prefix="UG-RISK-BLOCKED",
        title_prefix="blocked risks",
        description_template="{scope}:{target} blocks risk in {value}",
        message_template="{var} is blocked; blocked={value}",
    )
    _legacy_membership_rules(
        out=rules,
        cfg=risk,
        base_key="allowed_risks",
        ctx_var="risk",
        positive=True,
        rule_prefix="UG-RISK-ALLOWED",
        title_prefix="allowed risks",
        description_template="{scope}:{target} blocks risk not in {value}",
        message_template="{var} is not in allowed_risks {value}",
    )
    _legacy_bool_rules(
        out=rules,
        cfg=risk,
        base_key="block_review_required",
        ctx_var="review_required",
        rule_prefix="UG-RISK-REVIEW-REQUIRED",
        title_prefix="block review_required",
        description_template="{scope}:{target} blocks when review_required=true",
        message_template="kernel metadata marks this action as review_required",
    )
    _legacy_bool_rules(
        out=rules,
        cfg=risk,
        base_key="block_approval_required",
        ctx_var="approval_required",
        rule_prefix="UG-RISK-APPROVAL-REQUIRED",
        title_prefix="block approval_required",
        description_template="{scope}:{target} blocks when approval_required=true",
        message_template="kernel metadata marks this action as approval_required",
    )
    _legacy_bool_rules(
        out=rules,
        cfg=risk,
        base_key="block_destructive",
        ctx_var="destructive",
        rule_prefix="UG-RISK-DESTRUCTIVE",
        title_prefix="block destructive",
        description_template="{scope}:{target} blocks when destructive=true",
        message_template="kernel metadata marks this action as destructive",
    )

    tags = _get_tag_cfg()
    default_blocked_tags = tags.get("blocked_tags")
    if default_blocked_tags not in (None, "", [], {}):
        blocked = sorted(_norm_set(default_blocked_tags))
        _append_rule(
            rules,
            rule_id="UG-TAG-BLOCKED-DEFAULT",
            title="blocked tags default",
            description=f"default blocks if tags intersect with {blocked}",
            scope="any",
            selector={},
            predicate={"intersects": [{"var": "tags"}, {"const": blocked}]},
            effect="BLOCK",
            message=f"blocked tag(s): {blocked}",
        )
    for kind, suffix in (("tool", "by_tool"), ("instruction", "by_instruction"), ("category", "by_category")):
        mapping = tags.get(f"blocked_tags_{suffix}")
        if not isinstance(mapping, dict):
            continue
        for name, value in mapping.items():
            blocked = sorted(_norm_set(value))
            if not blocked:
                continue
            _append_rule(
                rules,
                rule_id=f"UG-TAG-BLOCKED-{kind.upper()}-{_safe_upper(name, str(name))}",
                title=f"blocked tags {kind}:{name}",
                description=f"{kind}:{name} blocks if tags intersect with {blocked}",
                scope="any",
                selector=_selector_for_level(kind, str(name)),
                predicate={"intersects": [{"var": "tags"}, {"const": blocked}]},
                effect="BLOCK",
                message=f"blocked tag(s): {blocked}",
            )

    _legacy_required_tag_rules(
        out=rules,
        cfg=tags,
        base_key="required_tags",
        rule_prefix="UG-TAG-REQUIRED",
    )

    return {
        "evaluation_mode": "first_match",
        "rules": rules,
        "source": "legacy-config-compiled",
    }


def _load_rule_bundle() -> Dict[str, Any]:
    unary_cfg = _get_unary_cfg()
    path = _safe_str(
        unary_cfg.get("rule_file") or "arbiteros_kernel/policy/unary_gate_rules.json"
    )
    if path:
        bundle = _read_rule_bundle_from_file(path)
        if bundle:
            return bundle
    return _legacy_rule_bundle()


# ---------------------------------------------------------------------------
# Context building + user-facing messages
# ---------------------------------------------------------------------------


def _build_tool_context(
    *,
    tool_name: str,
    tool_call_id: str,
    args_dict: Dict[str, Any],
    ins: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if isinstance(ins, dict):
        md = _extract_metadata_view(ins)
    else:
        md = {
            "instruction_type": _safe_upper(RUNTIME.tool_to_instruction_type(tool_name)),
            "instruction_category": RUNTIME.instruction_type_to_category(
                _safe_upper(RUNTIME.tool_to_instruction_type(tool_name))
            ),
            "trustworthiness": "UNKNOWN",
            "confidentiality": "UNKNOWN",
            "prop_trustworthiness": "UNKNOWN",
            "prop_confidentiality": "UNKNOWN",
            "confidence": "UNKNOWN",
            "authority": "UNKNOWN",
            "reversible": False,
            "risk": "UNKNOWN",
            "custom": {},
            "tags": set(),
            "review_required": False,
            "approval_required": False,
            "destructive": False,
        }

    instruction_type = md["instruction_type"] or _safe_upper(
        RUNTIME.tool_to_instruction_type(tool_name)
    )
    category = md["instruction_category"] or RUNTIME.instruction_type_to_category(
        instruction_type
    )

    return {
        "scope": "tool",
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "instruction_type": instruction_type,
        "instruction_category": category,
        "missing_instruction": not isinstance(ins, dict),
        "arg_total_str_len": _estimate_argument_string_budget(args_dict),
        **md,
    }


def _build_respond_context(ins: Dict[str, Any]) -> Dict[str, Any]:
    md = _extract_metadata_view(ins)
    md["instruction_type"] = "RESPOND"
    md["instruction_category"] = md["instruction_category"] or "EXECUTION.Human"
    return {
        "scope": "respond",
        "tool_name": "@instruction",
        "tool_call_id": "",
        "instruction_type": "RESPOND",
        "instruction_category": md["instruction_category"],
        "missing_instruction": False,
        "arg_total_str_len": 0,
        **md,
    }


def _format_actual(actual: Dict[str, Any]) -> str:
    parts: List[str] = []
    for k, v in actual.items():
        if isinstance(v, list):
            parts.append(f"{k}={v}")
        else:
            parts.append(f"{k}={v}")
    return ", ".join(parts)


def _friendly_tool_block(decision: RuleDecision, ctx: Dict[str, Any]) -> str:
    lines: List[str] = [
        f"我没有执行工具 `{ctx.get('tool_name')}`。",
        f"命中第 {decision.index} 条 policy（{decision.rule_id}）。",
        f"这一步被识别为 `{ctx.get('instruction_type')}` 类型的操作。",
    ]
    category = _safe_str(ctx.get("instruction_category"))
    if category:
        lines.append(f"当前操作类别为 `{category}`。")
    if decision.description:
        lines.append(f"规则描述：{decision.description}")
    lines.append(f"形式化条件：{_render_predicate(decision.predicate)}")
    actual = _format_actual(decision.actual)
    if actual:
        lines.append(f"实际值：{actual}")
    lines.append(f"处理结果：{decision.effect}")
    if decision.message:
        lines.append(f"补充说明：{decision.message}")
    lines.append("如果你希望继续，请修改对应规则文件、上游 metadata，或调整执行流程后再试。")
    return "\n".join(lines)


def _friendly_respond_block(decision: RuleDecision, ctx: Dict[str, Any]) -> str:
    lines: List[str] = [
        "我没有直接输出这条回复。",
        f"命中第 {decision.index} 条 policy（{decision.rule_id}）。",
        "原因：基于当前 RESPOND instruction 的 metadata，当前策略不允许直接返回这类内容。",
    ]
    if decision.description:
        lines.append(f"规则描述：{decision.description}")
    lines.append(f"形式化条件：{_render_predicate(decision.predicate)}")
    actual = _format_actual(decision.actual)
    if actual:
        lines.append(f"实际值：{actual}")
    lines.append(f"处理结果：{decision.effect}")
    if decision.message:
        lines.append(f"补充说明：{decision.message}")
    lines.append("如果你希望继续，请先调整当前响应对应的 metadata / policy 规则文件。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class UnaryGatePolicy(Policy):
    """
    Main unary gate.

    Boundary:
    - only reads current instruction + lowered metadata
    - does NOT parse shell/path semantics
    - assumes kernel is responsible for lowering quality

    Main upgrade in this version:
    - rules are declarative and can live in a separate file
    - every block points to the matched rule index / rule id / predicate / actual values
    - old structured config is auto-compiled into rule list for backward compatibility
    """

    def check(
        self,
        instructions: List[Dict[str, Any]],
        current_response: Dict[str, Any],
        latest_instructions: List[Dict[str, Any]],
        trace_id: str,
        **kwargs: Any,
    ) -> PolicyCheckResult:
        policy_enabled = bool(kwargs.get("policy_enabled", True))
        response = dict(current_response)
        tool_calls = RUNTIME.extract_tool_calls(response)
        latest_idx = _latest_tool_instr_index(latest_instructions)
        rule_bundle = _load_rule_bundle()
        rules = rule_bundle.get("rules")
        rules = rules if isinstance(rules, list) else []

        errors: List[str] = []
        inactive_errors: List[str] = []
        kept: List[Dict[str, Any]] = []
        changed = False

        # -------------------------------------------------------------------
        # Tool-call unary gating
        # -------------------------------------------------------------------
        for tc in tool_calls:
            tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}
            args_dict = canonicalize_args(args_dict)

            ins = latest_idx.get(tool_call_id or "")
            ctx = _build_tool_context(
                tool_name=tool_name,
                tool_call_id=tool_call_id or "",
                args_dict=args_dict,
                ins=ins,
            )

            decision = _evaluate_rules(rules=rules, ctx=ctx)

            new_tc = RUNTIME.write_back_tool_args(tc, args_dict, was_json_str)
            if new_tc != tc:
                changed = True

            if decision is None:
                kept.append(new_tc)
                continue

            user_msg = _friendly_tool_block(decision, ctx)
            RUNTIME.audit(
                phase="policy.unary_gate",
                trace_id=trace_id,
                tool=tool_name,
                decision=decision.effect if policy_enabled else "INACTIVE",
                reason=(
                    f"rule#{decision.index} {decision.rule_id}: "
                    f"{decision.message or decision.description or decision.title}"
                ),
                args=args_dict,
                extra={
                    "rule_index": decision.index,
                    "rule_id": decision.rule_id,
                    "rule_title": decision.title,
                    "rule_description": decision.description,
                    "rule_effect": decision.effect,
                    "rule_scope": decision.scope,
                    "rule_selector": decision.selector,
                    "rule_predicate": decision.predicate,
                    "rule_source": decision.source or rule_bundle.get("source"),
                    "actual": decision.actual,
                    "ctx": {
                        "instruction_type": ctx["instruction_type"],
                        "instruction_category": ctx["instruction_category"],
                        "trustworthiness": ctx["trustworthiness"],
                        "confidentiality": ctx["confidentiality"],
                        "prop_trustworthiness": ctx["prop_trustworthiness"],
                        "prop_confidentiality": ctx["prop_confidentiality"],
                        "confidence": ctx["confidence"],
                        "authority": ctx["authority"],
                        "reversible": ctx["reversible"],
                        "risk": ctx["risk"],
                        "tags": sorted(ctx["tags"]) if isinstance(ctx["tags"], set) else ctx["tags"],
                        "review_required": ctx["review_required"],
                        "approval_required": ctx["approval_required"],
                        "destructive": ctx["destructive"],
                        "missing_instruction": ctx["missing_instruction"],
                        "arg_total_str_len": ctx["arg_total_str_len"],
                    },
                },
            )

            if policy_enabled:
                errors.append(user_msg)
            else:
                inactive_errors.append(user_msg)
                kept.append(new_tc)

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
        # RESPOND unary gating
        # -------------------------------------------------------------------
        content = response.get("content")
        if isinstance(content, str) and content.strip():
            respond_ins = _find_latest_respond_instruction(latest_instructions)
            if isinstance(respond_ins, dict):
                ctx = _build_respond_context(respond_ins)
                decision = _evaluate_rules(rules=rules, ctx=ctx)

                if decision is not None:
                    user_msg = _friendly_respond_block(decision, ctx)
                    RUNTIME.audit(
                        phase="policy.unary_gate",
                        trace_id=trace_id,
                        tool="@instruction",
                        decision=decision.effect if policy_enabled else "INACTIVE",
                        reason=(
                            f"rule#{decision.index} {decision.rule_id}: "
                            f"{decision.message or decision.description or decision.title}"
                        ),
                        args={},
                        extra={
                            "rule_index": decision.index,
                            "rule_id": decision.rule_id,
                            "rule_title": decision.title,
                            "rule_description": decision.description,
                            "rule_effect": decision.effect,
                            "rule_scope": decision.scope,
                            "rule_selector": decision.selector,
                            "rule_predicate": decision.predicate,
                            "rule_source": decision.source or rule_bundle.get("source"),
                            "actual": decision.actual,
                            "ctx": {
                                "instruction_type": ctx["instruction_type"],
                                "instruction_category": ctx["instruction_category"],
                                "trustworthiness": ctx["trustworthiness"],
                                "confidentiality": ctx["confidentiality"],
                                "prop_trustworthiness": ctx["prop_trustworthiness"],
                                "prop_confidentiality": ctx["prop_confidentiality"],
                                "confidence": ctx["confidence"],
                                "authority": ctx["authority"],
                                "reversible": ctx["reversible"],
                                "risk": ctx["risk"],
                                "tags": sorted(ctx["tags"]) if isinstance(ctx["tags"], set) else ctx["tags"],
                                "review_required": ctx["review_required"],
                                "approval_required": ctx["approval_required"],
                                "destructive": ctx["destructive"],
                            },
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

        if changed and policy_enabled:
            response["tool_calls"] = kept
            return PolicyCheckResult(
                modified=True,
                response=response,
                error_type=None,
                inactivate_error_type=None,
            )

        return PolicyCheckResult(
            modified=False,
            response=current_response,
            error_type=None,
            inactivate_error_type=None,
        )
