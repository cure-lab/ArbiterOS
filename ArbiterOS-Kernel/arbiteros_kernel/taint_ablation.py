"""
Taint ablation (policy input only): optionally disable propagated taint inheritance
for policy evaluation by setting prop_* to match base trustworthiness/confidentiality.

Does not mutate the instruction lists passed in; returns copies when the toggle is on.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


def _litellm_config_path() -> Optional[Path]:
    here = Path(__file__).resolve().parent
    cand = here.parent / "litellm_config.yaml"
    return cand if cand.is_file() else None


def taint_ablation_disable_inheritance_enabled() -> bool:
    """True when ``taint_ablation.disable_inheritance`` is set in litellm_config.yaml."""
    if yaml is None:
        return False
    path = _litellm_config_path()
    if path is None:
        return False
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    block = raw.get("taint_ablation")
    if not isinstance(block, dict):
        return False
    return bool(block.get("disable_inheritance", False))


def _normalize_instruction_prop_like_base(instr: Dict[str, Any]) -> None:
    st = instr.get("security_type")
    if not isinstance(st, dict):
        return
    if "trustworthiness" in st:
        st["prop_trustworthiness"] = st["trustworthiness"]
    if "confidentiality" in st:
        st["prop_confidentiality"] = st["confidentiality"]


def apply_taint_inheritance_ablation_for_policy(
    *,
    instructions: List[Dict[str, Any]],
    latest_instructions: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    When ``taint_ablation.disable_inheritance`` is true, return deep copies where each
    instruction's ``prop_trustworthiness`` / ``prop_confidentiality`` mirror
    ``trustworthiness`` / ``confidentiality`` (ablation: no inheritance).

    When false, return the original references unchanged.
    """
    latest = latest_instructions if latest_instructions is not None else []

    if not taint_ablation_disable_inheritance_enabled():
        return (instructions, latest)

    instructions_copy = copy.deepcopy(instructions)
    # latest slice is always a suffix of instructions; after deepcopy, same indices apply
    n_tail = len(latest)
    start = max(0, len(instructions_copy) - n_tail)
    for instr in instructions_copy:
        if isinstance(instr, dict):
            _normalize_instruction_prop_like_base(instr)
    latest_copy = instructions_copy[start:] if n_tail else []
    return (instructions_copy, latest_copy)
