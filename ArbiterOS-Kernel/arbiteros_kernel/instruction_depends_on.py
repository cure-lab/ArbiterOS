"""Resolve model-declared depends_on refs to instruction-bound dependency metadata."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

REF_TYPE_RUNTIME_STEP = "runtime_step"
REF_TYPE_TOOL_CALL_ID = "tool_call_id"
REF_TYPE_INSTRUCTION_ID = "instruction_id"
SOURCE_MODEL = "model"
SOURCE_KERNEL = "kernel"

STEP_REF_PREFIX = "step:"

REF_KIND_SYSTEMPROMPT = "SYSTEMPROMPT"
REF_KIND_USERINPUT = "USERINPUT"
REF_KIND_TOOLCALL = "TOOLCALL"
REF_KIND_TOOLRESULT = "TOOLRESULT"
REF_KIND_LLMOUTPUT = "LLMOUTPUT"

REF_MARKER_PREFIX = "[ARBITEROS_REF"
_ARBITEROS_REF_LINE_RE = re.compile(
    r"^\[ARBITEROS_REF id=([^\s\]]+) kind=([A-Z_]+)\]\s*\n?",
)

DEPENDS_ON_CAUSAL_RULES = (
    "Identify DIRECT causal dependencies only: predecessors without which this step's "
    "decision or action would likely be meaningfully different. "
    "Causal test for each candidate: (1) Did that predecessor supply the specific "
    "evidence, failure signal, code context, or reasoning this step is explicitly "
    "reacting to, continuing from, or constrained by? "
    "(2) If it were absent, would this step probably change? "
    "(3) Prefer the nearest direct causal predecessors—omit distant background unless "
    "this step explicitly continues that earlier output. "
    "When this step fixes, interprets, or acts on the immediately preceding tool or "
    "text output, include that predecessor. "
    "Return ONLY the most direct causal predecessors; omit indirect, transitive, or "
    "merely thematic links. Use [] only when no prior step causally shaped this step."
)

DEPENDS_ON_COUNTERFACTUAL_RULES = (
    "Each depends_on entry must include confidence (0-1, how direct and necessary the causal "
    "link is) and counterfactual (one short sentence): if that predecessor had not occurred "
    "or produced its output, how would this step's decision or action likely change? Mention "
    "specific evidence from that predecessor; do not restate ids alone or use vague phrases "
    "like 'informs this step'."
)

DEPENDS_ON_REF_RULES = (
    f"{DEPENDS_ON_CAUSAL_RULES} "
    "Prior steps are labeled in the conversation with markers like "
    "[ARBITEROS_REF id=<instruction-uuid> kind=SYSTEMPROMPT|USERINPUT|TOOLCALL|TOOLRESULT|LLMOUTPUT]. "
    "Set depends_on to objects with instruction_id copied from those markers; use [] when none. "
    "Only reference instruction ids that appear earlier in the conversation. "
    f"{DEPENDS_ON_COUNTERFACTUAL_RULES}"
)

DEPENDS_ON_CATALOG_RULES = DEPENDS_ON_REF_RULES

DEPENDS_ON_STATIC_SCHEMA_HINT = (
    "Prior-step dependencies from [ARBITEROS_REF ...] markers; each entry needs "
    "instruction_id, confidence (0-1), and counterfactual. Use [] when none. "
    "Allowed ids are injected at request time."
)

KERNEL_TOOL_RESULT_CONFIDENCE = 1.0
KERNEL_TOOL_RESULT_COUNTERFACTUAL = (
    "Without the preceding tool call, this tool result would not exist."
)

DEFAULT_LEGACY_CONFIDENCE = 0.0
DEFAULT_LEGACY_COUNTERFACTUAL = ""


def format_arbiteros_ref_marker(instruction_id: str, kind: str) -> str:
    iid = instruction_id.strip()
    k = kind.strip()
    return f"[ARBITEROS_REF id={iid} kind={k}]\n"


def strip_arbiteros_ref_marker(text: str) -> str:
    if not isinstance(text, str):
        return text
    return _ARBITEROS_REF_LINE_RE.sub("", text, count=1)


def strip_arbiteros_ref_markers(text: str) -> str:
    """Remove all leading ``[ARBITEROS_REF ...]`` lines (API-only watermarks)."""
    if not isinstance(text, str):
        return text
    out = text
    while True:
        stripped = strip_arbiteros_ref_marker(out)
        if stripped == out:
            break
        out = stripped
    return out.lstrip("\n")


def _looks_like_instruction_id(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def instruction_ref_kind(instr: dict[str, Any]) -> Optional[str]:
    if not isinstance(instr, dict):
        return None
    explicit = instr.get("arbiteros_ref_kind")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    itype = str(instr.get("instruction_type") or "").strip()
    if itype == REF_KIND_SYSTEMPROMPT:
        return REF_KIND_SYSTEMPROMPT
    if itype == REF_KIND_USERINPUT:
        return REF_KIND_USERINPUT
    content = instr.get("content")
    if isinstance(content, dict):
        if content.get("result") is not None:
            return REF_KIND_TOOLRESULT
        if content.get("tool_call_id"):
            return REF_KIND_TOOLCALL
    if isinstance(content, str):
        return REF_KIND_LLMOUTPUT
    return None


def find_instruction_by_id(
    instructions: list[dict[str, Any]], instruction_id: str
) -> Optional[dict[str, Any]]:
    iid = instruction_id.strip()
    if not iid:
        return None
    for instr in instructions:
        if not isinstance(instr, dict):
            continue
        cand = instr.get("id")
        if isinstance(cand, str) and cand.strip() == iid:
            return instr
    return None


def build_allowed_depends_on_instruction_ids(
    instructions: list[dict[str, Any]],
    *,
    current_runtime_step: Optional[int] = None,
) -> list[str]:
    """Return instruction ids strictly before ``current_runtime_step`` (or all when unset)."""
    allowed: list[str] = []
    for instr in instructions:
        if not isinstance(instr, dict):
            continue
        instr_id = instr.get("id")
        if not isinstance(instr_id, str) or not instr_id.strip():
            continue
        step = instr.get("runtime_step")
        if (
            isinstance(current_runtime_step, int)
            and current_runtime_step > 0
            and isinstance(step, int)
            and step >= current_runtime_step
        ):
            continue
        allowed.append(instr_id.strip())
    return allowed


def clamp_confidence(value: Any) -> float:
    if isinstance(value, bool):
        return DEFAULT_LEGACY_CONFIDENCE
    if isinstance(value, (int, float)):
        n = float(value)
        if n < 0.0:
            return 0.0
        if n > 1.0:
            return 1.0
        return n
    return DEFAULT_LEGACY_CONFIDENCE


def normalize_counterfactual(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return DEFAULT_LEGACY_COUNTERFACTUAL


def build_depends_on_entry_schema(allowed_ids: list[str]) -> dict[str, Any]:
    instruction_id_schema: dict[str, Any] = {
        "type": "string",
        "description": "Prior instruction id from an [ARBITEROS_REF ...] marker.",
    }
    if allowed_ids:
        instruction_id_schema["enum"] = allowed_ids
    return {
        "type": "object",
        "properties": {
            "instruction_id": instruction_id_schema,
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "How direct and necessary this causal link is (0-1).",
            },
            "counterfactual": {
                "type": "string",
                "maxLength": 240,
                "description": (
                    "One short sentence: if that predecessor had not occurred or produced "
                    "its output, how would this step likely change?"
                ),
            },
        },
        "required": ["instruction_id", "confidence", "counterfactual"],
        "additionalProperties": False,
    }


def build_depends_on_items_schema(
    instructions: list[dict[str, Any]],
    *,
    current_runtime_step: Optional[int] = None,
) -> dict[str, Any]:
    allowed_ids = build_allowed_depends_on_instruction_ids(
        instructions, current_runtime_step=current_runtime_step
    )
    return build_depends_on_entry_schema(allowed_ids)


def build_depends_on_schema_description(
    instructions: list[dict[str, Any]],
    *,
    current_runtime_step: Optional[int] = None,
    text_preview_chars: int = 60,
    tool_call_id_prefix_chars: int = 12,
) -> str:
    del text_preview_chars, tool_call_id_prefix_chars
    allowed_ids = build_allowed_depends_on_instruction_ids(
        instructions, current_runtime_step=current_runtime_step
    )
    if not allowed_ids:
        return (
            f"{DEPENDS_ON_REF_RULES} "
            "No prior instruction ids are available yet; use []."
        )
    ids_preview = ", ".join(allowed_ids[:8])
    suffix = " …" if len(allowed_ids) > 8 else ""
    return (
        f"{DEPENDS_ON_REF_RULES} "
        f"Allowed instruction ids for this turn: {ids_preview}{suffix}."
    )


def normalize_depends_on_declarations(value: Any) -> list[dict[str, Any]]:
    """Parse model depends_on (objects or legacy strings) into declaration dicts."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            iid = item.get("instruction_id") or item.get("id")
            if isinstance(iid, str) and iid.strip():
                out.append(
                    {
                        "instruction_id": iid.strip(),
                        "confidence": clamp_confidence(item.get("confidence")),
                        "counterfactual": normalize_counterfactual(
                            item.get("counterfactual")
                        ),
                    }
                )
            continue
        if isinstance(item, bool):
            continue
        if isinstance(item, str) and item.strip():
            out.append(
                {
                    "instruction_id": item.strip(),
                    "confidence": DEFAULT_LEGACY_CONFIDENCE,
                    "counterfactual": DEFAULT_LEGACY_COUNTERFACTUAL,
                }
            )
            continue
        if isinstance(item, int) and item > 0:
            out.append(
                {
                    "instruction_id": str(item),
                    "confidence": DEFAULT_LEGACY_CONFIDENCE,
                    "counterfactual": DEFAULT_LEGACY_COUNTERFACTUAL,
                }
            )
            continue
        if isinstance(item, float) and item.is_integer() and item > 0:
            out.append(
                {
                    "instruction_id": str(int(item)),
                    "confidence": DEFAULT_LEGACY_CONFIDENCE,
                    "counterfactual": DEFAULT_LEGACY_COUNTERFACTUAL,
                }
            )
    return out


def normalize_text_depends_on_raw(value: Any) -> list[str]:
    """Normalize depends_on raw values to string refs (instruction ids preferred)."""
    return [
        decl["instruction_id"]
        for decl in normalize_depends_on_declarations(value)
        if isinstance(decl.get("instruction_id"), str) and decl["instruction_id"]
    ]


def normalize_tool_depends_on_raw(value: Any) -> list[str]:
    if isinstance(value, list):
        return normalize_text_depends_on_raw(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return normalize_text_depends_on_raw(parsed)
        except json.JSONDecodeError:
            pass
        return normalize_text_depends_on_raw([stripped])
    return []


def _split_tool_depends_on_ref(item: Any) -> tuple[Optional[int], Optional[str]]:
    """Split one tool depends_on entry into (runtime_step, tool_call_id)."""
    if isinstance(item, bool):
        return None, None
    if isinstance(item, int) and item > 0:
        return item, None
    if isinstance(item, float) and item.is_integer() and item > 0:
        return int(item), None
    if isinstance(item, str):
        stripped = item.strip()
        if not stripped:
            return None, None
        if _looks_like_instruction_id(stripped):
            return None, stripped
        lowered = stripped.lower()
        if lowered.startswith(STEP_REF_PREFIX):
            step_text = stripped[len(STEP_REF_PREFIX) :].strip()
            if step_text.isdigit():
                step = int(step_text)
                if step > 0:
                    return step, None
        if stripped.isdigit():
            step = int(stripped)
            if step > 0:
                return step, None
        return None, stripped
    return None, None


def _make_resolved_entry(
    *,
    instruction_id: str,
    ref: Any,
    ref_type: str,
    source: str,
    confidence: float,
    counterfactual: str,
) -> dict[str, Any]:
    return {
        "instruction_id": instruction_id,
        "ref": ref,
        "ref_type": ref_type,
        "source": source,
        "confidence": clamp_confidence(confidence),
        "counterfactual": normalize_counterfactual(counterfactual),
    }


def resolve_instruction_ids_to_depends_on(
    instructions: list[dict[str, Any]],
    raw_ids: Any,
    *,
    current_runtime_step: Optional[int] = None,
    trace_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    declarations = normalize_depends_on_declarations(raw_ids)
    if not declarations:
        return []
    resolved: list[dict[str, Any]] = []
    for decl in declarations:
        ref = decl["instruction_id"]
        instr = find_instruction_by_id(instructions, ref)
        if instr is None:
            logger.debug(
                "depends_on unresolved instruction_id=%s trace_id=%s",
                ref,
                trace_id or "",
            )
            continue
        step = instr.get("runtime_step")
        if (
            isinstance(current_runtime_step, int)
            and current_runtime_step > 0
            and isinstance(step, int)
            and step >= current_runtime_step
        ):
            logger.debug(
                "depends_on skip self/future instruction_id=%s current=%s trace_id=%s",
                ref,
                current_runtime_step,
                trace_id or "",
            )
            continue
        resolved.append(
            _make_resolved_entry(
                instruction_id=ref,
                ref=ref,
                ref_type=REF_TYPE_INSTRUCTION_ID,
                source=SOURCE_MODEL,
                confidence=decl["confidence"],
                counterfactual=decl["counterfactual"],
            )
        )
    return _dedupe_entries(resolved)


def resolve_depends_on_refs(
    instructions: list[dict[str, Any]],
    raw_refs: Any,
    *,
    current_runtime_step: Optional[int] = None,
    trace_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Resolve depends_on refs: instruction ids first, then legacy step/tool_call_id."""
    raw_list = (
        raw_refs
        if isinstance(raw_refs, list)
        else normalize_tool_depends_on_raw(raw_refs)
    )
    if not raw_list:
        return []
    declarations = normalize_depends_on_declarations(raw_list)
    resolved: list[dict[str, Any]] = []
    legacy: list[Any] = []
    for decl in declarations:
        ref = decl["instruction_id"]
        instr = find_instruction_by_id(instructions, ref)
        if instr is None:
            legacy.append(ref)
            continue
        step = instr.get("runtime_step")
        if (
            isinstance(current_runtime_step, int)
            and current_runtime_step > 0
            and isinstance(step, int)
            and step >= current_runtime_step
        ):
            continue
        resolved.append(
            _make_resolved_entry(
                instruction_id=ref,
                ref=ref,
                ref_type=REF_TYPE_INSTRUCTION_ID,
                source=SOURCE_MODEL,
                confidence=decl["confidence"],
                counterfactual=decl["counterfactual"],
            )
        )
    if legacy:
        resolved.extend(
            resolve_mixed_depends_on_refs(
                instructions,
                legacy,
                current_runtime_step=current_runtime_step,
                trace_id=trace_id,
            )
        )
    return _dedupe_entries(resolved)


def resolve_mixed_depends_on_refs(
    instructions: list[dict[str, Any]],
    raw_refs: Any,
    *,
    current_runtime_step: Optional[int] = None,
    trace_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Resolve tool depends_on values that may mix runtime steps and tool_call_ids."""
    refs = raw_refs if isinstance(raw_refs, list) else normalize_tool_depends_on_raw(raw_refs)
    if not refs:
        return []
    resolved: list[dict[str, Any]] = []
    for item in refs:
        step, tool_call_id = _split_tool_depends_on_ref(item)
        if step is not None:
            resolved.extend(
                resolve_runtime_steps_to_depends_on(
                    instructions,
                    [step],
                    current_runtime_step=current_runtime_step,
                    trace_id=trace_id,
                )
            )
            continue
        if tool_call_id:
            if find_instruction_by_id(instructions, tool_call_id):
                resolved.extend(
                    resolve_instruction_ids_to_depends_on(
                        instructions,
                        [tool_call_id],
                        current_runtime_step=current_runtime_step,
                        trace_id=trace_id,
                    )
                )
                continue
            resolved.extend(
                resolve_tool_call_ids_to_depends_on(
                    instructions,
                    [tool_call_id],
                    trace_id=trace_id,
                )
            )
    return _dedupe_entries(resolved)


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


def find_tool_result_instruction_for_call_id(
    instructions: list[dict[str, Any]], tool_call_id: str
) -> Optional[dict[str, Any]]:
    """Return the first instruction that records a result for ``tool_call_id``."""
    tc_id = tool_call_id.strip()
    if not tc_id:
        return None
    for instr in instructions:
        if not isinstance(instr, dict):
            continue
        content = _instruction_content(instr.get("content"))
        if content.get("tool_call_id") != tc_id:
            continue
        if content.get("result") is None:
            continue
        return instr
    return None


def builder_has_tool_result_for_call_id(
    instructions: list[dict[str, Any]], tool_call_id: str
) -> bool:
    return find_tool_result_instruction_for_call_id(instructions, tool_call_id) is not None


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


_REF_TYPE_DEDUPE_RANK = {
    REF_TYPE_INSTRUCTION_ID: 3,
    REF_TYPE_TOOL_CALL_ID: 2,
    REF_TYPE_RUNTIME_STEP: 1,
}


def _depends_on_entry_rank(entry: dict[str, Any]) -> tuple[float, int, int]:
    ref_type = entry.get("ref_type")
    rank = _REF_TYPE_DEDUPE_RANK.get(ref_type, 0) if isinstance(ref_type, str) else 0
    counterfactual_bonus = 1 if normalize_counterfactual(entry.get("counterfactual")) else 0
    return (clamp_confidence(entry.get("confidence")), rank, counterfactual_bonus)


def _dedupe_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        instr_id = entry.get("instruction_id")
        if not isinstance(instr_id, str) or not instr_id.strip():
            continue
        iid = instr_id.strip()
        prev = by_id.get(iid)
        if prev is None or _depends_on_entry_rank(entry) >= _depends_on_entry_rank(prev):
            by_id[iid] = dict(entry)
    return list(by_id.values())


def resolve_runtime_steps_to_depends_on(
    instructions: list[dict[str, Any]],
    raw_steps: Any,
    *,
    current_runtime_step: Optional[int] = None,
    trace_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    steps: list[int] = []
    for item in normalize_text_depends_on_raw(raw_steps):
        if item.isdigit():
            steps.append(int(item))
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
            _make_resolved_entry(
                instruction_id=instruction_id,
                ref=step,
                ref_type=REF_TYPE_RUNTIME_STEP,
                source=SOURCE_MODEL,
                confidence=DEFAULT_LEGACY_CONFIDENCE,
                counterfactual=DEFAULT_LEGACY_COUNTERFACTUAL,
            )
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
            _make_resolved_entry(
                instruction_id=instruction_id,
                ref=ref,
                ref_type=REF_TYPE_TOOL_CALL_ID,
                source=SOURCE_MODEL,
                confidence=DEFAULT_LEGACY_CONFIDENCE,
                counterfactual=DEFAULT_LEGACY_COUNTERFACTUAL,
            )
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
    iid = instruction_id.strip()
    return [
        _make_resolved_entry(
            instruction_id=iid,
            ref=iid,
            ref_type=REF_TYPE_INSTRUCTION_ID,
            source=SOURCE_KERNEL,
            confidence=KERNEL_TOOL_RESULT_CONFIDENCE,
            counterfactual=KERNEL_TOOL_RESULT_COUNTERFACTUAL,
        )
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
    kind = instruction_ref_kind(instr) or "UNKNOWN"
    instr_id = instr.get("id")
    id_prefix = ""
    if isinstance(instr_id, str) and instr_id.strip():
        id_prefix = instr_id.strip()[:8]
    content = instr.get("content")
    if isinstance(content, str):
        preview = _preview_text_content(content, max_chars=text_preview_chars)
        detail = preview or str(instr.get("instruction_type") or "")
        return f"  {step} {kind} [{id_prefix}] | {detail}"
    if isinstance(content, dict):
        tool_name = content.get("tool_name")
        tool_name_str = (
            str(tool_name).strip() if isinstance(tool_name, str) and tool_name.strip() else "tool"
        )
        tc_id = content.get("tool_call_id")
        tc_prefix = ""
        if isinstance(tc_id, str) and tc_id.strip():
            tc_prefix = tc_id.strip()[:tool_call_id_prefix_chars]
        if tc_prefix:
            return f"  {step} {kind} {tool_name_str} | {tc_prefix}"
        return f"  {step} {kind} {tool_name_str}"
    return f"  {step} {kind}"


def build_step_catalog_with_previews(
    instructions: list[dict[str, Any]],
    *,
    text_preview_chars: int = 60,
    tool_call_id_prefix_chars: int = 12,
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
        return "Prior steps: (none yet.)"
    return "Prior steps (instruction id markers in messages):\n" + "\n".join(lines)


def _tool_history_wording(
    *,
    use_codex_responses_wording: bool = False,
    use_claude_code_wording: bool = False,
) -> str:
    if use_codex_responses_wording:
        return (
            "Prior tool outputs: copy the instruction id from the matching "
            "[ARBITEROS_REF kind=TOOLRESULT] marker. "
        )
    if use_claude_code_wording:
        return (
            "Prior tool outputs: copy the instruction id from the matching "
            "[ARBITEROS_REF kind=TOOLRESULT] marker. "
        )
    return (
        "Prior tool outputs: copy the instruction id from the matching "
        "[ARBITEROS_REF kind=TOOLRESULT] marker in role='tool' messages. "
    )


def build_tool_depends_on_description(
    prior_tool_items: list[tuple[str, str]],
    instructions: list[dict[str, Any]],
    *,
    use_codex_responses_wording: bool = False,
    use_claude_code_wording: bool = False,
    text_preview_chars: int = 60,
    tool_call_id_prefix_chars: int = 12,
    current_runtime_step: Optional[int] = None,
) -> str:
    del prior_tool_items, text_preview_chars, tool_call_id_prefix_chars
    history = _tool_history_wording(
        use_codex_responses_wording=use_codex_responses_wording,
        use_claude_code_wording=use_claude_code_wording,
    )
    allowed_ids = build_allowed_depends_on_instruction_ids(
        instructions, current_runtime_step=current_runtime_step
    )
    parts = [
        DEPENDS_ON_REF_RULES,
        history,
        "Copy exact instruction uuid strings from [ARBITEROS_REF ...] markers.",
    ]
    if allowed_ids:
        preview = ", ".join(allowed_ids[:6])
        suffix = " …" if len(allowed_ids) > 6 else ""
        parts.append(f"Allowed ids for this turn: {preview}{suffix}.")
    return " ".join(parts)


def build_runtime_step_catalog(instructions: list[dict[str, Any]]) -> str:
    """Backward-compatible alias for preview catalog."""
    return build_step_catalog_with_previews(instructions)
