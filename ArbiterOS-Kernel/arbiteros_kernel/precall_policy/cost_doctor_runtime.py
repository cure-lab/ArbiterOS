"""Shared runtime cache and config for Cost Doctor precall."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from flow_cost_doctor.runtime.policy import (
    bootstrap_live_policy_from_rule_engine,
    load_policy_document,
    load_rule_engine,
    offline_session_steps,
)
from flow_cost_doctor.runtime.state import PrecallState

_lock = threading.Lock()
_state_by_trace: dict[str, PrecallState] = {}
_rule_engine_cache: dict[str, Any] | None = None
_rule_engine_mtime: float | None = None
_rule_engine_metadata: dict[str, Any] = {}
_validation_doc_cache: dict[str, Any] | None = None
_validation_mtime: float | None = None
_live_policy_by_trace: dict[str, dict[str, Any]] = {}


def _env_path(name: str) -> Path | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def rule_engine_config_path() -> Path | None:
    """Primary live config: deployment-default rule engine template."""
    return _env_path("ARBITEROS_COST_DOWN_RULE_ENGINE")


def validation_policy_path() -> Path | None:
    """Optional offline instance policy for replay validation only."""
    return _env_path("ARBITEROS_COST_DOWN_VALIDATION_POLICY")


def legacy_policy_path() -> Path | None:
    """Deprecated alias for rule engine path (backward compatibility)."""
    return _env_path("ARBITEROS_COST_DOWN_POLICY")


def cost_down_enabled() -> bool:
    return (
        rule_engine_config_path() is not None
        or legacy_policy_path() is not None
    )


def cost_down_phase() -> str:
    phase = os.getenv("ARBITEROS_COST_DOWN_PHASE", "A").strip().upper()
    return phase if phase in {"A", "B", "C", "D"} else "A"


def live_policy_dir() -> Path:
    raw = os.getenv("ARBITEROS_COST_DOWN_LIVE_POLICY_DIR", "log/cost_down").strip()
    return Path(raw).expanduser()


def decisions_log_path(trace_id: str) -> Path:
    return live_policy_dir() / trace_id / "precall_decisions.jsonl"


def live_policy_path(trace_id: str) -> Path:
    return live_policy_dir() / trace_id / "policy.json"


def _resolve_rule_engine_path() -> Path | None:
    path = rule_engine_config_path()
    if path is not None:
        return path
    return legacy_policy_path()


def _load_rule_engine_cached() -> dict[str, Any]:
    global _rule_engine_cache, _rule_engine_mtime, _rule_engine_metadata
    path = _resolve_rule_engine_path()
    if path is None or not path.exists():
        from flow_cost_doctor.cost_down import build_rule_engine_config

        _rule_engine_cache = build_rule_engine_config()
        _rule_engine_mtime = None
        _rule_engine_metadata = {}
        return _rule_engine_cache

    mtime = path.stat().st_mtime
    if _rule_engine_cache is not None and _rule_engine_mtime == mtime:
        return _rule_engine_cache

    doc = load_policy_document(path)
    _rule_engine_cache = load_rule_engine(doc)
    _rule_engine_metadata = dict(doc.get("metadata") or {})
    _rule_engine_mtime = mtime
    return _rule_engine_cache


def _load_validation_document() -> dict[str, Any] | None:
    global _validation_doc_cache, _validation_mtime
    path = validation_policy_path()
    if path is None or not path.exists():
        _validation_doc_cache = None
        _validation_mtime = None
        return None
    mtime = path.stat().st_mtime
    if _validation_doc_cache is not None and _validation_mtime == mtime:
        return _validation_doc_cache
    doc = load_policy_document(path)
    _validation_doc_cache = doc
    _validation_mtime = mtime
    return doc


def get_rule_engine() -> dict[str, Any]:
    return dict(_load_rule_engine_cached())


def get_rule_engine_metadata() -> dict[str, Any]:
    _load_rule_engine_cached()
    return dict(_rule_engine_metadata)


def get_offline_steps() -> list[dict[str, Any]]:
    """Optional offline session steps for replay validation only (not applied live)."""
    doc = _load_validation_document()
    if doc is None:
        return []
    return offline_session_steps(doc)


def get_or_create_state(trace_id: str) -> PrecallState:
    with _lock:
        state = _state_by_trace.get(trace_id)
        if state is None:
            state = PrecallState(trace_id=trace_id)
            from flow_cost_doctor.runtime.weights import attribution_params_from_rule_engine

            engine = _load_rule_engine_cached()
            state.apply_attribution_params(attribution_params_from_rule_engine(engine))
            state.apply_attribution_mode(engine.get("attribution_mode"))
            state.apply_decision_params(engine)
            _state_by_trace[trace_id] = state
        return state


def get_or_create_live_policy(trace_id: str) -> dict[str, Any]:
    with _lock:
        existing = _live_policy_by_trace.get(trace_id)
        if existing is not None:
            return existing
        live = bootstrap_live_policy_from_rule_engine(
            get_rule_engine(),
            trace_id=trace_id,
            metadata=get_rule_engine_metadata(),
        )
        _live_policy_by_trace[trace_id] = live
        return live


def append_decision_log(trace_id: str, record: dict[str, Any]) -> None:
    path = decisions_log_path(trace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False))
        handle.write("\n")


def persist_live_policy(trace_id: str, doc: dict[str, Any]) -> None:
    from flow_cost_doctor.runtime.policy import write_live_policy

    path = live_policy_path(trace_id)
    write_live_policy(path, doc)
    with _lock:
        _live_policy_by_trace[trace_id] = doc


def record_post_call_attribution(
    trace_id: str,
    instructions: list[dict[str, Any]] | None = None,
) -> None:
    """Update cumulative metrics after a successful LLM call (causal depends_on)."""
    from flow_cost_doctor.runtime.depends_on import finalize_and_record_post_call_attribution

    state = _state_by_trace.get(trace_id)
    if state is None or not state.pending_prompt_items:
        return
    if instructions is None:
        instructions = []
    finalize_and_record_post_call_attribution(state, instructions)
