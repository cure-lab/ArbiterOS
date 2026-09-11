from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

DEFAULT_REPLACEMENT = "[Sensitive information has been eliminated]"


@dataclass(frozen=True)
class PatternRule:
    id: str
    kind: Literal["literal", "regex"]
    pattern: str
    compiled: re.Pattern[str] | None = None


@dataclass(frozen=True)
class ContentScanConfig:
    replacement: str = DEFAULT_REPLACEMENT
    patterns: list[PatternRule] = field(default_factory=list)


def content_scan_registry_enabled() -> bool:
    """Master switch: ``ContentScanPolicy.enabled`` in ``policy_registry.json``."""
    from arbiteros_kernel.policy.defaults import get_policy_enabled

    return bool(get_policy_enabled().get("ContentScanPolicy", False))


def _parse_patterns(raw: Any) -> list[PatternRule]:
    if not isinstance(raw, list):
        return []
    out: list[PatternRule] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if isinstance(item, str) and item:
            out.append(PatternRule(id=f"literal_{index}", kind="literal", pattern=item, compiled=None))
            continue
        if not isinstance(item, dict):
            continue
        pattern = str(item.get("pattern") or "").strip()
        if not pattern:
            continue
        rule_id = str(item.get("id") or f"rule_{index}").strip() or f"rule_{index}"
        if rule_id in seen:
            rule_id = f"{rule_id}_{index}"
        seen.add(rule_id)
        kind_raw = str(item.get("type") or item.get("kind") or "literal").strip().lower()
        kind: Literal["literal", "regex"] = "regex" if kind_raw == "regex" else "literal"
        compiled = None
        if kind == "regex":
            try:
                compiled = re.compile(pattern)
            except re.error:
                continue
        out.append(PatternRule(id=rule_id, kind=kind, pattern=pattern, compiled=compiled))
    return out


def load_content_scan_config(raw: Any) -> ContentScanConfig:
    data = raw if isinstance(raw, dict) else {}
    replacement = str(data.get("replacement") or DEFAULT_REPLACEMENT)
    if not replacement.strip():
        replacement = DEFAULT_REPLACEMENT
    return ContentScanConfig(
        replacement=replacement,
        patterns=_parse_patterns(data.get("patterns")),
    )
