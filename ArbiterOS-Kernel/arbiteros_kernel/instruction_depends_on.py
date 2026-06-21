"""Resolve model-declared depends_on refs to instruction-bound dependency metadata."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

REF_TYPE_RUNTIME_STEP = "runtime_step"
REF_TYPE_TOOL_CALL_ID = "tool_call_id"
SOURCE_MODEL = "model"
SOURCE_KERNEL = "kernel"

DEPENDS_ON_CATALOG_RULES = (
    "Global runtime_step integers (1, 2, 3, ...) label every instruction in order: "
    "each text reply, each tool_call, and each tool_result each consume one step. "
    "Set depends_on to the step integers listed in the catalog below that this step "
    "semantically depends on; use [] when none. Only reference steps strictly less "
    "than your new step number(s). "
    "If this reply includes tool_calls and text, steps are assigned in order: "
    "all tool_calls first (in listed order), then the text content step last."
)

DEFAULT_TEXT_PREVIEW_CHARS = 60
DEFAULT_TOOL_CALL_ID_PREFIX_CHARS = 12


def normalize_text_depends_on_raw(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        if isinstance(item, bool):
            continue
        if isinstance(item, int) and item > 0:
            out.append(item)
            continue
        if isinstance(item, float) and item.is_integer() and item > 0:
            out.append(int(item))
            continue
        if isinstance(item, str) and item.strip().isdigit():
            parsed = int(item.strip())
            if parsed > 0:
                out.append(parsed)
    return out


def normalize_tool_depends_on_raw(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if x is not None and str(x).strip()]
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [
                    str(x).strip()
                    for x in parsed
                    if x is not None and str(x).strip()
                ]
        except json.JSONDecodeError:
            pass
        return [stripped]
    return []


def _instruction_content(content: Any) -> dict[str, Any]:
    return content if isinstance(content, dict) else {}


def find_instruction_id_by_runtime_step(
    instructions: list[dict[str, Any]], runtime_step: int
) -> Optional[str]:
    if runtime_step <= 0:
        return None
    for instr in instructions:
        if not isinstance(instr, dict):
            continue
        step = instr.get("runtime_step")
        if isinstance(step, int) and step == runtime_step:
            instr_id = instr.get("id")
            if isinstance(instr_id, str) and instr_id.strip():
                return instr_id.strip()
    return None


def find_instruction_id_by_tool_call_id(
    instructions: list[dict[str, Any]],
    tool_call_id: str,
    *,
    prefer_with_result: bool = True,
) -> Optional[str]:
    tc_id = tool_call_id.strip()
    if not tc_id:
        return None
    matches: list[dict[str, Any]] = []
    for instr in instructions:
        if not isinstance(instr, dict):
            continue
        content = _instruction_content(instr.get("content"))
        if content.get("tool_call_id") != tc_id:
            continue
        matches.append(instr)
    if not matches:
        return None
    if prefer_with_result:
        for instr in reversed(matches):
            content = _instruction_content(instr.get("content"))
            if content.get("result") is not None:
                instr_id = instr.get("id")
                if isinstance(instr_id, str) and instr_id.strip():
                    return instr_id.strip()
    instr_id = matches[-1].get("id")
    return instr_id.strip() if isinstance(instr_id, str) and instr_id.strip() else None


def find_tool_call_instruction_id_without_result(
    instructions: list[dict[str, Any]], tool_call_id: str
) -> Optional[str]:
    tc_id = tool_call_id.strip()
    if not tc_id:
        return None
    for instr in instructions:
        if not isinstance(instr, dict):
            continue
        content = _instruction_content(instr.get("content"))
        if content.get("tool_call_id") != tc_id:
            continue
        if content.get("result") is not None:
            continue
        instr_id = instr.get("id")
        if isinstance(instr_id, str) and instr_id.strip():
            return instr_id.strip()
    return find_instruction_id_by_tool_call_id(
        instructions, tc_id, prefer_with_result=False
    )


def _dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        instr_id = entry.get("instruction_id")
        if not isinstance(instr_id, str) or not instr_id.strip():
            continue
        iid = instr_id.strip()
        if iid in seen:
            continue
        seen.add(iid)
        out.append(dict(entry))
    return out


def resolve_runtime_steps_to_depends_on(
    instructions: list[dict[str, Any]],
    raw_steps: Any,
    *,
    current_runtime_step: Optional[int] = None,
    trace_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    steps = normalize_text_depends_on_raw(raw_steps)
    if not steps:
        return []
    resolved: list[dict[str, Any]] = []
    for step in steps:
        if (
            isinstance(current_runtime_step, int)
            and current_runtime_step > 0
            and step >= current_runtime_step
        ):
            logger.debug(
                "depends_on skip self/future runtime_step=%s current=%s trace_id=%s",
                step,
                current_runtime_step,
                trace_id or "",
            )
            continue
        instruction_id = find_instruction_id_by_runtime_step(instructions, step)
        if instruction_id is None:
            logger.debug(
                "depends_on unresolved runtime_step=%s trace_id=%s",
                step,
                trace_id or "",
            )
            continue
        resolved.append(
            {
                "instruction_id": instruction_id,
                "ref": step,
                "ref_type": REF_TYPE_RUNTIME_STEP,
                "source": SOURCE_MODEL,
            }
        )
    return _dedupe_entries(resolved)


def resolve_tool_call_ids_to_depends_on(
    instructions: list[dict[str, Any]],
    raw_ids: Any,
    *,
    trace_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    refs = normalize_tool_depends_on_raw(raw_ids)
    if not refs:
        return []
    resolved: list[dict[str, Any]] = []
    for ref in refs:
        instruction_id = find_instruction_id_by_tool_call_id(instructions, ref)
        if instruction_id is None:
            logger.debug(
                "depends_on unresolved tool_call_id=%s trace_id=%s",
                ref,
                trace_id or "",
            )
            continue
        resolved.append(
            {
                "instruction_id": instruction_id,
                "ref": ref,
                "ref_type": REF_TYPE_TOOL_CALL_ID,
                "source": SOURCE_MODEL,
            }
        )
    return _dedupe_entries(resolved)


def kernel_depends_on_tool_call(
    instructions: list[dict[str, Any]], tool_call_id: str
) -> list[dict[str, Any]]:
    instruction_id = find_tool_call_instruction_id_without_result(
        instructions, tool_call_id
    )
    if instruction_id is None:
        return []
    return [
        {
            "instruction_id": instruction_id,
            "ref": tool_call_id.strip(),
            "ref_type": REF_TYPE_TOOL_CALL_ID,
            "source": SOURCE_KERNEL,
        }
    ]


def _preview_text_content(content: Any, *, max_chars: int) -> str:
    if not isinstance(content, str):
        return ""
    collapsed = " ".join(content.strip().split())
    if not collapsed:
        return ""
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 1] + "…"


def _format_step_catalog_line(
    instr: dict[str, Any],
    *,
    text_preview_chars: int,
    tool_call_id_prefix_chars: int,
) -> Optional[str]:
    step = instr.get("runtime_step")
    if not isinstance(step, int) or step <= 0:
        return None
    itype = str(instr.get("instruction_type") or "").strip() or "UNKNOWN"
    content = instr.get("content")
    if isinstance(content, str):
        preview = _preview_text_content(content, max_chars=text_preview_chars)
        label = "TEXT"
        detail = preview or itype
        return f"  {step} {label} [{itype}] | {detail}"
    if isinstance(content, dict):
        tool_name = content.get("tool_name")
        tool_name_str = (
            str(tool_name).strip() if isinstance(tool_name, str) and tool_name.strip() else "tool"
        )
        tc_id = content.get("tool_call_id")
        tc_prefix = ""
        if isinstance(tc_id, str) and tc_id.strip():
            tc_prefix = tc_id.strip()[:tool_call_id_prefix_chars]
        has_result = content.get("result") is not None
        label = "TOOL·result" if has_result else "TOOL"
        if tc_prefix:
            return f"  {step} {label} {tool_name_str} | {tc_prefix}"
        return f"  {step} {label} {tool_name_str}"
    return f"  {step} UNKNOWN [{itype}]"


def build_step_catalog_with_previews(
    instructions: list[dict[str, Any]],
    *,
    text_preview_chars: int = DEFAULT_TEXT_PREVIEW_CHARS,
    tool_call_id_prefix_chars: int = DEFAULT_TOOL_CALL_ID_PREFIX_CHARS,
) -> str:
    lines: list[str] = []
    for instr in instructions:
        if not isinstance(instr, dict):
            continue
        line = _format_step_catalog_line(
            instr,
            text_preview_chars=text_preview_chars,
            tool_call_id_prefix_chars=tool_call_id_prefix_chars,
        )
        if line:
            lines.append(line)
    if not lines:
        return "Step catalog: (no prior steps yet.)"
    return "Step catalog (copy step integers into depends_on):\n" + "\n".join(lines)


def build_depends_on_schema_description(
    instructions: list[dict[str, Any]],
    *,
    text_preview_chars: int = DEFAULT_TEXT_PREVIEW_CHARS,
    tool_call_id_prefix_chars: int = DEFAULT_TOOL_CALL_ID_PREFIX_CHARS,
) -> str:
    next_step = len(instructions) + 1
    catalog = build_step_catalog_with_previews(
        instructions,
        text_preview_chars=text_preview_chars,
        tool_call_id_prefix_chars=tool_call_id_prefix_chars,
    )
    return (
        f"{DEPENDS_ON_CATALOG_RULES}\n\n{catalog}\n\n"
        f"Next step number starts at: {next_step} "
        f"(each tool_call in this reply uses consecutive numbers before any text step)."
    )


def build_runtime_step_catalog(instructions: list[dict[str, Any]]) -> str:
    """Backward-compatible alias for preview catalog."""
    return build_step_catalog_with_previews(instructions)
