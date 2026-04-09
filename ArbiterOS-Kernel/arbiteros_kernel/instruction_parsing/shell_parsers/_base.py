"""
Shared base types for shell command analysis.

Public API:
    CommandAnalysis   — dataclass with all derived fields (shared by all parsers)
    ShellAnalyzer     — Protocol for the analyze_command callable
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol, runtime_checkable


@dataclass
class CommandAnalysis:
    """All derived analysis results for a single shell command string."""

    command: str
    segments: List[str]
    operators: List[str]
    itypes: List[str]       # per-segment instruction type (EXEC/WRITE/READ)
    itype: str              # folded: EXEC > WRITE > READ
    risks: List[str]        # per-segment risk level (HIGH/UNKNOWN/LOW)
    risk: str               # folded: HIGH > UNKNOWN > LOW
    path_tokens: List[str]
    write_targets: List[str]


@runtime_checkable
class ShellAnalyzer(Protocol):
    """Protocol satisfied by ``bash.analyze_command`` and
    ``powershell.analyze_command``.  Any callable matching this signature can
    be used as a drop-in shell analysis backend.
    """

    def __call__(self, command: str) -> CommandAnalysis:
        ...
