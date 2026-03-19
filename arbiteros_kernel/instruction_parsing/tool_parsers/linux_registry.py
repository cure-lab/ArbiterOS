"""
Linux-registry helpers: two-layer YAML rule-sets for execution and path
security classification.

Layer 1 — source (read-only):
    <package>/linux_registry/*.yaml   (shipped with the code; never written)

Layer 2 — user (read-write):
    ~/.arbiteros/instruction_parsing/linux_registry/*.yaml
    (empty on first run; all writes go here; overrides source on match)

Classification strategy:
    The user layer is checked first.  On a match the result is returned
    immediately.  Only when no match is found in the user layer is the
    source layer consulted.  This lets users extend or override built-in
    rules without touching the shipped files.
"""

import atexit
import fnmatch
import logging
import os
from pathlib import PurePosixPath
from typing import Dict, List, Optional

import yaml

from ..types import ALL_LEVELS, CONCRETE_LEVELS, LEVEL_ORDER, SecurityLevel

logger = logging.getLogger(__name__)

# Levels ordered for classification: confidentiality (highest wins) and
# trustworthiness (lowest wins), derived from the shared CONCRETE_LEVELS table.
_CONF_LEVELS: List[SecurityLevel] = list(reversed(CONCRETE_LEVELS))
_TRUST_LEVELS: List[SecurityLevel] = list(CONCRETE_LEVELS)

# ---------------------------------------------------------------------------
# Directory paths
# ---------------------------------------------------------------------------

# Source layer — read-only, ships with the package
_SOURCE_REGISTRY_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "linux_registry")
)

# User layer — read-write, lives in ~/.arbiteros/
# Override with ARBITEROS_USER_REGISTRY_DIR for testing or alternative deployments.
_USER_REGISTRY_DIR = os.environ.get(
    "ARBITEROS_USER_REGISTRY_DIR",
    os.path.join(
        os.path.expanduser("~"),
        ".arbiteros",
        "instruction_parsing",
        "linux_registry",
    ),
)

# ---------------------------------------------------------------------------
# In-memory state — source layer (never written back)
# ---------------------------------------------------------------------------

_EXE_SOURCE: Optional[Dict[str, List[str]]] = None
_FILE_CONF_SOURCE: Optional[Dict[str, List[str]]] = None
_FILE_TRUST_SOURCE: Optional[Dict[str, List[str]]] = None

# ---------------------------------------------------------------------------
# In-memory state — user layer (persisted on exit / explicit save)
# ---------------------------------------------------------------------------

_EXE_USER: Optional[Dict[str, List[str]]] = None
_FILE_CONF_USER: Optional[Dict[str, List[str]]] = None
_FILE_TRUST_USER: Optional[Dict[str, List[str]]] = None

_EXE_DIRTY: bool = False
_FILE_CONF_DIRTY: bool = False
_FILE_TRUST_DIRTY: bool = False

# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _load_yaml_registry(path: str) -> Dict[str, List[str]]:
    """Load a YAML registry file from *path*, returning {} on any failure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return {k: [str(v) for v in vs] for k, vs in (data or {}).items()}
    except Exception:
        logger.error("Failed to load YAML registry from %s", path, exc_info=True)
        return {}


def _save_yaml_registry(path: str, data: Dict[str, List[str]]) -> bool:
    """Persist *data* to *path*, creating parent directories as needed.

    Returns True on success; errors are silently swallowed so a save
    failure never crashes the policy-check hot path.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=True)
        return True
    except Exception:
        logger.error("Failed to save YAML registry to %s", path, exc_info=True)
        return False


def _source_path(filename: str) -> str:
    return os.path.join(_SOURCE_REGISTRY_DIR, filename)


def _user_path(filename: str) -> str:
    return os.path.join(_USER_REGISTRY_DIR, filename)


def _ensure_user_registry(path: str, keys: List[str]) -> None:
    """Create *path* with empty-list values for each key if it does not exist."""
    if os.path.exists(path):
        return
    _save_yaml_registry(path, {k: [] for k in keys})


# ---------------------------------------------------------------------------
# atexit flush — writes only to user layer
# ---------------------------------------------------------------------------


def _atexit_save() -> None:
    """Flush dirty user registries to ~/.arbiteros/... on interpreter exit."""
    global _EXE_DIRTY, _FILE_CONF_DIRTY, _FILE_TRUST_DIRTY
    if _EXE_DIRTY and _EXE_USER is not None:
        _save_yaml_registry(_user_path("exe_registry.yaml"), _EXE_USER)
        _EXE_DIRTY = False
    if _FILE_CONF_DIRTY and _FILE_CONF_USER is not None:
        _save_yaml_registry(_user_path("file_confidentiality.yaml"), _FILE_CONF_USER)
        _FILE_CONF_DIRTY = False
    if _FILE_TRUST_DIRTY and _FILE_TRUST_USER is not None:
        _save_yaml_registry(_user_path("file_trustworthiness.yaml"), _FILE_TRUST_USER)
        _FILE_TRUST_DIRTY = False


atexit.register(_atexit_save)

# ---------------------------------------------------------------------------
# Lazy getters — source layer
# ---------------------------------------------------------------------------


def _get_exe_source() -> Dict[str, List[str]]:
    global _EXE_SOURCE
    if _EXE_SOURCE is None:
        _EXE_SOURCE = _load_yaml_registry(_source_path("exe_registry.yaml"))
    return _EXE_SOURCE


def _get_conf_source() -> Dict[str, List[str]]:
    global _FILE_CONF_SOURCE
    if _FILE_CONF_SOURCE is None:
        _FILE_CONF_SOURCE = _load_yaml_registry(
            _source_path("file_confidentiality.yaml")
        )
    return _FILE_CONF_SOURCE


def _get_trust_source() -> Dict[str, List[str]]:
    global _FILE_TRUST_SOURCE
    if _FILE_TRUST_SOURCE is None:
        _FILE_TRUST_SOURCE = _load_yaml_registry(
            _source_path("file_trustworthiness.yaml")
        )
    return _FILE_TRUST_SOURCE


# ---------------------------------------------------------------------------
# Lazy getters — user layer
# ---------------------------------------------------------------------------


def _get_exe_user() -> Dict[str, List[str]]:
    global _EXE_USER
    if _EXE_USER is None:
        _ensure_user_registry(_user_path("exe_registry.yaml"), ["EXEC", "WRITE", "READ"])
        _EXE_USER = _load_yaml_registry(_user_path("exe_registry.yaml"))
    return _EXE_USER


def _get_conf_user() -> Dict[str, List[str]]:
    global _FILE_CONF_USER
    if _FILE_CONF_USER is None:
        _ensure_user_registry(
            _user_path("file_confidentiality.yaml"), ["HIGH", "MID", "LOW"]
        )
        _FILE_CONF_USER = _load_yaml_registry(_user_path("file_confidentiality.yaml"))
    return _FILE_CONF_USER


def _get_trust_user() -> Dict[str, List[str]]:
    global _FILE_TRUST_USER
    if _FILE_TRUST_USER is None:
        _ensure_user_registry(
            _user_path("file_trustworthiness.yaml"), ["HIGH", "MID", "LOW"]
        )
        _FILE_TRUST_USER = _load_yaml_registry(_user_path("file_trustworthiness.yaml"))
    return _FILE_TRUST_USER


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
        logger.debug(
            "_path_matches: PurePosixPath.match failed for path=%r pattern=%r",
            expanded,
            pattern,
            exc_info=True,
        )
    # Basename match for extension patterns (e.g. *.pem)
    return fnmatch.fnmatch(os.path.basename(path), pattern)


# ---------------------------------------------------------------------------
# Mutators — write only to user layer
# ---------------------------------------------------------------------------


def save_registries() -> None:
    """Explicitly flush all dirty user registries to disk immediately.

    Call this whenever you need changes to be persisted right away rather
    than waiting for the interpreter to exit (e.g. after a batch update).
    """
    _atexit_save()


def get_user_registered_paths() -> List[str]:
    """Return a flat list of every file path recorded in the user confidentiality registry.

    Useful for testing and auditing: callers can check whether a specific file
    has been registered (i.e. produced by a previous WRITE operation) without
    accessing private module state directly.
    """
    user_conf = _get_conf_user()
    return [path for entries in user_conf.values() for path in entries]


def register_file_taint(
    path: str,
    trustworthiness: SecurityLevel,
    confidentiality: SecurityLevel,
) -> None:
    """Record the security labels of a written file in the user registry.

    The effective label stored is the worst-case of the supplied taint and the
    source-layer classification for the path:
      • confidentiality: higher level wins  (HIGH > MID > LOW > UNKNOWN)
      • trustworthiness: lower  level wins  (LOW  < MID < HIGH < UNKNOWN)

    This ensures that a file's inherent sensitivity (e.g. .env → HIGH conf in
    the source registry) is never downgraded by a low-taint write.

    The path is moved to the effective level in the user registry (removed from
    any other level first) and both registries are marked dirty.
    """
    global _FILE_CONF_DIRTY, _FILE_TRUST_DIRTY

    # Source-layer classification for the path
    source_conf: SecurityLevel = next(
        (
            lvl
            for lvl in _CONF_LEVELS
            if any(_path_matches(path, pat) for pat in _get_conf_source().get(lvl, []))
        ),
        "UNKNOWN",
    )
    source_trust: SecurityLevel = next(
        (
            lvl
            for lvl in _TRUST_LEVELS
            if any(_path_matches(path, pat) for pat in _get_trust_source().get(lvl, []))
        ),
        "UNKNOWN",
    )

    # Worst-case: more restrictive of source and taint.
    # UNKNOWN is treated as MID, so normalise after comparison.
    _raw_conf: SecurityLevel = max(
        confidentiality, source_conf, key=lambda v: LEVEL_ORDER.get(v, 1)
    )
    effective_conf: SecurityLevel = "MID" if _raw_conf == "UNKNOWN" else _raw_conf
    _raw_trust: SecurityLevel = min(
        trustworthiness, source_trust, key=lambda v: LEVEL_ORDER.get(v, 1)
    )
    effective_trust: SecurityLevel = "MID" if _raw_trust == "UNKNOWN" else _raw_trust

    conf = _get_conf_user()
    for lvl in ALL_LEVELS:
        entries = conf.setdefault(lvl, [])
        if path in entries:
            entries.remove(path)
    conf.setdefault(effective_conf, []).append(path)
    _FILE_CONF_DIRTY = True

    trust = _get_trust_user()
    for lvl in ALL_LEVELS:
        entries = trust.setdefault(lvl, [])
        if path in entries:
            entries.remove(path)
    trust.setdefault(effective_trust, []).append(path)
    _FILE_TRUST_DIRTY = True


def classify_exe(exe: str, subcommand: Optional[str]) -> str:
    """Return instruction type (EXEC/WRITE/READ) for *exe*.

    User registry is checked first; source registry is the fallback.
    Priority within each registry: EXEC > WRITE > READ.
    Default when nothing matches: EXEC.
    """
    candidates: List[str] = []
    if subcommand:
        candidates.append(f"{exe} {subcommand}")
    candidates.append(exe)

    for reg in (_get_exe_user(), _get_exe_source()):
        for category in ("EXEC", "WRITE", "READ"):
            for candidate in candidates:
                if candidate in reg.get(category, []):
                    return category
    return "EXEC"


def classify_confidentiality(paths: List[str]) -> SecurityLevel:
    """Return highest confidentiality level matching any of *paths*.

    User registry is checked first; source registry is the fallback.
    Priority: HIGH > MID > LOW; default UNKNOWN.
    """
    if not paths:
        return "UNKNOWN"
    for reg in (_get_conf_user(), _get_conf_source()):
        for level in _CONF_LEVELS:
            for path in paths:
                if any(_path_matches(path, pat) for pat in reg.get(level, [])):
                    return level
    logger.info(
        "classify_confidentiality: no rule matched %s; returning UNKNOWN", paths
    )
    return "UNKNOWN"


def classify_trustworthiness(paths: List[str]) -> SecurityLevel:
    """Return lowest trustworthiness level matching any of *paths*.

    User registry is checked first; source registry is the fallback.
    Priority: LOW > MID > HIGH (worst-case wins); default UNKNOWN.
    """
    if not paths:
        return "UNKNOWN"
    for reg in (_get_trust_user(), _get_trust_source()):
        for level in _TRUST_LEVELS:
            for path in paths:
                if any(_path_matches(path, pat) for pat in reg.get(level, [])):
                    return level
    logger.info(
        "classify_trustworthiness: no rule matched %s; returning UNKNOWN", paths
    )
    return "UNKNOWN"
