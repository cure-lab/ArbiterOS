from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from arbiteros_kernel.policy_runtime import get_runtime


PROMPT_COMPACTION_MARKER = "[arbiteros_prompt_compaction]"


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
    return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return _to_bool(raw, default) if raw is not None else default


def _runtime_cfg() -> dict[str, Any]:
    try:
        cfg = get_runtime().cfg
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        return {}
    cost_down = cfg.get("cost_down")
    if not isinstance(cost_down, dict):
        return {}
    phase3 = cost_down.get("phase3_runtime")
    return phase3 if isinstance(phase3, dict) else {}


def _phase3_runtime_enabled(cfg: dict[str, Any]) -> bool:
    return _env_bool("ARBITEROS_COST_DOWN_PHASE3_RUNTIME", _to_bool(cfg.get("enabled"), False))


def _load_policy(cfg: dict[str, Any]) -> dict[str, Any]:
    for key in ("policy", "runtime_policy", "phase3_policy"):
        value = cfg.get(key)
        if isinstance(value, dict):
            return value
    path_raw = (
        os.getenv("ARBITEROS_COST_DOWN_PHASE3_POLICY_PATH")
        or str(
            cfg.get("policy_path")
            or cfg.get("runtime_policy_path")
            or cfg.get("phase3_policy_path")
            or ""
        )
    ).strip()
    if not path_raw:
        return {}
    try:
        loaded = json.loads(
            Path(os.path.expandvars(os.path.expanduser(path_raw))).read_text(encoding="utf-8")
        )
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _policy_prompt_scaffold_enabled(cfg: dict[str, Any]) -> bool:
    policy = _load_policy(cfg)
    execution = policy.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    defaults = execution.get("runtime_defaults")
    defaults = defaults if isinstance(defaults, dict) else {}
    scaffold = defaults.get("prompt_scaffold_compaction")
    scaffold = scaffold if isinstance(scaffold, dict) else {}
    return _to_bool(scaffold.get("enabled"), False)


def _prompt_scaffold_compaction_enabled() -> bool:
    raw = os.getenv("ARBITEROS_COST_DOWN_AGENT_SCAFFOLD_COMPACTION")
    if raw is not None:
        return _to_bool(raw, False)
    cfg = _runtime_cfg()
    return _phase3_runtime_enabled(cfg) and _policy_prompt_scaffold_enabled(cfg)


def _request_has_tool_result(request_data: dict[str, Any]) -> bool:
    messages = request_data.get("messages")
    if not isinstance(messages, list):
        return False
    return any(
        isinstance(message, dict) and message.get("role") == "tool"
        for message in messages
    )


def _compact_between_headers(
    text: str,
    *,
    start_header: str,
    end_header: str,
    replacement: str,
) -> str:
    pattern = re.compile(
        rf"(?ms)^## {re.escape(start_header)}\s*\n.*?(?=^## {re.escape(end_header)}\s*$)"
    )
    return pattern.sub(replacement.rstrip() + "\n\n", text)


def _compact_section_until_any_header(
    text: str,
    *,
    start_header: str,
    end_headers: tuple[str, ...],
    replacement: str,
) -> str:
    end_patterns = [rf"^## {re.escape(header)}\s*$" for header in end_headers]
    end_patterns.append(r"^</instructions>\s*$")
    pattern = re.compile(
        rf"(?ms)^## {re.escape(start_header)}\s*\n.*?(?={'|'.join(end_patterns)})"
    )
    return pattern.sub(replacement.rstrip() + "\n\n", text)


def compact_agent_scaffold_text(text: str) -> tuple[str, dict[str, Any]]:
    """Compact common coding-agent scaffolding while keeping task constraints."""

    if (
        "## Recommended Workflow" not in text
        or "## Command Execution Rules" not in text
        or "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" not in text
    ):
        return text, {}

    original = text
    actions: list[str] = []
    mode = os.getenv("ARBITEROS_COST_DOWN_AGENT_SCAFFOLD_COMPACTION_MODE", "full")
    mode = (mode or "full").strip().lower()
    compact_behavioral_sections = mode not in {"mechanical", "safe", "submission_only"}

    if compact_behavioral_sections:
        workflow = (
            "## Recommended Workflow\n\n"
            "Read the relevant code, edit only necessary source file(s), run the "
            "focused/required test, inspect the diff, then submit if it passes. Avoid "
            "extra repro scripts or broad scans unless needed."
        )
        updated = _compact_between_headers(
            text,
            start_header="Recommended Workflow",
            end_header="Command Execution Rules",
            replacement=workflow,
        )
        if updated != text:
            actions.append("compact_recommended_workflow")
        text = updated

        command_rules = (
            "## Command Execution Rules\n\n"
            "Each response must include brief reasoning and at least one bash tool "
            "call. Commands run in fresh subshells, so use explicit paths or inline "
            "environment variables when needed."
        )
        if _env_bool("ARBITEROS_COST_DOWN_AGENT_SCAFFOLD_TOOL_HYGIENE", False):
            command_rules += (
                " Do not assume rg or apply_patch exists in benchmark containers; "
                "prefer targeted grep/sed/python, and use find only with narrow "
                "path or name filters."
            )
        updated = _compact_section_until_any_header(
            text,
            start_header="Command Execution Rules",
            end_headers=("Useful command examples", "Environment Details", "Submission"),
            replacement=command_rules,
        )
        if updated != text:
            actions.append("compact_command_rules")
        text = updated

    updated = re.sub(
        r"(?ms)^Example of a CORRECT response:\s*\n<example_response>\s*.*?^</example_response>\s*\n*",
        "",
        text,
    )
    if updated != text:
        actions.append("remove_response_example")
    text = updated

    environment = (
        "## Environment Details\n\n"
        "Use non-interactive shell commands. Avoid interactive editors. Install "
        "missing tools only if necessary."
    )
    updated = _compact_section_until_any_header(
        text,
        start_header="Environment Details",
        end_headers=("Submission",),
        replacement=environment,
    )
    if updated != text:
        actions.append("compact_environment_details")
    text = updated

    updated = re.sub(r"(?ms)^<system_information>\s*\n.*?^</system_information>\s*\n*", "", text)
    if updated != text:
        actions.append("remove_system_information")
    text = updated

    useful_commands = (
        "## Useful command examples\n\n"
        "Use ordinary shell commands to inspect files, edit the requested source, "
        "run the required test, and submit."
    )
    updated = re.sub(
        r"(?ms)^## Useful command examples\s*\n.*?(?=^## |\Z)",
        useful_commands.rstrip() + "\n",
        text,
    )
    if updated != text:
        actions.append("compact_command_examples")
    text = updated

    submission = (
        "## Submission\n\n"
        "When finished, create `patch.txt` from only the modified source files, "
        "inspect it, then submit in a separate final command exactly as:\n\n"
        "```bash\n"
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt\n"
        "```\n\n"
        "Do not include tests, helper scripts, generated files, config/build files, "
        "or binaries unless the issue directly requires them. Do not continue "
        "working after submitting."
    )
    updated = _compact_section_until_any_header(
        text,
        start_header="Submission",
        end_headers=(),
        replacement=submission,
    )
    if updated != text:
        actions.append("compact_submission")
    text = updated

    submit_marker = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    if submit_marker in original and submit_marker not in text:
        text = text.rstrip() + (
            "\n\nSubmit with `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` after "
            "the required verification passes.\n"
        )
        actions.append("preserve_submit_marker")

    if text == original:
        return original, {}

    return text, {
        "actions": actions,
        "original_chars": len(original),
        "compacted_chars": len(text),
        "saved_chars": max(0, len(original) - len(text)),
    }


def apply_agent_scaffold_compaction_to_request(
    request_data: dict[str, Any],
    *,
    trace_id: Optional[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compact repeated benchmark/agent instruction scaffolding before the model call."""

    stats: dict[str, Any] = {
        "enabled": _prompt_scaffold_compaction_enabled(),
        "changed": False,
        "action": "agent_scaffold_compaction",
        "messages": 0,
        "original_chars": 0,
        "compacted_chars": 0,
        "saved_chars": 0,
        "estimated_input_tokens_saved": 0,
    }
    if not stats["enabled"] or not isinstance(request_data, dict):
        return request_data, stats

    messages = request_data.get("messages")
    if not isinstance(messages, list):
        return request_data, stats
    if _env_bool("ARBITEROS_COST_DOWN_AGENT_SCAFFOLD_SKIP_FIRST_TURN", False):
        if not _request_has_tool_result(request_data):
            stats["reason"] = "skip_first_turn_before_tool_result"
            return request_data, stats

    changed_messages: list[Any] = []
    changed = False
    actions: dict[str, int] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            changed_messages.append(message)
            continue
        content = message.get("content")
        if not isinstance(content, str):
            changed_messages.append(message)
            continue
        compacted, info = compact_agent_scaffold_text(content)
        if not info:
            changed_messages.append(message)
            continue
        updated_message = dict(message)
        updated_message["content"] = compacted
        changed_messages.append(updated_message)
        changed = True
        stats["messages"] += 1
        stats["original_chars"] += int(info.get("original_chars", 0) or 0)
        stats["compacted_chars"] += int(info.get("compacted_chars", 0) or 0)
        stats["saved_chars"] += int(info.get("saved_chars", 0) or 0)
        for action in info.get("actions") or []:
            actions[str(action)] = actions.get(str(action), 0) + 1

    if not changed:
        return request_data, stats

    stats["changed"] = True
    stats["estimated_input_tokens_saved"] = max(0, (int(stats["saved_chars"]) + 3) // 4)
    stats["actions"] = actions

    data = dict(request_data)
    data["messages"] = changed_messages
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    metadata = dict(metadata)
    metadata["arbiteros_prompt_compaction"] = {
        "marker": PROMPT_COMPACTION_MARKER,
        "trace_id": trace_id,
        "action": stats["action"],
        "messages": stats["messages"],
        "original_chars": stats["original_chars"],
        "compacted_chars": stats["compacted_chars"],
        "saved_chars": stats["saved_chars"],
        "estimated_input_tokens_saved": stats["estimated_input_tokens_saved"],
        "actions": actions,
    }
    data["metadata"] = metadata
    return data, stats


__all__ = [
    "PROMPT_COMPACTION_MARKER",
    "apply_agent_scaffold_compaction_to_request",
    "compact_agent_scaffold_text",
]
