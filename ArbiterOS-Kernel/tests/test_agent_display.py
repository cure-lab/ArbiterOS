"""Tests for route-agent display (not transport channel)."""

from __future__ import annotations

from arbiteros_kernel.tui.trace_catalog import _agent_from_state, _infer_agent_from_instructions


def test_agent_from_state_prefers_agent_name_over_channel() -> None:
    assert (
        _agent_from_state(
            {
                "channel": "webchat",
                "device_key": "webchat:msg-1",
                "agent_name": "openclaw",
            }
        )
        == "openclaw"
    )


def test_agent_from_state_uses_round_agent() -> None:
    assert (
        _agent_from_state(
            {
                "channel": "webchat",
                "token_usage_rounds": [
                    {"model": "gpt-5.5", "agent": "openclaw"},
                ],
            }
        )
        == "openclaw"
    )


def test_agent_from_state_ignores_channel_when_missing() -> None:
    assert (
        _agent_from_state(
            {
                "channel": "webchat",
                "device_key": "webchat:msg-1",
                "token_usage_rounds": [{"model": "gpt-5.5"}],
            }
        )
        == "unknown"
    )


def test_infer_openclaw_from_system_prompt() -> None:
    assert (
        _infer_agent_from_instructions(
            [
                {
                    "content": (
                        "You are a personal assistant running inside OpenClaw.\n"
                        "## Tooling\nRun Codex CLI, Claude Code, OpenCode via background process.\n"
                    ),
                }
            ]
        )
        == "openclaw"
    )


def test_infer_claude_code_not_fooled_by_short_mention() -> None:
    assert (
        _infer_agent_from_instructions(
            [
                {
                    "content": (
                        "x-anthropic-billing-header: cc\n"
                        "You are Claude Code, Anthropic's official CLI for Claude, running within the IDE.\n"
                    ),
                }
            ]
        )
        == "claude_code"
    )
