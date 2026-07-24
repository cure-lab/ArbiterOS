"""TUI submenu for `sw` (sandbox wizard) — Codex or Claude Code."""

from __future__ import annotations

from typing import Optional

from rich.console import Console

from arbiteros_kernel.tui.sandbox import profiles
from arbiteros_kernel.tui.sandbox.profiles import AgentName, normalize_agent
from arbiteros_kernel.tui.sandbox.wizard import (
    CANCEL,
    WizardIO,
    confirm_and_apply,
    current_settings,
    run_custom_wizard,
    run_preset_picker,
    show_settings,
)


def _help(agent: AgentName) -> str:
    if agent == "codex":
        return (
            "Commands: [bold]list[/bold]  |  [bold]use <name>[/bold]  |  [bold]new[/bold]  |  "
            "[bold]show[/bold]  |  [bold]delete <name>[/bold]  |  [bold]back[/bold] (q)\n"
            "Codex. Profiles: ~/.arbiteros/sandbox_profiles/codex/\n"
            "Applies Codex [bold]permission profiles[/bold] to ~/.codex/config.toml "
            "(not legacy sandbox_mode). Restart Codex after apply."
        )
    return (
        "Commands: [bold]list[/bold]  |  [bold]use <name>[/bold]  |  [bold]new[/bold]  |  "
        "[bold]show[/bold]  |  [bold]delete <name>[/bold]  |  [bold]back[/bold] (q)\n"
        "Claude Code. Profiles: ~/.arbiteros/sandbox_profiles/claude/\n"
        "Applies [bold]sandbox[/bold] block to ~/.claude/settings.json "
        "(logical modes map to enabled / autoAllowBashIfSandboxed / filesystem / network). "
        "Restart Claude Code after apply."
    )


def _label(agent: AgentName) -> str:
    return "Codex" if agent == "codex" else "Claude Code"


def run_sandbox_wizard_menu(*, console: Optional[Console] = None) -> None:
    console = console or Console()
    io = WizardIO(console)
    console.print()
    console.print("[bold]Sandbox wizard[/bold]")
    console.print("Pick which agent to configure.")
    choice = io.ask_choice(
        "Agent",
        {
            "1": "Codex  (~/.codex/config.toml)",
            "2": "Claude Code / cc  (~/.claude/settings.json)",
        },
        default="1",
        allow_back=True,
    )
    if choice is CANCEL or choice is None:
        console.print("Cancelled.")
        return
    from arbiteros_kernel.tui.sandbox.wizard import BACK

    if choice is BACK:
        console.print("Cancelled.")
        return
    agent: AgentName = "codex" if str(choice) == "1" else "claude"
    _run_agent_menu(agent, console=console, io=io)


def _run_agent_menu(
    agent: AgentName,
    *,
    console: Console,
    io: WizardIO,
) -> None:
    agent = normalize_agent(agent)
    help_text = _help(agent)
    console.print()
    console.print(f"[bold]Sandbox wizard ({_label(agent)})[/bold]")
    console.print(help_text)
    console.print()

    prompt = "[bold cyan]sw/codex>[/bold cyan] " if agent == "codex" else "[bold cyan]sw/cc>[/bold cyan] "

    while True:
        try:
            raw = console.input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not raw:
            continue
        lower = raw.lower()
        if lower in {"q", "quit", "exit", "back"}:
            return

        if lower in {"help", "h", "?"}:
            console.print(help_text)
            continue

        if lower == "list":
            names = profiles.list_profiles(agent)
            if not names:
                console.print("[dim]No saved profiles yet. Use [bold]new[/bold].[/dim]")
            else:
                console.print("[bold]Saved profiles[/bold]")
                for name in names:
                    console.print(f"  · {name}")
            continue

        if lower == "show":
            show_settings(
                io,
                f"Active {_label(agent)} config",
                current_settings(agent),
                agent=agent,
            )
            continue

        if lower.startswith("use "):
            name = raw.split(maxsplit=1)[1].strip()
            _cmd_use(io, console, name, agent)
            continue

        if lower.startswith("delete "):
            name = raw.split(maxsplit=1)[1].strip()
            _cmd_delete(console, name, agent)
            continue

        if lower == "new":
            _cmd_new(io, console, agent)
            continue

        console.print(
            "[dim]Unknown. Try list, use <name>, new, show, delete <name>, back.[/dim]"
        )


def _cmd_use(io: WizardIO, console: Console, name: str, agent: AgentName) -> None:
    try:
        settings = profiles.load_profile(name, agent)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        return
    show_settings(io, f"Profile: {name}", settings, agent=agent)
    target = (
        "~/.codex/config.toml" if agent == "codex" else "~/.claude/settings.json"
    )
    ok = io.ask_yes_no(
        f"Apply profile '{name}' to {target}", default=False, allow_back=False
    )
    if ok is CANCEL or not ok:
        console.print("Cancelled.")
        return
    if agent == "claude":
        from arbiteros_kernel.tui.sandbox import claude_config_io as claude_io

        backup = claude_io.apply_settings(settings)
        config_path = claude_io.CONFIG_PATH
        restart = "Claude Code"
    else:
        from arbiteros_kernel.tui.sandbox.config_io import CONFIG_PATH, apply_settings

        backup = apply_settings(settings)
        config_path = CONFIG_PATH
        restart = "Codex"
    console.print(f"Applied '{name}' → {config_path}")
    if backup != config_path:
        console.print(f"Backup: {backup}")
    console.print(f"Restart {restart} for changes to take effect.")


def _cmd_delete(console: Console, name: str, agent: AgentName) -> None:
    try:
        if profiles.delete_profile(name, agent):
            console.print(f"Deleted profile '{name}'.")
        else:
            console.print(f"[red]Profile not found: {name}[/red]")
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")


def _cmd_new(io: WizardIO, console: Console, agent: AgentName) -> None:
    from arbiteros_kernel.tui.sandbox.wizard import BACK

    name_raw = io.ask("Profile name", allow_back=False)
    if name_raw is CANCEL or not name_raw:
        console.print("Cancelled.")
        return
    try:
        name = profiles.validate_profile_name(str(name_raw))
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return
    if profiles.profile_exists(name, agent):
        overwrite = io.ask_yes_no(
            f"Profile '{name}' exists. Overwrite", default=False, allow_back=False
        )
        if overwrite is CANCEL or not overwrite:
            console.print("Cancelled.")
            return

    mode = io.ask_choice(
        "How do you want to define this profile?",
        {
            "1": "Quick preset",
            "2": "Custom step-by-step (type b to go back a step)",
        },
        default="1",
        allow_back=True,
    )
    if mode is CANCEL or mode is BACK:
        console.print("Cancelled.")
        return

    seed = current_settings(agent)
    if str(mode) == "1":
        settings = run_preset_picker(io, agent=agent)
    else:
        settings = run_custom_wizard(io, seed, agent=agent)

    if settings is None:
        console.print("Cancelled.")
        return

    show_settings(io, f"New profile '{name}'", settings, agent=agent)
    save_ok = io.ask_yes_no("Save this profile", default=True, allow_back=False)
    if save_ok is CANCEL or not save_ok:
        console.print("Not saved.")
        return
    path = profiles.save_profile(name, settings, agent)
    console.print(f"Saved: {path}")

    target = (
        "~/.codex/config.toml" if agent == "codex" else "~/.claude/settings.json"
    )
    apply_now = io.ask_yes_no(
        f"Also apply to {target} now", default=True, allow_back=False
    )
    if apply_now is CANCEL or not apply_now:
        return
    confirm_and_apply(io, settings, agent=agent)
