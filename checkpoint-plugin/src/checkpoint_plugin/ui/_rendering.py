"""Tree rendering helpers for session browser."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from checkpoint_plugin.ui.session_browser import SessionNode, TreeRow


def render_tree_row(row: TreeRow, is_selected: bool, all_rows: list[TreeRow]) -> list[tuple[str, str]]:
    """Render a tree row with box-drawing characters and proper styling."""
    fragments: list[tuple[str, str]] = []

    prefix = _build_tree_prefix(row, all_rows)
    if prefix:
        fragments.append(("class:tree.branch", prefix))

    if row.kind == "group":
        marker = "▼ " if row.expanded else "▶ "
        fragments.append(("class:group", marker))
    elif row.kind == "session":
        fragments.append(_session_marker_fragment(row))
    elif row.kind == "link":
        fragments.append(("class:link", "→ "))
    else:
        fragments.append(("class:tree.branch", "  "))

    base_style = _row_style(row, is_selected)
    fragments.append((base_style, row.label))

    return fragments


def _build_tree_prefix(row: TreeRow, all_rows: list[TreeRow]) -> str:
    """Build the tree prefix with box-drawing characters."""
    if row.depth == 0:
        return ""

    parts = []
    current_depth = row.depth

    row_index = all_rows.index(row)
    has_sibling = [False] * current_depth

    for i in range(row_index + 1, len(all_rows)):
        next_row = all_rows[i]
        if next_row.depth < current_depth:
            break
        if next_row.depth == current_depth:
            has_sibling[current_depth - 1] = True
            break

    for d in range(current_depth):
        if d < current_depth - 1:
            if _has_ancestor_sibling(row, all_rows, d + 1):
                parts.append("│  ")
            else:
                parts.append("   ")
        else:
            if has_sibling[d]:
                parts.append("├─ ")
            else:
                parts.append("└─ ")

    return "".join(parts)


def _has_ancestor_sibling(row: TreeRow, all_rows: list[TreeRow], depth: int) -> bool:
    """Check if there's a sibling at the given ancestor depth below current row."""
    row_index = all_rows.index(row)

    for i in range(row_index + 1, len(all_rows)):
        next_row = all_rows[i]
        if next_row.depth < depth:
            return False
        if next_row.depth == depth:
            return True

    return False


def _session_marker_fragment(row: TreeRow) -> tuple[str, str]:
    """Get the marker fragment for a session row (expanded/collapsed indicator)."""
    if not row.has_children:
        marker = "  "
    else:
        marker = "▼ " if row.expanded else "▶ "
    return ("class:session", marker)


def _row_style(row: TreeRow, is_selected: bool) -> str:
    """Determine the style for a row based on its properties."""
    base_style = row.style

    if is_selected:
        base_style = "reverse " + base_style

    if row.kind == "session":
        if row.node.source == "startup":
            base_style = base_style.replace("class:session", "class:session.startup")
        elif row.node.source in {"fork", "resume"} or row.node.fork_parent:
            base_style = base_style.replace("class:session", "class:session.fork")
        elif row.node.source == "subagent" or row.node.subagent_parent:
            base_style = base_style.replace("class:session", "class:session.subagent")

    return base_style
