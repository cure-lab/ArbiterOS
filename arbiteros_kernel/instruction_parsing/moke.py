"""
Taint-status oracle for the current agent session.

`get_current_taint_status()` is called whenever the kernel needs to know how
tainted the running session is — e.g. when recording what security labels a
newly written file should carry in the user registry.

The real implementation will query the live policy runtime to derive the
worst-case (confidentiality, trustworthiness) across all in-flight tool calls
in the current session.  The stub below returns conservative mid-level defaults
and is meant to be replaced once the runtime integration is ready.
"""

from typing import NamedTuple

from .types import SecurityLevel


class TaintStatus(NamedTuple):
    trustworthiness: SecurityLevel
    confidentiality: SecurityLevel


def get_current_taint_status() -> TaintStatus:
    """Return the taint status of the current agent session.

    Stub — hardcoded to MID/MID.  Replace with live runtime query.
    """
    return TaintStatus(trustworthiness="MID", confidentiality="MID")
