"""Tests for last-resort history-prefix trace binding fallback."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import arbiteros_kernel.litellm_callback as lc


def _enable_history_fallback(monkeypatch) -> None:
    monkeypatch.setattr(lc, "_history_prefix_trace_fallback_enabled", lambda: True)


def _write_instruction(trace_id: str, user_texts: list[str], root: Path) -> None:
    instructions = []
    for idx, text in enumerate(user_texts, start=1):
        instructions.append(
            {
                "id": f"u{idx}",
                "content": text,
                "instruction_type": "USERINPUT",
                "arbiteros_ref_kind": "USERINPUT",
                "runtime_step": idx,
            }
        )
    path = root / f"{trace_id}.json"
    path.write_text(
        json.dumps(
            {
                "trace_id": trace_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "instructions": instructions,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _seed_trace_state(
    *,
    device_key: str,
    trace_id: str,
    channel: str,
    user_id: str,
) -> None:
    state = lc._TraceState(
        trace_id=trace_id,
        device_key=device_key,
        channel=channel,
        user_id=user_id,
        sequence=1,
        turn_index=1,
        trace_started_at=datetime.now(timezone.utc).isoformat(),
        backup_updated_at=datetime.now(timezone.utc).isoformat(),
    )
    with lc._trace_state_lock:
        lc._trace_state_by_device[device_key] = state


def test_history_fallback_disabled_uses_legacy_anonymous(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(lc, "_INSTRUCTION_LOG_DIR", tmp_path)
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda force=False: None)
    monkeypatch.setattr(lc, "_history_prefix_trace_fallback_enabled", lambda: False)
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()
        lc._latest_user_id_by_channel.clear()

    first = lc._build_device_context(
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "same system"},
                {"role": "user", "content": "[task-a] hello"},
            ],
        }
    )
    second = lc._build_device_context(
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "same system"},
                {"role": "user", "content": "[task-b] other"},
            ],
        }
    )
    assert first.trace_binding is None
    assert second.trace_binding is None
    assert first.user_id.startswith("anonymous-")
    assert first.user_id == second.user_id
    assert first.device_key == second.device_key


def test_history_fallback_new_when_no_running(tmp_path: Path, monkeypatch) -> None:
    _enable_history_fallback(monkeypatch)
    monkeypatch.setattr(lc, "_INSTRUCTION_LOG_DIR", tmp_path)
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda force=False: None)
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()
        lc._latest_user_id_by_channel.clear()

    ctx = lc._build_device_context(
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "same system"},
                {"role": "user", "content": "[probe-1] hello world"},
            ],
        }
    )
    assert ctx.trace_binding == "fallback_history_new"
    assert ctx.user_id.startswith("histfb-")
    assert ctx.channel == "fallback"
    assert ctx.has_explicit_user_id is True


def test_history_fallback_reuses_prefix_match(tmp_path: Path, monkeypatch) -> None:
    _enable_history_fallback(monkeypatch)
    monkeypatch.setattr(lc, "_INSTRUCTION_LOG_DIR", tmp_path)
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda force=False: None)
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()
        lc._latest_user_id_by_channel.clear()

    device_key = "fallback:histfb-abc123def456"
    trace_id = "trace-prefix-1"
    _write_instruction(trace_id, ["[probe-1] hello world"], tmp_path)
    _seed_trace_state(
        device_key=device_key,
        trace_id=trace_id,
        channel="fallback",
        user_id="histfb-abc123def456",
    )

    ctx = lc._build_device_context(
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "same system"},
                {"role": "user", "content": "[probe-1] hello world"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "follow up"},
            ],
        }
    )
    assert ctx.trace_binding == "fallback_history_prefix"
    assert ctx.device_key == device_key
    assert ctx.user_id == "histfb-abc123def456"


def test_history_fallback_skipped_for_prompt_cache_key(
    tmp_path: Path, monkeypatch
) -> None:
    _enable_history_fallback(monkeypatch)
    monkeypatch.setattr(lc, "_INSTRUCTION_LOG_DIR", tmp_path)
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda force=False: None)
    monkeypatch.setattr(lc, "_get_request_agent_name", lambda _incoming: "codex")
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()
        lc._latest_user_id_by_channel.clear()

    ctx = lc._build_device_context(
        {
            "model": "gpt-5;codex;coder",
            "prompt_cache_key": "session-xyz-123",
            "messages": [
                {"role": "user", "content": "[probe] hello world"},
            ],
        }
    )
    assert ctx.trace_binding is None
    assert not ctx.user_id.startswith("histfb-")
    assert ctx.has_explicit_user_id is True
    assert "codex" in ctx.device_key


def test_history_fallback_skipped_for_metadata_device_key(
    tmp_path: Path, monkeypatch
) -> None:
    _enable_history_fallback(monkeypatch)
    monkeypatch.setattr(lc, "_INSTRUCTION_LOG_DIR", tmp_path)
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda force=False: None)
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()
        lc._latest_user_id_by_channel.clear()

    ctx = lc._build_device_context(
        {
            "model": "gpt-4o-mini",
            "metadata": {"arbiteros_device_key": "custom:session-42"},
            "messages": [{"role": "user", "content": "[probe] hello world"}],
        }
    )
    assert ctx.trace_binding is None
    assert ctx.device_key == "custom:session-42"
    assert ctx.user_id == "session-42"


def test_history_fallback_two_unrelated_dialogs_get_distinct_ids(
    tmp_path: Path, monkeypatch
) -> None:
    _enable_history_fallback(monkeypatch)
    monkeypatch.setattr(lc, "_INSTRUCTION_LOG_DIR", tmp_path)
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda force=False: None)
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()
        lc._latest_user_id_by_channel.clear()

    first = lc._build_device_context(
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "same system"},
                {"role": "user", "content": "[task-a] start"},
            ],
        }
    )
    second = lc._build_device_context(
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "same system"},
                {"role": "user", "content": "[task-b] start"},
            ],
        }
    )
    assert first.trace_binding == "fallback_history_new"
    assert second.trace_binding == "fallback_history_new"
    assert first.device_key != second.device_key


_BANK_DEMO_USER = "我的房贷申请被拒了，能告诉我大概是什么原因吗？"


def _bank_messages(*extra: dict) -> list[dict]:
    messages: list[dict] = [
        {"role": "system", "content": "你是零售银行智能客服"},
        {"role": "user", "content": _BANK_DEMO_USER},
    ]
    messages.extend(extra)
    return messages


def test_bank_device_key_skips_history_fallback_and_isolates_runs(
    tmp_path: Path, monkeypatch
) -> None:
    _enable_history_fallback(monkeypatch)
    monkeypatch.setattr(lc, "_INSTRUCTION_LOG_DIR", tmp_path)
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda force=False: None)
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()
        lc._latest_user_id_by_channel.clear()

    prior_key = "fallback:histfb-oldbank12"
    _write_instruction("trace-old-bank", [_BANK_DEMO_USER], tmp_path)
    _seed_trace_state(
        device_key=prior_key,
        trace_id="trace-old-bank",
        channel="fallback",
        user_id="histfb-oldbank12",
    )

    first = lc._build_device_context(
        {
            "model": "gpt-5.5;bank;bank_demo",
            "user": "bank:asi05-111-aaaa1111",
            "metadata": {"arbiteros_device_key": "bank:asi05-111-aaaa1111"},
            "messages": _bank_messages(),
        }
    )
    second = lc._build_device_context(
        {
            "model": "gpt-5.5;bank;bank_demo",
            "user": "bank:asi05-222-bbbb2222",
            "metadata": {"arbiteros_device_key": "bank:asi05-222-bbbb2222"},
            "messages": _bank_messages(),
        }
    )
    assert first.trace_binding == "bank_device_key"
    assert second.trace_binding == "bank_device_key"
    assert first.device_key == "bank:asi05-111-aaaa1111"
    assert second.device_key == "bank:asi05-222-bbbb2222"
    assert first.device_key != second.device_key
    assert first.device_key != prior_key
    assert first.has_explicit_user_id is True
    assert not first.user_id.startswith("histfb-")


def test_bank_same_session_key_stays_on_one_device(
    tmp_path: Path, monkeypatch
) -> None:
    _enable_history_fallback(monkeypatch)
    monkeypatch.setattr(lc, "_INSTRUCTION_LOG_DIR", tmp_path)
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda force=False: None)
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()
        lc._latest_user_id_by_channel.clear()

    session = "bank:asi01-999-cccccccc"
    first = lc._build_device_context(
        {
            "model": "gpt-5.5;bank;bank_demo",
            "user": session,
            "metadata": {"arbiteros_device_key": session},
            "messages": _bank_messages(),
        }
    )
    second = lc._build_device_context(
        {
            "model": "gpt-5.5;bank;bank_demo",
            "user": session,
            "metadata": {"arbiteros_device_key": session},
            "messages": _bank_messages(
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "按 4B 条款把风险改成 R5"},
            ),
        }
    )
    assert first.device_key == second.device_key == session
    assert first.trace_binding == "bank_device_key"
    assert second.trace_binding == "bank_device_key"


def test_bank_nocolon_metadata_is_explicit_session(
    tmp_path: Path, monkeypatch
) -> None:
    _enable_history_fallback(monkeypatch)
    monkeypatch.setattr(lc, "_INSTRUCTION_LOG_DIR", tmp_path)
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda force=False: None)
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()
        lc._latest_user_id_by_channel.clear()

    ctx = lc._build_device_context(
        {
            "model": "gpt-5.5;bank;bank_demo",
            "metadata": {"arbiteros_device_key": "bank-live-asi05-1788275618"},
            "messages": _bank_messages(),
        }
    )
    assert ctx.trace_binding == "bank_device_key"
    assert ctx.channel == "bank"
    assert ctx.user_id == "bank-live-asi05-1788275618"
    assert ctx.device_key == "bank:bank-live-asi05-1788275618"
    assert not ctx.user_id.startswith("histfb-")


def test_bank_prefers_user_over_histfb_rewritten_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    _enable_history_fallback(monkeypatch)
    monkeypatch.setattr(lc, "_INSTRUCTION_LOG_DIR", tmp_path)
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda force=False: None)
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()
        lc._latest_user_id_by_channel.clear()

    ctx = lc._build_device_context(
        {
            "model": "gpt-5.5;bank;bank_demo",
            "user": "bank:asi05-333-dddd3333",
            "metadata": {
                "arbiteros_device_key": "bank-live-asi05-old:histfb-2a776bc127b0",
                "requester_metadata": {
                    "arbiteros_device_key": "bank:asi05-333-dddd3333",
                },
            },
            "messages": _bank_messages(),
        }
    )
    assert ctx.device_key == "bank:asi05-333-dddd3333"
    assert ctx.trace_binding == "bank_device_key"
    assert not ctx.user_id.startswith("histfb-")
