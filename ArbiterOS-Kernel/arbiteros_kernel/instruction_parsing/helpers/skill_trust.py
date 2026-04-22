"""
Skill-scanner integration: map skill package paths to trustworthiness using
cisco-ai-skill-scanner (CLI), with a persistent JSON cache under the user
registry directory (same as linux_registry YAML files).

Cache hits skip re-scanning only when ``SKILL.md`` SHA-256 matches the value
stored in the cache entry (``skill_md_sha256``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from typing import Any, Dict, Optional, Set, Tuple, Union

import platform as _platform

import yaml

from ..types import LEVEL_ORDER, SecurityLevel

logger = logging.getLogger(__name__)

# ThreatCategory values that force LOW regardless of max_severity (empty disables).
FORCE_LOW_TRUST_CATEGORIES: frozenset[str] = frozenset()


# Same directory as the OS-appropriate user registry.
_IS_WINDOWS = _platform.system() == "Windows"
_IS_DARWIN = _platform.system() == "Darwin"
_USER_REGISTRY_DIR = os.environ.get(
    "ARBITEROS_USER_REGISTRY_DIR",
    os.path.join(
        os.path.expanduser("~"),
        ".arbiteros",
        "instruction_parsing",
        "windows_registry" if _IS_WINDOWS else "darwin_registry" if _IS_DARWIN else "linux_registry",
    ),
)

SKILL_TRUST_CACHE_FILENAME = "skill_trust_by_name.json"

# Per-skill manifest for cache invalidation (same basename as common Agent Skills layout).
SKILL_MD_BASENAME = "SKILL.md"
_SKILL_MD_SHA256_HEX_LEN = 64

# Values: legacy plain trust string, or dict (see schema in ``trust_from_scan_report``).
_SKILL_TRUST_CACHE: Dict[str, Union[str, Dict[str, Any]]] = {}
_SKILL_TRUST_CACHE_LOADED: bool = False
_SKILL_TRUST_LOCK = threading.Lock()
_SCAN_LOCKS: Dict[str, threading.Lock] = {}
_SCAN_LOCKS_GUARD = threading.Lock()


def _cache_path() -> str:
    return os.path.join(_USER_REGISTRY_DIR, SKILL_TRUST_CACHE_FILENAME)


def _ensure_cache_dir() -> None:
    os.makedirs(_USER_REGISTRY_DIR, exist_ok=True)


def _load_cache_from_disk() -> None:
    global _SKILL_TRUST_CACHE_LOADED, _SKILL_TRUST_CACHE
    with _SKILL_TRUST_LOCK:
        if _SKILL_TRUST_CACHE_LOADED:
            return
        path = _cache_path()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    text = fh.read()
                stripped = text.strip()
                if not stripped:
                    _SKILL_TRUST_CACHE = {}
                else:
                    raw = json.loads(stripped)
                if stripped and isinstance(raw, dict):
                    merged: Dict[str, Union[str, Dict[str, Any]]] = {}
                    for k, v in raw.items():
                        if not isinstance(k, str):
                            continue
                        if isinstance(v, str):
                            merged[k] = v
                        elif isinstance(v, dict):
                            merged[k] = v
                    _SKILL_TRUST_CACHE = merged
                else:
                    _SKILL_TRUST_CACHE = {}
            except json.JSONDecodeError as e:
                logger.warning(
                    "skill_trust: invalid or empty JSON in %s (%s); starting with empty cache",
                    path,
                    e,
                )
                _SKILL_TRUST_CACHE = {}
            except Exception:
                logger.debug("skill_trust: failed to load %s", path, exc_info=True)
                _SKILL_TRUST_CACHE = {}
        else:
            _SKILL_TRUST_CACHE = {}
        _SKILL_TRUST_CACHE_LOADED = True


def _persist_cache() -> None:
    _ensure_cache_dir()
    path = _cache_path()
    try:
        with _SKILL_TRUST_LOCK:
            payload = dict(_SKILL_TRUST_CACHE)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
    except Exception:
        logger.warning("skill_trust: failed to write %s", path, exc_info=True)


def skills_root_raw() -> str:
    """Config/env value before ``exists/isdir`` checks (may be a host-only path in Docker)."""
    raw = os.environ.get("ARBITEROS_SKILLS_ROOT", "").strip()
    if not raw:
        raw = (_read_skills_root_from_litellm_config() or "").strip()
    return raw


def get_skills_root() -> Optional[str]:
    """Return normalized absolute path to the ``.../skills`` directory, or None.

    Priority: non-empty ``ARBITEROS_SKILLS_ROOT`` environment variable, then
    ``arbiteros_skill_trust.skills_root`` in ``litellm_config.yaml`` (see
    ``ARBITEROS_LITELLM_CONFIG`` or search path).
    """
    raw = skills_root_raw()
    if not raw:
        return None
    expanded = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(expanded):
        logger.debug("skill_trust: skills root is not a directory: %s", expanded)
        return None
    return expanded


def is_local_registry_path(p: str) -> bool:
    """True for absolute filesystem paths (after expanduser), excluding URLs."""
    ex = os.path.expanduser(p)
    return os.path.isabs(ex)


def skill_name_under_root(path: str, skills_root: str) -> Optional[str]:
    """First path segment under *skills_root* is the skill name (case-sensitive)."""
    try:
        expanded = os.path.abspath(os.path.expanduser(path))
        root = os.path.abspath(os.path.expanduser(skills_root))
    except Exception:
        return None
    if not expanded.startswith(root + os.sep) and expanded != root:
        return None
    rel = os.path.relpath(expanded, root)
    if rel == ".":
        return None
    first = rel.split(os.sep)[0]
    return first if first else None


def list_skill_packages(skills_root: str) -> list[tuple[str, str]]:
    """Return sorted ``(skill_name, absolute_skill_dir)`` for each direct child directory.

    Skips dot-directories. Used for startup warm-up of the trust cache.
    """
    out: list[tuple[str, str]] = []
    try:
        root = os.path.abspath(os.path.expanduser(skills_root))
        if not os.path.isdir(root):
            return out
        for name in sorted(os.listdir(root)):
            if name.startswith("."):
                continue
            d = os.path.join(root, name)
            if os.path.isdir(d):
                out.append((name, os.path.abspath(d)))
    except OSError as e:
        logger.warning("skill_trust: cannot list %s: %s", skills_root, e)
    return out


def _litellm_config_path() -> Optional[str]:
    env = os.environ.get("ARBITEROS_LITELLM_CONFIG", "").strip()
    if env and os.path.isfile(env):
        return env
    cwd = os.path.join(os.getcwd(), "litellm_config.yaml")
    if os.path.isfile(cwd):
        return cwd
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        cand = os.path.join(here, "litellm_config.yaml")
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return None


def _read_skills_root_from_litellm_config() -> Optional[str]:
    """Read ``arbiteros_skill_trust.skills_root`` from ``litellm_config.yaml``."""
    path = _litellm_config_path()
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        block = data.get("arbiteros_skill_trust") or {}
        if not isinstance(block, dict):
            return None
        root = block.get("skills_root")
        if root is None:
            return None
        s = str(root).strip()
        return s or None
    except Exception:
        logger.debug(
            "skill_trust: could not read arbiteros_skill_trust from %s", path, exc_info=True
        )
        return None


def _read_skill_scanner_llm_from_litellm_config() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    path = _litellm_config_path()
    if not path:
        return None, None, None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        block = data.get("skill_scanner_llm") or {}
        if not isinstance(block, dict):
            return None, None, None
        model = (block.get("model") or "").strip() or None
        api_base = (block.get("api_base") or "").strip() or None
        api_key = (block.get("api_key") or "").strip() or None
        if model and api_base and api_key:
            return model, api_base, api_key
    except Exception:
        logger.debug("skill_trust: could not read skill_scanner_llm from %s", path, exc_info=True)
    return None, None, None


def max_severity_to_trustworthiness(severity: str) -> SecurityLevel:
    """Map scanner ``max_severity`` to a trustworthiness level."""
    s = (severity or "").strip().upper()
    if s in ("CRITICAL", "HIGH"):
        return "LOW"
    if s == "MEDIUM":
        return "UNKNOWN"
    if s in ("LOW", "INFO", "SAFE"):
        return "HIGH"
    logger.debug("skill_trust: unknown max_severity %r → UNKNOWN", severity)
    return "UNKNOWN"


def _finding_categories(findings: Any) -> Set[str]:
    out: Set[str] = set()
    if not isinstance(findings, list):
        return out
    for f in findings:
        if isinstance(f, dict):
            c = f.get("category")
            if isinstance(c, str) and c.strip():
                out.add(c.strip())
    return out


def skill_md_path(skill_dir: str) -> str:
    """Absolute path to ``SKILL.md`` inside a skill package directory."""
    return os.path.join(os.path.abspath(os.path.expanduser(skill_dir)), SKILL_MD_BASENAME)


def compute_skill_md_sha256(skill_dir: str) -> Optional[str]:
    """SHA-256 (hex) of ``SKILL.md`` bytes. Missing file → hash of empty bytes.

    Returns None if the file exists but cannot be read (permissions, etc.).
    """
    path = skill_md_path(skill_dir)
    try:
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                data = fh.read()
        else:
            data = b""
    except OSError:
        logger.debug("skill_trust: cannot read %s for hash", path, exc_info=True)
        return None
    return hashlib.sha256(data).hexdigest()


def _skill_md_sha256_from_cache_entry(
    entry: Union[str, Dict[str, Any], None],
) -> Optional[str]:
    """Return normalized hex hash from cache entry, or None if absent/invalid."""
    if not isinstance(entry, dict):
        return None
    raw = entry.get("skill_md_sha256")
    if not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if len(s) != _SKILL_MD_SHA256_HEX_LEN or any(c not in "0123456789abcdef" for c in s):
        return None
    return s


def _cache_allows_skip_scan(
    entry: Union[str, Dict[str, Any], None],
    skill_dir: str,
) -> bool:
    """True if entry has valid trust *and* stored ``SKILL.md`` hash matches disk."""
    if _cache_entry_trustworthiness(entry) is None:
        return False
    current = compute_skill_md_sha256(skill_dir)
    if current is None:
        return False
    stored = _skill_md_sha256_from_cache_entry(entry)
    if stored is None:
        return False
    return stored == current.lower()


def trust_from_scan_report(report: Dict[str, Any]) -> Tuple[SecurityLevel, Dict[str, Any]]:
    """Derive trust from scanner JSON and build a cache record.

    If any finding's ``category`` is in ``FORCE_LOW_TRUST_CATEGORIES``,
    trustworthiness is ``LOW``; otherwise ``max_severity`` is mapped via
    ``max_severity_to_trustworthiness``.
    """
    findings = report.get("findings") or []
    categories = _finding_categories(findings)
    categories_force_low = sorted(FORCE_LOW_TRUST_CATEGORIES & categories)

    max_sev_raw = report.get("max_severity")
    max_sev_str = max_sev_raw.strip().upper() if isinstance(max_sev_raw, str) else ""

    if categories_force_low:
        trust: SecurityLevel = "LOW"
    else:
        trust = max_severity_to_trustworthiness(max_sev_str)

    record: Dict[str, Any] = {
        "schema_version": 3,
        "trustworthiness": trust,
        "max_severity": max_sev_str or None,
        "categories_present": sorted(categories),
        "categories_force_low": categories_force_low,
        "findings_count": len(findings) if isinstance(findings, list) else 0,
    }
    return trust, record


def _cache_entry_trustworthiness(entry: Union[str, Dict[str, Any], None]) -> Optional[SecurityLevel]:
    """Return trust level from a cache value, or None if invalid."""
    if entry is None:
        return None
    if isinstance(entry, str):
        return entry if entry in LEVEL_ORDER else None  # type: ignore[return-value]
    if isinstance(entry, dict):
        t = entry.get("trustworthiness")
        if isinstance(t, str) and t in LEVEL_ORDER:
            return t  # type: ignore[return-value]
    return None


def is_skill_cached(skill_name: str, skill_dir: str) -> bool:
    """Return True if trust is cached *and* ``SKILL.md`` hash matches (scan can be skipped)."""
    _load_cache_from_disk()
    with _SKILL_TRUST_LOCK:
        entry = _SKILL_TRUST_CACHE.get(skill_name)
    return _cache_allows_skip_scan(entry, skill_dir)


def _scan_skill_package(skill_dir: str) -> Optional[Dict[str, Any]]:
    if not shutil.which("skill-scanner"):
        logger.debug("skill_trust: skill-scanner not on PATH")
        return None

    model, api_base, api_key = _read_skill_scanner_llm_from_litellm_config()
    use_llm = bool(model and api_base and api_key)

    fd, report_path = tempfile.mkstemp(suffix=".json", prefix="skill_scan_")
    os.close(fd)
    try:
        cmd = [
            "skill-scanner",
            "scan",
            skill_dir,
            "--use-behavioral",
            "--format",
            "json",
            "-o",
            report_path,
        ]
        if use_llm:
            cmd.append("--use-llm")

        env = os.environ.copy()
        if use_llm:
            env["SKILL_SCANNER_LLM_MODEL"] = model  # type: ignore[assignment]
            env["SKILL_SCANNER_LLM_BASE_URL"] = api_base  # type: ignore[assignment]
            env["SKILL_SCANNER_LLM_API_KEY"] = api_key  # type: ignore[assignment]

        r = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if r.returncode != 0:
            logger.debug(
                "skill_trust: skill-scanner failed rc=%s stderr=%s",
                r.returncode,
                (r.stderr or "")[:2000],
            )
            return None
        with open(report_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        logger.debug("skill_trust: scan exception for %s", skill_dir, exc_info=True)
        return None
    finally:
        try:
            os.unlink(report_path)
        except OSError:
            pass


def _lock_for_skill(skill_name: str) -> threading.Lock:
    with _SCAN_LOCKS_GUARD:
        if skill_name not in _SCAN_LOCKS:
            _SCAN_LOCKS[skill_name] = threading.Lock()
        return _SCAN_LOCKS[skill_name]


def resolve_trust_for_skill(skill_name: str, skill_dir: str) -> Optional[SecurityLevel]:
    """Return cached trust or run scanner; persist on success. None if unavailable."""
    _load_cache_from_disk()
    with _SKILL_TRUST_LOCK:
        cached = _SKILL_TRUST_CACHE.get(skill_name)
    hit = _cache_entry_trustworthiness(cached)
    if hit is not None and _cache_allows_skip_scan(cached, skill_dir):
        return hit

    with _lock_for_skill(skill_name):
        with _SKILL_TRUST_LOCK:
            cached = _SKILL_TRUST_CACHE.get(skill_name)
        hit = _cache_entry_trustworthiness(cached)
        if hit is not None and _cache_allows_skip_scan(cached, skill_dir):
            return hit

        report = _scan_skill_package(skill_dir)
        if not report:
            return None
        trust, record = trust_from_scan_report(report)
        h = compute_skill_md_sha256(skill_dir)
        if h is not None:
            record["skill_md_sha256"] = h
        with _SKILL_TRUST_LOCK:
            _SKILL_TRUST_CACHE[skill_name] = record
        _persist_cache()
        return trust


def trust_for_path_with_skills_root(
    path: str,
    skills_root: str,
    registry_trust_for_path,
) -> SecurityLevel:
    """If *path* is under *skills_root*, prefer skill-scanner trust; else *registry_trust_for_path(path)*."""
    if not is_local_registry_path(path):
        return registry_trust_for_path(path)
    name = skill_name_under_root(path, skills_root)
    if not name:
        return registry_trust_for_path(path)
    skill_dir = os.path.join(skills_root, name)
    t = resolve_trust_for_skill(name, skill_dir)
    if t is not None:
        return t
    return registry_trust_for_path(path)
