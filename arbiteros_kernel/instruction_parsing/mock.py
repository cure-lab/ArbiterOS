"""
Taint-status oracle for the current agent session.

`get_current_taint_status()` is called whenever the kernel needs to know how
tainted the running session is — e.g. when recording what security labels a
newly written file should carry in the user registry.

Returns the minimum trustworthiness and minimum confidentiality across all
instructions in the current trace's history (LOW < MID < HIGH; UNKNOWN treated as MID).
"""

from contextvars import ContextVar
from typing import Any, List, NamedTuple, Optional

from .types import SecurityLevel

_current_instruction_history: ContextVar[Optional[List[dict[str, Any]]]] = ContextVar(
    "instruction_history", default=None
)


class TaintStatus(NamedTuple):
    trustworthiness: SecurityLevel
    confidentiality: SecurityLevel


# 用于比较的序：LOW(0) < UNKNOWN(1) < MID(2) < HIGH(3)，取最小值即取序最小的
_LEVEL_ORDER: dict[str, int] = {
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
    instructions: List[dict[str, Any]],
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


def get_current_taint_status(
    instructions: Optional[List[dict[str, Any]]] = None,
) -> TaintStatus:
    """Return the taint status of the current agent session.

    取 instruction history 中所有 instruction 的 trustworthiness 和 confidentiality
    各自的最小值。若未传入 instructions，则从 context 中读取（由 builder 在解析前设置）。
    若仍无数据，返回 MID/MID。
    """
    hist = instructions if instructions is not None else _current_instruction_history.get()
    if hist:
        return compute_taint_status_from_instructions(hist)
    return TaintStatus(trustworthiness="MID", confidentiality="MID")


def set_current_instruction_history(instructions: Optional[List[dict[str, Any]]]) -> None:
    """由 InstructionBuilder 在解析 tool call 前设置当前 trace 的 instruction history。"""
    _current_instruction_history.set(instructions)
