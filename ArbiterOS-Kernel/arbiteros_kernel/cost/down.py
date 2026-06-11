from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from arbiteros_kernel.policy_runtime import get_runtime


_MARKER = "[arbiteros_cost_down]"


@dataclass(frozen=True)
class CostDownDecision:
    applied: bool
    level: str
    reason: str
    hint: str
    metrics: dict[str, Any]
    max_completion_tokens_applied: Optional[int] = None


def _cfg() -> dict[str, Any]:
    try:
        cfg = get_runtime().cfg
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        return {}
    block = cfg.get("cost_down")
    return block if isinstance(block, dict) else {}


def _resource_cfg() -> dict[str, Any]:
    try:
        cfg = get_runtime().cfg
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        return {}
    block = cfg.get("resource_guard")
    return block if isinstance(block, dict) else {}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v >= 0 else default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return v if v >= 0 else default


def _stringify_for_budget(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def estimate_request_input_tokens(request_data: dict[str, Any]) -> int:
    """Cheap pre-call token estimate for budget guidance.

    This is intentionally approximate. It is used only to decide whether to add
    a short cost-down hint before the real provider usage is known.
    """

    if not isinstance(request_data, dict):
        return 0
    char_count = 0
    messages = request_data.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            char_count += len(_stringify_for_budget(msg.get("role")))
            char_count += len(_stringify_for_budget(msg.get("content")))
            for key in ("tool_calls", "function_call", "name"):
                if key in msg:
                    char_count += len(_stringify_for_budget(msg.get(key)))
    else:
        for key in ("input", "instructions", "prompt"):
            if key in request_data:
                char_count += len(_stringify_for_budget(request_data.get(key)))

    for key in ("tools", "response_format"):
        if key in request_data:
            char_count += len(_stringify_for_budget(request_data.get(key)))

    return max(0, (char_count + 3) // 4)


def _ratio(observed: float, threshold: float) -> float:
    if threshold <= 0:
        return 0.0
    return observed / threshold


def _metric(
    metrics: dict[str, Any],
    *,
    key: str,
    observed: float,
    threshold: float,
    warn_ratio: float,
    critical_ratio: float,
) -> None:
    ratio = _ratio(observed, threshold)
    metrics[key] = {
        "observed": observed,
        "threshold": threshold,
        "ratio": ratio,
        "warn": threshold > 0 and ratio >= warn_ratio,
        "critical": threshold > 0 and ratio >= critical_ratio,
    }


def _build_metrics(
    request_data: dict[str, Any],
    policy_runtime_context: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    resource = _resource_cfg()
    rg = policy_runtime_context.get("resource_guard")
    rg = rg if isinstance(rg, dict) else {}

    request_estimated_input_tokens = estimate_request_input_tokens(request_data)
    total_tokens = _to_float(rg.get("total_tokens"))
    total_cost_usd = _to_float(rg.get("total_cost_usd"))
    instruction_bytes = _to_float(rg.get("instruction_bytes"))
    instruction_count = _to_float(rg.get("instruction_count"))

    warn_ratio = _to_float(cfg.get("warn_ratio"), 0.8)
    critical_ratio = _to_float(cfg.get("critical_ratio"), 0.95)
    warn_ratio = min(max(warn_ratio, 0.0), 1.0)
    critical_ratio = min(max(critical_ratio, warn_ratio), 1.0)

    metrics: dict[str, Any] = {
        "request_estimated_input_tokens": request_estimated_input_tokens,
        "trace_total_tokens": total_tokens,
        "trace_total_cost_usd": total_cost_usd,
        "trace_instruction_bytes": instruction_bytes,
        "trace_instruction_count": instruction_count,
    }
    _metric(
        metrics,
        key="token_budget",
        observed=total_tokens + request_estimated_input_tokens,
        threshold=_to_float(resource.get("max_total_tokens")),
        warn_ratio=warn_ratio,
        critical_ratio=critical_ratio,
    )
    _metric(
        metrics,
        key="cost_budget",
        observed=total_cost_usd,
        threshold=_to_float(resource.get("max_total_cost_usd")),
        warn_ratio=_to_float(cfg.get("warn_cost_ratio"), warn_ratio),
        critical_ratio=_to_float(cfg.get("critical_cost_ratio"), critical_ratio),
    )
    _metric(
        metrics,
        key="instruction_bytes_budget",
        observed=instruction_bytes,
        threshold=_to_float(resource.get("max_instruction_bytes")),
        warn_ratio=_to_float(cfg.get("warn_instruction_bytes_ratio"), warn_ratio),
        critical_ratio=_to_float(
            cfg.get("critical_instruction_bytes_ratio"), critical_ratio
        ),
    )
    _metric(
        metrics,
        key="request_input_budget",
        observed=request_estimated_input_tokens,
        threshold=_to_float(cfg.get("max_request_input_tokens")),
        warn_ratio=warn_ratio,
        critical_ratio=critical_ratio,
    )
    return metrics


def _should_apply(metrics: dict[str, Any]) -> tuple[bool, str, str]:
    critical = [
        name
        for name, value in metrics.items()
        if isinstance(value, dict) and bool(value.get("critical"))
    ]
    if critical:
        return True, "critical", ", ".join(critical)
    warn = [
        name
        for name, value in metrics.items()
        if isinstance(value, dict) and bool(value.get("warn"))
    ]
    if warn:
        return True, "warn", ", ".join(warn)
    return False, "none", ""


def _fmt_int(value: Any) -> str:
    return f"{int(_to_float(value)):d}"


def _fmt_usd(value: Any) -> str:
    return f"${_to_float(value):.6f}"


def build_cost_down_hint(
    *,
    trace_id: Optional[str],
    level: str,
    reason: str,
    metrics: dict[str, Any],
    max_chars: int,
) -> str:
    token_budget = metrics.get("token_budget")
    cost_budget = metrics.get("cost_budget")
    bytes_budget = metrics.get("instruction_bytes_budget")
    lines = [
        _MARKER,
        "Cost budget signal for this trace.",
        f"- level: {level}",
        f"- reason: {reason}",
        f"- trace_id: {trace_id or 'unknown'}",
        f"- estimated current request input tokens: {_fmt_int(metrics.get('request_estimated_input_tokens'))}",
    ]
    if isinstance(token_budget, dict) and token_budget.get("threshold", 0) > 0:
        lines.append(
            "- token budget: "
            f"{_fmt_int(token_budget.get('observed'))}/"
            f"{_fmt_int(token_budget.get('threshold'))}"
        )
    if isinstance(cost_budget, dict) and cost_budget.get("threshold", 0) > 0:
        lines.append(
            "- cost budget: "
            f"{_fmt_usd(cost_budget.get('observed'))}/"
            f"{_fmt_usd(cost_budget.get('threshold'))}"
        )
    if isinstance(bytes_budget, dict) and bytes_budget.get("threshold", 0) > 0:
        lines.append(
            "- instruction memory: "
            f"{_fmt_int(bytes_budget.get('observed'))}/"
            f"{_fmt_int(bytes_budget.get('threshold'))} bytes"
        )

    lines.extend(
        [
            "",
            "Cost-down guidance:",
            "- If enough evidence is available, stop optional tool calls and produce the final answer now.",
            "- If another tool call is necessary, make it narrow and avoid broad listings, full logs, or repeated retries.",
            "- Keep the next response concise; summarize only the decision-relevant evidence.",
        ]
    )
    if level == "critical":
        lines.append(
            "- Budget is critical: prefer a final answer over further exploration unless the user explicitly requires more tool use."
        )

    hint = "\n".join(lines)
    if max_chars > 0 and len(hint) > max_chars:
        return hint[: max_chars - 3].rstrip() + "..."
    return hint


def _apply_completion_cap(
    request_data: dict[str, Any], cap: int
) -> tuple[dict[str, Any], Optional[int]]:
    if cap <= 0:
        return request_data, None
    data = dict(request_data)
    key = "max_completion_tokens" if "max_completion_tokens" in data else None
    if key is None and "max_tokens" in data:
        key = "max_tokens"
    if key is None:
        key = "max_completion_tokens"
    current_int = _to_int(data.get(key), 0)
    if current_int <= 0 or current_int > cap:
        data[key] = cap
        return data, cap
    return data, None


def apply_cost_down_to_request(
    request_data: dict[str, Any],
    *,
    trace_id: Optional[str],
    policy_runtime_context: dict[str, Any],
    inject_system_hint: Callable[[dict[str, Any], str, str], dict[str, Any]],
) -> tuple[dict[str, Any], CostDownDecision]:
    cfg = _cfg()
    if not bool(cfg.get("enabled", True)):
        return request_data, CostDownDecision(False, "disabled", "", "", {})

    metrics = _build_metrics(request_data, policy_runtime_context, cfg)
    should_apply, level, reason = _should_apply(metrics)
    if not should_apply:
        return request_data, CostDownDecision(False, level, reason, "", metrics)

    hint = build_cost_down_hint(
        trace_id=trace_id,
        level=level,
        reason=reason,
        metrics=metrics,
        max_chars=_to_int(cfg.get("hint_max_chars"), 900),
    )
    data = inject_system_hint(request_data, hint, _MARKER)
    cap_applied: Optional[int] = None
    if level == "critical":
        cap = _to_int(cfg.get("critical_max_completion_tokens"), 0)
        data, cap_applied = _apply_completion_cap(data, cap)

    return data, CostDownDecision(
        True,
        level,
        reason,
        hint,
        metrics,
        max_completion_tokens_applied=cap_applied,
    )


__all__ = [
    "CostDownDecision",
    "apply_cost_down_to_request",
    "build_cost_down_hint",
    "estimate_request_input_tokens",
]
