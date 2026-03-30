import pytest

import arbiteros_kernel.instruction_parsing.tool_parsers.linux_registry as lr


@pytest.fixture(autouse=True)
def isolated_user_registry(tmp_path, monkeypatch):
    """Redirect the user-layer registry to a per-test temp directory.

    Prevents tests from reading or polluting ~/.arbiteros/... and ensures
    each test starts with an empty user registry.
    """
    user_dir = str(tmp_path / "linux_registry")
    monkeypatch.setenv("ARBITEROS_USER_REGISTRY_DIR", user_dir)
    monkeypatch.setattr(lr, "_USER_REGISTRY_DIR", user_dir)

    # Reset all cached in-memory state so each test loads fresh from disk
    monkeypatch.setattr(lr, "_EXE_USER", None)
    monkeypatch.setattr(lr, "_FILE_CONF_USER", None)
    monkeypatch.setattr(lr, "_FILE_TRUST_USER", None)
    monkeypatch.setattr(lr, "_EXE_RISK_USER", None)
    monkeypatch.setattr(lr, "_EXE_DIRTY", False)
    monkeypatch.setattr(lr, "_FILE_CONF_DIRTY", False)
    monkeypatch.setattr(lr, "_FILE_TRUST_DIRTY", False)
    monkeypatch.setattr(lr, "_EXE_RISK_DIRTY", False)

    yield
