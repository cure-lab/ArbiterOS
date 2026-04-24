#!/usr/bin/env python3
"""
Check whether the tool_parsers DSL schemas are well-formed and reasonable.

Runs structural, meta-schema, cross-reference, and logical consistency checks.
Exit code 0 = all passed, 1 = one or more failures.
"""

import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry
    from referencing.jsonschema import DRAFT202012
except ImportError as e:
    print(f"ERROR: missing dependency — {e}\nRun: uv add jsonschema referencing")
    sys.exit(1)

DSL_DIR = Path(__file__).parent
TOOL_PARSERS_SCHEMA = DSL_DIR / "tool_parsers.schema.json"
RESULT_SCHEMA = DSL_DIR / "instruction_result.schema.json"

PASS_TYPES = ["DefaultPass", "RegexPass", "NumericPass", "PathPass", "ShellPass"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load(path: Path) -> dict:
    try:
        with path.open() as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"FAIL  {path.name}: invalid JSON — {e}")
        sys.exit(1)


def build_registry(tool_schema: dict, result_schema: dict) -> Registry:
    """Register both schemas under their absolute file:// URIs so relative $refs resolve."""
    tool_uri = TOOL_PARSERS_SCHEMA.resolve().as_uri()
    result_uri = RESULT_SCHEMA.resolve().as_uri()
    tool_abs = {**tool_schema, "$id": tool_uri}
    result_abs = {**result_schema, "$id": result_uri}
    return Registry().with_resources([
        (tool_uri, DRAFT202012.create_resource(tool_abs)),
        (result_uri, DRAFT202012.create_resource(result_abs)),
    ])


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_meta_schema(schema: dict, name: str) -> bool:
    try:
        Draft202012Validator.check_schema(schema)
        print(f"OK    {name}: valid JSON Schema Draft 2020-12 document")
        return True
    except Exception as e:
        print(f"FAIL  {name}: meta-schema violation — {e}")
        return False


def check_cross_ref(tool_schema: dict, result_schema: dict) -> bool:
    """Verify that the cross-file $ref in tool_parsers resolves correctly."""
    registry = build_registry(tool_schema, result_schema)
    tool_uri = TOOL_PARSERS_SCHEMA.resolve().as_uri()
    resolver = registry.resolver(base_uri=tool_uri)
    ref = "instruction_result.schema.json#/$defs/Result"
    try:
        resolved = resolver.lookup(ref)
        _ = resolved.contents  # actually dereference
        print(f"OK    cross-ref resolved: {ref!r}")
        return True
    except Exception as e:
        print(f"FAIL  cross-ref unresolvable: {ref!r} — {e}")
        return False


def check_internal_refs(schema: dict, name: str) -> bool:
    """Verify every internal #/$defs/... $ref points to an existing definition."""
    defs = schema.get("$defs", {})
    refs: list[str] = []

    def collect(obj):
        if isinstance(obj, dict):
            if "$ref" in obj:
                refs.append(obj["$ref"])
            for v in obj.values():
                collect(v)
        elif isinstance(obj, list):
            for item in obj:
                collect(item)

    collect(schema)
    ok = True
    for ref in refs:
        if ref.startswith("#/$defs/"):
            def_name = ref.removeprefix("#/$defs/")
            if def_name not in defs:
                print(f"FAIL  {name}: $ref '#/$defs/{def_name}' has no matching definition")
                ok = False
    if ok:
        print(f"OK    {name}: all internal $refs resolve to existing $defs")
    return ok


def check_pass_discriminator(schema: dict) -> bool:
    """Ensure every Pass variant has a unique match_type const (discriminator)."""
    defs = schema.get("$defs", {})
    consts: list[str] = []
    for type_name in PASS_TYPES:
        defn = defs.get(type_name, {})
        const = defn.get("properties", {}).get("match_type", {}).get("const")
        if const is None:
            print(f"FAIL  {type_name}: 'match_type' has no 'const' — cannot discriminate")
            return False
        consts.append(const)

    if len(consts) == len(set(consts)):
        print(f"OK    Pass discriminator: {len(consts)} unique match_type values {consts}")
        return True
    duplicates = [c for c in set(consts) if consts.count(c) > 1]
    print(f"FAIL  Pass discriminator: duplicate match_type values: {duplicates}")
    return False


def check_warnings(schema: dict) -> None:
    """Report schema design notes (not failures)."""
    defs = schema.get("$defs", {})

    # Warn: passes array doesn't enforce a DefaultPass via schema constraints
    passes_schema = defs.get("ToolParserDef", {}).get("properties", {}).get("passes", {})
    if "contains" not in passes_schema:
        print("WARN  ToolParserDef.passes: schema description requires ≥1 DefaultPass,")
        print("      but no 'contains' constraint enforces it. Consider adding one.")

    # Warn: Result allows an empty object {}
    result_def = defs.get("Result", {})
    required = result_def.get("required", [])
    if not required:
        print("WARN  Result: both 'instruction_type' and 'metadata' are optional,")
        print("      so {} is a valid Result. Intentional for deep-merge semantics.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== ArbiterOS DSL Schema Checker ===\n")

    tool_schema = load(TOOL_PARSERS_SCHEMA)
    result_schema = load(RESULT_SCHEMA)
    print(f"OK    Loaded {TOOL_PARSERS_SCHEMA.name}")
    print(f"OK    Loaded {RESULT_SCHEMA.name}\n")

    results: list[bool] = [
        check_meta_schema(tool_schema, TOOL_PARSERS_SCHEMA.name),
        check_meta_schema(result_schema, RESULT_SCHEMA.name),
    ]
    print()
    results.append(check_cross_ref(tool_schema, result_schema))
    print()
    results.append(check_internal_refs(tool_schema, TOOL_PARSERS_SCHEMA.name))
    results.append(check_internal_refs(result_schema, RESULT_SCHEMA.name))
    print()
    results.append(check_pass_discriminator(tool_schema))
    print()
    check_warnings(tool_schema)

    print()
    if all(results):
        print(f"=== All {len(results)} checks PASSED ===")
    else:
        failed = results.count(False)
        print(f"=== {failed}/{len(results)} check(s) FAILED ===")
        sys.exit(1)


if __name__ == "__main__":
    main()
