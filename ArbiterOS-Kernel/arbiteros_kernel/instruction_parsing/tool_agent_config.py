"""
Resolve which tool parser set to use (openclaw | nanobot | hermes) from litellm_config.yaml.
"""

from __future__ import annotations

import logging
from pathlib import Path
import yaml

logger = logging.getLogger(__name__)

_VALID = frozenset({"openclaw", "nanobot", "hermes"})
_DEFAULT_AGENT = "openclaw"


def _load() -> str:
    env_path = __import__("os").environ.get("ARBITEROS_LITELLM_CONFIG", "").strip()
    path = Path(env_path).expanduser().resolve() if env_path else Path(__file__).resolve().parents[2] / "litellm_config.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        block = raw.get("arbiteros_config") if isinstance(raw, dict) else None
        v = block.get("tool_agent", "").strip().lower() if isinstance(block, dict) else ""
        if v in _VALID:
            return v
        if v:
            logger.warning("Invalid arbiteros_config.tool_agent %r in %s; using %s", v, path, _DEFAULT_AGENT)
    except Exception as e:
        logger.debug("Could not read tool_agent from %s: %s", path, e)
    return _DEFAULT_AGENT


_TOOL_AGENT: str = _load()


def get_tool_agent() -> str:
    return _TOOL_AGENT
