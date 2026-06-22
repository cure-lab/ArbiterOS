from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal


McpFlowKind = Literal[
    "none",
    "read_sensitive",
    "comm_sink",
    "business_side_effect",
    "persist_side_effect",
]


_MAIL_SERVICES = {"gmail", "email", "mail"}
_CHAT_SERVICES = {"slack", "telegram"}
_CRM_SERVICES = {"salesforce", "crm", "customer_service"}
_SCHEDULING_SERVICES = {"calendar", "zoom"}
_WORK_TRACKING_SERVICES = {"atlassian", "jira", "confluence"}
_MODEL_REVIEW_SERVICES = {
    "claude_review",
    "codex",
    "gemini_review",
    "llm_chat",
    "manual_review",
    "minimax_chat",
    "oracle",
}

_READ_VERBS = (
    "search",
    "find",
    "fetch",
    "get",
    "list",
    "lookup",
    "query",
    "read",
    "show",
    "view",
    "describe",
    "download",
    "meta",
    "relationship",
)
_READ_TOKENS = {
    "access",
    "accessible",
    "account",
    "body",
    "clipboard",
    "comment",
    "comments",
    "dataset",
    "datasets",
    "directory",
    "eligibility",
    "exists",
    "feed",
    "field",
    "fields",
    "health",
    "history",
    "inbox",
    "info",
    "issue",
    "issues",
    "job",
    "jobs",
    "me",
    "metadata",
    "page",
    "pages",
    "project",
    "projects",
    "resource",
    "resources",
    "schema",
    "screenshot",
    "scrape",
    "snapshot",
    "space",
    "spaces",
    "status",
    "tab",
    "tabs",
    "table",
    "tables",
    "user",
    "visible",
    "wait",
    "worklog",
}
_SEND_VERBS = (
    "call",
    "chat",
    "email",
    "forward",
    "invite",
    "notify",
    "post",
    "publish",
    "reply",
    "send",
    "share",
)
_SEND_TOKENS = set(_SEND_VERBS) | {"message"}
_MUTATION_VERBS = (
    "accept",
    "add",
    "admit",
    "apply",
    "approve",
    "assign",
    "autofill",
    "book",
    "cancel",
    "checkin",
    "checkout",
    "clear",
    "click",
    "close",
    "convert",
    "create",
    "decline",
    "delete",
    "disable",
    "drag",
    "duplicate",
    "edit",
    "enable",
    "end",
    "execute",
    "exchange",
    "fork",
    "fill",
    "grant",
    "generate",
    "hover",
    "init",
    "inject",
    "insert",
    "join",
    "key",
    "launch",
    "leave",
    "link",
    "log",
    "login",
    "logout",
    "mark",
    "merge",
    "modify",
    "move",
    "open",
    "pay",
    "pause",
    "resume",
    "refund",
    "register",
    "remove",
    "run",
    "scroll",
    "set",
    "shell",
    "select",
    "save",
    "start",
    "stop",
    "submit",
    "switch",
    "trade",
    "transfer",
    "transition",
    "type",
    "update",
    "upload",
    "upsert",
    "write",
)
_DESTRUCTIVE_VERBS = (
    "cancel",
    "clear",
    "close",
    "delete",
    "disable",
    "drop",
    "erase",
    "purge",
    "remove",
    "revoke",
    "terminate",
    "wipe",
)
_MUTATION_TOKENS = {
    "accept",
    "add",
    "admit",
    "apply",
    "approve",
    "assign",
    "book",
    "cancel",
    "checkin",
    "checkout",
    "clear",
    "click",
    "close",
    "convert",
    "create",
    "dbsql",
    "decline",
    "delete",
    "disable",
    "drag",
    "duplicate",
    "edit",
    "enable",
    "end",
    "exec",
    "execute",
    "exchange",
    "fill",
    "grant",
    "generate",
    "hover",
    "init",
    "inject",
    "insert",
    "join",
    "key",
    "launch",
    "leave",
    "link",
    "login",
    "mark",
    "modify",
    "move",
    "navigate",
    "open",
    "pay",
    "pause",
    "refund",
    "register",
    "remove",
    "request",
    "reset",
    "restore",
    "resize",
    "resume",
    "review",
    "run",
    "save",
    "scroll",
    "select",
    "set",
    "shell",
    "sql",
    "star",
    "start",
    "stop",
    "submit",
    "switch",
    "trade",
    "transfer",
    "transition",
    "type",
    "unstar",
    "update",
    "upload",
    "upsert",
    "write",
}

_DEFAULT_ALLOWLIST_PATH = "~/.arbiteros/mcp_tool_allowlist.json"


def _split_mcp_tool_name(tool_name: str) -> tuple[str, str]:
    name = (tool_name or "").strip()
    if "__" not in name:
        return "", ""
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3:
            return _normalize_name(parts[1]), _normalize_name(parts[2])
    service, action = name.split("__", 1)
    return _normalize_name(service), _normalize_name(action)


def _normalize_name(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value.strip())
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value.lower()


def _starts_with_any(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(
        value == prefix
        or value.startswith(f"{prefix}_")
        or value.startswith(f"{prefix}-")
        for prefix in prefixes
    )


def _tokens(value: str) -> set[str]:
    return {part for part in value.replace("-", "_").split("_") if part}


def _drop_service_prefix(service: str, action: str) -> str:
    parts = action.split("_")
    compact_service = service.replace("_", "")
    for count in range(1, min(len(parts), 4) + 1):
        if "".join(parts[:count]) == compact_service:
            stripped = "_".join(parts[count:])
            return stripped or action
    return action


def _contains_mutation_token(value: str) -> bool:
    return bool(_tokens(value) & _MUTATION_TOKENS)


def _contains_read_token(value: str) -> bool:
    return bool(_tokens(value) & (_READ_TOKENS | set(_READ_VERBS)))


def _contains_send_token(value: str) -> bool:
    return bool(_tokens(value) & _SEND_TOKENS)


def is_mcp_tool_name(tool_name: str) -> bool:
    """Return True for names that look like namespaced MCP tools."""

    name = (tool_name or "").strip()
    return "__" in name or name.startswith("mcp__")


def unknown_mcp_allowlist_path() -> Path:
    configured = os.getenv("ARBITEROS_UNKNOWN_MCP_ALLOWLIST_FILE", "")
    return Path(configured or _DEFAULT_ALLOWLIST_PATH).expanduser()


def _env_allowlist() -> set[str]:
    raw = os.getenv("ARBITEROS_UNKNOWN_MCP_ALLOWLIST", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _file_allowlist() -> set[str]:
    path = unknown_mcp_allowlist_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except Exception:
        return set()

    if isinstance(data, list):
        return {item.strip().lower() for item in data if isinstance(item, str)}
    if isinstance(data, dict):
        tools = data.get("tools")
        if isinstance(tools, list):
            return {item.strip().lower() for item in tools if isinstance(item, str)}
    return set()


def is_unknown_mcp_tool_allowlisted(tool_name: str) -> bool:
    name = (tool_name or "").strip().lower()
    if not name:
        return False
    return name in (_env_allowlist() | _file_allowlist())


def classify_mcp_tool_flow(tool_name: str) -> McpFlowKind:
    """Classify domain MCP tools into ArbiterOS flow buckets.

    This is deliberately service/verb based rather than case-id based. DTAP and
    real customer-service agents expose business systems as MCP tools such as
    ``gmail__send_email`` or ``salesforce__update_record``; policies can only
    reason about them once they are lowered into the existing flow vocabulary.
    """

    service, action = _split_mcp_tool_name(tool_name)
    if not service or not action:
        return "none"
    action = _drop_service_prefix(service, action)

    if service in _MAIL_SERVICES:
        if _starts_with_any(action, _SEND_VERBS):
            return "comm_sink"
        if _starts_with_any(action, _READ_VERBS):
            return "read_sensitive"
        if _starts_with_any(action, _MUTATION_VERBS) or _contains_mutation_token(action):
            return "persist_side_effect"
        if _contains_read_token(action):
            return "read_sensitive"
        if _contains_send_token(action):
            return "comm_sink"
        return "none"

    if service in _CHAT_SERVICES:
        if _starts_with_any(action, _SEND_VERBS):
            return "comm_sink"
        if _starts_with_any(action, _READ_VERBS) or action.endswith("_history"):
            return "read_sensitive"
        if _starts_with_any(action, _MUTATION_VERBS) or _contains_mutation_token(action):
            return "persist_side_effect"
        if _contains_read_token(action):
            return "read_sensitive"
        if _contains_send_token(action):
            return "comm_sink"
        return "none"

    if service in _CRM_SERVICES:
        if _starts_with_any(action, _MUTATION_VERBS) or _contains_mutation_token(action):
            if not _starts_with_any(action, _DESTRUCTIVE_VERBS):
                return "business_side_effect"
            return "persist_side_effect"
        if _starts_with_any(action, _READ_VERBS) or _contains_read_token(action):
            return "read_sensitive"
        return "none"

    if service in _SCHEDULING_SERVICES:
        if _starts_with_any(action, _READ_VERBS):
            return "read_sensitive"
        if _starts_with_any(action, _SEND_VERBS) or _contains_send_token(action):
            return "comm_sink"
        if _starts_with_any(action, _MUTATION_VERBS) or _contains_mutation_token(action):
            if _starts_with_any(action, _DESTRUCTIVE_VERBS):
                return "persist_side_effect"
            return "business_side_effect"
        if _contains_read_token(action):
            return "read_sensitive"
        return "none"

    if service in _WORK_TRACKING_SERVICES:
        if _starts_with_any(action, _READ_VERBS):
            return "read_sensitive"
        if _starts_with_any(action, _SEND_VERBS) or _contains_send_token(action):
            return "comm_sink"
        if _starts_with_any(action, _MUTATION_VERBS) or _contains_mutation_token(action):
            if _starts_with_any(action, _DESTRUCTIVE_VERBS):
                return "persist_side_effect"
            return "business_side_effect"
        if _contains_read_token(action):
            return "read_sensitive"
        return "none"

    if service in _MODEL_REVIEW_SERVICES:
        if action.endswith("_status") or _starts_with_any(action, _READ_VERBS):
            return "read_sensitive"
        return "comm_sink"

    if service == "codex_image2":
        if action.endswith("_status") or _starts_with_any(action, _READ_VERBS):
            return "read_sensitive"
        if action.startswith("generate"):
            return "persist_side_effect"

    if _starts_with_any(action, _SEND_VERBS):
        return "comm_sink"
    if _starts_with_any(action, _READ_VERBS):
        return "read_sensitive"
    if _starts_with_any(action, _MUTATION_VERBS) or _contains_mutation_token(action):
        return "persist_side_effect"
    if _contains_read_token(action):
        return "read_sensitive"
    if _contains_send_token(action):
        return "comm_sink"

    return "none"
