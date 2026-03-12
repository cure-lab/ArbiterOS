"""
Core types, schema constructors, and vocabulary for the instruction_parsing package.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Literal, NamedTuple, Optional

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
    custom: Optional[Dict[str, Any]] = None,
) -> SecurityType:
    return {
        "confidentiality": confidentiality,
        "trustworthiness": trustworthiness,
        "confidence": confidence,
        "reversible": reversible,
        "authority": authority,
        "custom": custom or {},
    }


# ---------------------------------------------------------------------------
# Tool parser contract
# ---------------------------------------------------------------------------


class ToolParseResult(NamedTuple):
    """Result returned by a tool parser."""

    instruction_type: str
    security_type: Optional[SecurityType] = None


ToolParser = Callable[[Dict[str, Any]], ToolParseResult]


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
# Taint status
# ---------------------------------------------------------------------------


class TaintStatus(NamedTuple):
    trustworthiness: SecurityLevel
    confidentiality: SecurityLevel


# LOW(0) < UNKNOWN/MID(1) < HIGH(2); UNKNOWN is treated as MID
_LEVEL_ORDER: Dict[str, int] = {
    "LOW": 0,
    "UNKNOWN": 1,
    "MID": 1,
    "HIGH": 2,
}


def _collect_levels(instructions: List[Dict[str, Any]], key: str) -> List[str]:
    result: List[str] = []
    for instr in instructions or []:
        st = instr.get("security_type")
        if isinstance(st, dict):
            v = st.get(key)
            if isinstance(v, str) and v.strip() and v.strip() in _LEVEL_ORDER:
                result.append(v.strip())
    return result


def compute_taint_status_from_instructions(
    instructions: List[Dict[str, Any]],
) -> TaintStatus:
    """Compute the taint status of the current session from its instruction history.

    - trustworthiness: minimum across all instructions (least trusted wins)
    - confidentiality:  maximum across all instructions (most sensitive wins)
    - UNKNOWN is treated as MID; empty list defaults to MID.
    """
    trust_vals = _collect_levels(instructions, "trustworthiness")
    conf_vals = _collect_levels(instructions, "confidentiality")

    raw_trust = min(trust_vals, key=lambda v: _LEVEL_ORDER[v]) if trust_vals else "MID"
    raw_conf = max(conf_vals, key=lambda v: _LEVEL_ORDER[v]) if conf_vals else "MID"
    trustworthiness: SecurityLevel = "MID" if raw_trust == "UNKNOWN" else raw_trust
    confidentiality: SecurityLevel = "MID" if raw_conf == "UNKNOWN" else raw_conf
    return TaintStatus(trustworthiness=trustworthiness, confidentiality=confidentiality)
