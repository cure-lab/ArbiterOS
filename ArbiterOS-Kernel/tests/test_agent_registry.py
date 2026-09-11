from arbiteros_kernel.agent_registry import (
    drop_tools_by_type,
    load_agent_profiles,
    resolve_agent_profile,
    resolve_upstream_compat_flags,
    validate_request_route,
)
from arbiteros_kernel.policy_check import split_model_agent_role


def test_split_model_agent_role_two_segments():
    route, agent, role = split_model_agent_role("gpt-5.2-chat-latest;openclaw")
    assert route == "gpt-5.2-chat-latest"
    assert agent == "openclaw"
    assert role is None


def test_split_model_agent_role_three_segments():
    route, agent, role = split_model_agent_role(
        "claude-sonnet-4-5-20250929;claude_code;planner"
    )
    assert route == "claude-sonnet-4-5-20250929"
    assert agent == "claude_code"
    assert role == "planner"


def test_split_model_agent_role_bare_model():
    route, agent, role = split_model_agent_role("gpt-5.2-chat-latest")
    assert route == "gpt-5.2-chat-latest"
    assert agent is None
    assert role is None


def test_validate_request_route_requires_agent_name():
    err, route, agent, role = validate_request_route("gpt-5.2-chat-latest")
    assert err is not None
    assert route is None
    assert agent is None
    assert role is None


def test_validate_request_route_accepts_agent_only():
    err, route, agent, role = validate_request_route("gpt-5.2-chat-latest;codex")
    assert err is None
    assert route == "gpt-5.2-chat-latest"
    assert agent == "codex"
    assert role is None


def test_agent_profiles_load_builtin_agents():
    profiles = load_agent_profiles(force_reload=True)
    assert set(profiles) >= {
        "openclaw",
        "codex",
        "claude_code",
        "nanobot",
        "hermes",
        "bank",
    }


def test_claude_code_profile_enables_sidecar():
    profile = resolve_agent_profile("claude_code")
    assert profile.depends_on_sidecar_enabled is True
    assert profile.upstream_compat_enabled is True


def test_openclaw_profile_disables_sidecar():
    profile = resolve_agent_profile("openclaw")
    assert profile.depends_on_sidecar_enabled is False
    assert profile.upstream_compat_enabled is False


def test_bank_profile_disables_sidecar():
    profile = resolve_agent_profile("bank")
    assert profile.depends_on_sidecar_enabled is False
    assert profile.upstream_compat_enabled is False


def test_validate_request_route_accepts_bank_role():
    err, route, agent, role = validate_request_route("gpt-5.5;bank;bank_demo")
    assert err is None
    assert route == "gpt-5.5"
    assert agent == "bank"
    assert role == "bank_demo"


def test_codex_gpt5_drops_image_generation_tool():
    load_agent_profiles(force_reload=True)
    flags = resolve_upstream_compat_flags("gpt-5", agent_name="codex")
    assert flags["drop_tool_types"] == ["image_generation"]
    payload = {
        "model": "gpt-5",
        "tools": [
            {"type": "function", "name": "exec_command"},
            {"type": "web_search"},
            {"type": "image_generation"},
        ],
    }
    out = drop_tools_by_type(payload, flags["drop_tool_types"])
    assert [t.get("type") for t in out["tools"]] == ["function", "web_search"]


def test_codex_gpt55_drops_image_generation_tool():
    load_agent_profiles(force_reload=True)
    flags = resolve_upstream_compat_flags("gpt-5.5", agent_name="codex")
    assert flags["drop_tool_types"] == ["image_generation"]
    assert flags["force_non_stream"] is True
    payload = {
        "model": "gpt-5.5",
        "tools": [
            {"type": "function", "name": "exec_command"},
            {"type": "web_search"},
            {"type": "image_generation"},
        ],
    }
    out = drop_tools_by_type(payload, flags["drop_tool_types"])
    assert [t.get("type") for t in out["tools"]] == ["function", "web_search"]
