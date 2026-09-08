"""Per-agent profiles and request-scoped agent resolution."""

from __future__ import annotations

import copy
import logging
import os
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from arbiteros_kernel.policy_check import split_model_agent_role

logger = logging.getLogger(__name__)

VALID_AGENT_NAMES = frozenset(
    {"openclaw", "nanobot", "hermes", "codex", "claude_code", "pi"}
)
_DEFAULT_AGENT_NAME = "openclaw"

_request_agent_var: ContextVar[Optional[str]] = ContextVar(
    "arbiteros_request_agent", default=None
)

_registry_lock = threading.Lock()
_agent_profiles_cache_key: Optional[str] = None
_agent_profiles_cache: dict[str, "AgentProfile"] = {}


@dataclass(frozen=True)
class AgentProfile:
    agent_name: str
    depends_on_sidecar_enabled: bool = False
    upstream_compat_enabled: bool = False
    upstream_compat_rules: tuple[dict[str, Any], ...] = field(default_factory=tuple)


def agents_dir() -> Path:
    env_path = os.environ.get("ARBITEROS_AGENTS_DIR", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "agents"


def split_request_model(model_value: Any) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse ``route_model;agent_name;role`` from the request model field."""
    return split_model_agent_role(model_value)


def set_request_agent(agent_name: Optional[str]) -> None:
    normalized = (agent_name or "").strip().lower() or None
    _request_agent_var.set(normalized)


def get_request_agent() -> Optional[str]:
    value = _request_agent_var.get()
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def agent_name_from_request_data(request_data: Any) -> Optional[str]:
    if not isinstance(request_data, dict):
        return get_request_agent()

    metadata = request_data.get("metadata")
    if isinstance(metadata, dict):
        explicit = metadata.get("arbiteros_agent_name")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip().lower()

    model = request_data.get("model")
    if isinstance(model, str) and model.strip():
        _, agent_name, _ = split_model_agent_role(model)
        if isinstance(agent_name, str) and agent_name.strip():
            return agent_name.strip().lower()

    return get_request_agent()


def _normalize_agent_profile(raw: Any, *, source: Path) -> Optional[AgentProfile]:
    if not isinstance(raw, dict):
        return None
    agent_name = raw.get("agent_name")
    if not isinstance(agent_name, str) or not agent_name.strip():
        logger.warning("Skipping agent profile without agent_name: %s", source)
        return None
    normalized_name = agent_name.strip().lower()
    if normalized_name not in VALID_AGENT_NAMES:
        logger.warning(
            "Skipping unknown agent_name %r in %s", normalized_name, source
        )
        return None

    sidecar_block = raw.get("depends_on_sidecar")
    sidecar_enabled = (
        isinstance(sidecar_block, dict) and sidecar_block.get("enabled") is True
    )

    compat_block = raw.get("upstream_compat")
    compat_enabled = False
    compat_rules: list[dict[str, Any]] = []
    if isinstance(compat_block, dict):
        compat_enabled = compat_block.get("enabled") is True
        rules = compat_block.get("rules")
        if isinstance(rules, list):
            compat_rules = [rule for rule in rules if isinstance(rule, dict)]

    return AgentProfile(
        agent_name=normalized_name,
        depends_on_sidecar_enabled=sidecar_enabled,
        upstream_compat_enabled=compat_enabled,
        upstream_compat_rules=tuple(compat_rules),
    )


def _load_agent_profiles_unlocked() -> dict[str, AgentProfile]:
    profiles: dict[str, AgentProfile] = {}
    root = agents_dir()
    if not root.is_dir():
        logger.warning("Agents directory does not exist: %s", root)
        return profiles

    yaml_paths = sorted(root.glob("*.yaml")) + sorted(root.glob("*.yml"))
    for path in yaml_paths:
        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read agent profile %s: %s", path, exc)
            continue
        profile = _normalize_agent_profile(parsed, source=path)
        if profile is None:
            continue
        profiles[profile.agent_name] = profile
    return profiles


def load_agent_profiles(*, force_reload: bool = False) -> dict[str, AgentProfile]:
    root = agents_dir()
    cache_key = str(root)
    try:
        if root.is_dir():
            mtimes = [
                str(path.stat().st_mtime_ns)
                for path in sorted(root.glob("*.yaml")) + sorted(root.glob("*.yml"))
            ]
            cache_key = f"{root}|{'|'.join(mtimes)}"
    except Exception:
        pass

    global _agent_profiles_cache_key, _agent_profiles_cache
    with _registry_lock:
        if not force_reload and cache_key == _agent_profiles_cache_key:
            return dict(_agent_profiles_cache)
        loaded = _load_agent_profiles_unlocked()
        _agent_profiles_cache_key = cache_key
        _agent_profiles_cache = loaded
        return dict(loaded)


def resolve_agent_profile(agent_name: Optional[str]) -> AgentProfile:
    normalized = (agent_name or "").strip().lower()
    profiles = load_agent_profiles()
    if normalized in profiles:
        return profiles[normalized]
    if normalized and normalized not in VALID_AGENT_NAMES:
        logger.warning("Unknown agent_name %r; using default profile", normalized)
    fallback_name = _DEFAULT_AGENT_NAME
    if fallback_name in profiles:
        return profiles[fallback_name]
    return AgentProfile(agent_name=fallback_name or _DEFAULT_AGENT_NAME)


def validate_request_route(model_value: Any) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Validate ``route_model;agent_name;role``.

    Returns ``(error_message, route_model, agent_name, role_name)``.
    """
    if not isinstance(model_value, str) or not model_value.strip():
        return "model is required", None, None, None

    route_model, agent_name, role_name = split_model_agent_role(model_value)
    if not isinstance(route_model, str) or not route_model.strip():
        return "model route is required", None, None, None
    if not isinstance(agent_name, str) or not agent_name.strip():
        return (
            "model must use route_model;agent_name or route_model;agent_name;role "
            f"(got {model_value!r})",
            None,
            None,
            None,
        )

    normalized_agent = agent_name.strip().lower()
    if normalized_agent not in VALID_AGENT_NAMES:
        return (
            f"unknown agent_name {normalized_agent!r}; "
            f"expected one of {sorted(VALID_AGENT_NAMES)}",
            None,
            None,
            None,
        )

    profiles = load_agent_profiles()
    if normalized_agent not in profiles:
        return (
            f"agent profile for {normalized_agent!r} is not registered under {agents_dir()}",
            None,
            None,
            None,
        )

    normalized_role = role_name.strip() if isinstance(role_name, str) and role_name.strip() else None
    return None, route_model.strip(), normalized_agent, normalized_role


def _upstream_model_name_for_compat(model: str) -> str:
    m = (model or "").strip()
    if m.lower().startswith("openai/"):
        return m[7:].strip() or m
    return m


def _normalize_model_name_for_compat(raw_model: Any) -> str:
    if not isinstance(raw_model, str):
        return ""
    route_model, _, _ = split_model_agent_role(raw_model)
    model_name = route_model if isinstance(route_model, str) and route_model else raw_model
    return _upstream_model_name_for_compat(model_name.strip())


def _model_matches_compat_rule(rule_value: Any, normalized_model: str) -> bool:
    if not normalized_model:
        return False
    if isinstance(rule_value, str):
        return _normalize_model_name_for_compat(rule_value) == normalized_model
    if isinstance(rule_value, list):
        for item in rule_value:
            if isinstance(item, str) and _normalize_model_name_for_compat(item) == normalized_model:
                return True
    return False


def resolve_upstream_compat_flags(
    model: Any,
    *,
    agent_name: Optional[str] = None,
) -> dict[str, bool]:
    defaults = {
        "strip_metadata": False,
        "force_non_stream": False,
        "prefer_chat_completions": False,
    }
    normalized_model = _normalize_model_name_for_compat(model)
    if not normalized_model:
        return defaults

    profile = resolve_agent_profile(agent_name or get_request_agent())
    if not profile.upstream_compat_enabled:
        return defaults

    resolved = dict(defaults)
    for rule in profile.upstream_compat_rules:
        if not _model_matches_compat_rule(rule.get("match_model"), normalized_model):
            continue
        for key in resolved:
            if isinstance(rule.get(key), bool):
                resolved[key] = resolved[key] or bool(rule.get(key))
    return resolved


def read_depends_on_sidecar_enabled_for_agent(agent_name: Optional[str] = None) -> bool:
    profile = resolve_agent_profile(agent_name or get_request_agent())
    return profile.depends_on_sidecar_enabled


def copy_global_response_format(cfg: dict[str, Any]) -> Optional[dict[str, Any]]:
    response_format = cfg.get("response_format")
    if isinstance(response_format, dict):
        return copy.deepcopy(response_format)
    return None
