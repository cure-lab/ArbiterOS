"""Claude Code parent/subagent trace split and TUI graph."""

from __future__ import annotations

import io
from datetime import datetime, timezone

from rich.console import Console

import arbiteros_kernel.agent_graph as ag
import arbiteros_kernel.litellm_callback as lc
from arbiteros_kernel.tui.app import ArbiterTuiApp
from arbiteros_kernel.tui.trace_catalog import TraceRow


SESSION = "363251f1-b6bb-45f1-bc7d-72272a00b9b5"
AGENT_A = "aa139c0fcbae522d2"
AGENT_B = "bb249d1fdcbf633e3"

_LAUNCH_TEXT = (
    "Async agent launched successfully. (This tool result is internal metadata "
    "— never quote or paste any part of it, including the agentId below, into "
    "a user-facing reply.)\n"
    f"agentId: {AGENT_A} (internal ID - do not mention to user. Use SendMessage "
    f"with to: '{AGENT_A}', summary: '<5-10 word recap>' to continue this agent.)\n"
    "The agent is working in the background."
)


def _headers(session_id: str, agent_id: str | None = None) -> dict[str, str]:
    headers = {"x-claude-code-session-id": session_id}
    if agent_id:
        headers["x-claude-code-agent-id"] = agent_id
    return headers


def _incoming(
    session_id: str,
    *,
    agent_id: str | None = None,
    messages: list | None = None,
) -> dict:
    headers = _headers(session_id, agent_id)
    return {
        "model": "claude-sonnet-4-5-20250929",
        "messages": messages
        or [{"role": "user", "content": "list the desktop"}],
        "litellm_metadata": {"headers": headers},
        "proxy_server_request": {"headers": headers},
    }


def _openai_agent_spawn_messages(
    *,
    agent_id: str = AGENT_A,
    subagent_type: str = "Explore",
    call_id: str = "call_explore_1",
) -> list[dict]:
    return [
        {"role": "user", "content": "look around"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "Agent",
                        "arguments": (
                            '{"subagent_type": "%s", "prompt": "explore"}'
                            % subagent_type
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": _LAUNCH_TEXT.replace(AGENT_A, agent_id),
        },
    ]


def _anthropic_agent_spawn_messages(
    *,
    agent_id: str = AGENT_A,
    subagent_type: str = "Explore",
    call_id: str = "toolu_explore_1",
) -> list[dict]:
    return [
        {"role": "user", "content": "look around"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": "Agent",
                    "input": {"subagent_type": subagent_type, "prompt": "explore"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": _LAUNCH_TEXT.replace(AGENT_A, agent_id),
                }
            ],
        },
    ]


def _patch_claude_code(monkeypatch) -> None:
    monkeypatch.setattr(lc, "_get_request_agent_name", lambda incoming=None: "claude_code")
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda force=False: None)
    monkeypatch.setattr(lc, "_persist_trace_state_to_disk", lambda: None)


def _isolate_graph(tmp_path, monkeypatch):
    path = tmp_path / "agent_graph.json"
    monkeypatch.setenv("ARBITEROS_AGENT_GRAPH_FILE", str(path))
    ag.reset_graph_file_cache()
    return path


def _seed_parent_state(session_id: str = SESSION, trace_id: str = "parent-tid") -> lc._TraceState:
    user_id = ag.claude_code_user_id(session_id)
    device_key = f"claude_code:{user_id}"
    state = lc._TraceState(
        trace_id=trace_id,
        device_key=device_key,
        channel="claude_code",
        user_id=user_id,
        sequence=1,
        turn_index=1,
        agent_name="claude_code",
        trace_started_at=datetime.now(timezone.utc).isoformat(),
    )
    with lc._trace_state_lock:
        lc._trace_state_by_device[device_key] = state
    return state


def test_claude_code_user_id_parent_vs_child():
    parent = ag.claude_code_user_id(SESSION)
    child = ag.claude_code_user_id(SESSION, AGENT_A)
    assert parent == f"claude-code-session-{SESSION}"
    assert child == f"claude-code-session-{SESSION}:agent-{AGENT_A}"
    assert parent != child
    assert ag.parse_claude_code_user_id(parent) == (SESSION, None)
    assert ag.parse_claude_code_user_id(child) == (SESSION, AGENT_A)


def test_parse_agent_id_from_launch_text():
    assert ag.parse_agent_id_from_text(_LAUNCH_TEXT) == AGENT_A
    assert ag.parse_agent_id_from_text("no id here") is None


def test_iter_agent_spawns_openai_and_anthropic():
    openai_hits = ag.iter_agent_spawns_from_messages(_openai_agent_spawn_messages())
    assert openai_hits == [
        {
            "agent_id": AGENT_A,
            "tool_call_id": "call_explore_1",
            "subagent_type": "Explore",
        }
    ]
    anthropic_hits = ag.iter_agent_spawns_from_messages(
        _anthropic_agent_spawn_messages()
    )
    assert anthropic_hits == [
        {
            "agent_id": AGENT_A,
            "tool_call_id": "toolu_explore_1",
            "subagent_type": "Explore",
        }
    ]
    send = ag.iter_agent_spawns_from_messages(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_send",
                        "function": {
                            "name": "SendMessage",
                            "arguments": '{"to": "%s"}' % AGENT_A,
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_send",
                "content": f"delivered to {AGENT_A}",
            },
        ]
    )
    assert send == []


def test_record_link_and_format_graph(tmp_path, monkeypatch):
    _isolate_graph(tmp_path, monkeypatch)
    ag.record_spawn(
        parent_trace_id="parent-tid",
        session_id=SESSION,
        agent_id=AGENT_A,
        subagent_type="Explore",
        tool_call_id="call_explore_1",
    )
    assert ag.resolve_parent_trace_id(agent_id=AGENT_A) == "parent-tid"
    linked = ag.link_child(
        child_trace_id="child-tid",
        session_id=SESSION,
        agent_id=AGENT_A,
        parent_trace_id=None,
    )
    assert linked == "parent-tid"
    assert ag.parent_of("child-tid") == "parent-tid"
    assert ag.children_of("parent-tid") == ["child-tid"]

    ag.record_spawn(
        parent_trace_id="parent-tid",
        session_id=SESSION,
        agent_id=AGENT_B,
        subagent_type="Plan",
    )
    ag.link_child(
        child_trace_id="child-b",
        session_id=SESSION,
        agent_id=AGENT_B,
        parent_trace_id="parent-tid",
    )

    text = ag.format_graph_trees(
        extra_by_trace={
            "parent-tid": {"agent": "claude_code", "status": "running"},
            "child-tid": {"agent": "claude_code", "status": "running"},
            "child-b": {"agent": "claude_code", "status": "done"},
            "lonely": {"agent": "claude_code", "status": "running"},
        },
        known_trace_ids=["parent-tid", "child-tid", "child-b", "lonely"],
    )
    assert "lonely" not in text
    assert "parent  claude_code  parent-tid  running" in text
    assert f"Explore [{AGENT_A}]  claude_code  child-tid  running" in text
    assert f"Plan [{AGENT_B}]  claude_code  child-b  done" in text
    assert "├─ " in text
    assert "└─ " in text


def test_format_graph_empty(tmp_path, monkeypatch):
    _isolate_graph(tmp_path, monkeypatch)
    assert (
        ag.format_graph_trees() == "No parent/subagent relations recorded yet."
    )


def test_build_device_context_splits_parent_and_subagent(monkeypatch):
    _patch_claude_code(monkeypatch)
    parent = lc._build_device_context(_incoming(SESSION))
    child = lc._build_device_context(_incoming(SESSION, agent_id=AGENT_A))
    other = lc._build_device_context(_incoming(SESSION, agent_id=AGENT_B))
    same_child = lc._build_device_context(_incoming(SESSION, agent_id=AGENT_A))

    assert parent.channel == "claude_code"
    assert parent.user_id == f"claude-code-session-{SESSION}"
    assert parent.device_key == f"claude_code:claude-code-session-{SESSION}"
    assert child.device_key == (
        f"claude_code:claude-code-session-{SESSION}:agent-{AGENT_A}"
    )
    assert other.device_key == (
        f"claude_code:claude-code-session-{SESSION}:agent-{AGENT_B}"
    )
    assert parent.device_key != child.device_key
    assert child.device_key != other.device_key
    assert child.device_key == same_child.device_key
    channel, user_id = lc._parse_device_key(child.device_key)
    assert channel == "claude_code"
    assert user_id == f"claude-code-session-{SESSION}:agent-{AGENT_A}"


def test_scope_key_includes_agent_id(monkeypatch):
    _patch_claude_code(monkeypatch)
    parent_key = lc._extract_claude_code_scope_key(_incoming(SESSION))
    child_key = lc._extract_claude_code_scope_key(_incoming(SESSION, agent_id=AGENT_A))
    assert parent_key == f"sid:{SESSION}"
    assert child_key == f"sid:{SESSION}:agent:{AGENT_A}"
    assert parent_key != child_key


def test_sync_graph_records_spawn_and_links_child(tmp_path, monkeypatch):
    _isolate_graph(tmp_path, monkeypatch)
    _patch_claude_code(monkeypatch)
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()
    parent_state = _seed_parent_state()
    lc._sync_claude_code_agent_graph(
        _incoming(SESSION, messages=_openai_agent_spawn_messages()),
        parent_state,
    )
    assert ag.resolve_parent_trace_id(agent_id=AGENT_A) == "parent-tid"

    child_user = ag.claude_code_user_id(SESSION, AGENT_A)
    child_state = lc._TraceState(
        trace_id="child-tid",
        device_key=f"claude_code:{child_user}",
        channel="claude_code",
        user_id=child_user,
        agent_name="claude_code",
    )
    lc._sync_claude_code_agent_graph(
        _incoming(SESSION, agent_id=AGENT_A),
        child_state,
    )
    assert child_state.parent_trace_id == "parent-tid"
    assert ag.parent_of("child-tid") == "parent-tid"
    assert child_state.claude_code_agent_id == AGENT_A
    assert child_state.claude_code_session_id == SESSION


def test_sync_graph_falls_back_to_session_parent(tmp_path, monkeypatch):
    _isolate_graph(tmp_path, monkeypatch)
    _patch_claude_code(monkeypatch)
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()
    _seed_parent_state()
    child_user = ag.claude_code_user_id(SESSION, AGENT_A)
    child_state = lc._TraceState(
        trace_id="child-tid",
        device_key=f"claude_code:{child_user}",
        channel="claude_code",
        user_id=child_user,
        agent_name="claude_code",
    )
    lc._sync_claude_code_agent_graph(
        _incoming(SESSION, agent_id=AGENT_A),
        child_state,
    )
    assert child_state.parent_trace_id == "parent-tid"
    assert ag.parent_of("child-tid") == "parent-tid"


def test_tui_graph_commands(tmp_path, monkeypatch):
    _isolate_graph(tmp_path, monkeypatch)
    ag.record_spawn(
        parent_trace_id="parent-tid",
        session_id=SESSION,
        agent_id=AGENT_A,
        subagent_type="Explore",
    )
    ag.link_child(
        child_trace_id="child-tid",
        session_id=SESSION,
        agent_id=AGENT_A,
        parent_trace_id="parent-tid",
    )
    rows = [
        TraceRow(
            trace_id="parent-tid",
            status="running",
            agent="claude_code",
            role="-",
            created_at="",
            context="",
            tokens="",
            pending_block="-",
            instruction_count=1,
            is_test=False,
        ),
        TraceRow(
            trace_id="child-tid",
            status="running",
            agent="claude_code",
            role="-",
            created_at="",
            context="",
            tokens="",
            pending_block="-",
            instruction_count=1,
            is_test=False,
        ),
    ]
    import arbiteros_kernel.tui.app as tui_app

    monkeypatch.setattr(tui_app, "load_trace_rows", lambda: rows)
    buf = io.StringIO()
    app = ArbiterTuiApp(console=Console(file=buf, force_terminal=False, width=120))
    assert app.handle_home_input("g") is True
    assert app.handle_home_input("graph") is True
    out = buf.getvalue()
    assert "parent  claude_code  parent-tid  running" in out
    assert f"Explore [{AGENT_A}]  claude_code  child-tid  running" in out
    assert out.count("Agent graph") == 2
