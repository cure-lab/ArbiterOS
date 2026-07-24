"""Tests for Claude Code sandbox settings mapping and profile I/O."""

from __future__ import annotations

import json
from pathlib import Path

from arbiteros_kernel.tui.sandbox import claude_config_io as claude_io
from arbiteros_kernel.tui.sandbox import profiles
from arbiteros_kernel.tui.sandbox.config_io import SandboxSettings


def test_claude_profile_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(profiles, "PROFILES_ROOT", tmp_path)
    monkeypatch.setattr(profiles, "PROFILES_DIR", tmp_path / "codex")
    settings = SandboxSettings(
        sandbox_mode="workspace-write",
        approval_policy="on-request",
        network_policy="allowlist",
        allowed_domains=["github.com"],
        deny_globs=["**/*.env"],
        deny_paths=[str(Path.home() / ".ssh")],
        writable_roots=["/tmp/extra"],
    )
    path = profiles.save_profile("daily-cc", settings, agent="claude")
    assert path.parent.name == "claude"
    assert profiles.list_profiles("claude") == ["daily-cc"]
    assert profiles.list_profiles("codex") == []
    loaded = profiles.load_profile("daily-cc", agent="cc")
    assert loaded.sandbox_mode == "workspace-write"
    assert loaded.allowed_domains == ["github.com"]


def test_normalize_agent() -> None:
    assert profiles.normalize_agent("cc") == "claude"
    assert profiles.normalize_agent("claude_code") == "claude"
    assert profiles.normalize_agent("codex") == "codex"


def test_settings_to_claude_sandbox_locked() -> None:
    sandbox = claude_io.settings_to_claude_sandbox(
        SandboxSettings(
            sandbox_mode="read-only",
            approval_policy="untrusted",
            deny_globs=["**/*.env"],
            deny_paths=[str(Path.home() / ".ssh")],
            network_policy="off",
        )
    )
    assert sandbox["enabled"] is True
    assert sandbox["autoAllowBashIfSandboxed"] is False
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["network"]["allowedDomains"] == []
    assert "**/*.env" in sandbox["filesystem"]["denyRead"]
    assert str(Path.home() / ".ssh") in sandbox["filesystem"]["denyRead"]


def test_settings_to_claude_sandbox_workspace_network() -> None:
    sandbox = claude_io.settings_to_claude_sandbox(
        SandboxSettings(
            sandbox_mode="workspace-write",
            approval_policy="on-request",
            network_policy="open",
            writable_roots=["/tmp/build"],
        )
    )
    assert sandbox["enabled"] is True
    assert sandbox["autoAllowBashIfSandboxed"] is True
    assert sandbox["filesystem"]["allowWrite"] == ["/tmp/build"]
    assert sandbox["network"]["allowLocalBinding"] is True
    assert "allowedDomains" not in sandbox["network"]


def test_settings_to_claude_full_access() -> None:
    sandbox = claude_io.settings_to_claude_sandbox(
        SandboxSettings(sandbox_mode="danger-full-access", approval_policy="never")
    )
    assert sandbox == {"enabled": False, "allowUnsandboxedCommands": True}


def test_apply_and_load_claude_settings(tmp_path: Path) -> None:
    cfg = tmp_path / "settings.json"
    cfg.write_text(
        json.dumps({"permissions": {"allow": ["Bash(git *)"]}, "theme": "dark"}) + "\n",
        encoding="utf-8",
    )
    original = SandboxSettings(
        sandbox_mode="workspace-write",
        approval_policy="on-request",
        network_policy="allowlist",
        allowed_domains=["api.github.com"],
        denied_domains=["evil.example"],
        deny_globs=["**/*.pem"],
        deny_paths=[str(Path.home() / ".aws")],
        writable_roots=["/tmp/extra-root"],
    )
    backup = claude_io.apply_settings(original, path=cfg)
    assert backup.exists()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
    assert data["permissions"]["allow"] == ["Bash(git *)"]
    assert data["sandbox"]["enabled"] is True
    assert data["sandbox"]["autoAllowBashIfSandboxed"] is True
    assert data["sandbox"]["network"]["allowedDomains"] == ["api.github.com"]
    assert claude_io.WIZARD_MARKER_KEY in data

    loaded = claude_io.load_settings(path=cfg)
    assert loaded.sandbox_mode == "workspace-write"
    assert loaded.network_policy == "allowlist"
    assert loaded.allowed_domains == ["api.github.com"]
    assert loaded.denied_domains == ["evil.example"]
    assert "**/*.pem" in loaded.deny_globs
    assert str(Path.home() / ".aws") in loaded.deny_paths
    assert "/tmp/extra-root" in loaded.writable_roots


def test_load_disabled_sandbox(tmp_path: Path) -> None:
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({"sandbox": {"enabled": False}}) + "\n", encoding="utf-8")
    loaded = claude_io.load_settings(path=cfg)
    assert loaded.sandbox_mode == "danger-full-access"
