from __future__ import annotations

import json
from pathlib import Path

from arbiteros_kernel.tool_list_log import (
    save_tool_list_log,
    summarize_tools,
    tools_fingerprint,
)

_CODEX_TOOLS = [
    {
        "type": "function",
        "name": "exec_command",
        "description": "Runs a command in a PTY.",
        "parameters": {
            "type": "object",
            "properties": {"cmd": {"type": "string"}, "workdir": {"type": "string"}},
        },
    },
    {
        "type": "custom",
        "name": "apply_patch",
        "description": "Use apply_patch to edit files.",
        "format": {"type": "grammar", "syntax": "lark", "definition": "..."},
    },
    {
        "type": "namespace",
        "name": "mcp__cua_repl__",
        "description": "UI automation.",
        "tools": [
            {
                "type": "function",
                "name": "js",
                "description": "Run JS.",
                "parameters": {"type": "object", "properties": {"code": {"type": "string"}}},
            }
        ],
    },
    {"type": "web_search", "external_web_access": True},
    {
        "type": "function",
        "function": {
            "name": "chat_style",
            "description": "Wrapped chat tool.",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        },
    },
]


def test_summarize_keeps_param_names_and_flattens_namespace() -> None:
    cards = summarize_tools(_CODEX_TOOLS)
    by_name = {item["name"]: item for item in cards}
    assert by_name["exec_command"]["params"] == ["cmd", "workdir"]
    assert by_name["exec_command"]["description"] == "Runs a command in a PTY."
    assert by_name["apply_patch"]["type"] == "custom"
    assert by_name["apply_patch"]["params"] == []
    assert by_name["mcp__cua_repl__"]["params"] == ["js"]
    assert by_name["mcp__cua_repl__.js"]["params"] == ["code"]
    assert by_name["web_search"]["name"] == "web_search"
    assert by_name["chat_style"]["params"] == ["q"]


def test_save_overwrites_and_skips_llm_when_unchanged(tmp_path: Path) -> None:
    calls: list[int] = []

    def fake_llm(cards):
        calls.append(len(cards))
        return {"scores": {item["name"]: 7 for item in cards}}

    request = {"model": "gpt-5.5", "tools": _CODEX_TOOLS}
    first = save_tool_list_log(
        request,
        trace_id="trace-a",
        agent_name="codex",
        log_dir=tmp_path,
        enabled=True,
        llm_enabled=True,
        llm_fn=fake_llm,
    )
    assert first is not None
    assert first["llm_ran"] is True
    assert first["llm_reason"] == "first"
    assert first["tools"][0]["risk"] == 7
    assert len(calls) == 1

    second = save_tool_list_log(
        request,
        trace_id="trace-a",
        agent_name="codex",
        log_dir=tmp_path,
        enabled=True,
        llm_enabled=True,
        llm_fn=fake_llm,
    )
    assert second is not None
    assert second["llm_ran"] is False
    assert second["llm_reason"] == "unchanged"
    assert second["fingerprint"] == first["fingerprint"]
    assert second["tools"][0]["risk"] == 7
    assert len(calls) == 1

    path = tmp_path / "trace-a.json"
    dumped = json.loads(path.read_text(encoding="utf-8"))
    assert dumped["fingerprint"] == first["fingerprint"]
    assert path.read_text(encoding="utf-8").count('"trace_id"') == 1


def test_save_reruns_llm_when_tools_change(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_llm(cards):
        calls.append(cards[0]["name"])
        return {"scores": {item["name"]: 3 for item in cards}}

    save_tool_list_log(
        {"tools": [_CODEX_TOOLS[0]]},
        trace_id="trace-b",
        log_dir=tmp_path,
        enabled=True,
        llm_enabled=True,
        llm_fn=fake_llm,
    )
    changed = save_tool_list_log(
        {"tools": [_CODEX_TOOLS[1]]},
        trace_id="trace-b",
        log_dir=tmp_path,
        enabled=True,
        llm_enabled=True,
        llm_fn=fake_llm,
    )
    assert changed is not None
    assert changed["llm_ran"] is True
    assert changed["llm_reason"] == "tools_changed"
    assert calls == ["exec_command", "apply_patch"]
    assert changed["tools"][0]["name"] == "apply_patch"
    assert changed["tools"][0]["risk"] == 3


def test_llm_off_does_not_call_model(tmp_path: Path) -> None:
    def boom(cards):
        raise AssertionError("llm should not run")

    record = save_tool_list_log(
        {"tools": [_CODEX_TOOLS[0]]},
        trace_id="trace-c",
        log_dir=tmp_path,
        enabled=True,
        llm_enabled=False,
        llm_fn=boom,
    )
    assert record is not None
    assert record["llm_ran"] is False
    assert record["llm_reason"] == "disabled"
    assert record["tools"][0]["risk"] is None


def test_disabled_log_writes_nothing(tmp_path: Path) -> None:
    record = save_tool_list_log(
        {"tools": [_CODEX_TOOLS[0]]},
        trace_id="trace-d",
        log_dir=tmp_path,
        enabled=False,
        llm_enabled=True,
        llm_fn=lambda cards: {"scores": {}},
    )
    assert record is None
    assert list(tmp_path.iterdir()) == []


def test_fingerprint_ignores_risk_scores() -> None:
    cards = summarize_tools([_CODEX_TOOLS[0]])
    fp1 = tools_fingerprint(cards)
    cards[0]["risk"] = 9
    assert tools_fingerprint(cards) == fp1
