from checkpoint_plugin.env.differ import diff_environments, render_diff
from checkpoint_plugin.types import EnvironmentState


def test_diff_environments_includes_structured_status_changes():
    current = EnvironmentState(
        provider="codex",
        mcp_servers={"context7": "inactive"},
        skill_status={"skill-a": "active"},
        plugin_status={"github": "active"},
    )
    target = EnvironmentState(
        provider="codex",
        mcp_servers={"context7": "active"},
        skill_status={"skill-a": "inactive"},
        plugin_status={"github": "inactive"},
    )

    text = render_diff(diff_environments(current, target), current, target)

    assert "MCP servers" in text
    assert "Skill status" in text
    assert "Plugin status" in text
    assert "~ context7" in text
    assert "~ skill-a" in text
    assert "~ github" in text


def test_diff_environments_ignores_plugin_hook_only_settings_diff():
    blobs = {
        "sha-without": (
            b'{\n  "hooks": {},\n  "model": "sonnet"\n}\n'
        ),
        "sha-with": (
            b'{\n  "hooks": {\n'
            b'    "Stop": [\n'
            b'      {"hooks": [{"command": "python -m checkpoint_plugin.integrations.claude_code_hook turn_end", "type": "command"}]}\n'
            b'    ]\n  },\n  "model": "sonnet"\n}\n'
        ),
    }

    def loader(sha: str) -> bytes:
        return blobs[sha]

    current = EnvironmentState(provider="claude", settings={"settings.json": "sha-with"})
    target = EnvironmentState(provider="claude", settings={"settings.json": "sha-without"})

    diff = diff_environments(
        current, target, blob_loader=loader, ignore_plugin_hooks=True
    )
    assert not diff.settings.has_changes()

    diff_default = diff_environments(current, target, blob_loader=loader)
    assert diff_default.settings.modified == ["settings.json"]


def test_diff_environments_still_surfaces_real_settings_changes():
    blobs = {
        "sha-a": b'{"model": "sonnet"}',
        "sha-b": b'{"model": "opus"}',
    }

    def loader(sha: str) -> bytes:
        return blobs[sha]

    current = EnvironmentState(provider="claude", settings={"settings.json": "sha-a"})
    target = EnvironmentState(provider="claude", settings={"settings.json": "sha-b"})

    diff = diff_environments(
        current, target, blob_loader=loader, ignore_plugin_hooks=True
    )
    assert diff.settings.modified == ["settings.json"]
