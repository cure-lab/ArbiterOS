"""Co-launch checkpoint-plugin alongside ArbiterOS (no kernel trace integration).

When enabled, ArbiterOS turns on checkpoint recording for agent hooks while the
proxy is running, and turns it off on shutdown. Recover remains ``checkpoint resume``.
"""

from __future__ import annotations

import atexit
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_TOOL_AGENT_TO_HOOK_PROVIDER: dict[str, str] = {
    "codex": "codex",
    "claude_code": "claude",
    "claude": "claude",
    "opencode": "opencode",
}

_SHUTDOWN_REGISTERED = False
_STARTED = False
_ACTIVE_HOME: Optional[Path] = None


@dataclass(frozen=True)
class CheckpointSettings:
    enabled: bool
    home: Optional[Path]
    auto_install_hooks: bool
    providers: tuple[str, ...]


def _litellm_config_yaml_path() -> Path:
    return Path(__file__).resolve().parent.parent / "litellm_config.yaml"


def _parse_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _env_enabled_override() -> Optional[bool]:
    raw = os.environ.get("ARBITEROS_CHECKPOINT_ENABLED")
    if raw is None:
        return None
    return _parse_bool(raw, default=False)


def _expand_home(value: Any) -> Optional[Path]:
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value.strip()).expanduser().resolve()


def _read_tool_agent(cfg: dict[str, Any]) -> Optional[str]:
    arb_cfg = cfg.get("arbiteros_config")
    if not isinstance(arb_cfg, dict):
        return None
    raw = arb_cfg.get("tool_agent")
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().lower()
    return normalized or None


def _resolve_providers(
    checkpoint_cfg: dict[str, Any], tool_agent: Optional[str]
) -> tuple[str, ...]:
    raw = checkpoint_cfg.get("providers")
    if raw is None or raw == "auto":
        if not tool_agent:
            return ()
        provider = _TOOL_AGENT_TO_HOOK_PROVIDER.get(tool_agent)
        return (provider,) if provider else ()
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"", "auto"}:
            if not tool_agent:
                return ()
            provider = _TOOL_AGENT_TO_HOOK_PROVIDER.get(tool_agent)
            return (provider,) if provider else ()
        return (normalized,)
    if isinstance(raw, list):
        providers: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            normalized = item.strip().lower()
            if normalized:
                providers.append(normalized)
        return tuple(providers)
    return ()


def load_checkpoint_settings() -> CheckpointSettings:
    env_override = _env_enabled_override()
    cfg: dict[str, Any] = {}
    if yaml is not None:
        path = _litellm_config_yaml_path()
        if path.exists():
            try:
                parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    cfg = parsed
            except Exception:
                logger.warning("Failed to read %s for checkpoint settings", path)

    arb_cfg = cfg.get("arbiteros_config")
    checkpoint_cfg = (
        arb_cfg.get("checkpoint") if isinstance(arb_cfg, dict) else None
    )
    if not isinstance(checkpoint_cfg, dict):
        checkpoint_cfg = {}

    enabled = _parse_bool(checkpoint_cfg.get("enabled"), default=True)
    if env_override is not None:
        enabled = env_override

    home = _expand_home(checkpoint_cfg.get("home"))
    env_home = os.environ.get("CHECKPOINT_PLUGIN_HOME", "").strip()
    if env_home:
        home = Path(env_home).expanduser().resolve()

    auto_install_hooks = _parse_bool(
        checkpoint_cfg.get("auto_install_hooks"), default=True
    )
    tool_agent = _read_tool_agent(cfg)
    providers = _resolve_providers(checkpoint_cfg, tool_agent)
    return CheckpointSettings(
        enabled=enabled,
        home=home,
        auto_install_hooks=auto_install_hooks,
        providers=providers,
    )


def set_checkpoint_recording_enabled(
    enabled: bool, *, home: Optional[Path] = None
) -> None:
    from checkpoint_plugin.paths import ensure_home, load_config, write_config

    root = ensure_home(home)
    config = load_config(home)
    config["enabled"] = bool(enabled)
    write_config(config, home)
    logger.info(
        "checkpoint recording %s (home=%s)",
        "enabled" if enabled else "disabled",
        root,
    )


def _install_hooks(providers: tuple[str, ...], *, home: Optional[Path]) -> None:
    from checkpoint_plugin.integrations.hook_installer import install_hooks

    if home is not None:
        os.environ["CHECKPOINT_PLUGIN_HOME"] = str(home)

    for provider in providers:
        try:
            results = install_hooks(provider)
        except ValueError as exc:
            logger.warning("checkpoint hook install skipped for %s: %s", provider, exc)
            continue
        for result in results:
            state = "updated" if result.changed else "already present"
            logger.info(
                "checkpoint hooks for %s %s at %s",
                result.provider,
                state,
                result.path,
            )


def start_checkpoint_lifecycle() -> None:
    global _STARTED, _ACTIVE_HOME
    if _STARTED:
        return

    settings = load_checkpoint_settings()
    _ACTIVE_HOME = settings.home
    if settings.home is not None:
        os.environ["CHECKPOINT_PLUGIN_HOME"] = str(settings.home)

    if not settings.enabled:
        set_checkpoint_recording_enabled(False, home=settings.home)
        logger.info("checkpoint co-launch disabled via config")
        _register_shutdown_handlers()
        _STARTED = True
        return

    if not settings.providers:
        set_checkpoint_recording_enabled(False, home=settings.home)
        logger.warning(
            "checkpoint enabled but no hook providers resolved for tool_agent; "
            "recording stays off until providers are configured"
        )
        _register_shutdown_handlers()
        _STARTED = True
        return

    set_checkpoint_recording_enabled(True, home=settings.home)
    if settings.auto_install_hooks:
        _install_hooks(settings.providers, home=settings.home)
        logger.info(
            "checkpoint hooks installed for %s; restart Codex/Claude if this is the first install",
            ", ".join(settings.providers),
        )

    _register_shutdown_handlers()
    _STARTED = True


def stop_checkpoint_lifecycle() -> None:
    if not _STARTED:
        return
    set_checkpoint_recording_enabled(False, home=_ACTIVE_HOME)


def _register_shutdown_handlers() -> None:
    global _SHUTDOWN_REGISTERED
    if _SHUTDOWN_REGISTERED:
        return
    _SHUTDOWN_REGISTERED = True
    atexit.register(stop_checkpoint_lifecycle)


def main(argv: list[str] | None = None) -> int:
    """CLI helper: ``python -m arbiteros_kernel.checkpoint_lifecycle start|stop|status``."""
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        print(
            "Usage: python -m arbiteros_kernel.checkpoint_lifecycle "
            "{start|stop|status}"
        )
        return 0

    command = args[0].strip().lower()
    settings = load_checkpoint_settings()
    if command == "start":
        start_checkpoint_lifecycle()
        return 0
    if command == "stop":
        stop_checkpoint_lifecycle()
        return 0
    if command == "status":
        from checkpoint_plugin.paths import load_config

        enabled = bool(load_config(settings.home).get("enabled", True))
        print(f"arbiteros_config.enabled={settings.enabled}")
        print(f"providers={','.join(settings.providers) or '(none)'}")
        print(f"checkpoint.config.enabled={enabled}")
        print(f"home={settings.home or Path.home() / '.checkpoint-plugin'}")
        return 0

    print(f"Unknown command: {command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
