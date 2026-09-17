"""Accumulate long-term tool-call habits per governance role.

One JSON file per role under ``log/habit/{role}.json``. Each trace replaces its
own previous contribution so instruction re-saves do not double-count.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

from arbiteros_kernel.policy_check import DEFAULT_ROLE_NAME

_CATEGORIES = ("READ", "WRITE", "EXEC")
_TOOL_KINDS = {"TOOLCALL", "TOOLRESULT"}
_PATTERN_TOP_K = 8
_KERNEL_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG_DIR = _KERNEL_ROOT / "log" / "habit"
_LITELLM_CONFIG = _KERNEL_ROOT / "litellm_config.yaml"
_ROLE_FILE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_LOCK = threading.Lock()


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _read_litellm_config() -> dict[str, Any]:
    if yaml is None or not _LITELLM_CONFIG.is_file():
        return {}
    try:
        parsed = yaml.safe_load(_LITELLM_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def role_habit_log_enabled() -> bool:
    return _as_bool(_read_litellm_config().get("role_habit_log_enabled"), True)


def normalize_role_name(role: Any) -> str:
    if not isinstance(role, str) or not role.strip():
        return DEFAULT_ROLE_NAME
    value = role.strip()
    if value.lower() == DEFAULT_ROLE_NAME:
        return DEFAULT_ROLE_NAME
    return value


def role_habit_filename(role: str) -> str:
    slug = _ROLE_FILE_RE.sub("_", normalize_role_name(role)).strip("._-")
    return f"{slug or DEFAULT_ROLE_NAME}.json"


def _empty_counts() -> dict[str, int]:
    return {name: 0 for name in _CATEGORIES}


def _norm_category(value: Any) -> str | None:
    name = str(value or "").strip().upper()
    return name if name in _CATEGORIES else None


def extract_tool_categories(instructions: Any) -> list[str]:
    """Ordered READ/WRITE/EXEC sequence for tool invocations in one trace."""
    if not isinstance(instructions, list):
        return []
    ranked: list[tuple[int, int, str, str]] = []
    for index, instr in enumerate(instructions):
        if not isinstance(instr, dict):
            continue
        category = _norm_category(instr.get("instruction_type"))
        if category is None:
            continue
        kind = str(instr.get("arbiteros_ref_kind") or "").strip().upper()
        if kind not in _TOOL_KINDS:
            continue
        content = instr.get("content")
        tool_id = ""
        if isinstance(content, dict):
            tool_id = str(content.get("tool_call_id") or "").strip()
        if not tool_id:
            tool_id = str(instr.get("id") or f"idx:{index}").strip()
        try:
            step = int(instr.get("runtime_step") or 0)
        except (TypeError, ValueError):
            step = 0
        ranked.append((step, index, tool_id, category))
    ranked.sort()
    seen: set[str] = set()
    out: list[str] = []
    for _step, _index, tool_id, category in ranked:
        if tool_id in seen:
            continue
        seen.add(tool_id)
        out.append(category)
    return out


def pattern_ngrams(sequence: list[str], *, sizes: tuple[int, ...] = (2, 3)) -> dict[str, int]:
    counts: dict[str, int] = {}
    for size in sizes:
        if size < 2:
            continue
        for start in range(0, len(sequence) - size + 1):
            key = "→".join(sequence[start : start + size])
            counts[key] = counts.get(key, 0) + 1
    return counts


def snapshot_trace(
    instructions: Any,
    *,
    tokens: int = 0,
    elapsed_seconds: float = 0.0,
    usd: float = 0.0,
) -> dict[str, Any]:
    sequence = extract_tool_categories(instructions)
    counts = _empty_counts()
    for category in sequence:
        counts[category] += 1
    return {
        "counts": counts,
        "patterns": pattern_ngrams(sequence),
        "tool_calls": len(sequence),
        "tokens": max(0, int(tokens or 0)),
        "elapsed_seconds": max(0.0, float(elapsed_seconds or 0.0)),
        "usd": max(0.0, float(usd or 0.0)),
    }


def _add_map(dst: dict[str, int], src: Any, *, sign: int = 1) -> None:
    if not isinstance(src, dict):
        return
    for key, value in src.items():
        name = str(key or "").strip()
        if not name:
            continue
        try:
            delta = int(value or 0) * sign
        except (TypeError, ValueError):
            continue
        dst[name] = int(dst.get(name, 0)) + delta
        if dst[name] <= 0:
            if name in _CATEGORIES:
                dst[name] = 0
            else:
                dst.pop(name, None)


def _empty_record(role: str) -> dict[str, Any]:
    return {
        "role": normalize_role_name(role),
        "updated_at": datetime.now().isoformat(),
        "trace_count": 0,
        "total_tool_calls": 0,
        "counts": _empty_counts(),
        "mix": {name: 0.0 for name in _CATEGORIES},
        "frequency": {
            "calls_per_hour": 0.0,
            "avg_calls_per_trace": 0.0,
        },
        "patterns": {},
        "patterns_top": [],
        "cost": {
            "tokens": 0,
            "elapsed_seconds": 0.0,
            "usd": 0.0,
            "tokens_per_second": None,
            "usd_per_hour": None,
        },
        "window": {"first_ts": None, "last_ts": None},
        "traces": {},
    }


def _finalize(record: dict[str, Any]) -> dict[str, Any]:
    counts = record.get("counts")
    if not isinstance(counts, dict):
        counts = _empty_counts()
    total = max(0, int(record.get("total_tool_calls") or 0))
    mix = {}
    for name in _CATEGORIES:
        value = max(0, int(counts.get(name) or 0))
        counts[name] = value
        mix[name] = (value / total) if total else 0.0
    record["counts"] = counts
    record["mix"] = mix

    trace_count = max(0, int(record.get("trace_count") or 0))
    cost = record.get("cost") if isinstance(record.get("cost"), dict) else {}
    elapsed = max(0.0, float(cost.get("elapsed_seconds") or 0.0))
    tokens = max(0, int(cost.get("tokens") or 0))
    usd = max(0.0, float(cost.get("usd") or 0.0))
    window = record.get("window") if isinstance(record.get("window"), dict) else {}
    first_ts = window.get("first_ts")
    last_ts = window.get("last_ts")
    window_hours = 0.0
    if isinstance(first_ts, str) and isinstance(last_ts, str) and first_ts and last_ts:
        try:
            window_hours = max(
                0.0,
                (
                    datetime.fromisoformat(last_ts) - datetime.fromisoformat(first_ts)
                ).total_seconds()
                / 3600.0,
            )
        except Exception:
            window_hours = 0.0
    if window_hours <= 0 and elapsed > 0:
        window_hours = elapsed / 3600.0
    record["frequency"] = {
        "calls_per_hour": (total / window_hours) if window_hours > 0 else 0.0,
        "avg_calls_per_trace": (total / trace_count) if trace_count else 0.0,
    }
    record["cost"] = {
        "tokens": tokens,
        "elapsed_seconds": elapsed,
        "usd": usd,
        "tokens_per_second": (tokens / elapsed) if elapsed > 0 else None,
        "usd_per_hour": (usd / (elapsed / 3600.0)) if elapsed > 0 else None,
    }
    patterns = record.get("patterns")
    if not isinstance(patterns, dict):
        patterns = {}
    cleaned = {str(k): int(v) for k, v in patterns.items() if int(v or 0) > 0}
    record["patterns"] = cleaned
    ranked = sorted(cleaned.items(), key=lambda item: (-item[1], item[0]))
    record["patterns_top"] = [
        {"pattern": name, "count": count} for name, count in ranked[:_PATTERN_TOP_K]
    ]
    record["updated_at"] = datetime.now().isoformat()
    return record


def _apply_snapshot(record: dict[str, Any], snap: dict[str, Any], *, sign: int) -> None:
    _add_map(record["counts"], snap.get("counts"), sign=sign)
    patterns = record.get("patterns")
    if not isinstance(patterns, dict):
        patterns = {}
        record["patterns"] = patterns
    _add_map(patterns, snap.get("patterns"), sign=sign)
    record["total_tool_calls"] = max(
        0, int(record.get("total_tool_calls") or 0) + sign * int(snap.get("tool_calls") or 0)
    )
    cost = record.get("cost") if isinstance(record.get("cost"), dict) else {}
    cost["tokens"] = max(0, int(cost.get("tokens") or 0) + sign * int(snap.get("tokens") or 0))
    cost["elapsed_seconds"] = max(
        0.0,
        float(cost.get("elapsed_seconds") or 0.0)
        + sign * float(snap.get("elapsed_seconds") or 0.0),
    )
    cost["usd"] = max(0.0, float(cost.get("usd") or 0.0) + sign * float(snap.get("usd") or 0.0))
    record["cost"] = cost


def accumulate_role_habit(
    role: Any,
    *,
    trace_id: str,
    instructions: Any,
    tokens: int = 0,
    elapsed_seconds: float = 0.0,
    usd: float = 0.0,
    log_dir: Path | None = None,
    enabled: bool | None = None,
) -> dict[str, Any] | None:
    if enabled is None:
        enabled = role_habit_log_enabled()
    if not enabled:
        return None
    if not isinstance(trace_id, str) or not trace_id.strip():
        return None

    role_name = normalize_role_name(role)
    snap = snapshot_trace(
        instructions,
        tokens=tokens,
        elapsed_seconds=elapsed_seconds,
        usd=usd,
    )
    path = (log_dir or _DEFAULT_LOG_DIR) / role_habit_filename(role_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat()

    with _LOCK:
        record: dict[str, Any]
        if path.is_file():
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                parsed = None
            record = parsed if isinstance(parsed, dict) else _empty_record(role_name)
        else:
            record = _empty_record(role_name)
        if not isinstance(record.get("counts"), dict):
            record["counts"] = _empty_counts()
        record["role"] = role_name
        traces = record.get("traces")
        if not isinstance(traces, dict):
            traces = {}
        tid = trace_id.strip()
        previous = traces.get(tid)
        if isinstance(previous, dict):
            _apply_snapshot(record, previous, sign=-1)
        else:
            record["trace_count"] = int(record.get("trace_count") or 0) + 1
        _apply_snapshot(record, snap, sign=1)
        traces[tid] = snap
        record["traces"] = traces
        window = record.get("window") if isinstance(record.get("window"), dict) else {}
        if not window.get("first_ts"):
            window["first_ts"] = now
        window["last_ts"] = now
        record["window"] = window
        record = _finalize(record)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    return record
