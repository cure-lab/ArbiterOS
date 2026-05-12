"""
DSL schema validator — validates YAML tool-parser definitions against
tool_parsers.schema.json before the engine builds parsers from them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

_DSL_DIR = Path(__file__).parent
_SCHEMA_PATH = _DSL_DIR / "tool_parsers.schema.json"

_validator: Draft202012Validator | None = None


def _build_validator() -> Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text())
    return Draft202012Validator(schema)


def validate(defs: Any, source_name: str = "<unknown>") -> None:
    """Validate a parsed YAML document against tool_parsers.schema.json.

    Raises ValueError with a human-readable message on the first 10 errors.
    """
    global _validator
    if _validator is None:
        _validator = _build_validator()
    errors = list(_validator.iter_errors(defs))
    if errors:
        messages = "\n".join(
            f"  [{e.json_path}] {e.message}" for e in errors[:10]
        )
        raise ValueError(
            f"Schema validation failed for {source_name}:\n{messages}"
        )
