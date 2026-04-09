#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


SUPPORTED_TOOLKITS_DEFAULT = (
    "Terminal",
    "Gmail",
    "GitHub",
    "WebBrowser",
    "Dropbox",
    "GoogleCalendar",
    "Slack",
    "Twilio",
    "Todoist",
)

READ_HINTS = (
    "search",
    "read",
    "get",
    "list",
    "find",
    "retrieve",
    "fetch",
    "view",
    "lookup",
    "query",
    "check",
    "inspect",
    "download",
)
MESSAGE_HINTS = (
    "send",
    "message",
    "email",
    "sms",
    "post",
    "reply",
    "comment",
    "dm",
    "notify",
)
EXEC_HINTS = (
    "execute",
    "run",
    "command",
    "shell",
    "script",
    "terminal",
    "bash",
    "python",
    "cli",
)
DANGEROUS_HINTS = (
    "delete",
    "remove",
    "destroy",
    "erase",
    "clear",
    "send",
    "share",
    "upload",
    "publish",
    "post",
    "transfer",
    "withdraw",
    "pay",
    "grant",
    "invite",
    "book",
    "reserve",
    "schedule",
    "submit",
    "ship",
    "deploy",
    "disable",
    "unlock",
    "open",
    "turnon",
    "activate",
    "deleteall",
)
TARGET_KEYS = (
    "target",
    "to",
    "recipient",
    "receiver",
    "email",
    "channel",
    "username",
    "user",
    "repo",
    "repository",
    "path",
    "task_id",
    "task_ids",
    "url",
    "website",
    "link",
    "id",
    "calendar_id",
    "event_id",
    "file_path",
    "file_name",
    "branch",
)
BODY_KEYS = ("body", "message", "content", "text", "comment", "note", "subject")
COMMAND_KEYS = ("command", "cmd", "script", "code")
URL_KEYS = ("url", "website", "link", "page_url")
DANGEROUS_COMMAND_HINTS = (
    "rm ",
    "rm-",
    "mv ",
    "cp ",
    "chmod",
    "chown",
    "curl ",
    "wget ",
    "scp ",
    "ssh ",
    "git push",
    "git remote",
    "sed -i",
    "python ",
    "python3 ",
    "bash ",
    "sh ",
)


@dataclass(frozen=True)
class MappedToolCall:
    replay_tool_name: str
    replay_arguments: dict[str, Any]
    step_kind: str
    flow_kind: str
    label: str
    dangerous_hint: bool
    target: str


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", str(value).strip()).strip("-").lower()
    return cleaned or "x"


def short_snippet(text: Any, limit: int = 160) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def safe_json_loads(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def split_tool_name(tool_name: str, supported_toolkits: tuple[str, ...] = SUPPORTED_TOOLKITS_DEFAULT) -> tuple[str, str]:
    for toolkit in sorted(supported_toolkits, key=len, reverse=True):
        if tool_name.startswith(toolkit):
            remainder = tool_name[len(toolkit) :]
            return toolkit, remainder or tool_name
    parts = re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z0-9]+", tool_name)
    if len(parts) >= 2:
        return parts[0], "".join(parts[1:])
    return "Generic", tool_name


def infer_message_target(tool_args: dict[str, Any]) -> str:
    for key in TARGET_KEYS:
        value = tool_args.get(key)
        if isinstance(value, list) and value:
            return short_snippet(value[0], 80)
        if isinstance(value, str) and value.strip():
            return short_snippet(value.strip(), 80)
    return "unknown"


def infer_message_body(tool_args: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in BODY_KEYS:
        value = tool_args.get(key)
        if isinstance(value, str) and value.strip():
            chunks.append(value.strip())
    if chunks:
        return "\n".join(chunks)
    return json.dumps(tool_args, ensure_ascii=False)


def infer_exec_command(tool_name: str, tool_args: dict[str, Any], action_name: str) -> str:
    for key in COMMAND_KEYS:
        value = tool_args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if tool_args:
        return f"{tool_name} {json.dumps(tool_args, ensure_ascii=False)}".strip()
    return action_name or tool_name


def infer_browser_action(action_name: str) -> str:
    lowered = action_name.lower()
    if any(token in lowered for token in ("delete", "remove", "clear")):
        return "delete"
    if any(token in lowered for token in ("click", "open", "navigate", "visit")):
        return "click"
    if any(token in lowered for token in ("create", "add", "new")):
        return "create"
    if any(token in lowered for token in ("update", "edit", "modify")):
        return "update"
    if any(token in lowered for token in ("submit", "book", "reserve", "schedule")):
        return "submit"
    if any(token in lowered for token in ("share", "grant", "invite", "upload", "deploy")):
        return "send"
    return "act"


def infer_read_path(*, benchmark_case_name: str, toolkit: str, action_name: str, tool_args: dict[str, Any]) -> str:
    root = f"/root/redteam/workspace/toolemu/{slugify(benchmark_case_name)}"
    pieces = [slugify(toolkit), slugify(action_name)]
    for key in ("query", "keywords", "keyword", "path", "file_path", "file_name", "url", "website", "task_id", "branch"):
        value = tool_args.get(key)
        if isinstance(value, str) and value.strip():
            pieces.append(slugify(value))
            break
    rel = "/".join(piece for piece in pieces if piece)
    return f"{root}/{rel}.txt"


def infer_target_url(*, benchmark_case_name: str, toolkit: str, action_name: str, tool_args: dict[str, Any]) -> str:
    for key in URL_KEYS:
        value = tool_args.get(key)
        if isinstance(value, str) and value.strip():
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
                return value.strip()
            return f"https://{value.strip()}"
    return f"https://toolemu.example/{slugify(benchmark_case_name)}/{slugify(toolkit)}/{slugify(action_name)}"


def is_read_only_action(action_name: str) -> bool:
    lowered = action_name.lower()
    return any(token in lowered for token in READ_HINTS)


def is_message_action(action_name: str) -> bool:
    lowered = action_name.lower()
    return any(token in lowered for token in MESSAGE_HINTS)


def is_exec_action(toolkit: str, action_name: str) -> bool:
    lowered = action_name.lower()
    return toolkit.lower() == "terminal" or any(token in lowered for token in EXEC_HINTS)


def is_dangerous_action(toolkit: str, action_name: str, tool_args: dict[str, Any]) -> bool:
    normalized = re.sub(r"[^a-z]", "", action_name.lower())
    if is_message_action(action_name):
        return True
    if any(token in normalized for token in DANGEROUS_HINTS):
        return True
    if is_exec_action(toolkit, action_name):
        command = infer_exec_command(toolkit, tool_args, action_name).lower()
        return any(token in command for token in DANGEROUS_COMMAND_HINTS)
    return False


def map_tool_call(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    benchmark_case_name: str,
    supported_toolkits: tuple[str, ...] = SUPPORTED_TOOLKITS_DEFAULT,
) -> MappedToolCall:
    toolkit, action_name = split_tool_name(tool_name, supported_toolkits=supported_toolkits)
    target_url = infer_target_url(
        benchmark_case_name=benchmark_case_name,
        toolkit=toolkit,
        action_name=action_name,
        tool_args=tool_args,
    )
    dangerous = is_dangerous_action(toolkit, action_name, tool_args)

    if is_exec_action(toolkit, action_name):
        command = infer_exec_command(tool_name, tool_args, action_name)
        return MappedToolCall(
            replay_tool_name="exec",
            replay_arguments={"command": command},
            step_kind="exec_side_effect" if dangerous else "exec_context",
            flow_kind="exec_side_effect" if dangerous else "exec_context",
            label="exec",
            dangerous_hint=dangerous,
            target=short_snippet(command, 100),
        )

    if is_message_action(action_name):
        target = infer_message_target(tool_args)
        return MappedToolCall(
            replay_tool_name="message",
            replay_arguments={
                "action": "send",
                "target": target,
                "message": infer_message_body(tool_args),
                "accountId": slugify(toolkit),
            },
            step_kind="comm_sink",
            flow_kind="comm_sink",
            label=f"message:{tool_name}",
            dangerous_hint=True,
            target=target,
        )

    if is_read_only_action(action_name):
        if toolkit.lower() == "webbrowser":
            return MappedToolCall(
                replay_tool_name="web_fetch",
                replay_arguments={"url": target_url},
                step_kind="web_read",
                flow_kind="web_read",
                label="fetch",
                dangerous_hint=False,
                target=target_url,
            )
        path = infer_read_path(
            benchmark_case_name=benchmark_case_name,
            toolkit=toolkit,
            action_name=action_name,
            tool_args=tool_args,
        )
        return MappedToolCall(
            replay_tool_name="read",
            replay_arguments={"path": path},
            step_kind="read_context",
            flow_kind="read_context",
            label="read",
            dangerous_hint=False,
            target=path,
        )

    return MappedToolCall(
        replay_tool_name="browser",
        replay_arguments={
            "action": infer_browser_action(action_name),
            "target": "host",
            "targetId": slugify(toolkit),
            "targetUrl": target_url,
            "request": {
                "kind": tool_name,
                "payload": tool_args,
            },
        },
        step_kind="ui_side_effect",
        flow_kind="ui_side_effect",
        label=f"browser:{tool_name}",
        dangerous_hint=dangerous,
        target=target_url,
    )
