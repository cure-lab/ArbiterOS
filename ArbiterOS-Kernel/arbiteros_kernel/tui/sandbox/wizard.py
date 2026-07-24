"""English sandbox wizard with step-back support (type b / back).

Logical steps are shared; apply/load target Codex or Claude Code via ``agent``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from rich.console import Console

from arbiteros_kernel.tui.sandbox.config_io import (
    CONFIG_PATH as CODEX_CONFIG_PATH,
    SandboxSettings,
    apply_settings as apply_codex_settings,
    load_settings as load_codex_settings,
)
from arbiteros_kernel.tui.sandbox.profiles import normalize_agent

BACK = object()
CANCEL = object()

SENSITIVE_GLOBS: dict[str, tuple[str, str]] = {
    "1": ("**/*.env", "all .env files"),
    "2": ("**/.env.*", ".env.local / .env.production etc."),
    "3": ("**/secrets/**", "directories named secrets"),
    "4": ("**/*.pem", "PEM keys/certs"),
    "5": ("**/*id_rsa*", "SSH private key filenames"),
    "6": ("**/.ssh/**", ".ssh dirs inside the project"),
}

DEFAULT_DENY_PATHS = [
    str(Path.home() / ".ssh"),
    str(Path.home() / ".aws"),
    str(Path.home() / ".gnupg"),
]

PRESETS: dict[str, tuple[str, SandboxSettings]] = {
    "1": (
        "Locked down (:read-only + denials + untrusted approval)",
        SandboxSettings(
            sandbox_mode="read-only",
            approval_policy="untrusted",
            deny_globs=["**/*.env", "**/.env.*", "**/*.pem"],
            deny_paths=[str(Path.home() / ".ssh")],
        ),
    ),
    "2": (
        "Daily dev (:workspace + denials, no network)",
        SandboxSettings(
            sandbox_mode="workspace-write",
            approval_policy="on-request",
            network_policy="off",
            deny_globs=["**/*.env", "**/.env.*"],
            deny_paths=[str(Path.home() / ".ssh")],
        ),
    ),
    "3": (
        "Networked dev (:workspace + denials + open network)",
        SandboxSettings(
            sandbox_mode="workspace-write",
            approval_policy="on-request",
            network_policy="open",
            deny_globs=["**/*.env"],
            deny_paths=[str(Path.home() / ".ssh")],
        ),
    ),
}


def _clone(s: SandboxSettings) -> SandboxSettings:
    return replace(
        s,
        writable_roots=list(s.writable_roots),
        deny_globs=list(s.deny_globs),
        deny_paths=list(s.deny_paths),
        allowed_domains=list(s.allowed_domains),
        denied_domains=list(s.denied_domains),
    )


class WizardIO:
    """Thin prompt layer so TUI and tests can inject a console."""

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()

    def say(self, msg: str = "") -> None:
        self.console.print(msg)

    def ask(self, prompt: str, default: str | None = None, *, allow_back: bool = True) -> Any:
        hint = ""
        if allow_back:
            hint = " (b=back)"
        suffix = f" [{default}]" if default is not None else ""
        while True:
            try:
                ans = self.console.input(f"{prompt}{hint}{suffix}: ").strip()
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                return CANCEL
            if allow_back and ans.lower() in {"b", "back"}:
                return BACK
            if ans:
                return ans
            if default is not None:
                return default
            self.say("Enter a value, or use the default shown in brackets.")

    def ask_yes_no(self, prompt: str, default: bool = False, *, allow_back: bool = True) -> Any:
        default_s = "y" if default else "n"
        while True:
            ans = self.ask(f"{prompt} (y/n)", default_s, allow_back=allow_back)
            if ans is BACK or ans is CANCEL:
                return ans
            low = str(ans).lower()
            if low in {"y", "yes", "1"}:
                return True
            if low in {"n", "no", "0"}:
                return False
            self.say("Please enter y or n (or b to go back).")

    def ask_choice(
        self,
        prompt: str,
        options: dict[str, str],
        default: str | None = None,
        *,
        allow_back: bool = True,
    ) -> Any:
        self.say(prompt)
        for key, label in options.items():
            self.say(f"  {key}) {label}")
        if allow_back:
            self.say("  b) go back")
        while True:
            ans = self.ask("Choose", default, allow_back=allow_back)
            if ans is BACK or ans is CANCEL:
                return ans
            if ans in options:
                return ans
            self.say("Invalid choice, try again.")


def show_settings(
    io: WizardIO,
    title: str,
    settings: SandboxSettings,
    *,
    agent: str = "codex",
) -> None:
    agent_n = normalize_agent(agent)
    io.say()
    io.say(title)
    io.say("-" * 50)
    if agent_n == "claude":
        from arbiteros_kernel.tui.sandbox.claude_config_io import claude_summary_lines

        for line in claude_summary_lines(settings):
            io.say(f"  {line}")
    else:
        for line in settings.summary_lines():
            io.say(f"  {line}")
    io.say("-" * 50)


def _expand_path(raw: str) -> str:
    p = Path(raw.strip()).expanduser()
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def _collect_writable_roots(io: WizardIO, existing: list[str]) -> Any:
    roots = list(existing)
    io.say()
    io.say("Extra writable roots (outside the current workspace)")
    io.say("  Empty line finishes. Type b to go back.")
    while True:
        raw = io.ask("Add path (empty=done)", "", allow_back=True)
        if raw is BACK or raw is CANCEL:
            return raw
        if not raw:
            return roots
        path = _expand_path(str(raw))
        if path in roots:
            io.say("  Already listed, skipped.")
            continue
        if not Path(path).exists():
            ok = io.ask_yes_no(f"  Path does not exist: {path}. Add anyway", False)
            if ok is BACK or ok is CANCEL:
                return ok
            if not ok:
                continue
        roots.append(path)
        io.say(f"  Added: {path}")


def _collect_deny_globs(io: WizardIO, existing: list[str]) -> Any:
    globs = list(existing)
    io.say()
    io.say("=== Sensitive file protection (glob deny) ===")
    io.say("  Toggle numbers (e.g. 1 3 5), empty keeps current selection.")
    for key, (pattern, label) in SENSITIVE_GLOBS.items():
        mark = "x" if pattern in globs else " "
        io.say(f"  [{mark}] {key}) {label}  ({pattern})")
    raw = io.ask("Toggle", "", allow_back=True)
    if raw is BACK or raw is CANCEL:
        return raw
    if raw:
        for part in str(raw).split():
            if part in SENSITIVE_GLOBS:
                g = SENSITIVE_GLOBS[part][0]
                if g in globs:
                    globs.remove(g)
                else:
                    globs.append(g)
    io.say("  Custom deny globs (empty line finishes):")
    while True:
        custom = io.ask("Custom deny glob", "", allow_back=True)
        if custom is BACK or custom is CANCEL:
            return custom
        if not custom:
            break
        if custom not in globs:
            globs.append(str(custom))
    return globs


def _collect_deny_paths(io: WizardIO, existing: list[str]) -> Any:
    paths = list(existing)
    io.say()
    io.say("=== Absolute path deny ===")
    add_common = io.ask_yes_no(
        "Add common sensitive dirs (~/.ssh ~/.aws ~/.gnupg)",
        default=not paths,
    )
    if add_common is BACK or add_common is CANCEL:
        return add_common
    if add_common:
        for p in DEFAULT_DENY_PATHS:
            if p not in paths:
                paths.append(p)
    while True:
        raw = io.ask("Add absolute path (empty=done)", "", allow_back=True)
        if raw is BACK or raw is CANCEL:
            return raw
        if not raw:
            return paths
        path = _expand_path(str(raw))
        if path not in paths:
            paths.append(path)


def _collect_network_policy(io: WizardIO, current: SandboxSettings) -> Any:
    io.say()
    io.say("=== Network policy ===")
    pol = io.ask_choice(
        "Network inside the sandbox?",
        {
            "1": "Off (no network — safest)",
            "2": "Open (full outbound)",
            "3": "Allowlist (only listed domains)",
        },
        default={"off": "1", "open": "2", "allowlist": "3"}.get(current.network_policy, "1"),
    )
    if pol is BACK or pol is CANCEL:
        return pol
    mapping = {"1": "off", "2": "open", "3": "allowlist"}
    current.network_policy = mapping[str(pol)]  # type: ignore[assignment]
    current.network_access = current.network_policy in {"open", "allowlist"}
    current.allowed_domains = []
    current.denied_domains = []

    if current.network_policy == "allowlist":
        io.say("  Allowed domains, one per line; empty line finishes.")
        while True:
            dom = io.ask("Allow domain", "", allow_back=True)
            if dom is BACK or dom is CANCEL:
                return dom
            if not dom:
                break
            if dom not in current.allowed_domains:
                current.allowed_domains.append(str(dom))
        extra = io.ask_yes_no("Also add explicit deny domains", default=False)
        if extra is BACK or extra is CANCEL:
            return extra
        if extra:
            while True:
                dom = io.ask("Deny domain", "", allow_back=True)
                if dom is BACK or dom is CANCEL:
                    return dom
                if not dom:
                    break
                if dom not in current.denied_domains:
                    current.denied_domains.append(str(dom))
    return current


def run_custom_wizard(
    io: WizardIO,
    seed: SandboxSettings,
    *,
    agent: str = "codex",
) -> SandboxSettings | None:
    """Step machine with back navigation. Returns None if cancelled.

    Codex: permission profiles (:read-only / :workspace / :danger-full-access).
    Claude: same logical modes map to sandbox.enabled / autoAllow / filesystem.
    """
    agent_n = normalize_agent(agent)
    s = _clone(seed)
    step = 0

    def steps_for(mode: str) -> list[str]:
        # Full access cannot use filesystem denials (Codex rejects extending it).
        if mode == "danger-full-access":
            return ["mode", "approval"]
        base = ["mode", "approval", "globs", "paths", "network"]
        if mode == "workspace-write":
            if agent_n == "claude":
                base.append("writable")
            else:
                base.extend(["writable", "tmpdir"])
        return base

    while True:
        active = steps_for(s.sandbox_mode)
        if step < 0:
            return None
        if step >= len(active):
            return s
        name = active[step]
        io.say()
        io.say(f"--- Step {step + 1}/{len(active)}: {name} ---")

        result: Any = None
        if name == "mode":
            if agent_n == "claude":
                mode_prompt = "Access mode (maps to Claude Code sandbox)"
                mode_opts = {
                    "1": "read-only → enabled, prompts (no auto-allow)",
                    "2": "workspace-write → enabled + auto-allow when not untrusted",
                    "3": "danger-full-access → sandbox.enabled=false",
                }
            else:
                mode_prompt = "Access mode (Codex permission profile)"
                mode_opts = {
                    "1": "read-only → :read-only (inspect only)",
                    "2": "workspace-write → :workspace (typical)",
                    "3": "danger-full-access → no sandbox",
                }
            choice = io.ask_choice(
                mode_prompt,
                mode_opts,
                default={"read-only": "1", "workspace-write": "2", "danger-full-access": "3"}.get(
                    s.sandbox_mode, "2"
                ),
                allow_back=step > 0,
            )
            if choice is BACK:
                step -= 1
                continue
            if choice is CANCEL:
                return None
            s.sandbox_mode = {
                "1": "read-only",
                "2": "workspace-write",
                "3": "danger-full-access",
            }[str(choice)]
            if s.sandbox_mode == "danger-full-access":
                ok = io.ask_yes_no("WARNING: disable sandbox entirely. Confirm", False)
                if ok is BACK:
                    continue
                if ok is CANCEL or not ok:
                    continue
                s.network_policy = "off"
                s.network_access = False
                s.writable_roots = []
                s.exclude_tmpdir_env_var = False
                s.exclude_slash_tmp = False
                s.allowed_domains = []
                s.denied_domains = []
                s.deny_globs = []
                s.deny_paths = []
            elif s.sandbox_mode == "read-only":
                s.writable_roots = []
                s.exclude_tmpdir_env_var = False
                s.exclude_slash_tmp = False
            step += 1
            continue

        if name == "approval":
            if agent_n == "claude":
                approval_prompt = (
                    "Claude bash approval inside sandbox "
                    "(separate from ArbiterOS defender)"
                )
                approval_opts = {
                    "1": "untrusted — no auto-allow, fail if sandbox unavailable",
                    "2": "on-request — auto-allow only for workspace-write",
                    "3": "never — autoAllowBashIfSandboxed=true",
                }
            else:
                approval_prompt = (
                    "Codex built-in approval policy (separate from ArbiterOS defender)"
                )
                approval_opts = {
                    "1": "untrusted — strictest",
                    "2": "on-request — default",
                    "3": "never — never ask (dangerous)",
                }
            choice = io.ask_choice(
                approval_prompt,
                approval_opts,
                default={"untrusted": "1", "on-request": "2", "never": "3"}.get(
                    s.approval_policy, "2"
                ),
            )
            if choice is BACK:
                step -= 1
                continue
            if choice is CANCEL:
                return None
            s.approval_policy = {"1": "untrusted", "2": "on-request", "3": "never"}[str(choice)]
            step += 1
            continue

        if name == "globs":
            conf = io.ask_yes_no(
                "Configure sensitive-file glob denials",
                default=bool(s.deny_globs),
            )
            if conf is BACK:
                step -= 1
                continue
            if conf is CANCEL:
                return None
            if conf:
                result = _collect_deny_globs(io, s.deny_globs)
                if result is BACK:
                    continue
                if result is CANCEL:
                    return None
                s.deny_globs = list(result)
            else:
                s.deny_globs = []
            step += 1
            continue

        if name == "paths":
            conf = io.ask_yes_no("Configure absolute path denials", default=bool(s.deny_paths))
            if conf is BACK:
                step -= 1
                continue
            if conf is CANCEL:
                return None
            if conf:
                result = _collect_deny_paths(io, s.deny_paths)
                if result is BACK:
                    continue
                if result is CANCEL:
                    return None
                s.deny_paths = list(result)
            else:
                s.deny_paths = []
            step += 1
            continue

        if name == "network":
            result = _collect_network_policy(io, s)
            if result is BACK:
                step -= 1
                continue
            if result is CANCEL:
                return None
            s = result
            step += 1
            continue

        if name == "writable":
            writable_q = (
                "Add extra writable roots (sandbox.filesystem.allowWrite)"
                if agent_n == "claude"
                else "Add extra workspace roots (permissions.workspace_roots)"
            )
            conf = io.ask_yes_no(
                writable_q,
                default=bool(s.writable_roots),
            )
            if conf is BACK:
                step -= 1
                continue
            if conf is CANCEL:
                return None
            if conf:
                result = _collect_writable_roots(io, s.writable_roots)
                if result is BACK:
                    continue
                if result is CANCEL:
                    return None
                s.writable_roots = list(result)
            else:
                s.writable_roots = []
            step += 1
            continue

        if name == "tmpdir":
            ex_tmp = io.ask_yes_no(
                "Deny $TMPDIR (:tmpdir = deny)", default=s.exclude_tmpdir_env_var
            )
            if ex_tmp is BACK:
                step -= 1
                continue
            if ex_tmp is CANCEL:
                return None
            ex_slash = io.ask_yes_no(
                "Deny /tmp (:slash_tmp = deny)", default=s.exclude_slash_tmp
            )
            if ex_slash is BACK:
                continue
            if ex_slash is CANCEL:
                return None
            s.exclude_tmpdir_env_var = bool(ex_tmp)
            s.exclude_slash_tmp = bool(ex_slash)
            step += 1
            continue

        io.say(f"Internal error: unknown step {name}")
        return None


def run_preset_picker(io: WizardIO, *, agent: str = "codex") -> SandboxSettings | None:
    _ = normalize_agent(agent)
    io.say()
    io.say("=== Quick presets ===")
    for key, (label, _) in PRESETS.items():
        io.say(f"  {key}) {label}")
    io.say("  b) back")
    choice = io.ask("Choose preset", "2", allow_back=True)
    if choice is BACK or choice is CANCEL:
        return None
    if choice not in PRESETS:
        io.say("Invalid choice.")
        return None
    _, preset = PRESETS[str(choice)]
    return _clone(preset)


def confirm_and_apply(
    io: WizardIO,
    settings: SandboxSettings,
    *,
    agent: str = "codex",
) -> bool:
    agent_n = normalize_agent(agent)
    show_settings(io, "About to write", settings, agent=agent_n)
    if agent_n == "claude":
        from arbiteros_kernel.tui.sandbox import claude_config_io as claude_io

        target = claude_io.CONFIG_PATH
        io.say(f"Target: {target}")
        io.say("Merges/replaces the sandbox object in Claude settings.json.")
        restart = "Claude Code"
        apply_fn = claude_io.apply_settings
    else:
        target = CODEX_CONFIG_PATH
        io.say(f"Target: {target}")
        io.say("Writes permission profiles only; removes legacy sandbox_mode if present.")
        restart = "Codex"
        apply_fn = apply_codex_settings
    ok = io.ask_yes_no("Confirm write", default=False, allow_back=False)
    if ok is CANCEL or not ok:
        io.say("Cancelled — config unchanged.")
        return False
    backup = apply_fn(settings)
    io.say()
    io.say(f"Wrote {target}")
    if backup != target:
        io.say(f"Backup: {backup}")
    io.say(f"Restart {restart} for changes to take effect.")
    return True


def current_settings(agent: str = "codex") -> SandboxSettings:
    agent_n = normalize_agent(agent)
    if agent_n == "claude":
        from arbiteros_kernel.tui.sandbox.claude_config_io import load_settings as load_claude

        return load_claude()
    return load_codex_settings()
