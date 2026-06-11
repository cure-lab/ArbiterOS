"""Small path helpers shared by checkpoint restore flows."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Literal

PathRootKind = Literal["file", "directory"]


def mirror_path(path: Path) -> Path:
    return Path(*path.parts[1:]) if path.is_absolute() else path


def path_within(path: Path, root: Path) -> bool:
    resolved = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    return resolved == resolved_root or resolved_root in resolved.parents


def path_matches_root(path: Path, root: Path, *, kind: PathRootKind = "directory") -> bool:
    resolved = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if kind == "file":
        return resolved == resolved_root
    return resolved == resolved_root or resolved_root in resolved.parents


def rewrite_path_references_bytes(data: bytes, path_map: Mapping[str, str]) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    rewritten = rewrite_path_references_text(text, path_map)
    return rewritten.encode("utf-8") if rewritten != text else data


def rewrite_path_references_text(text: str, path_map: Mapping[str, str]) -> str:
    replacements: list[tuple[str, str]] = []
    for source, dest in sorted(path_map.items(), key=lambda item: len(item[0]), reverse=True):
        if not source or not dest or source == dest:
            continue
        source_text = source.rstrip("/") if source != "/" else source
        dest_text = dest.rstrip("/") if dest != "/" else dest
        if not source_text or not dest_text or source_text == "/":
            continue
        replacements.append((source_text, dest_text))
    if not replacements:
        return text

    index = 0
    result: list[str] = []
    while index < len(text):
        match = _path_reference_match_at(text, index, replacements)
        if match is None:
            result.append(text[index])
            index += 1
        else:
            source, dest = match
            result.append(dest)
            index += len(source)
    return "".join(result)


def _path_reference_match_at(text: str, index: int, replacements: list[tuple[str, str]]) -> tuple[str, str] | None:
    before = text[index - 1 : index]
    for source, dest in replacements:
        if not text.startswith(source, index):
            continue
        after = text[index + len(source) : index + len(source) + 1]
        if _path_reference_boundary_before(before) and _path_reference_boundary_after(after):
            return source, dest
    return None


def _path_reference_boundary_before(char: str) -> bool:
    return char == "" or not (char.isalnum() or char in "-_./")


def _path_reference_boundary_after(char: str) -> bool:
    return char == "" or char == "/" or not (char.isalnum() or char in "-_.")
