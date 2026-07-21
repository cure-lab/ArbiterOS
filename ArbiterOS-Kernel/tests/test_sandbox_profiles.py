"""Tests for named Codex sandbox profiles."""

from __future__ import annotations

from pathlib import Path

from arbiteros_kernel.tui.sandbox import profiles
from arbiteros_kernel.tui.sandbox.config_io import SandboxSettings


def test_profile_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "codex")
    settings = SandboxSettings(
        sandbox_mode="workspace-write",
        approval_policy="on-request",
        network_policy="off",
        deny_globs=["**/*.env"],
        deny_paths=[str(Path.home() / ".ssh")],
    )
    path = profiles.save_profile("daily-dev", settings)
    assert path.exists()
    assert profiles.list_profiles() == ["daily-dev"]
    loaded = profiles.load_profile("daily-dev")
    assert loaded.sandbox_mode == "workspace-write"
    assert loaded.deny_globs == ["**/*.env"]
    assert profiles.delete_profile("daily-dev") is True
    assert profiles.list_profiles() == []


def test_invalid_profile_name() -> None:
    try:
        profiles.validate_profile_name("../evil")
        assert False, "expected ValueError"
    except ValueError:
        pass
