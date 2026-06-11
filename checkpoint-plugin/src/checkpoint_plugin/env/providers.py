"""Provider-specific environment layouts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


RESUME_SESSION_ID = "{session_id}"
RESUME_RUNTIME_ARGS = "{runtime_args}"


@dataclass(frozen=True)
class ProviderResumePolicy:
    allowed_env: frozenset[str]
    home_env_keys: tuple[str, ...]
    command_template: tuple[str, ...]
    data_dir_env_key: str | None = None
    data_dir_name: str | None = None
    path_env_keys: frozenset[str] = frozenset()
    runtime_env_extra: str | None = None
    runtime_env_skip_keys: frozenset[str] = frozenset()
    preflight_kind: str = "none"
    runtime_arg_fields: tuple[tuple[str, str], ...] = ()
    runtime_json_config_arg_fields: tuple[tuple[str, str, str], ...] = ()


_OPENCODE_ALLOWED_RUNTIME_ENV = frozenset(
    {
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
        "OPENCODE_TUI_CONFIG",
        "OPENCODE_DATA_DIR",
        "OPENCODE_DISABLE_PROJECT_CONFIG",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS",
        "OPENCODE_DISABLE_CLAUDE_CODE",
        "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS",
        "OPENCODE_DISABLE_AUTOCOMPACT",
        "OPENCODE_DISABLE_PRUNE",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS",
        "OPENCODE_PURE",
        "OPENCODE_WORKSPACE_ID",
        "OPENCODE_EXPERIMENTAL_WORKSPACES",
        "OPENCODE_PERMISSION",
    }
)


CODEX_RESUME_POLICY = ProviderResumePolicy(
    allowed_env=frozenset({"CODEX_HOME"}),
    home_env_keys=("CODEX_HOME",),
    command_template=("codex", "resume", RESUME_RUNTIME_ARGS, RESUME_SESSION_ID),
    runtime_arg_fields=(("model", "--model"),),
    runtime_json_config_arg_fields=(("effort", "-c", "model_reasoning_effort"),),
)

CLAUDE_RESUME_POLICY = ProviderResumePolicy(
    allowed_env=frozenset({"CLAUDE_CONFIG_DIR", "CLAUDE_HOME"}),
    home_env_keys=("CLAUDE_CONFIG_DIR", "CLAUDE_HOME"),
    command_template=("claude", RESUME_RUNTIME_ARGS, "--resume", RESUME_SESSION_ID),
    runtime_arg_fields=(
        ("model", "--model"),
        ("effort", "--effort"),
        ("permission_mode", "--permission-mode"),
    ),
)

OPENCODE_RESUME_POLICY = ProviderResumePolicy(
    allowed_env=_OPENCODE_ALLOWED_RUNTIME_ENV,
    home_env_keys=("OPENCODE_CONFIG_DIR",),
    command_template=("opencode", "--session", RESUME_SESSION_ID),
    data_dir_env_key="OPENCODE_DATA_DIR",
    data_dir_name="opencode-data",
    path_env_keys=frozenset({"OPENCODE_CONFIG", "OPENCODE_TUI_CONFIG"}),
    runtime_env_extra="opencode_runtime_env",
    runtime_env_skip_keys=frozenset({"OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR", "OPENCODE_DATA_DIR"}),
    preflight_kind="opencode_import",
)


@dataclass(frozen=True)
class ProviderLayout:
    name: str
    home: Path
    memory_dir: Path | None
    mcp_config: Path | None
    mcp_config_files: list[Path]
    settings_files: list[Path]
    skills_dirs: dict[str, Path]
    project_files: list[str]
    resume_policy: ProviderResumePolicy | None = None


def _home() -> Path:
    return Path(os.environ.get("TEST_HOME", str(Path.home()))).expanduser()


def _directory_project_file(path: Path | str) -> str:
    return str(path).rstrip("/\\") + os.sep


def claude_layout() -> ProviderLayout:
    home = _home()
    claude_home = home / ".claude"
    managed_root = Path("/Library/Application Support/ClaudeCode") if os.name != "nt" else Path.home()
    return ProviderLayout(
        name="claude",
        home=claude_home,
        memory_dir=claude_home / "memories",
        # NOTE: ~/.claude.json is deliberately NOT blob-stored or restored. It is
        # global cross-project state (every project's history, identity, oauth,
        # onboarding); restoring it wholesale on resume would revert unrelated
        # projects. Its behavior-relevant subset (mcpServers, skill/plugin
        # enablement) is captured structurally in EnvironmentState instead.
        mcp_config=None,
        mcp_config_files=[
            managed_root / "managed-mcp.json",
        ],
        settings_files=[
            managed_root / "managed-settings.json",
            managed_root / "managed-mcp.json",
            claude_home / "settings.json",
            claude_home / "settings.local.json",
            claude_home / "config.json",
            claude_home / "CLAUDE.md",
            claude_home / "rules.json",
        ],
        skills_dirs={
            "user": claude_home / "skills",
        },
        resume_policy=CLAUDE_RESUME_POLICY,
        project_files=[
            "CLAUDE.md",
            "CLAUDE.local.md",
            ".mcp.json",
            ".claude/CLAUDE.md",
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".claude/memory",
            ".claude/skills",
            ".claude/agents",
            ".claude/commands",
            ".claude/output-styles",
        ],
    )


def codex_layout() -> ProviderLayout:
    home = _home()
    codex_home = Path(os.environ.get("CODEX_HOME", str(home / ".codex"))).expanduser()
    system_codex = Path("/etc/codex") if os.name != "nt" else codex_home
    return ProviderLayout(
        name="codex",
        home=codex_home,
        memory_dir=codex_home / "memories",
        mcp_config=codex_home / "config.toml",
        mcp_config_files=[
            system_codex / "managed_config.toml",
            system_codex / "requirements.toml",
            codex_home / "config.toml",
            home / ".mcp.json",
        ],
        settings_files=[
            system_codex / "managed_config.toml",
            system_codex / "requirements.toml",
            codex_home / "config.toml",
            codex_home / "AGENTS.md",
            codex_home / "hooks.json",
            codex_home / "rules.json",
        ],
        skills_dirs={
            "codex-user": codex_home / "skills",
            "agent-user": home / ".agents" / "skills",
            "codex-admin": Path("/etc/codex/skills"),
        },
        resume_policy=CODEX_RESUME_POLICY,
        project_files=[
            "AGENTS.override.md",
            "AGENTS.md",
            ".mcp.json",
            ".codex/config.toml",
            ".codex/hooks.json",
            ".codex/requirements.toml",
            ".codex/rules",
            ".codex/skills",
            ".agents/skills",
        ],
    )


def opencode_layout() -> ProviderLayout:
    home = _home()
    default_opencode_home = (Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))) / "opencode").expanduser()
    # OpenCode's OPENCODE_CONFIG_DIR is the actual config directory
    # (Global.Path.config), not the XDG parent. OPENCODE_HOME is kept for
    # backwards-compatible hook installer tests and older plugin setups.
    opencode_home = Path(
        os.environ.get("OPENCODE_CONFIG_DIR")
        or os.environ.get("OPENCODE_HOME")
        or default_opencode_home
    ).expanduser()
    config_homes = []
    if os.environ.get("OPENCODE_CONFIG_DIR") and default_opencode_home != opencode_home:
        config_homes.append(default_opencode_home)
    config_homes.append(opencode_home)
    custom_config = os.environ.get("OPENCODE_CONFIG")
    custom_tui_config = os.environ.get("OPENCODE_TUI_CONFIG")
    custom_config_files = [Path(custom_config).expanduser()] if custom_config else []
    custom_tui_files = [Path(custom_tui_config).expanduser()] if custom_tui_config else []
    opencode_config_files = [
        path
        for config_home in config_homes
        for path in (
            config_home / "opencode.json",
            config_home / "opencode.jsonc",
            config_home / "config.json",
        )
    ]
    opencode_settings_files = [
        path
        for config_home in config_homes
        for path in (
            config_home / "opencode.json",
            config_home / "opencode.jsonc",
            config_home / "config.json",
            config_home / "tui.json",
            config_home / "tui.jsonc",
        )
    ]
    opencode_plugin_files = [
        path
        for config_home in config_homes
        for path in (
            config_home / "plugin" / "checkpoint.ts",
            config_home / "plugin" / "checkpoint.js",
            config_home / "plugins" / "checkpoint.ts",
            config_home / "plugins" / "checkpoint.js",
        )
    ]
    skills_dirs = {
        "opencode-user": opencode_home / "skills",
        "opencode-user-singular": opencode_home / "skill",
        "opencode-home": home / ".opencode" / "skills",
        "opencode-home-singular": home / ".opencode" / "skill",
        "agent-user": home / ".agents" / "skills",
    }
    if not _truthy_env("OPENCODE_DISABLE_CLAUDE_CODE") and not _truthy_env("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"):
        skills_dirs["claude-user"] = home / ".claude" / "skills"
    if default_opencode_home != opencode_home:
        skills_dirs["opencode-default"] = default_opencode_home / "skills"
        skills_dirs["opencode-default-singular"] = default_opencode_home / "skill"
    global_project_files = [
        _directory_project_file(path)
        for config_home in config_homes
        for path in (
            config_home / "agent",
            config_home / "agents",
            config_home / "command",
            config_home / "commands",
            config_home / "mode",
            config_home / "modes",
            config_home / "plugin",
            config_home / "plugins",
            config_home / "tool",
            config_home / "tools",
        )
    ]

    return ProviderLayout(
        name="opencode",
        home=opencode_home,
        memory_dir=opencode_home / "memories",
        # OpenCode config files - supports both .json and .jsonc formats
        mcp_config=opencode_home / "opencode.json",
        mcp_config_files=[
            *opencode_config_files,
            *custom_config_files,
        ],
        settings_files=[
            *opencode_settings_files,
            *custom_config_files,
            *custom_tui_files,
            # TypeScript plugin files for checkpoint integration
            *opencode_plugin_files,
        ],
        skills_dirs=skills_dirs,
        resume_policy=OPENCODE_RESUME_POLICY,
        project_files=[
            # OpenCode instruction/rule files (plus Claude-compatible prompt files)
            "AGENTS.md",
            "CLAUDE.md",
            "CONTEXT.md",
            # Project-local OpenCode configuration
            "opencode.json",
            "opencode.jsonc",
            "tui.json",
            "tui.jsonc",
            ".opencode/opencode.json",
            ".opencode/opencode.jsonc",
            ".opencode/tui.json",
            ".opencode/tui.jsonc",
            ".opencode/env.d.ts",
            ".opencode/agent/*.md",
            ".opencode/agents/*.md",
            ".opencode/command/*.md",
            ".opencode/commands/*.md",
            ".opencode/mode/*.md",
            ".opencode/modes/*.md",
            ".opencode/skill/",
            ".opencode/skills/",
            ".opencode/glossary/",
            ".opencode/themes/",
            ".opencode/plugin/",
            ".opencode/plugins/",
            ".opencode/tool/",
            ".opencode/tools/",
            # Project-local checkpoint plugin
            ".opencode/plugin/checkpoint.ts",
            ".opencode/plugin/checkpoint.js",
            ".opencode/plugins/checkpoint.ts",
            ".opencode/plugins/checkpoint.js",
            # Global OpenCode commands/agents/modes live under the config dir.
            *global_project_files,
            _directory_project_file(home / ".opencode"),
            ".agents/skills/",
        ],
    )


def _truthy_env(name: str) -> bool:
    value = os.environ.get(name, "").lower()
    return value in {"1", "true"}


def generic_layout() -> ProviderLayout:
    home = _home()
    return ProviderLayout(
        name="generic",
        home=home,
        memory_dir=None,
        mcp_config=None,
        mcp_config_files=[],
        settings_files=[],
        skills_dirs={},
        project_files=["AGENTS.md", "CLAUDE.md", ".mcp.json"],
    )


def layout_for_provider(name: str) -> ProviderLayout:
    normalized = name.strip().lower()
    if normalized == "claude":
        return claude_layout()
    if normalized == "codex":
        return codex_layout()
    if normalized == "opencode":
        return opencode_layout()
    return generic_layout()


def resume_policy_for_provider(name: str) -> ProviderResumePolicy | None:
    normalized = name.strip().lower()
    if normalized == "claude":
        return CLAUDE_RESUME_POLICY
    if normalized == "codex":
        return CODEX_RESUME_POLICY
    if normalized == "opencode":
        return OPENCODE_RESUME_POLICY
    return None


def detect_provider(cwd: Path) -> ProviderLayout:
    env_provider = os.environ.get("CHECKPOINT_PROVIDER") or os.environ.get("CLAUDE_PROVIDER")
    if env_provider:
        return layout_for_provider(env_provider)

    _PROVIDER_ENV_MARKERS = [
        (["OPENCODE_PROVIDER", "OPENCODE_CONFIG_DIR", "OPENCODE_DB", "OPENCODE_WORKSPACE_ID", "OPENCODE_CLIENT"], opencode_layout),
        (["CLAUDE_SESSION_ID", "CLAUDE_PROJECT_DIR"], claude_layout),
        (["CODEX_HOME", "CODEX_SESSION_ID"], codex_layout),
    ]

    for env_vars, layout_fn in _PROVIDER_ENV_MARKERS:
        if any(os.environ.get(var) for var in env_vars):
            return layout_fn()

    cwd = cwd.resolve()
    _PROVIDER_FILE_MARKERS = [
        ([".opencode", "opencode.json", "opencode.jsonc"], opencode_layout),
        (["CLAUDE.md", ".claude"], claude_layout),
        (["AGENTS.md", ".codex"], codex_layout),
    ]

    for markers, layout_fn in _PROVIDER_FILE_MARKERS:
        if any((path / marker).exists() for path in (cwd, *cwd.parents) for marker in markers):
            return layout_fn()

    return generic_layout()
