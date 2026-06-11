import json
import sys

from checkpoint_plugin.cli import main
from checkpoint_plugin.integrations.hook_installer import install_hooks, uninstall_hooks


def test_install_and_uninstall_claude_hooks(tmp_path, monkeypatch):
    home = tmp_path / "home"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"theme": "dark"}\n', encoding="utf-8")
    monkeypatch.setenv("TEST_HOME", str(home))

    first = install_hooks("claude")
    second = install_hooks("claude")

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert first[0].changed is True
    assert second[0].changed is False
    assert data["theme"] == "dark"
    assert len(data["hooks"]["SessionStart"]) == 1
    assert "PostToolUse" not in data["hooks"]
    assert len(data["hooks"]["SubagentStop"]) == 1
    subagent_command = data["hooks"]["SubagentStop"][0]["hooks"][0]["command"]
    assert subagent_command.endswith("checkpoint_plugin.integrations.claude_code_hook subagent_end")
    command = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert command.startswith(sys.executable)
    assert command.endswith("checkpoint_plugin.integrations.claude_code_hook session_start")

    removed = uninstall_hooks("claude")

    data = json.loads(settings.read_text(encoding="utf-8"))
    assert removed[0].changed is True
    assert data == {"theme": "dark", "hooks": {}}


def test_install_and_uninstall_codex_hooks(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("TEST_HOME", str(home))

    first = install_hooks("codex")
    second = install_hooks("codex")

    hooks_path = home / ".codex" / "hooks.json"
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert first[0].changed is True
    assert second[0].changed is False
    assert first[0].path == hooks_path
    assert "PostToolUse" not in data["hooks"]
    command = data["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert command.startswith(sys.executable)
    assert command.endswith("checkpoint_plugin.integrations.codex_hook turn_end")

    removed = uninstall_hooks("codex")

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert removed[0].changed is True
    assert data == {"hooks": {}}


def test_reinstall_replaces_legacy_python_hook_command(tmp_path, monkeypatch):
    home = tmp_path / "home"
    hooks_path = home / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "python -m checkpoint_plugin.integrations.codex_hook turn_end",
                                }
                            ]
                        }
                    ]
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_HOME", str(home))

    result = install_hooks("codex")

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entries in data["hooks"].values()
        for entry in entries
        for hook in entry["hooks"]
    ]
    assert result[0].changed is True
    assert "python -m checkpoint_plugin.integrations.codex_hook turn_end" not in commands
    assert any(command.startswith(sys.executable) for command in commands)


def test_reinstall_removes_checkpoint_post_tool_use_hook(tmp_path, monkeypatch):
    home = tmp_path / "home"
    hooks_path = home / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    command = f"{sys.executable} -m checkpoint_plugin.integrations.codex_hook turn_end"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": command}],
                        }
                    ],
                    "Stop": [
                        {
                            "hooks": [{"type": "command", "command": command}],
                        }
                    ],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_HOME", str(home))

    result = install_hooks("codex")

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert result[0].changed is True
    assert "PostToolUse" not in data["hooks"]
    assert "Stop" in data["hooks"]


def test_uninstall_keeps_unrelated_hooks_and_settings(tmp_path, monkeypatch):
    home = tmp_path / "home"
    hooks_path = home / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    checkpoint_command = (
        f"{sys.executable} -m checkpoint_plugin.integrations.codex_hook turn_end"
    )
    other_stop_command = "/usr/local/bin/custom-stop-hook"
    other_session_command = "/usr/local/bin/custom-session-hook"
    hooks_path.write_text(
        json.dumps(
            {
                "theme": "dark",
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [
                                {"type": "command", "command": other_session_command}
                            ],
                        }
                    ],
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": checkpoint_command},
                                {"type": "command", "command": other_stop_command},
                            ]
                        }
                    ],
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/usr/local/bin/custom-prompt-hook",
                                }
                            ]
                        }
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_HOME", str(home))

    result = uninstall_hooks("codex")

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert result[0].changed is True
    assert data["theme"] == "dark"
    assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == other_session_command
    assert data["hooks"]["Stop"] == [
        {"hooks": [{"type": "command", "command": other_stop_command}]}
    ]
    assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "/usr/local/bin/custom-prompt-hook"


def test_hooks_cli_installs_all(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setenv("TEST_HOME", str(home))

    assert main(["hooks", "install"]) == 0

    output = capsys.readouterr().out
    assert "claude: updated" in output
    assert "codex: updated" in output
    assert (home / ".claude" / "settings.json").exists()
    assert (home / ".codex" / "hooks.json").exists()
