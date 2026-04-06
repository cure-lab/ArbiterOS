"""
Resolve which tool parser set to use (OpenClaw vs nanobot) from litellm_config.yaml.

Override with environment variable ``ARBITEROS_TOOL_AGENT=openclaw|nanobot`` (highest priority).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_AGENT = "openclaw"
_VALID = frozenset({"openclaw", "nanobot"})

_config_path: Optional[Path] = None
_cached_mtime: Optional[float] = None
_cached_value: Optional[str] = None


def _litellm_config_path() -> Path:
    env = os.environ.get("ARBITEROS_LITELLM_CONFIG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # arbiteros_kernel/instruction_parsing/tool_agent_config.py -> parents[2] = ArbiterOS-Kernel
    return Path(__file__).resolve().parents[2] / "litellm_config.yaml"


def _read_tool_agent_from_yaml(path: Path) -> Optional[str]:
    if not path.is_file():
        logger.debug("litellm config not found at %s", path)
        return None
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None
    if not isinstance(raw, dict):
        return None
    block = raw.get("arbiteros_config")
    if isinstance(block, dict):
        v = block.get("tool_agent")
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return None


def get_tool_agent() -> str:
    """Return ``openclaw`` or ``nanobot`` (default ``openclaw``)."""
    global _cached_mtime, _cached_value, _config_path

    env = os.environ.get("ARBITEROS_TOOL_AGENT", "").strip().lower()
    if env in _VALID:
        return env

    path = _litellm_config_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None

    if _cached_value is not None and path == _config_path and mtime == _cached_mtime:
        return _cached_value

    _config_path = path
    _cached_mtime = mtime

    yaml_val = _read_tool_agent_from_yaml(path)
    if yaml_val in _VALID:
        _cached_value = yaml_val
        return yaml_val

    if yaml_val:
        logger.warning(
            "Invalid arbiteros_config.tool_agent %r in %s; using %s",
            yaml_val,
            path,
            _DEFAULT_AGENT,
        )

    _cached_value = _DEFAULT_AGENT
    return _DEFAULT_AGENT


def invalidate_tool_agent_cache() -> None:
    """Testing / hot-reload hook."""
    global _cached_mtime, _cached_value, _config_path
    _cached_mtime = None
    _cached_value = None
    _config_path = None
