from __future__ import annotations

import argparse
import sys

from rich.console import Console

from arbiteros_kernel.tui.app import ArbiterTuiApp
from arbiteros_kernel.tui.proxy_launcher import ensure_proxy_running, stop_proxy
from arbiteros_kernel.tui_bridge import disable_tui_mode, enable_tui_mode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ArbiterOS terminal shell")
    parser.add_argument(
        "--no-start-proxy",
        action="store_true",
        help="Do not start/stop the Kernel; only attach to an existing proxy on :4000",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=4000,
        help="Proxy port to wait for (default: 4000)",
    )
    args = parser.parse_args(argv)
    console = Console()
    proxy = None
    owns_proxy = not args.no_start_proxy
    enable_tui_mode()
    try:
        if args.no_start_proxy:
            console.print("[dim]Connecting to existing proxy on port 4000…[/dim]")
            ready, _ = ensure_proxy_running(
                port=args.port,
                start_if_missing=False,
                own_lifecycle=False,
                on_status=lambda msg: console.print(f"[dim]{msg}[/dim]"),
            )
            if not ready:
                console.print(
                    "[yellow]Proxy is not ready. Start it with `uv run poe litellm` "
                    "or rerun without --no-start-proxy.[/yellow]"
                )
            else:
                console.print(
                    "[dim]Attached to existing proxy (not owned by this shell; "
                    "quit will not stop it).[/dim]"
                )
        else:
            ready, proxy = ensure_proxy_running(
                port=args.port,
                start_if_missing=True,
                own_lifecycle=True,
                on_status=lambda msg: console.print(f"[dim]{msg}[/dim]"),
            )
            if not ready:
                console.print("[red]Failed to start ArbiterOS Kernel proxy.[/red]")
                console.print("[dim]Check ArbiterOS-Kernel/log/proxy.log for details.[/dim]")
                stop_proxy(proxy)
                return 1
        console.print()
        console.print("[bold green]ArbiterOS is ready.[/bold green]")
        console.print()
        ArbiterTuiApp(console=console, proxy=proxy).run()
        return 0
    finally:
        disable_tui_mode()
        if owns_proxy and proxy is not None:
            console.print("[dim]Stopping ArbiterOS Kernel proxy…[/dim]")
            stop_proxy(proxy)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
