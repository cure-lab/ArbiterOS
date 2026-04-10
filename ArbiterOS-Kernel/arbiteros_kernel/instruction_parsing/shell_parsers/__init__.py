"""
Shell parser package — OS-aware command analysis.

At import time, ``platform.system()`` is checked once to select both the
appropriate shell parser and the matching registry:
  • Windows  →  ``powershell`` parser  +  ``windows`` registry
  • Darwin   →  ``bash`` parser        +  ``darwin`` registry
  • other    →  ``bash`` parser        +  ``linux`` registry

``CommandAnalysis`` and ``ShellAnalyzer`` are always imported from ``_base``
regardless of the host OS.

Typical usage:

    from ..shell_parsers import analyze_command, CommandAnalysis
"""

import functools
import platform as _platform

from ._base import CommandAnalysis, ShellAnalyzer  # noqa: F401

if _platform.system() == "Windows":
    from .powershell import analyze_command as _analyze_command
    from ..registries.windows import classify_exe as _classify_exe
    from ..registries.windows import classify_exe_risk as _classify_exe_risk
elif _platform.system() == "Darwin":
    from .bash import analyze_command as _analyze_command
    from ..registries.darwin import classify_exe as _classify_exe
    from ..registries.darwin import classify_exe_risk as _classify_exe_risk
else:
    from .bash import analyze_command as _analyze_command
    from ..registries.linux import classify_exe as _classify_exe
    from ..registries.linux import classify_exe_risk as _classify_exe_risk

analyze_command = functools.partial(
    _analyze_command,
    classify_exe=_classify_exe,
    classify_exe_risk=_classify_exe_risk,
)

__all__ = ["analyze_command", "CommandAnalysis", "ShellAnalyzer"]
