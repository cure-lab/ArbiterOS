"""UI helper functions for formatting and display."""

from __future__ import annotations

from datetime import datetime


def format_timestamp(ts_str: str | None) -> str:
    """Format timestamp as relative time or short date."""
    if not ts_str:
        return "-"

    try:
        # Try parsing ISO format
        if "T" in ts_str:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        else:
            # Fallback for other formats
            return ts_str[:16] if len(ts_str) > 16 else ts_str

        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        delta = now - dt

        # Format as relative time for recent items with better granularity
        seconds = delta.total_seconds()
        if seconds < 10:
            return "just now"
        elif seconds < 60:
            return f"{int(seconds)}s ago"
        elif seconds < 3600:
            mins = int(seconds / 60)
            return f"{mins}m ago"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            mins = int((seconds % 3600) / 60)
            if hours < 6 and mins > 0:
                return f"{hours}h {mins}m ago"
            return f"{hours}h ago"
        elif seconds < 604800:
            days = int(seconds / 86400)
            hours = int((seconds % 86400) / 3600)
            if days < 3 and hours > 0:
                return f"{days}d {hours}h ago"
            return f"{days}d ago"
        else:
            # Format as date for older items with better readability
            return dt.strftime("%b %d, %H:%M")
    except (ValueError, AttributeError):
        return ts_str[:16] if len(ts_str) > 16 else ts_str


def truncate_with_ellipsis(text: str, max_len: int = 60) -> str:
    """Truncate text with ellipsis if too long, respecting word boundaries."""
    if len(text) <= max_len:
        return text

    # Try to break at word boundary
    truncated = text[: max_len - 1]
    last_space = truncated.rfind(" ")

    # If we found a space in the last 20% of the string, use it
    if last_space > max_len * 0.8:
        return truncated[:last_space] + "…"

    return truncated + "…"


def build_tree_prefix(depth: int, is_last: bool, parent_prefixes: list[bool]) -> str:
    """Build tree structure prefix with box-drawing characters."""
    if depth == 0:
        return ""

    parts = []
    for i in range(depth - 1):
        if i < len(parent_prefixes) and parent_prefixes[i]:
            parts.append("│   ")
        else:
            parts.append("    ")

    if is_last:
        parts.append("└── ")
    else:
        parts.append("├── ")

    return "".join(parts)
