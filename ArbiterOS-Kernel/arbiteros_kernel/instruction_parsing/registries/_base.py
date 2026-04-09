"""
Generic two-layer (source + user) YAML registry for exe and file-path
security classification.

Both the Linux and Windows registries are instances of RegistrySet,
parameterised by source-directory and user-directory so the same logic
serves both rule sets without code duplication.

Public class:
    RegistrySet   — manages one source/user YAML pair
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

_URL_PREFIXES = ("http://", "https://", "ftp://")


# ---------------------------------------------------------------------------
# Module-level I/O helpers (shared, no registry-specific state)
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


# ---------------------------------------------------------------------------
# Path matching helpers (shared, no registry-specific state)
# ---------------------------------------------------------------------------


def _is_classifiable(p: str) -> bool:
    """Return True for paths worth consulting the registry: absolute filesystem
    paths (including ``~/…`` after expanduser) and URLs.
    Relative filesystem paths are rejected — they cannot be matched reliably
    without knowing the working directory.

    Windows drive-letter paths (``C:/...`` or ``C:\\...``) are accepted even on
    non-Windows hosts so that PowerShell command analysis works in cross-platform
    environments (e.g. WSL2).
    """
    if os.path.isabs(os.path.expanduser(p)):
        return True
    if p.startswith(_URL_PREFIXES):
        return True
    # Windows absolute path: drive-letter + colon + separator (C:/ or C:\)
    norm = p.replace("\\", "/")
    if len(norm) >= 3 and norm[1] == ":" and norm[2] == "/" and norm[0].isalpha():
        return True
    return False


def _path_matches(path: str, pattern: str) -> bool:
    """Return True if *path* matches *pattern* (glob, PurePosixPath-aware).

    Both *path* and *pattern* are normalised with expanduser before comparison
    so that registry entries written as ``~/...`` match against already-expanded
    absolute paths (and vice-versa).

    Backslashes are normalised to forward slashes before comparison so that
    Windows-style paths (C:\\Users\\...) and registry entries (C:/Users/...)
    match correctly on all host platforms.
    """
    norm_path = os.path.expanduser(path).replace("\\", "/")
    norm_pattern = os.path.expanduser(pattern).replace("\\", "/")

    if fnmatch.fnmatch(norm_path, norm_pattern):
        return True
    try:
        if PurePosixPath(norm_path).match(norm_pattern):
            return True
    except Exception:
        logger.debug(
            "_path_matches: PurePosixPath.match failed for path=%r pattern=%r",
            norm_path,
            norm_pattern,
            exc_info=True,
        )
    # Basename match for extension patterns (e.g. *.pem)
    return fnmatch.fnmatch(os.path.basename(norm_path), norm_pattern)


# ---------------------------------------------------------------------------
# RegistrySet
# ---------------------------------------------------------------------------


class RegistrySet:
    """Two-layer (source + user) YAML registry for exe and file-path security
    classification.

    *source_dir* holds the built-in YAML files shipped with the package
    (read-only).  *user_dir* holds user overrides; these are read-write and
    take precedence over the source layer on any match.

    Both layers follow the same four-file layout:
        exe_registry.yaml        — EXEC / WRITE / READ per executable name
        exe_risk.yaml            — HIGH / LOW risk per executable name
        file_confidentiality.yaml — HIGH / LOW per path glob
        file_trustworthiness.yaml — HIGH / LOW per path glob
    """

    def __init__(self, source_dir: str, user_dir: str, name: str = "registry") -> None:
        self._source_dir = source_dir
        self._user_dir = user_dir
        self._name = name

        # In-memory cache — source layer (never written back)
        self._exe_source: Optional[Dict[str, List[str]]] = None
        self._file_conf_source: Optional[Dict[str, List[str]]] = None
        self._file_trust_source: Optional[Dict[str, List[str]]] = None
        self._exe_risk_source: Optional[Dict[str, List[str]]] = None

        # In-memory cache — user layer (persisted on exit / explicit save)
        self._exe_user: Optional[Dict[str, List[str]]] = None
        self._file_conf_user: Optional[Dict[str, List[str]]] = None
        self._file_trust_user: Optional[Dict[str, List[str]]] = None
        self._exe_risk_user: Optional[Dict[str, List[str]]] = None

        self._exe_dirty: bool = False
        self._file_conf_dirty: bool = False
        self._file_trust_dirty: bool = False
        self._exe_risk_dirty: bool = False

        # Eager user-registry bootstrap — create all user config files on import
        # (creates empty-list stubs only when the file does not already exist)
        self._ensure_user_registry(self._user_path("exe_registry.yaml"), ["EXEC", "WRITE", "READ"])
        self._ensure_user_registry(self._user_path("file_confidentiality.yaml"), ["HIGH", "LOW"])
        self._ensure_user_registry(self._user_path("file_trustworthiness.yaml"), ["HIGH", "LOW"])
        self._ensure_user_registry(self._user_path("exe_risk.yaml"), ["HIGH", "LOW"])

        atexit.register(self.save_registries)

    # ------------------------------------------------------------------
    # Internal path helpers
    # ------------------------------------------------------------------

    def _source_path(self, filename: str) -> str:
        return os.path.join(self._source_dir, filename)

    def _user_path(self, filename: str) -> str:
        return os.path.join(self._user_dir, filename)

    def _ensure_user_registry(self, path: str, keys: List[str]) -> None:
        """Create *path* with empty-list values for each key if it does not exist."""
        if os.path.exists(path):
            return
        _save_yaml_registry(path, {k: [] for k in keys})

    # ------------------------------------------------------------------
    # Lazy getters — source layer
    # ------------------------------------------------------------------

    def _get_exe_source(self) -> Dict[str, List[str]]:
        if self._exe_source is None:
            self._exe_source = _load_yaml_registry(self._source_path("exe_registry.yaml"))
        return self._exe_source

    def _get_exe_risk_source(self) -> Dict[str, List[str]]:
        if self._exe_risk_source is None:
            self._exe_risk_source = _load_yaml_registry(self._source_path("exe_risk.yaml"))
        return self._exe_risk_source

    def _get_conf_source(self) -> Dict[str, List[str]]:
        if self._file_conf_source is None:
            self._file_conf_source = _load_yaml_registry(
                self._source_path("file_confidentiality.yaml")
            )
        return self._file_conf_source

    def _get_trust_source(self) -> Dict[str, List[str]]:
        if self._file_trust_source is None:
            self._file_trust_source = _load_yaml_registry(
                self._source_path("file_trustworthiness.yaml")
            )
        return self._file_trust_source

    # ------------------------------------------------------------------
    # Lazy getters — user layer
    # ------------------------------------------------------------------

    def _get_exe_user(self) -> Dict[str, List[str]]:
        if self._exe_user is None:
            self._ensure_user_registry(self._user_path("exe_registry.yaml"), ["EXEC", "WRITE", "READ"])
            self._exe_user = _load_yaml_registry(self._user_path("exe_registry.yaml"))
        return self._exe_user

    def _get_exe_risk_user(self) -> Dict[str, List[str]]:
        if self._exe_risk_user is None:
            self._ensure_user_registry(self._user_path("exe_risk.yaml"), ["HIGH", "LOW"])
            self._exe_risk_user = _load_yaml_registry(self._user_path("exe_risk.yaml"))
        return self._exe_risk_user

    def _get_conf_user(self) -> Dict[str, List[str]]:
        if self._file_conf_user is None:
            self._ensure_user_registry(
                self._user_path("file_confidentiality.yaml"), ["HIGH", "LOW"]
            )
            self._file_conf_user = _load_yaml_registry(
                self._user_path("file_confidentiality.yaml")
            )
        return self._file_conf_user

    def _get_trust_user(self) -> Dict[str, List[str]]:
        if self._file_trust_user is None:
            self._ensure_user_registry(
                self._user_path("file_trustworthiness.yaml"), ["HIGH", "LOW"]
            )
            self._file_trust_user = _load_yaml_registry(
                self._user_path("file_trustworthiness.yaml")
            )
        return self._file_trust_user

    # ------------------------------------------------------------------
    # atexit flush — writes only to user layer
    # ------------------------------------------------------------------

    def save_registries(self) -> None:
        """Flush all dirty user registries to disk immediately.

        Called automatically on interpreter exit (via atexit).  May also be
        called explicitly whenever changes need to be persisted right away.
        """
        if self._exe_dirty and self._exe_user is not None:
            _save_yaml_registry(self._user_path("exe_registry.yaml"), self._exe_user)
            self._exe_dirty = False
        if self._file_conf_dirty and self._file_conf_user is not None:
            _save_yaml_registry(
                self._user_path("file_confidentiality.yaml"), self._file_conf_user
            )
            self._file_conf_dirty = False
        if self._file_trust_dirty and self._file_trust_user is not None:
            _save_yaml_registry(
                self._user_path("file_trustworthiness.yaml"), self._file_trust_user
            )
            self._file_trust_dirty = False
        if self._exe_risk_dirty and self._exe_risk_user is not None:
            _save_yaml_registry(self._user_path("exe_risk.yaml"), self._exe_risk_user)
            self._exe_risk_dirty = False

    # ------------------------------------------------------------------
    # Public API — user registry inspection / mutation
    # ------------------------------------------------------------------

    def get_user_registered_paths(self) -> List[str]:
        """Return a flat list of every file path recorded in the user
        confidentiality registry.

        Useful for testing and auditing: callers can check whether a specific
        file has been registered (i.e. produced by a previous WRITE operation)
        without accessing private module state directly.
        """
        user_conf = self._get_conf_user()
        return [path for entries in user_conf.values() for path in entries]

    def register_file_taint(
        self,
        path: str,
        trustworthiness: SecurityLevel,
        confidentiality: SecurityLevel,
    ) -> None:
        """Record the security labels of a written file in the user registry.

        The effective label stored is the worst-case of the supplied taint and
        the source-layer classification for the path:
          • confidentiality: higher level wins  (LOW < UNKNOWN < HIGH)
          • trustworthiness: lower  level wins  (LOW < UNKNOWN < HIGH)

        Backslashes are normalised to forward slashes so that Windows paths
        written as ``C:\\Users\\...`` are stored and looked up consistently.
        Only absolute paths (POSIX or Windows drive-letter) are registered.
        """
        path = os.path.expanduser(path).replace("\\", "/")
        # Accept POSIX absolute paths and Windows drive-letter paths (C:/...)
        is_posix_abs = os.path.isabs(path)
        is_win_abs = len(path) >= 3 and path[1] == ":" and path[2] == "/"
        if not (is_posix_abs or is_win_abs):
            logger.debug("register_file_taint: skipping non-absolute path %r", path)
            return

        source_conf: SecurityLevel = next(
            (
                lvl
                for lvl in _CONF_LEVELS
                if any(_path_matches(path, pat) for pat in self._get_conf_source().get(lvl, []))
            ),
            "UNKNOWN",
        )
        source_trust: SecurityLevel = next(
            (
                lvl
                for lvl in _TRUST_LEVELS
                if any(_path_matches(path, pat) for pat in self._get_trust_source().get(lvl, []))
            ),
            "UNKNOWN",
        )

        effective_conf: SecurityLevel = max(
            confidentiality, source_conf, key=lambda v: LEVEL_ORDER.get(v, 1)
        )
        effective_trust: SecurityLevel = min(
            trustworthiness, source_trust, key=lambda v: LEVEL_ORDER.get(v, 1)
        )

        conf = self._get_conf_user()
        for lvl in ALL_LEVELS:
            entries = conf.setdefault(lvl, [])
            if path in entries:
                entries.remove(path)
        if effective_conf != "UNKNOWN":
            conf.setdefault(effective_conf, []).append(path)
        self._file_conf_dirty = True

        trust = self._get_trust_user()
        for lvl in ALL_LEVELS:
            entries = trust.setdefault(lvl, [])
            if path in entries:
                entries.remove(path)
        if effective_trust != "UNKNOWN":
            trust.setdefault(effective_trust, []).append(path)
        self._file_trust_dirty = True

    # ------------------------------------------------------------------
    # Public API — classification
    # ------------------------------------------------------------------

    def classify_exe(self, exe: str, subcommand: Optional[str]) -> str:
        """Return instruction type (EXEC/WRITE/READ) for *exe*.

        User registry is checked first; source registry is the fallback.
        Priority within each registry: EXEC > WRITE > READ.
        Default when nothing matches: EXEC.
        """
        candidates: List[str] = []
        if subcommand:
            candidates.append(f"{exe} {subcommand}")
        candidates.append(exe)

        for reg in (self._get_exe_user(), self._get_exe_source()):
            for category in ("EXEC", "WRITE", "READ"):
                for candidate in candidates:
                    if candidate in reg.get(category, []):
                        return category
        return "EXEC"

    def classify_exe_risk(self, exe: str, subcommand: Optional[str]) -> SecurityLevel:
        """Return risk level for *exe* based on exe_risk.yaml.

        User registry is checked first; source registry is the fallback.
        Level priority: HIGH > UNKNOWN > LOW; UNKNOWN is returned when no
        pattern matches.
        """
        candidates: List[str] = []
        if subcommand:
            candidates.append(f"{exe} {subcommand}")
        candidates.append(exe)

        for level in ("HIGH", "LOW"):
            for layer, reg in (
                ("user", self._get_exe_risk_user()),
                ("source", self._get_exe_risk_source()),
            ):
                for candidate in candidates:
                    if candidate in reg.get(level, []):
                        logger.debug(
                            "%s classify_exe_risk: %r (sub=%r) → %s"
                            " (layer=%s, matched=%r)",
                            self._name,
                            exe,
                            subcommand,
                            level,
                            layer,
                            candidate,
                        )
                        return level  # type: ignore[return-value]
        logger.debug(
            "%s classify_exe_risk: %r (sub=%r) → UNKNOWN (no match)",
            self._name,
            exe,
            subcommand,
        )
        return "UNKNOWN"

    def classify_confidentiality(self, paths: List[str]) -> SecurityLevel:
        """Return highest confidentiality level matching any of *paths*.

        User registry is checked first; source registry is the fallback.
        Priority: HIGH > LOW; default UNKNOWN.
        """
        if not paths:
            return "UNKNOWN"
        abs_paths = [p for p in paths if _is_classifiable(p)]
        if not abs_paths:
            logger.debug(
                "%s classify_confidentiality: no classifiable paths in %s;"
                " returning UNKNOWN",
                self._name,
                paths,
            )
            return "UNKNOWN"
        for layer, reg in (("user", self._get_conf_user()), ("source", self._get_conf_source())):
            for level in _CONF_LEVELS:
                for path in abs_paths:
                    for pat in reg.get(level, []):
                        if _path_matches(path, pat):
                            logger.debug(
                                "%s classify_confidentiality: %r → %s"
                                " (layer=%s, pattern=%r)",
                                self._name,
                                path,
                                level,
                                layer,
                                pat,
                            )
                            return level
        logger.debug(
            "%s classify_confidentiality: no rule matched %s; returning UNKNOWN",
            self._name,
            abs_paths,
        )
        return "UNKNOWN"

    def _registry_trust_for_path(self, path: str) -> SecurityLevel:
        """Trustworthiness from YAML registries only (user layer, then source)."""
        for layer, reg in (
            ("user", self._get_trust_user()),
            ("source", self._get_trust_source()),
        ):
            for level in _TRUST_LEVELS:
                for pat in reg.get(level, []):
                    if _path_matches(path, pat):
                        logger.debug(
                            "%s _registry_trust_for_path: %r → %s"
                            " (layer=%s, pattern=%r)",
                            self._name,
                            path,
                            level,
                            layer,
                            pat,
                        )
                        return level
        logger.debug(
            "%s _registry_trust_for_path: %r → UNKNOWN (no rule matched)",
            self._name,
            path,
        )
        return "UNKNOWN"

    def classify_trustworthiness(self, paths: List[str]) -> SecurityLevel:
        """Return lowest trustworthiness level matching any of *paths*.

        User registry is checked first; source registry is the fallback.
        Priority: LOW > HIGH (worst-case wins); default UNKNOWN.

        When a skills root is set (``ARBITEROS_SKILLS_ROOT`` or
        ``arbiteros_skill_trust.skills_root`` in ``litellm_config.yaml``),
        paths under that ``.../skills/<name>/…`` tree may use skill-scanner
        results (cached in ``skill_trust_by_name.json``), which override YAML
        registry for that path.  Across paths, least trusted wins.
        """
        if not paths:
            return "UNKNOWN"
        abs_paths = [p for p in paths if _is_classifiable(p)]
        if not abs_paths:
            logger.debug(
                "%s classify_trustworthiness: no classifiable paths in %s;"
                " returning UNKNOWN",
                self._name,
                paths,
            )
            return "UNKNOWN"

        from ..tool_parsers import skill_trust  # local import to avoid circular dependency

        skills_root = skill_trust.get_skills_root()
        per_path: List[SecurityLevel] = []
        for path in abs_paths:
            if skills_root:
                t = skill_trust.trust_for_path_with_skills_root(
                    path, skills_root, self._registry_trust_for_path
                )
            else:
                t = self._registry_trust_for_path(path)
            per_path.append(t)

        worst = min(per_path, key=lambda v: LEVEL_ORDER.get(v, 10))
        logger.debug(
            "%s classify_trustworthiness: paths=%s per_path=%s → %s",
            self._name,
            abs_paths,
            list(zip(abs_paths, per_path)),
            worst,
        )
        return worst
