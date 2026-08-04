import shlex
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_default_proxy_task_binds_to_loopback() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    command = pyproject["tool"]["poe"]["tasks"]["litellm-proxy"]
    arguments = shlex.split(command)
    host_index = arguments.index("--host")

    assert arguments[host_index + 1] == "127.0.0.1"
