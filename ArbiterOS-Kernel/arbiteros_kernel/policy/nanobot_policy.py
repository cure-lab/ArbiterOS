from __future__ import annotations

import copy
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from arbiteros_kernel.policy_check import PolicyCheckResult

from .policy import Policy

__all__ = [
    "NanobotPolicy",
    "NanobotResponsePolicy",
]


# ============================================================================
# Nanobot response-layer rules modeled here
# ----------------------------------------------------------------------------
# 1) exec dangerous command guard:
#    - same 9 deny_patterns from nanobot/agent/tools/shell.py::_guard_command
#
# 2) URL / SSRF safety:
#    - exec: scan command string for all http/https URLs
#    - web_fetch: validate input url
#    - optional: if arguments already carry resolved/final_url, validate that too
#
# 3) repeated external lookup throttle:
#    - web_fetch:url.lower().strip()
#    - web_search:(query or search_term).lower().strip()
#    - allow first 2 attempts, block 3rd+
#
# Important boundary:
# - This policy only sees the assistant response before tool execution.
# - So redirect-time final URL checks can only be enforced here if the call args
#   already contain something like final_url/resolved_url. Otherwise Nanobot's
#   own tool-side guard still remains the source of truth after the request runs.
# ============================================================================


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

_MAX_REPEAT_EXTERNAL_LOOKUPS = 2

_REPEAT_LOOKUP_ERROR = (
    "Error: repeated external lookup blocked. "
    "Use the results you already have to answer, or try a meaningfully different source."
)

_EXEC_DANGEROUS_PATTERN_ERROR = (
    "Error: Command blocked by safety guard (dangerous pattern detected)"
)

_EXEC_INTERNAL_URL_ERROR = (
    "Error: Command blocked by safety guard (internal/private URL detected)"
)

# Same intent as nanobot/security/network.py::_URL_RE
_URL_RE = re.compile(r"https?://[^\s\"'`;|<>]+", re.IGNORECASE)

# Same core deny patterns as nanobot/agent/tools/shell.py
_EXEC_DENY_PATTERNS: Tuple[str, ...] = (
    r"\brm\s+-[rf]{1,2}\b",             # rm -r, rm -rf, rm -fr
    r"\bdel\s+/[fq]\b",                 # del /f, del /q
    r"\brmdir\s+/s\b",                  # rmdir /s
    r"(?:^|[;&|]\s*)format\b",          # format as standalone command
    r"\b(mkfs|diskpart)\b",             # disk ops
    r"\bdd\s+if=",                      # dd
    r">\s*/dev/sd",                     # write to disk
    r"\b(shutdown|reboot|poweroff)\b",  # power ops
    r":\(\)\s*\{.*\};\s*:",             # fork bomb
)

# Same blocked ranges as nanobot/security/network.py
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)

# If you want to mirror Nanobot's tools.ssrf_whitelist behavior,
# replace this tuple during integration or wire it to config loading.
_SSRF_WHITELIST: Tuple[str, ...] = ()

_ALLOWED_NETWORKS = tuple(
    net
    for cidr in _SSRF_WHITELIST
    for net in (
        (
            ipaddress.ip_network(cidr, strict=False),
        )
        if isinstance(cidr, str) and cidr.strip()
        else ()
    )
)


# ----------------------------------------------------------------------------
# Data types
# ----------------------------------------------------------------------------

@dataclass
class BlockDecision:
    rule_id: str
    tool_name: str
    reason: str
    detail: str = ""


# ----------------------------------------------------------------------------
# Generic helpers
# ----------------------------------------------------------------------------

def _safe_str(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default


def _safe_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _json_dumps_pretty(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        return repr(value)


def _parse_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _looks_like_tool_call(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False

    fn = obj.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        return True

    if isinstance(obj.get("tool_name"), str):
        return True

    if isinstance(obj.get("name"), str) and (
        "arguments" in obj or "args" in obj or "tool_call_id" in obj
    ):
        return True

    return False


def _parse_tool_call(tool_call: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    tool_call_id = _safe_str(tool_call.get("id") or tool_call.get("tool_call_id"))

    function_block = tool_call.get("function")
    if isinstance(function_block, dict):
        tool_name = _safe_str(function_block.get("name") or tool_call.get("name"))
        args = _parse_arguments(function_block.get("arguments"))
        return tool_name, tool_call_id, args

    tool_name = _safe_str(tool_call.get("tool_name") or tool_call.get("name"))
    args = _parse_arguments(tool_call.get("arguments") or tool_call.get("args"))
    return tool_name, tool_call_id, args


def _get_response_message_obj(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Support both:
    1) stripped assistant message dict: {"content": ..., "tool_calls": ...}
    2) raw provider response: {"choices": [{"message": {...}}]}
    """
    if not isinstance(response, dict):
        return {}

    if (
        "tool_calls" in response
        or "content" in response
        or "function_call" in response
    ):
        return response

    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict):
                return msg

    return response


def _extract_response_tool_calls(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    msg = _get_response_message_obj(response)
    tool_calls = msg.get("tool_calls")
    return [tc for tc in _safe_list(tool_calls) if isinstance(tc, dict)]


def _clear_response_tool_calls(response: Dict[str, Any]) -> None:
    msg = _get_response_message_obj(response)
    msg["tool_calls"] = None
    msg["function_call"] = None


def _set_response_content(response: Dict[str, Any], content: str) -> None:
    msg = _get_response_message_obj(response)
    msg["content"] = content


def _iter_tool_calls_from_instruction_obj(obj: Any) -> Iterable[Dict[str, Any]]:
    """
    Best-effort extractor for historical tool calls from instructions.
    We intentionally only traverse likely wrapper keys to avoid false positives.
    """
    seen: set[int] = set()

    def walk(node: Any) -> Iterable[Dict[str, Any]]:
        node_id = id(node)
        if node_id in seen:
            return
        seen.add(node_id)

        if isinstance(node, dict):
            if _looks_like_tool_call(node):
                yield node

            for key in ("tool_calls", "content", "message", "response", "current_response"):
                child = node.get(key)
                if child is not None:
                    yield from walk(child)

        elif isinstance(node, list):
            for item in node:
                yield from walk(item)

    yield from walk(obj)


# ----------------------------------------------------------------------------
# URL / SSRF checks
# ----------------------------------------------------------------------------

def _is_whitelisted(addr: ipaddress._BaseAddress) -> bool:
    return any(addr in net for net in _ALLOWED_NETWORKS)


def _is_private_like(addr: ipaddress._BaseAddress) -> bool:
    if _is_whitelisted(addr):
        return False
    return any(addr in net for net in _BLOCKED_NETWORKS)


def validate_url_target(url: str) -> Tuple[bool, str]:
    """
    Mirrors nanobot/security/network.py::validate_url_target semantics.
    """
    try:
        parsed = urlparse(url)
    except Exception as exc:
        return False, str(exc)

    if parsed.scheme not in ("http", "https"):
        return False, f"Only http/https allowed, got '{parsed.scheme or 'none'}'"

    if not parsed.netloc:
        return False, "Missing domain"

    hostname = parsed.hostname
    if not hostname:
        return False, "Missing hostname"

    try:
        infos = socket.getaddrinfo(
            hostname,
            None,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )
    except socket.gaierror:
        return False, f"Cannot resolve hostname: {hostname}"

    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue

        if _is_private_like(addr):
            return False, (
                f"Blocked: {hostname} resolves to private/internal address {addr}"
            )

    return True, ""


def validate_resolved_url(url: str) -> Tuple[bool, str]:
    """
    Mirrors nanobot/security/network.py::validate_resolved_url semantics.
    Useful if arguments already contain final_url/resolved_url.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return True, ""

    hostname = parsed.hostname
    if not hostname:
        return True, ""

    try:
        addr = ipaddress.ip_address(hostname)
        if _is_private_like(addr):
            return False, f"Redirect target is a private address: {addr}"
        return True, ""
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(
            hostname,
            None,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )
    except socket.gaierror:
        # Nanobot's own helper treats this as non-blocking for resolved_url check.
        return True, ""

    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if _is_private_like(addr):
            return False, f"Redirect target {hostname} resolves to private address {addr}"

    return True, ""


def _find_unsafe_urls_in_text(text: str) -> List[Tuple[str, str]]:
    hits: List[Tuple[str, str]] = []
    for match in _URL_RE.finditer(text or ""):
        url = match.group(0)
        ok, err = validate_url_target(url)
        if not ok:
            hits.append((url, err))
    return hits


# ----------------------------------------------------------------------------
# Repeated external lookup checks
# ----------------------------------------------------------------------------

def external_lookup_signature(tool_name: str, arguments: Dict[str, Any]) -> Optional[str]:
    """
    Mirrors nanobot/utils/runtime.py::external_lookup_signature
    """
    name = _safe_str(tool_name).lower()

    if name == "web_fetch":
        url = _safe_str(arguments.get("url"))
        if url:
            return f"web_fetch:{url.lower()}"

    if name == "web_search":
        query = _safe_str(arguments.get("query") or arguments.get("search_term"))
        if query:
            return f"web_search:{query.lower()}"

    return None


def _build_historical_lookup_counts(instructions: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}

    for ins in instructions or []:
        for tc in _iter_tool_calls_from_instruction_obj(ins):
            tool_name, _tool_call_id, args = _parse_tool_call(tc)
            sig = external_lookup_signature(tool_name, args)
            if sig is None:
                continue
            counts[sig] = counts.get(sig, 0) + 1

    return counts


# ----------------------------------------------------------------------------
# Rule evaluation
# ----------------------------------------------------------------------------

def _check_exec_tool(arguments: Dict[str, Any]) -> Optional[BlockDecision]:
    command = _safe_str(arguments.get("command"))
    if not command:
        return None

    lower = command.lower()

    for pattern in _EXEC_DENY_PATTERNS:
        if re.search(pattern, lower):
            return BlockDecision(
                rule_id="NB-RESP-EXEC-001",
                tool_name="exec",
                reason=_EXEC_DANGEROUS_PATTERN_ERROR,
                detail=f"Matched deny pattern: {pattern}",
            )

    unsafe_urls = _find_unsafe_urls_in_text(command)
    if unsafe_urls:
        url, err = unsafe_urls[0]
        return BlockDecision(
            rule_id="NB-RESP-URL-001",
            tool_name="exec",
            reason=_EXEC_INTERNAL_URL_ERROR,
            detail=f"Unsafe URL in command: {url}\nReason: {err}",
        )

    return None


def _check_web_fetch_tool(
    arguments: Dict[str, Any],
    seen_counts: Dict[str, int],
) -> Optional[BlockDecision]:
    url = _safe_str(arguments.get("url"))
    if url:
        ok, err = validate_url_target(url)
        if not ok:
            return BlockDecision(
                rule_id="NB-RESP-URL-002",
                tool_name="web_fetch",
                reason=f"Error: URL validation failed: {err}",
                detail=f"Input URL: {url}",
            )

    # Best-effort support if final/resolved URL is already present in args
    final_url = _safe_str(
        arguments.get("final_url")
        or arguments.get("finalUrl")
        or arguments.get("resolved_url")
        or arguments.get("resolvedUrl")
    )
    if final_url:
        ok, err = validate_resolved_url(final_url)
        if not ok:
            return BlockDecision(
                rule_id="NB-RESP-URL-003",
                tool_name="web_fetch",
                reason=f"Error: Redirect blocked: {err}",
                detail=f"Resolved URL: {final_url}",
            )

    sig = external_lookup_signature("web_fetch", arguments)
    if sig is not None:
        count = seen_counts.get(sig, 0) + 1
        seen_counts[sig] = count
        if count > _MAX_REPEAT_EXTERNAL_LOOKUPS:
            return BlockDecision(
                rule_id="NB-RESP-LOOKUP-001",
                tool_name="web_fetch",
                reason=_REPEAT_LOOKUP_ERROR,
                detail=f"Signature={sig}, attempt={count}",
            )

    return None


def _check_web_search_tool(
    arguments: Dict[str, Any],
    seen_counts: Dict[str, int],
) -> Optional[BlockDecision]:
    sig = external_lookup_signature("web_search", arguments)
    if sig is not None:
        count = seen_counts.get(sig, 0) + 1
        seen_counts[sig] = count
        if count > _MAX_REPEAT_EXTERNAL_LOOKUPS:
            return BlockDecision(
                rule_id="NB-RESP-LOOKUP-001",
                tool_name="web_search",
                reason=_REPEAT_LOOKUP_ERROR,
                detail=f"Signature={sig}, attempt={count}",
            )

    return None


def _evaluate_tool_call(
    tool_name: str,
    arguments: Dict[str, Any],
    seen_counts: Dict[str, int],
) -> Optional[BlockDecision]:
    name = _safe_str(tool_name).lower()

    if name == "exec":
        return _check_exec_tool(arguments)

    if name == "web_fetch":
        return _check_web_fetch_tool(arguments, seen_counts)

    if name == "web_search":
        return _check_web_search_tool(arguments, seen_counts)

    return None


# ----------------------------------------------------------------------------
# User-facing block message
# ----------------------------------------------------------------------------

def _render_block_message(
    tool_name: str,
    arguments: Dict[str, Any],
    decision: BlockDecision,
) -> str:
    return "\n".join(
        [
            "## ⚠️ 安全策略拦截确认",
            "",
            "### 1. 触发工具",
            f"- 工具：`{tool_name}`",
            "```json",
            _json_dumps_pretty(arguments),
            "```",
            "",
            "### 2. 拦截说明",
            decision.reason,
            "",
            "### 3. 命中规则",
            f"- 规则编号：`{decision.rule_id}`",
            f"- 详细原因：{decision.detail or '无'}",
        ]
    )


# ----------------------------------------------------------------------------
# Policy
# ----------------------------------------------------------------------------

class NanobotPolicy(Policy):
    """
    Response-layer policy modeled after Nanobot's own runtime/tool guards.

    Behavior:
    - inspect assistant response tool_calls in order
    - first blocked tool call => block the entire batch immediately
    - content-only responses are left unchanged
    """

    def check(
        self,
        instructions: List[Dict[str, Any]],
        current_response: Dict[str, Any],
        latest_instructions: List[Dict[str, Any]],
        trace_id: str,
        **kwargs: Any,
    ) -> PolicyCheckResult:
        del latest_instructions, trace_id, kwargs  # not needed in this policy

        tool_calls = _extract_response_tool_calls(current_response)
        if not tool_calls:
            return PolicyCheckResult(
                modified=False,
                response=current_response,
                error_type=None,
                inactivate_error_type=None,
            )

        seen_counts = _build_historical_lookup_counts(instructions)
        response = copy.deepcopy(current_response)

        for tc in tool_calls:
            tool_name, _tool_call_id, arguments = _parse_tool_call(tc)
            if not tool_name:
                continue

            decision = _evaluate_tool_call(tool_name, arguments, seen_counts)
            if decision is None:
                continue

            block_message = _render_block_message(tool_name, arguments, decision)

            _clear_response_tool_calls(response)
            _set_response_content(response, block_message)

            return PolicyCheckResult(
                modified=True,
                response=response,
                error_type=block_message,
                inactivate_error_type=None,
            )

        return PolicyCheckResult(
            modified=False,
            response=current_response,
            error_type=None,
            inactivate_error_type=None,
        )


NanobotResponsePolicy = NanobotPolicy