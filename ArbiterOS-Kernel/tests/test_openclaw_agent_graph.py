"""OpenClaw sessions_spawn parent/subagent graph."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import arbiteros_kernel.agent_graph as ag
import arbiteros_kernel.chat_agent_session as cas
import arbiteros_kernel.litellm_callback as lc

TASK = (
    "请在这台 Mac 上打开用户桌面的 redt/ 文件夹。"
    "路径应为 $HOME/Desktop/redt（用户上一句里“桌买呢”应理解为“桌面的”）。"
    "执行后简短回复主会话结果。"
)
CHILD_KEY = "agent:main:subagent:0e4abea0-ef30-4d49-a502-749db824972d"
LABEL = "打开桌面redt"


def _subagent_system(
    *,
    session_key: str = CHILD_KEY,
    task: str = TASK,
    label: str = LABEL,
) -> str:
    return (
        "You are a personal assistant running inside OpenClaw.\n"
        "## Subagent Context\n"
        "You are a **subagent** spawned by the main agent for a specific task.\n"
        f"You were created to handle: {task}\n"
        "## Session Context\n"
        f"- Label: {label}\n"
        "- Requester session: agent:main:main.\n"
        "- Requester channel: webchat.\n"
        f"- Your session: {session_key}.\n"
        "Runtime: agent=main | channel=webchat | thinking=off\n"
    )


def _parent_system() -> str:
    return (
        "You are a personal assistant running inside OpenClaw.\n"
        "Runtime: agent=main | channel=webchat | thinking=off\n"
    )


def _spawn_messages(*, include_result: bool = True) -> list[dict]:
    messages = [
        {"role": "system", "content": _parent_system()},
        {
            "role": "user",
            "content": (
                "启动一个subagent，然后用这个subagent来打开桌面 redt/ 文件夹\n"
                "[message_id: 37f2eb79-3011-4f28-b1fc-5ab382f76057]"
            ),
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_spawn_1",
                    "type": "function",
                    "function": {
                        "name": "sessions_spawn",
                        "arguments": json.dumps(
                            {
                                "task": TASK,
                                "label": LABEL,
                                "timeoutSeconds": 10,
                            },
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        },
    ]
    if include_result:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": "call_spawn_1",
                "content": json.dumps(
                    {
                        "status": "accepted",
                        "childSessionKey": CHILD_KEY,
                        "runId": "a0528f5b-768c-4911-baa2-4a46df550ce0",
                    }
                ),
            }
        )
    return messages


def _child_messages() -> list[dict]:
    return [
        {"role": "system", "content": _subagent_system()},
        {"role": "user", "content": f"[Sat 2026-09-12 16:16 GMT+8] {TASK}"},
    ]


def _isolate_graph(tmp_path, monkeypatch):
    path = tmp_path / "agent_graph.json"
    monkeypatch.setenv("ARBITEROS_AGENT_GRAPH_FILE", str(path))
    ag.reset_graph_file_cache()
    return path


def _patch_openclaw(monkeypatch) -> None:
    monkeypatch.setattr(lc, "_get_request_agent_name", lambda incoming=None: "openclaw")
    monkeypatch.setattr(lc, "_sync_trace_state_from_disk", lambda force=False: None)
    monkeypatch.setattr(lc, "_persist_trace_state_to_disk", lambda: None)


def test_extract_openclaw_subagent_identity():
    messages = _child_messages()
    assert cas.is_openclaw_subagent_messages(messages) is True
    assert cas.extract_openclaw_own_session_key(messages) == CHILD_KEY
    assert cas.extract_openclaw_spawn_task(messages) == TASK
    parent_msgs = _spawn_messages()
    assert cas.is_openclaw_subagent_messages(parent_msgs) is False


def test_iter_sessions_spawn_parses_child_session_key():
    hits = ag.iter_sessions_spawn_from_messages(_spawn_messages())
    assert len(hits) == 1
    assert hits[0]["agent_id"] == CHILD_KEY
    assert hits[0]["subagent_type"] == LABEL
    assert hits[0]["task_key"] == ag.spawn_task_key(TASK)
    send_only = ag.iter_sessions_spawn_from_messages(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_send",
                        "function": {
                            "name": "sessions_send",
                            "arguments": json.dumps(
                                {"sessionKey": CHILD_KEY, "message": "ping"}
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_send",
                "content": "ok",
            },
        ]
    )
    assert send_only == []


def test_iter_sessions_spawn_records_task_key_before_result():
    hits = ag.iter_sessions_spawn_from_messages(_spawn_messages(include_result=False))
    assert len(hits) == 1
    assert hits[0]["agent_id"] == ""
    assert hits[0]["task_key"] == ag.spawn_task_key(TASK)


def test_sync_openclaw_graph_links_child(tmp_path, monkeypatch):
    _isolate_graph(tmp_path, monkeypatch)
    _patch_openclaw(monkeypatch)
    with lc._trace_state_lock:
        lc._trace_state_by_device.clear()

    parent_state = lc._TraceState(
        trace_id="34f13f6bc2fa258d34165c90c46ae6f2",
        device_key="webchat:msg-37f2eb79-3011-4f28-b1fc-5ab382f76057",
        channel="webchat",
        user_id="msg-37f2eb79-3011-4f28-b1fc-5ab382f76057",
        agent_name="openclaw",
        trace_started_at=datetime.now(timezone.utc).isoformat(),
    )
    lc._sync_openclaw_agent_graph(
        {"messages": _spawn_messages()},
        parent_state,
    )
    assert ag.resolve_parent_trace_id(agent_id=CHILD_KEY) == (
        "34f13f6bc2fa258d34165c90c46ae6f2"
    )

    child_state = lc._TraceState(
        trace_id="dda40711b38f680d6949c2119aeaf6ac",
        device_key="webchat:anonymous-child",
        channel="webchat",
        user_id="anonymous-child",
        agent_name="openclaw",
    )
    lc._sync_openclaw_agent_graph(
        {"messages": _child_messages()},
        child_state,
    )
    assert child_state.parent_trace_id == "34f13f6bc2fa258d34165c90c46ae6f2"
    assert ag.parent_of("dda40711b38f680d6949c2119aeaf6ac") == (
        "34f13f6bc2fa258d34165c90c46ae6f2"
    )
    text = ag.format_graph_trees(
        extra_by_trace={
            "34f13f6bc2fa258d34165c90c46ae6f2": {
                "agent": "openclaw",
                "status": "running",
            },
            "dda40711b38f680d6949c2119aeaf6ac": {
                "agent": "openclaw",
                "status": "running",
            },
        }
    )
    assert "parent  openclaw  34f13f6bc2fa258d34165c90c46ae6f2  running" in text
    assert (
        f"{LABEL} [0e4abea0-ef30-4d49-a502-749db824972d]  openclaw  "
        "dda40711b38f680d6949c2119aeaf6ac  running"
    ) in text


def test_sync_openclaw_graph_matches_task_before_child_session_key(
    tmp_path, monkeypatch
):
    _isolate_graph(tmp_path, monkeypatch)
    _patch_openclaw(monkeypatch)
    parent_state = lc._TraceState(
        trace_id="parent-tid",
        device_key="webchat:msg-parent",
        channel="webchat",
        user_id="msg-parent",
        agent_name="openclaw",
    )
    lc._record_openclaw_spawns_from_assistant_response(
        {"messages": [{"role": "system", "content": _parent_system()}]},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_spawn_1",
                    "function": {
                        "name": "sessions_spawn",
                        "arguments": json.dumps(
                            {"task": TASK, "label": LABEL},
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        },
        parent_state.trace_id,
    )
    child_state = lc._TraceState(
        trace_id="child-tid",
        device_key="webchat:child",
        channel="webchat",
        user_id="child",
        agent_name="openclaw",
    )
    lc._sync_openclaw_agent_graph({"messages": _child_messages()}, child_state)
    assert child_state.parent_trace_id == "parent-tid"
    text = ag.format_graph_trees()
    assert f"{LABEL} [0e4abea0-ef30-4d49-a502-749db824972d]" in text
