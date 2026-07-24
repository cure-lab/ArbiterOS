"""Read and patch Claude Code sandbox settings in ~/.claude/settings.json.

Logical ArbiterOS SandboxSettings map onto Claude's ``sandbox`` object:

- danger-full-access → ``enabled: false``
- read-only          → enabled + regular permission prompts (no auto-allow)
- workspace-write    → enabled + ``autoAllowBashIfSandboxed`` when not untrusted
- deny_* / writable_roots → filesystem.denyRead / allowWrite
- network_policy     → network.allowedDomains / deniedDomains
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arbiteros_kernel.tui.sandbox.config_io import SandboxSettings

CONFIG_PATH = Path.home() / ".claude" / "settings.json"
WIZARD_MARKER_KEY = "arbiteros_sandbox_wizard"


def settings_to_claude_sandbox(settings: SandboxSettings) -> dict[str, Any]:
    """Translate logical settings into a Claude ``sandbox`` object."""
    if settings.sandbox_mode == "danger-full-access":
        return {
            "enabled": False,
            "allowUnsandboxedCommands": True,
        }

    auto_allow = settings.approval_policy == "never" or (
        settings.sandbox_mode == "workspace-write"
        and settings.approval_policy != "untrusted"
    )
    sandbox: dict[str, Any] = {
        "enabled": True,
        "autoAllowBashIfSandboxed": auto_allow,
        "allowUnsandboxedCommands": False,
        "failIfUnavailable": settings.approval_policy == "untrusted",
    }

    filesystem: dict[str, Any] = {}
    if settings.writable_roots and settings.sandbox_mode == "workspace-write":
        filesystem["allowWrite"] = list(settings.writable_roots)
    deny_read = list(dict.fromkeys([*settings.deny_paths, *settings.deny_globs]))
    if deny_read:
        filesystem["denyRead"] = deny_read
    if filesystem:
        sandbox["filesystem"] = filesystem

    network: dict[str, Any] = {}
    if settings.network_policy == "off":
        network["allowedDomains"] = []
    elif settings.network_policy == "allowlist":
        network["allowedDomains"] = list(settings.allowed_domains)
        if settings.denied_domains:
            network["deniedDomains"] = list(settings.denied_domains)
    elif settings.network_policy == "open":
        # No domain allowlist → Claude prompts / allows per its defaults.
        network["allowLocalBinding"] = True
    if network:
        sandbox["network"] = network

    return sandbox


def claude_sandbox_to_settings(sandbox: dict[str, Any] | None) -> SandboxSettings:
    """Best-effort reverse map from Claude ``sandbox`` into SandboxSettings."""
    if not isinstance(sandbox, dict) or not sandbox.get("enabled", False):
        return SandboxSettings(
            sandbox_mode="danger-full-access",
            approval_policy="never",
            network_policy="off",
        )

    auto_allow = bool(sandbox.get("autoAllowBashIfSandboxed", False))
    filesystem = sandbox.get("filesystem") if isinstance(sandbox.get("filesystem"), dict) else {}
    network = sandbox.get("network") if isinstance(sandbox.get("network"), dict) else {}

    allow_write = [str(x) for x in (filesystem.get("allowWrite") or [])]
    deny_read = [str(x) for x in (filesystem.get("denyRead") or [])]
    deny_globs = [p for p in deny_read if "*" in p or "?" in p]
    deny_paths = [p for p in deny_read if p not in deny_globs]

    allowed = [str(x) for x in (network.get("allowedDomains") or [])]
    denied = [str(x) for x in (network.get("deniedDomains") or [])]
    if "allowedDomains" in network and allowed == [] and not denied:
        network_policy: str = "off"
    elif allowed or denied:
        network_policy = "allowlist"
    elif network.get("allowLocalBinding") or network:
        network_policy = "open"
    else:
        network_policy = "off"

    if auto_allow:
        mode = "workspace-write"
        approval = "never" if sandbox.get("allowUnsandboxedCommands") else "on-request"
    else:
        mode = "read-only" if not allow_write else "workspace-write"
        approval = "untrusted" if sandbox.get("failIfUnavailable") else "on-request"

    return SandboxSettings(
        sandbox_mode=mode,  # type: ignore[arg-type]
        approval_policy=approval,
        network_access=network_policy in {"open", "allowlist"},
        writable_roots=allow_write,
        deny_globs=deny_globs,
        deny_paths=deny_paths,
        network_policy=network_policy,  # type: ignore[arg-type]
        allowed_domains=allowed,
        denied_domains=denied,
    )


def claude_summary_lines(settings: SandboxSettings) -> list[str]:
    """Human-readable Claude mapping for show/confirm."""
    sandbox = settings_to_claude_sandbox(settings)
    lines = [
        f"claude.enabled                    = {sandbox.get('enabled')}",
        f"claude.autoAllowBashIfSandboxed   = {sandbox.get('autoAllowBashIfSandboxed', False)}",
        f"claude.allowUnsandboxedCommands   = {sandbox.get('allowUnsandboxedCommands', False)}",
        f"claude.failIfUnavailable          = {sandbox.get('failIfUnavailable', False)}",
    ]
    fs = sandbox.get("filesystem") if isinstance(sandbox.get("filesystem"), dict) else {}
    net = sandbox.get("network") if isinstance(sandbox.get("network"), dict) else {}
    if fs.get("allowWrite"):
        lines.append(f"claude.filesystem.allowWrite      = {fs['allowWrite']}")
    if fs.get("denyRead"):
        lines.append(f"claude.filesystem.denyRead        = {fs['denyRead']}")
    if "allowedDomains" in net:
        lines.append(f"claude.network.allowedDomains     = {net.get('allowedDomains')}")
    if net.get("deniedDomains"):
        lines.append(f"claude.network.deniedDomains      = {net['deniedDomains']}")
    if net.get("allowLocalBinding"):
        lines.append("claude.network.allowLocalBinding  = True")
    lines.append("(logical Codex-style fields still stored in ArbiterOS profiles)")
    for line in settings.summary_lines():
        lines.append(f"  logical.{line}")
    return lines


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_settings(path: Path = CONFIG_PATH) -> SandboxSettings:
    data = _load_json(path)
    sandbox = data.get("sandbox")
    return claude_sandbox_to_settings(sandbox if isinstance(sandbox, dict) else None)


def apply_settings(settings: SandboxSettings, path: Path = CONFIG_PATH) -> Path:
    """Merge ``sandbox`` into Claude settings.json; backup first when file exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_suffix(f".json.bak.{ts}")
        shutil.copy2(path, backup)
    else:
        backup = path

    data = _load_json(path)
    data["sandbox"] = settings_to_claude_sandbox(settings)
    data[WIZARD_MARKER_KEY] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "arbiteros_tui_sw",
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return backup
