from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from arbiteros_kernel.cost.prompt_compaction import apply_agent_scaffold_compaction_to_request
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


LLMCompressor = Callable[..., Optional[str]]


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


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
    return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return _to_bool(raw, default) if raw is not None else default


def _phase3_policy_from_cfg(cost_down_cfg: dict[str, Any]) -> dict[str, Any]:
    phase3 = cost_down_cfg.get("phase3_runtime")
    phase3 = phase3 if isinstance(phase3, dict) else {}
    for key in ("policy", "runtime_policy", "phase3_policy"):
        value = phase3.get(key)
        if isinstance(value, dict):
            return value

    path_raw = (
        os.getenv("ARBITEROS_COST_DOWN_PHASE3_POLICY_PATH")
        or str(
            phase3.get("policy_path")
            or phase3.get("runtime_policy_path")
            or phase3.get("phase3_policy_path")
            or ""
        )
    ).strip()
    if not path_raw:
        return {}
    try:
        with Path(os.path.expandvars(os.path.expanduser(path_raw))).open(
            "r", encoding="utf-8"
        ) as handle:
            loaded = json.load(handle)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _phase3_runtime_defaults_from_policy(
    cost_down_cfg: dict[str, Any],
) -> dict[str, Any]:
    policy = _phase3_policy_from_cfg(cost_down_cfg)
    execution = policy.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    runtime_defaults = execution.get("runtime_defaults")
    return dict(runtime_defaults) if isinstance(runtime_defaults, dict) else {}


def _phase3_budget_hints_from_policy(cost_down_cfg: dict[str, Any]) -> dict[str, Any]:
    runtime_defaults = _phase3_runtime_defaults_from_policy(cost_down_cfg)
    hints = runtime_defaults.get("budget_hints")
    return dict(hints) if isinstance(hints, dict) else {}


def _phase3_runtime_enabled(cost_down_cfg: dict[str, Any]) -> bool:
    phase3 = cost_down_cfg.get("phase3_runtime")
    phase3 = phase3 if isinstance(phase3, dict) else {}
    return _env_bool(
        "ARBITEROS_COST_DOWN_PHASE3_RUNTIME",
        _to_bool(phase3.get("enabled"), False),
    )


def _effective_cfg(request_data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Merge Cost Doctor Phase 3 budget hints into the existing budget path.

    Explicit `cost_down` config still wins.  Phase 3 policy defaults only fill in
    the non-compression budget-hint channel when the exported policy opts in.
    """

    cfg = dict(_cfg())
    hints = _phase3_budget_hints_from_policy(cfg)
    hints_enabled = _env_bool(
        "ARBITEROS_COST_DOWN_PHASE3_BUDGET_HINTS",
        _to_bool(hints.get("enabled"), False),
    )
    if not hints_enabled:
        return cfg

    cfg_was_enabled = _to_bool(cfg.get("enabled"), True)
    if request_data is not None and not cfg_was_enabled:
        guard = phase3_online_activation_guard(request_data, cost_down_cfg=cfg)
        if guard.get("enabled") and not guard.get("met"):
            cfg["_phase3_budget_hints"] = {
                "enabled": False,
                "deferred": True,
                "source": hints.get("source") or "phase3_runtime_policy",
                "reason": "phase3_deferred_early_trajectory",
                "online_activation_guard": guard,
            }
            return cfg

    if not cfg_was_enabled:
        cfg["enabled"] = True

    fill_values = {
        "max_request_input_tokens": _to_int(
            hints.get("max_request_input_tokens"), 0
        ),
        "warn_ratio": _to_float(hints.get("warn_ratio"), 0.0),
        "critical_ratio": _to_float(hints.get("critical_ratio"), 0.0),
        "critical_max_completion_tokens": _to_int(
            hints.get("critical_max_completion_tokens"), 0
        ),
        "hint_max_chars": _to_int(hints.get("hint_max_chars"), 0),
    }
    for key, value in fill_values.items():
        if value <= 0:
            continue
        current = cfg.get(key)
        if not cfg_was_enabled:
            cfg[key] = value
            continue
        if key == "critical_max_completion_tokens" and key in cfg:
            # For completion caps, an explicit 0 means "do not cap the model's
            # next answer". Phase 3 may still inject budget hints, but it must
            # not convert that explicit opt-out into a hidden output cap.
            continue
        if _to_float(current, 0.0) <= 0:
            cfg[key] = value

    hint_style = str(hints.get("hint_style") or "").strip()
    if hint_style and (not cfg_was_enabled or not str(cfg.get("hint_style") or "").strip()):
        cfg["hint_style"] = hint_style

    cfg["_phase3_budget_hints"] = {
        "enabled": True,
        "source": hints.get("source") or "phase3_runtime_policy",
        "max_request_input_tokens": cfg.get("max_request_input_tokens"),
        "selection": hints.get("selection") if isinstance(hints.get("selection"), dict) else {},
    }
    return cfg


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


def count_request_tool_results(request_data: dict[str, Any]) -> int:
    if not isinstance(request_data, dict):
        return 0
    messages = request_data.get("messages")
    if not isinstance(messages, list):
        return 0
    return sum(
        1
        for message in messages
        if isinstance(message, dict) and message.get("role") == "tool"
    )


def _phase3_activation_guard(
    request_data: dict[str, Any],
    *,
    cost_down_cfg: Optional[dict[str, Any]] = None,
    env_key_prefix: str,
    cfg_key_prefix: str,
    default_min_input_tokens: int,
    default_min_tool_results: int,
    deferred_reason: str,
) -> dict[str, Any]:
    cfg = dict(cost_down_cfg) if isinstance(cost_down_cfg, dict) else dict(_cfg())
    phase3 = cfg.get("phase3_runtime")
    phase3 = phase3 if isinstance(phase3, dict) else {}
    runtime_enabled = _phase3_runtime_enabled(cfg)
    runtime_defaults = _phase3_runtime_defaults_from_policy(cfg)
    guard_key = f"{cfg_key_prefix}_guard"
    min_input_key = f"{cfg_key_prefix}_min_input_tokens"
    min_tool_key = f"{cfg_key_prefix}_min_tool_results"
    guard_enabled = _env_bool(
        f"{env_key_prefix}_GUARD",
        _to_bool(
            phase3.get(guard_key),
            _to_bool(runtime_defaults.get(guard_key), False),
        ),
    )
    estimated_input_tokens = estimate_request_input_tokens(request_data)
    tool_results = count_request_tool_results(request_data)
    min_input_tokens = _to_int(
        os.getenv(f"{env_key_prefix}_MIN_INPUT_TOKENS"),
        _to_int(
            phase3.get(min_input_key),
            _to_int(runtime_defaults.get(min_input_key), default_min_input_tokens),
        ),
    )
    min_tool_results = _to_int(
        os.getenv(f"{env_key_prefix}_MIN_TOOL_RESULTS"),
        _to_int(
            phase3.get(min_tool_key),
            _to_int(runtime_defaults.get(min_tool_key), default_min_tool_results),
        ),
    )

    enabled = bool(runtime_enabled and guard_enabled)
    if not enabled:
        met = True
        reason = "disabled"
    else:
        token_met = min_input_tokens > 0 and estimated_input_tokens >= min_input_tokens
        tool_met = min_tool_results > 0 and tool_results >= min_tool_results
        met = token_met or tool_met or (
            min_input_tokens <= 0 and min_tool_results <= 0
        )
        reason = "activated" if met else deferred_reason

    return {
        "enabled": enabled,
        "met": met,
        "reason": reason,
        "estimated_input_tokens": estimated_input_tokens,
        "tool_results": tool_results,
        "min_input_tokens": min_input_tokens,
        "min_tool_results": min_tool_results,
    }


def phase3_online_activation_guard(
    request_data: dict[str, Any],
    *,
    cost_down_cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Decide whether Phase 3 online hints have enough trajectory to act.

    Short coding tasks are very sensitive to early prompts. This guard lets
    budget/tool feedback wait until the agent has enough evidence for the hint
    to be worth the behavioral risk.
    """

    return _phase3_activation_guard(
        request_data,
        cost_down_cfg=cost_down_cfg,
        env_key_prefix="ARBITEROS_COST_DOWN_PHASE3_ONLINE_ACTIVATION",
        cfg_key_prefix="online_activation",
        default_min_input_tokens=16000,
        default_min_tool_results=8,
        deferred_reason="phase3_deferred_early_trajectory",
    )


def phase3_compression_activation_guard(
    request_data: dict[str, Any],
    *,
    cost_down_cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Decide whether Phase 3 compression has enough trajectory to act.

    Compression can safely start earlier than budget/tool feedback because it
    changes evidence length, not the agent's next-step instructions.
    """

    return _phase3_activation_guard(
        request_data,
        cost_down_cfg=cost_down_cfg,
        env_key_prefix="ARBITEROS_COST_DOWN_PHASE3_COMPRESSION_ACTIVATION",
        cfg_key_prefix="compression_activation",
        default_min_input_tokens=9000,
        default_min_tool_results=5,
        deferred_reason="phase3_deferred_early_compression",
    )


def apply_phase3_runtime_policy_to_request(
    request_data: dict[str, Any],
    *,
    trace_id: Optional[str],
    llm_compressor: LLMCompressor | None = None,
    global_instructions: Optional[list[dict[str, Any]]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply Cost Doctor Phase 3 runtime compression to an outgoing request."""

    from arbiteros_kernel.cost.phase3_compression import (
        apply_phase3_runtime_compression,
    )

    return apply_phase3_runtime_compression(
        request_data,
        trace_id=trace_id,
        llm_compressor=llm_compressor,
        global_instructions=global_instructions,
    )


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
    phase3_budget_hints = cfg.get("_phase3_budget_hints")
    if isinstance(phase3_budget_hints, dict):
        metrics["phase3_budget_hints"] = dict(phase3_budget_hints)
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


def _metric_ratio(metrics: dict[str, Any], name: str) -> float:
    value = metrics.get(name)
    if not isinstance(value, dict):
        return 0.0
    return _to_float(value.get("ratio"))


def _build_compact_cost_down_hint(
    *,
    level: str,
    reason: str,
    metrics: dict[str, Any],
    max_chars: int,
) -> str:
    request_tokens = _fmt_int(metrics.get("request_estimated_input_tokens"))
    token_ratio = _metric_ratio(metrics, "token_budget")
    request_ratio = _metric_ratio(metrics, "request_input_budget")
    pressure = max(token_ratio, request_ratio)
    pressure_text = f"{pressure:.2f}x" if pressure > 0 else "n/a"
    lines = [
        _MARKER,
        (
            f"Budget pressure {level}: {reason}; request~{request_tokens} "
            f"input tokens; pressure={pressure_text}."
        ),
        (
            "Act cost-aware: if evidence is sufficient, finish now; otherwise "
            "make exactly one narrow tool call and avoid broad logs/retries."
        ),
    ]
    if level == "critical":
        lines.append(
            "Critical: prefer final patch/answer over more exploration unless blocked."
        )
    hint = "\n".join(lines)
    if max_chars > 0 and len(hint) > max_chars:
        return hint[: max_chars - 3].rstrip() + "..."
    return hint


def _build_focused_cost_down_hint(
    *,
    level: str,
    reason: str,
    metrics: dict[str, Any],
    max_chars: int,
) -> str:
    request_tokens = _fmt_int(metrics.get("request_estimated_input_tokens"))
    token_ratio = _metric_ratio(metrics, "token_budget")
    request_ratio = _metric_ratio(metrics, "request_input_budget")
    pressure = max(token_ratio, request_ratio)
    pressure_text = f"{pressure:.2f}x" if pressure > 0 else "n/a"
    lines = [
        _MARKER,
        (
            f"Budget pressure {level}: {reason}; request~{request_tokens} "
            f"input tokens; pressure={pressure_text}."
        ),
        (
            "Correctness first. Reduce cost by batching related cheap checks, "
            "reading only targeted files/log slices, and avoiding repeated probes."
        ),
        (
            "When patch plus focused verification is enough, stop exploring and "
            "submit/finalize with concise evidence."
        ),
    ]
    if level == "critical":
        lines.append(
            "Critical: do only the next highest-value check, then decide."
        )
    hint = "\n".join(lines)
    if max_chars > 0 and len(hint) > max_chars:
        return hint[: max_chars - 3].rstrip() + "..."
    return hint


def build_cost_down_hint(
    *,
    trace_id: Optional[str],
    level: str,
    reason: str,
    metrics: dict[str, Any],
    max_chars: int,
    style: str = "standard",
) -> str:
    normalized_style = style.strip().lower()
    if normalized_style in {"compact", "short", "brief"}:
        return _build_compact_cost_down_hint(
            level=level,
            reason=reason,
            metrics=metrics,
            max_chars=max_chars,
        )
    if normalized_style in {"focused", "balanced"}:
        return _build_focused_cost_down_hint(
            level=level,
            reason=reason,
            metrics=metrics,
            max_chars=max_chars,
        )

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
    cfg = _effective_cfg(request_data)
    if not bool(cfg.get("enabled", True)):
        return request_data, CostDownDecision(False, "disabled", "", "", {})

    metrics = _build_metrics(request_data, policy_runtime_context, cfg)
    phase3_budget_hints = metrics.get("phase3_budget_hints")
    if (
        isinstance(phase3_budget_hints, dict)
        and _to_bool(phase3_budget_hints.get("enabled"), False)
    ):
        guard = phase3_online_activation_guard(request_data, cost_down_cfg=cfg)
        if guard.get("enabled") and not guard.get("met"):
            metrics = dict(metrics)
            deferred_hints = dict(phase3_budget_hints)
            deferred_hints["deferred"] = True
            deferred_hints["reason"] = guard.get("reason")
            deferred_hints["online_activation_guard"] = guard
            metrics["phase3_budget_hints"] = deferred_hints
            return request_data, CostDownDecision(
                False,
                "deferred",
                str(guard.get("reason") or "phase3_deferred_early_trajectory"),
                "",
                metrics,
            )

    should_apply, level, reason = _should_apply(metrics)
    if not should_apply:
        return request_data, CostDownDecision(False, level, reason, "", metrics)

    hint = build_cost_down_hint(
        trace_id=trace_id,
        level=level,
        reason=reason,
        metrics=metrics,
        max_chars=_to_int(cfg.get("hint_max_chars"), 900),
        style=str(cfg.get("hint_style") or "standard"),
    )
    data = inject_system_hint(request_data, hint, _MARKER)
    cap_applied: Optional[int] = None
    if level == "critical":
        cap = _to_int(cfg.get("critical_max_completion_tokens"), 0)
        data, cap_applied = _apply_completion_cap(data, cap)
    metadata = data.get("metadata") if isinstance(data, dict) else None
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    metadata["arbiteros_cost_down_hint"] = {
        "level": level,
        "reason": reason,
        "trace_id": trace_id,
        "max_completion_tokens_applied": cap_applied,
        "metrics": {
            "request_estimated_input_tokens": metrics.get("request_estimated_input_tokens"),
            "phase3_budget_hints": metrics.get("phase3_budget_hints"),
            "request_input_budget": metrics.get("request_input_budget"),
        },
    }
    data["metadata"] = metadata

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
    "apply_agent_scaffold_compaction_to_request",
    "apply_phase3_runtime_policy_to_request",
    "apply_cost_down_to_request",
    "build_cost_down_hint",
    "count_request_tool_results",
    "estimate_request_input_tokens",
    "phase3_compression_activation_guard",
    "phase3_online_activation_guard",
]
