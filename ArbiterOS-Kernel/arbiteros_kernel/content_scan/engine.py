from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from arbiteros_kernel.instruction_depends_on import strip_arbiteros_ref_marker

from .config import ContentScanConfig, DEFAULT_REPLACEMENT

_CONTROL_PREFIXES = (
    "[arbiteros_turn_context]",
    "[arbiteros_topic_hint]",
    "[arbiteros_depends_on]",
)


@dataclass
class ScanResult:
    text: str
    changed: bool
    hits: list[dict[str, Any]] = field(default_factory=list)


def is_skipped_context_text(text: str) -> bool:
    body = strip_arbiteros_ref_marker(text or "").lstrip()
    lowered = body.lower()
    return any(lowered.startswith(prefix) for prefix in _CONTROL_PREFIXES)


def _split_ref_prefix(text: str) -> tuple[str, str]:
    if not isinstance(text, str) or not text.startswith("[ARBITEROS_REF"):
        return "", text
    newline = text.find("\n")
    if newline < 0:
        return text, ""
    return text[: newline + 1], text[newline + 1 :]


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    ordered = sorted((int(start), int(end)) for start, end in spans if end > start)
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _replace_spans(text: str, spans: list[tuple[int, int]], replacement: str) -> str:
    out = text
    for start, end in reversed(_merge_spans(spans)):
        out = out[:start] + replacement + out[end:]
    return out


def _pattern_spans(text: str, cfg: ContentScanConfig) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for rule in cfg.patterns:
        if rule.kind == "literal":
            needle = rule.pattern
            if not needle:
                continue
            start = 0
            while True:
                found = text.find(needle, start)
                if found < 0:
                    break
                spans.append((found, found + len(needle)))
                start = found + max(len(needle), 1)
            continue
        compiled = rule.compiled
        if compiled is None:
            continue
        for match in compiled.finditer(text):
            if match.end() > match.start():
                spans.append((match.start(), match.end()))
    return spans


def scan_text(
    text: str,
    cfg: ContentScanConfig,
    *,
    side: Literal["input", "output"] = "input",
    trace_id: str = "",
    skip_control: bool = True,
) -> ScanResult:
    _ = side, trace_id
    if not isinstance(text, str) or not text:
        return ScanResult(text=text if isinstance(text, str) else "", changed=False)
    if skip_control and is_skipped_context_text(text):
        return ScanResult(text=text, changed=False)

    prefix, body = _split_ref_prefix(text)
    if not body:
        return ScanResult(text=text, changed=False)

    replacement = cfg.replacement or DEFAULT_REPLACEMENT
    spans = _pattern_spans(body, cfg)
    if not spans:
        return ScanResult(text=text, changed=False)

    current = _replace_spans(body, spans, replacement)
    return ScanResult(
        text=prefix + current,
        changed=True,
        hits=[{"detector": "pattern", "count": len(spans)}],
    )
