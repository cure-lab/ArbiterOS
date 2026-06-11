"""Gitignore-style exclusion matching for workspace snapshots."""

from __future__ import annotations

import fnmatch
from pathlib import Path

BUILTIN_PATTERNS = [
    ".git/**",
    "node_modules/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "*.pyc",
    ".venv/**",
    "dist/**",
    "build/**",
    ".checkpoint-plugin/**",
]

SECRET_PATTERNS = [
    ".env",
    ".env*",
    "**/.env*",
    "*credential*",
    "**/*credential*",
    "*.pem",
    "*.key",
]


class IgnoreMatcher:
    def __init__(self, cwd: Path, extra_patterns: list[str] | None = None) -> None:
        self.cwd = Path(cwd).expanduser().resolve()
        self.patterns = [*BUILTIN_PATTERNS, *SECRET_PATTERNS, *(extra_patterns or [])]
        gitignore = self.cwd / ".gitignore"
        if gitignore.exists():
            self.patterns.extend(_read_gitignore(gitignore))

    def matches(self, path: Path) -> bool:
        rel = path
        if path.is_absolute():
            try:
                rel = path.relative_to(self.cwd)
            except ValueError:
                return True
        rel_text = rel.as_posix()
        return any(_match_pattern(rel_text, pattern) for pattern in self.patterns)


def _read_gitignore(path: Path) -> list[str]:
    patterns: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        patterns.append(stripped)
    return patterns


def _match_pattern(rel: str, pattern: str) -> bool:
    normalized = pattern.strip().lstrip("/")
    if not normalized:
        return False
    if normalized.endswith("/"):
        normalized += "**"
    if normalized.endswith("/**"):
        base = normalized[:-3].rstrip("/")
        return rel == base or rel.startswith(base + "/") or fnmatch.fnmatch(rel, normalized)
    return fnmatch.fnmatch(rel, normalized) or fnmatch.fnmatch(Path(rel).name, normalized)
