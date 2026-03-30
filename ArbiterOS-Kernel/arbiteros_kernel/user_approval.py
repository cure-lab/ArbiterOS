"""
User approval preprocessing: temporarily elevate prop_* for instructions
that were previously blocked and user chose to allow (user_approved=True),
before passing to policy check.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from .instruction_parsing.types import (
    compute_prop_taint_for_instruction,
)


def _get_tool_call_id(instr: Dict[str, Any]) -> Optional[str]:
    """Extract tool_call_id from instruction content."""
    content = instr.get("content")
    if not isinstance(content, dict):
        return None
    tc_id = content.get("tool_call_id")
    return tc_id.strip() if isinstance(tc_id, str) and tc_id.strip() else None


def _get_reference_tool_ids(instr: Dict[str, Any]) -> List[str]:
    """Extract reference_tool_id list from instruction content."""
    content = instr.get("content")
    if not isinstance(content, dict):
        return []
    args = content.get("arguments") or {}
    ref_ids = args.get("reference_tool_id")
    if not isinstance(ref_ids, list):
        return []
    result = []
    for r in ref_ids:
        if isinstance(r, str) and r.strip():
            result.append(r.strip())
    return result


def _get_last_n_valid_instructions(
    instructions: List[Dict[str, Any]],
    exclude_indices: set[int],
    n: int = 5,
) -> List[Dict[str, Any]]:
    """
    From the end of instructions backwards, collect up to n "valid" instructions.
    Excludes: instructions at exclude_indices, policy_confirmation_ask,
    instructions with policy_protected. Deduplicates by tool_call_id
    (same tool_call_id counts as one); when deduplicating, keep the earlier one
    (lower index), since only that one can have user_approved.
    """
    result: List[Dict[str, Any]] = []
    tc_id_to_result_index: Dict[str, int] = {}
    for i in range(len(instructions) - 1, -1, -1):
        if i in exclude_indices:
            continue
        instr = instructions[i]
        if instr.get("policy_confirmation_ask"):
            continue
        if "policy_protected" in instr:
            continue
        tc_id = _get_tool_call_id(instr)
        if tc_id:
            if tc_id in tc_id_to_result_index:
                # Replace with earlier (lower index) instruction
                result[tc_id_to_result_index[tc_id]] = instr
            else:
                result.append(instr)
                tc_id_to_result_index[tc_id] = len(result) - 1
        else:
            result.append(instr)
        if len(result) >= n:
            break
    return result


def apply_user_approval_preprocessing(
    *,
    instructions: List[Dict[str, Any]],
    latest_instructions: Optional[List[Dict[str, Any]]] = None,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Create a deep copy of instructions, apply user_approval elevation, and
    recompute current prop_* for instructions with reference_tool_id.

    Returns (instructions_for_policy, latest_instructions_for_policy).
    """
    instructions_copy = copy.deepcopy(instructions)
    if not instructions_copy:
        latest = list(latest_instructions) if latest_instructions else []
        return (instructions_copy, latest)

    # Current = last len(latest_instructions) of instructions
    n_tail = len(latest_instructions) if latest_instructions else 0
    start = max(0, len(instructions_copy) - n_tail)
    exclude_indices = set(range(start, len(instructions_copy)))
    latest_for_policy = instructions_copy[start:]

    # Get last 5 valid (excluding current, policy_confirmation_ask, policy_protected, dedup by tool_call_id)
    last_5_valid = _get_last_n_valid_instructions(
        instructions_copy, exclude_indices, n=5
    )

    # Find user_approved instructions in those 5
    approved = [instr for instr in last_5_valid if instr.get("user_approved")]

    # Elevate: for each approved, set self + reference_tool_id targets to HIGH/LOW
    for instr in approved:
        ref_ids = _get_reference_tool_ids(instr)
        tc_id = _get_tool_call_id(instr)
        ids_to_elevate = set(ref_ids)
        if tc_id:
            ids_to_elevate.add(tc_id)
        for other in instructions_copy:
            o_tc = _get_tool_call_id(other)
            if o_tc and o_tc in ids_to_elevate:
                st = other.get("security_type")
                if isinstance(st, dict):
                    st["prop_trustworthiness"] = "HIGH"
                    st["prop_confidentiality"] = "LOW"

    # Recompute prop for current instructions that have reference_tool_id
    for instr in latest_for_policy:
        if _get_reference_tool_ids(instr):
            taint = compute_prop_taint_for_instruction(instructions_copy, instr)
            st = instr.get("security_type")
            if isinstance(st, dict):
                st["prop_trustworthiness"] = taint.trustworthiness
                st["prop_confidentiality"] = taint.confidentiality

    return (instructions_copy, latest_for_policy)
