"""Said/Done session index + PreToolUse matcher (no LLM)."""

from __future__ import annotations

import json
from pathlib import Path

import arbiteros_kernel.execution_check as execution_check
import arbiteros_kernel.session_index as session_index


def _isolate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(
        "ARBITEROS_SESSION_INDEX_FILE", str(tmp_path / "session_index.json")
    )
    monkeypatch.setenv(
        "ARBITEROS_SAID_DONE_PENDING_FILE", str(tmp_path / "pending_actions.json")
    )
    monkeypatch.setenv("ARBITEROS_SAID_DONE_HOOK_ENABLED", "1")
    session_index._INDEX_PATH = None
    execution_check._PENDING_PATH = None
    execution_check._CONFIG_CACHE_MTIME_NS = None
    execution_check._CONFIG_CACHE_ENABLED = None


def test_session_index_scheme_b_resolves_session_and_tool_call(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    session_index.register_binding(
        trace_id="trace-aaa",
        device_key="codex:codex-pck-abc",
        prompt_cache_key="019f6a00-0f1d-7cb1-b1fc-aa800ed40503",
        session_id="019f6a00-0f1d-7cb1-b1fc-aa800ed40503",
        channel="codex",
    )
    session_index.register_tool_call_id(
        tool_call_id="call_RQUiakR5SeWEBMzjFhprV78E",
        trace_id="trace-aaa",
    )

    assert (
        session_index.resolve_trace_id(
            session_id="019f6a00-0f1d-7cb1-b1fc-aa800ed40503"
        )
        == "trace-aaa"
    )
    assert (
        session_index.resolve_trace_id(
            tool_call_id="call_RQUiakR5SeWEBMzjFhprV78E"
        )
        == "trace-aaa"
    )


def test_pretool_allow_by_tool_call_id_consume_once(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    session_index.register_binding(
        trace_id="trace-bbb",
        prompt_cache_key="sess-1",
        session_id="sess-1",
        channel="codex",
    )
    n = execution_check.register_pending_toolcalls(
        trace_id="trace-bbb",
        toolcalls=[
            {
                "tool_call_id": "call_ABC",
                "tool_name": "exec_command",
                "arguments": {
                    "cmd": "ls",
                    "justification": "list files",
                    "sandbox_permissions": "require_escalated",
                },
            }
        ],
    )
    assert n == 1

    first = execution_check.check_pretool_use(
        {
            "session_id": "sess-1",
            "tool_name": "Bash",
            "tool_use_id": "call_ABC",
            "tool_input": {"command": "ls"},
        }
    )
    assert first.allowed
    assert first.reason == "matched_tool_call_id"

    second = execution_check.check_pretool_use(
        {
            "session_id": "sess-1",
            "tool_name": "Bash",
            "tool_use_id": "call_ABC",
            "tool_input": {"command": "ls"},
        }
    )
    assert not second.allowed
    assert second.reason == "said_done_mismatch"


def test_pretool_escalate_detail_mismatch_same_id(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    session_index.register_binding(
        trace_id="trace-det",
        session_id="sess-det",
        prompt_cache_key="sess-det",
        channel="codex",
    )
    execution_check.register_pending_toolcalls(
        trace_id="trace-det",
        toolcalls=[
            {
                "tool_call_id": "call_DET",
                "tool_name": "exec_command",
                "arguments": {"cmd": "ls /tmp"},
            }
        ],
    )

    result = execution_check.check_pretool_use(
        {
            "session_id": "sess-det",
            "tool_name": "Bash",
            "tool_use_id": "call_DET",
            "tool_input": {"command": "ls /tmp && rm -rf /"},
        }
    )
    assert not result.allowed
    assert result.reason == "said_done_detail_mismatch"
    assert result.matched_tool_call_id == "call_DET"
    assert result.diff
    assert "command" in (result.diff or "")

    # Not consumed — correct command can still auto-allow later.
    ok = execution_check.check_pretool_use(
        {
            "session_id": "sess-det",
            "tool_name": "Bash",
            "tool_use_id": "call_DET",
            "tool_input": {"command": "ls /tmp"},
        }
    )
    assert ok.allowed
    assert ok.reason == "matched_tool_call_id"


def test_pretool_allow_ignores_justification_drift(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    session_index.register_binding(
        trace_id="trace-meta",
        session_id="sess-meta",
        channel="codex",
    )
    execution_check.register_pending_toolcalls(
        trace_id="trace-meta",
        toolcalls=[
            {
                "tool_call_id": "call_META",
                "tool_name": "exec_command",
                "arguments": {"cmd": "pwd", "justification": "said reason"},
            }
        ],
    )
    result = execution_check.check_pretool_use(
        {
            "session_id": "sess-meta",
            "tool_name": "Bash",
            "tool_use_id": "call_META",
            "tool_input": {"command": "pwd", "justification": "different reason"},
        }
    )
    assert result.allowed
    assert result.reason == "matched_tool_call_id"


def test_pretool_allow_by_canonical_command(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    session_index.register_binding(
        trace_id="trace-ccc",
        session_id="sess-2",
        prompt_cache_key="sess-2",
        channel="codex",
    )
    execution_check.register_pending_toolcalls(
        trace_id="trace-ccc",
        toolcalls=[
            {
                "tool_call_id": "call_NO_ID_MATCH",
                "tool_name": "exec_command",
                "arguments": {"cmd": "open /tmp/demo"},
            }
        ],
    )

    # Different tool_use_id, same command → fingerprint / command path.
    result = execution_check.check_pretool_use(
        {
            "session_id": "sess-2",
            "tool_name": "exec_command",
            "tool_use_id": "call_OTHER",
            "tool_input": {"cmd": "open /tmp/demo"},
        }
    )
    assert result.allowed
    assert result.reason == "matched_canonical_args"


def test_pretool_escalate_unknown(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    session_index.register_binding(
        trace_id="trace-ddd",
        session_id="sess-3",
        channel="claude_code",
    )
    result = execution_check.check_pretool_use(
        {
            "session_id": "sess-3",
            "tool_name": "Bash",
            "tool_use_id": "call_UNKNOWN",
            "tool_input": {"command": "curl http://evil.example"},
        }
    )
    assert not result.allowed
    assert result.reason == "said_done_mismatch"
    assert result.trace_id == "trace-ddd"


def test_pretool_escalate_when_trace_unresolved(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    result = execution_check.check_pretool_use(
        {
            "session_id": "never-seen",
            "tool_name": "Bash",
            "tool_use_id": "call_X",
            "tool_input": {"command": "pwd"},
        }
    )
    assert not result.allowed
    assert result.reason == "trace_unresolved"


def test_pending_file_shape(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    execution_check.register_pending_toolcalls(
        trace_id="t1",
        toolcalls=[
            {
                "tool_call_id": "call_1",
                "tool_name": "exec_command",
                "arguments": {"cmd": "echo hi"},
            }
        ],
    )
    raw = json.loads((tmp_path / "pending_actions.json").read_text(encoding="utf-8"))
    assert "call_1" in raw["actions"]
    assert raw["actions"]["call_1"]["consumed"] is False


def test_said_done_hook_disabled_skips_gate(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("ARBITEROS_SAID_DONE_HOOK_ENABLED", "0")
    execution_check._CONFIG_CACHE_MTIME_NS = None
    execution_check._CONFIG_CACHE_ENABLED = None

    n = execution_check.register_pending_toolcalls(
        trace_id="t-off",
        toolcalls=[
            {
                "tool_call_id": "call_off",
                "tool_name": "exec_command",
                "arguments": {"cmd": "pwd"},
            }
        ],
    )
    assert n == 0
    assert not (tmp_path / "pending_actions.json").exists()

    result = execution_check.check_pretool_use(
        {
            "session_id": "any",
            "tool_name": "Bash",
            "tool_use_id": "call_off",
            "tool_input": {"command": "pwd"},
        }
    )
    assert result.allowed
    assert result.reason == "said_done_disabled"
