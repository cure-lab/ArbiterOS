from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

_ROLE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ROLE_POLICY_DIR = Path(__file__).resolve().parent / "role_policy_cfg"


def _default_policy_path() -> str:
    return str((Path(__file__).resolve().parent / "policy.json").resolve())


def _normalize_role_name(role_name: Optional[str]) -> Optional[str]:
    if not isinstance(role_name, str):
        return None
    normalized = role_name.strip()
    if not normalized:
        return None
    if not _ROLE_NAME_RE.fullmatch(normalized):
        return None
    return normalized


def _is_env_policy_source_active() -> bool:
    inline = os.getenv("ARBITEROS_POLICY_CONFIG_JSON", "").strip()
    path = os.getenv("ARBITEROS_POLICY_CONFIG", "").strip()
    return bool(inline or path)


def load_role_policy_config(
    role_name: Optional[str],
) -> tuple[Optional[dict[str, Any]], str, Optional[str]]:
    """
    Resolve request-scoped role policy config.

    Returns:
      - config dict when role config is available and valid; otherwise None.
      - source string for observability.
      - fallback reason when config is None.
    """
    if _is_env_policy_source_active():
        return None, "env_policy_config", "env_policy_config_present"

    normalized_role = _normalize_role_name(role_name)
    if normalized_role is None:
        reason = "role_name_missing" if not role_name else "invalid_role_name"
        return None, _default_policy_path(), reason

    role_file = _ROLE_POLICY_DIR / f"{normalized_role}_policy.json"
    if not role_file.exists():
        return None, _default_policy_path(), f"role_policy_file_not_found:{role_file.name}"

    try:
        parsed = json.loads(role_file.read_text(encoding="utf-8"))
    except Exception:
        return None, _default_policy_path(), f"role_policy_file_parse_error:{role_file.name}"

    if not isinstance(parsed, dict):
        return (
            None,
            _default_policy_path(),
            f"role_policy_file_invalid_root:{role_file.name}",
        )

    return dict(parsed), str(role_file.resolve()), None
