from __future__ import annotations

import os
import re
import shlex
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from arbiteros_kernel.instruction_parsing.tool_parsers import parse_tool_instruction
from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME, canonicalize_args

from .policy import Policy

if TYPE_CHECKING:
    from arbiteros_kernel.instruction_parsing.types import TaintStatus


# Fallback splitter: coarse shell split, same style as current kernel exec parser.
_SHELL_OP_RE = re.compile(r"\|\||&&|[|;&\n]")

_DEFAULT_INTERPRETERS = {
    "python",
    "python2",
    "python3",
    "python3.10",
    "python3.11",
    "python3.12",
    "python3.13",
    "py",
    "bash",
    "sh",
    "zsh",
    "dash",
    "ksh",
    "fish",
    "node",
    "nodejs",
    "deno",
    "bun",
    "ruby",
    "perl",
    "php",
    "lua",
    "R",
    "Rscript",
    "java",
    "julia",
}

_DEFAULT_SCRIPT_SUFFIXES = {
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".pl",
    ".rb",
    ".php",
    ".lua",
    ".R",
}

_DEFAULT_COMPOSITE_OPERATORS = {"|", "||", "&&", ";", "&", "\n"}


def _extract_command(args_dict: Dict[str, Any]) -> str:
    for key in ("command", "cmd"):
        value = args_dict.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_exec_parse(
    args_dict: Dict[str, Any],
    current_taint_status: Optional["TaintStatus"],
) -> dict[str, Any]:
    """
    Try to reuse kernel-side parse metadata from:
      security_type.custom["exec_parse"]

    If kernel has not been patched to expose it, return {} and let policy fallback.
    """
    try:
        parsed = parse_tool_instruction(
            "exec",
            args_dict,
            taint_status=current_taint_status,
        )
        security_type = parsed.security_type if hasattr(parsed, "security_type") else {}
        if not isinstance(security_type, dict):
            return {}
        custom = security_type.get("custom")
        if not isinstance(custom, dict):
            return {}
        exec_parse = custom.get("exec_parse")
        return exec_parse if isinstance(exec_parse, dict) else {}
    except Exception:
        return {}


def _normalize_seg_types(seg_types: Any) -> List[str]:
    if not isinstance(seg_types, list):
        return []
    out: List[str] = []
    for x in seg_types:
        if isinstance(x, str) and x.strip():
            out.append(x.strip().upper())
    return out


def _normalize_segments(segments: Any) -> List[str]:
    if not isinstance(segments, list):
        return []
    out: List[str] = []
    for x in segments:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _normalize_operators(operators: Any) -> List[str]:
    if not isinstance(operators, list):
        return []
    out: List[str] = []
    for x in operators:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _split_shell_fallback(command: str) -> Tuple[List[str], List[str]]:
    segments = [seg.strip() for seg in _SHELL_OP_RE.split(command) if seg.strip()]
    operators = [m.group(0) for m in _SHELL_OP_RE.finditer(command)]
    return segments, operators


def _build_exec_parse_fallback(command: str) -> Dict[str, Any]:
    """
    Coarse fallback parse used when kernel does not expose exec_parse metadata.
    """
    segments, operators = _split_shell_fallback(command)
    return {
        "command": command,
        "segments": segments,
        "operators": operators,
        "segment_instruction_types": [],
        "path_tokens": [],
        "write_targets": [],
        "parser_kind": "policy_fallback_split",
    }


def _tokenize_shell(command: str) -> Tuple[List[str], Optional[str]]:
    try:
        return shlex.split(command), None
    except ValueError as e:
        return [], str(e)


def _looks_like_script_path(token: str, script_suffixes: set[str]) -> bool:
    t = token.strip()
    if not t:
        return False

    lower = t.lower()

    if lower.startswith("./") or lower.startswith("../") or lower.startswith("~/"):
        return True

    return any(lower.endswith(suf) for suf in script_suffixes)


def _is_interpreter_invocation(
    command: str,
    interpreters: set[str],
    script_suffixes: set[str],
) -> Tuple[bool, List[str]]:
    tokens, parse_error = _tokenize_shell(command)
    reasons: List[str] = []

    if parse_error is not None:
        # Parse failure itself is already a reason to review.
        reasons.append(f"shell_parse_error:{parse_error}")
        return True, reasons

    if not tokens:
        return False, reasons

    first = os.path.basename(tokens[0]).strip().lower()

    if first in interpreters:
        reasons.append(f"interpreter_exec:{first}")
        if len(tokens) >= 2:
            second = tokens[1].strip()
            if second == "-c":
                reasons.append("inline_code_execution")
            elif _looks_like_script_path(second, script_suffixes):
                reasons.append(f"script_argument:{second}")
        return True, reasons

    # Direct script execution: ./run.py, ./deploy.sh, ~/x/test.py
    if _looks_like_script_path(tokens[0], script_suffixes):
        reasons.append(f"direct_script_execution:{tokens[0]}")
        return True, reasons

    return False, reasons


def _build_review_message(
    *,
    command: str,
    review_reasons: List[str],
    segments: List[str],
    seg_types: List[str],
    operators: List[str],
    parser_kind: str,
) -> str:
    lines: List[str] = []
    lines.append("检测到未知或复杂的 exec 操作，已暂停自动执行。")
    lines.append("")
    lines.append(f"原始命令: {command}")

    if review_reasons:
        lines.append("触发原因:")
        for reason in review_reasons:
            lines.append(f"  - {reason}")

    if segments:
        lines.append("Kernel/Policy 粗解析得到的子段如下：")
        for i, seg in enumerate(segments, 1):
            seg_type = seg_types[i - 1] if i - 1 < len(seg_types) else "UNKNOWN"
            lines.append(f"  {i}. [{seg_type}] {seg}")

    if operators:
        lines.append(f"检测到的连接符: {', '.join(operators)}")

    lines.append(f"解析来源: {parser_kind}")
    lines.append("")
    lines.append("当前版本会将此类未知/复杂操作交由用户确认；若选择保护，则不会执行该命令。")
    return "\n".join(lines)


class ExecCompositePolicy(Policy):
    """
    Composite/unknown exec review policy.

    Merged behavior:
    - interpreter / script execution -> treat as unknown operation -> require review
    - composite shell command -> treat as complex operation -> require review

    This is NOT taint analysis.
    It is a confirmation-oriented exec policy that plugs into the existing
    Yes/No policy protection flow in the kernel callback layer.

    Compatibility:
    - class name remains ExecCompositePolicy
    - existing policies do not need to change
    """

    def check(
        self,
        instructions: List[Dict[str, Any]],
        current_response: Dict[str, Any],
        latest_instructions: List[Dict[str, Any]],
        trace_id: str,
        *,
        current_taint_status: Optional["TaintStatus"] = None,
        **kwargs: Any,
    ) -> PolicyCheckResult:
        cfg = (RUNTIME.cfg.get("exec_composite_policy") or {})
        if not isinstance(cfg, dict) or not bool(cfg.get("enabled", False)):
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        allow_multi_read_only = bool(cfg.get("allow_multi_read_only", True))
        review_interpreter_exec = bool(cfg.get("review_interpreter_exec", True))
        review_composite_exec = bool(cfg.get("review_composite_exec", True))

        interpreters_cfg = cfg.get("interpreter_commands")
        interpreters = {
            str(x).strip().lower()
            for x in (interpreters_cfg if isinstance(interpreters_cfg, list) else _DEFAULT_INTERPRETERS)
            if str(x).strip()
        }
        if not interpreters:
            interpreters = set(_DEFAULT_INTERPRETERS)

        suffixes_cfg = cfg.get("script_suffixes")
        script_suffixes = {
            str(x).strip().lower()
            for x in (suffixes_cfg if isinstance(suffixes_cfg, list) else _DEFAULT_SCRIPT_SUFFIXES)
            if str(x).strip()
        }
        if not script_suffixes:
            script_suffixes = set(_DEFAULT_SCRIPT_SUFFIXES)

        operators_cfg = cfg.get("composite_operators")
        composite_operators = {
            str(x)
            for x in (operators_cfg if isinstance(operators_cfg, list) else _DEFAULT_COMPOSITE_OPERATORS)
        }
        if not composite_operators:
            composite_operators = set(_DEFAULT_COMPOSITE_OPERATORS)

        response = dict(current_response)
        tool_calls = RUNTIME.extract_tool_calls(response)

        kept: List[Dict[str, Any]] = []
        review_msgs: List[str] = []
        errors: List[str] = []

        for tc in tool_calls:
            tool_name, _tool_call_id, raw_args, was_json_str = RUNTIME.parse_tool_call(tc)
            args_dict = raw_args if isinstance(raw_args, dict) else {}
            args_dict = canonicalize_args(args_dict)

            if tool_name != "exec":
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                continue

            command = _extract_command(args_dict)
            if not command:
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                continue

            # 1) Prefer kernel-exposed parse metadata if available
            exec_parse = _extract_exec_parse(args_dict, current_taint_status)
            if not exec_parse:
                exec_parse = _build_exec_parse_fallback(command)

            parser_kind = str(exec_parse.get("parser_kind") or "unknown")
            segments = _normalize_segments(exec_parse.get("segments"))
            seg_types = _normalize_seg_types(exec_parse.get("segment_instruction_types"))
            operators = _normalize_operators(exec_parse.get("operators"))

            # If fallback parser gives nothing useful, do one more local fallback.
            if not segments:
                segments, fallback_ops = _split_shell_fallback(command)
                if not operators:
                    operators = fallback_ops
                if not parser_kind or parser_kind == "unknown":
                    parser_kind = "policy_fallback_split"

            # 2) Unknown operation: interpreter / script execution
            should_review = False
            review_reasons: List[str] = []

            is_interpreter_exec, interpreter_reasons = _is_interpreter_invocation(
                command,
                interpreters,
                script_suffixes,
            )
            if is_interpreter_exec and review_interpreter_exec:
                should_review = True
                review_reasons.extend(interpreter_reasons)

            # 3) Complex operation: composite shell command
            normalized_ops = [op for op in operators if op in composite_operators]
            is_composite = len(segments) > 1 or bool(normalized_ops)

            if is_composite and review_composite_exec:
                all_read = bool(seg_types) and all(t == "READ" for t in seg_types)
                any_write = any(t == "WRITE" for t in seg_types)
                any_exec = any(t == "EXEC" for t in seg_types)

                if all_read and allow_multi_read_only:
                    pass
                else:
                    should_review = True
                    if normalized_ops:
                        review_reasons.append(f"operators:{','.join(normalized_ops)}")
                    if len(segments) > 1:
                        review_reasons.append(f"segment_count:{len(segments)}")

                    if any_write:
                        review_reasons.append("contains_WRITE_segment")
                    elif any_exec:
                        review_reasons.append("contains_EXEC_segment")
                    elif all_read and not allow_multi_read_only:
                        review_reasons.append("read_only_composite_requires_review")
                    elif not seg_types:
                        review_reasons.append("multi_segment_command")
                    else:
                        review_reasons.append("multi_segment_not_read_only")

            if not should_review:
                kept.append(RUNTIME.write_back_tool_args(tc, args_dict, was_json_str))
                continue

            msg = _build_review_message(
                command=command,
                review_reasons=review_reasons,
                segments=segments,
                seg_types=seg_types,
                operators=operators,
                parser_kind=parser_kind,
            )
            review_msgs.append(msg)

            err = (
                f"REQUIRE_APPROVAL tool=exec reason={';'.join(review_reasons)} "
                f"(command={command})"
            )
            errors.append(err)

            RUNTIME.audit(
                phase="policy.exec_composite",
                trace_id=trace_id,
                tool="exec",
                decision="REQUIRE_APPROVAL",
                reason=";".join(review_reasons),
                args={"command": command},
                extra={
                    "segments": segments,
                    "segment_instruction_types": seg_types,
                    "operators": operators,
                    "parser_kind": parser_kind,
                },
            )

        if errors:
            response["tool_calls"] = kept if kept else None
            if not kept:
                response["function_call"] = None

            notice = "\n\n".join(review_msgs[:3])
            if isinstance(response.get("content"), str) and response.get("content", "").strip():
                response["content"] = response["content"].rstrip() + "\n\n" + notice
            else:
                response["content"] = notice

            return PolicyCheckResult(
                modified=True,
                response=response,
                error_type="\n".join(errors),
            )

        return PolicyCheckResult(modified=False, response=current_response, error_type=None)