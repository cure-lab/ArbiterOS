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
from arbiteros_kernel.tui.budget import build_budget_matrix, format_cell
from arbiteros_kernel.tui.trace_catalog import (
    load_trace_detail,
    load_trace_rows,
    resolve_trace_id,
)
from arbiteros_kernel.policy_check import DEFAULT_ROLE_NAME, list_registered_roles
from arbiteros_kernel.trace_roles import display_role_name, set_trace_role
from arbiteros_kernel.tui_bridge import (
    KIND_SAID_DONE,
    list_pending_confirms,
    pending_kind,
    read_events,
    sort_pending_confirms,
    submit_confirm_answer,
)
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
                "Commands: [bold]list[/bold]  |  [bold]bg[/bold] (budget)  |  "
                "[bold]role[/bold]  |  "
                "[bold]attach <trace_id>[/bold]  |  "
                "[bold]sw[/bold] (sandbox wizard)  |  [bold]quit[/bold] (q)\n"
                "If policy or said/done needs your decision, [bold]attach[/bold] that trace and answer "
                "[bold]Y[/bold]/[bold]N[/bold] inside it "
                "([bold]Y[/bold]=deny/keep block, [bold]N[/bold]=allow). "
                "Same trace: policy first, then said/done. "
                "`list` shows a [bold]block[/bold] column when confirmation is pending. "
                "`role` assigns a governance role to a trace at runtime. "
                "`sw` configures Codex or Claude Code sandbox profiles "
                "(pick agent, then list/show/new/use).",
                title="How to use",
                border_style="white",
            )
        )
        self._render_pending_hints()

    def _render_pending_hints(self) -> None:
        pending = sort_pending_confirms(list_pending_confirms())
        if not pending:
            return
        self.console.print()
        self.console.print("[bold yellow]Needs confirmation[/bold yellow]")
        for item in pending:
            trace_id = item.get("trace_id", "?")
            kind = pending_kind(item)
            if kind == KIND_SAID_DONE:
                label = "said/done"
            else:
                policies = item.get("policy_names") or []
                label = f"policy={', '.join(policies) if policies else '(unknown)'}"
            self.console.print(
                f"  [!] trace [bold]{trace_id}[/bold]  {label}"
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
        table.add_column("role")
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
                row.role,
                row.trace_id,
                row.created_at,
                row.context,
                row.tokens,
            )
        self.console.print(table)

    def cmd_budget(self) -> None:
        matrix = build_budget_matrix()
        if not matrix.models:
            self.console.print(
                "[dim]No models in litellm_config.yaml model_list; nothing to show.[/dim]"
            )
            return
        if not matrix.agents:
            self.console.print(
                "[dim]No agents registered under agents/; nothing to show.[/dim]"
            )
            return
        table = Table(
            title="Budget (model × agent) · same traces as list",
            show_lines=True,
            header_style="bold white",
        )
        table.add_column("model", style="bold", no_wrap=True)
        for agent in matrix.agents:
            table.add_column(agent, overflow="fold")
        table.add_column("Σ model", style="bold", overflow="fold")

        for model in matrix.models:
            row_cells = [model]
            for agent in matrix.agents:
                row_cells.append(format_cell(matrix.cell(model, agent)))
            row_cells.append(format_cell(matrix.row_total(model)))
            table.add_row(*row_cells)

        bottom = ["Σ agent"]
        for agent in matrix.agents:
            bottom.append(format_cell(matrix.col_total(agent)))
        bottom.append(format_cell(matrix.grand_total()))
        table.add_row(*bottom)
        self.console.print(table)
        self.console.print(
            "[dim]Each cell: tokens / usd / context (instr + size). "
            "Context uses each trace's list context, attributed to its last LLM round.[/dim]"
        )

    def cmd_role(self) -> None:
        self.console.print(
            Panel(
                "A [bold]role[/bold] = policy enable/observe table in "
                "[bold]arbiteros_kernel/role_policy_sets.json[/bold]\n"
                "+ parameter file under "
                "[bold]arbiteros_kernel/role_policy_cfg/{role}_policy.json[/bold].\n\n"
                f"[bold]{DEFAULT_ROLE_NAME}[/bold] uses global "
                "[bold]policy_registry.json[/bold] + [bold]policy.json[/bold].\n"
                "Agent may pass [bold]model;agent;role[/bold] only to initialize. "
                "Once you set a role here, OS lock wins until you change it again.\n"
                "Type [bold]quit[/bold] at any prompt to cancel.",
                title="Configure / assign role",
                border_style="white",
            )
        )

        rows = load_trace_rows(include_tests=True)
        if not rows:
            self.console.print("[dim]No traces found in log/instruction/.[/dim]")
            return

        self.console.print("[bold]Select a trace[/bold]")
        for idx, row in enumerate(rows, start=1):
            status_style = "green" if row.status == "running" else "dim"
            self.console.print(
                f"  [{idx}] [{status_style}]{row.status}[/{status_style}]  "
                f"agent={row.agent}  role={row.role}  {row.trace_id}"
            )
        choice = self.console.input(
            "[bold cyan]trace # (or quit)[/bold cyan] "
        ).strip()
        if choice.lower() in {"q", "quit", "exit", ""}:
            self.console.print("[dim]Cancelled.[/dim]")
            return
        try:
            trace_idx = int(choice)
        except ValueError:
            resolved = resolve_trace_id(choice, rows)
            if resolved is None:
                self.console.print(f"[red]Invalid selection:[/red] {choice}")
                return
            trace_id = resolved
        else:
            if trace_idx < 1 or trace_idx > len(rows):
                self.console.print(f"[red]Out of range:[/red] {choice}")
                return
            trace_id = rows[trace_idx - 1].trace_id

        roles = list_registered_roles()
        options: list[tuple[str, str]] = [
            (DEFAULT_ROLE_NAME, "Global policy_registry.json + policy.json")
        ]
        for role in roles:
            name = str(role.get("name") or "")
            desc = str(role.get("description") or "").strip() or "(no description)"
            options.append((name, desc))

        self.console.print()
        self.console.print(f"Trace [bold]{trace_id}[/bold] — select a role")
        for idx, (name, desc) in enumerate(options, start=1):
            self.console.print(f"  [{idx}] [bold]{name}[/bold]  {desc}")

        role_choice = self.console.input(
            "[bold cyan]role # (or quit)[/bold cyan] "
        ).strip()
        if role_choice.lower() in {"q", "quit", "exit", ""}:
            self.console.print("[dim]Cancelled.[/dim]")
            return
        try:
            role_idx = int(role_choice)
        except ValueError:
            needle = role_choice.strip()
            matches = [name for name, _ in options if name == needle]
            if len(matches) != 1:
                self.console.print(f"[red]Invalid selection:[/red] {role_choice}")
                return
            selected_role = matches[0]
        else:
            if role_idx < 1 or role_idx > len(options):
                self.console.print(f"[red]Out of range:[/red] {role_choice}")
                return
            selected_role = options[role_idx - 1][0]

        try:
            result = set_trace_role(
                trace_id,
                role_name=None if selected_role == DEFAULT_ROLE_NAME else selected_role,
                source="os",
                locked_by_os=True,
            )
        except ValueError as exc:
            self.console.print(f"[red]Failed:[/red] {exc}")
            return

        self.console.print(
            f"[green]OK[/green] trace [bold]{trace_id}[/bold] → role "
            f"[bold]{result.get('display_role', display_role_name(selected_role))}[/bold] "
            f"(source=os, locked)"
        )

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
                f"role: {detail.get('role', 'default')}   "
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
        pending = sort_pending_confirms(
            [p for p in list_pending_confirms() if p.get("trace_id") == trace_id]
        )
        if not pending:
            return
        self.console.print()
        for item in pending:
            kind = pending_kind(item)
            if kind == KIND_SAID_DONE:
                self.console.print(
                    "[bold yellow]Said/Done confirmation required[/bold yellow]"
                )
                err = str(item.get("error_type") or "").strip()
                if err:
                    self.console.print(f"  reason: {err[:500]}")
                extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
                said = extra.get("said_summary")
                done = extra.get("done_summary")
                diff = extra.get("diff")
                if said:
                    self.console.print(f"  said:  {str(said)[:400]}")
                if done:
                    self.console.print(f"  done:  {str(done)[:400]}")
                if diff:
                    self.console.print(f"  diff:  {str(diff)[:400]}")
                self.console.print(
                    "  Answer [bold]Y[/bold] (deny this tool) or "
                    "[bold]N[/bold] (allow mismatched Done)"
                )
            else:
                self.console.print(
                    "[bold yellow]Policy confirmation required[/bold yellow]"
                )
                policies = item.get("policy_names") or []
                self.console.print(
                    f"  policies: {', '.join(policies) if policies else '(unknown)'}"
                )
                err = str(item.get("error_type") or "").strip()
                if err:
                    self.console.print(f"  reason: {err[:500]}")
                self.console.print(
                    "  Answer [bold]Y[/bold] (keep block) or [bold]N[/bold] (allow original)"
                )
            if len(pending) > 1:
                self.console.print(
                    "[dim]  (policy is answered before said/done on this trace)[/dim]"
                )
                break

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
        pending = sort_pending_confirms(
            [p for p in list_pending_confirms() if p.get("trace_id") == trace_id]
        )
        if not pending:
            self.console.print("[dim]No pending confirmations for this trace.[/dim]")
            return False
        item = pending[0]
        request_id = item.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return False
        kind = pending_kind(item)
        submit_confirm_answer(request_id=request_id, keep_block=keep_block)
        if kind == KIND_SAID_DONE:
            decision = "deny tool" if keep_block else "allow mismatched Done"
        else:
            decision = "keep block" if keep_block else "allow original"
        label = "said/done" if kind == KIND_SAID_DONE else "policy"
        self.console.print(
            f"[green]Recorded {label} confirmation:[/green] {decision}"
        )
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
        if lower in {"bg", "budget"}:
            self.cmd_budget()
            return True
        if lower == "role":
            self.cmd_role()
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
            "[dim]Unknown command. Try list, bg, role, attach <trace_id>, sw, quit.[/dim]"
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
