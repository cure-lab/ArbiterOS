"""Claude Code stream/shadow twins must still persist keep-block instructions."""

from __future__ import annotations

from arbiteros_kernel.instruction_parsing.builder import InstructionBuilder
from arbiteros_kernel import litellm_callback as cb


def _claude_code_request(*, stream: bool, session: str = "sess-policy-1") -> dict:
    return {
        "model": "claude-sonnet-4-5-20250929",
        "stream": stream,
        "messages": [{"role": "user", "content": "list Desktop/redt/"}],
        "proxy_server_request": {
            "headers": {"x-claude-code-session-id": session},
            "body": {"stream": stream},
        },
    }


def test_non_stream_shadow_does_not_make_stream_turn_aux(monkeypatch):
    monkeypatch.setattr(
        cb, "_get_request_agent_name", lambda incoming=None: "claude_code"
    )
    with cb._claude_code_recent_request_lock:
        cb._claude_code_recent_request_by_scope.clear()

    shadow = _claude_code_request(stream=False)
    streaming = _claude_code_request(stream=True)

    assert cb._is_claude_code_aux_request(shadow) is True
    assert cb._is_claude_code_duplicate_request(shadow) is False
    assert cb._is_claude_code_aux_request(streaming) is False


def test_second_identical_stream_turn_is_still_aux(monkeypatch):
    monkeypatch.setattr(
        cb, "_get_request_agent_name", lambda incoming=None: "claude_code"
    )
    with cb._claude_code_recent_request_lock:
        cb._claude_code_recent_request_by_scope.clear()

    first = _claude_code_request(stream=True)
    second = _claude_code_request(stream=True)
    assert cb._is_claude_code_aux_request(first) is False
    assert cb._is_claude_code_aux_request(second) is True


def test_commit_keep_block_writes_policy_protected_from_text_blocks(monkeypatch):
    monkeypatch.setattr(cb, "_save_instructions_to_trace_file", lambda *a, **k: None)
    builder = InstructionBuilder(trace_id="fc093bc6cf8a3ac82332b5032b0783df")
    builder.add_from_context_message(
        ref_kind="USERINPUT",
        content="list the folder",
        context_key="user:0",
    )
    reason = (
        "## ⚠️ 资源保护策略拦截确认\n"
        "parent_trace_id: c40e97bf7da114d32a3e2a10ca4c3680"
    )
    cb._commit_response_instructions_after_policy(
        builder,
        "fc093bc6cf8a3ac82332b5032b0783df",
        {
            "role": "assistant",
            "content": [{"type": "text", "text": reason}],
            "tool_calls": None,
        },
        instruction_start_index=1,
        policy_protected=reason,
    )
    added = builder.instructions[1:]
    assert added
    assert added[0]["instruction_type"] == "RESPOND"
    assert "parent_trace_id: c40e97bf7da114d32a3e2a10ca4c3680" in added[0]["content"]
    assert added[0]["policy_protected"] == reason
    cb._commit_response_instructions_after_policy(
        builder,
        "fc093bc6cf8a3ac82332b5032b0783df",
        {
            "role": "assistant",
            "content": reason,
            "tool_calls": None,
        },
        instruction_start_index=1,
        policy_protected=reason,
    )
    assert sum(1 for instr in builder.instructions if instr.get("policy_protected")) == 1
