"""Named Codex sandbox profiles under ~/.arbiteros/sandbox_profiles/codex/."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from arbiteros_kernel.tui.sandbox.config_io import SandboxSettings

PROFILES_DIR = Path.home() / ".arbiteros" / "sandbox_profiles" / "codex"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def profiles_dir() -> Path:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILES_DIR


def validate_profile_name(name: str) -> str:
    raw = (name or "").strip()
    if not _NAME_RE.match(raw):
        raise ValueError(
            "Name must be 1–64 chars: letters/digits/._- and start with alnum."
        )
    return raw


def profile_path(name: str) -> Path:
    return profiles_dir() / f"{validate_profile_name(name)}.json"


def list_profiles() -> list[str]:
    d = profiles_dir()
    names = sorted(p.stem for p in d.glob("*.json") if p.is_file())
    return names


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


def load_profile(name: str) -> SandboxSettings:
    path = profile_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {name}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid profile file: {path}")
    settings = raw.get("settings")
    if not isinstance(settings, dict):
        raise ValueError(f"Profile missing settings: {path}")
    return settings_from_dict(settings)


def save_profile(name: str, settings: SandboxSettings) -> Path:
    path = profile_path(name)
    payload = {
        "name": validate_profile_name(name),
        "agent": "codex",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "settings": asdict(settings),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def delete_profile(name: str) -> bool:
    path = profile_path(name)
    if not path.exists():
        return False
    path.unlink()
    return True


def profile_exists(name: str) -> bool:
    try:
        return profile_path(name).exists()
    except ValueError:
        return False
