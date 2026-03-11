"""
Linux-registry helpers: load, cache, classify, and persist the YAML
rule-sets that drive execution and path security classification.

Three registries are managed here:
  exe_registry.yaml          — maps executable names to instruction types
  file_confidentiality.yaml  — maps path patterns to confidentiality levels
  file_trustworthiness.yaml  — maps path patterns to trustworthiness levels

All registries are loaded lazily on first access, held in memory, and
flushed back to disk only when dirty (on exit or via save_registries()).
"""

import atexit
import fnmatch
import os
from pathlib import PurePosixPath
from typing import Dict, List, Optional

from ..types import SecurityLevel

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_LINUX_REGISTRY_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "linux_registry")
)

_EXE_REGISTRY: Optional[Dict[str, List[str]]] = None
_FILE_CONF_REGISTRY: Optional[Dict[str, List[str]]] = None
_FILE_TRUST_REGISTRY: Optional[Dict[str, List[str]]] = None

# Dirty flags — set to True whenever an in-memory registry is modified.
# The atexit handler only writes back registries that were actually changed.
_EXE_DIRTY: bool = False
_FILE_CONF_DIRTY: bool = False
_FILE_TRUST_DIRTY: bool = False

# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def _load_yaml_registry(filename: str) -> Dict[str, List[str]]:
    """Load a linux_registry YAML file, returning {} on failure."""
    try:
        import yaml

        path = os.path.join(_LINUX_REGISTRY_DIR, filename)
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return {k: [str(v) for v in vs] for k, vs in (data or {}).items()}
    except Exception:
        return {}


def _save_yaml_registry(filename: str, data: Dict[str, List[str]]) -> bool:
    """Persist *data* back to the linux_registry YAML file.

    Returns True on success, False on failure (errors are silently swallowed
    so a save failure never crashes the policy-check hot path).
    """
    try:
        import yaml

        path = os.path.join(_LINUX_REGISTRY_DIR, filename)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=True)
        return True
    except Exception:
        return False


def _atexit_save() -> None:
    """Flush dirty registries to disk when the interpreter exits."""
    global _EXE_DIRTY, _FILE_CONF_DIRTY, _FILE_TRUST_DIRTY
    if _EXE_DIRTY and _EXE_REGISTRY is not None:
        _save_yaml_registry("exe_registry.yaml", _EXE_REGISTRY)
        _EXE_DIRTY = False
    if _FILE_CONF_DIRTY and _FILE_CONF_REGISTRY is not None:
        _save_yaml_registry("file_confidentiality.yaml", _FILE_CONF_REGISTRY)
        _FILE_CONF_DIRTY = False
    if _FILE_TRUST_DIRTY and _FILE_TRUST_REGISTRY is not None:
        _save_yaml_registry("file_trustworthiness.yaml", _FILE_TRUST_REGISTRY)
        _FILE_TRUST_DIRTY = False


atexit.register(_atexit_save)

# ---------------------------------------------------------------------------
# Getters (lazy load)
# ---------------------------------------------------------------------------


def _get_exe_registry() -> Dict[str, List[str]]:
    global _EXE_REGISTRY
    if _EXE_REGISTRY is None:
        _EXE_REGISTRY = _load_yaml_registry("exe_registry.yaml")
    return _EXE_REGISTRY


def _get_conf_registry() -> Dict[str, List[str]]:
    global _FILE_CONF_REGISTRY
    if _FILE_CONF_REGISTRY is None:
        _FILE_CONF_REGISTRY = _load_yaml_registry("file_confidentiality.yaml")
    return _FILE_CONF_REGISTRY


def _get_trust_registry() -> Dict[str, List[str]]:
    global _FILE_TRUST_REGISTRY
    if _FILE_TRUST_REGISTRY is None:
        _FILE_TRUST_REGISTRY = _load_yaml_registry("file_trustworthiness.yaml")
    return _FILE_TRUST_REGISTRY

# ---------------------------------------------------------------------------
# Mutators
# ---------------------------------------------------------------------------


def update_exe_registry(data: Dict[str, List[str]]) -> None:
    """Replace the in-memory exe registry and mark it dirty."""
    global _EXE_REGISTRY, _EXE_DIRTY
    _EXE_REGISTRY = data
    _EXE_DIRTY = True


def update_conf_registry(data: Dict[str, List[str]]) -> None:
    """Replace the in-memory file-confidentiality registry and mark it dirty."""
    global _FILE_CONF_REGISTRY, _FILE_CONF_DIRTY
    _FILE_CONF_REGISTRY = data
    _FILE_CONF_DIRTY = True


def update_trust_registry(data: Dict[str, List[str]]) -> None:
    """Replace the in-memory file-trustworthiness registry and mark it dirty."""
    global _FILE_TRUST_REGISTRY, _FILE_TRUST_DIRTY
    _FILE_TRUST_REGISTRY = data
    _FILE_TRUST_DIRTY = True


def save_registries() -> None:
    """Explicitly flush all dirty registries to disk immediately.

    Call this whenever you need changes to be persisted right away rather than
    waiting for the interpreter to exit (e.g. after a batch admin update).
    """
    _atexit_save()

# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def _path_matches(path: str, pattern: str) -> bool:
    """Return True if *path* matches *pattern* (glob, PurePosixPath-aware)."""
    # Full-string match first — handles URLs (http://*) and absolute globs
    if fnmatch.fnmatch(path, pattern):
        return True
    # Expand leading ~ so patterns like ~/.ssh/* work
    expanded = os.path.expanduser(path)
    if fnmatch.fnmatch(expanded, pattern):
        return True
    try:
        if PurePosixPath(expanded).match(pattern):
            return True
    except Exception:
        pass
    # Basename match for extension patterns (e.g. *.pem)
    return fnmatch.fnmatch(os.path.basename(path), pattern)


def classify_exe(exe: str, subcommand: Optional[str]) -> str:
    """
    Return the instruction type (EXEC / WRITE / READ) for *exe*.

    Checks in priority order EXEC → WRITE → READ; the first match wins.
    Within each category the compound pattern "exe subcommand" is tried
    before the bare executable name.  Default when nothing matches: EXEC.
    """
    reg = _get_exe_registry()
    candidates: List[str] = []
    if subcommand:
        candidates.append(f"{exe} {subcommand}")
    candidates.append(exe)

    for category in ("EXEC", "WRITE", "READ"):
        patterns = reg.get(category, [])
        for candidate in candidates:
            if candidate in patterns:
                return category
    return "EXEC"


def classify_confidentiality(paths: List[str]) -> SecurityLevel:
    """
    Return the highest confidentiality level that matches any of *paths*.
    Priority: HIGH > MID > LOW; default UNKNOWN.
    """
    if not paths:
        return "UNKNOWN"
    reg = _get_conf_registry()
    for level in ("HIGH", "MID", "LOW"):
        for path in paths:
            if any(_path_matches(path, pat) for pat in reg.get(level, [])):
                return level
    return "UNKNOWN"


def classify_trustworthiness(paths: List[str]) -> SecurityLevel:
    """
    Return the lowest (most conservative) trustworthiness level that matches
    any of *paths*.  Priority: LOW > MID > HIGH (worst-case wins); default UNKNOWN.
    """
    if not paths:
        return "UNKNOWN"
    reg = _get_trust_registry()
    for level in ("LOW", "MID", "HIGH"):
        for path in paths:
            if any(_path_matches(path, pat) for pat in reg.get(level, [])):
                return level
    return "UNKNOWN"
