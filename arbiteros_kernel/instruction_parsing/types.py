"""
Core types, schema constructors, and vocabulary for the instruction_parsing package.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Literal, NamedTuple, Optional, cast

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base type aliases
# ---------------------------------------------------------------------------

SecurityType = Dict[str, Any]
RuleType = Dict[str, Any]
Instruction = Dict[str, Any]

SecurityLevel = Literal["LOW", "MID", "HIGH", "UNKNOWN"]
AuthorityLevel = Literal[
    "HUMAN_APPROVED",
    "POLICY_APPROVED",
    "HUMAN_BLOCKED",
    "POLICY_BLOCKED",
    "UNKNOWN",
]


# ---------------------------------------------------------------------------
# Schema constructors
# ---------------------------------------------------------------------------


def make_security_type(
    *,
    confidentiality: SecurityLevel,
    trustworthiness: SecurityLevel,
    confidence: SecurityLevel,
    reversible: bool,
    authority: AuthorityLevel,
    risk: SecurityLevel = "LOW",
    custom: Optional[Dict[str, Any]] = None,
) -> SecurityType:
    return {
        "confidentiality": confidentiality,
        "trustworthiness": trustworthiness,
        "confidence": confidence,
        "reversible": reversible,
        "authority": authority,
        "risk": risk,
        "custom": custom or {},
    }


# ---------------------------------------------------------------------------
# Taint status
# ---------------------------------------------------------------------------


class TaintStatus(NamedTuple):
    trustworthiness: SecurityLevel
    confidentiality: SecurityLevel


# ---------------------------------------------------------------------------
# Tool parser contract
# ---------------------------------------------------------------------------


class ToolParseResult(NamedTuple):
    """Result returned by a tool parser."""

    instruction_type: str
    security_type: SecurityType


ToolParser = Callable[[Dict[str, Any], Optional[TaintStatus]], ToolParseResult]


# ---------------------------------------------------------------------------
# Instruction type → category mapping
# ---------------------------------------------------------------------------

# Maps each atomic instruction type to its high-level category string.
# Used by InstructionBuilder to populate instruction_category.
INSTRUCTION_TYPE_TO_CATEGORY: Dict[str, str] = {
    # Cognitive
    "REASON": "COGNITIVE.Reasoning",
    "PLAN": "COGNITIVE.Reasoning",
    "CRITIQUE": "COGNITIVE.Reasoning",
    # Memory
    "STORE": "MEMORY.Management",
    "RETRIEVE": "MEMORY.Management",
    "COMPRESS": "MEMORY.Management",
    "PRUNE": "MEMORY.Management",
    # Env execution / I/O
    "READ": "EXECUTION.Env",
    "WRITE": "EXECUTION.Env",
    "EXEC": "EXECUTION.Env",
    "WAIT": "EXECUTION.Env",
    # Human interaction
    "ASK": "EXECUTION.Human",
    "RESPOND": "EXECUTION.Human",
    "USER_MESSAGE": "EXECUTION.Human",
    # Agent collaboration
    "DELEGATE": "EXECUTION.Agent",
    # Perception / events
    "SUBSCRIBE": "EXECUTION.Perception",
    "RECEIVE": "EXECUTION.Perception",
}


# ---------------------------------------------------------------------------
# Taint computation helpers
# ---------------------------------------------------------------------------


# LOW(0) < UNKNOWN(0.5) < MID(1) < HIGH(2); UNKNOWN stays as UNKNOWN in output
LEVEL_ORDER: Dict[str, float] = {
    "LOW": 0,
    "UNKNOWN": 0.5,
    "MID": 1,
    "HIGH": 2,
}

# All concrete (non-sentinel) levels sorted low → high.
# Automatically includes any future level added to LEVEL_ORDER.
CONCRETE_LEVELS: List[SecurityLevel] = sorted(
    (cast(SecurityLevel, k) for k in LEVEL_ORDER if k != "UNKNOWN"),
    key=lambda v: LEVEL_ORDER[v],
)

# All levels including the "UNKNOWN" sentinel.
ALL_LEVELS: List[SecurityLevel] = [*CONCRETE_LEVELS, "UNKNOWN"]


def collect_levels(instructions: List[Dict[str, Any]], key: str) -> List[str]:
    result: List[str] = []
    for instr in instructions or []:
        st = instr.get("security_type")
        if isinstance(st, dict):
            v = st.get(key)
            if isinstance(v, str) and v.strip() and v.strip() in LEVEL_ORDER:
                result.append(v.strip())
    return result


def compute_taint_status_from_instructions(
    instructions: List[Dict[str, Any]],
) -> TaintStatus:
    """Compute the taint status of the current session from its instruction history.

    Aggregation rules:
    - trustworthiness: minimum across all instructions (least trusted wins)
    - confidentiality:  maximum across all instructions (most sensitive wins)

    UNKNOWN semantics (registry does not define UNKNOWN):
    - trustworthiness: UNKNOWN participates with score 0.5; when UNKNOWN wins, normalised to MID.
    - confidentiality: UNKNOWN participates with score 0.5; when UNKNOWN wins, normalised to LOW.
    - Final output is always LOW, MID, or HIGH — never UNKNOWN.
    """
    trust_vals = collect_levels(instructions, "trustworthiness")
    conf_vals = collect_levels(instructions, "confidentiality")

    # UNKNOWN participates in min/max (LEVEL_ORDER has UNKNOWN=0.5)
    raw_trust = (
        min(trust_vals, key=lambda v: LEVEL_ORDER[v])
        if trust_vals
        else "UNKNOWN"
    )
    raw_conf = (
        max(conf_vals, key=lambda v: LEVEL_ORDER[v])
        if conf_vals
        else "UNKNOWN"
    )

    if raw_trust == "UNKNOWN":
        logger.warning(
            "compute_taint_status_from_instructions: trustworthiness resolved to "
            "UNKNOWN (no concrete level found); keeping as UNKNOWN."
        )
        raw_trust = "MID"
    if raw_conf == "UNKNOWN":
        logger.warning(
            "compute_taint_status_from_instructions: confidentiality resolved to "
            "UNKNOWN (no concrete level found); normalised to LOW."
        )
        raw_conf = "LOW"

    return TaintStatus(trustworthiness=raw_trust, confidentiality=raw_conf)


def compute_prop_taint_for_instruction(
    instructions: List[Dict[str, Any]],
    instr: Dict[str, Any],
) -> TaintStatus:
    """Compute prop_confidentiality and prop_trustworthiness for a single instruction.

    - Pure text (no tool_name): use own confidentiality and trustworthiness.
    - Tool call: aggregate own conf/trust + all instructions whose tool_call_id
      equals this one or any reference_tool_id. trust=min, conf=max.
    """
    st = instr.get("security_type")
    if not isinstance(st, dict):
        return TaintStatus(trustworthiness="MID", confidentiality="LOW")

    def _safe_level(v: Any) -> str:
        s = v if isinstance(v, str) else "UNKNOWN"
        return (s or "UNKNOWN").strip() or "UNKNOWN"

    own_conf = _safe_level(st.get("confidentiality"))
    own_trust = _safe_level(st.get("trustworthiness"))

    content = instr.get("content")
    if not isinstance(content, dict) or "tool_name" not in content:
        # Pure text: use own values
        return TaintStatus(
            trustworthiness=own_trust if own_trust in LEVEL_ORDER else "MID",
            confidentiality=own_conf if own_conf in LEVEL_ORDER else "LOW",
        )

    # Tool call: collect tool_call_ids to include (self + reference_tool_id)
    tc_id = content.get("tool_call_id")
    ref_ids = content.get("arguments", {}).get("reference_tool_id") or []
    ids_to_include: set[str] = set()
    if isinstance(tc_id, str) and tc_id.strip():
        ids_to_include.add(tc_id.strip())
    for r in ref_ids if isinstance(ref_ids, list) else []:
        if isinstance(r, str) and r.strip():
            ids_to_include.add(r.strip())

    trust_vals: List[str] = [own_trust]
    conf_vals: List[str] = [own_conf]

    for other in instructions or []:
        if other is instr:
            continue
        oc = other.get("content")
        if not isinstance(oc, dict):
            continue
        o_tc = oc.get("tool_call_id")
        if not isinstance(o_tc, str) or o_tc.strip() not in ids_to_include:
            continue
        ost = other.get("security_type")
        if not isinstance(ost, dict):
            continue
        prop_conf = ost.get("prop_confidentiality") or ost.get("confidentiality")
        prop_trust = ost.get("prop_trustworthiness") or ost.get("trustworthiness")
        if isinstance(prop_conf, str) and prop_conf.strip() in LEVEL_ORDER:
            conf_vals.append(prop_conf.strip())
        if isinstance(prop_trust, str) and prop_trust.strip() in LEVEL_ORDER:
            trust_vals.append(prop_trust.strip())

    raw_trust = (
        min(trust_vals, key=lambda v: LEVEL_ORDER[v])
        if trust_vals
        else "UNKNOWN"
    )
    raw_conf = (
        max(conf_vals, key=lambda v: LEVEL_ORDER[v])
        if conf_vals
        else "UNKNOWN"
    )
    if raw_trust == "UNKNOWN":
        raw_trust = "MID"
    if raw_conf == "UNKNOWN":
        raw_conf = "LOW"
    return TaintStatus(trustworthiness=raw_trust, confidentiality=raw_conf)
