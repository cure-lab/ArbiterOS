from checkpoint_plugin.path_utils import rewrite_path_references_text


def test_rewrite_path_references_matches_complete_path_references():
    source = "/tmp/home/.codex"
    dest = "/tmp/runtime/codex"

    rewritten = rewrite_path_references_text(
        "\n".join(
            [
                f'exact = "{source}"',
                f'child = "{source}/config.toml"',
                f'starts_with_path = "{source}/first" and second = "{source}/second"',
                f'suffix = "{source}-backup"',
                f'dotted = "{source}.bak"',
                f'embedded = "/prefix{source}/config.toml"',
            ]
        ),
        {source: dest},
    )

    assert f'exact = "{dest}"' in rewritten
    assert f'child = "{dest}/config.toml"' in rewritten
    assert f'starts_with_path = "{dest}/first" and second = "{dest}/second"' in rewritten
    assert f'suffix = "{source}-backup"' in rewritten
    assert f'dotted = "{source}.bak"' in rewritten
    assert f'embedded = "/prefix{source}/config.toml"' in rewritten


def test_rewrite_path_references_does_not_rewrite_replacements_again():
    home = "/tmp/home"
    source = f"{home}/.codex"
    dest = f"{home}/.checkpoint-plugin/env-state/s1/codex"
    external_home = f"{home}/.checkpoint-plugin/env-state/s1/external/tmp/home"

    rewritten = rewrite_path_references_text(
        "\n".join(
            [
                f'CODEX_HOME = "{source}"',
                f'MCP_CONFIG = "{home}/.mcp.json"',
            ]
        ),
        {home: external_home, source: dest},
    )

    assert f'CODEX_HOME = "{dest}"' in rewritten
    assert f'CODEX_HOME = "{external_home}/.checkpoint-plugin' not in rewritten
    assert f'MCP_CONFIG = "{external_home}/.mcp.json"' in rewritten
