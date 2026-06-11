"""Shared test fixtures.

Hook adapters intentionally mutate ``os.environ`` (via ``setdefault``) because in
production each hook runs as its own short-lived process. In-process tests share
one interpreter, so without isolation those mutations leak across tests (and the
ambient shell environment leaks in). This fixture snapshots and restores the
environment around every test, and strips provider env vars that would otherwise
bias provider detection or model capture.
"""

from __future__ import annotations

import os

import pytest

_PROVIDER_ENV_VARS = (
    "ANTHROPIC_MODEL",
    "CLAUDE_MODEL",
    "OPENAI_MODEL",
    "CODEX_MODEL",
    "CLAUDE_SESSION_ID",
    "CLAUDE_PROJECT_DIR",
    "CLAUDE_PERMISSION_MODE",
    "CLAUDE_EFFORT",
    "CLAUDE_AGENT_TYPE",
    "CLAUDE_AGENT_ID",
    "CLAUDE_PROVIDER",
    "CODEX_HOME",
    "CODEX_SESSION_ID",
    "CODEX_PERMISSION_MODE",
    "CODEX_SANDBOX_MODE",
    "CODEX_AGENT_TYPE",
    "CHECKPOINT_PROVIDER",
)


@pytest.fixture(autouse=True)
def isolate_environ():
    saved = dict(os.environ)
    for name in _PROVIDER_ENV_VARS:
        os.environ.pop(name, None)
    # F12: disable the subagent flush-settle delay by default so the suite never
    # blocks on the bounded poll. The two settle tests opt back in explicitly.
    os.environ["CHECKPOINT_SIDECHAIN_SETTLE_TIMEOUT"] = "0"
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
