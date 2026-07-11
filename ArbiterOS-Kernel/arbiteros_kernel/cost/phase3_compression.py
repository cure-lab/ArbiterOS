from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from arbiteros_kernel.policy_runtime import get_runtime


PHASE3_MARKER = "[arbiteros_phase3_runtime_cost_down]"
LLMCompressor = Callable[..., Optional[str]]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v >= 0 else default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return v if v >= 0 else default


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


def _env_int(name: str, default: int = 0) -> int:
    raw = os.getenv(name)
    return _to_int(raw, default) if raw is not None else default


def _env_float(name: str, default: float = 0.0) -> float:
    raw = os.getenv(name)
    return _to_float(raw, default) if raw is not None else default


def _env_str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    return raw.strip() if isinstance(raw, str) and raw.strip() else default


def _normalize_choice(value: Any, *, default: str, allowed: set[str]) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    return raw if raw in allowed else default


def _stringify_for_budget(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def estimate_request_input_tokens(request_data: dict[str, Any]) -> int:
    if not isinstance(request_data, dict):
        return 0
    char_count = 0
    messages = request_data.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            char_count += len(_stringify_for_budget(msg.get("role")))
            char_count += len(_stringify_for_budget(msg.get("content")))
            for key in ("tool_calls", "function_call", "name"):
                if key in msg:
                    char_count += len(_stringify_for_budget(msg.get(key)))
    else:
        for key in ("input", "instructions", "prompt"):
            if key in request_data:
                char_count += len(_stringify_for_budget(request_data.get(key)))

    for key in ("tools", "response_format"):
        if key in request_data:
            char_count += len(_stringify_for_budget(request_data.get(key)))
    return max(0, (char_count + 3) // 4)


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
    block = cost_down.get("phase3_runtime")
    return block if isinstance(block, dict) else {}


def _runtime_enabled(cfg: dict[str, Any]) -> bool:
    return _env_bool("ARBITEROS_COST_DOWN_PHASE3_RUNTIME", _to_bool(cfg.get("enabled"), False))


def _rule_enabled(cfg: dict[str, Any]) -> bool:
    return _env_bool(
        "ARBITEROS_COST_DOWN_PHASE3_RULE_COMPRESSION",
        _to_bool(cfg.get("enable_rule_compression"), False),
    )


def _llm_enabled(cfg: dict[str, Any]) -> bool:
    return _env_bool(
        "ARBITEROS_COST_DOWN_PHASE3_LLM_COMPRESSION",
        _to_bool(cfg.get("enable_llm_compression"), False),
    )


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
        with Path(os.path.expandvars(os.path.expanduser(path_raw))).open(
            "r", encoding="utf-8"
        ) as handle:
            loaded = json.load(handle)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


_ANCHOR_RE = re.compile(
    r"(\b(?:passed|failed|errors?|failures?|skipped)\b|"
    r"\bpytest\b|\bunittest\b|\.py::|nodeid|traceback|assert|"
    r"\bshort test summary info\b|<returncode>\s*[1-9])",
    re.IGNORECASE,
)
_SOURCE_LIKE_RE = re.compile(
    r"(^\s*(?:def|class|import|from|if|elif|else:|for|while|try:|except|with|"
    r"return|raise|yield|async\s+def)\b|"
    r"^[\w./-]+\.(?:py|pyi|js|ts|tsx|jsx|go|rs|java|c|cc|cpp|h|hpp|md|rst|"
    r"toml|yaml|yml|json|ini|cfg):\d*:|"
    r"^(?:diff --git|@@ |--- |\+\+\+ ))",
    re.IGNORECASE,
)
_TEST_FIXTURE_RE = re.compile(
    r"(^\s*(?:rule|test_(?:pass|fail)|pass_str|fail_str|fix_str|violations|"
    r"configs?|skip|xfail)\s*:|"
    r"\b(?:test_pass|test_fail|pass_str|fail_str|fix_str|violations)\b)",
    re.IGNORECASE | re.MULTILINE,
)
_EDIT_SIGNAL_RE = re.compile(
    r"(\bapply_patch\b|\bgit\s+diff\b|\bpatch\.txt\b|\bpath\.write_text\b|"
    r"\bwrite_text\(|\bopen\([^\\n]{0,120},\s*['\"][wa]\b|"
    r"\bsed\s+-i\b|\bperl\s+-0?pi\b|"
    r"\bcat\s+>|diff --git|COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT)",
    re.IGNORECASE,
)
_NOISY_TOOL_RE = re.compile(
    r"(\btraceback\b|\bpytest\b|\b(?:failed|passed|errors?|failures?|skipped)\b|"
    r"\bassert(?:ion)?\b|\bwarning\b|\bexception\b|<warning>|"
    r"\bomitted .* repeated|<returncode>\s*[1-9])",
    re.IGNORECASE,
)
_LOW_VALUE_SEARCH_NOISE_LINE_RE = re.compile(
    r"("
    r"\bbinary file matches\b|"
    r"(?:^|/|\s)(?:[^/\s]+/)*locale/[^/\s]+/LC_MESSAGES/|"
    r"(?:^|/)LC_MESSAGES/|"
    r"\.(?:mo|po)(?::|\b)"
    r")",
    re.IGNORECASE,
)
_TOOL_OUTPUT_WRAPPER_RE = re.compile(
    r"\A(?P<prefix>\s*<returncode>.*?</returncode>\s*"
    r"(?:<warning>.*?</warning>\s*)?<output>\n?)"
    r"(?P<body>.*?)(?P<suffix>\n?</output>\s*)\Z",
    re.DOTALL,
)
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_PATH_FOCUS_RE = re.compile(
    r"[A-Za-z0-9_./-]+\.(?:py|pyi|js|ts|tsx|jsx|go|rs|java|c|cc|cpp|h|hpp|"
    r"md|rst|toml|yaml|yml|json|ini|cfg)(?:::[A-Za-z0-9_./:-]+)?"
)
_PYTEST_NODE_RE = re.compile(r"[\w./-]+\.py::[A-Za-z0-9_:\[\]./-]+")
_FAILED_COUNT_RE = re.compile(
    r"\b(?:=+\s*)?(?:(\d+)\s+failed|(\d+)\s+passed|(\d+)\s+errors?|"
    r"(\d+)\s+skipped)\b",
    re.IGNORECASE,
)
_TRACEBACK_FRAME_RE = re.compile(r'^\s*File\s+["\'][^"\']+["\'],\s+line\s+\d+', re.IGNORECASE)
_EXCEPTION_LINE_RE = re.compile(
    r"^(?:[A-Za-z_][\w.]*\.)?[A-Za-z_][\w]*(?:Error|Exception):\s+.+$"
)
_RETURN_CODE_RE = re.compile(r"<returncode>\s*([^<]+?)\s*</returncode>", re.DOTALL)
_LOW_VALUE_FOCUS_TERMS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "before",
    "call",
    "command",
    "could",
    "error",
    "file",
    "files",
    "from",
    "have",
    "info",
    "informational",
    "into",
    "last",
    "line",
    "lines",
    "most",
    "need",
    "output",
    "repeated",
    "return",
    "should",
    "short",
    "summary",
    "task",
    "test",
    "that",
    "their",
    "there",
    "this",
    "tool",
    "with",
    "would",
    "warning",
    "warnings",
}


def _content_mode_and_text(message: dict[str, Any]) -> tuple[Optional[str], str]:
    content = message.get("content")
    if isinstance(content, str):
        return "str", content
    if isinstance(content, dict):
        if isinstance(content.get("content"), str):
            return "dict_content", str(content.get("content") or "")
        if isinstance(content.get("text"), str):
            return "dict_text", str(content.get("text") or "")
    if isinstance(content, list) and content and isinstance(content[0], dict):
        item = content[0]
        for key in ("text", "content"):
            if isinstance(item.get(key), str):
                return f"list_0_{key}", str(item.get(key) or "")
    return None, ""


def _set_message_text_content(message: dict[str, Any], mode: Optional[str], text: str) -> None:
    if mode == "str":
        message["content"] = text
        return
    if mode in {"dict_content", "dict_text"}:
        content = message.get("content")
        key = "content" if mode == "dict_content" else "text"
        if isinstance(content, dict):
            updated = dict(content)
            updated[key] = text
            message["content"] = updated
        return
    if mode and mode.startswith("list_0_"):
        content = message.get("content")
        key = mode.removeprefix("list_0_")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            updated = dict(content[0])
            updated[key] = text
            message["content"] = [updated, *content[1:]]


def _context_id(message: dict[str, Any]) -> Optional[str]:
    for key in ("context_id", "span_id"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        for key in ("context_id", "span_id"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    content = message.get("content")
    if isinstance(content, dict):
        for key in ("context_id", "span_id"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _tool_call_id(message: dict[str, Any]) -> Optional[str]:
    for key in ("tool_call_id", "id"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _reference_tool_ids_from_tool_call(tool_call: dict[str, Any]) -> set[str]:
    candidates: list[Any] = []
    for key in ("reference_tool_id", "reference_tool_ids", "depends_on_tool_call_ids"):
        if key in tool_call:
            candidates.append(tool_call.get(key))
    function = tool_call.get("function")
    if isinstance(function, dict):
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                for key in (
                    "reference_tool_id",
                    "reference_tool_ids",
                    "depends_on_tool_call_ids",
                ):
                    if key in parsed:
                        candidates.append(parsed.get(key))
        elif isinstance(arguments, dict):
            for key in (
                "reference_tool_id",
                "reference_tool_ids",
                "depends_on_tool_call_ids",
            ):
                if key in arguments:
                    candidates.append(arguments.get(key))
    out: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            out.add(candidate.strip())
        elif isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, str) and item.strip():
                    out.add(item.strip())
    return out


_GLOBAL_REF_RE = re.compile(r"\[ARBITEROS_REF id=([^\s\]]+)")


class GlobalDependencyIndex:
    """Read the ArbiterOS instruction graph for runtime evidence lifetime.

    The older Phase 3 path only sees tool-call references embedded in the
    request. The newer ArbiterOS branch also records instruction-level
    ``depends_on`` edges. This adapter consumes those edges without changing
    the existing compression strategies: the newest graph frontier is live,
    its transitive ancestors are protected evidence, and everything outside
    that closure becomes eligible for a compact digest.
    """

    def __init__(
        self,
        instructions: list[dict[str, Any]],
        *,
        active_tool_ids: Optional[set[str]] = None,
    ):
        self.instructions = [item for item in instructions if isinstance(item, dict)]
        self.active_tool_ids = {
            item.strip()
            for item in (active_tool_ids or set())
            if isinstance(item, str) and item.strip()
        }
        self.nodes: dict[str, dict[str, Any]] = {}
        self.parents: dict[str, set[str]] = {}
        self.children: dict[str, set[str]] = {}
        self.call_by_tool_id: dict[str, str] = {}
        self.result_by_tool_id: dict[str, str] = {}
        self.step_by_id: dict[str, int] = {}
        self.live_ids: set[str] = set()
        self.tool_state: dict[str, dict[str, Any]] = {}
        self._build()

    @staticmethod
    def _id(value: Any) -> Optional[str]:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _tool_id(instruction: dict[str, Any]) -> Optional[str]:
        content = instruction.get("content")
        if not isinstance(content, dict):
            return None
        return GlobalDependencyIndex._id(content.get("tool_call_id"))

    @staticmethod
    def _is_result(instruction: dict[str, Any]) -> bool:
        content = instruction.get("content")
        return isinstance(content, dict) and content.get("result") is not None

    @classmethod
    def _dependency_ids(cls, instruction: dict[str, Any]) -> list[str]:
        raw = instruction.get("depends_on")
        if not isinstance(raw, list):
            return []
        ids: list[str] = []
        for item in raw:
            value = item.get("instruction_id") if isinstance(item, dict) else item
            value = value or (item.get("ref") if isinstance(item, dict) else None)
            item_id = cls._id(value)
            if item_id:
                ids.append(item_id)
        return list(dict.fromkeys(ids))

    def _build(self) -> None:
        for instruction in self.instructions:
            instruction_id = self._id(instruction.get("id"))
            if not instruction_id:
                continue
            self.nodes[instruction_id] = instruction
            raw_step = instruction.get("runtime_step")
            self.step_by_id[instruction_id] = (
                int(raw_step) if isinstance(raw_step, (int, float)) else 0
            )
            tool_id = self._tool_id(instruction)
            if tool_id:
                if self._is_result(instruction):
                    self.result_by_tool_id[tool_id] = instruction_id
                else:
                    self.call_by_tool_id[tool_id] = instruction_id

        for instruction_id, instruction in self.nodes.items():
            parent_ids = [
                parent_id
                for parent_id in self._dependency_ids(instruction)
                if parent_id in self.nodes and parent_id != instruction_id
            ]
            tool_id = self._tool_id(instruction)
            if self._is_result(instruction) and tool_id:
                call_id = self.call_by_tool_id.get(tool_id)
                if call_id and call_id not in parent_ids:
                    parent_ids.append(call_id)
            self.parents[instruction_id] = set(parent_ids)
            for parent_id in parent_ids:
                self.children.setdefault(parent_id, set()).add(instruction_id)

        dynamic = []
        for instruction_id, instruction in self.nodes.items():
            kind = str(
                instruction.get("arbiteros_ref_kind")
                or instruction.get("instruction_type")
                or ""
            ).upper()
            if kind not in {"SYSTEMPROMPT", "USERINPUT"}:
                dynamic.append(instruction_id)
        dynamic.sort(key=lambda item: self.step_by_id.get(item, 0))
        active_dynamic = {
            node_id
            for tool_id in self.active_tool_ids
            for node_id in (
                self.call_by_tool_id.get(tool_id),
                self.result_by_tool_id.get(tool_id),
            )
            if node_id is not None
        }
        scoped_dynamic = sorted(
            active_dynamic,
            key=lambda item: self.step_by_id.get(item, 0),
        )
        frontier = (scoped_dynamic or dynamic)[-2:]
        pending = list(frontier)
        while pending:
            current = pending.pop()
            if current in self.live_ids:
                continue
            self.live_ids.add(current)
            pending.extend(self.parents.get(current, set()))

        latest_step = max(self.step_by_id.values(), default=0)
        for tool_id, call_id in self.call_by_tool_id.items():
            node_ids = {call_id}
            result_id = self.result_by_tool_id.get(tool_id)
            if result_id:
                node_ids.add(result_id)
            child_ids = {
                child_id
                for node_id in node_ids
                for child_id in self.children.get(node_id, set())
            }
            downstream_child_ids = child_ids - node_ids
            last_use_step = max(
                (
                    self.step_by_id.get(child_id, 0)
                    for child_id in downstream_child_ids
                ),
                default=0,
            )
            protected = any(
                bool(self.nodes[node_id].get("policy_protected"))
                for node_id in node_ids
                if node_id in self.nodes
            )
            self.tool_state[tool_id] = {
                "state": "live" if node_ids & self.live_ids else "expired",
                "fanout": len(downstream_child_ids),
                "last_use_step": last_use_step,
                "latest_step": latest_step,
                "protected": protected,
                "node_ids": sorted(node_ids),
            }

    def diagnostics(self) -> dict[str, Any]:
        return {
            "instruction_nodes": len(self.nodes),
            "dependency_edges": sum(len(items) for items in self.parents.values()),
            "request_tool_ids": len(self.active_tool_ids),
            "matched_request_tool_nodes": sum(
                1
                for tool_id in self.active_tool_ids
                if tool_id in self.call_by_tool_id or tool_id in self.result_by_tool_id
            ),
            "frontier_scope": "current_request_tools" if self.active_tool_ids else "global_tail",
            "frontier_nodes": sorted(
                self.live_ids,
                key=lambda item: self.step_by_id.get(item, 0),
            )[-2:],
            "live_nodes": len(self.live_ids),
            "tool_nodes": len(self.tool_state),
            "expired_tool_nodes": sum(
                1 for item in self.tool_state.values() if item["state"] == "expired"
            ),
        }


def _tool_call_arguments(tool_call: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Any]:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return None, None
    raw_arguments = function.get("arguments")
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
        except Exception:
            return None, raw_arguments
        return (parsed if isinstance(parsed, dict) else None), raw_arguments
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments), raw_arguments
    return None, raw_arguments


def _reference_argument_subset(arguments: dict[str, Any]) -> dict[str, Any]:
    preserved: dict[str, Any] = {}
    for key in ("reference_tool_id", "reference_tool_ids", "depends_on_tool_call_ids"):
        if key in arguments:
            preserved[key] = arguments[key]
    return preserved


def _fold_repeated_adjacent_lines(text: str) -> str:
    lines = text.splitlines()
    if len(lines) < 3:
        return text
    out: list[str] = []
    previous: Optional[str] = None
    repeats = 0

    def flush_repeats() -> None:
        nonlocal repeats
        if repeats > 0:
            out.append(f"[omitted {repeats} repeated identical line(s)]")
            repeats = 0

    for line in lines:
        if previous is not None and line == previous:
            repeats += 1
            continue
        flush_repeats()
        out.append(line)
        previous = line
    flush_repeats()
    folded = "\n".join(out)
    if text.endswith("\n"):
        folded += "\n"
    return folded


def _wrapped_tool_parts(text: str) -> Optional[tuple[str, str, str]]:
    match = _TOOL_OUTPUT_WRAPPER_RE.match(text)
    if not match:
        return None
    return match.group("prefix"), match.group("body"), match.group("suffix")


def _wrap_tool_body(text: str, body: str) -> str:
    parts = _wrapped_tool_parts(text)
    if parts is None:
        return body
    prefix, _old_body, suffix = parts
    return f"{prefix}{body}{suffix}"


def _tool_body(text: str) -> str:
    parts = _wrapped_tool_parts(text)
    return text if parts is None else parts[1]


def _looks_source_like(text: str) -> bool:
    source_like_lines = 0
    nonempty_lines = 0
    for raw_line in text.splitlines()[:240]:
        line = raw_line.rstrip()
        if not line:
            continue
        nonempty_lines += 1
        if _SOURCE_LIKE_RE.search(line):
            source_like_lines += 1
        if source_like_lines >= 4:
            return True
    return nonempty_lines >= 8 and source_like_lines >= 2


def _is_compactable_assistant(message: dict[str, Any]) -> bool:
    if message.get("role") != "assistant":
        return False
    if message.get("tool_calls") or message.get("function_call"):
        return False
    _mode, text = _content_mode_and_text(message)
    return bool(text)


def _extract_anchors(text: str, *, max_lines: int = 16) -> str:
    anchors: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line in seen:
            continue
        if _ANCHOR_RE.search(line) or _SOURCE_LIKE_RE.search(line):
            seen.add(line)
            anchors.append(line[:240])
        if len(anchors) >= max_lines:
            break
    return "\n".join(anchors)


def _extract_dependency_evidence_anchors(text: str, *, max_lines: int = 10) -> str:
    """Keep root-cause frames when historical evidence is summarized."""

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    frames = [line for line in lines if _TRACEBACK_FRAME_RE.search(line)]
    exceptions = [line for line in lines if _EXCEPTION_LINE_RE.search(line)]
    generic = _extract_anchors(text, max_lines=max_lines).splitlines()
    candidates = [*frames[-6:], *exceptions[-2:], *generic]
    return "\n".join(_unique_preserve_order(candidates, limit=max_lines))


def _extract_invocation_anchors(text: str, *, max_items: int = 12) -> list[str]:
    anchors: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        item = raw.strip()
        if not item or item in seen or len(anchors) >= max_items:
            return
        seen.add(item)
        anchors.append(item[:220])

    for path in _PATH_FOCUS_RE.findall(text):
        add(path)
    for nodeid in _PYTEST_NODE_RE.findall(text):
        add(nodeid)
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if (
            _ANCHOR_RE.search(line)
            or _EDIT_SIGNAL_RE.search(line)
            or _SOURCE_LIKE_RE.search(line)
            or _line_contains_path_like_text(line)
        ):
            add(line)
        if len(anchors) >= max_items:
            break
    return anchors


def _history_has_edit_signal(messages: list[Any]) -> bool:
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") not in {"assistant", "tool"}:
            continue
        fragments: list[str] = []
        content = message.get("content")
        if isinstance(content, str):
            fragments.append(content)
        elif content is not None:
            fragments.append(_stringify_for_budget(content))
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if isinstance(function, dict):
                    fragments.append(_stringify_for_budget(function.get("name")))
                    fragments.append(_stringify_for_budget(function.get("arguments")))
        if _EDIT_SIGNAL_RE.search("\n".join(fragments)):
            return True
    return False


def _add_focus_term(raw: str, terms: list[str], seen: set[str], max_terms: int) -> bool:
    term = raw.strip().strip("`'\"").lower()
    if not term or term in _LOW_VALUE_FOCUS_TERMS or term in seen:
        return False
    seen.add(term)
    terms.append(term)
    return len(terms) >= max_terms


def _extract_focus_terms(messages: list[Any], *, max_terms: int = 80) -> set[str]:
    terms: list[str] = []
    seen: set[str] = set()
    candidates = [
        message
        for message in messages
        if isinstance(message, dict) and message.get("role") == "user"
    ]
    candidates.extend(
        [
            message
            for message in messages
            if isinstance(message, dict)
            and message.get("role") == "assistant"
            and not message.get("tool_calls")
            and not message.get("function_call")
        ][-2:]
    )
    candidates.extend(
        [
            message
            for message in messages
            if isinstance(message, dict)
            and message.get("role") == "assistant"
            and isinstance(message.get("tool_calls"), list)
        ][-4:]
    )
    recent_tool_evidence = []
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        _mode, text = _content_mode_and_text(message)
        body = _tool_body(text)
        if not body or _looks_source_like(body):
            continue
        if _ANCHOR_RE.search(body):
            recent_tool_evidence.append(message)
        if len(recent_tool_evidence) >= 2:
            break
    candidates.extend(reversed(recent_tool_evidence))

    for message in candidates:
        text_fragments: list[str] = []
        _mode, text = _content_mode_and_text(message)
        if text:
            text_fragments.append(text)
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
        if isinstance(tool_calls, list):
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    continue
                text_fragments.append(_stringify_for_budget(function.get("name")))
                text_fragments.append(_stringify_for_budget(function.get("arguments")))
        for text in text_fragments:
            if not text:
                continue
            for path in _PATH_FOCUS_RE.findall(text):
                if _add_focus_term(path, terms, seen, max_terms):
                    return set(terms)
                basename = path.rsplit("/", 1)[-1]
                if basename != path and _add_focus_term(basename, terms, seen, max_terms):
                    return set(terms)
            for raw in _IDENTIFIER_RE.findall(text):
                if _add_focus_term(raw, terms, seen, max_terms):
                    return set(terms)
    return set(terms)


def _line_matches_focus_terms(line: str, focus_terms: set[str]) -> bool:
    if not focus_terms:
        return False
    lower = line.lower()
    return any(term in lower for term in focus_terms)


def _line_contains_path_like_text(line: str) -> bool:
    return bool(_PATH_FOCUS_RE.search(line))


def _low_value_search_noise_lines(text: str) -> tuple[list[str], list[str]]:
    noisy: list[str] = []
    kept: list[str] = []
    for raw_line in _tool_body(text).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _LOW_VALUE_SEARCH_NOISE_LINE_RE.search(line):
            noisy.append(line)
        else:
            kept.append(line)
    return noisy, kept


def _is_low_value_search_noise(text: str) -> bool:
    noisy, kept = _low_value_search_noise_lines(text)
    if not noisy:
        return False
    if len(noisy) >= 6:
        return True
    if len(noisy) >= 3 and len(_tool_body(text)) >= 1200:
        return True
    return len(noisy) > len(kept) and len(noisy) >= 2


def _unique_preserve_order(items: list[str], *, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        item = raw.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= limit:
            break
    return out


@dataclass(frozen=True)
class Phase3Config:
    raw: dict[str, Any]
    policy: dict[str, Any]
    context_policies: dict[str, dict[str, Any]]
    runtime_defaults: dict[str, Any]
    enabled: bool
    rule_enabled: bool
    llm_enabled: bool

    @classmethod
    def load(cls) -> "Phase3Config":
        raw = _runtime_cfg()
        policy = _load_policy(raw)
        contexts = cls._context_policies(policy)
        runtime_defaults = cls._runtime_defaults(policy)
        return cls(
            raw=raw,
            policy=policy,
            context_policies=contexts,
            runtime_defaults=runtime_defaults,
            enabled=_runtime_enabled(raw),
            rule_enabled=_rule_enabled(raw),
            llm_enabled=_llm_enabled(raw),
        )

    @staticmethod
    def _context_policies(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw = policy.get("context_policies")
        if not isinstance(raw, dict):
            return {}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}

    @staticmethod
    def _runtime_defaults(policy: dict[str, Any]) -> dict[str, Any]:
        execution = policy.get("execution")
        if not isinstance(execution, dict):
            return {}
        raw = execution.get("runtime_defaults")
        return dict(raw) if isinstance(raw, dict) else {}

    def _configured_default(self, key: str, default: Any) -> Any:
        if key in self.raw:
            return self.raw.get(key)
        if key in self.runtime_defaults:
            return self.runtime_defaults.get(key)
        return default

    def env_bool(self, name: str, key: str, default: bool = False) -> bool:
        return _env_bool(name, _to_bool(self._configured_default(key, default), default))

    def env_int(self, name: str, key: str, default: int = 0) -> int:
        return _env_int(name, _to_int(self._configured_default(key, default), default))

    def env_float(self, name: str, key: str, default: float = 0.0) -> float:
        return _env_float(name, _to_float(self._configured_default(key, default), default))

    def reflection_default(self, key: str, default: Any) -> Any:
        raw_reflection = self.raw.get("llm_reflection")
        if isinstance(raw_reflection, dict) and key in raw_reflection:
            return raw_reflection.get(key)
        runtime_key = f"reflection_{key}"
        if runtime_key in self.runtime_defaults:
            return self.runtime_defaults.get(runtime_key)

        execution = self.policy.get("execution")
        execution = execution if isinstance(execution, dict) else {}
        llm = execution.get("llm_compression")
        llm = llm if isinstance(llm, dict) else {}
        if key in llm:
            return llm.get(key)

        trigger = llm.get("trigger")
        if isinstance(trigger, dict) and key in trigger:
            return trigger.get(key)
        window = llm.get("context_window")
        if isinstance(window, dict) and key in window:
            return window.get(key)
        guard = llm.get("replacement_guard")
        if isinstance(guard, dict) and key in guard:
            return guard.get(key)
        return default

    def reflection_bool(self, env_name: str, key: str, default: bool = False) -> bool:
        return _env_bool(env_name, _to_bool(self.reflection_default(key, default), default))

    def reflection_int(self, env_name: str, key: str, default: int = 0) -> int:
        return _env_int(env_name, _to_int(self.reflection_default(key, default), default))

    def reflection_float(self, env_name: str, key: str, default: float = 0.0) -> float:
        return _env_float(env_name, _to_float(self.reflection_default(key, default), default))

    def llm_position(self) -> str:
        configured = self._configured_default(
            "llm_position",
            self.reflection_default("position", "before_rule"),
        )
        raw = _env_str("ARBITEROS_COST_DOWN_PHASE3_LLM_POSITION", str(configured or ""))
        return _normalize_choice(
            raw,
            default="before_rule",
            allowed={"before_rule", "after_rule", "hybrid_high_signal"},
        )

    def has_action(self, action: str) -> bool:
        target = action.strip().lower()
        for entry in self.context_policies.values():
            if str(entry.get("action") or "").strip().lower() == target:
                return True
        fallback_action = str(self.runtime_defaults.get("message_fallback_action") or "").strip().lower()
        if target == "compress" and fallback_action == "compress":
            return True
        return False

    def has_runtime_fallback_policy(self) -> bool:
        return _to_bool(
            self.runtime_defaults.get("allow_message_fallback_without_context_policy"),
            False,
        ) and self.has_action("compress")

    def target_ratio(self, context_policy: Optional[dict[str, Any]], progress_signal: Optional[str]) -> float:
        if context_policy is not None:
            ratio = _to_float(context_policy.get("compress_target_ratio"), 0.0)
            if ratio > 0:
                return min(max(ratio, 0.05), 1.0)

        execution = self.policy.get("execution")
        execution = execution if isinstance(execution, dict) else {}
        rule = execution.get("rule_compression")
        rule = rule if isinstance(rule, dict) else {}
        by_signal = rule.get("target_ratio_by_signal")
        if progress_signal and isinstance(by_signal, dict):
            signal_ratio = _to_float(by_signal.get(progress_signal), 0.0)
            if signal_ratio > 0:
                return min(max(signal_ratio, 0.05), 1.0)

        cfg_ratio = _to_float(self._configured_default("default_target_ratio", 0.0), 0.0)
        if cfg_ratio > 0:
            return min(max(cfg_ratio, 0.05), 1.0)
        return min(max(_to_float(rule.get("default_target_ratio"), 0.3), 0.05), 1.0)

    def target_chars(self, text_len: int, ratio: float) -> int:
        min_chars = self.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_MIN_TARGET_CHARS",
            "min_target_chars",
            240,
        )
        max_chars = self.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_MAX_TARGET_CHARS",
            "max_target_chars",
            6000,
        )
        target = max(min_chars, int(text_len * ratio))
        if max_chars > 0:
            target = min(target, max_chars)
        return max(160, target)


@dataclass
class MessageState:
    index: int
    message: dict[str, Any]
    role: str
    mode: Optional[str]
    text: str
    context_id: Optional[str]
    context_policy: Optional[dict[str, Any]]
    source_like: bool
    test_fixture_like: bool
    dependency_protected: bool = False
    dependency_prunable: bool = False
    dependency_expired_reference: bool = False
    dependency_offline_expired_context: bool = False
    dependency_superseded: bool = False
    dependency_unreferenced_stale: bool = False
    global_dependency_prunable: bool = False
    global_dependency_fanout: int = 0
    global_dependency_last_use_step: int = 0
    late_source_like_compaction: bool = False
    stale_tool: bool = False
    stale_assistant: bool = False
    duplicate_later_idx: Optional[int] = None
    near_duplicate_later: Optional[tuple[int, float]] = None
    repeated_line_folded: Optional[str] = None
    fallback_allowed: bool = False
    blocked: bool = False
    match_type: str = "message_fallback"
    target_ratio: float = 0.3
    target_chars: int = 0
    reason: str = ""


@dataclass
class CompressionResult:
    text: str
    action: str
    backend: str


@dataclass
class CompressionRiskBudget:
    enabled: bool = True
    active: bool = False
    conservative_active: bool = False
    reason: str = ""
    min_tool_results: int = 8
    min_input_tokens: int = 14000
    conservative_min_tool_results: int = 12
    conservative_min_input_tokens: int = 16000
    min_target_ratio: float = 0.45
    source_like_min_target_chars: int = 1800
    test_fixture_target_ratio: float = 0.7
    protect_recent_tool_results: int = 5
    disable_duplicate_elision: bool = True
    disable_near_duplicate_elision: bool = True
    source_like_raw_head_tail: bool = True
    protected_tool_indexes: set[int] = field(default_factory=set)


@dataclass
class CompressionPlan:
    data: dict[str, Any]
    messages: list[Any]
    config: Phase3Config
    stats: dict[str, Any]
    global_instructions: list[dict[str, Any]] = field(default_factory=list)
    tool_indexes: list[int] = field(default_factory=list)
    stale_tool_indexes: set[int] = field(default_factory=set)
    stale_assistant_indexes: set[int] = field(default_factory=set)
    focus_terms: set[str] = field(default_factory=set)
    dependency_protected_tool_indexes: set[int] = field(default_factory=set)
    dependency_prunable_tool_indexes: set[int] = field(default_factory=set)
    tool_id_by_index: dict[int, str] = field(default_factory=dict)
    tool_index_by_id: dict[str, int] = field(default_factory=dict)
    referenced_tool_ids: set[str] = field(default_factory=set)
    dependency_edges_by_tool_index: dict[int, list[int]] = field(default_factory=dict)
    dependency_last_reference_index_by_tool_index: dict[int, int] = field(default_factory=dict)
    dependency_expired_reference_tool_indexes: set[int] = field(default_factory=set)
    dependency_superseded_tool_indexes: set[int] = field(default_factory=set)
    dependency_unreferenced_stale_tool_indexes: set[int] = field(default_factory=set)
    dependency_superseding_later_index_by_tool_index: dict[int, int] = field(default_factory=dict)
    dependency_supersede_reason_by_tool_index: dict[int, str] = field(default_factory=dict)
    active_source_like_tool_indexes: set[int] = field(default_factory=set)
    duplicate_latest: dict[int, int] = field(default_factory=dict)
    near_duplicate_latest: dict[int, tuple[int, float]] = field(default_factory=dict)
    edit_signal_seen: bool = False
    fallback_gate_met: bool = True
    source_like_fast_lane_active: bool = False
    source_like_pre_edit_gate_met: bool = True
    pressure_active: bool = False
    pressure_target_ratio: float = 0.0
    source_like_structure: bool = True
    source_like_focus_context_lines: int = 2
    source_like_max_structure_lines_when_focused: int = 24
    source_like_focused_target_ratio: float = 0.18
    source_like_min_target_chars: int = 760
    preserve_test_fixture_outputs: bool = True
    test_fixture_target_ratio: float = 0.55
    semantic_digest_enabled: bool = False
    semantic_digest_hybrid: bool = False
    min_saved_tokens_per_message: int = 0
    dependency_protected_action: str = "compress"
    keep_recent_tool: int = 3
    keep_recent_assistant: int = 2
    risk_budget: CompressionRiskBudget = field(default_factory=CompressionRiskBudget)
    global_dependency_index: Optional[GlobalDependencyIndex] = None
    global_dependency_prunable_tool_indexes: set[int] = field(default_factory=set)
    global_dependency_tool_state_by_index: dict[int, dict[str, Any]] = field(default_factory=dict)


class CompressionStrategy(Protocol):
    def try_compress(self, state: MessageState, plan: CompressionPlan) -> Optional[CompressionResult]:
        ...


class ActionTelemetry:
    @staticmethod
    def add(
        stats: dict[str, Any],
        *,
        action: str,
        role: str,
        backend: str,
        before_chars: int,
        after_chars: int,
        target_ratio: float,
        match_type: str,
        rule_id: Optional[str],
    ) -> None:
        actions = stats.setdefault("actions", {})
        current = actions.get(action)
        if not isinstance(current, dict):
            current = {
                "messages": 0,
                "source_roles": {},
                "backend": backend,
                "original_chars": 0,
                "compacted_chars": 0,
                "saved_chars": 0,
                "target_ratios": [],
                "match_types": {},
                "rule_ids": {},
            }
            actions[action] = current
        current["messages"] = int(current.get("messages", 0) or 0) + 1
        roles = current.setdefault("source_roles", {})
        if isinstance(roles, dict):
            roles[role] = int(roles.get(role, 0) or 0) + 1
        current["original_chars"] = int(current.get("original_chars", 0) or 0) + before_chars
        current["compacted_chars"] = int(current.get("compacted_chars", 0) or 0) + after_chars
        current["saved_chars"] = int(current.get("saved_chars", 0) or 0) + max(
            0, before_chars - after_chars
        )
        ratios = current.setdefault("target_ratios", [])
        if isinstance(ratios, list):
            ratios.append(target_ratio)
        match_types = current.setdefault("match_types", {})
        if isinstance(match_types, dict):
            match_types[match_type] = int(match_types.get(match_type, 0) or 0) + 1
        if rule_id:
            rule_ids = current.setdefault("rule_ids", {})
            if isinstance(rule_ids, dict):
                rule_ids[rule_id] = int(rule_ids.get(rule_id, 0) or 0) + 1


class CandidateIndexBuilder:
    def __init__(self, plan: CompressionPlan):
        self.plan = plan
        self.cfg = plan.config

    def build(self) -> None:
        messages = self.plan.messages
        self.plan.tool_indexes = [
            idx
            for idx, message in enumerate(messages)
            if isinstance(message, dict) and message.get("role") == "tool"
        ]
        self._index_tool_ids()
        self.plan.keep_recent_tool = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_STALE_TOOL_KEEP_RECENT",
            "stale_tool_keep_recent",
            3,
        )
        self.plan.stale_tool_indexes = set(
            self.plan.tool_indexes[
                : max(0, len(self.plan.tool_indexes) - self.plan.keep_recent_tool)
            ]
        )
        assistant_indexes = [
            idx
            for idx, message in enumerate(messages)
            if isinstance(message, dict) and _is_compactable_assistant(message)
        ]
        self.plan.keep_recent_assistant = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_STALE_ASSISTANT_KEEP_RECENT",
            "stale_assistant_keep_recent",
            2,
        )
        self.plan.stale_assistant_indexes = set(
            assistant_indexes[
                : max(0, len(assistant_indexes) - self.plan.keep_recent_assistant)
            ]
        )
        self.plan.focus_terms = _extract_focus_terms(messages)
        self.plan.edit_signal_seen = _history_has_edit_signal(messages)
        self._build_dependency_indexes()
        self._build_global_dependency_indexes()
        self._build_dependency_prunable_indexes()
        self.plan.duplicate_latest = self._duplicate_latest()
        self.plan.near_duplicate_latest = self._near_duplicate_latest()

    def _index_tool_ids(self) -> None:
        for idx in self.plan.tool_indexes:
            message = self.plan.messages[idx]
            if not isinstance(message, dict):
                continue
            tool_id = _tool_call_id(message)
            if not tool_id:
                continue
            self.plan.tool_id_by_index[idx] = tool_id
            self.plan.tool_index_by_id.setdefault(tool_id, idx)

    def _build_dependency_indexes(self) -> None:
        dependency_protection_enabled = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PROTECT_TOOL_OUTPUTS",
            "dependency_protect_tool_outputs",
            True,
        )
        referenced_in_order: list[str] = []
        edges: dict[int, list[int]] = {}
        for message_idx, message in enumerate(self.plan.messages):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                if isinstance(tool_call, dict):
                    referenced_ids = sorted(_reference_tool_ids_from_tool_call(tool_call))
                    referenced_in_order.extend(referenced_ids)
                    for tool_id in referenced_ids:
                        tool_idx = self.plan.tool_index_by_id.get(tool_id)
                        if tool_idx is None or tool_idx >= message_idx:
                            continue
                        edges.setdefault(tool_idx, []).append(message_idx)

        self.plan.referenced_tool_ids = set(referenced_in_order)
        self.plan.dependency_edges_by_tool_index = edges
        self.plan.dependency_last_reference_index_by_tool_index = {
            tool_idx: max(reference_indexes)
            for tool_idx, reference_indexes in edges.items()
            if reference_indexes
        }
        self.plan.stats["dependency_evidence_graph"] = {
            "tool_outputs": len(self.plan.tool_indexes),
            "referenced_tool_outputs": len(edges),
            "reference_edges": sum(len(value) for value in edges.values()),
        }

        if not dependency_protection_enabled:
            self.plan.stats["dependency_evidence_graph"]["protection_enabled"] = False
            return
        self.plan.stats["dependency_evidence_graph"]["protection_enabled"] = True

        keep_recent = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PROTECTED_KEEP_RECENT",
            "dependency_protected_keep_recent",
            2,
        )
        if keep_recent <= 0:
            protected_ids = set(referenced_in_order)
        else:
            protected_ids: set[str] = set()
            for tool_id in reversed(referenced_in_order):
                if tool_id in protected_ids:
                    continue
                protected_ids.add(tool_id)
                if len(protected_ids) >= keep_recent:
                    break

        high_signal_only = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PROTECT_HIGH_SIGNAL_ONLY",
            "dependency_protect_high_signal_only",
            True,
        )
        protected_indexes: set[int] = set()
        final_ids: list[str] = []
        skipped_ids: list[str] = []
        for idx in self.plan.tool_indexes:
            message = self.plan.messages[idx]
            if not isinstance(message, dict):
                continue
            tool_id = _tool_call_id(message)
            if not tool_id or tool_id not in protected_ids:
                continue
            _mode, text = _content_mode_and_text(message)
            if high_signal_only and not _ANCHOR_RE.search(text):
                skipped_ids.append(tool_id)
                continue
            protected_indexes.add(idx)
            final_ids.append(tool_id)
        self.plan.dependency_protected_tool_indexes = protected_indexes
        if protected_indexes:
            self.plan.stats["dependency_protected_tool_outputs"] = {
                "messages": len(protected_indexes),
                "tool_call_ids": sorted(final_ids),
                "high_signal_only": high_signal_only,
            }
        if skipped_ids:
            self.plan.stats["dependency_protected_tool_outputs_skipped"] = {
                "reason": "referenced_tool_output_without_high_signal",
                "tool_call_ids": sorted(skipped_ids),
            }

    def _build_global_dependency_indexes(self) -> None:
        enabled = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_GLOBAL_DEPENDENCY_COMPRESSION",
            "global_dependency_compression",
            False,
        )
        stats = self.plan.stats.setdefault("global_dependency_graph", {})
        if not isinstance(stats, dict):
            return
        stats["enabled"] = enabled
        stats["instruction_count"] = len(self.plan.global_instructions)
        if not enabled:
            stats["reason"] = "disabled"
            return
        if not self.plan.global_instructions:
            stats["reason"] = "instruction_graph_unavailable"
            return

        active_tool_ids = {
            tool_id
            for tool_id in self.plan.tool_id_by_index.values()
            if isinstance(tool_id, str) and tool_id
        }
        index = GlobalDependencyIndex(
            self.plan.global_instructions,
            active_tool_ids=active_tool_ids,
        )
        self.plan.global_dependency_index = index
        stats.update(index.diagnostics())
        prunable: set[int] = set()
        for tool_idx in self.plan.tool_indexes:
            tool_id = self.plan.tool_id_by_index.get(tool_idx)
            if not tool_id:
                continue
            state = index.tool_state.get(tool_id)
            if not isinstance(state, dict):
                continue
            self.plan.global_dependency_tool_state_by_index[tool_idx] = state
            if state.get("state") == "expired" and not state.get("protected"):
                prunable.add(tool_idx)
        self.plan.global_dependency_prunable_tool_indexes = prunable
        stats["expired_prunable_tool_outputs"] = len(prunable)
        stats["expired_prunable_tool_call_ids"] = sorted(
            self.plan.tool_id_by_index[idx]
            for idx in prunable
            if idx in self.plan.tool_id_by_index
        )

    def _build_dependency_prunable_indexes(self) -> None:
        enabled = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_EVIDENCE_PRUNING",
            "dependency_evidence_pruning",
            False,
        )
        graph_stats = self.plan.stats.setdefault("dependency_evidence_graph", {})
        if isinstance(graph_stats, dict):
            graph_stats["pruning_enabled"] = enabled
        if not enabled:
            return

        min_tool_results = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_MIN_TOOL_RESULTS",
            "dependency_prune_min_tool_results",
            4,
        )
        if min_tool_results > 0 and len(self.plan.tool_indexes) < min_tool_results:
            if isinstance(graph_stats, dict):
                graph_stats["pruning_skipped_reason"] = "below_min_tool_results"
                graph_stats["min_tool_results"] = min_tool_results
            return

        min_chars = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_MIN_CHARS",
            "dependency_prune_min_chars",
            900,
        )
        require_stale = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_REQUIRE_STALE",
            "dependency_prune_require_stale",
            True,
        )
        require_tool_call_id = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_REQUIRE_TOOL_CALL_ID",
            "dependency_prune_require_tool_call_id",
            True,
        )
        protect_noisy = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_PROTECT_NOISY",
            "dependency_prune_protect_noisy",
            True,
        )
        protect_source_like = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_PROTECT_SOURCE_LIKE",
            "dependency_prune_protect_source_like",
            True,
        )
        protect_focus = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_PROTECT_FOCUS",
            "dependency_prune_protect_focus",
            True,
        )
        prune_expired_references = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_EXPIRED_REFERENCES",
            "dependency_prune_expired_references",
            False,
        )
        expired_min_later_tool_results = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_EXPIRED_MIN_TOOL_RESULTS",
            "dependency_prune_expired_min_tool_results",
            2,
        )
        prune_superseded_outputs = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_SUPERSEDED_OUTPUTS",
            "dependency_prune_superseded_outputs",
            False,
        )
        prune_expired_bypass_signal_protection = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_EXPIRED_BYPASS_SIGNAL_PROTECTION",
            "dependency_prune_expired_bypass_signal_protection",
            False,
        )
        prune_unreferenced_stale_outputs = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_UNREFERENCED_STALE_OUTPUTS",
            "dependency_prune_unreferenced_stale_outputs",
            False,
        )
        unreferenced_min_later_tool_results = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_UNREFERENCED_MIN_TOOL_RESULTS",
            "dependency_prune_unreferenced_min_tool_results",
            4,
        )
        prune_unreferenced_bypass_signal_protection = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_UNREFERENCED_BYPASS_SIGNAL_PROTECTION",
            "dependency_prune_unreferenced_bypass_signal_protection",
            False,
        )
        superseded_min_shared_lines = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_SUPERSEDED_MIN_SHARED_LINES",
            "dependency_prune_superseded_min_shared_lines",
            6,
        )
        superseded_min_overlap_ratio = min(
            max(
                self.cfg.env_float(
                    "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_SUPERSEDED_MIN_OVERLAP_RATIO",
                    "dependency_prune_superseded_min_overlap_ratio",
                    0.35,
                ),
                0.05,
            ),
            0.99,
        )
        superseded_max_later_scan = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_SUPERSEDED_MAX_LATER_SCAN",
            "dependency_prune_superseded_max_later_scan",
            8,
        )

        prunable: set[int] = set()
        expired_referenced: set[int] = set()
        superseded: set[int] = set()
        unreferenced_stale: set[int] = set()
        skipped: dict[str, int] = {}

        def skip(reason: str) -> None:
            skipped[reason] = int(skipped.get(reason, 0) or 0) + 1

        for idx in self.plan.tool_indexes:
            if idx in self.plan.dependency_protected_tool_indexes:
                skip("dependency_protected")
                continue
            if require_stale and idx not in self.plan.stale_tool_indexes:
                skip("recent_tool_output")
                continue
            tool_id = self.plan.tool_id_by_index.get(idx)
            if require_tool_call_id and not tool_id:
                skip("missing_tool_call_id")
                continue
            later_tool_results_after_output = sum(
                1 for tool_idx in self.plan.tool_indexes if tool_idx > idx
            )
            unreferenced_old_enough = (
                prune_unreferenced_stale_outputs
                and tool_id is not None
                and tool_id not in self.plan.referenced_tool_ids
                and later_tool_results_after_output >= unreferenced_min_later_tool_results
            )
            if tool_id and tool_id in self.plan.referenced_tool_ids:
                if not prune_expired_references:
                    skip("referenced_by_later_tool_call")
                    continue
                last_reference_idx = self.plan.dependency_last_reference_index_by_tool_index.get(idx)
                if last_reference_idx is None:
                    skip("referenced_by_later_tool_call")
                    continue
                later_tool_results = sum(
                    1 for tool_idx in self.plan.tool_indexes if tool_idx > last_reference_idx
                )
                if later_tool_results < expired_min_later_tool_results:
                    skip("referenced_by_recent_tool_call")
                    continue
                expired_referenced.add(idx)
            message = self.plan.messages[idx]
            if not isinstance(message, dict):
                continue
            _mode, text = _content_mode_and_text(message)
            if not text or len(text) < min_chars:
                skip("below_min_chars")
                continue
            body = _tool_body(text)
            superseded_later_idx: Optional[int] = None
            superseded_reason = ""
            if prune_superseded_outputs:
                superseded_match = self._find_superseding_later_tool(
                    idx,
                    min_shared_lines=superseded_min_shared_lines,
                    min_overlap_ratio=superseded_min_overlap_ratio,
                    max_later_scan=superseded_max_later_scan,
                )
                if superseded_match is not None:
                    superseded_later_idx, superseded_reason = superseded_match
            bypass_signal_protection = (
                superseded_later_idx is not None
                or (
                    idx in expired_referenced
                    and prune_expired_bypass_signal_protection
                )
                or (
                    unreferenced_old_enough
                    and prune_unreferenced_bypass_signal_protection
                )
            )
            if protect_noisy and (
                _ANCHOR_RE.search(body)
                or _NOISY_TOOL_RE.search(body)
                or _TEST_FIXTURE_RE.search(body)
            ):
                if not bypass_signal_protection:
                    skip("high_signal_or_noisy")
                    continue
            if protect_source_like and _looks_source_like(body):
                if not bypass_signal_protection:
                    skip("source_like")
                    continue
            if protect_focus and _line_matches_focus_terms(body, self.plan.focus_terms):
                if not bypass_signal_protection:
                    skip("matches_focus_terms")
                    continue
            if superseded_later_idx is not None:
                superseded.add(idx)
                self.plan.dependency_superseding_later_index_by_tool_index[idx] = (
                    superseded_later_idx
                )
                self.plan.dependency_supersede_reason_by_tool_index[idx] = (
                    superseded_reason or "covered_by_later_tool_output"
                )
            if unreferenced_old_enough:
                unreferenced_stale.add(idx)
            prunable.add(idx)

        self.plan.dependency_prunable_tool_indexes = prunable
        self.plan.dependency_expired_reference_tool_indexes = expired_referenced & prunable
        self.plan.dependency_superseded_tool_indexes = superseded & prunable
        self.plan.dependency_unreferenced_stale_tool_indexes = unreferenced_stale & prunable
        if isinstance(graph_stats, dict):
            graph_stats["prunable_tool_outputs"] = len(prunable)
            graph_stats["prune_skipped"] = skipped
            graph_stats["expired_reference_pruning_enabled"] = prune_expired_references
            graph_stats["superseded_pruning_enabled"] = prune_superseded_outputs
            graph_stats["unreferenced_stale_pruning_enabled"] = prune_unreferenced_stale_outputs
            if prune_expired_references:
                graph_stats["expired_reference_min_later_tool_results"] = (
                    expired_min_later_tool_results
                )
                graph_stats["expired_reference_bypass_signal_protection"] = (
                    prune_expired_bypass_signal_protection
                )
            if prune_unreferenced_stale_outputs:
                graph_stats["unreferenced_stale_min_later_tool_results"] = (
                    unreferenced_min_later_tool_results
                )
                graph_stats["unreferenced_stale_bypass_signal_protection"] = (
                    prune_unreferenced_bypass_signal_protection
                )
            if prunable:
                graph_stats["prunable_tool_call_ids"] = sorted(
                    self.plan.tool_id_by_index[idx]
                    for idx in prunable
                    if idx in self.plan.tool_id_by_index
                )
            if self.plan.dependency_expired_reference_tool_indexes:
                graph_stats["expired_referenced_prunable_tool_outputs"] = len(
                    self.plan.dependency_expired_reference_tool_indexes
                )
                graph_stats["expired_referenced_prunable_tool_call_ids"] = sorted(
                    self.plan.tool_id_by_index[idx]
                    for idx in self.plan.dependency_expired_reference_tool_indexes
                    if idx in self.plan.tool_id_by_index
                )
            if self.plan.dependency_superseded_tool_indexes:
                graph_stats["superseded_prunable_tool_outputs"] = len(
                    self.plan.dependency_superseded_tool_indexes
                )
                graph_stats["superseded_prunable_tool_call_ids"] = sorted(
                    self.plan.tool_id_by_index[idx]
                    for idx in self.plan.dependency_superseded_tool_indexes
                    if idx in self.plan.tool_id_by_index
                )
                graph_stats["superseded_edges"] = [
                    {
                        "tool_call_id": self.plan.tool_id_by_index.get(idx),
                        "message_index": idx,
                        "covered_by_message_index": self.plan.dependency_superseding_later_index_by_tool_index.get(idx),
                        "reason": self.plan.dependency_supersede_reason_by_tool_index.get(idx),
                    }
                    for idx in sorted(self.plan.dependency_superseded_tool_indexes)
                ][:24]
            if self.plan.dependency_unreferenced_stale_tool_indexes:
                graph_stats["unreferenced_stale_prunable_tool_outputs"] = len(
                    self.plan.dependency_unreferenced_stale_tool_indexes
                )
                graph_stats["unreferenced_stale_prunable_tool_call_ids"] = sorted(
                    self.plan.tool_id_by_index[idx]
                    for idx in self.plan.dependency_unreferenced_stale_tool_indexes
                    if idx in self.plan.tool_id_by_index
                )

    def _find_superseding_later_tool(
        self,
        idx: int,
        *,
        min_shared_lines: int,
        min_overlap_ratio: float,
        max_later_scan: int,
    ) -> Optional[tuple[int, str]]:
        message = self.plan.messages[idx]
        if not isinstance(message, dict):
            return None
        _mode, text = _content_mode_and_text(message)
        current = self._evidence_signature(text)
        if not current["has_signal"]:
            return None

        later_indexes = [tool_idx for tool_idx in self.plan.tool_indexes if tool_idx > idx]
        if max_later_scan > 0:
            later_indexes = later_indexes[-max_later_scan:]
        for later_idx in reversed(later_indexes):
            later_message = self.plan.messages[later_idx]
            if not isinstance(later_message, dict):
                continue
            _later_mode, later_text = _content_mode_and_text(later_message)
            later = self._evidence_signature(later_text)
            reason = self._supersede_reason(
                current,
                later,
                min_shared_lines=min_shared_lines,
                min_overlap_ratio=min_overlap_ratio,
            )
            if reason:
                return later_idx, reason
        return None

    @staticmethod
    def _evidence_signature(text: str) -> dict[str, Any]:
        body = _tool_body(text)
        paths = {path.lower() for path in _PATH_FOCUS_RE.findall(body)}
        nodeids = {node.lower() for node in _PYTEST_NODE_RE.findall(body)}
        counts = {match.group(0).strip("= ").lower() for match in _FAILED_COUNT_RE.finditer(body)}
        returncode_match = _RETURN_CODE_RE.search(text)
        returncode = (
            " ".join(returncode_match.group(1).split()).lower()
            if returncode_match
            else ""
        )
        signal_lines = {
            line
            for line in CandidateIndexBuilder._line_signature(body)
            if _ANCHOR_RE.search(line)
            or _SOURCE_LIKE_RE.search(line)
            or _line_contains_path_like_text(line)
        }
        source_like = _looks_source_like(body)
        noisy = bool(_ANCHOR_RE.search(body) or _NOISY_TOOL_RE.search(body) or counts)
        has_signal = bool(paths or nodeids or counts or returncode or signal_lines or source_like or noisy)
        return {
            "paths": paths,
            "nodeids": nodeids,
            "counts": counts,
            "returncode": returncode,
            "lines": signal_lines,
            "source_like": source_like,
            "noisy": noisy,
            "has_signal": has_signal,
        }

    @staticmethod
    def _supersede_reason(
        current: dict[str, Any],
        later: dict[str, Any],
        *,
        min_shared_lines: int,
        min_overlap_ratio: float,
    ) -> str:
        if not later.get("has_signal"):
            return ""
        current_nodeids = current.get("nodeids") or set()
        later_nodeids = later.get("nodeids") or set()
        if current_nodeids and current_nodeids & later_nodeids:
            return "shared_test_nodeid"

        current_paths = current.get("paths") or set()
        later_paths = later.get("paths") or set()
        shared_paths = current_paths & later_paths
        if shared_paths:
            if current.get("source_like") and later.get("source_like"):
                current_lines = current.get("lines") or set()
                later_lines = later.get("lines") or set()
                shared_lines = len(current_lines & later_lines)
                overlap_ratio = shared_lines / max(1, len(current_lines))
                if shared_lines >= min_shared_lines and overlap_ratio >= min_overlap_ratio:
                    return "shared_source_path_and_lines"
            if current.get("noisy") and later.get("noisy"):
                return "shared_path_and_status_signal"

        current_lines = current.get("lines") or set()
        later_lines = later.get("lines") or set()
        shared_lines = len(current_lines & later_lines)
        overlap_ratio = shared_lines / max(1, len(current_lines))
        if shared_lines >= min_shared_lines and overlap_ratio >= min_overlap_ratio:
            return "shared_evidence_lines"
        return ""

    def _duplicate_latest(self) -> dict[int, int]:
        if not self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DUPLICATE_TOOL_ELISION",
            "duplicate_tool_elision",
            True,
        ):
            return {}
        include_recent = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DUPLICATE_TOOL_ELIDE_RECENT",
            "duplicate_tool_elide_recent",
            True,
        )
        min_chars = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_DUPLICATE_TOOL_MIN_CHARS",
            "duplicate_tool_min_chars",
            1200,
        )
        latest_by_normalized: dict[str, int] = {}
        duplicates: dict[int, int] = {}
        for idx in reversed(self.plan.tool_indexes):
            message = self.plan.messages[idx]
            if not isinstance(message, dict):
                continue
            _mode, text = _content_mode_and_text(message)
            if not text or len(text) < min_chars:
                continue
            normalized = re.sub(r"\s+", " ", _tool_body(text).strip())
            if len(normalized) < min_chars:
                continue
            later_idx = latest_by_normalized.get(normalized)
            if later_idx is None:
                latest_by_normalized[normalized] = idx
                continue
            if include_recent or idx in self.plan.stale_tool_indexes:
                duplicates[idx] = later_idx
        return duplicates

    def _near_duplicate_latest(self) -> dict[int, tuple[int, float]]:
        if not self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_NEAR_DUPLICATE_TOOL_ELISION",
            "near_duplicate_tool_elision",
            False,
        ):
            return {}
        min_chars = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_NEAR_DUPLICATE_TOOL_MIN_CHARS",
            "near_duplicate_tool_min_chars",
            2000,
        )
        min_lines = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_NEAR_DUPLICATE_TOOL_MIN_LINES",
            "near_duplicate_tool_min_lines",
            20,
        )
        min_shared_lines = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_NEAR_DUPLICATE_TOOL_MIN_SHARED_LINES",
            "near_duplicate_tool_min_shared_lines",
            16,
        )
        overlap_ratio = min(
            max(
                self.cfg.env_float(
                    "ARBITEROS_COST_DOWN_PHASE3_NEAR_DUPLICATE_TOOL_OVERLAP_RATIO",
                    "near_duplicate_tool_overlap_ratio",
                    0.82,
                ),
                0.5,
            ),
            0.99,
        )
        signatures: dict[int, set[str]] = {}
        for idx in self.plan.tool_indexes:
            message = self.plan.messages[idx]
            if not isinstance(message, dict):
                continue
            _mode, text = _content_mode_and_text(message)
            if not text or len(text) < min_chars:
                continue
            signature = self._line_signature(text)
            if len(signature) >= min_lines:
                signatures[idx] = signature

        near: dict[int, tuple[int, float]] = {}
        for idx in self.plan.tool_indexes:
            if idx not in self.plan.stale_tool_indexes or idx not in signatures:
                continue
            current = signatures[idx]
            best_later_idx: Optional[int] = None
            best_ratio = 0.0
            for later_idx in self.plan.tool_indexes:
                if later_idx <= idx or later_idx not in signatures:
                    continue
                shared = len(current & signatures[later_idx])
                if shared < min_shared_lines:
                    continue
                ratio = shared / max(1, len(current))
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_later_idx = later_idx
            if best_later_idx is not None and best_ratio >= overlap_ratio:
                near[idx] = (best_later_idx, best_ratio)
        return near

    @staticmethod
    def _line_signature(text: str, *, limit: int = 1200) -> set[str]:
        signature: set[str] = set()
        for raw in _tool_body(text).splitlines():
            line = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()
            line = re.sub(r"^\s*\d+\s+|\s*\|\s*", " ", line)
            line = re.sub(r"\s+", " ", line).strip()
            if len(line) < 8:
                continue
            if re.fullmatch(r"[-_=*#{}()[\],.;:\s]+", line):
                continue
            signature.add(line)
            if len(signature) >= limit:
                break
        return signature


class RiskBudgetController:
    def __init__(self, plan: CompressionPlan):
        self.plan = plan
        self.cfg = plan.config

    def configure(self) -> None:
        budget = CompressionRiskBudget(
            enabled=self.cfg.env_bool(
                "ARBITEROS_COST_DOWN_PHASE3_ONLINE_RISK_GUARD",
                "online_risk_guard",
                True,
            ),
            min_tool_results=self.cfg.env_int(
                "ARBITEROS_COST_DOWN_PHASE3_RISK_GUARD_MIN_TOOL_RESULTS",
                "risk_guard_min_tool_results",
                8,
            ),
            min_input_tokens=self.cfg.env_int(
                "ARBITEROS_COST_DOWN_PHASE3_RISK_GUARD_MIN_INPUT_TOKENS",
                "risk_guard_min_input_tokens",
                14000,
            ),
            conservative_min_tool_results=self.cfg.env_int(
                "ARBITEROS_COST_DOWN_PHASE3_RISK_GUARD_CONSERVATIVE_MIN_TOOL_RESULTS",
                "risk_guard_conservative_min_tool_results",
                12,
            ),
            conservative_min_input_tokens=self.cfg.env_int(
                "ARBITEROS_COST_DOWN_PHASE3_RISK_GUARD_CONSERVATIVE_MIN_INPUT_TOKENS",
                "risk_guard_conservative_min_input_tokens",
                16000,
            ),
            min_target_ratio=min(
                max(
                    self.cfg.env_float(
                        "ARBITEROS_COST_DOWN_PHASE3_RISK_GUARD_MIN_TARGET_RATIO",
                        "risk_guard_min_target_ratio",
                        0.45,
                    ),
                    0.05,
                ),
                1.0,
            ),
            source_like_min_target_chars=max(
                160,
                self.cfg.env_int(
                    "ARBITEROS_COST_DOWN_PHASE3_RISK_GUARD_SOURCE_MIN_TARGET_CHARS",
                    "risk_guard_source_like_min_target_chars",
                    1800,
                ),
            ),
            test_fixture_target_ratio=min(
                max(
                    self.cfg.env_float(
                        "ARBITEROS_COST_DOWN_PHASE3_RISK_GUARD_TEST_FIXTURE_TARGET_RATIO",
                        "risk_guard_test_fixture_target_ratio",
                        0.7,
                    ),
                    0.05,
                ),
                1.0,
            ),
            protect_recent_tool_results=self.cfg.env_int(
                "ARBITEROS_COST_DOWN_PHASE3_RISK_GUARD_PROTECT_RECENT_TOOL_RESULTS",
                "risk_guard_protect_recent_tool_results",
                5,
            ),
            disable_duplicate_elision=self.cfg.env_bool(
                "ARBITEROS_COST_DOWN_PHASE3_RISK_GUARD_DISABLE_DUPLICATE_ELISION",
                "risk_guard_disable_duplicate_elision",
                True,
            ),
            disable_near_duplicate_elision=self.cfg.env_bool(
                "ARBITEROS_COST_DOWN_PHASE3_RISK_GUARD_DISABLE_NEAR_DUPLICATE_ELISION",
                "risk_guard_disable_near_duplicate_elision",
                True,
            ),
            source_like_raw_head_tail=self.cfg.env_bool(
                "ARBITEROS_COST_DOWN_PHASE3_RISK_GUARD_SOURCE_LIKE_RAW_HEAD_TAIL",
                "risk_guard_source_like_raw_head_tail",
                True,
            ),
        )
        if not budget.enabled:
            self.plan.risk_budget = budget
            return

        tool_count = len(self.plan.tool_indexes)
        input_tokens = int(self.plan.stats["original_estimated_input_tokens"] or 0)
        high_signal_tools = self._high_signal_tool_count()
        long_enough = (
            budget.min_tool_results > 0 and tool_count >= budget.min_tool_results
        ) or (budget.min_input_tokens > 0 and input_tokens >= budget.min_input_tokens)
        debugging = self.plan.edit_signal_seen or high_signal_tools > 0
        budget.active = bool(long_enough and debugging)
        conservative_tool_met = (
            budget.conservative_min_tool_results <= 0
            or tool_count >= budget.conservative_min_tool_results
        )
        conservative_input_met = (
            budget.conservative_min_input_tokens <= 0
            or input_tokens >= budget.conservative_min_input_tokens
        )
        budget.conservative_active = bool(
            budget.active and conservative_tool_met and conservative_input_met
        )
        if budget.active:
            if self.plan.edit_signal_seen:
                budget.reason = (
                    "deep_debugging_trajectory"
                    if budget.conservative_active
                    else "long_debugging_trajectory"
                )
            else:
                budget.reason = (
                    "deep_high_signal_tool_history"
                    if budget.conservative_active
                    else "long_high_signal_tool_history"
                )
            if budget.protect_recent_tool_results > 0:
                budget.protected_tool_indexes = set(
                    self.plan.tool_indexes[-budget.protect_recent_tool_results :]
                )
            self.plan.stats["online_risk_guard"] = {
                "active": True,
                "conservative_active": budget.conservative_active,
                "reason": budget.reason,
                "tool_results": tool_count,
                "high_signal_tool_results": high_signal_tools,
                "original_estimated_input_tokens": input_tokens,
                "min_tool_results": budget.min_tool_results,
                "min_input_tokens": budget.min_input_tokens,
                "conservative_min_tool_results": budget.conservative_min_tool_results,
                "conservative_min_input_tokens": budget.conservative_min_input_tokens,
                "min_target_ratio": budget.min_target_ratio,
                "source_like_min_target_chars": budget.source_like_min_target_chars,
                "test_fixture_target_ratio": budget.test_fixture_target_ratio,
                "protected_recent_tool_results": len(budget.protected_tool_indexes),
                "duplicate_elision_disabled": budget.disable_duplicate_elision,
                "near_duplicate_elision_disabled": budget.disable_near_duplicate_elision,
                "source_like_raw_head_tail": budget.source_like_raw_head_tail,
            }
        else:
            self.plan.stats["online_risk_guard"] = {
                "active": False,
                "conservative_active": False,
                "tool_results": tool_count,
                "high_signal_tool_results": high_signal_tools,
                "original_estimated_input_tokens": input_tokens,
                "min_tool_results": budget.min_tool_results,
                "min_input_tokens": budget.min_input_tokens,
                "conservative_min_tool_results": budget.conservative_min_tool_results,
                "conservative_min_input_tokens": budget.conservative_min_input_tokens,
            }
        self.plan.risk_budget = budget

    def _high_signal_tool_count(self) -> int:
        count = 0
        for idx in self.plan.tool_indexes:
            message = self.plan.messages[idx]
            if not isinstance(message, dict):
                continue
            _mode, text = _content_mode_and_text(message)
            body = _tool_body(text)
            if _ANCHOR_RE.search(body) or _TEST_FIXTURE_RE.search(body):
                count += 1
        return count


class GatePlanner:
    def __init__(self, plan: CompressionPlan):
        self.plan = plan
        self.cfg = plan.config

    def configure(self) -> None:
        stats = self.plan.stats
        dependency_action = _env_str(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PROTECTED_ACTION",
            str(self.cfg.raw.get("dependency_protected_action") or "compress"),
        ).lower()
        self.plan.dependency_protected_action = (
            dependency_action if dependency_action in {"compress", "keep"} else "compress"
        )
        stats["dependency_protected_action"] = self.plan.dependency_protected_action

        pressure_threshold = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_PRESSURE_INPUT_TOKEN_THRESHOLD",
            "pressure_input_token_threshold",
            0,
        )
        self.plan.pressure_target_ratio = self.cfg.env_float(
            "ARBITEROS_COST_DOWN_PHASE3_PRESSURE_TARGET_RATIO",
            "pressure_target_ratio",
            0.0,
        )
        self.plan.pressure_active = (
            pressure_threshold > 0
            and self.plan.pressure_target_ratio > 0
            and int(stats["original_estimated_input_tokens"] or 0) >= pressure_threshold
        )
        if self.plan.pressure_active:
            stats["prompt_pressure"] = {
                "original_estimated_input_tokens": stats["original_estimated_input_tokens"],
                "threshold": pressure_threshold,
                "target_ratio": min(max(self.plan.pressure_target_ratio, 0.05), 1.0),
            }

        fallback_min_input_tokens = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_MESSAGE_FALLBACK_MIN_INPUT_TOKENS",
            "message_fallback_min_input_tokens",
            0,
        )
        self.plan.fallback_gate_met = (
            fallback_min_input_tokens <= 0
            or int(stats["original_estimated_input_tokens"] or 0)
            >= fallback_min_input_tokens
        )
        if fallback_min_input_tokens > 0:
            stats["message_fallback_gate"] = {
                "original_estimated_input_tokens": stats["original_estimated_input_tokens"],
                "min_input_tokens": fallback_min_input_tokens,
                "met": self.plan.fallback_gate_met,
            }

        source_like_min = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_FALLBACK_MIN_INPUT_TOKENS",
            "source_like_fallback_min_input_tokens",
            0,
        )
        source_requires_edit = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_FAST_LANE_REQUIRES_EDIT_SIGNAL",
            "source_like_fast_lane_requires_edit_signal",
            True,
        )
        source_gate_met = (
            source_like_min > 0
            and int(stats["original_estimated_input_tokens"] or 0) >= source_like_min
        )
        self.plan.source_like_fast_lane_active = source_gate_met and (
            not source_requires_edit or self.plan.edit_signal_seen
        )
        pre_edit_min = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_PRE_EDIT_MIN_INPUT_TOKENS",
            "source_like_pre_edit_min_input_tokens",
            16000,
        )
        self.plan.source_like_pre_edit_gate_met = (
            not source_requires_edit
            or self.plan.edit_signal_seen
            or pre_edit_min <= 0
            or int(stats["original_estimated_input_tokens"] or 0) >= pre_edit_min
        )
        if source_like_min > 0:
            stats["source_like_message_fallback_gate"] = {
                "original_estimated_input_tokens": stats["original_estimated_input_tokens"],
                "min_input_tokens": source_like_min,
                "met": self.plan.source_like_fast_lane_active,
                "requires_edit_signal": source_requires_edit,
                "edit_signal_seen": self.plan.edit_signal_seen,
                "pre_edit_min_input_tokens": pre_edit_min,
                "pre_edit_gate_met": self.plan.source_like_pre_edit_gate_met,
            }

        self.plan.source_like_structure = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_STRUCTURE_COMPRESSION",
            "source_like_structure_compression",
            True,
        )
        self.plan.source_like_focus_context_lines = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_FOCUS_CONTEXT_LINES",
            "source_like_focus_context_lines",
            2,
        )
        self.plan.source_like_max_structure_lines_when_focused = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_MAX_STRUCTURE_LINES_WHEN_FOCUSED",
            "source_like_max_structure_lines_when_focused",
            24,
        )
        self.plan.source_like_focused_target_ratio = self.cfg.env_float(
            "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_FOCUSED_TARGET_RATIO",
            "source_like_focused_target_ratio",
            0.18,
        )
        self.plan.source_like_min_target_chars = max(
            160,
            self.cfg.env_int(
                "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_MIN_TARGET_CHARS",
                "source_like_min_target_chars",
                760,
            ),
        )
        stats["source_like_min_target_chars"] = self.plan.source_like_min_target_chars
        self.plan.preserve_test_fixture_outputs = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_PRESERVE_TEST_FIXTURE_OUTPUTS",
            "preserve_test_fixture_outputs",
            True,
        )
        self.plan.test_fixture_target_ratio = min(
            max(
                self.cfg.env_float(
                    "ARBITEROS_COST_DOWN_PHASE3_TEST_FIXTURE_TARGET_RATIO",
                    "test_fixture_target_ratio",
                    0.55,
                ),
                0.05,
            ),
            1.0,
        )
        self.plan.semantic_digest_enabled = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_SEMANTIC_DIGEST_COMPRESSION",
            "semantic_digest_compression",
            False,
        )
        self.plan.semantic_digest_hybrid = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_SEMANTIC_DIGEST_HYBRID_RULE_TAIL",
            "semantic_digest_hybrid_rule_tail",
            False,
        )
        self.plan.min_saved_tokens_per_message = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_MIN_SAVED_TOKENS_PER_MESSAGE",
            "min_saved_tokens_per_message",
            _to_int(self.cfg.raw.get("min_saved_tokens_per_context"), 0),
        )
        if self.plan.min_saved_tokens_per_message > 0:
            stats["min_saved_tokens_per_message"] = self.plan.min_saved_tokens_per_message
        self._mark_active_source_like_outputs(source_requires_edit)

    def _mark_active_source_like_outputs(self, source_requires_edit: bool) -> None:
        protect_recent = max(
            0,
            self.cfg.env_int(
                "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_PRE_EDIT_PROTECT_RECENT",
                "source_like_pre_edit_protect_recent",
                2,
            ),
        )
        if not (
            source_requires_edit
            and not self.plan.edit_signal_seen
            and protect_recent > 0
        ):
            return
        source_like_tool_indexes = [
            idx
            for idx, message in enumerate(self.plan.messages)
            if isinstance(message, dict)
            and message.get("role") == "tool"
            and _looks_source_like(_tool_body(_content_mode_and_text(message)[1]))
        ]
        self.plan.active_source_like_tool_indexes = set(
            source_like_tool_indexes[-protect_recent:]
        )
        if self.plan.active_source_like_tool_indexes:
            self.plan.stats["source_like_pre_edit_active_evidence"] = {
                "protected_recent": protect_recent,
                "messages": len(self.plan.active_source_like_tool_indexes),
                "reason": "recent source-like tool output before edit signal",
            }

    def build_state(self, idx: int, message: dict[str, Any]) -> Optional[MessageState]:
        role = str(message.get("role") or "unknown")
        if role not in {"tool", "assistant"}:
            return None
        if role == "assistant" and not _is_compactable_assistant(message):
            return None
        mode, text = _content_mode_and_text(message)
        if mode is None or not text:
            return None
        context_id = _context_id(message)
        context_policy = self.cfg.context_policies.get(context_id or "")
        source_like = role == "tool" and _looks_source_like(_tool_body(text))
        test_fixture = (
            self.plan.preserve_test_fixture_outputs
            and role == "tool"
            and bool(_TEST_FIXTURE_RE.search(_tool_body(text)))
        )
        global_dependency_state = self.plan.global_dependency_tool_state_by_index.get(idx)
        state = MessageState(
            index=idx,
            message=message,
            role=role,
            mode=mode,
            text=text,
            context_id=context_id,
            context_policy=context_policy,
            source_like=source_like,
            test_fixture_like=test_fixture,
            dependency_protected=role == "tool"
            and idx in self.plan.dependency_protected_tool_indexes,
            dependency_expired_reference=role == "tool"
            and idx in self.plan.dependency_expired_reference_tool_indexes,
            dependency_superseded=role == "tool"
            and idx in self.plan.dependency_superseded_tool_indexes,
            dependency_unreferenced_stale=role == "tool"
            and idx in self.plan.dependency_unreferenced_stale_tool_indexes,
            global_dependency_prunable=role == "tool"
            and idx in self.plan.global_dependency_prunable_tool_indexes,
            global_dependency_fanout=_to_int(
                (global_dependency_state or {}).get("fanout"), 0
            ),
            global_dependency_last_use_step=_to_int(
                (global_dependency_state or {}).get("last_use_step"), 0
            ),
            stale_tool=idx in self.plan.stale_tool_indexes,
            stale_assistant=idx in self.plan.stale_assistant_indexes,
            duplicate_later_idx=self.plan.duplicate_latest.get(idx)
            if role == "tool"
            else None,
            near_duplicate_later=self.plan.near_duplicate_latest.get(idx)
            if role == "tool"
            else None,
            repeated_line_folded=self._repeated_line_candidate(text)
            if role == "tool" and self.cfg.rule_enabled
            else None,
        )
        state.dependency_offline_expired_context = self._offline_dependency_lifetime_prunable(
            state
        )
        state.dependency_prunable = (
            role == "tool"
            and not state.dependency_protected
            and (
                idx in self.plan.dependency_prunable_tool_indexes
                or state.dependency_offline_expired_context
            )
        )
        self._decide_gate(state)
        return state

    def _offline_dependency_lifetime_prunable(self, state: MessageState) -> bool:
        if state.role != "tool" or state.dependency_protected:
            return False
        if state.index not in self.plan.stale_tool_indexes:
            return False
        if not self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_OFFLINE_EXPIRED_CONTEXTS",
            "dependency_prune_offline_expired_contexts",
            False,
        ):
            return False
        if not isinstance(state.context_policy, dict):
            return False
        lifetime = state.context_policy.get("dependency_lifetime")
        if not isinstance(lifetime, dict):
            return False
        if not _to_bool(lifetime.get("dependency_expired"), False):
            return False
        remaining_tail_steps = _to_int(lifetime.get("remaining_tail_steps"), 0)
        if remaining_tail_steps <= 0:
            return False
        min_chars = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_MIN_CHARS",
            "dependency_prune_min_chars",
            900,
        )
        return len(state.text) >= min_chars

    def _late_source_like_compaction_active(self, state: MessageState) -> bool:
        enabled = self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_LATE_SOURCE_LIKE_COMPACTION",
            "late_source_like_compaction",
            False,
        )
        if not enabled:
            return False
        if state.role != "tool" or not state.source_like:
            return False
        if state.index not in self.plan.stale_tool_indexes:
            return False
        if state.dependency_protected:
            return False
        if state.index in self.plan.risk_budget.protected_tool_indexes:
            return False
        min_tool_results = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_LATE_SOURCE_LIKE_MIN_TOOL_RESULTS",
            "late_source_like_min_tool_results",
            10,
        )
        if min_tool_results > 0 and len(self.plan.tool_indexes) < min_tool_results:
            return False
        min_input_tokens = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_LATE_SOURCE_LIKE_MIN_INPUT_TOKENS",
            "late_source_like_min_input_tokens",
            14000,
        )
        original_tokens = int(self.plan.stats.get("original_estimated_input_tokens") or 0)
        if min_input_tokens > 0 and original_tokens < min_input_tokens:
            return False
        return True

    def _record_late_source_like_compaction(self, state: MessageState) -> None:
        stats = self.plan.stats.setdefault("late_source_like_compaction", {})
        if not isinstance(stats, dict):
            return
        stats["messages"] = int(stats.get("messages", 0) or 0) + 1
        ids = stats.setdefault("tool_call_ids", [])
        tool_id = _tool_call_id(state.message)
        if isinstance(ids, list) and tool_id and tool_id not in ids:
            ids.append(tool_id)
        stats["min_tool_results"] = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_LATE_SOURCE_LIKE_MIN_TOOL_RESULTS",
            "late_source_like_min_tool_results",
            10,
        )
        stats["min_input_tokens"] = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_LATE_SOURCE_LIKE_MIN_INPUT_TOKENS",
            "late_source_like_min_input_tokens",
            14000,
        )

    def _decide_gate(self, state: MessageState) -> None:
        exact_action = (
            str(state.context_policy.get("action") or "").strip().lower()
            if isinstance(state.context_policy, dict)
            else ""
        )
        if self.plan.fallback_gate_met:
            state.fallback_allowed = self._role_fallback_allowed(state)
        elif self.plan.source_like_fast_lane_active and state.source_like and not state.test_fixture_like:
            state.fallback_allowed = self._role_fallback_allowed(state)
        elif state.role == "tool" and _is_low_value_search_noise(state.text):
            state.fallback_allowed = True
        if state.dependency_prunable:
            state.fallback_allowed = True
        if state.global_dependency_prunable:
            state.fallback_allowed = True
        if self._late_source_like_compaction_active(state):
            state.fallback_allowed = True
            state.late_source_like_compaction = True
            self._record_late_source_like_compaction(state)

        source_like_pre_edit_blocked = (
            state.fallback_allowed
            and state.role == "tool"
            and state.source_like
            and not (
                state.dependency_prunable
                and self.cfg.env_bool(
                    "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_BYPASS_SOURCE_LIKE_GUARDS",
                    "dependency_prune_bypass_source_like_guards",
                    False,
                )
            )
            and not (
                state.global_dependency_prunable
                and self.cfg.env_bool(
                    "ARBITEROS_COST_DOWN_PHASE3_GLOBAL_DEPENDENCY_BYPASS_SOURCE_LIKE_GUARDS",
                    "global_dependency_bypass_source_like_guards",
                    True,
                )
            )
            and not state.late_source_like_compaction
            and (
                not self.plan.source_like_pre_edit_gate_met
                or state.index in self.plan.active_source_like_tool_indexes
            )
            and state.duplicate_later_idx is None
            and state.near_duplicate_later is None
            and state.repeated_line_folded is None
            and not _is_low_value_search_noise(state.text)
            and not _NOISY_TOOL_RE.search(state.text)
        )
        if source_like_pre_edit_blocked:
            blocked = self.plan.stats.setdefault("source_like_pre_edit_blocked", {})
            if isinstance(blocked, dict):
                blocked["messages"] = int(blocked.get("messages", 0) or 0) + 1
                blocked["min_input_tokens"] = self.cfg.env_int(
                    "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_PRE_EDIT_MIN_INPUT_TOKENS",
                    "source_like_pre_edit_min_input_tokens",
                    16000,
                )
                blocked["active_evidence_messages"] = int(
                    blocked.get("active_evidence_messages", 0) or 0
                ) + (1 if state.index in self.plan.active_source_like_tool_indexes else 0)
                blocked["original_estimated_input_tokens"] = self.plan.stats[
                    "original_estimated_input_tokens"
                ]
                blocked["reason"] = (
                    "recent_source_like_active_evidence_before_edit"
                    if state.index in self.plan.active_source_like_tool_indexes
                    else "awaiting_edit_signal_for_source_like_context"
                )
            state.fallback_allowed = False
            state.blocked = True

        if (
            self.plan.risk_budget.active
            and state.role == "tool"
            and state.index in self.plan.risk_budget.protected_tool_indexes
        ):
            self._mark_risk_guard_kept(state)
            state.blocked = True
            return

        if (
            exact_action not in {"compress", "gate_after_step"}
            and not state.fallback_allowed
            and state.duplicate_later_idx is None
            and state.near_duplicate_later is None
            and state.repeated_line_folded is None
        ):
            state.blocked = True
            return

        progress_signal = (
            str(state.context_policy.get("progress_signal"))
            if isinstance(state.context_policy, dict)
            and state.context_policy.get("progress_signal") is not None
            else None
        )
        state.target_ratio = self.cfg.target_ratio(
            state.context_policy if isinstance(state.context_policy, dict) else None,
            progress_signal,
        )
        if exact_action == "gate_after_step":
            state.target_ratio = min(
                state.target_ratio, _to_float(self.cfg.raw.get("gate_target_ratio"), 0.12)
            )
        if self.plan.pressure_active:
            state.target_ratio = min(
                state.target_ratio,
                min(max(self.plan.pressure_target_ratio, 0.05), 1.0),
            )
        source_like_focused = (
            state.role == "tool"
            and self.plan.source_like_structure
            and bool(self.plan.focus_terms)
            and state.source_like
            and _line_matches_focus_terms(state.text, self.plan.focus_terms)
        )
        if source_like_focused and self.plan.source_like_focused_target_ratio > 0:
            state.target_ratio = min(
                state.target_ratio,
                min(max(self.plan.source_like_focused_target_ratio, 0.05), 1.0),
            )
        if state.late_source_like_compaction:
            late_ratio = self.cfg.env_float(
                "ARBITEROS_COST_DOWN_PHASE3_LATE_SOURCE_LIKE_TARGET_RATIO",
                "late_source_like_target_ratio",
                0.10,
            )
            state.target_ratio = min(
                state.target_ratio,
                min(max(late_ratio, 0.03), 1.0),
            )
        if state.test_fixture_like:
            state.target_ratio = max(state.target_ratio, self.plan.test_fixture_target_ratio)
        if self.plan.risk_budget.conservative_active and state.role == "tool":
            state.target_ratio = max(
                state.target_ratio,
                self.plan.risk_budget.min_target_ratio,
            )
            if state.test_fixture_like:
                state.target_ratio = max(
                    state.target_ratio,
                    self.plan.risk_budget.test_fixture_target_ratio,
                )
        if state.dependency_protected:
            state.target_ratio = max(
                state.target_ratio,
                _to_float(self.cfg.raw.get("dependency_protected_target_ratio"), 0.45),
            )
            if self.plan.dependency_protected_action == "keep":
                self._mark_dependency_kept(state)
                state.blocked = True
                return
        if state.dependency_prunable:
            if state.dependency_offline_expired_context:
                self._record_offline_dependency_lifetime_prune(state)
            prune_ratio = self.cfg.env_float(
                "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_TARGET_RATIO",
                "dependency_prune_target_ratio",
                0.12,
            )
            state.target_ratio = min(state.target_ratio, min(max(prune_ratio, 0.03), 1.0))
        if state.global_dependency_prunable:
            global_ratio = self.cfg.env_float(
                "ARBITEROS_COST_DOWN_PHASE3_GLOBAL_DEPENDENCY_TARGET_RATIO",
                "global_dependency_target_ratio",
                0.08,
            )
            # A shared ancestor is still outside the active frontier, but it
            # can support several downstream decisions. Give it a larger
            # digest budget instead of treating it like disposable noise.
            if state.global_dependency_fanout >= 2:
                global_ratio = self.cfg.env_float(
                    "ARBITEROS_COST_DOWN_PHASE3_GLOBAL_DEPENDENCY_SHARED_TARGET_RATIO",
                    "global_dependency_shared_target_ratio",
                    min(max(global_ratio * 2.0, 0.12), 0.20),
                )
            state.target_ratio = min(
                state.target_ratio,
                min(max(global_ratio, 0.03), 1.0),
            )
        state.target_chars = self.cfg.target_chars(len(state.text), state.target_ratio)
        if state.dependency_prunable:
            prune_max_chars = self.cfg.env_int(
                "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_MAX_TARGET_CHARS",
                "dependency_prune_max_target_chars",
                520,
            )
            prune_min_chars = self.cfg.env_int(
                "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_MIN_TARGET_CHARS",
                "dependency_prune_min_target_chars",
                180,
            )
            if prune_max_chars > 0:
                state.target_chars = min(state.target_chars, prune_max_chars)
            state.target_chars = max(120, max(prune_min_chars, state.target_chars))
        if state.global_dependency_prunable:
            global_max_chars = self.cfg.env_int(
                "ARBITEROS_COST_DOWN_PHASE3_GLOBAL_DEPENDENCY_MAX_TARGET_CHARS",
                "global_dependency_max_target_chars",
                720,
            )
            global_min_chars = self.cfg.env_int(
                "ARBITEROS_COST_DOWN_PHASE3_GLOBAL_DEPENDENCY_MIN_TARGET_CHARS",
                "global_dependency_min_target_chars",
                180,
            )
            if state.global_dependency_fanout >= 2:
                global_max_chars = max(
                    global_max_chars,
                    self.cfg.env_int(
                        "ARBITEROS_COST_DOWN_PHASE3_GLOBAL_DEPENDENCY_SHARED_MAX_TARGET_CHARS",
                        "global_dependency_shared_max_target_chars",
                        max(global_max_chars * 2, 1200),
                    ),
                )
            if global_max_chars > 0:
                state.target_chars = min(state.target_chars, global_max_chars)
            state.target_chars = max(120, max(global_min_chars, state.target_chars))
        dependency_bypasses_source_floor = (
            state.dependency_prunable
            and self.cfg.env_bool(
                "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_PRUNE_BYPASS_SOURCE_LIKE_GUARDS",
                "dependency_prune_bypass_source_like_guards",
                False,
            )
        )
        if state.global_dependency_prunable and self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_GLOBAL_DEPENDENCY_BYPASS_SOURCE_LIKE_GUARDS",
            "global_dependency_bypass_source_like_guards",
            True,
        ):
            dependency_bypasses_source_floor = True
        if state.role == "tool" and state.source_like and not dependency_bypasses_source_floor:
            state.target_chars = max(state.target_chars, self.plan.source_like_min_target_chars)
            if self.plan.risk_budget.conservative_active:
                state.target_chars = max(
                    state.target_chars,
                    self.plan.risk_budget.source_like_min_target_chars,
                )
            if state.late_source_like_compaction:
                late_max_chars = self.cfg.env_int(
                    "ARBITEROS_COST_DOWN_PHASE3_LATE_SOURCE_LIKE_MAX_TARGET_CHARS",
                    "late_source_like_max_target_chars",
                    700,
                )
                late_min_chars = self.cfg.env_int(
                    "ARBITEROS_COST_DOWN_PHASE3_LATE_SOURCE_LIKE_MIN_TARGET_CHARS",
                    "late_source_like_min_target_chars",
                    360,
                )
                capped_target = state.target_chars
                if late_max_chars > 0:
                    capped_target = min(capped_target, late_max_chars)
                state.target_chars = max(160, late_min_chars, capped_target)
        elif state.role == "tool" and state.source_like and dependency_bypasses_source_floor:
            bypassed = self.plan.stats.setdefault(
                "dependency_source_like_guard_bypass",
                {},
            )
            if isinstance(bypassed, dict):
                bypassed["messages"] = int(bypassed.get("messages", 0) or 0) + 1
                bypassed["reason"] = "dependency_prunable_source_like_evidence"
        if len(state.text) <= state.target_chars:
            state.blocked = True
            return
        state.reason = self._reason(state, exact_action)
        state.match_type = "context_policy" if exact_action else "message_fallback"
        if state.role == "tool" and _is_low_value_search_noise(state.text):
            state.match_type = f"{state.match_type}+low_value_search_noise"
        if state.dependency_protected:
            state.match_type = f"{state.match_type}+dependency_protected"
        if state.dependency_prunable:
            state.match_type = f"{state.match_type}+dependency_prunable"
            if state.dependency_expired_reference:
                state.match_type = f"{state.match_type}+dependency_expired_reference"
            if state.dependency_offline_expired_context:
                state.match_type = f"{state.match_type}+dependency_offline_expired_context"
            if state.dependency_superseded:
                state.match_type = f"{state.match_type}+dependency_superseded"
            if state.dependency_unreferenced_stale:
                state.match_type = f"{state.match_type}+dependency_unreferenced_stale"
        if state.global_dependency_prunable:
            state.match_type = f"{state.match_type}+global_dependency_expired"
        if state.late_source_like_compaction:
            state.match_type = f"{state.match_type}+late_source_like"
        if self.plan.risk_budget.active and state.role == "tool":
            state.match_type = f"{state.match_type}+online_risk_guard"

    def _record_offline_dependency_lifetime_prune(self, state: MessageState) -> None:
        graph = self.plan.stats.setdefault("dependency_evidence_graph", {})
        if not isinstance(graph, dict):
            return
        offline = graph.setdefault("offline_expired_contexts", {})
        if not isinstance(offline, dict):
            return
        offline["messages"] = int(offline.get("messages", 0) or 0) + 1
        ids = offline.setdefault("context_ids", [])
        if isinstance(ids, list) and state.context_id and state.context_id not in ids:
            ids.append(state.context_id)
        offline["source"] = "context_policy.dependency_lifetime"

    def _mark_risk_guard_kept(self, state: MessageState) -> None:
        kept = self.plan.stats.setdefault("online_risk_guard_kept_tool_outputs", {})
        if not isinstance(kept, dict):
            return
        kept["messages"] = int(kept.get("messages", 0) or 0) + 1
        ids = kept.setdefault("tool_call_ids", [])
        tool_id = _tool_call_id(state.message)
        if isinstance(ids, list) and tool_id and tool_id not in ids:
            ids.append(tool_id)
        kept["reason"] = "recent tool evidence protected by online risk guard"

    def _mark_dependency_kept(self, state: MessageState) -> None:
        kept = self.plan.stats.setdefault("dependency_protected_tool_outputs_kept", {})
        if not isinstance(kept, dict):
            return
        kept["messages"] = int(kept.get("messages", 0) or 0) + 1
        ids = kept.setdefault("tool_call_ids", [])
        tool_id = _tool_call_id(state.message)
        if isinstance(ids, list) and tool_id and tool_id not in ids:
            ids.append(tool_id)
        kept["reason"] = "dependency_protected_action=keep"

    def _role_fallback_allowed(self, state: MessageState) -> bool:
        if not _to_bool(self.cfg.raw.get("allow_message_fallback"), True):
            return False
        min_original_chars = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_MESSAGE_FALLBACK_MIN_ORIGINAL_CHARS",
            "message_fallback_min_original_chars",
            0,
        )
        if min_original_chars > 0 and len(state.text) < min_original_chars:
            return False
        if not self.cfg.has_action("compress") and not self.cfg.has_action("gate_after_step"):
            return False
        if state.role == "tool":
            source_like_requires_gate = (
                state.source_like
                and self.cfg.env_bool(
                    "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_FALLBACK_REQUIRES_SOURCE_GATE",
                    "source_like_fallback_requires_source_gate",
                    False,
                )
                and not self.plan.source_like_fast_lane_active
            )
            if state.repeated_line_folded is not None:
                return True
            if source_like_requires_gate:
                return False
            if self._semantic_wide_allowed(state.text):
                return True
            if state.index in self.plan.stale_tool_indexes:
                if _NOISY_TOOL_RE.search(state.text):
                    return True
                if state.source_like and not self.cfg.env_bool(
                    "ARBITEROS_COST_DOWN_PHASE3_FALLBACK_COMPACT_SOURCE_LIKE_TOOL_OUTPUTS",
                    "fallback_compact_source_like_tool_outputs",
                    False,
                ):
                    return False
                if state.source_like:
                    return True
                folded = _fold_repeated_adjacent_lines(state.text)
                return len(folded) <= int(len(state.text) * 0.75)
            if len(state.text) >= _to_int(self.cfg.raw.get("long_tool_min_chars"), 6000):
                if _NOISY_TOOL_RE.search(state.text):
                    return True
                if state.source_like:
                    if self.cfg.env_bool(
                        "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_ONLY_WHEN_STALE",
                        "source_like_only_when_stale",
                        False,
                    ):
                        return False
                    return self.cfg.env_bool(
                        "ARBITEROS_COST_DOWN_PHASE3_FALLBACK_COMPACT_SOURCE_LIKE_TOOL_OUTPUTS",
                        "fallback_compact_source_like_tool_outputs",
                        False,
                    )
                folded = _fold_repeated_adjacent_lines(state.text)
                return len(folded) <= int(len(state.text) * 0.75)
        if state.role == "assistant" and state.index in self.plan.stale_assistant_indexes:
            return True
        return False

    def _semantic_wide_allowed(self, text: str) -> bool:
        return self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_SEMANTIC_DIGEST_ALLOW_WIDE_FALLBACK",
            "semantic_digest_allow_wide_fallback",
            False,
        ) and self._semantic_candidate(text)

    def _semantic_candidate(self, text: str) -> bool:
        if not self.plan.semantic_digest_enabled:
            return False
        min_chars = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_SEMANTIC_DIGEST_MIN_CHARS",
            "semantic_digest_min_chars",
            1200,
        )
        if min_chars and len(text) < min_chars:
            return False
        body = _tool_body(text)
        if _NOISY_TOOL_RE.search(body):
            return True
        if _PYTEST_NODE_RE.search(body) and _ANCHOR_RE.search(body):
            return True
        return self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_SEMANTIC_DIGEST_SOURCE_LIKE",
            "semantic_digest_source_like",
            False,
        ) and _looks_source_like(body)

    def _repeated_line_candidate(self, text: str) -> Optional[str]:
        if not self.cfg.env_bool(
            "ARBITEROS_COST_DOWN_PHASE3_REPEATED_LINE_FOLDING",
            "repeated_line_folding",
            False,
        ):
            return None
        folded = _fold_repeated_adjacent_lines(text)
        if folded == text:
            return None
        min_saved = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_REPEATED_LINE_FOLD_MIN_SAVED_CHARS",
            "repeated_line_fold_min_saved_chars",
            1000,
        )
        if len(text) - len(folded) < min_saved:
            return None
        max_ratio = min(
            max(
                self.cfg.env_float(
                    "ARBITEROS_COST_DOWN_PHASE3_REPEATED_LINE_FOLD_MAX_RATIO",
                    "repeated_line_fold_max_ratio",
                    0.85,
                ),
                0.05,
            ),
            0.99,
        )
        if len(folded) > int(len(text) * max_ratio):
            return None
        return folded

    def _reason(self, state: MessageState, exact_action: str) -> str:
        bits: list[str] = []
        if exact_action:
            bits.append(f"phase3 policy action={exact_action}")
        if state.context_id:
            bits.append(f"context_id={state.context_id}")
        if state.index in self.plan.stale_tool_indexes:
            bits.append(f"older than last {self.plan.keep_recent_tool} tool result(s)")
        if state.index in self.plan.stale_assistant_indexes:
            bits.append(
                f"older than last {self.plan.keep_recent_assistant} assistant text message(s)"
            )
        source_like_min = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_SOURCE_LIKE_FALLBACK_MIN_INPUT_TOKENS",
            "source_like_fallback_min_input_tokens",
            0,
        )
        if self.plan.source_like_fast_lane_active and state.source_like:
            bits.append(
                f"source-like fallback over {source_like_min} estimated input tokens"
            )
        if state.test_fixture_like:
            bits.append("test fixture-like output preserved or lightly compacted")
        if state.dependency_protected:
            bits.append("referenced by later tool call reference_tool_id")
        if state.dependency_prunable:
            if state.dependency_expired_reference:
                bits.append(
                    "last dependency edge is stale; later tool results supersede this output"
                )
            elif state.dependency_offline_expired_context:
                bits.append(
                    "offline Phase 3 dependency lifetime expired for this context"
                )
            elif state.dependency_superseded:
                later_idx = self.plan.dependency_superseding_later_index_by_tool_index.get(
                    state.index
                )
                reason = self.plan.dependency_supersede_reason_by_tool_index.get(
                    state.index,
                    "covered_by_later_tool_output",
                )
                if later_idx is not None:
                    bits.append(
                        f"stale evidence covered by later tool message {later_idx} ({reason})"
                    )
                else:
                    bits.append(f"stale evidence covered by later tool output ({reason})")
            elif state.dependency_unreferenced_stale:
                bits.append(
                    "ArbiterOS dependency graph shows no later step references this old tool output"
                )
            else:
                bits.append("no later dependency edge references this stale tool output")
        if not bits:
            bits.append("phase3 message fallback")
        return "; ".join(bits)


class DuplicateToolStrategy:
    def try_compress(self, state: MessageState, plan: CompressionPlan) -> Optional[CompressionResult]:
        if not plan.config.rule_enabled or state.role != "tool":
            return None
        if plan.risk_budget.active and plan.risk_budget.disable_duplicate_elision:
            return None
        if state.duplicate_later_idx is None or state.dependency_protected:
            return None
        body = "\n".join(
            [
                f"{PHASE3_MARKER} duplicate tool output omitted",
                f"same_as_later_message_index: {state.duplicate_later_idx}",
                f"original_chars: {len(state.text)}",
                "note: a later tool message carries the same normalized output.",
            ]
        )
        return CompressionResult(
            _wrap_tool_body(state.text, body),
            "phase3_duplicate_tool_elide",
            "deterministic_duplicate_elision",
        )


class NearDuplicateToolStrategy:
    def try_compress(self, state: MessageState, plan: CompressionPlan) -> Optional[CompressionResult]:
        if not plan.config.rule_enabled or state.role != "tool":
            return None
        if plan.risk_budget.active and plan.risk_budget.disable_near_duplicate_elision:
            return None
        if state.near_duplicate_later is None or state.dependency_protected:
            return None
        later_idx, overlap_ratio = state.near_duplicate_later
        anchors = _extract_anchors(state.text, max_lines=8)
        body_lines = [
            f"{PHASE3_MARKER} near-duplicate stale tool output omitted",
            f"covered_by_later_message_index: {later_idx}",
            f"line_overlap_ratio: {overlap_ratio:.2f}",
            f"original_chars: {len(state.text)}",
            "note: a later tool message preserves most nontrivial lines from this stale output.",
        ]
        if anchors:
            body_lines.extend(["anchors:", anchors])
        return CompressionResult(
            _wrap_tool_body(state.text, "\n".join(body_lines)),
            "phase3_near_duplicate_tool_elide",
            "deterministic_near_duplicate_elision",
        )


class GlobalDependencyEvidenceStrategy:
    """Compact evidence outside the live transitive dependency closure."""

    def try_compress(
        self, state: MessageState, plan: CompressionPlan
    ) -> Optional[CompressionResult]:
        if not (
            plan.config.rule_enabled
            and state.role == "tool"
            and state.global_dependency_prunable
        ):
            return None
        graph = plan.stats.get("global_dependency_graph")
        fanout = state.global_dependency_fanout
        note = "global ArbiterOS dependency closure expired"
        if fanout > 1:
            note += f"; shared by {fanout} downstream nodes"
        compacted, changed = _prune_unreferenced_tool_evidence(
            state.text,
            target_chars=state.target_chars,
            reason=state.reason,
            tool_call_id=_tool_call_id(state.message),
            evidence_graph_note=note,
            preserve_traceback_frames=True,
        )
        if not changed:
            return None
        if isinstance(graph, dict):
            graph["global_compacted_messages"] = int(
                graph.get("global_compacted_messages", 0) or 0
            ) + 1
        return CompressionResult(
            compacted,
            "phase3_global_dependency_evidence_prune",
            "deterministic_global_dependency_budget",
        )


class DependencyEvidencePruneStrategy:
    def try_compress(self, state: MessageState, plan: CompressionPlan) -> Optional[CompressionResult]:
        if not plan.config.rule_enabled or state.role != "tool" or not state.dependency_prunable:
            return None
        if state.dependency_expired_reference:
            evidence_graph_note = (
                "last dependency edge is stale; later tool results supersede this output"
            )
        elif state.dependency_offline_expired_context:
            evidence_graph_note = (
                "offline Phase 3 dependency lifetime expired for this context"
            )
        elif state.dependency_superseded:
            later_idx = plan.dependency_superseding_later_index_by_tool_index.get(
                state.index
            )
            reason = plan.dependency_supersede_reason_by_tool_index.get(
                state.index,
                "covered_by_later_tool_output",
            )
            if later_idx is None:
                evidence_graph_note = f"stale evidence is covered by a later tool output ({reason})"
            else:
                evidence_graph_note = (
                    f"stale evidence is covered by later message {later_idx} ({reason})"
                )
        elif state.dependency_unreferenced_stale:
            evidence_graph_note = (
                "ArbiterOS step dependency graph has no later reference to this old tool output"
            )
        else:
            evidence_graph_note = "no later tool call references this stale output"
        compacted, changed = _prune_unreferenced_tool_evidence(
            state.text,
            target_chars=state.target_chars,
            reason=state.reason,
            tool_call_id=_tool_call_id(state.message),
            evidence_graph_note=evidence_graph_note,
        )
        if not changed:
            return None
        return CompressionResult(
            compacted,
            "phase3_dependency_evidence_prune",
            "deterministic_dependency_evidence_pruner",
        )


class RepeatedLineFoldStrategy:
    def try_compress(self, state: MessageState, plan: CompressionPlan) -> Optional[CompressionResult]:
        if state.repeated_line_folded is None:
            return None
        return CompressionResult(
            state.repeated_line_folded,
            "phase3_repeated_line_fold",
            "deterministic_repeated_line_folding",
        )


class LowValueSearchNoiseStrategy:
    def try_compress(self, state: MessageState, plan: CompressionPlan) -> Optional[CompressionResult]:
        if not (
            plan.config.rule_enabled
            and state.role == "tool"
            and plan.config.env_bool(
                "ARBITEROS_COST_DOWN_PHASE3_LOW_VALUE_SEARCH_NOISE_PRUNING",
                "low_value_search_noise_pruning",
                True,
            )
            and _is_low_value_search_noise(state.text)
        ):
            return None
        max_chars = plan.config.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_LOW_VALUE_SEARCH_NOISE_MAX_CHARS",
            "low_value_search_noise_max_chars",
            1200,
        )
        target_chars = min(state.target_chars, max(360, max_chars)) if max_chars > 0 else state.target_chars
        compacted, changed = _compact_low_value_search_noise(
            state.text,
            target_chars=target_chars,
            focus_terms=plan.focus_terms,
        )
        if not changed:
            return None
        return CompressionResult(
            compacted,
            "phase3_low_value_search_noise_prune",
            "deterministic_low_value_search_noise_pruner",
        )


class SemanticDigestStrategy:
    def try_compress(self, state: MessageState, plan: CompressionPlan) -> Optional[CompressionResult]:
        if not (
            plan.config.rule_enabled
            and state.role == "tool"
            and plan.semantic_digest_enabled
            and GatePlanner(plan)._semantic_candidate(state.text)
        ):
            return None
        if plan.semantic_digest_hybrid:
            compacted, changed = _semantic_hybrid_tool_text(
                state.text,
                target_chars=state.target_chars,
                reason=state.reason,
                focus_terms=plan.focus_terms,
            )
            action = "phase3_semantic_rule_hybrid"
            backend = "deterministic_semantic_rule_hybrid"
        else:
            compacted, changed = _semantic_digest_tool_text(
                state.text,
                target_chars=state.target_chars,
                reason=state.reason,
                focus_terms=plan.focus_terms,
            )
            action = "phase3_semantic_digest"
            backend = "deterministic_semantic_digest"
        if not changed:
            return None
        return CompressionResult(compacted, action, backend)


class LLMCompressionStrategy:
    def __init__(self, llm_compressor: Optional[LLMCompressor]):
        self.llm_compressor = llm_compressor

    def try_compress(self, state: MessageState, plan: CompressionPlan) -> Optional[CompressionResult]:
        if not plan.config.llm_enabled or self.llm_compressor is None:
            return None
        target_chars, skip_reason = self._admit(state, plan)
        if skip_reason:
            self._record_skip(state, plan, skip_reason)
            return None
        try:
            trajectory_window = self._trajectory_window(state, plan)
            self._record_attempt(
                state,
                plan,
                target_chars=target_chars,
                trajectory_window=trajectory_window,
            )
            result = self.llm_compressor(
                text=state.text,
                target_chars=target_chars,
                context_policy=state.context_policy or {},
                runtime_policy=plan.config.policy,
                role=state.role,
                reason=state.reason,
                trajectory_window=trajectory_window,
            )
        except Exception as exc:
            self._record_skip(
                state,
                plan,
                "compressor_exception",
                detail=type(exc).__name__,
            )
            return None
        if not isinstance(result, str):
            self._record_skip(state, plan, "non_string_result")
            return None
        result = result.strip()
        if not result or len(result) >= len(state.text):
            self._record_skip(state, plan, "not_shorter_result")
            return None
        min_saved_tokens = self._min_saved_tokens(plan)
        actual_saved_tokens = max(0, (len(state.text) - len(result)) // 4)
        if min_saved_tokens > 0 and actual_saved_tokens < min_saved_tokens:
            self._record_skip(state, plan, "below_min_saved_tokens_after_llm")
            return None
        if state.role == "tool":
            parts = _wrapped_tool_parts(state.text)
            if parts is not None and _wrapped_tool_parts(result) is None:
                prefix, _body, suffix = parts
                result = f"{prefix}{result}{suffix}"
                if len(result) >= len(state.text):
                    self._record_skip(state, plan, "tool_wrapper_restore_not_shorter")
                    return None
        return CompressionResult(result, "phase3_llm_compress", "llm_semantic_summary")

    def _admit(self, state: MessageState, plan: CompressionPlan) -> tuple[int, Optional[str]]:
        cfg = plan.config
        preserve_recent = cfg.reflection_bool(
            "ARBITEROS_COST_DOWN_PHASE3_REFLECTION_PRESERVE_RECENT_STEP",
            "preserve_recent_step",
            True,
        )
        delay_steps = cfg.reflection_int(
            "ARBITEROS_COST_DOWN_PHASE3_REFLECTION_DELAY_STEPS",
            "delay_steps",
            0,
        )
        target_offset = cfg.reflection_int(
            "ARBITEROS_COST_DOWN_PHASE3_REFLECTION_TARGET_STEP_OFFSET",
            "target_step_offset",
            0,
        )
        later_messages = max(0, len(plan.messages) - 1 - state.index)
        min_later = max(delay_steps, target_offset if preserve_recent else 0)
        if min_later > 0 and later_messages < min_later:
            return state.target_chars, "recent_step_protected"

        skip_late_source_like = cfg.reflection_bool(
            "ARBITEROS_COST_DOWN_PHASE3_REFLECTION_SKIP_LATE_SOURCE_LIKE",
            "skip_late_source_like",
            False,
        )
        if skip_late_source_like and state.late_source_like_compaction:
            return state.target_chars, "late_source_like_protected"

        min_step_tokens = cfg.reflection_int(
            "ARBITEROS_COST_DOWN_PHASE3_REFLECTION_MIN_STEP_TOKENS",
            "min_step_tokens",
            0,
        )
        approx_tokens = max(0, (len(state.text) + 3) // 4)
        if min_step_tokens > 0 and approx_tokens < min_step_tokens:
            return state.target_chars, "below_min_step_tokens"

        keep_ratio = cfg.reflection_float(
            "ARBITEROS_COST_DOWN_PHASE3_REFLECTION_KEEP_RATIO",
            "keep_ratio_hint",
            0.0,
        )
        target_chars = state.target_chars
        if keep_ratio > 0:
            target_chars = min(target_chars, max(160, int(len(state.text) * keep_ratio)))

        min_saved_tokens = self._min_saved_tokens(plan)
        estimated_saved_tokens = max(0, (len(state.text) - target_chars) // 4)
        if min_saved_tokens > 0 and estimated_saved_tokens < min_saved_tokens:
            return target_chars, "below_min_saved_tokens_before_llm"
        return target_chars, None

    @staticmethod
    def _record_attempt(
        state: MessageState,
        plan: CompressionPlan,
        *,
        target_chars: int,
        trajectory_window: list[dict[str, Any]],
    ) -> None:
        attempts = plan.stats.setdefault("llm_reflection_attempts", {})
        if not isinstance(attempts, dict):
            return
        attempts["messages"] = int(attempts.get("messages", 0) or 0) + 1
        attempts["original_chars"] = int(attempts.get("original_chars", 0) or 0) + len(
            state.text
        )
        attempts["target_chars"] = int(attempts.get("target_chars", 0) or 0) + target_chars
        attempts["trajectory_window_items"] = int(
            attempts.get("trajectory_window_items", 0) or 0
        ) + len(trajectory_window)
        roles = attempts.setdefault("source_roles", {})
        if isinstance(roles, dict):
            roles[state.role] = int(roles.get(state.role, 0) or 0) + 1

    @staticmethod
    def _record_skip(
        state: MessageState,
        plan: CompressionPlan,
        reason: str,
        *,
        detail: Optional[str] = None,
    ) -> None:
        skipped = plan.stats.setdefault("llm_reflection_skipped", {})
        if not isinstance(skipped, dict):
            return
        skipped[reason] = int(skipped.get(reason, 0) or 0) + 1
        skipped["messages"] = int(skipped.get("messages", 0) or 0) + 1
        skipped["original_chars"] = int(skipped.get("original_chars", 0) or 0) + len(
            state.text
        )
        roles = skipped.setdefault("source_roles", {})
        if isinstance(roles, dict):
            roles[state.role] = int(roles.get(state.role, 0) or 0) + 1
        if detail:
            details = skipped.setdefault("details", {})
            if isinstance(details, dict):
                details[reason] = str(detail)[:120]

    @staticmethod
    def _min_saved_tokens(plan: CompressionPlan) -> int:
        return plan.config.reflection_int(
            "ARBITEROS_COST_DOWN_PHASE3_REFLECTION_MIN_SAVED_TOKENS",
            "min_saved_tokens",
            0,
        )

    @staticmethod
    def _trajectory_window(state: MessageState, plan: CompressionPlan) -> list[dict[str, Any]]:
        before = plan.config.reflection_int(
            "ARBITEROS_COST_DOWN_PHASE3_REFLECTION_WINDOW_BEFORE",
            "before",
            2,
        )
        after = plan.config.reflection_int(
            "ARBITEROS_COST_DOWN_PHASE3_REFLECTION_WINDOW_AFTER",
            "after",
            1,
        )
        start = max(0, state.index - max(0, before))
        stop = min(len(plan.messages), state.index + max(0, after) + 1)
        window: list[dict[str, Any]] = []
        for idx in range(start, stop):
            message = plan.messages[idx]
            if not isinstance(message, dict):
                continue
            mode, text = _content_mode_and_text(message)
            preview = text[:1200] if isinstance(text, str) else ""
            window.append(
                {
                    "index": idx,
                    "is_target": idx == state.index,
                    "role": str(message.get("role") or ""),
                    "context_id": _context_id(message),
                    "tool_call_id": _tool_call_id(message),
                    "content_mode": mode,
                    "preview": preview,
                    "original_chars": len(text or ""),
                }
            )
        return window


class HighSignalLLMCompressionStrategy(LLMCompressionStrategy):
    def try_compress(self, state: MessageState, plan: CompressionPlan) -> Optional[CompressionResult]:
        if not self._allow_high_signal_pre_rule(state, plan):
            return None
        return super().try_compress(state, plan)

    @staticmethod
    def _allow_high_signal_pre_rule(state: MessageState, plan: CompressionPlan) -> bool:
        if state.role != "tool":
            return False
        if state.dependency_prunable:
            return False
        if state.late_source_like_compaction:
            return False
        body = _tool_body(state.text)
        if not body.strip():
            return False
        if not (_ANCHOR_RE.search(body) or _NOISY_TOOL_RE.search(body)):
            return False
        if state.source_like:
            max_source_like_tokens = plan.config.reflection_int(
                "ARBITEROS_COST_DOWN_PHASE3_REFLECTION_SOURCE_LIKE_MAX_STEP_TOKENS",
                "source_like_max_step_tokens",
                1800,
            )
            approx_tokens = max(0, (len(state.text) + 3) // 4)
            if max_source_like_tokens > 0 and approx_tokens > max_source_like_tokens:
                return False
        return True


class RuleFallbackStrategy:
    def try_compress(self, state: MessageState, plan: CompressionPlan) -> Optional[CompressionResult]:
        if not plan.config.rule_enabled:
            return None
        source_like_structure = False
        if state.role == "assistant":
            compacted, changed = _compress_assistant_text(
                state.text,
                target_chars=state.target_chars,
                reason=state.reason,
            )
        else:
            source_like_structure = plan.source_like_structure
            if (
                plan.risk_budget.conservative_active
                and state.source_like
                and plan.risk_budget.source_like_raw_head_tail
            ):
                source_like_structure = False
            compacted, changed = _compress_tool_text(
                state.text,
                target_chars=state.target_chars,
                reason=state.reason,
                focus_terms=plan.focus_terms,
                source_like_structure=source_like_structure,
                source_like_focus_context_lines=plan.source_like_focus_context_lines,
                source_like_max_structure_lines_when_focused=plan.source_like_max_structure_lines_when_focused,
            )
        if not changed:
            return None
        backend = (
            "deterministic_source_like_structure"
            if state.role == "tool" and source_like_structure and state.source_like
            else "deterministic_head_tail_anchor"
        )
        return CompressionResult(compacted, "phase3_rule_compress", backend)


class AdmissionController:
    def admit(self, state: MessageState, result: CompressionResult, plan: CompressionPlan) -> bool:
        saved_tokens = max(0, len(state.text) - len(result.text)) // 4
        if (
            plan.min_saved_tokens_per_message > 0
            and saved_tokens < plan.min_saved_tokens_per_message
        ):
            skipped = plan.stats.setdefault("min_saved_tokens_per_message_skipped", {})
            if isinstance(skipped, dict):
                skipped["messages"] = int(skipped.get("messages", 0) or 0) + 1
                skipped["min_saved_tokens_per_message"] = plan.min_saved_tokens_per_message
                skipped["saved_tokens_below_threshold"] = int(
                    skipped.get("saved_tokens_below_threshold", 0) or 0
                ) + saved_tokens
            return False
        return True


class DependencyInvocationArgumentCompactor:
    def __init__(self, plan: CompressionPlan):
        self.plan = plan
        self.cfg = plan.config

    def apply(self) -> bool:
        if not (
            self.cfg.rule_enabled
            and self.cfg.env_bool(
                "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_COMPACT_PRUNABLE_INVOCATIONS",
                "dependency_compact_prunable_invocations",
                False,
            )
        ):
            return False
        prunable_ids = {
            self.plan.tool_id_by_index[idx]
            for idx in (
                self.plan.dependency_prunable_tool_indexes
                | self.plan.global_dependency_prunable_tool_indexes
            )
            if idx in self.plan.tool_id_by_index
        }
        if not prunable_ids:
            return False

        min_chars = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_INVOCATION_MIN_CHARS",
            "dependency_invocation_min_chars",
            900,
        )
        max_chars = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_INVOCATION_MAX_TARGET_CHARS",
            "dependency_invocation_max_target_chars",
            520,
        )
        min_saved_tokens = self.cfg.env_int(
            "ARBITEROS_COST_DOWN_PHASE3_DEPENDENCY_INVOCATION_MIN_SAVED_TOKENS",
            "dependency_invocation_min_saved_tokens",
            48,
        )

        changed = False
        for message in self.plan.messages:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                tool_call_id = tool_call.get("id") or tool_call.get("tool_call_id")
                if not isinstance(tool_call_id, str) or tool_call_id not in prunable_ids:
                    continue
                if self._compact_tool_call_arguments(
                    tool_call,
                    tool_call_id=tool_call_id,
                    min_chars=min_chars,
                    max_chars=max_chars,
                    min_saved_tokens=min_saved_tokens,
                ):
                    changed = True
        return changed

    def _compact_tool_call_arguments(
        self,
        tool_call: dict[str, Any],
        *,
        tool_call_id: str,
        min_chars: int,
        max_chars: int,
        min_saved_tokens: int,
    ) -> bool:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return False
        arguments, raw_arguments = _tool_call_arguments(tool_call)
        if arguments is None:
            return False
        original_text = (
            raw_arguments
            if isinstance(raw_arguments, str)
            else json.dumps(raw_arguments, ensure_ascii=False, default=str)
        )
        if len(original_text) < min_chars:
            return False

        command_text = self._primary_argument_text(arguments)
        summary_source = command_text or original_text
        compacted = self._compact_invocation_text(
            summary_source,
            tool_call_id=tool_call_id,
            max_chars=max_chars,
            original_argument_chars=len(original_text),
        )
        if not compacted:
            return False

        next_arguments = _reference_argument_subset(arguments)
        command_keys = [
            key
            for key in ("command", "cmd", "script", "input")
            if isinstance(arguments.get(key), str)
        ]
        if command_keys:
            next_arguments[command_keys[0]] = compacted
        else:
            next_arguments["summary"] = compacted
        next_arguments["arbiteros_compacted_arguments"] = True
        next_arguments["original_argument_chars"] = len(original_text)

        compacted_text = json.dumps(next_arguments, ensure_ascii=False, separators=(",", ":"))
        if len(compacted_text) >= len(original_text):
            return False
        saved_tokens = (len(original_text) - len(compacted_text)) // 4
        if min_saved_tokens > 0 and saved_tokens < min_saved_tokens:
            return False

        function["arguments"] = compacted_text
        self._record(tool_call_id, len(original_text), len(compacted_text))
        return True

    @staticmethod
    def _primary_argument_text(arguments: dict[str, Any]) -> str:
        for key in ("command", "cmd", "script", "input"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    @staticmethod
    def _compact_invocation_text(
        text: str,
        *,
        tool_call_id: str,
        max_chars: int,
        original_argument_chars: int,
    ) -> str:
        target = max(240, max_chars if max_chars > 0 else 520)
        anchors = _extract_invocation_anchors(text, max_items=10)
        header_lines = [
            f"{PHASE3_MARKER} compacted historical tool invocation",
            f"tool_call_id: {tool_call_id}",
            f"original_argument_chars: {original_argument_chars}",
        ]
        if anchors:
            header_lines.append("anchors:")
            header_lines.extend(f"- {anchor}" for anchor in anchors[:10])
        header = "\n".join(header_lines).rstrip()
        omitted = "\n[... omitted historical invocation body ...]\n"
        budget = target - len(header) - len(omitted)
        if budget <= 80:
            return header[:target]
        head = max(40, int(budget * 0.62))
        tail = max(40, budget - head)
        return f"{header}{omitted}{text[:head].rstrip()}\n...\n{text[-tail:].lstrip()}"

    def _record(self, tool_call_id: str, before_chars: int, after_chars: int) -> None:
        stats = self.plan.stats.setdefault("dependency_prunable_invocation_compaction", {})
        if not isinstance(stats, dict):
            return
        stats["tool_calls"] = int(stats.get("tool_calls", 0) or 0) + 1
        stats["original_chars"] = int(stats.get("original_chars", 0) or 0) + before_chars
        stats["compacted_chars"] = int(stats.get("compacted_chars", 0) or 0) + after_chars
        stats["saved_chars"] = int(stats.get("saved_chars", 0) or 0) + max(
            0, before_chars - after_chars
        )
        ids = stats.setdefault("tool_call_ids", [])
        if isinstance(ids, list) and tool_call_id not in ids:
            ids.append(tool_call_id)
        ActionTelemetry.add(
            self.plan.stats,
            action="phase3_dependency_invocation_prune",
            role="assistant",
            backend="deterministic_dependency_invocation_argument_pruner",
            before_chars=before_chars,
            after_chars=after_chars,
            target_ratio=after_chars / max(1, before_chars),
            match_type="dependency_prunable_invocation",
            rule_id=None,
        )


def _compact_text_to_target(
    text: str,
    *,
    target_chars: int,
    label: str,
    reason: str,
    include_anchors: bool,
) -> tuple[str, bool]:
    if target_chars <= 0 or len(text) <= target_chars:
        return text, False
    folded = _fold_repeated_adjacent_lines(text)
    source = folded if len(folded) < len(text) else text
    anchors = _extract_anchors(text) if include_anchors else ""
    header_lines = [f"{PHASE3_MARKER} compacted {label}"]
    if _env_bool("ARBITEROS_COST_DOWN_PHASE3_VERBOSE_COMPACTION_REASON", False):
        header_lines.append(f"reason: {reason}")
    header_lines.append(f"original_chars: {len(text)}")
    if anchors:
        header_lines.extend(["anchors:", anchors])
    header = "\n".join(header_lines).rstrip() + "\n\n"
    omitted = "\n\n[... omitted middle ...]\n\n"
    available = max(0, target_chars - len(header) - len(omitted))
    if available <= 80:
        compacted = header + source[: max(40, target_chars - len(header))].rstrip()
    else:
        head_chars = max(40, int(available * 0.62))
        tail_chars = max(40, available - head_chars)
        compacted = header + source[:head_chars].rstrip() + omitted + source[-tail_chars:].lstrip()
    return compacted, compacted != text


def _compact_low_value_search_noise(
    text: str,
    *,
    target_chars: int,
    focus_terms: set[str],
) -> tuple[str, bool]:
    noisy, kept = _low_value_search_noise_lines(text)
    if not noisy:
        return text, False

    focused_kept = [
        line
        for line in kept
        if _line_matches_focus_terms(line, focus_terms)
        or _SOURCE_LIKE_RE.search(line)
        or _ANCHOR_RE.search(line)
    ]
    if not focused_kept:
        focused_kept = kept

    kept_lines = _unique_preserve_order(focused_kept, limit=28)
    noisy_examples = _unique_preserve_order(noisy, limit=6)
    body_lines = [
        f"{PHASE3_MARKER} low_value_search_noise_pruned",
        f"original_chars: {len(text)}",
        f"omitted_locale_or_binary_lines: {len(noisy)}",
    ]
    if kept_lines:
        body_lines.append("kept_non_locale_hits:")
        body_lines.extend(f"- {line[:220]}" for line in kept_lines)
    if noisy_examples:
        body_lines.append("omitted_examples:")
        body_lines.extend(f"- {line[:180]}" for line in noisy_examples)

    compacted_body = "\n".join(body_lines).strip()
    if target_chars > 0 and len(compacted_body) > target_chars:
        marker = "\n[low-value search summary truncated]"
        compacted_body = compacted_body[: max(0, target_chars - len(marker))].rstrip() + marker
    compacted = _wrap_tool_body(text, compacted_body)
    return compacted, len(compacted) < len(text)


def _compact_source_like_text_to_target(
    text: str,
    *,
    target_chars: int,
    reason: str,
    focus_terms: set[str],
    focus_context_lines: int = 2,
    max_structure_lines_when_focused: int = 24,
) -> tuple[str, bool]:
    if target_chars <= 0 or len(text) <= target_chars:
        return text, False

    lines = text.splitlines()
    selected_by_idx: dict[int, tuple[str, str]] = {}
    structure_lines = 0
    focus_line_indexes: set[int] = set()
    for idx, raw_line in enumerate(lines, 1):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        label = ""
        if _ANCHOR_RE.search(line):
            label = "anchor"
            if _line_matches_focus_terms(line, focus_terms):
                focus_line_indexes.add(idx)
        elif _line_matches_focus_terms(line, focus_terms):
            label = "focus"
            focus_line_indexes.add(idx)
        elif _SOURCE_LIKE_RE.search(line):
            if focus_terms and structure_lines >= max_structure_lines_when_focused:
                continue
            label = "structure"
            structure_lines += 1
        if label:
            selected_by_idx[idx] = (label, line[:260])

    if focus_line_indexes and focus_context_lines > 0:
        for focus_idx in sorted(focus_line_indexes):
            start = max(1, focus_idx - focus_context_lines)
            end = min(len(lines), focus_idx + focus_context_lines)
            for idx in range(start, end + 1):
                if idx in selected_by_idx:
                    continue
                line = lines[idx - 1].rstrip()
                if line.strip():
                    selected_by_idx[idx] = ("context", line[:260])

    priority = {"anchor": 0, "focus": 1, "context": 2, "structure": 3}
    selected = [
        (idx, label, line)
        for idx, (label, line) in sorted(
            selected_by_idx.items(), key=lambda item: (priority.get(item[1][0], 9), item[0])
        )
    ]
    if not selected:
        return _compact_text_to_target(
            text,
            target_chars=target_chars,
            label="source_like_tool_output",
            reason=reason,
            include_anchors=True,
        )

    header_lines = [
        f"{PHASE3_MARKER} compacted source_like_tool_output",
        f"original_chars: {len(text)}",
        f"selected_lines: {len(selected)}",
    ]
    if _env_bool("ARBITEROS_COST_DOWN_PHASE3_VERBOSE_COMPACTION_REASON", False):
        header_lines.append(f"reason: {reason}")
    header = "\n".join(header_lines).rstrip() + "\n\n"
    tail_marker = "\n\n[... omitted unselected source/log lines ...]\n\n"
    tail_budget = min(900, max(240, target_chars // 5))
    available_after_header = max(0, target_chars - len(header))
    if available_after_header <= len(tail_marker) + 80:
        return _compact_text_to_target(
            text,
            target_chars=target_chars,
            label="source_like_tool_output",
            reason=reason,
            include_anchors=True,
        )
    min_body_budget = min(240, max(80, target_chars // 3))
    if available_after_header - len(tail_marker) - tail_budget < min_body_budget:
        tail_budget = max(0, available_after_header - len(tail_marker) - min_body_budget)
    body_budget = max(0, available_after_header - len(tail_marker) - tail_budget)
    out_lines: list[str] = []
    used = 0
    omitted_selected = 0
    for line_no, label, line in selected:
        formatted = f"L{line_no} [{label}] {line}"
        next_used = used + len(formatted) + 1
        if next_used > body_budget:
            omitted_selected += 1
            if not out_lines and body_budget > 40:
                out_lines.append(formatted[: max(0, body_budget - 16)].rstrip() + " [truncated]")
            continue
        out_lines.append(formatted)
        used = next_used
    selected_block = "\n".join(out_lines).rstrip()
    omitted_note = (
        f"\n[omitted {omitted_selected} selected line(s)]"
        if omitted_selected > 0
        else ""
    )
    if omitted_note and len(selected_block) + len(omitted_note) <= body_budget:
        selected_block += omitted_note
    compacted_prefix = header + selected_block + tail_marker
    remaining_tail_budget = max(0, target_chars - len(compacted_prefix))
    tail = text[-min(tail_budget, remaining_tail_budget) :].lstrip()
    compacted = compacted_prefix + tail
    if len(compacted) > target_chars:
        tail = tail[: max(0, len(tail) - (len(compacted) - target_chars))]
        compacted = compacted_prefix + tail
    return compacted, compacted != text


def _semantic_digest_tool_text(
    text: str,
    *,
    target_chars: int,
    reason: str,
    focus_terms: Optional[set[str]] = None,
) -> tuple[str, bool]:
    if target_chars <= 0 or len(text) <= target_chars:
        return text, False
    parts = _wrapped_tool_parts(text)
    prefix = ""
    suffix = ""
    body = text
    if parts is not None:
        prefix, body, suffix = parts

    focus_terms = focus_terms or set()
    lines = body.splitlines()
    folded_body = _fold_repeated_adjacent_lines(body)
    paths = _unique_preserve_order(_PATH_FOCUS_RE.findall(body), limit=16)
    nodeids = _unique_preserve_order(_PYTEST_NODE_RE.findall(body), limit=12)
    count_matches = _unique_preserve_order(
        [match.group(0).strip("= ") for match in _FAILED_COUNT_RE.finditer(body)],
        limit=12,
    )
    returncode = ""
    match = _RETURN_CODE_RE.search(text)
    if match:
        returncode = " ".join(match.group(1).split())

    anchor_lines: list[str] = []
    focus_lines: list[str] = []
    command_lines: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        lower = line.lower()
        if (
            lower.startswith(("$ ", "python ", "python3 ", "pytest ", "git ", "uv "))
            or "manage.py test" in lower
            or "apply_patch" in lower
        ):
            command_lines.append(line[:240])
        if _ANCHOR_RE.search(line):
            anchor_lines.append(line[:260])
        if focus_terms and _line_matches_focus_terms(line, focus_terms):
            focus_lines.append(line[:260])

    symbols = _unique_preserve_order(
        [
            token
            for token in _IDENTIFIER_RE.findall(body)
            if token.lower() not in _LOW_VALUE_FOCUS_TERMS
        ],
        limit=18,
    )
    command_lines = _unique_preserve_order(command_lines, limit=8)
    anchor_lines = _unique_preserve_order(anchor_lines, limit=18)
    priority_re = re.compile(
        r"\b(?:AssertionError|ImportError|ModuleNotFoundError|TypeError|ValueError|"
        r"RuntimeError|Exception|FAILED|ERROR)\b",
        re.IGNORECASE,
    )
    anchor_lines = sorted(anchor_lines, key=lambda line: 0 if priority_re.search(line) else 1)
    focus_lines = _unique_preserve_order(focus_lines, limit=14)

    header = [f"{PHASE3_MARKER} semantic_digest tool_output", f"original_chars: {len(text)}"]
    if returncode:
        header.append(f"returncode: {returncode}")
    if _env_bool("ARBITEROS_COST_DOWN_PHASE3_VERBOSE_COMPACTION_REASON", False):
        header.append(f"reason: {reason}")

    sections: list[str] = ["\n".join(header)]
    if command_lines:
        sections.append("commands:\n" + "\n".join(f"- {line}" for line in command_lines))
    if count_matches or nodeids:
        sections.append(
            "test_signals:\n" + "\n".join(f"- {line}" for line in [*count_matches, *nodeids])
        )
    if focus_lines:
        sections.append("focus_evidence:\n" + "\n".join(f"- {line}" for line in focus_lines))
    if anchor_lines:
        sections.append(
            "error_or_status_anchors:\n" + "\n".join(f"- {line}" for line in anchor_lines)
        )
    if paths:
        sections.append("paths:\n" + "\n".join(f"- {line[:220]}" for line in paths))
    if symbols:
        sections.append("symbols:\n" + ", ".join(symbols))

    digest = "\n\n".join(sections).rstrip()
    if len(folded_body) < len(body):
        digest += (
            f"\n\nrepetition: folded adjacent duplicate lines saved "
            f"{len(body) - len(folded_body)} chars"
        )
    tail_budget = min(900, max(160, target_chars // 5))
    evidence_tail = body[-tail_budget:].strip()
    tail_section = "\n\ntail_evidence:\n" + evidence_tail if evidence_tail else ""

    if len(digest) + len(tail_section) > target_chars:
        marker = "\n[semantic digest truncated]"
        if tail_section:
            main_budget = target_chars - len(tail_section) - len(marker)
            if main_budget < 120:
                tail_budget = max(80, target_chars // 4)
                evidence_tail = body[-tail_budget:].strip()
                tail_section = "\n\ntail_evidence:\n" + evidence_tail
                main_budget = max(120, target_chars - len(tail_section) - len(marker))
            digest = digest[:main_budget].rstrip() + marker
        else:
            digest = digest[: max(0, target_chars - len(marker))].rstrip() + marker
    digest += tail_section
    if parts is not None:
        digest = f"{prefix}{digest}{suffix}"
    return digest, len(digest) < len(text)


def _unwrap_tool_output_text(text: str) -> str:
    parts = _wrapped_tool_parts(text)
    if parts is None:
        return text.strip()
    return parts[1].strip()


def _semantic_hybrid_tool_text(
    text: str,
    *,
    target_chars: int,
    reason: str,
    focus_terms: Optional[set[str]] = None,
) -> tuple[str, bool]:
    if target_chars <= 0 or len(text) <= target_chars:
        return text, False
    parts = _wrapped_tool_parts(text)
    prefix = ""
    suffix = ""
    if parts is not None:
        prefix, _body, suffix = parts
    body_budget = max(160, target_chars - len(prefix) - len(suffix))
    digest_budget = max(220, int(body_budget * 0.38))
    evidence_budget = max(220, body_budget - digest_budget - 80)
    digest, digest_changed = _semantic_digest_tool_text(
        text,
        target_chars=digest_budget + len(prefix) + len(suffix),
        reason=reason,
        focus_terms=focus_terms,
    )
    evidence, evidence_changed = _compress_tool_text(
        text,
        target_chars=evidence_budget + len(prefix) + len(suffix),
        reason=reason,
        focus_terms=focus_terms,
        source_like_structure=False,
    )
    if not digest_changed and not evidence_changed:
        return text, False
    hybrid_body = (
        _unwrap_tool_output_text(digest)
        + "\n\npreserved_rule_evidence:\n"
        + _unwrap_tool_output_text(evidence)
    ).strip()
    if len(hybrid_body) > body_budget:
        marker = "\n[hybrid semantic/rule compaction truncated]"
        hybrid_body = hybrid_body[: max(0, body_budget - len(marker))].rstrip() + marker
    hybrid = f"{prefix}{hybrid_body}{suffix}" if parts is not None else hybrid_body
    return hybrid, len(hybrid) < len(text)


def _prune_unreferenced_tool_evidence(
    text: str,
    *,
    target_chars: int,
    reason: str,
    tool_call_id: Optional[str],
    evidence_graph_note: str = "no later tool call references this stale output",
    preserve_traceback_frames: bool = False,
) -> tuple[str, bool]:
    if target_chars <= 0 or len(text) <= target_chars:
        return text, False
    parts = _wrapped_tool_parts(text)
    prefix = ""
    suffix = ""
    body = text
    if parts is not None:
        prefix, body, suffix = parts

    body_target = max(120, target_chars - len(prefix) - len(suffix))
    nonempty = [line.strip() for line in body.splitlines() if line.strip()]
    head = _unique_preserve_order(nonempty[:3], limit=3)
    tail = _unique_preserve_order(nonempty[-3:], limit=3)
    anchors = (
        _extract_dependency_evidence_anchors(body, max_lines=10)
        if preserve_traceback_frames
        else _extract_anchors(body, max_lines=4)
    )

    lines = [
        f"{PHASE3_MARKER} pruned unreferenced tool evidence",
        f"evidence_graph: {evidence_graph_note}",
        f"original_chars: {len(text)}",
    ]
    if tool_call_id:
        lines.append(f"tool_call_id: {tool_call_id}")
    if _env_bool("ARBITEROS_COST_DOWN_PHASE3_VERBOSE_COMPACTION_REASON", False):
        lines.append(f"reason: {reason}")
    if anchors:
        lines.extend(["anchors:", anchors])
    if head:
        lines.extend(["head:", *[f"- {line[:220]}" for line in head]])
    tail_only = [line for line in tail if line not in head]
    if tail_only:
        lines.extend(["tail:", *[f"- {line[:220]}" for line in tail_only]])

    compacted_body = "\n".join(lines).strip()
    if len(compacted_body) > body_target:
        marker = "\n[dependency evidence summary truncated]"
        compacted_body = compacted_body[: max(0, body_target - len(marker))].rstrip() + marker
    compacted = f"{prefix}{compacted_body}{suffix}" if parts is not None else compacted_body
    return compacted, len(compacted) < len(text)


def _compress_tool_text(
    text: str,
    *,
    target_chars: int,
    reason: str,
    focus_terms: Optional[set[str]] = None,
    source_like_structure: bool = False,
    source_like_focus_context_lines: int = 2,
    source_like_max_structure_lines_when_focused: int = 24,
) -> tuple[str, bool]:
    if source_like_structure and _looks_source_like(_tool_body(text)):
        parts = _wrapped_tool_parts(text)
        if parts is None:
            return _compact_source_like_text_to_target(
                text,
                target_chars=target_chars,
                reason=reason,
                focus_terms=focus_terms or set(),
                focus_context_lines=source_like_focus_context_lines,
                max_structure_lines_when_focused=source_like_max_structure_lines_when_focused,
            )
        prefix, body, suffix = parts
        body_target_chars = max(160, target_chars - len(prefix) - len(suffix))
        compacted_body, changed = _compact_source_like_text_to_target(
            body,
            target_chars=body_target_chars,
            reason=reason,
            focus_terms=focus_terms or set(),
            focus_context_lines=source_like_focus_context_lines,
            max_structure_lines_when_focused=source_like_max_structure_lines_when_focused,
        )
        if not changed:
            return text, False
        return f"{prefix}{compacted_body}{suffix}", True

    parts = _wrapped_tool_parts(text)
    if parts is None:
        return _compact_text_to_target(
            text,
            target_chars=target_chars,
            label="tool_output",
            reason=reason,
            include_anchors=True,
        )
    prefix, body, suffix = parts
    body_target_chars = max(160, target_chars - len(prefix) - len(suffix))
    compacted_body, changed = _compact_text_to_target(
        body,
        target_chars=body_target_chars,
        label="tool_output_body",
        reason=reason,
        include_anchors=True,
    )
    if not changed:
        return text, False
    return f"{prefix}{compacted_body}{suffix}", True


def _compress_assistant_text(
    text: str,
    *,
    target_chars: int,
    reason: str,
) -> tuple[str, bool]:
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("content"), str):
        return _compact_text_to_target(
            text,
            target_chars=target_chars,
            label="assistant_history",
            reason=reason,
            include_anchors=True,
        )
    wrapped = dict(parsed)
    compacted, changed = _compact_text_to_target(
        str(wrapped.get("content") or ""),
        target_chars=target_chars,
        label="assistant_history",
        reason=reason,
        include_anchors=True,
    )
    if not changed:
        return text, False
    wrapped["content"] = compacted
    return json.dumps(wrapped, ensure_ascii=False, separators=(",", ":")), True


class Phase3RuntimeCompressionEngine:
    def __init__(self, *, llm_compressor: Optional[LLMCompressor] = None):
        self.llm_compressor = llm_compressor

    def apply(
        self,
        request_data: dict[str, Any],
        *,
        trace_id: Optional[str],
        global_instructions: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        config = Phase3Config.load()
        stats: dict[str, Any] = {
            "enabled": config.enabled,
            "source": "phase3_runtime_policy",
            "trace_id": trace_id,
            "changed": False,
            "rule_compression_enabled": config.rule_enabled,
            "llm_compression_enabled": config.llm_enabled,
            "engine": "phase3_runtime_v2",
            "original_estimated_input_tokens": (
                estimate_request_input_tokens(request_data)
                if isinstance(request_data, dict)
                else 0
            ),
        }
        if not config.enabled or not isinstance(request_data, dict):
            return self._unchanged(request_data, stats)
        if not config.policy:
            stats["reason"] = "missing_phase3_runtime_policy"
            return self._unchanged(request_data, stats)
        messages = request_data.get("messages")
        if not isinstance(messages, list):
            stats["reason"] = "request_has_no_chat_messages"
            return self._unchanged(request_data, stats)
        try:
            from arbiteros_kernel.cost.down import phase3_compression_activation_guard

            online_guard = phase3_compression_activation_guard(request_data)
        except Exception:
            online_guard = {"enabled": False, "met": True, "reason": "unavailable"}
        stats["compression_activation_guard"] = online_guard
        if online_guard.get("enabled") and not online_guard.get("met"):
            stats["reason"] = online_guard.get("reason") or "phase3_deferred_early_compression"
            return self._unchanged(request_data, stats)
        if not config.context_policies and not config.has_runtime_fallback_policy():
            stats["reason"] = "phase3_policy_has_no_context_policies"
            return self._unchanged(request_data, stats)

        data = copy.deepcopy(request_data)
        compacted_messages = data.get("messages")
        if not isinstance(compacted_messages, list):
            return self._unchanged(request_data, stats)

        plan = CompressionPlan(
            data=data,
            messages=compacted_messages,
            config=config,
            stats=stats,
            global_instructions=(
                [item for item in (global_instructions or []) if isinstance(item, dict)]
            ),
        )
        if config.llm_enabled and self.llm_compressor is None:
            stats["llm_compression_status"] = "enabled_but_no_compressor"

        CandidateIndexBuilder(plan).build()
        RiskBudgetController(plan).configure()
        gate = GatePlanner(plan)
        gate.configure()
        llm_strategy = LLMCompressionStrategy(self.llm_compressor)
        rule_fallback_strategy = RuleFallbackStrategy()
        llm_position = config.llm_position()
        stats["llm_compression_position"] = llm_position
        strategies: list[CompressionStrategy] = [
            DuplicateToolStrategy(),
            NearDuplicateToolStrategy(),
            GlobalDependencyEvidenceStrategy(),
            DependencyEvidencePruneStrategy(),
            RepeatedLineFoldStrategy(),
            LowValueSearchNoiseStrategy(),
            SemanticDigestStrategy(),
        ]
        if llm_position == "after_rule":
            strategies.extend([rule_fallback_strategy, llm_strategy])
        elif llm_position == "hybrid_high_signal":
            strategies.extend(
                [
                    HighSignalLLMCompressionStrategy(self.llm_compressor),
                    rule_fallback_strategy,
                ]
            )
        else:
            strategies.extend([llm_strategy, rule_fallback_strategy])
        admission = AdmissionController()
        changed = DependencyInvocationArgumentCompactor(plan).apply()
        for idx, message in enumerate(compacted_messages):
            if not isinstance(message, dict):
                continue
            state = gate.build_state(idx, message)
            if state is None or state.blocked:
                continue
            for strategy in strategies:
                result = strategy.try_compress(state, plan)
                if result is None:
                    continue
                if not admission.admit(state, result, plan):
                    break
                _set_message_text_content(message, state.mode, result.text)
                ActionTelemetry.add(
                    stats,
                    action=result.action,
                    role=state.role,
                    backend=result.backend,
                    before_chars=len(state.text),
                    after_chars=len(result.text),
                    target_ratio=state.target_ratio,
                    match_type=state.match_type,
                    rule_id=(
                        str(state.context_policy.get("rule_id"))
                        if isinstance(state.context_policy, dict)
                        and state.context_policy.get("rule_id") is not None
                        else None
                    ),
                )
                changed = True
                break

        optimized_tokens = estimate_request_input_tokens(data)
        stats["optimized_estimated_input_tokens"] = optimized_tokens
        stats["estimated_input_tokens_saved"] = max(
            0, int(stats["original_estimated_input_tokens"] or 0) - optimized_tokens
        )
        stats["changed"] = changed
        if not changed:
            return request_data, stats
        metadata = data.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        metadata["arbiteros_runtime_cost_down"] = stats
        data["metadata"] = metadata
        return data, stats

    @staticmethod
    def _unchanged(
        request_data: dict[str, Any], stats: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        stats["optimized_estimated_input_tokens"] = stats["original_estimated_input_tokens"]
        stats["estimated_input_tokens_saved"] = 0
        return request_data, stats


def apply_phase3_runtime_compression(
    request_data: dict[str, Any],
    *,
    trace_id: Optional[str],
    llm_compressor: Optional[LLMCompressor] = None,
    global_instructions: Optional[list[dict[str, Any]]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return Phase3RuntimeCompressionEngine(llm_compressor=llm_compressor).apply(
        request_data,
        trace_id=trace_id,
        global_instructions=global_instructions,
    )
