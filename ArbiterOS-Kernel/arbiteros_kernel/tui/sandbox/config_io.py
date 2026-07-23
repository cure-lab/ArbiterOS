"""Read and patch Codex sandbox settings in ~/.codex/config.toml.

Codex permission profiles must not be mixed with legacy `sandbox_mode` /
`[sandbox_workspace_write]`. This module always writes the permissions-profile
system and removes those older keys on apply.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

CONFIG_PATH = Path.home() / ".codex" / "config.toml"
WIZARD_MARKER = "# --- sandbox_wizard managed ---"
PROFILE_NAME = "arbiteros"
LEGACY_TABLE = "sandbox_workspace_write"

NetworkPolicy = Literal["off", "open", "allowlist"]
AccessMode = Literal["read-only", "workspace-write", "danger-full-access"]


@dataclass
class SandboxSettings:
    """Logical sandbox posture. Access mode maps to Codex permission profiles."""

    sandbox_mode: AccessMode = "read-only"
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

    def needs_custom_profile(self) -> bool:
        if self.sandbox_mode == "danger-full-access":
            return False
        return bool(
            self.deny_globs
            or self.deny_paths
            or self.writable_roots
            or self.exclude_tmpdir_env_var
            or self.exclude_slash_tmp
            or self.network_policy != "off"
        )

    def resolved_default_permissions(self) -> str:
        if self.sandbox_mode == "danger-full-access":
            return ":danger-full-access"
        if not self.needs_custom_profile():
            return ":read-only" if self.sandbox_mode == "read-only" else ":workspace"
        return PROFILE_NAME

    def summary_lines(self) -> list[str]:
        lines = [
            f"access_mode       = {self.sandbox_mode}",
            f"default_permissions = {self.resolved_default_permissions()}",
            f"approval_policy   = {self.approval_policy}",
        ]
        if self.sandbox_mode == "workspace-write":
            lines.extend(
                [
                    f"writable_roots    = {self.writable_roots or '[]'}",
                    f"exclude_tmpdir    = {self.exclude_tmpdir_env_var}",
                    f"exclude /tmp      = {self.exclude_slash_tmp}",
                ]
            )
        if self.deny_globs:
            lines.append(f"deny_globs        = {self.deny_globs}")
        if self.deny_paths:
            lines.append(f"deny_paths        = {self.deny_paths}")
        lines.append(f"network_policy    = {self.network_policy}")
        if self.allowed_domains:
            lines.append(f"allowed_domains   = {self.allowed_domains}")
        if self.denied_domains:
            lines.append(f"denied_domains    = {self.denied_domains}")
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


def _normalize_section_name(sec: str) -> str:
    """Drop optional quotes around dotted table path segments."""
    parts: list[str] = []
    for part in re.findall(r'(?:"(?:\\.|[^"\\])*"|[^.\s]+)', sec.strip()):
        if part.startswith('"') and part.endswith('"'):
            parts.append(_parse_string_scalar(part))
        else:
            parts.append(part)
    return ".".join(parts)


def _read_config_values(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    top: dict[str, Any] = {}
    sections: dict[str, dict[str, Any]] = {}
    current_section: str | None = None
    scalar = re.compile(r'^("(?:\\.|[^"\\])*"|[A-Za-z0-9_.:/-]+)\s*=\s*(.+)$')

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            current_section = _normalize_section_name(stripped[1:-1])
            sections.setdefault(current_section, {})
            continue
        m = scalar.match(stripped)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if key.startswith('"') and key.endswith('"'):
            key = _parse_string_scalar(key)
        parsed: Any
        if value.strip().startswith("["):
            parsed = _parse_string_list(value)
        elif value.strip().startswith("{"):
            parsed = _parse_kv_pairs(value)
        elif value.strip().lower() in {"true", "false"}:
            parsed = _parse_bool(value)
        else:
            parsed = _parse_string_scalar(value)

        if current_section is None:
            top[key] = parsed
        else:
            sections[current_section][key] = parsed

    top["_sections"] = sections
    return top


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    sections = data.get("_sections") or {}
    sec = sections.get(name) or {}
    return sec if isinstance(sec, dict) else {}


def _infer_mode_from_permissions(default_perm: str, extends: str | None) -> AccessMode:
    token = (extends or default_perm or "").strip()
    if token in {":danger-full-access", "danger-full-access"}:
        return "danger-full-access"
    if token in {":read-only", "read-only"}:
        return "read-only"
    if token in {":workspace", "workspace"}:
        return "workspace-write"
    return "workspace-write"


def _load_from_permission_profile(data: dict[str, Any]) -> SandboxSettings | None:
    default_perm = str(data.get("default_permissions") or "").strip()
    approval = str(data.get("approval_policy") or "on-request")

    # Managed tables may exist even if default_permissions was written in the wrong
    # place (after a [table]) and TOML nested it — recover from the profile body.
    if not default_perm:
        if _section(data, f"permissions.{PROFILE_NAME}"):
            default_perm = PROFILE_NAME
        else:
            return None

    if default_perm == ":danger-full-access":
        return SandboxSettings(sandbox_mode="danger-full-access", approval_policy=approval)
    if default_perm == ":read-only":
        return SandboxSettings(sandbox_mode="read-only", approval_policy=approval)
    if default_perm == ":workspace":
        return SandboxSettings(sandbox_mode="workspace-write", approval_policy=approval)

    # Custom profile (prefer our managed name; also accept legacy "workspace")
    profile = default_perm
    base = _section(data, f"permissions.{profile}")
    extends = str(base.get("extends") or "").strip() or None
    mode = _infer_mode_from_permissions(default_perm, extends)
    if extends == ":read-only":
        mode = "read-only"
    elif extends == ":workspace":
        mode = "workspace-write"

    deny_globs: list[str] = []
    deny_paths: list[str] = []
    exclude_tmpdir = False
    exclude_slash_tmp = False

    fs = _section(data, f"permissions.{profile}.filesystem")
    for key, val in fs.items():
        if key == "glob_scan_max_depth":
            continue
        if isinstance(val, dict):
            # Inline table form: ":workspace_roots" = { "." = "write", "**/*.env" = "deny" }
            if key == ":workspace_roots":
                for gk, gv in val.items():
                    if gv == "deny" and gk != ".":
                        deny_globs.append(str(gk))
            continue
        if val == "deny":
            if key == ":tmpdir":
                exclude_tmpdir = True
            elif key == ":slash_tmp":
                exclude_slash_tmp = True
            elif key not in {".", ":minimal", ":root", ":workspace_roots"}:
                deny_paths.append(str(key))

    roots_sec = _section(data, f"permissions.{profile}.filesystem.:workspace_roots")
    for key, val in roots_sec.items():
        if val == "deny" and key != ".":
            deny_globs.append(str(key))

    writable_roots: list[str] = []
    wr = _section(data, f"permissions.{profile}.workspace_roots")
    for key, val in wr.items():
        if val is True or val == "true":
            writable_roots.append(str(key))

    network_policy: NetworkPolicy = "off"
    allowed_domains: list[str] = []
    denied_domains: list[str] = []
    net = _section(data, f"permissions.{profile}.network")
    domains = _section(data, f"permissions.{profile}.network.domains")
    enabled = bool(net.get("enabled", False))
    if enabled:
        for dom, action in domains.items():
            if action == "allow":
                allowed_domains.append(str(dom))
            elif action == "deny":
                denied_domains.append(str(dom))
        if "*" in allowed_domains and len(allowed_domains) == 1 and not denied_domains:
            network_policy = "open"
            allowed_domains = []
        else:
            network_policy = "allowlist"
    network_access = network_policy in {"open", "allowlist"}

    return SandboxSettings(
        sandbox_mode=mode,
        approval_policy=approval,
        network_access=network_access,
        writable_roots=writable_roots,
        exclude_tmpdir_env_var=exclude_tmpdir,
        exclude_slash_tmp=exclude_slash_tmp,
        deny_globs=deny_globs,
        deny_paths=deny_paths,
        network_policy=network_policy,
        allowed_domains=allowed_domains,
        denied_domains=denied_domains,
    )


def _load_from_legacy_sandbox(data: dict[str, Any]) -> SandboxSettings:
    """Best-effort read of older sandbox_mode configs (pre-migration)."""
    sections = data.get("_sections") or {}
    ws = sections.get(LEGACY_TABLE) or {}
    if not isinstance(ws, dict):
        ws = {}
    roots = ws.get("writable_roots") or []
    if not isinstance(roots, list):
        roots = []

    deny_globs: list[str] = []
    deny_paths: list[str] = []
    # Legacy wizard wrote permissions.workspace.* after mixing systems
    for pname in (PROFILE_NAME, "workspace"):
        fs = _section(data, f"permissions.{pname}.filesystem")
        if fs:
            for key, val in fs.items():
                if isinstance(val, dict) and key == ":workspace_roots":
                    for gk, gv in val.items():
                        if gv == "deny" and gk != ".":
                            deny_globs.append(str(gk))
                elif val == "deny" and key not in {
                    ".",
                    ":minimal",
                    ":root",
                    ":tmpdir",
                    ":slash_tmp",
                    "glob_scan_max_depth",
                }:
                    deny_paths.append(str(key))
            roots_sec = _section(data, f"permissions.{pname}.filesystem.:workspace_roots")
            for key, val in roots_sec.items():
                if val == "deny" and key != ".":
                    deny_globs.append(str(key))
            break

    network_access = bool(ws.get("network_access", False))
    allowed_domains: list[str] = []
    denied_domains: list[str] = []
    for pname in (PROFILE_NAME, "workspace"):
        domains = _section(data, f"permissions.{pname}.network.domains")
        if domains:
            for dom, action in domains.items():
                if action == "allow":
                    allowed_domains.append(str(dom))
                elif action == "deny":
                    denied_domains.append(str(dom))
            break
    network_policy: NetworkPolicy = "off"
    if network_access:
        network_policy = "allowlist" if allowed_domains else "open"

    mode_raw = str(data.get("sandbox_mode") or "read-only")
    mode: AccessMode
    if mode_raw in {"read-only", "workspace-write", "danger-full-access"}:
        mode = mode_raw  # type: ignore[assignment]
    else:
        mode = "read-only"

    return SandboxSettings(
        sandbox_mode=mode,
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


def load_settings(path: Path = CONFIG_PATH) -> SandboxSettings:
    data = _read_config_values(path)
    from_perm = _load_from_permission_profile(data)
    if from_perm is not None and not data.get("sandbox_mode"):
        return from_perm
    # Mixed or legacy: prefer reconstructing from permission profile when present,
    # else fall back to sandbox_mode.
    if from_perm is not None and data.get("default_permissions"):
        # sandbox_mode was present and wins at runtime for Codex; surface that.
        legacy = _load_from_legacy_sandbox(data)
        # Keep deny/network details from the permission parse when available.
        return SandboxSettings(
            sandbox_mode=legacy.sandbox_mode,
            approval_policy=legacy.approval_policy,
            network_access=from_perm.network_access or legacy.network_access,
            writable_roots=from_perm.writable_roots or legacy.writable_roots,
            exclude_tmpdir_env_var=from_perm.exclude_tmpdir_env_var
            or legacy.exclude_tmpdir_env_var,
            exclude_slash_tmp=from_perm.exclude_slash_tmp or legacy.exclude_slash_tmp,
            deny_globs=from_perm.deny_globs or legacy.deny_globs,
            deny_paths=from_perm.deny_paths or legacy.deny_paths,
            network_policy=from_perm.network_policy
            if from_perm.network_policy != "off"
            else legacy.network_policy,
            allowed_domains=from_perm.allowed_domains or legacy.allowed_domains,
            denied_domains=from_perm.denied_domains or legacy.denied_domains,
        )
    return _load_from_legacy_sandbox(data)


def json_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _first_table_index(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            return i
    return None


def _replace_or_insert_top_level(lines: list[str], key: str, rendered: str) -> list[str]:
    """Insert/replace a root TOML key before any [table] (required by TOML)."""
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    table_at = _first_table_index(lines)
    top_end = table_at if table_at is not None else len(lines)
    for i in range(top_end):
        if pat.match(lines[i]):
            lines[i] = rendered
            return lines
    insert_at = 0
    for i in range(top_end):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            continue
        insert_at = i + 1
    if table_at is not None and insert_at > table_at:
        insert_at = table_at
    lines.insert(insert_at, rendered)
    return lines


def _remove_top_level_keys(lines: list[str], keys: set[str]) -> list[str]:
    pats = {k: re.compile(rf"^\s*{re.escape(k)}\s*=") for k in keys}
    out: list[str] = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_table = True
            out.append(line)
            continue
        if not in_table and any(p.match(line) for p in pats.values()):
            continue
        out.append(line)
    return out


def _remove_toml_tables(lines: list[str], *, exact: set[str] | None = None, prefix: str | None = None) -> list[str]:
    """Drop whole TOML tables by exact name and/or dotted-name prefix."""
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            name = _normalize_section_name(stripped[1:-1])
            skipping = False
            if exact and name in exact:
                skipping = True
            elif prefix and (name == prefix or name.startswith(prefix + ".")):
                skipping = True
            if skipping:
                continue
            out.append(line)
            continue
        if skipping:
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


def _build_permission_table_lines(settings: SandboxSettings) -> list[str]:
    """Emit only `[permissions.*]` tables (default_permissions must be top-level)."""
    settings.network_access = settings.network_policy in {"open", "allowlist"}

    if settings.sandbox_mode == "danger-full-access" or not settings.needs_custom_profile():
        return []

    parent = ":read-only" if settings.sandbox_mode == "read-only" else ":workspace"
    lines = [
        f"[permissions.{PROFILE_NAME}]",
        'description = "Managed by ArbiterOS sandbox wizard"',
        f"extends = {json_quote(parent)}",
    ]

    if settings.writable_roots and settings.sandbox_mode == "workspace-write":
        lines.append("")
        lines.append(f"[permissions.{PROFILE_NAME}.workspace_roots]")
        for root in settings.writable_roots:
            lines.append(f"{json_quote(root)} = true")

    fs_entries: list[str] = []
    if settings.deny_globs:
        fs_entries.append("glob_scan_max_depth = 3")
    if settings.exclude_tmpdir_env_var:
        fs_entries.append('":tmpdir" = "deny"')
    if settings.exclude_slash_tmp:
        fs_entries.append('":slash_tmp" = "deny"')
    for path in settings.deny_paths:
        fs_entries.append(f'{json_quote(path)} = "deny"')

    if fs_entries:
        lines.append("")
        lines.append(f"[permissions.{PROFILE_NAME}.filesystem]")
        lines.extend(fs_entries)

    if settings.deny_globs:
        lines.append("")
        lines.append(f'[permissions.{PROFILE_NAME}.filesystem.":workspace_roots"]')
        for glob in settings.deny_globs:
            lines.append(f'{json_quote(glob)} = "deny"')

    if settings.network_policy != "off":
        lines.append("")
        lines.append(f"[permissions.{PROFILE_NAME}.network]")
        lines.append("enabled = true")
        if settings.network_policy == "allowlist":
            lines.append('mode = "limited"')
        lines.append("")
        lines.append(f"[permissions.{PROFILE_NAME}.network.domains]")
        if settings.network_policy == "open":
            lines.append('"*" = "allow"')
        else:
            for dom in settings.allowed_domains:
                lines.append(f'{json_quote(dom)} = "allow"')
            for dom in settings.denied_domains:
                lines.append(f'{json_quote(dom)} = "deny"')
    elif settings.sandbox_mode == "workspace-write":
        lines.append("")
        lines.append(f"[permissions.{PROFILE_NAME}.network]")
        lines.append("enabled = false")

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

    # Permission profiles are ignored if sandbox_mode is present — strip legacy.
    lines = _remove_top_level_keys(lines, {"sandbox_mode", "default_permissions"})
    lines = _remove_toml_tables(
        lines,
        exact={LEGACY_TABLE},
        prefix=f"permissions.{PROFILE_NAME}",
    )
    # Also clear previous wizard profile name "workspace" if orphaned above marker.
    lines = _remove_toml_tables(lines, prefix="permissions.workspace")

    # Root keys MUST sit above any [table]; trailing keys nest into the last table.
    lines = _replace_or_insert_top_level(
        lines,
        "approval_policy",
        f"approval_policy = {json_quote(settings.approval_policy)}",
    )
    lines = _replace_or_insert_top_level(
        lines,
        "default_permissions",
        f"default_permissions = {json_quote(settings.resolved_default_permissions())}",
    )

    table_lines = _build_permission_table_lines(settings)
    lines.extend(["", WIZARD_MARKER])
    if table_lines:
        lines.append("")
        lines.extend(table_lines)

    text = "\n".join(lines).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    return backup
