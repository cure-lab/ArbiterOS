from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def kernel_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def load_models() -> list[str]:
    cfg_path = kernel_root() / "litellm_config.yaml"
    if not cfg_path.exists():
        return []
    try:
        parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(parsed, dict):
        return []
    model_list = parsed.get("model_list")
    if not isinstance(model_list, list):
        return []
    names: list[str] = []
    for item in model_list:
        if isinstance(item, dict):
            name = item.get("model_name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return names


def load_agents() -> list[str]:
    agents_dir = kernel_root() / "agents"
    if not agents_dir.is_dir():
        return []
    return sorted(p.stem for p in agents_dir.glob("*.yaml"))


def registration_hints() -> dict[str, str]:
    return {
        "models": "Edit litellm_config.yaml → model_list",
        "agents": "Add agents/<name>.yaml and the matching tool parser DSL",
        "docs": "assets/docs/multi_agent_routing.md",
    }
