import pytest

import arbiteros_kernel.instruction_parsing.registries.linux as lr


@pytest.fixture(autouse=True)
def isolated_user_registry(tmp_path, monkeypatch):
    """Redirect the user-layer registry to a per-test temp directory.

    Prevents tests from reading or polluting ~/.arbiteros/... and ensures
    each test starts with an empty user registry.
    """
    user_dir = str(tmp_path / "linux_registry")
    monkeypatch.setenv("ARBITEROS_USER_REGISTRY_DIR", user_dir)
    monkeypatch.setattr(lr._LINUX, "_user_dir", user_dir)

    # Reset all cached in-memory state so each test loads fresh from disk
    monkeypatch.setattr(lr._LINUX, "_exe_user", None)
    monkeypatch.setattr(lr._LINUX, "_file_conf_user", None)
    monkeypatch.setattr(lr._LINUX, "_file_trust_user", None)
    monkeypatch.setattr(lr._LINUX, "_exe_risk_user", None)
    monkeypatch.setattr(lr._LINUX, "_exe_dirty", False)
    monkeypatch.setattr(lr._LINUX, "_file_conf_dirty", False)
    monkeypatch.setattr(lr._LINUX, "_file_trust_dirty", False)
    monkeypatch.setattr(lr._LINUX, "_exe_risk_dirty", False)

    yield
