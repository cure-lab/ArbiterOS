from __future__ import annotations

from pathlib import Path

from rich.console import Console

# LiteLLM-style block banner (ansi_shadow), brand name ArbiterOS.
_BANNER = r"""
 █████╗ ██████╗ ██████╗ ██╗████████╗███████╗██████╗  ██████╗ ███████╗
██╔══██╗██╔══██╗██╔══██╗██║╚══██╔══╝██╔════╝██╔══██╗██╔═══██╗██╔════╝
███████║██████╔╝██████╔╝██║   ██║   █████╗  ██████╔╝██║   ██║███████╗
██╔══██║██╔══██╗██╔══██╗██║   ██║   ██╔══╝  ██╔══██╗██║   ██║╚════██║
██║  ██║██║  ██║██████╔╝██║   ██║   ███████╗██║  ██║╚██████╔╝███████║
╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝   ╚═╝   ╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝
""".strip(
    "\n"
)


def render_banner(console: Console) -> None:
    banner_path = Path(__file__).resolve().parent / "assets" / "arbiteros_banner.txt"
    art = _BANNER
    if banner_path.exists():
        try:
            art = banner_path.read_text(encoding="utf-8").strip("\n")
        except OSError:
            pass
    console.print()
    console.print(art)
    console.print()
