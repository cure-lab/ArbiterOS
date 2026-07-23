"""
Resolve which tool parser set to use from the current request's agent_name.

Agent selection priority:
1. Request context (set in litellm pre_call_hook)
2. ``metadata.arbiteros_agent_name`` on the active request
3. ``route_model;agent_name;role`` suffix on request model
4. Env ``ARBITEROS_TOOL_AGENT`` (tests / local overrides)
5. Default: openclaw
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from arbiteros_kernel.agent_registry import (
    VALID_AGENT_NAMES,
    agent_name_from_request_data,
    get_request_agent,
)

logger = logging.getLogger(__name__)

_DEFAULT_AGENT = "openclaw"


def _env_fallback_agent() -> Optional[str]:
    value = os.environ.get("ARBITEROS_TOOL_AGENT", "").strip().lower()
    if value in VALID_AGENT_NAMES:
        return value
    if value:
        logger.warning(
            "Invalid ARBITEROS_TOOL_AGENT %r; using %s", value, _DEFAULT_AGENT
        )
    return None


def get_tool_agent(request_data: object | None = None) -> str:
    agent = agent_name_from_request_data(request_data)
    if isinstance(agent, str) and agent in VALID_AGENT_NAMES:
        return agent
    context_agent = get_request_agent()
    if isinstance(context_agent, str) and context_agent in VALID_AGENT_NAMES:
        return context_agent
    env_agent = _env_fallback_agent()
    if env_agent:
        return env_agent
    return _DEFAULT_AGENT
