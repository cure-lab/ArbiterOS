"""Built-in pre-call policy registry."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from arbiteros_kernel.precall_policy.cost_doctor_policy import CostDoctorPreCallPolicy
from arbiteros_kernel.precall_policy.cost_doctor_runtime import cost_down_enabled
from arbiteros_kernel.precall_policy.policy import PreCallPolicy

PRECALL_POLICY_CLASS_MAP: dict[str, type[PreCallPolicy]] = {
    "CostDoctorPreCallPolicy": CostDoctorPreCallPolicy,
}


@dataclass(frozen=True)
class PreCallPolicyEntry:
    policy: type[PreCallPolicy]
    description: str
    enabled: bool = True

    @property
    def name(self) -> str:
        return self.policy.__name__


def _registry_config_path() -> Path:
    env_path = os.getenv("ARBITEROS_PRECALL_POLICY_REGISTRY", "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / "precall_policy_registry.json").resolve()


def _default_registry_data() -> list[dict[str, object]]:
    return [
        {
            "name": "CostDoctorPreCallPolicy",
            "enabled": True,
            "description": (
                "Cost Doctor precall: cumulative ratio context optimization before upstream LLM dispatch."
            ),
        }
    ]


_registry_lock = threading.Lock()
_registry_cache: list[PreCallPolicyEntry] | None = None
_registry_mtime: float | None = None


def _load_registry_data_from_file() -> list[dict[str, object]]:
    path = _registry_config_path()
    if not path.exists():
        return _default_registry_data()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _default_registry_data()
    if not isinstance(raw, list):
        return _default_registry_data()
    return raw


def get_precall_policy_registry(*, force_reload: bool = False) -> list[PreCallPolicyEntry]:
    global _registry_cache, _registry_mtime
    path = _registry_config_path()
    mtime = path.stat().st_mtime if path.exists() else None
    with _registry_lock:
        if (
            not force_reload
            and _registry_cache is not None
            and _registry_mtime == mtime
        ):
            return list(_registry_cache)

        entries: list[PreCallPolicyEntry] = []
        for row in _load_registry_data_from_file():
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            policy_cls = PRECALL_POLICY_CLASS_MAP.get(name)
            if policy_cls is None:
                continue
            entries.append(
                PreCallPolicyEntry(
                    policy=policy_cls,
                    description=str(row.get("description") or ""),
                    enabled=bool(row.get("enabled", True)),
                )
            )
        _registry_cache = entries
        _registry_mtime = mtime
        return list(entries)


def get_precall_policy_classes(*, tool_agent: Optional[str] = None) -> list[type[PreCallPolicy]]:
    """Return enabled pre-call policy classes for the active tool agent."""
    _ = tool_agent
    if not cost_down_enabled():
        return []
    return [
        entry.policy
        for entry in get_precall_policy_registry()
        if entry.enabled
    ]
