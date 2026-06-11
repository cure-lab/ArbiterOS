from __future__ import annotations

from pathlib import Path

import pytest

from arbiteros_kernel import checkpoint_lifecycle as lifecycle


def test_resolve_providers_auto_from_tool_agent() -> None:
    providers = lifecycle._resolve_providers({"providers": "auto"}, "codex")
    assert providers == ("codex",)

    providers = lifecycle._resolve_providers({"providers": "auto"}, "claude_code")
    assert providers == ("claude",)

    providers = lifecycle._resolve_providers({"providers": "auto"}, "openclaw")
    assert providers == ()


def test_resolve_providers_explicit_list() -> None:
    providers = lifecycle._resolve_providers(
        {"providers": ["codex", "claude"]}, "openclaw"
    )
    assert providers == ("codex", "claude")


def test_set_checkpoint_recording_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(tmp_path))
    lifecycle.set_checkpoint_recording_enabled(True, home=tmp_path)
    from checkpoint_plugin.paths import load_config

    assert load_config(tmp_path)["enabled"] is True
    lifecycle.set_checkpoint_recording_enabled(False, home=tmp_path)
    assert load_config(tmp_path)["enabled"] is False


def test_recording_enabled_respects_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHECKPOINT_PLUGIN_HOME", str(tmp_path))
    from checkpoint_plugin.integrations._hook_common import recording_enabled
    from checkpoint_plugin.paths import load_config, write_config

    config = load_config(tmp_path)
    config["enabled"] = False
    write_config(config, tmp_path)
    assert recording_enabled() is False

    config["enabled"] = True
    write_config(config, tmp_path)
    assert recording_enabled() is True
