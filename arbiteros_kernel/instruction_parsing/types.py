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

    This function is the **final aggregation step** and enforces a two-stage contract:

    - Parsing-stage classifiers may legitimately emit ``UNKNOWN`` for paths or
      executables that are not registered in any registry.  That sentinel travels
      through individual instructions unchanged so that information is preserved.
    - The aggregated result returned here must always be a concrete level
      (LOW / MID / HIGH) because downstream policy checks require a definite
      value.  Any ``UNKNOWN`` result — whether inherited from individual
      instructions or produced by an empty instruction list — is normalised to
      ``MID`` and a warning is logged so that callers are aware of the
      substitution.

    Aggregation rules:
    - trustworthiness: minimum across all instructions (least trusted wins).
    - confidentiality:  maximum across all instructions (most sensitive wins).
    - ``UNKNOWN`` sits between ``LOW`` and ``MID`` in the ordering (score 0.5)
      during comparison, so it can be superseded by any concrete level.
    """
    trust_vals = collect_levels(instructions, "trustworthiness")
    conf_vals = collect_levels(instructions, "confidentiality")

    raw_trust = min(trust_vals, key=lambda v: LEVEL_ORDER[v]) if trust_vals else "UNKNOWN"
    raw_conf = max(conf_vals, key=lambda v: LEVEL_ORDER[v]) if conf_vals else "UNKNOWN"

    trustworthiness: SecurityLevel = raw_trust
    confidentiality: SecurityLevel = raw_conf

    if trustworthiness == "UNKNOWN":
        logger.warning(
            "compute_taint_status_from_instructions: trustworthiness resolved to "
            "UNKNOWN (no concrete level found); normalising to MID for downstream processing."
        )
        trustworthiness = "MID"
    if confidentiality == "UNKNOWN":
        logger.warning(
            "compute_taint_status_from_instructions: confidentiality resolved to "
            "UNKNOWN (no concrete level found); normalising to MID for downstream processing."
        )
        confidentiality = "MID"

    return TaintStatus(trustworthiness=trustworthiness, confidentiality=confidentiality)
