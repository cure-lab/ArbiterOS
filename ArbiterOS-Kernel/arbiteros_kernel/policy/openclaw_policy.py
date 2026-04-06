from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME

from .policy import Policy

try:
    from arbiteros_kernel.policy_runtime import canonicalize_args  # type: ignore
except Exception:  # pragma: no cover
    def canonicalize_args(args: Dict[str, Any]) -> Dict[str, Any]:
        return args


TOOL_CALL_NAME_MAX_LEN = 64
TOOL_CALL_NAME_RE = re.compile(r"^[A-Za-z0-9_:.-]+$")
FINAL_TAG_RE = re.compile(r"<\s*/?\s*final\s*>", re.IGNORECASE)
THINKING_TAG_RE = re.compile(
    r"<\s*(?:think|thinking)\b[^>]*>.*?<\s*/\s*(?:think|thinking)\s*>",
    re.IGNORECASE | re.DOTALL,
)
EXTERNAL_BLOCK_RE = re.compile(
    r"""
    ^[ \t]*<<<EXTERNAL_UNTRUSTED_CONTENT\b[^>]*>>>[ \t]*\r?\n
    .*?
    ^[ \t]*<<<END_EXTERNAL_UNTRUSTED_CONTENT\b[^>]*>>>[ \t]*(?:\r?\n)?
    """,
    re.IGNORECASE | re.MULTILINE | re.DOTALL | re.VERBOSE,
)
LEGACY_INTERNAL_CONTEXT_RE = re.compile(
    r"""
    (?:
        ^[ \t]*OpenClaw\ runtime\ context\ \(internal\):.*?(?:\r?\n){1,2}
      | ^[ \t]*\[Internal\ task\ context\].*?(?:\r?\n){1,2}
      | ^[ \t]*\[Inter-session\ message\].*?(?:\r?\n)?
    )
    """,
    re.IGNORECASE | re.MULTILINE | re.DOTALL | re.VERBOSE,
)
REPLY_TO_CURRENT_RE = re.compile(r"\[\[\s*reply_to_current\s*\]\]", re.IGNORECASE)
REPLY_TO_ID_RE = re.compile(r"\[\[\s*reply_to\s*:\s*([^\]\s]+)\s*\]\]", re.IGNORECASE)

ERROR_PREFIX_RE = re.compile(
    r"^(?:error|(?:[a-z][\w-]*\s+)?api\s*error|openai\s*error|anthropic\s*error|gateway\s*error|codex\s*error|request failed|failed|exception)(?:\s+\d{3})?[:\s-]+",
    re.IGNORECASE,
)
CONTEXT_OVERFLOW_RE = re.compile(
    r"context overflow|request_too_large|request size exceeds|context length exceeded|maximum context length|prompt is too long|exceeds model context window",
    re.IGNORECASE,
)
RATE_LIMIT_RE = re.compile(r"rate limit|too many requests|quota|429\b", re.IGNORECASE)
BILLING_RE = re.compile(
    r"insufficient credits|insufficient quota|credit balance|insufficient balance|billing hard limit|hard limit reached|402\b",
    re.IGNORECASE,
)
AUTH_RE = re.compile(
    r"unauthorized|authentication|invalid api key|missing authentication|forbidden|401\b|403\b",
    re.IGNORECASE,
)
TIMEOUT_RE = re.compile(
    r"timeout|timed out|ETIMEDOUT|ECONNRESET|ECONNABORTED|ECONNREFUSED|ENETUNREACH|EHOSTUNREACH|EAI_AGAIN",
    re.IGNORECASE,
)

DEFAULT_WARNING_THRESHOLD = 10
DEFAULT_CRITICAL_THRESHOLD = 20
DEFAULT_GLOBAL_THRESHOLD = 30


@dataclass
class P1BlockedTool:
    tool_name: str
    instruction_type: str
    category: str
    reason: str


@dataclass
class ToolHistoryEvent:
    tool_call_id: str
    tool_name: str
    args_hash: str
    result_hash: Optional[str]


@dataclass
class LoopDecision:
    stuck: bool
    level: str
    detector: str
    count: int
    message: str
    paired_tool_name: Optional[str] = None


def _safe_str(v: Any, default: str = "") -> str:
    return v.strip() if isinstance(v, str) and v.strip() else default


def _safe_lower(v: Any, default: str = "") -> str:
    s = _safe_str(v, default)
    return s.lower() if s else default


def _safe_upper(v: Any, default: str = "") -> str:
    s = _safe_str(v, default)
    return s.upper() if s else default


def _safe_dict(v: Any) -> Dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _safe_list(v: Any) -> List[Any]:
    return v if isinstance(v, list) else []


def _coerce_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return str(v)


def _stable_json(v: Any) -> str:
    try:
        return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return repr(v)


def _hash(v: Any) -> str:
    return hashlib.sha256(_stable_json(v).encode("utf-8")).hexdigest()


def _normalize_name(name: Any) -> str:
    return _safe_lower(name)


def _normalize_entries(items: Iterable[Any]) -> Set[str]:
    out: Set[str] = set()
    for x in items:
        if isinstance(x, str) and x.strip():
            out.add(x.strip().lower())
    return out


def _latest_tool_instr_index(latest_instructions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for ins in latest_instructions or []:
        content = ins.get("content")
        if not isinstance(content, dict):
            continue
        tcid = content.get("tool_call_id")
        if isinstance(tcid, str) and tcid.strip():
            out[tcid.strip()] = ins
    return out


def _extract_tool_instruction_meta(ins: Optional[Dict[str, Any]], tool_name: str) -> Tuple[str, str]:
    if isinstance(ins, dict):
        instruction_type = _safe_upper(ins.get("instruction_type"))
        category = _safe_upper(ins.get("instruction_category"))
        if instruction_type or category:
            return instruction_type, category
    instruction_type = _safe_upper(RUNTIME.tool_to_instruction_type(tool_name))
    category = _safe_upper(RUNTIME.instruction_type_to_category(instruction_type))
    return instruction_type, category


def _tool_allowed(tool_name: str, instruction_type: str, category: str, cfg: Dict[str, Any]) -> Tuple[bool, str]:
    allow = _safe_dict(cfg.get("allow"))
    deny = _safe_dict(cfg.get("deny"))

    tool = _normalize_name(tool_name)
    instruction_type = _safe_lower(instruction_type)
    category = _safe_lower(category)

    deny_tools = _normalize_entries(_safe_list(deny.get("tools")))
    deny_instruction_types = _normalize_entries(_safe_list(deny.get("instruction_types")))
    deny_categories = _normalize_entries(_safe_list(deny.get("categories")))

    if tool in deny_tools or (tool == "apply_patch" and "write" in deny_tools):
        return False, "tool denied by allow/deny config"
    if instruction_type and instruction_type in deny_instruction_types:
        return False, "instruction_type denied by allow/deny config"
    if category and category in deny_categories:
        return False, "instruction_category denied by allow/deny config"

    allow_tools = _normalize_entries(_safe_list(allow.get("tools")))
    allow_instruction_types = _normalize_entries(_safe_list(allow.get("instruction_types")))
    allow_categories = _normalize_entries(_safe_list(allow.get("categories")))

    if allow_tools:
        if tool not in allow_tools and not (tool == "apply_patch" and "write" in allow_tools):
            return False, "tool not present in allow.tools"
    if allow_instruction_types:
        if not instruction_type or instruction_type not in allow_instruction_types:
            return False, "instruction_type not present in allow.instruction_types"
    if allow_categories:
        if not category or category not in allow_categories:
            return False, "instruction_category not present in allow.categories"

    return True, ""


def _redact_sessions_spawn_attachments(args_dict: Dict[str, Any]) -> Dict[str, Any]:
    attachments = args_dict.get("attachments")
    if not isinstance(attachments, list):
        return args_dict
    changed = False
    next_items: List[Any] = []
    for item in attachments:
        if not isinstance(item, dict) or "content" not in item:
            next_items.append(item)
            continue
        changed = True
        redacted = dict(item)
        redacted["content"] = "__OPENCLAW_REDACTED__"
        next_items.append(redacted)
    if not changed:
        return args_dict
    out = dict(args_dict)
    out["attachments"] = next_items
    return out


def _repair_tool_calls(tool_calls: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool, List[str]]:
    kept: List[Dict[str, Any]] = []
    changed = False
    reasons: List[str] = []

    for tc in tool_calls:
        tool_name, tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
        trimmed_name = _safe_str(tool_name)
        trimmed_id = _safe_str(tool_call_id)

        if raw_args is None or not isinstance(raw_args, dict):
            changed = True
            reasons.append(f"P10 dropped `{trimmed_name or '<unknown>'}`: missing input/arguments.")
            continue
        if not trimmed_id:
            changed = True
            reasons.append(f"P10 dropped `{trimmed_name or '<unknown>'}`: missing tool_call id.")
            continue
        if not trimmed_name or len(trimmed_name) > TOOL_CALL_NAME_MAX_LEN or TOOL_CALL_NAME_RE.match(trimmed_name) is None:
            changed = True
            reasons.append(f"P10 dropped `{trimmed_name or '<unknown>'}`: invalid tool name.")
            continue

        next_args = canonicalize_args(raw_args)
        if _normalize_name(trimmed_name) == "sessions_spawn":
            redacted = _redact_sessions_spawn_attachments(next_args)
            if redacted != next_args:
                next_args = redacted
                changed = True

        next_tc = RUNTIME.write_back_tool_args(tc, next_args, was_json_str)
        if next_tc != tc:
            changed = True
        kept.append(next_tc)

    return kept, changed, reasons


def _extract_tool_result_payload(ins: Dict[str, Any]) -> Any:
    content = ins.get("content")
    if isinstance(content, dict):
        for key in ("result", "tool_result", "output", "response", "value", "content"):
            if key in content:
                return content.get(key)
    for key in ("result", "tool_result", "output", "response", "value"):
        if key in ins:
            return ins.get(key)
    return None


def _extract_tool_call_payload(ins: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Any]:
    content = ins.get("content")
    merged: Dict[str, Any] = {}
    if isinstance(content, dict):
        merged.update(content)
    merged.update({k: v for k, v in ins.items() if k not in {"content", "security_type"}})

    tool_call_id = _safe_str(merged.get("tool_call_id") or merged.get("id"))
    tool_name = _safe_str(merged.get("tool_name") or merged.get("name"))
    args = None
    for key in ("args", "arguments", "input", "tool_args", "params"):
        if key in merged:
            args = merged.get(key)
            break

    if tool_name or tool_call_id or args is not None:
        return tool_call_id or None, tool_name or None, args
    return None, None, None


def _build_history(instructions: List[Dict[str, Any]], history_size: int) -> List[ToolHistoryEvent]:
    events: List[ToolHistoryEvent] = []
    pending: Dict[str, Tuple[str, str]] = {}
    anon = 0

    for ins in instructions or []:
        if not isinstance(ins, dict):
            continue

        call_id, tool_name, args = _extract_tool_call_payload(ins)
        if tool_name:
            normalized_tool_name = _normalize_name(tool_name)
            args_hash = _hash(args if args is not None else {})
            if call_id:
                pending[call_id] = (normalized_tool_name, args_hash)
            else:
                anon += 1
                pending[f"__anon__{anon}"] = (normalized_tool_name, args_hash)

        result = _extract_tool_result_payload(ins)
        if result is None:
            continue

        content = ins.get("content")
        tool_call_id = ""
        if isinstance(content, dict):
            tool_call_id = _safe_str(content.get("tool_call_id"))
        if not tool_call_id:
            tool_call_id = _safe_str(ins.get("tool_call_id"))

        if tool_call_id and tool_call_id in pending:
            tool_name, args_hash = pending.pop(tool_call_id)
        elif pending:
            last_key = next(reversed(pending))
            tool_name, args_hash = pending.pop(last_key)
            tool_call_id = last_key
        else:
            continue

        events.append(
            ToolHistoryEvent(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_hash=args_hash,
                result_hash=_hash(result),
            )
        )

    if history_size > 0 and len(events) > history_size:
        return events[-history_size:]
    return events


def _is_known_poll_tool(tool_name: str, args_dict: Dict[str, Any]) -> bool:
    normalized = _normalize_name(tool_name)
    if normalized == "command_status":
        return True
    if normalized != "process":
        return False
    action = _safe_lower(args_dict.get("action"))
    return action in {"poll", "log"}


def _get_no_progress_streak(history: List[ToolHistoryEvent], tool_name: str, args_hash: str) -> int:
    streak = 0
    latest_hash: Optional[str] = None
    target_tool = _normalize_name(tool_name)

    for event in reversed(history):
        if event.tool_name != target_tool or event.args_hash != args_hash:
            continue
        if not event.result_hash:
            continue
        if latest_hash is None:
            latest_hash = event.result_hash
            streak = 1
            continue
        if event.result_hash != latest_hash:
            break
        streak += 1

    return streak


def _get_ping_pong(history: List[ToolHistoryEvent], current_args_hash: str) -> Tuple[int, Optional[str], bool]:
    if len(history) < 2:
        return 0, None, False

    last = history[-1]
    other_signature: Optional[str] = None
    other_tool_name: Optional[str] = None

    for event in reversed(history[:-1]):
        if event.args_hash != last.args_hash:
            other_signature = event.args_hash
            other_tool_name = event.tool_name
            break

    if not other_signature or current_args_hash != other_signature:
        return 0, None, False

    alternating_tail_count = 0
    expected = last.args_hash
    for event in reversed(history):
        if event.args_hash != expected:
            break
        alternating_tail_count += 1
        expected = other_signature if expected == last.args_hash else last.args_hash

    if alternating_tail_count < 2:
        return 0, None, False

    tail = history[-alternating_tail_count:]
    first_a: Optional[str] = None
    first_b: Optional[str] = None
    no_progress = True

    for event in tail:
        if not event.result_hash:
            no_progress = False
            break
        if event.args_hash == last.args_hash:
            if first_a is None:
                first_a = event.result_hash
            elif first_a != event.result_hash:
                no_progress = False
                break
        elif event.args_hash == other_signature:
            if first_b is None:
                first_b = event.result_hash
            elif first_b != event.result_hash:
                no_progress = False
                break
        else:
            no_progress = False
            break

    if not first_a or not first_b:
        no_progress = False

    return alternating_tail_count + 1, other_tool_name, no_progress


def _resolve_p9_thresholds() -> Dict[str, Any]:
    rate_limit_cfg = _safe_dict(RUNTIME.cfg.get("rate_limit"))
    critical = int(rate_limit_cfg.get("max_consecutive_same_tool") or DEFAULT_CRITICAL_THRESHOLD)
    warning = min(DEFAULT_WARNING_THRESHOLD, critical)
    global_threshold = max(DEFAULT_GLOBAL_THRESHOLD, critical + 10)
    return {
        "history_size": 30,
        "warning_threshold": warning,
        "critical_threshold": critical,
        "global_threshold": global_threshold,
    }


def _detect_tool_loop(history: List[ToolHistoryEvent], tool_name: str, args_dict: Dict[str, Any]) -> Optional[LoopDecision]:
    if not history:
        return None

    thresholds = _resolve_p9_thresholds()
    args_hash = _hash(args_dict)
    no_progress_streak = _get_no_progress_streak(history, tool_name, args_hash) + 1
    known_poll = _is_known_poll_tool(tool_name, args_dict)

    if no_progress_streak >= thresholds["global_threshold"]:
        return LoopDecision(
            stuck=True,
            level="critical",
            detector="global_circuit_breaker",
            count=no_progress_streak,
            message=(
                f"CRITICAL: {tool_name} has repeated identical no-progress outcomes "
                f"{no_progress_streak} times. Session execution blocked."
            ),
        )

    if known_poll and no_progress_streak >= thresholds["critical_threshold"]:
        return LoopDecision(
            stuck=True,
            level="critical",
            detector="known_poll_no_progress",
            count=no_progress_streak,
            message=(
                f"CRITICAL: called {tool_name} with identical arguments and no progress "
                f"{no_progress_streak} times. This appears to be a stuck polling loop."
            ),
        )

    ping_pong_count, paired_tool_name, no_progress = _get_ping_pong(history, args_hash)
    if no_progress and ping_pong_count >= thresholds["critical_threshold"]:
        return LoopDecision(
            stuck=True,
            level="critical",
            detector="ping_pong",
            count=ping_pong_count,
            paired_tool_name=paired_tool_name,
            message=(
                f"CRITICAL: alternating repeated tool-call patterns ({ping_pong_count} consecutive calls) "
                f"with no progress. Session execution blocked."
            ),
        )

    return None


def _sanitize_user_facing_text(text: Any, error_context: bool = False) -> str:
    s = _coerce_text(text)
    if not s:
        return s

    s = FINAL_TAG_RE.sub("", s)
    s = THINKING_TAG_RE.sub("", s)
    s = EXTERNAL_BLOCK_RE.sub("", s)
    s = LEGACY_INTERNAL_CONTEXT_RE.sub("", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()

    lowered = s.lower()
    if error_context or ERROR_PREFIX_RE.search(s):
        if CONTEXT_OVERFLOW_RE.search(s):
            return "Context overflow: prompt too large for the model. Try again with less input or a larger-context model."
        if RATE_LIMIT_RE.search(s):
            return "⚠️ API rate limit reached. Please try again later."
        if BILLING_RE.search(s):
            return "⚠️ API provider returned a billing error — your API key has run out of credits or has an insufficient balance."
        if AUTH_RE.search(s):
            return "⚠️ Authentication failed with the upstream provider. Check the configured API key or auth header."
        if TIMEOUT_RE.search(s):
            return "⚠️ Network or provider timeout while contacting the model. Please try again."
        if lowered.startswith("{") and "error" in lowered:
            return "⚠️ Upstream model/provider returned an error payload."

    return s


def _strip_reply_tags(text: str, current_message_id: Optional[str]) -> Tuple[str, Optional[str], bool, bool]:
    if not text or "[[" not in text:
        return text, None, False, False

    explicit_ids = [m.group(1).strip() for m in REPLY_TO_ID_RE.finditer(text)]
    saw_current = bool(REPLY_TO_CURRENT_RE.search(text))
    spans = [m.span() for m in REPLY_TO_ID_RE.finditer(text)] + [m.span() for m in REPLY_TO_CURRENT_RE.finditer(text)]
    if not spans:
        return text, None, False, False

    spans.sort()
    pieces: List[str] = []
    last = 0
    for start, end in spans:
        pieces.append(text[last:start])
        left = text[start - 1] if start > 0 else ""
        right = text[end] if end < len(text) else ""
        if left and right and left.isalnum() and right.isalnum():
            pieces.append(" ")
        last = end
    pieces.append(text[last:])

    cleaned = "".join(pieces)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()

    reply_to_id = explicit_ids[0] if explicit_ids else (current_message_id if saw_current else None)
    return cleaned, reply_to_id, saw_current, True


def _friendly_tool_policy_block(blocks: List[P1BlockedTool]) -> str:
    lines = ["## ⚠️ OpenClaw P1 工具策略拦截", ""]
    for block in blocks[:5]:
        suffix = f"（{block.instruction_type or 'UNKNOWN'} / {block.category or 'UNKNOWN'}）"
        lines.append(f"- `{block.tool_name}` {suffix}：{block.reason}")
    if len(blocks) > 5:
        lines.append(f"- 其余 {len(blocks) - 5} 个工具调用也被策略拒绝。")
    return "\n".join(lines)


def _friendly_loop_block(tool_name: str, decision: LoopDecision) -> str:
    lines = [
        "## ⚠️ OpenClaw P9 工具循环拦截",
        "",
        f"- 工具：`{tool_name}`",
        f"- 检测器：`{decision.detector}`",
        f"- 次数：{decision.count}",
        f"- 原因：{decision.message}",
    ]
    if decision.paired_tool_name:
        lines.append(f"- 配对工具：`{decision.paired_tool_name}`")
    return "\n".join(lines)


class OpenClawPolicy(Policy):
    """
    OpenClaw-aligned single-file policy for ArbiterOS.

    Implemented scope:
    - P1: tool allow/deny
    - P3: user-facing text sanitization
    - P7: strip [[reply_to:*]] directives
    - P9: tool loop detection (critical block only)
    - P10: tool call input repair / sessions_spawn attachment redaction
    """

    def check(
        self,
        instructions: List[Dict[str, Any]],
        current_response: Dict[str, Any],
        latest_instructions: List[Dict[str, Any]],
        trace_id: str,
        **kwargs: Any,
    ) -> PolicyCheckResult:
        response = dict(current_response)
        tool_calls = list(RUNTIME.extract_tool_calls(response) or [])
        changed = False
        notices: List[str] = []

        latest_idx = _latest_tool_instr_index(latest_instructions)

        # P10
        if tool_calls:
            repaired, p10_changed, p10_reasons = _repair_tool_calls(tool_calls)
            tool_calls = repaired
            if p10_changed:
                changed = True
            for reason in p10_reasons:
                try:
                    RUNTIME.audit(
                        phase="policy.openclaw.p10",
                        trace_id=trace_id,
                        tool="@tool",
                        decision="drop",
                        reason=reason,
                        args={},
                    )
                except Exception:
                    pass

        # P1
        blocked_by_p1: List[P1BlockedTool] = []
        if tool_calls:
            kept_after_p1: List[Dict[str, Any]] = []
            for tc in tool_calls:
                tool_name, tool_call_id, raw_args, _ = RUNTIME.parse_tool_call(tc)
                trimmed_name = _safe_str(tool_name)
                ins = latest_idx.get(_safe_str(tool_call_id))
                instruction_type, category = _extract_tool_instruction_meta(ins, trimmed_name)
                allowed, reason = _tool_allowed(trimmed_name, instruction_type, category, RUNTIME.cfg)
                if allowed:
                    kept_after_p1.append(tc)
                    continue
                blocked = P1BlockedTool(
                    tool_name=trimmed_name or "<unknown>",
                    instruction_type=instruction_type,
                    category=category,
                    reason=reason,
                )
                blocked_by_p1.append(blocked)
                try:
                    RUNTIME.audit(
                        phase="policy.openclaw.p1",
                        trace_id=trace_id,
                        tool=trimmed_name,
                        decision="block",
                        reason=reason,
                        args=raw_args if isinstance(raw_args, dict) else {},
                        extra={
                            "instruction_type": instruction_type,
                            "instruction_category": category,
                        },
                    )
                except Exception:
                    pass

            if len(kept_after_p1) != len(tool_calls):
                changed = True
                tool_calls = kept_after_p1
                if not tool_calls:
                    notices.append(_friendly_tool_policy_block(blocked_by_p1))

        # P9
        if tool_calls:
            print("P9 entered, tool_calls =", len(tool_calls))
            print("instructions =", len(instructions))
            history = _build_history(instructions, history_size=30)
            print("history events =", len(history))
            print("history tail =", history[-3:] if history else [])
            kept_after_p9: List[Dict[str, Any]] = []
            for tc in tool_calls:
                tool_name, _, raw_args, _ = RUNTIME.parse_tool_call(tc)
                args_dict = canonicalize_args(raw_args if isinstance(raw_args, dict) else {})
                decision = _detect_tool_loop(history, _safe_str(tool_name), args_dict)
                if decision is None or decision.level != "critical":
                    kept_after_p9.append(tc)
                    continue

                changed = True
                notices.append(_friendly_loop_block(_safe_str(tool_name), decision))
                try:
                    RUNTIME.audit(
                        phase="policy.openclaw.p9",
                        trace_id=trace_id,
                        tool=_safe_str(tool_name),
                        decision="block",
                        reason=decision.message,
                        args=args_dict,
                        extra={
                            "detector": decision.detector,
                            "count": decision.count,
                            "paired_tool_name": decision.paired_tool_name,
                        },
                    )
                except Exception:
                    pass

            tool_calls = kept_after_p9

        if changed:
            response["tool_calls"] = tool_calls if tool_calls else None
            if not tool_calls:
                response["function_call"] = None

        # P7
        current_message_id = _safe_str(kwargs.get("current_message_id"))
        content = response.get("content")
        if isinstance(content, str) and content:
            cleaned, reply_to_id, reply_to_current, has_tag = _strip_reply_tags(content, current_message_id or None)
            if has_tag:
                response["content"] = cleaned
                response["reply_to_id"] = reply_to_id
                response["reply_to_current"] = reply_to_current
                response["reply_to_tag"] = True
                changed = True

        # P3
        content = response.get("content")
        if isinstance(content, str):
            sanitized = _sanitize_user_facing_text(content, error_context=bool(kwargs.get("error_context")))
            if sanitized != content:
                response["content"] = sanitized
                changed = True

        if notices and not _safe_str(response.get("content")):
            response["content"] = "\n\n".join(notices[:3])
            changed = True

        if changed:
            return PolicyCheckResult(
                modified=True,
                response=response,
                error_type="\n\n".join(notices) if notices else None,
                inactivate_error_type=None,
            )

        return PolicyCheckResult(
            modified=False,
            response=current_response,
            error_type=None,
            inactivate_error_type=None,
        )
