from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Tuple

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME

from .policy import Policy


_PATH_KEYS = {
    "path",
    "file_path",
    "file",
    "filename",
    "src",
    "dst",
    "directory",
    "dir",
}


def _friendly_path_budget_reason(tool_name: str, reason: str | None) -> str:
    text = (reason or "").strip()
    lower = text.lower()

    if "length" in lower or "too long" in lower or "max_str_len" in lower:
        lines = [
            f"我没有执行工具 `{tool_name}`。",
            "原因：这次请求中的参数内容过长，超过了当前策略允许的长度限制。",
        ]
        if text:
            lines.append(f"补充说明：{text}")
        lines.append("请缩短输入内容，或把操作拆成更小的几步后再试。")
        return "\n".join(lines)

    lines = [
        f"我没有执行工具 `{tool_name}`。",
        "原因：这次请求访问了当前策略明确禁止的路径。",
    ]
    if text:
        lines.append(f"补充说明：{text}")
    lines.append("请改用非受限路径后再试。")
    return "\n".join(lines)


def _iter_string_values(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
        return

    if isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_string_values(v)
        return

    if isinstance(obj, list):
        for v in obj:
            yield from _iter_string_values(v)
        return


def _iter_candidate_paths(obj: Any) -> Iterable[str]:
    """
    Recursively collect explicit path-like argument fields.
    We only treat well-known path keys as filesystem paths.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k in _PATH_KEYS and isinstance(v, str):
                yield v
            else:
                yield from _iter_candidate_paths(v)
        return

    if isinstance(obj, list):
        for v in obj:
            yield from _iter_candidate_paths(v)
        return


def _normalize_path(p: str) -> str:
    if not isinstance(p, str):
        return ""
    s = os.path.expandvars(os.path.expanduser(p.strip()))
    s = s.replace("\\", "/")
    while "//" in s:
        s = s.replace("//", "/")
    return s.rstrip("/") or "/"


def _rule_matches_path(rule: str, path: str) -> bool:
    rule = (rule or "").strip()
    if not rule:
        return False

    norm_path = _normalize_path(path)

    if rule.startswith("re:"):
        pattern = rule[3:]
        try:
            return re.match(pattern, norm_path) is not None
        except re.error:
            return False

    norm_rule = _normalize_path(rule)
    if norm_rule == "/":
        return True

    return norm_path == norm_rule or norm_path.startswith(norm_rule + "/")


def _check_input_budget(args_dict: Dict[str, Any]) -> Tuple[bool, str | None]:
    budget_cfg = RUNTIME.cfg.get("input_budget", {}) or {}
    max_len = int(budget_cfg.get("max_str_len", 0) or 0)
    if max_len <= 0:
        return True, None

    for s in _iter_string_values(args_dict):
        if len(s) > max_len:
            return False, f"string length exceeds input_budget.max_str_len ({len(s)} > {max_len})"

    return True, None


def _is_allowed_path(path: str, allow_prefixes: List[str]) -> bool:
    if not allow_prefixes:
        return False

    norm_path = _normalize_path(path)
    for rule in allow_prefixes:
        if isinstance(rule, str) and _rule_matches_path(rule, norm_path):
            return True
    return False


def _check_path_rules(args_dict: Dict[str, Any]) -> Tuple[bool, str | None]:
    """
    Path rule order:
    1. If a path matches allow_prefixes, skip deny check for that path.
    2. Otherwise, if it matches deny_prefixes, block it.
    3. If neither matches, allow it.

    Empty allow_prefixes means whitelist is effectively disabled.
    """
    paths_cfg = RUNTIME.cfg.get("paths", {}) or {}

    allow_prefixes = paths_cfg.get("allow_prefixes") or []
    deny_prefixes = paths_cfg.get("deny_prefixes") or []

    if not isinstance(allow_prefixes, list):
        allow_prefixes = []
    if not isinstance(deny_prefixes, list):
        deny_prefixes = []

    for path in _iter_candidate_paths(args_dict):
        norm_path = _normalize_path(path)

        # Whitelist has higher priority for this specific path.
        if _is_allowed_path(norm_path, allow_prefixes):
            continue

        for rule in deny_prefixes:
            if isinstance(rule, str) and _rule_matches_path(rule, norm_path):
                return False, f"path matches denied prefix: {rule} ({norm_path})"

    return True, None


class PathBudgetPolicy(Policy):
    """
    Minimal path policy:
    - enforce input_budget.max_str_len
    - support optional allow_prefixes whitelist
    - if a specific path matches allow_prefixes, skip deny check for that path
    - otherwise only block explicit deny_prefixes
    - do not distinguish tools
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
        response = dict(current_response)
        tool_calls = RUNTIME.extract_tool_calls(response)
        if not tool_calls:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        errors: List[str] = []
        kept: List[Dict[str, Any]] = []
        changed = False

        for tc in tool_calls:
            tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}

            # Keep canonicalization so relative paths / aliases are normalized first.
            args_dict = RUNTIME.canonicalize_tool_args(args_dict)

            ok_budget, reason_budget = _check_input_budget(args_dict)
            if not ok_budget:
                ok, reason = False, reason_budget
            else:
                ok_path, reason_path = _check_path_rules(args_dict)
                ok, reason = ok_path, reason_path

            new_tc = RUNTIME.write_back_tool_args(tc, args_dict, was_json_str)
            if new_tc != tc:
                changed = True

            if ok:
                kept.append(new_tc)
            else:
                errors.append(_friendly_path_budget_reason(tool_name, reason))
                RUNTIME.audit(
                    phase="policy.path_budget",
                    trace_id=trace_id,
                    tool=tool_name,
                    decision="BLOCK",
                    reason=reason,
                    args=args_dict,
                )

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

        if changed:
            response["tool_calls"] = kept
            return PolicyCheckResult(modified=True, response=response, error_type=None)

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)