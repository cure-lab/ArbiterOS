from __future__ import annotations

import select
import shlex
import sys
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from arbiteros_kernel.tui.banner import render_banner
from arbiteros_kernel.tui.config_catalog import load_agents, load_models, registration_hints
from arbiteros_kernel.tui.trace_catalog import (
    load_trace_detail,
    load_trace_rows,
    resolve_trace_id,
)
from arbiteros_kernel.tui_bridge import list_pending_confirms, read_events, submit_confirm_answer
from arbiteros_kernel.tui.proxy_launcher import ProxyProcess, is_proxy_ready


class ArbiterTuiApp:
    def __init__(self, *, console: Optional[Console] = None, proxy: Optional[ProxyProcess] = None):
        self.console = console or Console()
        self.proxy = proxy
        self.attached_trace_id: Optional[str] = None

    def render_home(self) -> None:
        self.console.clear()
        render_banner(self.console)
        proxy_status = "up" if is_proxy_ready() else "down"
        self.console.print(
            f"Proxy: [bold]http://127.0.0.1:4000[/bold]   status: [bold]{proxy_status}[/bold]"
        )
        self.console.print()

        models = load_models()
        agents = load_agents()
        hints = registration_hints()

        self.console.print("[bold]Models[/bold]")
        if models:
            for name in models:
                self.console.print(f"  · {name}")
        else:
            self.console.print("  (none configured)")
        self.console.print()
        self.console.print(
            Panel(
                hints["models"],
                title="Register models",
                border_style="dim white",
            )
        )
        self.console.print()

        self.console.print("[bold]Agents[/bold]")
        if agents:
            for name in agents:
                self.console.print(f"  · {name}")
        else:
            self.console.print("  (none configured)")
        self.console.print()
        self.console.print(
            Panel(
                f"{hints['agents']}\nDocs: {hints['docs']}",
                title="Register agents",
                border_style="dim white",
            )
        )
        self.console.print()

        self.console.print(
            Panel(
                "Commands: [bold]list[/bold]  |  [bold]attach <trace_id>[/bold]  |  "
                "[bold]sw[/bold] (sandbox wizard)  |  [bold]quit[/bold] (q)\n"
                "If a policy block needs your decision, [bold]attach[/bold] that trace and answer "
                "[bold]Y[/bold]/[bold]N[/bold] inside it. "
                "`list` shows a [bold]block[/bold] column when confirmation is pending. "
                "`sw` configures Codex sandbox profiles and applies them to ~/.codex/config.toml.",
                title="How to use",
                border_style="white",
            )
        )
        self._render_pending_hints()

    def _render_pending_hints(self) -> None:
        pending = list_pending_confirms()
        if not pending:
            return
        self.console.print()
        self.console.print("[bold yellow]Needs confirmation[/bold yellow]")
        for item in pending:
            trace_id = item.get("trace_id", "?")
            policies = item.get("policy_names") or []
            policy_text = ", ".join(policies) if policies else "(unknown)"
            self.console.print(
                f"  [!] trace [bold]{trace_id}[/bold]  policies={policy_text}"
            )
            self.console.print(
                f"      → run [bold]attach {trace_id}[/bold] then answer Y/N inside that view"
            )

    def cmd_list(self) -> None:
        rows = load_trace_rows()
        if not rows:
            self.console.print("[dim]No traces found in log/instruction/.[/dim]")
            return
        table = Table(title="Traces", show_lines=False, header_style="bold white")
        table.add_column("status", style="bold")
        table.add_column("block")
        table.add_column("agent")
        table.add_column("trace_id", no_wrap=True)
        table.add_column("created_at")
        table.add_column("context")
        table.add_column("tokens")
        for row in rows:
            status_style = "green" if row.status == "running" else "dim"
            if row.pending_block == "pending":
                block_cell = "[bold yellow]pending[/bold yellow]"
            else:
                block_cell = "[dim]-[/dim]"
            table.add_row(
                f"[{status_style}]{row.status}[/{status_style}]",
                block_cell,
                row.agent,
                row.trace_id,
                row.created_at,
                row.context,
                row.tokens,
            )
        self.console.print(table)

    def cmd_attach(self, trace_ref: str) -> bool:
        rows = load_trace_rows(include_tests=True)
        trace_id = resolve_trace_id(trace_ref, rows)
        if trace_id is None:
            self.console.print(f"[red]Trace not found:[/red] {trace_ref}")
            return False
        self.attached_trace_id = trace_id
        self.render_attach_view()
        return True

    def render_attach_view(self) -> None:
        trace_id = self.attached_trace_id
        if not trace_id:
            return
        self.console.clear()
        render_banner(self.console)
        detail = load_trace_detail(trace_id)
        block = detail.get("pending_block", "-")
        block_text = (
            "[bold yellow]pending[/bold yellow]"
            if block == "pending"
            else "[dim]-[/dim]"
        )
        self.console.print(
            Panel(
                f"trace_id: [bold]{trace_id}[/bold]\n"
                f"agent: {detail.get('agent', 'unknown')}   "
                f"status: {detail.get('status', 'unknown')}   "
                f"block: {block_text}   "
                f"tokens: {detail.get('tokens', '-')}   "
                f"context: {detail.get('context', '-')}",
                title="Attached trace (live · 10s)",
                border_style="white",
            )
        )
        instructions = detail.get("instructions")
        if isinstance(instructions, list) and instructions:
            self.console.print("[bold]Recent instructions[/bold]")
            for instr in instructions[-8:]:
                if not isinstance(instr, dict):
                    continue
                itype = instr.get("instruction_type", "?")
                cat = instr.get("instruction_category", "?")
                content = instr.get("content", "")
                preview = str(content).replace("\n", " ")[:120]
                self.console.print(f"  · [{itype}/{cat}] {preview}")
        else:
            self.console.print("[dim]No instructions yet.[/dim]")
        self._render_trace_events(trace_id)
        self._render_pending_for_trace(trace_id)
        self.console.print(
            "[dim]Live follow: auto-refresh every 10s. "
            "Commands: Y/N · quit (q) · Enter to force refresh[/dim]"
        )

    def _render_pending_for_trace(self, trace_id: str) -> None:
        pending = [p for p in list_pending_confirms() if p.get("trace_id") == trace_id]
        if not pending:
            return
        self.console.print()
        self.console.print("[bold yellow]Policy confirmation required[/bold yellow]")
        for item in pending:
            policies = item.get("policy_names") or []
            self.console.print(f"  policies: {', '.join(policies) if policies else '(unknown)'}")
            err = str(item.get("error_type") or "").strip()
            if err:
                self.console.print(f"  reason: {err[:500]}")
            self.console.print("  Answer [bold]Y[/bold] (keep block) or [bold]N[/bold] (allow original)")

    def _render_trace_events(self, trace_id: str) -> None:
        events = read_events(trace_id=trace_id, limit=40)
        recent = events[-12:]
        if not recent:
            return
        self.console.print()
        self.console.print("[bold]Trace events[/bold]")
        for event in recent:
            level = str(event.get("level", "info"))
            message = str(event.get("message", ""))
            ts = str(event.get("ts", ""))
            style = "yellow" if level == "confirm" else "red" if level == "error" else "white"
            self.console.print(f"  [{ts}] [{style}]{level}[/]: {message}")

    def _answer_pending(self, keep_block: bool, *, trace_id: str) -> bool:
        pending = [
            p for p in list_pending_confirms() if p.get("trace_id") == trace_id
        ]
        if not pending:
            self.console.print("[dim]No pending confirmations for this trace.[/dim]")
            return False
        item = pending[0]
        request_id = item.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return False
        submit_confirm_answer(request_id=request_id, keep_block=keep_block)
        decision = "keep block" if keep_block else "allow original"
        self.console.print(f"[green]Recorded confirmation:[/green] {decision}")
        return True

    @staticmethod
    def _readline_with_timeout(timeout_sec: float) -> Optional[str]:
        """Return a line, None on timeout, or raise EOFError."""
        if not sys.stdin.isatty():
            line = sys.stdin.readline()
            if line == "":
                raise EOFError
            return line.rstrip("\n")
        ready, _, _ = select.select([sys.stdin], [], [], timeout_sec)
        if not ready:
            return None
        line = sys.stdin.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\n")

    def handle_home_input(self, raw: str) -> bool:
        line = raw.strip()
        if not line:
            return True
        lower = line.lower()
        if lower in {"q", "quit", "exit"}:
            return False
        if lower in {"y", "yes", "n", "no"}:
            self.console.print(
                "[dim]Y/N only works inside attach. Use: attach <trace_id>[/dim]"
            )
            return True
        if lower == "list":
            self.cmd_list()
            return True
        if lower in {"sw", "sandbox", "sandbox_wizard"}:
            from arbiteros_kernel.tui.sandbox import run_sandbox_wizard_menu

            run_sandbox_wizard_menu(console=self.console)
            self.render_home()
            return True
        if lower.startswith("attach "):
            parts = shlex.split(line)
            if len(parts) != 2:
                self.console.print("Usage: attach <trace_id>")
                return True
            if self.cmd_attach(parts[1]):
                return self.attach_loop()
            return True
        self.console.print(
            "[dim]Unknown command. Try list, attach <trace_id>, sw, quit.[/dim]"
        )
        return True

    def attach_loop(self) -> bool:
        while self.attached_trace_id:
            self.render_attach_view()
            self.console.print("[bold cyan]attach>[/bold cyan] ", end="")
            try:
                sys.stdout.flush()
                raw = self._readline_with_timeout(10.0)
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                self.attached_trace_id = None
                self.render_home()
                return True
            if raw is None:
                # Timeout → live refresh
                continue
            lower = raw.strip().lower()
            if not lower:
                continue
            if lower in {"q", "quit", "exit"}:
                self.attached_trace_id = None
                self.render_home()
                return True
            if lower in {"y", "yes", "n", "no"}:
                self._answer_pending(
                    keep_block=lower in {"y", "yes"},
                    trace_id=self.attached_trace_id,
                )
                continue
            if lower == "refresh":
                continue
            self.console.print("[dim]In attach view: Y/N, quit. (auto-refresh is on)[/dim]")
            try:
                self._readline_with_timeout(1.2)
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                self.attached_trace_id = None
                self.render_home()
                return True
        return True

    def run(self) -> None:
        self.render_home()
        while True:
            try:
                raw = self.console.input("[bold cyan]arbiteros>[/bold cyan] ")
            except (EOFError, KeyboardInterrupt):
                self.console.print()
                break
            if not self.handle_home_input(raw):
                break
