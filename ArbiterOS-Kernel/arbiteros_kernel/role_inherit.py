"""Copy the parent's governance role onto a child that has none yet."""

from __future__ import annotations

from typing import Optional


def inherit_parent_role_if_unset(trace_id: str) -> Optional[str]:
    """
    If this trace has a graph parent with a named role, persist that role
    as ``source=inherit`` and return it. Otherwise return None (caller
    falls through to default).
    """
    from arbiteros_kernel.agent_graph import parent_of
    from arbiteros_kernel.trace_roles import get_trace_role, set_trace_role

    tid = (trace_id or "").strip()
    if not tid:
        return None
    current = get_trace_role(tid)
    if current.get("role_locked_by_os"):
        return None
    existing = current.get("role_name")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    parent_tid = parent_of(tid)
    if not isinstance(parent_tid, str) or not parent_tid.strip():
        return None
    stored = get_trace_role(parent_tid.strip()).get("role_name")
    if not isinstance(stored, str) or not stored.strip():
        return None
    parent_role = stored.strip()
    set_trace_role(
        tid,
        role_name=parent_role,
        source="inherit",
        locked_by_os=False,
    )
    return parent_role
