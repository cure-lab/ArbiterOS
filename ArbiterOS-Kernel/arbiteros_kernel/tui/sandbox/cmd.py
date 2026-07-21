"""TUI submenu for `sw` (sandbox wizard) — Codex only."""

from __future__ import annotations

from typing import Optional

from rich.console import Console

from arbiteros_kernel.tui.sandbox import profiles
from arbiteros_kernel.tui.sandbox.config_io import CONFIG_PATH, apply_settings
from arbiteros_kernel.tui.sandbox.wizard import (
    CANCEL,
    WizardIO,
    confirm_and_apply,
    current_settings,
    run_custom_wizard,
    run_preset_picker,
    show_settings,
)


HELP = (
    "Commands: [bold]list[/bold]  |  [bold]use <name>[/bold]  |  [bold]new[/bold]  |  "
    "[bold]show[/bold]  |  [bold]delete <name>[/bold]  |  [bold]back[/bold] (q)\n"
    "Codex only. Profiles: ~/.arbiteros/sandbox_profiles/codex/\n"
    "Applying a profile writes ~/.codex/config.toml (restart Codex after)."
)


def run_sandbox_wizard_menu(*, console: Optional[Console] = None) -> None:
    console = console or Console()
    io = WizardIO(console)
    console.print()
    console.print("[bold]Sandbox wizard (Codex)[/bold]")
    console.print(HELP)
    console.print()

    while True:
        try:
            raw = console.input("[bold cyan]sw>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not raw:
            continue
        lower = raw.lower()
        if lower in {"q", "quit", "exit", "back"}:
            return

        if lower in {"help", "h", "?"}:
            console.print(HELP)
            continue

        if lower == "list":
            names = profiles.list_profiles()
            if not names:
                console.print("[dim]No saved profiles yet. Use [bold]new[/bold].[/dim]")
            else:
                console.print("[bold]Saved profiles[/bold]")
                for name in names:
                    console.print(f"  · {name}")
            continue

        if lower == "show":
            show_settings(io, f"Active Codex config ({CONFIG_PATH})", current_settings())
            continue

        if lower.startswith("use "):
            name = raw.split(maxsplit=1)[1].strip()
            _cmd_use(io, console, name)
            continue

        if lower.startswith("delete "):
            name = raw.split(maxsplit=1)[1].strip()
            _cmd_delete(console, name)
            continue

        if lower == "new":
            _cmd_new(io, console)
            continue

        console.print(
            "[dim]Unknown. Try list, use <name>, new, show, delete <name>, back.[/dim]"
        )


def _cmd_use(io: WizardIO, console: Console, name: str) -> None:
    try:
        settings = profiles.load_profile(name)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        return
    show_settings(io, f"Profile: {name}", settings)
    ok = io.ask_yes_no(f"Apply profile '{name}' to {CONFIG_PATH}", default=False, allow_back=False)
    if ok is CANCEL or not ok:
        console.print("Cancelled.")
        return
    backup = apply_settings(settings)
    console.print(f"Applied '{name}' → {CONFIG_PATH}")
    if backup != CONFIG_PATH:
        console.print(f"Backup: {backup}")
    console.print("Restart Codex for changes to take effect.")


def _cmd_delete(console: Console, name: str) -> None:
    try:
        if profiles.delete_profile(name):
            console.print(f"Deleted profile '{name}'.")
        else:
            console.print(f"[red]Profile not found: {name}[/red]")
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")


def _cmd_new(io: WizardIO, console: Console) -> None:
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
    if profiles.profile_exists(name):
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

    seed = current_settings()
    if str(mode) == "1":
        settings = run_preset_picker(io)
    else:
        settings = run_custom_wizard(io, seed)

    if settings is None:
        console.print("Cancelled.")
        return

    show_settings(io, f"New profile '{name}'", settings)
    save_ok = io.ask_yes_no("Save this profile", default=True, allow_back=False)
    if save_ok is CANCEL or not save_ok:
        console.print("Not saved.")
        return
    path = profiles.save_profile(name, settings)
    console.print(f"Saved: {path}")

    apply_now = io.ask_yes_no(
        "Also apply to ~/.codex/config.toml now", default=True, allow_back=False
    )
    if apply_now is CANCEL or not apply_now:
        return
    confirm_and_apply(io, settings)
