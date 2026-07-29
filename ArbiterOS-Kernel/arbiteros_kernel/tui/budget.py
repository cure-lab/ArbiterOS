"""Budget matrix: model × agent aggregates for TUI ``bg`` command."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from arbiteros_kernel.policy_check import split_model_agent_role
from arbiteros_kernel.tui.config_catalog import kernel_root, load_agents, load_models
from arbiteros_kernel.tui.trace_catalog import load_trace_rows


def _instruction_dir():
    return kernel_root() / "log" / "instruction"


def _load_active_trace_meta() -> dict[str, dict[str, Any]]:
    path = kernel_root() / "log" / "trace_state.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    states = raw.get("states") if isinstance(raw, dict) else None
    if not isinstance(states, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for value in states.values():
        if not isinstance(value, dict):
            continue
        tid = value.get("trace_id")
        if isinstance(tid, str) and tid.strip():
            out[tid.strip()] = value
    return out


@dataclass
class CellStats:
    tokens: int = 0
    cost_usd: float = 0.0
    instr_count: int = 0
    context_bytes: int = 0

    def add_usage(self, *, tokens: int, cost_usd: float) -> None:
        self.tokens += max(0, int(tokens))
        self.cost_usd += max(0.0, float(cost_usd))

    def add_context(self, *, instr_count: int, context_bytes: int) -> None:
        self.instr_count += max(0, int(instr_count))
        self.context_bytes += max(0, int(context_bytes))

    def merge(self, other: "CellStats") -> None:
        self.add_usage(tokens=other.tokens, cost_usd=other.cost_usd)
        self.add_context(
            instr_count=other.instr_count, context_bytes=other.context_bytes
        )


@dataclass
class BudgetMatrix:
    models: list[str]
    agents: list[str]
    cells: dict[tuple[str, str], CellStats] = field(default_factory=dict)

    def cell(self, model: str, agent: str) -> CellStats:
        key = (model, agent)
        if key not in self.cells:
            self.cells[key] = CellStats()
        return self.cells[key]

    def row_total(self, model: str) -> CellStats:
        out = CellStats()
        for agent in self.agents:
            out.merge(self.cell(model, agent))
        return out

    def col_total(self, agent: str) -> CellStats:
        out = CellStats()
        for model in self.models:
            out.merge(self.cell(model, agent))
        return out

    def grand_total(self) -> CellStats:
        out = CellStats()
        for model in self.models:
            out.merge(self.row_total(model))
        return out


def format_cell(stats: CellStats) -> str:
    if stats.context_bytes >= 1024:
        ctx = f"{stats.instr_count} instr / {stats.context_bytes // 1024} KB"
    else:
        ctx = f"{stats.instr_count} instr / {stats.context_bytes} B"
    return f"{stats.tokens} tok\n${stats.cost_usd:.4f}\n{ctx}"


def _context_parts(instructions: list[Any]) -> tuple[int, int]:
    count = len(instructions)
    try:
        nbytes = len(json.dumps(instructions, ensure_ascii=False).encode("utf-8"))
    except Exception:
        nbytes = 0
    return count, nbytes


def _normalize_route_model(raw_model: Any, known_models: set[str]) -> str:
    route, _, _ = split_model_agent_role(raw_model)
    if isinstance(route, str) and route.strip():
        name = route.strip()
        if name in known_models:
            return name
        return name if name else "other"
    return "other"


def _agent_from_model_field(raw_model: Any) -> Optional[str]:
    _, agent, _ = split_model_agent_role(raw_model)
    if isinstance(agent, str) and agent.strip():
        return agent.strip().lower()
    return None


def _round_tokens_and_cost(round_record: dict[str, Any]) -> tuple[int, float]:
    tokens = round_record.get("round_total_tokens")
    if not isinstance(tokens, int) or tokens < 0:
        tokens = 0
    cost = round_record.get("round_cost_usd")
    if not isinstance(cost, (int, float)) or cost < 0:
        cost = 0.0
    return tokens, float(cost)


def build_budget_matrix() -> BudgetMatrix:
    """Aggregate the same traces shown by ``list`` into a model × agent matrix."""
    configured_models = load_models()
    configured_agents = load_agents()
    known_models = set(configured_models)

    rows = load_trace_rows()
    active_meta = _load_active_trace_meta()
    inst_dir = _instruction_dir()

    seen_models: set[str] = set()
    seen_agents: set[str] = set()
    # Deferred writes so we can finalize axis lists first? Better accumulate into
    # a temp map then rebuild matrix with stable axes.
    temp: dict[tuple[str, str], CellStats] = {}

    def _ensure(model: str, agent: str) -> CellStats:
        seen_models.add(model)
        seen_agents.add(agent)
        key = (model, agent)
        if key not in temp:
            temp[key] = CellStats()
        return temp[key]

    for row in rows:
        state = active_meta.get(row.trace_id)
        rounds: list[dict[str, Any]] = []
        if isinstance(state, dict):
            raw_rounds = state.get("token_usage_rounds")
            if isinstance(raw_rounds, list):
                rounds = [r for r in raw_rounds if isinstance(r, dict)]

        instructions: list[Any] = []
        path = inst_dir / f"{row.trace_id}.json"
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                raw_instr = payload.get("instructions")
                if isinstance(raw_instr, list):
                    instructions = raw_instr
        instr_count, context_bytes = _context_parts(instructions)

        last_model = "other"
        list_agent = (row.agent or "unknown").strip().lower() or "unknown"
        last_agent = list_agent
        state_agent = None
        if isinstance(state, dict):
            raw_agent = state.get("agent_name")
            if isinstance(raw_agent, str) and raw_agent.strip():
                state_agent = raw_agent.strip().lower()
                last_agent = state_agent

        for round_record in rounds:
            raw_model = round_record.get("model")
            model = _normalize_route_model(raw_model, known_models)
            agent = None
            round_agent = round_record.get("agent")
            if isinstance(round_agent, str) and round_agent.strip():
                agent = round_agent.strip().lower()
            if agent is None:
                agent = _agent_from_model_field(raw_model)
            if agent is None:
                agent = state_agent or list_agent
            tokens, cost = _round_tokens_and_cost(round_record)
            _ensure(model, agent).add_usage(tokens=tokens, cost_usd=cost)
            last_model = model
            last_agent = agent

        # Context: whole-trace size → last round's (model, agent); no rounds → list agent.
        if not rounds:
            agent = state_agent or list_agent
            model = "other"
            _ensure(model, agent).add_context(
                instr_count=instr_count, context_bytes=context_bytes
            )
        else:
            _ensure(last_model, last_agent).add_context(
                instr_count=instr_count, context_bytes=context_bytes
            )

    models = list(configured_models)
    for name in sorted(seen_models):
        if name not in known_models and name not in models:
            models.append(name)
    if "other" in seen_models and "other" not in models:
        models.append("other")

    agents = list(configured_agents)
    for name in sorted(seen_agents):
        if name not in agents:
            agents.append(name)

    matrix = BudgetMatrix(models=models, agents=agents)
    for (model, agent), stats in temp.items():
        matrix.cell(model, agent).merge(stats)
    return matrix
