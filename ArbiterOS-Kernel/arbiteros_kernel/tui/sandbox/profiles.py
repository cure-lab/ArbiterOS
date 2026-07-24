"""Named sandbox profiles under ~/.arbiteros/sandbox_profiles/{codex,claude}/."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from arbiteros_kernel.tui.sandbox.config_io import SandboxSettings

AgentName = Literal["codex", "claude"]

PROFILES_ROOT = Path.home() / ".arbiteros" / "sandbox_profiles"
# Backward-compatible alias (tests monkeypatch this for Codex).
PROFILES_DIR = PROFILES_ROOT / "codex"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def normalize_agent(agent: str) -> AgentName:
    raw = (agent or "").strip().lower()
    if raw in {"cc", "claude", "claude_code", "claude-code"}:
        return "claude"
    if raw in {"codex"}:
        return "codex"
    raise ValueError(f"Unknown agent '{agent}'. Use codex or cc/claude.")


def profiles_dir(agent: str = "codex") -> Path:
    name = normalize_agent(agent)
    # Codex: honor PROFILES_DIR so existing tests can monkeypatch it.
    # Claude: always under PROFILES_ROOT / "claude".
    d = PROFILES_DIR if name == "codex" else (PROFILES_ROOT / "claude")
    d.mkdir(parents=True, exist_ok=True)
    return d


def validate_profile_name(name: str) -> str:
    raw = (name or "").strip()
    if not _NAME_RE.match(raw):
        raise ValueError(
            "Name must be 1–64 chars: letters/digits/._- and start with alnum."
        )
    return raw


def profile_path(name: str, agent: str = "codex") -> Path:
    return profiles_dir(agent) / f"{validate_profile_name(name)}.json"


def list_profiles(agent: str = "codex") -> list[str]:
    d = profiles_dir(agent)
    return sorted(p.stem for p in d.glob("*.json") if p.is_file())


def settings_from_dict(data: dict[str, Any]) -> SandboxSettings:
    return SandboxSettings(
        sandbox_mode=str(data.get("sandbox_mode") or "read-only"),
        approval_policy=str(data.get("approval_policy") or "on-request"),
        network_access=bool(data.get("network_access", False)),
        writable_roots=[str(x) for x in (data.get("writable_roots") or [])],
        exclude_tmpdir_env_var=bool(data.get("exclude_tmpdir_env_var", False)),
        exclude_slash_tmp=bool(data.get("exclude_slash_tmp", False)),
        deny_globs=[str(x) for x in (data.get("deny_globs") or [])],
        deny_paths=[str(x) for x in (data.get("deny_paths") or [])],
        network_policy=data.get("network_policy") or "off",  # type: ignore[arg-type]
        allowed_domains=[str(x) for x in (data.get("allowed_domains") or [])],
        denied_domains=[str(x) for x in (data.get("denied_domains") or [])],
    )


def load_profile(name: str, agent: str = "codex") -> SandboxSettings:
    path = profile_path(name, agent)
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {name}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid profile file: {path}")
    settings = raw.get("settings")
    if not isinstance(settings, dict):
        raise ValueError(f"Profile missing settings: {path}")
    return settings_from_dict(settings)


def save_profile(name: str, settings: SandboxSettings, agent: str = "codex") -> Path:
    agent_n = normalize_agent(agent)
    path = profile_path(name, agent_n)
    payload = {
        "name": validate_profile_name(name),
        "agent": agent_n,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "settings": asdict(settings),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def delete_profile(name: str, agent: str = "codex") -> bool:
    path = profile_path(name, agent)
    if not path.exists():
        return False
    path.unlink()
    return True


def profile_exists(name: str, agent: str = "codex") -> bool:
    try:
        return profile_path(name, agent).exists()
    except ValueError:
        return False
