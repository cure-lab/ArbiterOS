"""
Registry package — OS-aware security classification for executables and
file paths.

At import time, ``platform.system()`` is checked once:
  • Windows  →  all public names come from ``registries.windows``
  • Linux / macOS / other  →  all public names come from ``registries.linux``

Callers (tool parsers, etc.) import from this package directly and remain
platform-agnostic:

    from ..registries import classify_confidentiality, classify_trustworthiness

The underlying linux/windows modules are always importable directly for
platform-specific use (e.g. ``bash.py`` always imports from ``.linux``
regardless of the host OS; ``powershell.py`` always imports from ``.windows``).
"""

import platform as _platform

if _platform.system() == "Windows":
    from .windows import (  # noqa: F401
        classify_confidentiality,
        classify_exe,
        classify_exe_risk,
        classify_trustworthiness,
        get_user_registered_paths,
        register_file_taint,
        save_registries,
    )
else:
    from .linux import (  # noqa: F401
        classify_confidentiality,
        classify_exe,
        classify_exe_risk,
        classify_trustworthiness,
        get_user_registered_paths,
        register_file_taint,
        save_registries,
    )

__all__ = [
    "classify_confidentiality",
    "classify_exe",
    "classify_exe_risk",
    "classify_trustworthiness",
    "get_user_registered_paths",
    "register_file_taint",
    "save_registries",
]
