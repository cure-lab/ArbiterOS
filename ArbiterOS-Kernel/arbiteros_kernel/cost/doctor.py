from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


_KERNEL_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LOG_FILE = _KERNEL_ROOT / "log" / "cost_telemetry.jsonl"


@dataclass(frozen=True)
class CostStep:
    step_index: int
    trace_id: str
    ts: str
    model: str
    request_model: str
    response_model: str
    pricing_model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _to_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed >= 0 else 0.0


def _log_path(path: Optional[str | Path] = None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env_path = os.getenv("ARBITEROS_COST_TELEMETRY_LOG_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    return _DEFAULT_LOG_FILE


def load_cost_entries(
    path: Optional[str | Path] = None,
    *,
    trace_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    log_path = _log_path(path)
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []

    if isinstance(limit, int) and limit > 0:
        lines = lines[-limit:]

    entries: list[dict[str, Any]] = []
    wanted_trace = trace_id.strip() if isinstance(trace_id, str) and trace_id.strip() else None
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        if wanted_trace and entry.get("trace_id") != wanted_trace:
            continue
        entries.append(entry)
    return entries


def normalize_cost_steps(entries: Iterable[dict[str, Any]]) -> list[CostStep]:
    steps: list[CostStep] = []
    for idx, entry in enumerate(entries, start=1):
        usage = entry.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        cost = entry.get("estimated_cost")
        cost = cost if isinstance(cost, dict) else {}
        request_model = str(entry.get("request_model") or "")
        response_model = str(entry.get("response_model") or "")
        pricing_model = str(entry.get("pricing_model") or "")
        model = response_model or request_model or pricing_model or "unknown"
        prompt_tokens = _to_int(usage.get("prompt_tokens"))
        completion_tokens = _to_int(usage.get("completion_tokens"))
        total_tokens = _to_int(usage.get("total_tokens"))
        if total_tokens <= 0 and (prompt_tokens > 0 or completion_tokens > 0):
            total_tokens = prompt_tokens + completion_tokens
        steps.append(
            CostStep(
                step_index=idx,
                trace_id=str(entry.get("trace_id") or ""),
                ts=str(entry.get("ts") or ""),
                model=model,
                request_model=request_model,
                response_model=response_model,
                pricing_model=pricing_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                reasoning_tokens=_to_int(usage.get("reasoning_tokens")),
                cached_tokens=_to_int(usage.get("cached_tokens")),
                input_cost_usd=_to_float(cost.get("input_cost_usd")),
                output_cost_usd=_to_float(cost.get("output_cost_usd")),
                total_cost_usd=_to_float(cost.get("total_cost_usd")),
            )
        )
    return steps


def _step_dict(step: CostStep) -> dict[str, Any]:
    return {
        "step_index": step.step_index,
        "ts": step.ts,
        "model": step.model,
        "prompt_tokens": step.prompt_tokens,
        "completion_tokens": step.completion_tokens,
        "total_tokens": step.total_tokens,
        "reasoning_tokens": step.reasoning_tokens,
        "cached_tokens": step.cached_tokens,
        "total_cost_usd": step.total_cost_usd,
    }


def _top_steps(
    steps: list[CostStep], key: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    return [
        _step_dict(step)
        for step in sorted(steps, key=lambda item: getattr(item, key), reverse=True)[
            :limit
        ]
        if getattr(step, key) > 0
    ]


def _model_breakdown(steps: list[CostStep]) -> list[dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "steps": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "total_cost_usd": 0.0,
        }
    )
    for step in steps:
        bucket = totals[step.model]
        bucket["steps"] += 1
        bucket["prompt_tokens"] += step.prompt_tokens
        bucket["completion_tokens"] += step.completion_tokens
        bucket["total_tokens"] += step.total_tokens
        bucket["reasoning_tokens"] += step.reasoning_tokens
        bucket["cached_tokens"] += step.cached_tokens
        bucket["total_cost_usd"] += step.total_cost_usd
    return [
        {"model": model, **values}
        for model, values in sorted(
            totals.items(),
            key=lambda item: (item[1]["total_cost_usd"], item[1]["total_tokens"]),
            reverse=True,
        )
    ]


def _bucket(value: int, size: int) -> int:
    if size <= 0:
        return value
    return int(round(value / size) * size)


def _diagnose(summary: dict[str, Any], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    steps = summary["_steps"]
    totals = summary["totals"]
    total_tokens = totals["total_tokens"]
    total_cost = totals["total_cost_usd"]
    prompt_tokens = totals["prompt_tokens"]
    diagnoses: list[dict[str, Any]] = []

    if not steps:
        return [
            {
                "rule_id": "CD-NO-DATA",
                "severity": "info",
                "message": "No cost telemetry entries were found for this trace.",
                "evidence": {},
            }
        ]

    input_heavy_ratio = _to_float(cfg.get("input_heavy_ratio")) or 0.8
    min_input_heavy_tokens = _to_int(cfg.get("min_input_heavy_tokens")) or 4096
    if total_tokens >= min_input_heavy_tokens:
        ratio = prompt_tokens / total_tokens if total_tokens else 0.0
        if ratio >= input_heavy_ratio:
            diagnoses.append(
                {
                    "rule_id": "CD-INPUT-HEAVY",
                    "severity": "medium",
                    "message": "Most tokens are input/context tokens; check whether tool output or history can be summarized before the next step.",
                    "evidence": {
                        "prompt_token_ratio": round(ratio, 4),
                        "prompt_tokens": prompt_tokens,
                        "total_tokens": total_tokens,
                    },
                }
            )

    large_step_tokens = _to_int(cfg.get("large_step_tokens")) or 32000
    large_steps = [step for step in steps if step.total_tokens >= large_step_tokens]
    if large_steps:
        diagnoses.append(
            {
                "rule_id": "CD-LARGE-STEP",
                "severity": "high",
                "message": "One or more LLM calls consumed an unusually large number of tokens.",
                "evidence": {
                    "threshold_tokens": large_step_tokens,
                    "steps": [_step_dict(step) for step in large_steps[:5]],
                },
            }
        )

    low_output_prompt_tokens = _to_int(cfg.get("low_output_prompt_tokens")) or 4000
    low_output_completion_tokens = _to_int(cfg.get("low_output_completion_tokens")) or 128
    low_output_steps = [
        step
        for step in steps
        if step.prompt_tokens >= low_output_prompt_tokens
        and step.completion_tokens <= low_output_completion_tokens
    ]
    if low_output_steps:
        diagnoses.append(
            {
                "rule_id": "CD-LOW-OUTPUT",
                "severity": "medium",
                "message": "Some calls sent a large context but produced very little output, which often indicates over-context or a no-op retry.",
                "evidence": {
                    "steps": [_step_dict(step) for step in low_output_steps[:5]],
                    "prompt_threshold": low_output_prompt_tokens,
                    "completion_threshold": low_output_completion_tokens,
                },
            }
        )

    cost_spike_ratio = _to_float(cfg.get("cost_spike_ratio")) or 0.4
    if total_cost > 0 and len(steps) >= 2:
        spike_steps = [
            step for step in steps if step.total_cost_usd / total_cost >= cost_spike_ratio
        ]
        if spike_steps:
            diagnoses.append(
                {
                    "rule_id": "CD-COST-SPIKE",
                    "severity": "medium",
                    "message": "A small number of steps dominate the trace cost.",
                    "evidence": {
                        "ratio_threshold": cost_spike_ratio,
                        "steps": [
                            {
                                **_step_dict(step),
                                "cost_share": round(step.total_cost_usd / total_cost, 4),
                            }
                            for step in spike_steps[:5]
                        ],
                    },
                }
            )

    repeat_min_count = _to_int(cfg.get("repeat_shape_min_count")) or 3
    repeat_bucket_size = _to_int(cfg.get("repeat_bucket_tokens")) or 256
    shapes = Counter(
        (
            step.model,
            _bucket(step.prompt_tokens, repeat_bucket_size),
            _bucket(step.completion_tokens, repeat_bucket_size),
        )
        for step in steps
        if step.total_tokens > 0
    )
    repeated = [
        {"model": model, "prompt_bucket": prompt, "completion_bucket": completion, "count": count}
        for (model, prompt, completion), count in shapes.items()
        if count >= repeat_min_count
    ]
    if repeated:
        diagnoses.append(
            {
                "rule_id": "CD-REPEATED-SHAPE",
                "severity": "low",
                "message": "Several calls have nearly identical token shapes; inspect for repeated planning, retries, or duplicated reviewer calls.",
                "evidence": {"repeated_shapes": repeated[:5]},
            }
        )

    return diagnoses


def _recommendations(diagnoses: list[dict[str, Any]]) -> list[dict[str, str]]:
    rule_ids = {str(item.get("rule_id")) for item in diagnoses}
    recommendations: list[dict[str, str]] = []
    if "CD-INPUT-HEAVY" in rule_ids:
        recommendations.append(
            {
                "action": "summarize_context_before_next_call",
                "why": "Input/context tokens dominate the trace.",
                "expected_effect": "Reduce prompt tokens by replacing old tool output with a compact evidence summary.",
            }
        )
    if "CD-LARGE-STEP" in rule_ids or "CD-LOW-OUTPUT" in rule_ids:
        recommendations.append(
            {
                "action": "narrow_tool_results_and_history",
                "why": "Large prompts are not always producing proportionally useful output.",
                "expected_effect": "Keep only task-relevant snippets, errors, and decisions in the next model call.",
            }
        )
    if "CD-COST-SPIKE" in rule_ids:
        recommendations.append(
            {
                "action": "route_or_cap_expensive_steps",
                "why": "One or two steps dominate cost.",
                "expected_effect": "Use a cheaper auxiliary model for review/summarization or cap completion tokens near budget.",
            }
        )
    if "CD-REPEATED-SHAPE" in rule_ids:
        recommendations.append(
            {
                "action": "inspect_loop_or_repeated_review",
                "why": "Repeated token shapes can indicate retries or duplicated agent roles.",
                "expected_effect": "Stop repeating the same action and force a final answer or a different next action.",
            }
        )
    if not recommendations:
        recommendations.append(
            {
                "action": "continue_monitoring",
                "why": "No obvious waste pattern was detected from token telemetry alone.",
                "expected_effect": "Keep collecting per-step cost so future traces can be compared.",
            }
        )
    return recommendations


def summarize_trace(
    entries: Iterable[dict[str, Any]],
    *,
    trace_id: Optional[str] = None,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    steps = normalize_cost_steps(entries)
    if trace_id is None:
        trace_ids = [step.trace_id for step in steps if step.trace_id]
        trace_id = trace_ids[0] if trace_ids else ""
    totals = {
        "steps": len(steps),
        "prompt_tokens": sum(step.prompt_tokens for step in steps),
        "completion_tokens": sum(step.completion_tokens for step in steps),
        "total_tokens": sum(step.total_tokens for step in steps),
        "reasoning_tokens": sum(step.reasoning_tokens for step in steps),
        "cached_tokens": sum(step.cached_tokens for step in steps),
        "input_cost_usd": sum(step.input_cost_usd for step in steps),
        "output_cost_usd": sum(step.output_cost_usd for step in steps),
        "total_cost_usd": sum(step.total_cost_usd for step in steps),
    }
    ratios = {
        "prompt_token_ratio": (
            totals["prompt_tokens"] / totals["total_tokens"]
            if totals["total_tokens"]
            else 0.0
        ),
        "completion_token_ratio": (
            totals["completion_tokens"] / totals["total_tokens"]
            if totals["total_tokens"]
            else 0.0
        ),
        "cache_token_ratio": (
            totals["cached_tokens"] / totals["prompt_tokens"]
            if totals["prompt_tokens"]
            else 0.0
        ),
    }
    summary: dict[str, Any] = {
        "trace_id": trace_id or "",
        "totals": totals,
        "ratios": {key: round(value, 4) for key, value in ratios.items()},
        "model_breakdown": _model_breakdown(steps),
        "top_steps_by_cost": _top_steps(steps, "total_cost_usd"),
        "top_steps_by_input_tokens": _top_steps(steps, "prompt_tokens"),
        "top_steps_by_total_tokens": _top_steps(steps, "total_tokens"),
        "_steps": steps,
    }
    diagnoses = _diagnose(summary, config or {})
    summary["diagnoses"] = diagnoses
    summary["repair_recommendations"] = _recommendations(diagnoses)
    summary.pop("_steps", None)
    return summary


def analyze_cost_log(
    path: Optional[str | Path] = None,
    *,
    trace_id: Optional[str] = None,
    limit: Optional[int] = None,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return summarize_trace(
        load_cost_entries(path, trace_id=trace_id, limit=limit),
        trace_id=trace_id,
        config=config,
    )


def _fmt_usd(value: Any) -> str:
    return f"${_to_float(value):.6f}"


def _fmt_ratio(value: Any) -> str:
    return f"{_to_float(value) * 100:.1f}%"


def build_markdown_report(summary: dict[str, Any]) -> str:
    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    ratios = summary.get("ratios") if isinstance(summary.get("ratios"), dict) else {}
    lines = [
        "# ArbiterOS Cost Doctor Report",
        "",
        f"- Trace: `{summary.get('trace_id') or 'unknown'}`",
        f"- LLM steps: {_to_int(totals.get('steps'))}",
        f"- Total tokens: {_to_int(totals.get('total_tokens'))}",
        f"- Prompt / completion: {_to_int(totals.get('prompt_tokens'))} / {_to_int(totals.get('completion_tokens'))}",
        f"- Cached prompt tokens: {_to_int(totals.get('cached_tokens'))} ({_fmt_ratio(ratios.get('cache_token_ratio'))})",
        f"- Estimated cost: {_fmt_usd(totals.get('total_cost_usd'))}",
        "",
        "## Top Steps",
        "",
        "| Step | Model | Prompt | Completion | Total | Cost |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    top_steps = summary.get("top_steps_by_cost")
    if not isinstance(top_steps, list) or not top_steps:
        top_steps = summary.get("top_steps_by_total_tokens")
    if isinstance(top_steps, list):
        for step in top_steps[:5]:
            if not isinstance(step, dict):
                continue
            lines.append(
                "| {step} | {model} | {prompt} | {completion} | {total} | {cost} |".format(
                    step=_to_int(step.get("step_index")),
                    model=str(step.get("model") or "unknown"),
                    prompt=_to_int(step.get("prompt_tokens")),
                    completion=_to_int(step.get("completion_tokens")),
                    total=_to_int(step.get("total_tokens")),
                    cost=_fmt_usd(step.get("total_cost_usd")),
                )
            )
    lines.extend(["", "## Diagnosis", ""])
    diagnoses = summary.get("diagnoses")
    if isinstance(diagnoses, list):
        for item in diagnoses:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('rule_id')}` ({item.get('severity')}): {item.get('message')}"
            )
    lines.extend(["", "## Repair Suggestions", ""])
    recommendations = summary.get("repair_recommendations")
    if isinstance(recommendations, list):
        for item in recommendations:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('action')}`: {item.get('why')} Expected effect: {item.get('expected_effect')}"
            )
    return "\n".join(lines).rstrip() + "\n"


def _main() -> int:
    parser = argparse.ArgumentParser(description="Analyze ArbiterOS cost telemetry.")
    parser.add_argument("--log", default=None, help="Path to cost_telemetry.jsonl")
    parser.add_argument("--trace-id", default=None, help="Trace id to analyze")
    parser.add_argument("--limit", type=int, default=None, help="Read only the last N log lines")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    args = parser.parse_args()

    summary = analyze_cost_log(args.log, trace_id=args.trace_id, limit=args.limit)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(build_markdown_report(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "CostStep",
    "analyze_cost_log",
    "build_markdown_report",
    "load_cost_entries",
    "normalize_cost_steps",
    "summarize_trace",
]
