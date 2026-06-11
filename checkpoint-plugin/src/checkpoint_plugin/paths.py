"""Path helpers for checkpoint plugin storage."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from checkpoint_plugin._utils import expand_and_resolve

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "save_frequency": "every_turn",
    "exclude_patterns": [
        "node_modules/**",
        ".git/**",
        "**/__pycache__/**",
        "**/.env*",
        "**/*credential*",
    ],
    "max_file_size_mb": 10,
    "retention": {"keep_last": 100, "keep_daily": 7, "keep_weekly": 4},
    "auto_backup_on_resume": True,
    "ignore_plugin_hook_diffs": True,
}


def plugin_home(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return expand_and_resolve(explicit)
    env_value = os.environ.get("CHECKPOINT_PLUGIN_HOME")
    if env_value:
        return expand_and_resolve(env_value)
    return Path.home() / ".checkpoint-plugin"


def sessions_dir(home: Path | None = None) -> Path:
    return plugin_home(home) / "sessions"


def blobs_dir(home: Path | None = None) -> Path:
    return plugin_home(home) / "blobs"


def session_dir(session_id: str, home: Path | None = None) -> Path:
    return sessions_dir(home) / session_id


def backups_dir(home: Path | None = None) -> Path:
    return plugin_home(home) / "backups"


def config_path(home: Path | None = None) -> Path:
    return plugin_home(home) / "config.json"


def ensure_home(home: Path | None = None) -> Path:
    root = plugin_home(home)
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "blobs").mkdir(parents=True, exist_ok=True)
    (root / "backups").mkdir(parents=True, exist_ok=True)
    path = root / "config.json"
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return root


def load_config(home: Path | None = None) -> dict[str, Any]:
    root = ensure_home(home)
    path = root / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
    merged = dict(DEFAULT_CONFIG)
    merged.update(data)
    return merged


def write_config(config: dict[str, Any], home: Path | None = None) -> None:
    root = ensure_home(home)
    (root / "config.json").write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
