"""
Shell parser package — OS-aware command analysis.

At import time, ``platform.system()`` is checked once:
  • Windows  →  ``analyze_command`` comes from ``powershell``
  • Linux / macOS / other  →  ``analyze_command`` comes from ``bash``

``CommandAnalysis`` and ``ShellAnalyzer`` are always imported from ``_base``
regardless of the host OS.

Typical usage:

    from ..shell_parsers import analyze_command, CommandAnalysis
"""

import platform as _platform

from ._base import CommandAnalysis, ShellAnalyzer  # noqa: F401

if _platform.system() == "Windows":
    from .powershell import analyze_command  # noqa: F401
else:
    from .bash import analyze_command  # noqa: F401

__all__ = ["analyze_command", "CommandAnalysis", "ShellAnalyzer"]
