#!/usr/bin/env python3
"""Validate example.yaml against tool_parsers.schema.json."""

import json
import sys
from pathlib import Path

try:
    import yaml
    from jsonschema import Draft202012Validator
    from referencing import Registry
    from referencing.jsonschema import DRAFT202012
except ImportError as e:
    print(f"ERROR: missing dependency — {e}\nRun: uv add jsonschema referencing pyyaml")
    sys.exit(1)

DSL_DIR = Path(__file__).parent
TOOL_PARSERS_SCHEMA = DSL_DIR / "tool_parsers.schema.json"
RESULT_SCHEMA = DSL_DIR / "instruction_result.schema.json"
EXAMPLE = DSL_DIR / "example.yaml"


def build_registry(tool_schema: dict, result_schema: dict) -> Registry:
    tool_uri = TOOL_PARSERS_SCHEMA.resolve().as_uri()
    result_uri = RESULT_SCHEMA.resolve().as_uri()
    return Registry().with_resources([
        (tool_uri, DRAFT202012.create_resource({**tool_schema, "$id": tool_uri})),
        (result_uri, DRAFT202012.create_resource({**result_schema, "$id": result_uri})),
    ])


def main() -> None:
    print("=== ArbiterOS DSL Example Validator ===\n")

    with TOOL_PARSERS_SCHEMA.open() as f:
        tool_schema = json.load(f)
    with RESULT_SCHEMA.open() as f:
        result_schema = json.load(f)
    with EXAMPLE.open() as f:
        example = yaml.safe_load(f)

    tool_uri = TOOL_PARSERS_SCHEMA.resolve().as_uri()
    registry = build_registry(tool_schema, result_schema)
    schema_abs = {**tool_schema, "$id": tool_uri}

    validator = Draft202012Validator(schema_abs, registry=registry)
    errors = sorted(validator.iter_errors(example), key=lambda e: list(e.absolute_path))

    if not errors:
        tools = [entry["tool"] for entry in example]
        print(f"OK    example.yaml is valid ({len(example)} tool definitions: {tools})")
    else:
        print(f"FAIL  example.yaml has {len(errors)} validation error(s):\n")
        for i, err in enumerate(errors, 1):
            path = " → ".join(str(p) for p in err.absolute_path) or "(root)"
            print(f"  {i}. [{path}] {err.message}")
        sys.exit(1)


if __name__ == "__main__":
    main()
