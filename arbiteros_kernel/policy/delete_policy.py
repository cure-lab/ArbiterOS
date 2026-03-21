from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy


_DEFAULT_DELETE_TOOLS = frozenset({
    "delete",
    "remove",
    "unlink",
    "rmdir",
    "trash",
    "move_to_trash",
})

_DEFAULT_DELETE_ACTIONS_BY_TOOL: Dict[str, List[str]] = {
    # Generic action-driven tools
    "browser": ["delete", "remove"],
    "canvas": ["delete", "remove"],
    "nodes": ["delete", "remove"],
    "cron": ["remove", "delete"],
    "sessions": ["delete", "remove"],
}

_DEFAULT_EXEC_DELETE_PATTERNS = [
    # POSIX / shell
    r"(^|[;&|]\s*)rm(\s|$)",
    r"(^|[;&|]\s*)unlink(\s|$)",
    r"(^|[;&|]\s*)rmdir(\s|$)",
    r"\bfind\b.*\s-delete(\s|$)",
    r"\bgit\s+clean\b",

    # Windows cmd
    r"(^|[;&|]\s*)del(\s|$)",
    r"(^|[;&|]\s*)erase(\s|$)",
    r"(^|[;&|]\s*)rd(\s|$)",
    r"(^|[;&|]\s*)rmdir(\s|$)",

    # PowerShell
    r"\bRemove-Item\b",
]


def _get_delete_policy_config() -> Tuple[frozenset[str], Dict[str, List[str]], List[str], bool]:
    """
    Returns:
      (delete_tools, delete_actions_by_tool, exec_delete_patterns, enabled)
    """
    cfg = RUNTIME.cfg.get("delete_policy", {}) or {}
    if not isinstance(cfg, dict):
        return _DEFAULT_DELETE_TOOLS, _DEFAULT_DELETE_ACTIONS_BY_TOOL, _DEFAULT_EXEC_DELETE_PATTERNS, False

    enabled = bool(cfg.get("enabled", False))

    delete_tools = _DEFAULT_DELETE_TOOLS
    raw_tools = cfg.get("tools")
    if isinstance(raw_tools, list) and raw_tools:
        delete_tools = frozenset(
            str(x).strip().lower() for x in raw_tools if isinstance(x, str) and x.strip()
        )

    delete_actions_by_tool = dict(_DEFAULT_DELETE_ACTIONS_BY_TOOL)
    raw_actions = cfg.get("actions_by_tool")
    if isinstance(raw_actions, dict):
        for tool, actions in raw_actions.items():
            if isinstance(tool, str) and tool.strip() and isinstance(actions, list):
                delete_actions_by_tool[tool.strip().lower()] = [
                    str(a).strip().lower() for a in actions if isinstance(a, str) and a.strip()
                ]

    exec_delete_patterns = list(_DEFAULT_EXEC_DELETE_PATTERNS)
    raw_patterns = cfg.get("exec_patterns")
    if isinstance(raw_patterns, list) and raw_patterns:
        exec_delete_patterns = [
            str(p).strip() for p in raw_patterns if isinstance(p, str) and p.strip()
        ]

    return delete_tools, delete_actions_by_tool, exec_delete_patterns, enabled


def _extract_command(args_dict: Dict[str, Any]) -> str:
    for key in ("command", "cmd"):
        value = args_dict.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _match_exec_delete(command: str, patterns: List[str]) -> str | None:
    if not command:
        return None

    for pat in patterns:
        try:
            if re.search(pat, command, flags=re.IGNORECASE):
                return pat
        except re.error:
            continue
    return None


def _classify_delete_operation(tool_name: str, args_dict: Dict[str, Any]) -> Tuple[bool, str | None]:
    """
    Return (is_delete_op, reason).
    """
    name = (tool_name or "").strip().lower()
    delete_tools, delete_actions_by_tool, exec_delete_patterns, _enabled = _get_delete_policy_config()

    if not name:
        return False, None

    # 1) Direct delete-like tool names
    if name in delete_tools:
        return True, f"tool `{name}` is a delete-like tool"

    # 2) Action-based delete-like operations
    action = args_dict.get("action")
    if isinstance(action, str) and action.strip():
        action_norm = action.strip().lower()
        allowed_delete_actions = delete_actions_by_tool.get(name, [])
        if action_norm in allowed_delete_actions:
            return True, f"tool `{name}` with action `{action_norm}` is delete-like"

    # 3) exec-based explicit delete commands
    if name == "exec":
        command = _extract_command(args_dict)
        matched = _match_exec_delete(command, exec_delete_patterns)
        if matched is not None:
            return True, f"exec command contains delete-like pattern `{matched}`"

    return False, None


def _friendly_delete_reason(tool_name: str, reason: str | None) -> str:
    text = (reason or "").strip()
    lines: List[str] = [
        f"我没有执行工具 `{tool_name}`。",
        "原因：这一步包含删除语义的操作，而当前 delete policy 禁止任何删除行为。"
    ]
    if text:
        lines.append(f"补充说明：{text}")
    lines.append("如果你确实要继续，请通过 kernel 的 approval 流程显式确认。")
    return "\n".join(lines)


class DeletePolicy(Policy):
    """
    Block any delete-like operation.
    This is intended to be optional and approval-friendly:
    once blocked, the kernel's existing approval flow can take over.
    """

    def check(
        self,
        instructions: List[Dict[str, Any]],
        current_response: Dict[str, Any],
        latest_instructions: List[Dict[str, Any]],
        trace_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> PolicyCheckResult:
        delete_tools, delete_actions_by_tool, exec_delete_patterns, enabled = _get_delete_policy_config()
        if not enabled:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        response = dict(current_response)
        tool_calls = RUNTIME.extract_tool_calls(response)
        if not tool_calls:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        errors: List[str] = []
        kept: List[Dict[str, Any]] = []

        for tc in tool_calls:
            tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}
            args_dict = canonicalize_args(args_dict)

            is_delete_op, reason = _classify_delete_operation(tool_name, args_dict)

            if is_delete_op:
                errors.append(_friendly_delete_reason(tool_name, reason))
                RUNTIME.audit(
                    phase="policy.delete",
                    trace_id=trace_id,
                    tool=tool_name,
                    decision="BLOCK",
                    reason=reason or "delete-like operation blocked",
                    args=args_dict,
                )
            else:
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))

        if errors:
            response["tool_calls"] = kept if kept else None
            if not kept:
                response["function_call"] = None
                if not isinstance(response.get("content"), str) or not response.get("content"):
                    response["content"] = "\n\n".join(errors[:3])
            return PolicyCheckResult(
                modified=True,
                response=response,
                error_type="\n\n".join(errors),
            )

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)