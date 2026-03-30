import os
from typing import Optional


def _get_nonempty_env(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def ensure_langfuse_env_compat() -> Optional[str]:
    """
    Make LANGFUSE_BASE_URL and LANGFUSE_HOST mutually compatible.

    Preference:
    - If LANGFUSE_BASE_URL is set (non-empty), it wins.
    - Otherwise LANGFUSE_HOST is used.

    The resolved value is written back to BOTH env vars so whichever a downstream
    SDK expects will work.
    """

    base_url = _get_nonempty_env("LANGFUSE_BASE_URL")
    host = _get_nonempty_env("LANGFUSE_HOST")
    resolved = base_url or host
    if not resolved:
        return None

    os.environ["LANGFUSE_BASE_URL"] = resolved
    os.environ["LANGFUSE_HOST"] = resolved
    return resolved
