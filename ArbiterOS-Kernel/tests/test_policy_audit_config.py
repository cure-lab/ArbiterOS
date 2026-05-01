import copy

from arbiteros_kernel import policy_runtime


def test_resolve_policy_audit_path_from_config(monkeypatch):
    monkeypatch.delenv("ARBITEROS_POLICY_AUDIT_ENABLED", raising=False)
    monkeypatch.delenv("ARBITEROS_POLICY_AUDIT_PATH", raising=False)

    cfg = {
        "audit": {
            "enabled": True,
            "path": "/tmp/policy_audit.jsonl",
        }
    }

    assert policy_runtime._resolve_policy_audit_path(cfg) == "/tmp/policy_audit.jsonl"


def test_resolve_policy_audit_path_disabled_in_config(monkeypatch):
    monkeypatch.delenv("ARBITEROS_POLICY_AUDIT_ENABLED", raising=False)
    monkeypatch.delenv("ARBITEROS_POLICY_AUDIT_PATH", raising=False)

    cfg = {
        "audit": {
            "enabled": False,
            "path": "/tmp/policy_audit.jsonl",
        }
    }

    assert policy_runtime._resolve_policy_audit_path(cfg) == ""


def test_resolve_policy_audit_path_env_path_overrides_config(monkeypatch):
    monkeypatch.delenv("ARBITEROS_POLICY_AUDIT_ENABLED", raising=False)
    monkeypatch.setenv("ARBITEROS_POLICY_AUDIT_PATH", "/tmp/from-env.jsonl")

    cfg = {
        "audit": {
            "enabled": True,
            "path": "/tmp/from-config.jsonl",
        }
    }

    assert policy_runtime._resolve_policy_audit_path(cfg) == "/tmp/from-env.jsonl"


def test_resolve_policy_audit_path_env_enabled_false_forces_disable(monkeypatch):
    monkeypatch.setenv("ARBITEROS_POLICY_AUDIT_ENABLED", "false")
    monkeypatch.setenv("ARBITEROS_POLICY_AUDIT_PATH", "/tmp/from-env.jsonl")

    cfg = {
        "audit": {
            "enabled": True,
            "path": "/tmp/from-config.jsonl",
        }
    }

    assert policy_runtime._resolve_policy_audit_path(cfg) == ""


def test_resolve_policy_audit_path_env_enabled_true_uses_config_path_when_env_path_missing(monkeypatch):
    monkeypatch.setenv("ARBITEROS_POLICY_AUDIT_ENABLED", "true")
    monkeypatch.delenv("ARBITEROS_POLICY_AUDIT_PATH", raising=False)

    cfg = {
        "audit": {
            "enabled": True,
            "path": "/tmp/from-config.jsonl",
        }
    }

    assert policy_runtime._resolve_policy_audit_path(cfg) == "/tmp/from-config.jsonl"
