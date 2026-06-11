import json

from checkpoint_plugin.env.hook_filter import (
    is_hook_config_basename,
    is_hook_config_path,
    merge_plugin_hooks,
    strip_plugin_hooks,
)


def _bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _plugin_hook(event_module: str = "claude_code_hook", name: str = "session_start") -> dict:
    return {
        "type": "command",
        "command": f"/usr/bin/python3 -m checkpoint_plugin.integrations.{event_module} {name}",
    }


def _user_hook(command: str = "echo hi") -> dict:
    return {"type": "command", "command": command}


def test_is_hook_config_basename_recognizes_known_files():
    assert is_hook_config_basename("settings.json", "claude")
    assert is_hook_config_basename("settings.local.json", "claude")
    assert is_hook_config_basename("hooks.json", "codex")
    assert not is_hook_config_basename("settings.json", "codex")
    assert not is_hook_config_basename("auth.json", "codex")


def test_is_hook_config_path_matches_project_paths():
    assert is_hook_config_path("/home/me/proj/.claude/settings.json", "claude")
    assert is_hook_config_path("/home/me/proj/.codex/hooks.json", "codex")
    assert not is_hook_config_path("/home/me/proj/.claude/settings.json", "codex")
    assert not is_hook_config_path("/home/me/proj/CLAUDE.md", "claude")


def test_strip_plugin_hooks_removes_only_plugin_entries():
    blob = _bytes(
        {
            "hooks": {
                "Stop": [
                    {"hooks": [_plugin_hook("claude_code_hook", "turn_end")]},
                    {"hooks": [_user_hook("./run.sh")]},
                ],
                "SessionStart": [
                    {"hooks": [_plugin_hook("claude_code_hook", "session_start")]}
                ],
            },
            "model": "sonnet",
        }
    )

    stripped = strip_plugin_hooks(blob)
    parsed = json.loads(stripped)
    assert parsed["model"] == "sonnet"
    assert "SessionStart" not in parsed["hooks"]
    assert parsed["hooks"]["Stop"] == [{"hooks": [_user_hook("./run.sh")]}]


def test_strip_plugin_hooks_normalizes_to_same_bytes_regardless_of_interpreter():
    a = _bytes(
        {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/usr/bin/python3.11 -m checkpoint_plugin.integrations.claude_code_hook turn_end",
                            }
                        ]
                    }
                ]
            }
        }
    )
    b = _bytes(
        {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/Users/me/.venv/bin/python -m checkpoint_plugin.integrations.claude_code_hook turn_end",
                            }
                        ]
                    }
                ]
            }
        }
    )
    assert strip_plugin_hooks(a) == strip_plugin_hooks(b)


def test_strip_plugin_hooks_passthrough_for_invalid_json():
    blob = b"not json"
    assert strip_plugin_hooks(blob) == blob


def test_strip_plugin_hooks_passthrough_when_no_hooks_key():
    blob = _bytes({"model": "sonnet"})
    assert strip_plugin_hooks(blob) == blob


def test_merge_plugin_hooks_injects_current_plugin_entries():
    current = _bytes(
        {
            "hooks": {
                "Stop": [{"hooks": [_plugin_hook("claude_code_hook", "turn_end")]}]
            }
        }
    )
    target = _bytes({"hooks": {"Stop": [{"hooks": [_user_hook("./run.sh")]}]}, "model": "old"})

    merged = merge_plugin_hooks(current, target)
    parsed = json.loads(merged)
    assert parsed["model"] == "old"
    commands = sorted(
        hook["command"]
        for entry in parsed["hooks"]["Stop"]
        for hook in entry["hooks"]
    )
    assert any("checkpoint_plugin.integrations" in c for c in commands)
    assert "./run.sh" in commands


def test_merge_plugin_hooks_idempotent_when_target_already_has_entry():
    plugin = _plugin_hook("claude_code_hook", "turn_end")
    current = _bytes({"hooks": {"Stop": [{"hooks": [plugin]}]}})
    target = _bytes({"hooks": {"Stop": [{"hooks": [plugin]}]}})

    merged = merge_plugin_hooks(current, target)
    parsed = json.loads(merged)
    assert len(parsed["hooks"]["Stop"]) == 1


def test_merge_plugin_hooks_drops_target_plugin_hooks_when_current_has_none():
    target = _bytes(
        {
            "hooks": {
                "Stop": [{"hooks": [_plugin_hook("claude_code_hook", "turn_end")]}]
            },
            "model": "sonnet",
        }
    )
    current = _bytes({"hooks": {}, "model": "sonnet"})

    merged = merge_plugin_hooks(current, target)
    parsed = json.loads(merged)
    assert parsed["model"] == "sonnet"
    assert parsed["hooks"] == {}


def test_merge_plugin_hooks_returns_target_when_current_unparseable():
    target = _bytes({"hooks": {}})
    assert merge_plugin_hooks(b"garbage", target) == target


def test_merge_plugin_hooks_with_empty_current_yields_target():
    target = _bytes({"hooks": {"Stop": [{"hooks": [_user_hook()]}]}})
    assert merge_plugin_hooks(b"", target) == target
