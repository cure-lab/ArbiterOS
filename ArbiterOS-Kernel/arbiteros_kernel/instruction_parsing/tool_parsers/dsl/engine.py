"""
DSL engine — interprets YAML tool-parser definitions into ToolParser callables.

Usage:
    from .engine import load_registry
    registry = load_registry(Path("my_tools.yaml"))
    result = registry["read"](args, taint_status)

Pass execution order:
  1. DefaultPass  — unconditional baseline
  2. RegexPass    — per-case regex match on a dot-path arg
  3. NumericPass  — per-case numeric comparison on a dot-path arg
  4. PathPass     — classify a path/URL arg via the path registry
  5. ShellPass    — delegate to shell_parsers.analyze_command

Each matching pass deep-merges its Result into the accumulated output.

PathPass extensions (not in schema, engine-only):
  fields: [confidentiality, trustworthiness]  # limit which metadata fields to write
  register: true                              # call register_file_taint after classification

ShellPass extension:
  Emits security_type.custom["exec_parse"] with segments/operators/path_tokens/etc.
  for consumption by exec_composite_policy.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .validator import validate

from ...registries import (
    classify_confidentiality,
    classify_trustworthiness,
    register_file_taint,
)
from ...shell_parsers import CommandAnalysis, analyze_command
from ...types import (
    TaintStatus,
    ToolParser,
    ToolParseResult,
    make_security_type,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dot-path accessor
# ---------------------------------------------------------------------------

_SEGMENT_RE = re.compile(r"(\w+)\[(\d+)\]")


def _get_path(obj: Any, dotpath: str) -> Any:
    """Resolve a dot-notation path against a nested dict/list structure.

    Supports simple keys and integer list indices (e.g. 'request.fields[0].ref').
    Returns None if any segment is missing.
    """
    for segment in dotpath.split("."):
        if obj is None:
            return None
        m = _SEGMENT_RE.fullmatch(segment)
        if m:
            obj = obj.get(m.group(1)) if isinstance(obj, dict) else None
            if isinstance(obj, list):
                idx = int(m.group(2))
                obj = obj[idx] if idx < len(obj) else None
            else:
                return None
        else:
            obj = obj.get(segment) if isinstance(obj, dict) else None
    return obj


# ---------------------------------------------------------------------------
# Accumulated result (mutable during pass execution)
# ---------------------------------------------------------------------------

class _Acc:
    """Mutable accumulator for deep-merge of pass results."""

    def __init__(self) -> None:
        self.instruction_type: Optional[str] = None
        self.confidentiality: Optional[str] = None
        self.trustworthiness: Optional[str] = None
        self.risk: Optional[str] = None
        self.reversible: Optional[bool] = None
        self.custom: Dict[str, Any] = {}

    def merge(self, result: Dict[str, Any]) -> None:
        if "instruction_type" in result:
            self.instruction_type = result["instruction_type"]
        meta = result.get("metadata") or {}
        if "confidentiality" in meta:
            self.confidentiality = meta["confidentiality"]
        if "trustworthiness" in meta:
            self.trustworthiness = meta["trustworthiness"]
        if "risk" in meta:
            self.risk = meta["risk"]
        if "reversible" in meta:
            self.reversible = meta["reversible"]
        if "custom" in result:
            self.custom.update(result["custom"])

    def to_tool_parse_result(self) -> ToolParseResult:
        return ToolParseResult(
            self.instruction_type or "EXEC",
            make_security_type(
                confidentiality=self.confidentiality or "UNKNOWN",  # type: ignore[arg-type]
                trustworthiness=self.trustworthiness or "UNKNOWN",  # type: ignore[arg-type]
                confidence="UNKNOWN",
                reversible=self.reversible if self.reversible is not None else False,
                authority="UNKNOWN",
                risk=self.risk or "LOW",  # type: ignore[arg-type]
                custom=self.custom,
            ),
        )


# ---------------------------------------------------------------------------
# Pass executors
# ---------------------------------------------------------------------------

def _run_default(pass_def: Dict[str, Any], args: Dict[str, Any], acc: _Acc) -> None:
    acc.merge(pass_def["result"])


def _run_regex(pass_def: Dict[str, Any], args: Dict[str, Any], acc: _Acc) -> None:
    for case in pass_def.get("cases", []):
        value = _get_path(args, case["arg"])
        if value is None:
            continue
        if re.search(case["pattern"], str(value)):
            acc.merge(case["result"])


_OPS = {
    "eq":  lambda a, b: a == b,
    "neq": lambda a, b: a != b,
    "lt":  lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "gt":  lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
}


def _run_numeric(pass_def: Dict[str, Any], args: Dict[str, Any], acc: _Acc) -> None:
    for case in pass_def.get("cases", []):
        value = _get_path(args, case["arg"])
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        op = _OPS.get(case["op"])
        if op and op(numeric, case["value"]):
            acc.merge(case["result"])


def _run_path(pass_def: Dict[str, Any], args: Dict[str, Any], acc: _Acc) -> None:
    """PathPass: classify a path/URL arg via the path registry.

    Outputs metadata.confidentiality and metadata.trustworthiness.
    """
    raw = _get_path(args, pass_def["arg"])
    if not raw:
        return
    path_str = str(raw)
    conf = classify_confidentiality([path_str])
    trust = classify_trustworthiness([path_str])
    acc.merge({"metadata": {"confidentiality": conf, "trustworthiness": trust}})


def _run_shell(pass_def: Dict[str, Any], args: Dict[str, Any], acc: _Acc) -> None:
    """ShellPass: delegate to analyze_command and emit exec_parse custom metadata."""
    raw = _get_path(args, pass_def["arg"])
    command = str(raw) if raw is not None else ""
    analysis: CommandAnalysis = analyze_command(command)

    if not analysis.segments:
        acc.merge({
            "instruction_type": "EXEC",
            "metadata": {
                "confidentiality": "UNKNOWN",
                "trustworthiness": "UNKNOWN",
                "risk": "UNKNOWN",
                "reversible": False,
            },
            "custom": {
                "exec_parse": {
                    "command": command,
                    "segments": [],
                    "operators": [],
                    "segment_instruction_types": [],
                    "path_tokens": [],
                    "write_targets": [],
                    "parser_kind": "coarse_shell_split",
                    "parse_error": "empty_command",
                }
            },
        })
        return

    if analysis.path_tokens:
        conf = classify_confidentiality(analysis.path_tokens)
        trust = classify_trustworthiness(analysis.path_tokens)
    else:
        conf = "LOW"
        trust = "HIGH"

    for wt in analysis.write_targets:
        register_file_taint(wt, trust, conf)

    acc.merge({
        "instruction_type": analysis.itype,
        "metadata": {
            "confidentiality": conf,
            "trustworthiness": trust,
            "risk": analysis.risk,
            "reversible": analysis.itype != "EXEC",
        },
        "custom": {
            "exec_parse": {
                "command": command,
                "segments": analysis.segments,
                "operators": analysis.operators,
                "segment_instruction_types": analysis.itypes,
                "path_tokens": analysis.path_tokens,
                "write_targets": analysis.write_targets,
                "parser_kind": "coarse_shell_split",
            }
        },
    })


_PASS_RUNNERS = {
    "default": _run_default,
    "regex":   _run_regex,
    "numeric": _run_numeric,
    "path":    _run_path,
    "shell":   _run_shell,
}


_WRITE_ITYPES = {"WRITE", "STORE"}


# ---------------------------------------------------------------------------
# Per-tool parser factory
# ---------------------------------------------------------------------------

def _make_parser(passes: List[Dict[str, Any]]) -> ToolParser:
    # Collect path args declared in PathPass entries once at build time.
    _path_args: List[str] = [
        p["arg"] for p in passes if p.get("match_type") == "path"
    ]

    def _parser(
        args: Dict[str, Any],
        taint_status: Optional[TaintStatus] = None,
    ) -> ToolParseResult:
        acc = _Acc()
        for pass_def in passes:
            runner = _PASS_RUNNERS.get(pass_def["match_type"])
            if runner is None:
                logger.warning("Unknown pass match_type %r; skipping", pass_def["match_type"])
                continue
            runner(pass_def, args, acc)

        result = acc.to_tool_parse_result()

        # Auto-register file taint for write operations so that subsequent
        # reads of the same path inherit the correct classification.
        if result.instruction_type in _WRITE_ITYPES:
            conf = result.security_type.get("confidentiality", "UNKNOWN")
            trust = result.security_type.get("trustworthiness", "UNKNOWN")
            for arg_name in _path_args:
                raw = _get_path(args, arg_name)
                if raw:
                    register_file_taint(str(raw), trust, conf)

        return result

    return _parser


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_registry(yaml_path: Path) -> Dict[str, ToolParser]:
    """Load a YAML tool-parser definition file and return a tool-name → parser map.

    Validates the document against tool_parsers.schema.json before building
    parsers. Raises ValueError on schema violations.
    """
    with yaml_path.open() as f:
        defs = yaml.safe_load(f)

    if not isinstance(defs, list):
        raise ValueError(f"Expected a list of tool definitions in {yaml_path}")

    validate(defs, yaml_path.name)

    registry: Dict[str, ToolParser] = {}
    for tool_def in defs:
        tool_name: str = tool_def["tool"]
        passes: List[Dict[str, Any]] = tool_def["passes"]
        registry[tool_name] = _make_parser(passes)
        logger.debug("DSL engine: registered parser for %r (%d passes)", tool_name, len(passes))

    return registry
