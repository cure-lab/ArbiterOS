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


# LOW(0) < UNKNOWN(1) < MID(2) < HIGH(3)，取最小值即取序最小的
_LEVEL_ORDER: Dict[str, int] = {
    "LOW": 0,
    "UNKNOWN": 1,
    "MID": 2,
    "HIGH": 3,
}


def _min_level(values: List[str]) -> SecurityLevel:
    """从多个 SecurityLevel 中取序最小的。空列表返回 MID。"""
    if not values:
        return "MID"
    valid = [v for v in values if v in _LEVEL_ORDER]
    if not valid:
        return "MID"
    return min(valid, key=lambda v: _LEVEL_ORDER[v])  # type: ignore[return-value]


def compute_taint_status_from_instructions(
    instructions: List[Dict[str, Any]],
) -> TaintStatus:
    """从 instruction history 计算 trustworthiness 和 confidentiality 各自的最小值。"""
    trust_vals: List[str] = []
    conf_vals: List[str] = []
    for instr in instructions or []:
        st = instr.get("security_type")
        if isinstance(st, dict):
            t = st.get("trustworthiness")
            c = st.get("confidentiality")
            if isinstance(t, str) and t.strip():
                trust_vals.append(t.strip())
            if isinstance(c, str) and c.strip():
                conf_vals.append(c.strip())
    return TaintStatus(
        trustworthiness=_min_level(trust_vals),
        confidentiality=_min_level(conf_vals),
    )
