"""Interactive resume diff viewer."""

from __future__ import annotations

import difflib
import sys
from dataclasses import dataclass
from typing import TextIO

from checkpoint_plugin.env.differ import CategoryDiff, diff_environments
from checkpoint_plugin.fs.restorer import diff_filesystems
from checkpoint_plugin.store import CheckpointStore
from checkpoint_plugin.types import ResumePlan
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Box, Label


@dataclass(frozen=True)
class DiffEntry:
    section: str
    status: str
    path: str
    display: str
    current_sha: str | None = None
    target_sha: str | None = None
    summary: str | None = None


@dataclass(frozen=True)
class DisplayLine:
    text: str
    index: int | None = None
    entry: DiffEntry | None = None


def show_diff_viewer(
    plan: ResumePlan,
    store: CheckpointStore,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> None:
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    entries = diff_entries(plan, store)
    if not entries:
        print("No resume changes to show.", file=output_stream)
        return

    if input_stream.isatty() and output_stream.isatty():
        _show_tui_viewer(plan, store, entries)
    else:
        _show_numbered_viewer(plan, store, entries, output_stream)


def diff_entries(plan: ResumePlan, store: CheckpointStore | None = None) -> list[DiffEntry]:
    entries = _environment_entries(plan, store)
    diff = diff_filesystems(plan.current_fs, plan.target_fs)
    entries.extend(
        DiffEntry(
            "Filesystem",
            "M",
            path,
            f"  ~ {path}",
            current_sha=plan.current_fs.files.get(path),
            target_sha=plan.target_fs.files.get(path),
        )
        for path in diff.modified
    )
    entries.extend(
        DiffEntry(
            "Filesystem",
            "D",
            path,
            f"  - {path}",
            current_sha=plan.current_fs.files.get(path),
            target_sha=plan.target_fs.files.get(path),
        )
        for path in diff.deleted
    )
    entries.extend(
        DiffEntry(
            "Filesystem",
            "A",
            path,
            f"  + {path}",
            current_sha=plan.current_fs.files.get(path),
            target_sha=plan.target_fs.files.get(path),
        )
        for path in diff.added
    )
    return entries


def render_file_diff(plan: ResumePlan, store: CheckpointStore, entry: DiffEntry) -> str:
    if entry.summary is not None:
        return entry.summary

    try:
        current = _snapshot_content(entry.current_sha, store)
        target = _snapshot_content(entry.target_sha, store)
    except Exception as exc:
        return f"Error loading file content: {exc}\n\nEntry: {entry.path}\nCurrent SHA: {entry.current_sha}\nTarget SHA: {entry.target_sha}"

    if current is not None and _is_binary(current):
        return _render_binary_diff(entry, current, target)
    if target is not None and _is_binary(target):
        return _render_binary_diff(entry, current, target)

    current_text = _decode_text(current)
    target_text = _decode_text(target)
    if current_text is None or target_text is None:
        return _render_binary_diff(entry, current, target)

    try:
        lines = difflib.unified_diff(
            current_text.splitlines(keepends=True),
            target_text.splitlines(keepends=True),
            fromfile=_diff_header("current", entry),
            tofile=_diff_header("checkpoint", entry),
            lineterm="\n",
        )
        rendered = "".join(lines)
        return rendered or f"No content changes for {entry.path}"
    except Exception as exc:
        return f"Error generating diff: {exc}\n\nPath: {entry.path}"


def _environment_entries(plan: ResumePlan, store: CheckpointStore | None = None) -> list[DiffEntry]:
    diff = diff_environments(
        plan.current_env,
        plan.target_env,
        blob_loader=store.load_blob if store is not None else None,
        ignore_plugin_hooks=plan.ignore_plugin_hooks,
    )
    entries: list[DiffEntry] = []
    if diff.provider_changed:
        entries.append(_value_entry("Provider", plan.current_env.provider, plan.target_env.provider))
    if diff.model_changed:
        entries.append(_value_entry("Model", plan.current_env.model, plan.target_env.model))
    if diff.permission_changed:
        entries.append(_value_entry("Permission", plan.current_env.permission_mode, plan.target_env.permission_mode))
    if diff.mcp_changed:
        entries.append(
            DiffEntry(
                "Environment",
                "M",
                "MCP config",
                "  ~ MCP config",
                current_sha=plan.current_env.mcp_config,
                target_sha=plan.target_env.mcp_config,
            )
        )
    _extend_blob_entries(entries, "MCP config files", diff.mcp_configs, plan.current_env.mcp_configs, plan.target_env.mcp_configs)
    _extend_value_entries(entries, "MCP servers", diff.mcp_servers, plan.current_env.mcp_servers, plan.target_env.mcp_servers)
    _extend_blob_entries(entries, "Memory", diff.memory, plan.current_env.memory_files, plan.target_env.memory_files)
    _extend_blob_entries(entries, "Skills", diff.skills, plan.current_env.skills, plan.target_env.skills)
    _extend_value_entries(entries, "Skill status", diff.skill_status, plan.current_env.skill_status, plan.target_env.skill_status)
    _extend_value_entries(
        entries,
        "Plugin status",
        diff.plugin_status,
        plan.current_env.plugin_status,
        plan.target_env.plugin_status,
    )
    _extend_blob_entries(entries, "Settings", diff.settings, plan.current_env.settings, plan.target_env.settings)
    _extend_blob_entries(
        entries,
        "Project context",
        diff.project_context,
        plan.current_env.project_context,
        plan.target_env.project_context,
    )
    return entries


def _extend_blob_entries(
    entries: list[DiffEntry],
    label: str,
    diff: CategoryDiff,
    current: dict[str, str],
    target: dict[str, str],
) -> None:
    for path in diff.modified:
        entries.append(_blob_entry("M", label, path, current.get(path), target.get(path)))
    for path in diff.removed:
        entries.append(_blob_entry("D", label, path, current.get(path), target.get(path)))
    for path in diff.added:
        entries.append(_blob_entry("A", label, path, current.get(path), target.get(path)))


def _extend_value_entries(
    entries: list[DiffEntry],
    label: str,
    diff: CategoryDiff,
    current: dict[str, str],
    target: dict[str, str],
) -> None:
    for path in diff.modified:
        entries.append(_value_entry(f"{label}/{path}", current.get(path), target.get(path)))
    for path in diff.removed:
        entries.append(_value_entry(f"{label}/{path}", current.get(path), None, status="D"))
    for path in diff.added:
        entries.append(_value_entry(f"{label}/{path}", None, target.get(path), status="A"))


def _blob_entry(status: str, label: str, path: str, current_sha: str | None, target_sha: str | None) -> DiffEntry:
    return DiffEntry(
        "Environment",
        status,
        f"{label}/{path}",
        f"    {_status_symbol(status)} {path}",
        current_sha=current_sha,
        target_sha=target_sha,
    )


def _value_entry(path: str, current: str | None, target: str | None, status: str = "M") -> DiffEntry:
    return DiffEntry(
        "Environment",
        status,
        path,
        f"  {_status_symbol(status)} {path}",
        summary=f"Environment {path}\ncurrent: {current or '-'}\ncheckpoint: {target or '-'}",
    )


def _show_numbered_viewer(
    plan: ResumePlan,
    store: CheckpointStore,
    entries: list[DiffEntry],
    output_stream: TextIO,
) -> None:
    while True:
        display_lines = _grouped_display_lines(entries)
        visible_entries = [line.entry for line in display_lines if line.entry is not None]
        print("\nDetailed resume changes:", file=output_stream)
        for line in display_lines:
            if line.entry is None:
                print(line.text, file=output_stream)
            else:
                print(f"  {line.index}. {line.text}", file=output_stream)
        answer = input("Select change number to view diff, or q to return: ")
        if answer.lower() in {"q", "quit", "esc", ""}:
            return
        try:
            selected = visible_entries[int(answer) - 1]
        except (ValueError, IndexError):
            print("Invalid selection.", file=output_stream)
            continue
        print(file=output_stream)
        print(render_file_diff(plan, store, selected), file=output_stream)


def _show_tui_viewer(plan: ResumePlan, store: CheckpointStore, entries: list[DiffEntry]) -> None:
    state = {"selected": 0, "mode": "list"}
    body_control = FormattedTextControl(lambda: _body_fragments(plan, store, entries, state))
    body = Window(content=body_control, wrap_lines=False, always_hide_cursor=True)
    title = Label(lambda: "Detailed resume changes" if state["mode"] == "list" else _diff_title(entries[state["selected"]]))
    help_text = Window(
        content=FormattedTextControl(
            lambda: [
                (
                    "bold ansiyellow",
                    (
                        "Enter: open diff    j/k or arrows: move    Esc/q: back"
                        if state["mode"] == "list"
                        else "Esc/q: back to changes"
                    ),
                )
            ]
        ),
        height=1,
        always_hide_cursor=True,
    )

    def invalidate(event) -> None:  # noqa: ANN001
        event.app.invalidate()

    bindings = KeyBindings()

    @bindings.add("down")
    @bindings.add("j")
    def _move_down(event) -> None:  # noqa: ANN001
        if state["mode"] != "list":
            return
        state["selected"] = min(state["selected"] + 1, len(entries) - 1)
        invalidate(event)

    @bindings.add("up")
    @bindings.add("k")
    def _move_up(event) -> None:  # noqa: ANN001
        if state["mode"] != "list":
            return
        state["selected"] = max(state["selected"] - 1, 0)
        invalidate(event)

    @bindings.add("enter")
    def _show_selected(event) -> None:  # noqa: ANN001
        state["mode"] = "diff"
        invalidate(event)

    @bindings.add("escape")
    @bindings.add("q")
    def _quit(event) -> None:  # noqa: ANN001
        if state["mode"] == "diff":
            state["mode"] = "list"
            invalidate(event)
        else:
            event.app.exit()

    root = HSplit(
        [
            title,
            Box(body, padding=1),
            help_text,
        ]
    )
    Application(layout=Layout(root), key_bindings=bindings, full_screen=True).run()


def _body_fragments(
    plan: ResumePlan,
    store: CheckpointStore,
    entries: list[DiffEntry],
    state: dict[str, int | str],
) -> list[tuple[str, str]]:
    selected = int(state["selected"])
    if state["mode"] == "diff":
        return _formatted_diff(render_file_diff(plan, store, entries[selected]))
    return _formatted_entry_list(entries, selected)


def _grouped_display_lines(entries: list[DiffEntry]) -> list[DisplayLine]:
    lines: list[DisplayLine] = []
    index = 1
    env_entries = [entry for entry in entries if entry.section == "Environment"]
    fs_entries = [entry for entry in entries if entry.section == "Filesystem"]
    if env_entries:
        lines.append(DisplayLine("Environment:"))
        env_lines, index = _environment_display_lines(env_entries, index)
        lines.extend(env_lines)
    if fs_entries:
        lines.append(DisplayLine("Filesystem:"))
        for entry in fs_entries:
            lines.append(DisplayLine(entry.display, index, entry))
            index += 1
    return lines


def _environment_display_lines(entries: list[DiffEntry], start_index: int) -> tuple[list[DisplayLine], int]:
    lines: list[DisplayLine] = []
    index = start_index
    grouped: dict[str, list[DiffEntry]] = {}
    for entry in entries:
        if "/" not in entry.path:
            lines.append(DisplayLine(entry.display, index, entry))
            index += 1
            continue
        category, _name = entry.path.split("/", 1)
        grouped.setdefault(category, []).append(entry)
    for category, category_entries in grouped.items():
        lines.append(DisplayLine(f"  {category} ({len(category_entries)} changes):"))
        for entry in category_entries:
            lines.append(DisplayLine(entry.display, index, entry))
            index += 1
    return lines, index


def _formatted_entry_list(entries: list[DiffEntry], selected: int) -> list[tuple[str, str]]:
    selected_entry = entries[selected]
    result: list[tuple[str, str]] = []
    for line in _grouped_display_lines(entries):
        if line.entry is None:
            result.append(("", f"  {line.text}\n"))
            continue
        style = "reverse" if line.entry is selected_entry else ""
        marker = ">" if line.entry is selected_entry else " "
        result.append((style, f"{marker} {line.text}\n"))
    return result


def _formatted_diff(text: str) -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = []
    for line in text.splitlines():
        style = ""
        if line.startswith("+++"):
            style = "bold ansigreen"
        elif line.startswith("---"):
            style = "bold ansired"
        elif line.startswith("+"):
            style = "ansigreen"
        elif line.startswith("-"):
            style = "ansired"
        elif line.startswith("@@"):
            style = "ansicyan"
        fragments.append((style, line + "\n"))
    return fragments


def _diff_title(entry: DiffEntry) -> str:
    return f"Diff: {entry.section} / {entry.path}"


def _snapshot_content(sha: str | None, store: CheckpointStore) -> bytes | None:
    return store.load_blob(sha) if sha is not None else None


def _diff_header(prefix: str, entry: DiffEntry) -> str:
    if entry.section == "Filesystem":
        return f"{prefix}/{entry.path}"
    return f"{prefix}/environment/{entry.path}"


def _decode_text(data: bytes | None) -> str | None:
    if data is None:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _is_binary(data: bytes) -> bool:
    return b"\0" in data


def _render_binary_diff(entry: DiffEntry, current: bytes | None, target: bytes | None) -> str:
    current_size = len(current) if current is not None else 0
    target_size = len(target) if target is not None else 0
    return (
        f"Binary file changed: {entry.path}\n"
        f"current size: {current_size} bytes\n"
        f"checkpoint size: {target_size} bytes"
    )


def _status_symbol(status: str) -> str:
    return {"A": "+", "D": "-"}.get(status, "~")
