"""
macOS (Darwin) registry: two-layer YAML rule set for macOS shell commands and
file-path security classification.

Source layer  — ``registries/darwin_data/*.yaml`` (read-only, ships with package)
User layer    — ``~/.arbiteros/instruction_parsing/darwin_registry/*.yaml``
                (override with env ``ARBITEROS_USER_REGISTRY_DIR``)

Public API (module-level forwarding functions):
    classify_exe(exe, subcommand)            → EXEC | WRITE | READ
    classify_exe_risk(exe, subcommand)       → HIGH | UNKNOWN | LOW
    classify_confidentiality(paths)          → HIGH | UNKNOWN | LOW
    classify_trustworthiness(paths)          → HIGH | UNKNOWN | LOW
    register_file_taint(path, trust, conf)
    save_registries()
    get_user_registered_paths()              → List[str]
"""

import os
from typing import List, Optional

from ..types import SecurityLevel
from ._base import RegistrySet

_DARWIN = RegistrySet(
    source_dir=os.path.join(os.path.dirname(__file__), "darwin_data"),
    user_dir=os.environ.get(
        "ARBITEROS_USER_REGISTRY_DIR",
        os.path.join(
            os.path.expanduser("~"),
            ".arbiteros",
            "instruction_parsing",
            "darwin_registry",
        ),
    ),
    name="darwin",
)


def classify_exe(exe: str, subcommand: Optional[str]) -> str:
    return _DARWIN.classify_exe(exe, subcommand)


def classify_exe_risk(exe: str, subcommand: Optional[str]) -> SecurityLevel:
    return _DARWIN.classify_exe_risk(exe, subcommand)


def classify_confidentiality(paths: List[str]) -> SecurityLevel:
    return _DARWIN.classify_confidentiality(paths)


def classify_trustworthiness(paths: List[str]) -> SecurityLevel:
    return _DARWIN.classify_trustworthiness(paths)


def register_file_taint(
    path: str,
    trustworthiness: SecurityLevel,
    confidentiality: SecurityLevel,
) -> None:
    _DARWIN.register_file_taint(path, trustworthiness, confidentiality)


def save_registries() -> None:
    _DARWIN.save_registries()


def get_user_registered_paths() -> List[str]:
    return _DARWIN.get_user_registered_paths()
