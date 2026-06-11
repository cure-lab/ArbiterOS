"""Shared utility functions used across checkpoint plugin modules."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def expand_and_resolve(path: Path | str) -> Path:
    """Expand user home and resolve path to absolute form."""
    return Path(path).expanduser().resolve()


def non_empty_str(value: Any) -> str | None:
    """Return value as string if it's a non-empty string, otherwise None."""
    return value if isinstance(value, str) and value else None


def clean_string_dict(d: dict[Any, Any] | None) -> dict[str, str]:
    """Convert dict to clean string-to-string dict, filtering empty values."""
    if not d:
        return {}
    return {str(k): str(v) for k, v in d.items() if v}


def load_json_safe(path: Path, default: Any = None) -> Any:
    """Load JSON from path, returning default on error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def load_json_value(data: dict[str, Any], key: str, default: Any = None) -> Any:
    """Get JSON value from dict, handling missing keys."""
    value = data.get(key, default)
    return value if value is not None else default


def read_metadata_json(path: Path) -> dict[str, Any]:
    """Read metadata.json file, returning empty dict on error."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def backup_file(path: Path, backup_path: Path, backed_up: list[str]) -> None:
    """Back up a file to backup_path and record it in backed_up list."""
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)
    backed_up.append(str(backup_path))


def is_sha_ref(value: object) -> bool:
    """Return True when value is a canonical SHA256 blob reference."""
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def extract_sha_refs(value: object) -> set[str]:
    """Recursively extract SHA256 references from nested JSON structures.

    Scans strings, dicts, and lists for 64-character hex strings matching
    the SHA256 format [0-9a-f]{64}.
    """
    refs: set[str] = set()
    if isinstance(value, str):
        if is_sha_ref(value):
            refs.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            refs.update(extract_sha_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(extract_sha_refs(item))
    return refs
