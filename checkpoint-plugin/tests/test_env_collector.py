from checkpoint_plugin.env.collector import collect_environment
from checkpoint_plugin.env.providers import claude_layout, codex_layout, opencode_layout
from checkpoint_plugin.store import CheckpointStore
import hashlib
import json


def test_collect_environment_with_mock_claude_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    claude = home / ".claude"
    (claude / "memories").mkdir(parents=True)
    (claude / "skills" / "skill-a").mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CLAUDE_MODEL", "sonnet-test")

    (claude / "memories" / "note.md").write_text("memory", encoding="utf-8")
    (claude / "settings.json").write_text('{"permissions": {}}', encoding="utf-8")
    (claude / "skills" / "skill-a" / "SKILL.md").write_text("skill", encoding="utf-8")
    (home / ".claude.json").write_text('{"mcpServers": {"ctx": {"command": "x"}}}', encoding="utf-8")
    (cwd / "CLAUDE.md").write_text("project", encoding="utf-8")

    store = CheckpointStore(tmp_path / "session")
    env = collect_environment(cwd, claude_layout(), store)

    assert env.provider == "claude"
    assert env.model == "sonnet-test"
    assert "note.md" in env.memory_files
    # ~/.claude.json is captured structurally (R2), never as a raw blob.
    assert env.mcp_config is None
    assert env.mcp_servers == {"ctx": "active"}
    assert "settings.json" in env.settings
    assert "user/skill-a/SKILL.md" in env.skills
    assert str(cwd / "CLAUDE.md") in env.project_context


def test_collect_environment_follows_symlinked_skill_dirs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    claude = home / ".claude"
    shared = home / ".cc-switch" / "skills" / "linked-skill"
    (claude / "skills").mkdir(parents=True)
    shared.mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))

    (claude / "skills" / ".DS_Store").write_text("metadata", encoding="utf-8")
    (claude / "skills" / "linked-skill").symlink_to(shared, target_is_directory=True)
    (shared / "SKILL.md").write_text("linked skill", encoding="utf-8")
    (shared / "notes.md").write_text("notes", encoding="utf-8")

    store = CheckpointStore(tmp_path / "session")
    env = collect_environment(cwd, claude_layout(), store)

    assert "user/linked-skill/SKILL.md" in env.skills
    assert "user/linked-skill/notes.md" in env.skills
    assert "user/.DS_Store" in env.skills
    assert env.skill_status == {"linked-skill": "present"}
    assert env.extra["skill_symlinks"] == {
        "user/linked-skill": str(shared),
    }


def test_collect_environment_skips_recursive_skill_symlink(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    claude = home / ".claude"
    skill = claude / "skills" / "loop-skill"
    skill.mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))

    (skill / "SKILL.md").write_text("loop skill", encoding="utf-8")
    (skill / "self").symlink_to(skill, target_is_directory=True)

    store = CheckpointStore(tmp_path / "session")
    env = collect_environment(cwd, claude_layout(), store)

    assert "user/loop-skill/SKILL.md" in env.skills
    assert not any(path.startswith("user/loop-skill/self/") for path in env.skills)
    assert env.extra["skill_symlinks"] == {
        "user/loop-skill/self": str(skill),
    }


def test_collect_codex_structured_env_status(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    codex = home / ".codex"
    (codex / "skills" / "skill-a").mkdir(parents=True)
    (home / ".agents" / "skills" / "global-agent-skill").mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex))

    (cwd / ".agents" / "skills" / "project-skill").mkdir(parents=True)
    (codex / "skills" / "skill-a" / "SKILL.md").write_text("skill", encoding="utf-8")
    (home / ".agents" / "skills" / "global-agent-skill" / "SKILL.md").write_text(
        "global agent skill",
        encoding="utf-8",
    )
    (cwd / ".agents" / "skills" / "project-skill" / "SKILL.md").write_text("project skill", encoding="utf-8")
    (cwd / ".mcp.json").write_text('{"mcpServers":{"project_mcp":{"command":"local"}}}', encoding="utf-8")
    (cwd / ".codex").mkdir()
    (cwd / ".codex" / "config.toml").write_text(
        """
[mcp_servers.project_config_mcp]
command = "local"

[plugins."project-plugin"]
enabled = true
""",
        encoding="utf-8",
    )
    (codex / "config.toml").write_text(
        """
[mcp_servers.context7]
type = "stdio"
command = "npx"

[mcp_servers.disabled_server]
command = "nope"
enabled = false

[plugins."github@openai-curated"]
enabled = true

[plugins."browser@openai-bundled"]
enabled = false

[[skills.config]]
path = "{skill_path}"
enabled = false
""".format(skill_path=(codex / "skills" / "skill-a" / "SKILL.md").as_posix()),
        encoding="utf-8",
    )

    store = CheckpointStore(tmp_path / "session")
    env = collect_environment(cwd, codex_layout(), store)

    assert env.mcp_config is not None
    assert env.mcp_servers == {
        "context7": "active",
        "disabled_server": "inactive",
        "project_config_mcp": "active",
        "project_mcp": "active",
    }
    assert env.plugin_status == {
        "browser@openai-bundled": "inactive",
        "github@openai-curated": "active",
        "project-plugin": "active",
    }
    assert "codex-user/skill-a/SKILL.md" in env.skills
    assert "agent-user/global-agent-skill/SKILL.md" in env.skills
    assert any(key.endswith(".agents/skills/project-skill/SKILL.md") for key in env.skills)
    assert env.skill_status["skill-a"] == "inactive"
    assert env.skill_status["global-agent-skill"] == "present"
    assert env.skill_status["project-skill"] == "present"
    assert any(key.endswith(".mcp.json") for key in env.mcp_configs)


def test_collect_codex_plugin_cache_skills(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    plugin_skill = (
        home
        / ".codex"
        / "plugins"
        / "cache"
        / "openai-bundled"
        / "browser"
        / "26.519.81530"
        / "skills"
        / "browser"
    )
    plugin_manifest = plugin_skill.parent.parent / ".codex-plugin" / "plugin.json"
    plugin_script = plugin_skill.parent.parent / "scripts" / "browser-client.mjs"
    marketplace = home / ".codex" / ".tmp" / "plugins"
    marketplace_manifest = marketplace / ".agents" / "plugins" / "marketplace.json"
    marketplace_plugin = marketplace / "plugins" / "github" / ".codex-plugin" / "plugin.json"
    cwd.mkdir()
    plugin_skill.mkdir(parents=True)
    plugin_manifest.parent.mkdir(parents=True)
    plugin_script.parent.mkdir(parents=True)
    marketplace_manifest.parent.mkdir(parents=True)
    marketplace_plugin.parent.mkdir(parents=True)
    monkeypatch.setenv("TEST_HOME", str(home))

    (plugin_skill / "SKILL.md").write_text("browser skill", encoding="utf-8")
    plugin_manifest.write_text('{"name":"browser"}', encoding="utf-8")
    plugin_script.write_text("export const run = true;\n", encoding="utf-8")
    (home / ".codex" / "config.toml").write_text(
        """
[plugins."browser@openai-bundled"]
enabled = true
""",
        encoding="utf-8",
    )
    marketplace_manifest.write_text(
        """
{
  "name": "openai-curated",
  "plugins": [
    {
      "name": "github",
      "source": {"source": "local", "path": "./plugins/github"}
    }
  ]
}
""",
        encoding="utf-8",
    )
    marketplace_plugin.write_text('{"name":"github"}', encoding="utf-8")

    store = CheckpointStore(tmp_path / "session")
    env = collect_environment(cwd, codex_layout(), store)

    key = "plugin:openai-bundled:browser:26.519.81530/browser/SKILL.md"
    assert key in env.skills
    assert store.load_blob(env.skills[key]) == b"browser skill"
    manifest_key = "codex-plugin-cache/openai-bundled/browser/26.519.81530/.codex-plugin/plugin.json"
    assert manifest_key in env.plugin_files
    assert store.load_blob(env.plugin_files[manifest_key]) == b'{"name":"browser"}'
    script_key = "codex-plugin-cache/openai-bundled/browser/26.519.81530/scripts/browser-client.mjs"
    assert script_key in env.plugin_files
    assert store.load_blob(env.plugin_files[script_key]) == b"export const run = true;\n"
    marketplace_key = "codex-marketplace:openai-curated/.agents/plugins/marketplace.json"
    assert marketplace_key in env.plugin_files
    assert store.load_blob(env.plugin_files[marketplace_key]).lstrip().startswith(b"{")
    marketplace_plugin_key = "codex-marketplace:openai-curated/plugins/github/.codex-plugin/plugin.json"
    assert marketplace_plugin_key in env.plugin_files
    assert store.load_blob(env.plugin_files[marketplace_plugin_key]) == b'{"name":"github"}'
    assert env.skill_status["browser"] == "present"


def test_collect_claude_structured_env_status(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    claude = home / ".claude"
    plugin = claude / "plugins" / "marketplaces" / "official" / "plugins" / "code-review"
    external_plugin = claude / "plugins" / "marketplaces" / "official" / "external_plugins" / "context7"
    (claude / "skills" / "skill-a").mkdir(parents=True)
    plugin.mkdir(parents=True)
    external_plugin.mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))

    (claude / "skills" / "skill-a" / "SKILL.md").write_text("skill", encoding="utf-8")
    (cwd / ".claude" / "skills" / "project-skill").mkdir(parents=True)
    (cwd / ".claude" / "skills" / "project-skill" / "SKILL.md").write_text("project skill", encoding="utf-8")
    (home / ".claude.json").write_text(
        """
{
  "mcpServers": {
    "context7": {"type": "stdio", "command": "npx"}
  },
  "enabledPlugins": ["code-review"],
  "skillOverrides": {"skill-a": false},
  "projects": {
    "%s": {
      "mcpServers": {"project_server": {"command": "local"}},
      "enabledMcpjsonServers": ["enabled_project"],
      "disabledMcpjsonServers": ["disabled_project"],
      "disabledMcpServers": ["context7"]
    }
  }
}
"""
        % cwd.as_posix(),
        encoding="utf-8",
    )

    store = CheckpointStore(tmp_path / "session")
    env = collect_environment(cwd, claude_layout(), store)

    assert env.mcp_servers == {
        "context7": "inactive",
        "disabled_project": "inactive",
        "enabled_project": "active",
        "project_server": "active",
    }
    assert env.skill_status == {
        "project-skill": "present",
        "skill-a": "inactive",
    }
    assert env.plugin_status == {
        "code-review": "active",
        "context7": "present",
    }


def test_collect_opencode_runtime_mcp_status_overrides_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    config_home = home / ".config" / "opencode"
    config_home.mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("OPENCODE_HOME", str(config_home))

    (config_home / "opencode.json").write_text(
        json.dumps(
            {
                "mcp": {
                    "context7": {
                        "type": "local",
                        "command": ["npx", "-y", "@upstash/context7-mcp@latest"],
                        "enabled": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    store = CheckpointStore(tmp_path / "session")
    (store.session_dir / "metadata.json").write_text(
        json.dumps(
            {
                "session_env": {
                    "mcp_status": json.dumps({"context7": {"status": "disabled"}}),
                }
            }
        ),
        encoding="utf-8",
    )

    env = collect_environment(cwd, opencode_layout(), store)

    assert env.mcp_servers == {"context7": "inactive"}


def test_collect_opencode_project_context_and_skills(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    cwd = project / "pkg"
    config_home = home / ".config" / "opencode"
    cwd.mkdir(parents=True)
    (project / ".git").mkdir()
    (config_home / "skills" / "global-skill").mkdir(parents=True)
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("OPENCODE_HOME", str(config_home))

    (project / "AGENTS.md").write_text("agents", encoding="utf-8")
    (project / "opencode.json").write_text('{"mcp":{"root":{"type":"local","command":["x"]}}}', encoding="utf-8")
    (project / "opencode.jsonc").write_text(
        '{\n  // redacted before storage\n  "provider": {"x": {"options": {"apiKey": "secret-value"}}}\n}\n',
        encoding="utf-8",
    )
    (project / ".opencode" / "agent").mkdir(parents=True)
    (project / ".opencode" / "agent" / "build.md").write_text("agent", encoding="utf-8")
    (project / ".opencode" / "commands").mkdir(parents=True)
    (project / ".opencode" / "commands" / "fmt.md").write_text("command", encoding="utf-8")
    (project / ".opencode" / "skills" / "project-skill").mkdir(parents=True)
    (project / ".opencode" / "skills" / "project-skill" / "SKILL.md").write_text("project skill", encoding="utf-8")
    (project / ".opencode" / "skill" / "legacy-skill").mkdir(parents=True)
    (project / ".opencode" / "skill" / "legacy-skill" / "SKILL.md").write_text("legacy skill", encoding="utf-8")
    (config_home / "skills" / "global-skill" / "SKILL.md").write_text("global skill", encoding="utf-8")

    store = CheckpointStore(tmp_path / "session")
    env = collect_environment(cwd, opencode_layout(), store)

    assert str(project / "AGENTS.md") in env.project_context
    assert str(project / "opencode.json") in env.project_context
    assert str(project / "opencode.jsonc") in env.project_context
    assert str(project / ".opencode" / "agent" / "build.md") in env.project_context
    assert str(project / ".opencode" / "commands" / "fmt.md") in env.project_context
    assert any(key.endswith(".opencode/skills/project-skill/SKILL.md") for key in env.skills)
    assert any(key.endswith(".opencode/skill/legacy-skill/SKILL.md") for key in env.skills)
    assert "opencode-user/global-skill/SKILL.md" in env.skills
    stored_jsonc = store.load_blob(env.project_context[str(project / "opencode.jsonc")]).decode("utf-8")
    assert "secret-value" not in stored_jsonc
    assert "***redacted***" in stored_jsonc


def test_collect_opencode_config_precedence_includes_config_content_and_dir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    default_config = home / ".config" / "opencode"
    overlay_config = tmp_path / "opencode-overlay"
    default_config.mkdir(parents=True)
    overlay_config.mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(overlay_config))
    monkeypatch.setenv(
        "OPENCODE_CONFIG_CONTENT",
        json.dumps({"mcp": {"context7": {"enabled": True}, "inline_only": {"enabled": False}}}),
    )

    (default_config / "opencode.json").write_text(
        '{"mcp":{"context7":{"type":"local","command":["default"],"enabled":true}}}',
        encoding="utf-8",
    )
    (overlay_config / "opencode.json").write_text(
        '{"mcp":{"context7":{"type":"local","command":["overlay"],"enabled":false}}}',
        encoding="utf-8",
    )

    store = CheckpointStore(tmp_path / "session")
    env = collect_environment(cwd, opencode_layout(), store)

    assert env.mcp_servers["context7"] == "active"
    assert env.mcp_servers["inline_only"] == "inactive"


def test_collect_opencode_resolved_config_and_saved_runtime_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    external_skills = tmp_path / "opencode-skills"
    cwd.mkdir()
    external_skills.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("OPENCODE_HOME", str(home / ".config" / "opencode"))

    store = CheckpointStore(tmp_path / "session")
    (store.session_dir / "metadata.json").write_text(
        json.dumps(
            {
                "session_env": {
                    "mcp_status": json.dumps({"context7": {"status": "disabled"}}),
                    "resolved_config": json.dumps(
                        {
                            "model": "opencode/test-model",
                            "plugin": ["npm-plugin", ["tuple-plugin", {"flag": True}]],
                            "mcp": {
                                "context7": {"enabled": True},
                                "disabled_by_config": {"enabled": False},
                            },
                            "provider": {"x": {"options": {"apiKey": "secret-value"}}},
                            "skills": {"paths": [str(external_skills)]},
                        }
                    ),
                    "opencode_runtime_env": json.dumps({"OPENCODE_DISABLE_EXTERNAL_SKILLS": "1"}),
                    "opencode_config_content": json.dumps(
                        {"mcp": {"inline_only": {"enabled": False}}}
                    ),
                }
            }
        ),
        encoding="utf-8",
    )
    (external_skills / "custom").mkdir()
    (external_skills / "custom" / "SKILL.md").write_text("custom skill", encoding="utf-8")
    (cwd / ".agents" / "skills" / "ignored").mkdir(parents=True)
    (cwd / ".agents" / "skills" / "ignored" / "SKILL.md").write_text("ignored", encoding="utf-8")
    (home / ".agents" / "skills" / "ignored-global").mkdir(parents=True)
    (home / ".agents" / "skills" / "ignored-global" / "SKILL.md").write_text("ignored", encoding="utf-8")
    (home / ".claude" / "skills" / "ignored-claude").mkdir(parents=True)
    (home / ".claude" / "skills" / "ignored-claude" / "SKILL.md").write_text("ignored", encoding="utf-8")

    env = collect_environment(cwd, opencode_layout(), store)

    assert env.model == "opencode/test-model"
    assert env.mcp_servers == {
        "context7": "inactive",
        "disabled_by_config": "inactive",
    }
    assert env.plugin_status == {"npm-plugin": "active", "tuple-plugin": "active"}
    assert f"opencode-config-skills:{external_skills}/custom/SKILL.md" in env.skills
    assert not any(".agents/skills/ignored" in key for key in env.skills)
    assert "agent-user/ignored-global/SKILL.md" not in env.skills
    assert "claude-user/ignored-claude/SKILL.md" not in env.skills
    assert env.extra["opencode_runtime_env"] == {"OPENCODE_DISABLE_EXTERNAL_SKILLS": "1"}
    assert env.extra["opencode_config_skill_roots"] == [str(external_skills)]
    assert env.extra["opencode_resolved_config"]["provider"]["x"]["options"]["apiKey"] == "***redacted***"
    saved_config = json.loads(env.extra["opencode_config_content"])
    assert saved_config["mcp"]["context7"]["enabled"] is False
    assert "inline_only" not in saved_config["mcp"]


def test_collect_environment_never_stores_secret_files(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    codex = home / ".codex"
    codex.mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex))

    secret = b'{"OPENAI_API_KEY": "sk-must-not-be-stored"}'
    (codex / "auth.json").write_bytes(secret)
    (codex / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
    (cwd / ".env").write_bytes(b"TOKEN=must-not-be-stored\n")
    (cwd / "AGENTS.md").write_text("project rules", encoding="utf-8")

    store = CheckpointStore(tmp_path / "session")
    env = collect_environment(cwd, codex_layout(), store)

    # config.toml is still captured; auth.json/.env are filtered out entirely.
    assert "config.toml" in env.settings
    assert "auth.json" not in env.settings
    assert not any(key.endswith(".env") for key in env.project_context)

    # Defense in depth: the secret bytes never reached the blob store.
    secret_sha = hashlib.sha256(secret).hexdigest()
    assert not store.blob_path(secret_sha).exists()


def test_codex_config_secret_values_redacted_before_storage(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    codex = home / ".codex"
    codex.mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex))

    config = (
        'model = "gpt-test"\n'
        'experimental_bearer_token = "sk-leak-me"\n'
        'trusted_hash = "deadbeef"\n'
        'base_url = "https://api.example.com"\n'
        "\n"
        "[mcp_servers.context7.env]\n"
        'API_KEY = "super-secret-value"\n'
    )
    (codex / "config.toml").write_text(config, encoding="utf-8")

    store = CheckpointStore(tmp_path / "session")
    env = collect_environment(cwd, codex_layout(), store)

    assert "config.toml" in env.settings
    stored = store.load_blob(env.settings["config.toml"]).decode("utf-8")
    # Secret-shaped values are redacted; non-secret keys are preserved verbatim.
    assert "sk-leak-me" not in stored
    assert "deadbeef" not in stored
    assert "super-secret-value" not in stored
    assert "***redacted***" in stored
    assert 'model = "gpt-test"' in stored
    assert 'base_url = "https://api.example.com"' in stored
    # The verbatim (secret-bearing) bytes never landed in the blob store.
    raw_sha = hashlib.sha256(config.encode("utf-8")).hexdigest()
    assert not store.blob_path(raw_sha).exists()


def test_codex_effort_pinned_from_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    codex = home / ".codex"
    codex.mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.delenv("CLAUDE_EFFORT", raising=False)
    (codex / "config.toml").write_text(
        'model = "gpt-test"\nmodel_reasoning_effort = "high"\n', encoding="utf-8"
    )

    store = CheckpointStore(tmp_path / "session")
    env = collect_environment(cwd, codex_layout(), store)

    assert env.effort == "high"


def test_codex_history_captured_as_blob_ref(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    codex = home / ".codex"
    codex.mkdir(parents=True)
    cwd.mkdir()
    monkeypatch.setenv("TEST_HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    history = b'{"session_id":"s","ts":1,"text":"hi"}\n'
    (codex / "history.jsonl").write_bytes(history)

    store = CheckpointStore(tmp_path / "session")
    env = collect_environment(cwd, codex_layout(), store)

    ref = env.extra.get("codex_history_ref")
    assert ref is not None
    assert store.load_blob(ref) == history
