"""Read and patch Codex sandbox settings in ~/.codex/config.toml."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

TOP_LEVEL_KEYS = frozenset(
    {"sandbox_mode", "approval_policy", "default_permissions", "model", "model_provider"}
)
CONFIG_PATH = Path.home() / ".codex" / "config.toml"
TABLE = "sandbox_workspace_write"
WIZARD_MARKER = "# --- sandbox_wizard managed ---"

PERM_SECTIONS = (
    "permissions.workspace.filesystem",
    "permissions.workspace.network",
    "permissions.workspace.network.domains",
)

NetworkPolicy = Literal["off", "open", "allowlist"]


@dataclass
class SandboxSettings:
    sandbox_mode: str = "read-only"
    approval_policy: str = "on-request"
    network_access: bool = False
    writable_roots: list[str] = field(default_factory=list)
    exclude_tmpdir_env_var: bool = False
    exclude_slash_tmp: bool = False
    deny_globs: list[str] = field(default_factory=list)
    deny_paths: list[str] = field(default_factory=list)
    network_policy: NetworkPolicy = "off"
    allowed_domains: list[str] = field(default_factory=list)
    denied_domains: list[str] = field(default_factory=list)

    def uses_fine_permissions(self) -> bool:
        return bool(
            self.deny_globs
            or self.deny_paths
            or self.network_policy == "allowlist"
            or self.denied_domains
        )

    def summary_lines(self) -> list[str]:
        lines = [
            f"sandbox_mode      = {self.sandbox_mode}",
            f"approval_policy   = {self.approval_policy}",
        ]
        if self.sandbox_mode == "workspace-write":
            lines.extend(
                [
                    f"network_access    = {self.network_access}",
                    f"writable_roots    = {self.writable_roots or '[]'}",
                    f"exclude_tmpdir    = {self.exclude_tmpdir_env_var}",
                    f"exclude /tmp      = {self.exclude_slash_tmp}",
                ]
            )
        if self.deny_globs:
            lines.append(f"deny_globs        = {self.deny_globs}")
        if self.deny_paths:
            lines.append(f"deny_paths        = {self.deny_paths}")
        if self.network_policy != "off":
            lines.append(f"network_policy    = {self.network_policy}")
        if self.allowed_domains:
            lines.append(f"allowed_domains   = {self.allowed_domains}")
        if self.denied_domains:
            lines.append(f"denied_domains    = {self.denied_domains}")
        if self.uses_fine_permissions():
            lines.append("fine_permissions  = enabled (permissions.workspace.*)")
        return lines


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"true", "1", "yes"}


def _parse_string_scalar(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    return raw


def _parse_string_list(raw: str) -> list[str]:
    raw = raw.strip()
    if raw == "[]":
        return []
    if not raw.startswith("[") or not raw.endswith("]"):
        return []
    inner = raw[1:-1].strip()
    if not inner:
        return []
    out: list[str] = []
    for part in re.findall(r'"((?:\\.|[^"\\])*)"', inner):
        out.append(part.replace('\\"', '"').replace("\\\\", "\\"))
    return out


def _parse_kv_pairs(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in re.findall(r'"((?:\\.|[^"\\])*)"\s*=\s*"([^"]*)"', raw):
        out[key.replace('\\"', '"').replace("\\\\", "\\")] = val
    return out


def _read_config_values(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    top: dict[str, Any] = {}
    table: dict[str, Any] = {}
    in_ws = False
    current_section: str | None = None
    section_data: dict[str, Any] = {}
    scalar = re.compile(r'^("(?:\\.|[^"\\])*"|[A-Za-z0-9_.]+)\s*=\s*(.+)$')

    def flush_section() -> None:
        nonlocal current_section, section_data
        if current_section:
            top[current_section] = dict(section_data)
        current_section = None
        section_data = {}

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            flush_section()
            sec = stripped[1:-1]
            if sec == TABLE:
                in_ws = True
                continue
            in_ws = False
            current_section = sec
            continue
        m = scalar.match(stripped)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if key.startswith('"') and key.endswith('"'):
            key = _parse_string_scalar(key)
        if key in TOP_LEVEL_KEYS:
            flush_section()
            in_ws = False
            top[key] = _parse_string_scalar(value)
            continue
        if current_section:
            if key == "writable_roots":
                section_data[key] = _parse_string_list(value)
            elif key in {"network_access", "exclude_tmpdir_env_var", "exclude_slash_tmp", "enabled"}:
                section_data[key] = _parse_bool(value)
            else:
                section_data[key] = _parse_string_scalar(value)
            continue
        if in_ws:
            if key == "writable_roots":
                table[key] = _parse_string_list(value)
            elif key in {"network_access", "exclude_tmpdir_env_var", "exclude_slash_tmp"}:
                table[key] = _parse_bool(value)
            else:
                table[key] = _parse_string_scalar(value)
        else:
            top[key] = _parse_string_scalar(value)
    flush_section()
    if table:
        top[TABLE] = table
    return top


def _parse_filesystem_section(sec: dict[str, Any]) -> tuple[list[str], list[str]]:
    globs: list[str] = []
    paths: list[str] = []
    roots = sec.get('":workspace_roots"') or sec.get(":workspace_roots")
    if isinstance(roots, str) and roots.startswith("{"):
        for k, v in _parse_kv_pairs(roots).items():
            if v == "deny" and k != ".":
                globs.append(k)
    for key, val in sec.items():
        if key in {":workspace_roots", '":workspace_roots"'}:
            continue
        if val == "deny":
            paths.append(key)
    return globs, paths


def load_settings(path: Path = CONFIG_PATH) -> SandboxSettings:
    data = _read_config_values(path)
    ws = data.get(TABLE) or {}
    if not isinstance(ws, dict):
        ws = {}
    roots = ws.get("writable_roots") or []
    if not isinstance(roots, list):
        roots = []

    deny_globs: list[str] = []
    deny_paths: list[str] = []
    fs = data.get("permissions.workspace.filesystem") or {}
    if isinstance(fs, dict):
        deny_globs, deny_paths = _parse_filesystem_section(fs)

    allowed_domains: list[str] = []
    denied_domains: list[str] = []
    domains = data.get("permissions.workspace.network.domains") or {}
    if isinstance(domains, dict):
        for dom, action in domains.items():
            if action == "allow":
                allowed_domains.append(dom)
            elif action == "deny":
                denied_domains.append(dom)

    network_access = bool(ws.get("network_access", False))
    network_policy: NetworkPolicy = "off"
    if network_access:
        network_policy = "allowlist" if allowed_domains else "open"

    return SandboxSettings(
        sandbox_mode=str(data.get("sandbox_mode") or "read-only"),
        approval_policy=str(data.get("approval_policy") or "on-request"),
        network_access=network_access,
        writable_roots=[str(p) for p in roots],
        exclude_tmpdir_env_var=bool(ws.get("exclude_tmpdir_env_var", False)),
        exclude_slash_tmp=bool(ws.get("exclude_slash_tmp", False)),
        deny_globs=deny_globs,
        deny_paths=deny_paths,
        network_policy=network_policy,
        allowed_domains=allowed_domains,
        denied_domains=denied_domains,
    )


def json_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_array_str(values: list[str]) -> str:
    inner = ", ".join(json_quote(v) for v in values)
    return f"[{inner}]"


def _replace_or_insert_top_level(lines: list[str], key: str, rendered: str) -> list[str]:
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = rendered
            return lines
    insert_at = 0
    for i, line in enumerate(lines):
        if line.strip() == WIZARD_MARKER:
            insert_at = i
            break
        if line.strip().startswith("model") or line.strip().startswith("sandbox_mode"):
            insert_at = i + 1
    lines.insert(insert_at, rendered)
    return lines


def _replace_or_insert_table_key(lines: list[str], key: str, rendered: str) -> list[str]:
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    in_table = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == f"[{TABLE}]":
            in_table = True
            continue
        if in_table and stripped.startswith("[") and stripped.endswith("]"):
            lines.insert(i, rendered)
            return lines
        if in_table and pat.match(line):
            lines[i] = rendered
            return lines
    if not any(l.strip() == f"[{TABLE}]" for l in lines):
        lines.extend(["", f"[{TABLE}]"])
    for i, line in enumerate(lines):
        if line.strip() == f"[{TABLE}]":
            lines.insert(i + 1, rendered)
            return lines
    lines.append(rendered)
    return lines


def _remove_table_keys(lines: list[str], keys: set[str]) -> list[str]:
    in_table = False
    out: list[str] = []
    key_pat = {k: re.compile(rf"^\s*{re.escape(k)}\s*=") for k in keys}
    for line in lines:
        stripped = line.strip()
        if stripped == f"[{TABLE}]":
            in_table = True
            out.append(line)
            continue
        if in_table and stripped.startswith("[") and stripped.endswith("]"):
            in_table = False
        if in_table and any(p.match(line) for p in key_pat.values()):
            continue
        out.append(line)
    return out


def _strip_wizard_managed(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if line.strip() == WIZARD_MARKER:
            break
        out.append(line)
    return out


def _build_fine_permission_lines(settings: SandboxSettings) -> list[str]:
    if not settings.uses_fine_permissions():
        return []

    roots_mode = "write" if settings.sandbox_mode == "workspace-write" else "read"
    inner = [f'"." = "{roots_mode}"']
    for g in settings.deny_globs:
        inner.append(f'{json_quote(g)} = "deny"')

    lines = [
        "",
        WIZARD_MARKER,
        'default_permissions = "workspace"',
        "",
        "[permissions.workspace.filesystem]",
        f'":workspace_roots" = {{ {", ".join(inner)} }}',
    ]
    for p in settings.deny_paths:
        lines.append(f'{json_quote(p)} = "deny"')

    if settings.network_policy == "allowlist":
        lines.extend(
            [
                "",
                "[permissions.workspace.network]",
                "enabled = true",
                'mode = "limited"',
                "",
                "[permissions.workspace.network.domains]",
            ]
        )
        for dom in settings.allowed_domains:
            lines.append(f'{json_quote(dom)} = "allow"')
        for dom in settings.denied_domains:
            lines.append(f'{json_quote(dom)} = "deny"')

    return lines


def apply_settings(settings: SandboxSettings, path: Path = CONFIG_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_suffix(f".toml.bak.{ts}")
        shutil.copy2(path, backup)
    else:
        backup = path

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = _strip_wizard_managed(lines)

    # sync legacy network flag from policy
    net_on = settings.network_policy in {"open", "allowlist"}
    settings.network_access = net_on

    lines = _replace_or_insert_top_level(
        lines, "sandbox_mode", f'sandbox_mode = {json_quote(settings.sandbox_mode)}'
    )
    lines = _replace_or_insert_top_level(
        lines, "approval_policy", f'approval_policy = {json_quote(settings.approval_policy)}'
    )

    if settings.sandbox_mode == "workspace-write":
        if not any(l.strip() == f"[{TABLE}]" for l in lines):
            lines.extend(["", f"[{TABLE}]"])
        lines = _replace_or_insert_table_key(
            lines, "network_access", f"network_access = {_toml_bool(settings.network_access)}"
        )
        lines = _replace_or_insert_table_key(
            lines, "writable_roots", f"writable_roots = {_toml_array_str(settings.writable_roots)}"
        )
        lines = _replace_or_insert_table_key(
            lines,
            "exclude_tmpdir_env_var",
            f"exclude_tmpdir_env_var = {_toml_bool(settings.exclude_tmpdir_env_var)}",
        )
        lines = _replace_or_insert_table_key(
            lines,
            "exclude_slash_tmp",
            f"exclude_slash_tmp = {_toml_bool(settings.exclude_slash_tmp)}",
        )
    else:
        lines = _remove_table_keys(
            lines,
            {"network_access", "writable_roots", "exclude_tmpdir_env_var", "exclude_slash_tmp"},
        )

    if not settings.uses_fine_permissions():
        pat = re.compile(r"^\s*default_permissions\s*=")
        lines = [l for l in lines if not pat.match(l.strip())]

    lines.extend(_build_fine_permission_lines(settings))

    text = "\n".join(lines).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    return backup
