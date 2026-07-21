"""Tests for Codex sandbox profiles and permission-profile config I/O."""

from __future__ import annotations

from pathlib import Path

from arbiteros_kernel.tui.sandbox import profiles
from arbiteros_kernel.tui.sandbox.config_io import (
    PROFILE_NAME,
    SandboxSettings,
    apply_settings,
    load_settings,
)


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


def test_apply_strips_sandbox_mode_and_writes_permission_profile(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "\n".join(
            [
                'model = "gpt-5"',
                'sandbox_mode = "workspace-write"',
                'approval_policy = "never"',
                "",
                "[sandbox_workspace_write]",
                "network_access = true",
                'writable_roots = ["/tmp/extra"]',
                "",
                "[hooks.state]",
                'trusted_hash = "sha256:abc"',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    settings = SandboxSettings(
        sandbox_mode="workspace-write",
        approval_policy="on-request",
        network_policy="off",
        deny_globs=["**/*.env", "**/.env.*"],
        deny_paths=[str(Path.home() / ".ssh")],
    )
    apply_settings(settings, path=cfg)
    text = cfg.read_text(encoding="utf-8")
    assert "sandbox_mode" not in text
    assert "[sandbox_workspace_write]" not in text
    assert f'default_permissions = "{PROFILE_NAME}"' in text
    assert f"[permissions.{PROFILE_NAME}]" in text
    assert 'extends = ":workspace"' in text
    assert '"**/*.env" = "deny"' in text
    assert f'"{Path.home() / ".ssh"}" = "deny"' in text
    assert "approval_policy = \"on-request\"" in text
    assert "enabled = false" in text
    # TOML: root keys must appear before any [table]
    top, _, rest = text.partition("[")
    assert f'default_permissions = "{PROFILE_NAME}"' in top
    assert "approval_policy" in top
    assert f"[permissions.{PROFILE_NAME}]" in "[" + rest


def test_apply_builtin_when_no_customizations(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    apply_settings(
        SandboxSettings(sandbox_mode="read-only", approval_policy="untrusted"),
        path=cfg,
    )
    text = cfg.read_text(encoding="utf-8")
    assert 'default_permissions = ":read-only"' in text
    assert f"[permissions.{PROFILE_NAME}]" not in text
    assert "sandbox_mode" not in text


def test_apply_full_access(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    apply_settings(
        SandboxSettings(sandbox_mode="danger-full-access", approval_policy="never"),
        path=cfg,
    )
    text = cfg.read_text(encoding="utf-8")
    assert 'default_permissions = ":danger-full-access"' in text
    assert "deny" not in text


def test_apply_open_network(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    apply_settings(
        SandboxSettings(
            sandbox_mode="workspace-write",
            network_policy="open",
            deny_globs=["**/*.env"],
        ),
        path=cfg,
    )
    text = cfg.read_text(encoding="utf-8")
    assert "[permissions.arbiteros.network]" in text
    assert "enabled = true" in text
    assert '"*" = "allow"' in text


def test_load_roundtrip_custom_profile(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    original = SandboxSettings(
        sandbox_mode="workspace-write",
        approval_policy="on-request",
        network_policy="allowlist",
        allowed_domains=["api.openai.com"],
        denied_domains=["tracking.example.com"],
        deny_globs=["**/*.env"],
        deny_paths=[str(Path.home() / ".aws")],
        writable_roots=["/tmp/extra-root"],
        exclude_tmpdir_env_var=True,
        exclude_slash_tmp=True,
    )
    apply_settings(original, path=cfg)
    loaded = load_settings(path=cfg)
    assert loaded.sandbox_mode == "workspace-write"
    assert loaded.approval_policy == "on-request"
    assert loaded.network_policy == "allowlist"
    assert loaded.allowed_domains == ["api.openai.com"]
    assert loaded.denied_domains == ["tracking.example.com"]
    assert "**/*.env" in loaded.deny_globs
    assert str(Path.home() / ".aws") in loaded.deny_paths
    assert "/tmp/extra-root" in loaded.writable_roots
    assert loaded.exclude_tmpdir_env_var is True
    assert loaded.exclude_slash_tmp is True


def test_load_builtin_read_only(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    apply_settings(
        SandboxSettings(sandbox_mode="read-only", approval_policy="untrusted"),
        path=cfg,
    )
    loaded = load_settings(path=cfg)
    assert loaded.sandbox_mode == "read-only"
    assert loaded.approval_policy == "untrusted"
    assert loaded.resolved_default_permissions() == ":read-only"


def test_load_read_only_with_denies(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    apply_settings(
        SandboxSettings(
            sandbox_mode="read-only",
            deny_globs=["**/*.pem"],
            deny_paths=[str(Path.home() / ".ssh")],
        ),
        path=cfg,
    )
    text = cfg.read_text(encoding="utf-8")
    assert 'extends = ":read-only"' in text
    loaded = load_settings(path=cfg)
    assert loaded.sandbox_mode == "read-only"
    assert "**/*.pem" in loaded.deny_globs
