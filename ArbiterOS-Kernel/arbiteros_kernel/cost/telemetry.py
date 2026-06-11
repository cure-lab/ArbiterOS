from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from arbiteros_kernel.policy_runtime import get_runtime

_KERNEL_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LOG_FILE = _KERNEL_ROOT / "log" / "cost_telemetry.jsonl"
_LOG_LOCK = threading.Lock()
_TRACE_TOTALS_LOCK = threading.Lock()
_TRACE_TOTALS: dict[str, dict[str, Any]] = {}


def _to_json(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Exception):
        return {"_type": "Exception", "name": type(obj).__name__, "msg": str(obj)}
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    if isinstance(obj, dict):
        return {k: _to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json(v) for v in obj]
    return str(obj)


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return 0


def _non_negative_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if v >= 0 else None
    if isinstance(value, str) and value.strip():
        try:
            v = float(value.strip())
        except ValueError:
            return None
        return v if v >= 0 else None
    return None


def _first_non_negative_int(*values: Any) -> int:
    for value in values:
        parsed = _non_negative_int(value)
        if parsed > 0:
            return parsed
    return 0


def _nested_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    return value if isinstance(value, dict) else {}


def extract_token_usage_from_response_obj(response_obj: Any) -> dict[str, int]:
    payload = _to_json(response_obj)
    if not isinstance(payload, dict):
        return _empty_usage()
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    prompt_details = _nested_dict(usage, "prompt_tokens_details")
    completion_details = _nested_dict(usage, "completion_tokens_details")
    input_details = _nested_dict(usage, "input_tokens_details")
    output_details = _nested_dict(usage, "output_tokens_details")

    prompt_tokens = _first_non_negative_int(
        usage.get("prompt_tokens"),
        usage.get("input_tokens"),
    )
    completion_tokens = _first_non_negative_int(
        usage.get("completion_tokens"),
        usage.get("output_tokens"),
    )
    total_tokens = _first_non_negative_int(usage.get("total_tokens"))
    if total_tokens <= 0 and (prompt_tokens > 0 or completion_tokens > 0):
        total_tokens = prompt_tokens + completion_tokens

    reasoning_tokens = _first_non_negative_int(
        completion_details.get("reasoning_tokens"),
        output_details.get("reasoning_tokens"),
        usage.get("reasoning_tokens"),
    )
    cached_tokens = _first_non_negative_int(
        prompt_details.get("cached_tokens"),
        input_details.get("cached_tokens"),
        usage.get("cached_input_tokens"),
        usage.get("cache_read_input_tokens"),
    )

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cached_tokens": cached_tokens,
    }


def _empty_usage() -> dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
    }


def _cfg() -> dict[str, Any]:
    try:
        runtime_cfg = get_runtime().cfg
    except Exception:
        runtime_cfg = {}
    if not isinstance(runtime_cfg, dict):
        return {}
    block = runtime_cfg.get("cost_telemetry")
    return block if isinstance(block, dict) else {}


def _enabled() -> bool:
    return bool(_cfg().get("enabled", True))


def _log_path() -> Path:
    env_path = os.getenv("ARBITEROS_COST_TELEMETRY_LOG_PATH", "").strip()
    if env_path:
        return Path(env_path).expanduser()
    raw_path = _cfg().get("log_path")
    if isinstance(raw_path, str) and raw_path.strip():
        p = Path(raw_path.strip()).expanduser()
        if p.is_absolute():
            return p
        return _KERNEL_ROOT / p
    return _DEFAULT_LOG_FILE


def _normalize_pricing_model_name(model: Any) -> str:
    if not isinstance(model, str):
        return ""
    value = model.strip().lower()
    if not value:
        return ""
    if ";" in value:
        value = value.split(";", 1)[0].strip()
    if "/" in value:
        value = value.rsplit("/", 1)[-1].strip()
    return value


def _pricing_rate(raw: Any, *keys: str) -> Optional[float]:
    if isinstance(raw, (int, float, str)):
        return _non_negative_float(raw)
    if not isinstance(raw, dict):
        return None
    for key in keys:
        parsed = _non_negative_float(raw.get(key))
        if parsed is not None:
            return parsed
    return None


def _find_model_pricing(
    *, request_model: Any, response_model: Any
) -> tuple[Optional[str], dict[str, Any]]:
    cfg = _cfg()
    prices = cfg.get("model_prices_usd_per_million_tokens")
    if not isinstance(prices, dict):
        prices = cfg.get("model_prices")
    if not isinstance(prices, dict):
        return None, {}

    candidates = []
    for model in (response_model, request_model):
        normalized = _normalize_pricing_model_name(model)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
        if isinstance(model, str):
            raw = model.strip().lower()
            if raw and raw not in candidates:
                candidates.append(raw)

    normalized_price_map = {
        _normalize_pricing_model_name(key): value
        for key, value in prices.items()
        if isinstance(key, str)
    }
    raw_price_map = {
        key.strip().lower(): value
        for key, value in prices.items()
        if isinstance(key, str)
    }
    for candidate in candidates:
        raw = raw_price_map.get(candidate)
        if raw is None:
            raw = normalized_price_map.get(candidate)
        if isinstance(raw, dict):
            return candidate, raw
    return None, {}


def estimate_llm_cost_usd(
    request_data: dict[str, Any], response_obj: Any, usage: dict[str, int]
) -> dict[str, Any]:
    payload = _to_json(response_obj)
    response_model = payload.get("model") if isinstance(payload, dict) else None
    request_model = (
        request_data.get("model") if isinstance(request_data, dict) else None
    )

    pricing_model, model_pricing = _find_model_pricing(
        request_model=request_model,
        response_model=response_model,
    )
    cfg = _cfg()

    input_rate = _pricing_rate(
        model_pricing,
        "input",
        "prompt",
        "input_usd_per_million_tokens",
        "prompt_usd_per_million_tokens",
    )
    output_rate = _pricing_rate(
        model_pricing,
        "output",
        "completion",
        "output_usd_per_million_tokens",
        "completion_usd_per_million_tokens",
    )
    cached_input_rate = _pricing_rate(
        model_pricing,
        "cached_input",
        "cached_prompt",
        "cached_input_usd_per_million_tokens",
    )
    if input_rate is None:
        input_rate = _pricing_rate(
            cfg,
            "default_input_usd_per_million_tokens",
            "default_prompt_usd_per_million_tokens",
        )
    if output_rate is None:
        output_rate = _pricing_rate(
            cfg,
            "default_output_usd_per_million_tokens",
            "default_completion_usd_per_million_tokens",
        )
    if cached_input_rate is None:
        cached_input_rate = input_rate

    priced = input_rate is not None or output_rate is not None
    input_rate = input_rate or 0.0
    output_rate = output_rate or 0.0
    cached_input_rate = cached_input_rate or 0.0

    prompt_tokens = max(0, int(usage.get("prompt_tokens", 0) or 0))
    completion_tokens = max(0, int(usage.get("completion_tokens", 0) or 0))
    cached_tokens = min(
        prompt_tokens,
        max(0, int(usage.get("cached_tokens", 0) or 0)),
    )
    uncached_prompt_tokens = max(0, prompt_tokens - cached_tokens)

    input_cost = (
        (uncached_prompt_tokens * input_rate) + (cached_tokens * cached_input_rate)
    ) / 1_000_000
    output_cost = completion_tokens * output_rate / 1_000_000
    total_cost = input_cost + output_cost

    return {
        "priced": priced,
        "currency": str(cfg.get("currency") or "USD"),
        "pricing_model": pricing_model,
        "request_model": request_model,
        "response_model": response_model,
        "input_usd_per_million_tokens": input_rate,
        "cached_input_usd_per_million_tokens": cached_input_rate,
        "output_usd_per_million_tokens": output_rate,
        "input_cost_usd": input_cost,
        "output_cost_usd": output_cost,
        "total_cost_usd": total_cost,
    }


def _empty_trace_totals() -> dict[str, Any]:
    return {
        "trace_started_at": None,
        "trace_total_tokens": 0,
        "trace_prompt_tokens": 0,
        "trace_completion_tokens": 0,
        "trace_reasoning_tokens": 0,
        "trace_cached_tokens": 0,
        "trace_cost_usd": 0.0,
    }


def get_trace_totals(trace_id: Optional[str]) -> dict[str, Any]:
    if not isinstance(trace_id, str) or not trace_id.strip():
        return _empty_trace_totals()
    tid = trace_id.strip()
    with _TRACE_TOTALS_LOCK:
        current = _TRACE_TOTALS.get(tid)
        return dict(current) if isinstance(current, dict) else _empty_trace_totals()


def _accumulate_trace_totals(
    trace_id: Optional[str], usage: dict[str, int], total_cost_usd: float
) -> dict[str, Any]:
    if not isinstance(trace_id, str) or not trace_id.strip():
        return _empty_trace_totals()
    total_delta = max(0, int(usage.get("total_tokens", 0) or 0))
    prompt_delta = max(0, int(usage.get("prompt_tokens", 0) or 0))
    completion_delta = max(0, int(usage.get("completion_tokens", 0) or 0))
    reasoning_delta = max(0, int(usage.get("reasoning_tokens", 0) or 0))
    cached_delta = max(0, int(usage.get("cached_tokens", 0) or 0))
    cost_delta = _non_negative_float(total_cost_usd) or 0.0
    if total_delta <= 0 and (prompt_delta > 0 or completion_delta > 0):
        total_delta = prompt_delta + completion_delta
    if total_delta <= 0 and cost_delta <= 0:
        return get_trace_totals(trace_id)

    tid = trace_id.strip()
    now = datetime.now().isoformat()
    with _TRACE_TOTALS_LOCK:
        current = dict(_TRACE_TOTALS.get(tid) or _empty_trace_totals())
        if not current.get("trace_started_at"):
            current["trace_started_at"] = now
        current["trace_total_tokens"] = (
            int(current.get("trace_total_tokens", 0) or 0) + total_delta
        )
        current["trace_prompt_tokens"] = (
            int(current.get("trace_prompt_tokens", 0) or 0) + prompt_delta
        )
        current["trace_completion_tokens"] = (
            int(current.get("trace_completion_tokens", 0) or 0) + completion_delta
        )
        current["trace_reasoning_tokens"] = (
            int(current.get("trace_reasoning_tokens", 0) or 0) + reasoning_delta
        )
        current["trace_cached_tokens"] = (
            int(current.get("trace_cached_tokens", 0) or 0) + cached_delta
        )
        current["trace_cost_usd"] = (
            float(current.get("trace_cost_usd", 0.0) or 0.0) + cost_delta
        )
        _TRACE_TOTALS[tid] = current
        return dict(current)


def _write_entry(entry: dict[str, Any]) -> None:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(_to_json(entry), ensure_ascii=False) + "\n")
    except Exception:
        return


def record_llm_call(
    *,
    trace_id: Optional[str],
    request_data: dict[str, Any],
    response_obj: Any,
) -> dict[str, Any]:
    usage = extract_token_usage_from_response_obj(response_obj)
    cost = estimate_llm_cost_usd(
        request_data if isinstance(request_data, dict) else {},
        response_obj,
        usage,
    )
    if not _enabled():
        return {
            "usage": usage,
            "cost": cost,
            "trace_totals": get_trace_totals(trace_id),
        }

    trace_totals = _accumulate_trace_totals(
        trace_id,
        usage,
        float(cost.get("total_cost_usd", 0.0) or 0.0),
    )
    if not isinstance(trace_id, str) or not trace_id.strip():
        return {"usage": usage, "cost": cost, "trace_totals": trace_totals}
    if not any(
        int(usage.get(k, 0) or 0) > 0
        for k in ("total_tokens", "prompt_tokens", "completion_tokens")
    ):
        return {"usage": usage, "cost": cost, "trace_totals": trace_totals}

    _write_entry(
        {
            "ts": datetime.now().isoformat(),
            "trace_id": trace_id.strip(),
            "request_model": cost.get("request_model"),
            "response_model": cost.get("response_model"),
            "pricing_model": cost.get("pricing_model"),
            "usage": {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
                "reasoning_tokens": int(usage.get("reasoning_tokens", 0) or 0),
                "cached_tokens": int(usage.get("cached_tokens", 0) or 0),
            },
            "estimated_cost": {
                "priced": bool(cost.get("priced", False)),
                "currency": cost.get("currency", "USD"),
                "input_cost_usd": cost.get("input_cost_usd", 0.0),
                "output_cost_usd": cost.get("output_cost_usd", 0.0),
                "total_cost_usd": cost.get("total_cost_usd", 0.0),
            },
            "trace_totals": trace_totals,
        }
    )
    return {"usage": usage, "cost": cost, "trace_totals": trace_totals}
