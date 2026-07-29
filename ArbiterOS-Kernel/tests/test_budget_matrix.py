"""Tests for TUI budget matrix aggregation."""

from __future__ import annotations

import json
from pathlib import Path

import arbiteros_kernel.tui.budget as budget
from arbiteros_kernel.tui.trace_catalog import TraceRow


def _write_instruction(root: Path, trace_id: str, n_instr: int = 2) -> None:
    instructions = [
        {
            "id": f"i{i}",
            "content": f"content-{i}-" + ("x" * 50),
            "instruction_type": "USERINPUT" if i else "SYSTEMPROMPT",
        }
        for i in range(n_instr)
    ]
    (root / f"{trace_id}.json").write_text(
        json.dumps({"trace_id": trace_id, "instructions": instructions}),
        encoding="utf-8",
    )


def test_budget_matrix_model_agent_and_margins(tmp_path: Path, monkeypatch) -> None:
    inst = tmp_path / "instruction"
    inst.mkdir()
    _write_instruction(inst, "t-codex", n_instr=2)
    _write_instruction(inst, "t-nano", n_instr=3)

    state = {
        "states": {
            "codex:u1": {
                "trace_id": "t-codex",
                "channel": "codex",
                "user_id": "u1",
                "token_usage_rounds": [
                    {
                        "model": "gpt-5.5;codex",
                        "round_total_tokens": 100,
                        "round_cost_usd": 0.01,
                    },
                    {
                        "model": "gpt-5.5;codex",
                        "round_total_tokens": 50,
                        "round_cost_usd": 0.005,
                    },
                ],
            },
            "fallback:u2": {
                "trace_id": "t-nano",
                "channel": "fallback",
                "user_id": "u2",
                "token_usage_rounds": [
                    {
                        "model": "claude-sonnet-4-5-20250929;nanobot",
                        "round_total_tokens": 200,
                        "round_cost_usd": 0.02,
                    }
                ],
            },
        }
    }
    state_path = tmp_path / "trace_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    monkeypatch.setattr(budget, "load_models", lambda: ["gpt-5.5", "claude-sonnet-4-5-20250929"])
    monkeypatch.setattr(budget, "load_agents", lambda: ["codex", "nanobot", "openclaw"])
    monkeypatch.setattr(
        budget,
        "load_trace_rows",
        lambda: [
            TraceRow(
                trace_id="t-codex",
                status="running",
                agent="codex",
                created_at="2026-07-29",
                context="2 instr / 1 KB",
                tokens="150",
                pending_block="-",
                instruction_count=2,
                is_test=False,
            ),
            TraceRow(
                trace_id="t-nano",
                status="offline",
                agent="nanobot",
                created_at="2026-07-29",
                context="3 instr / 1 KB",
                tokens="200",
                pending_block="-",
                instruction_count=3,
                is_test=False,
            ),
        ],
    )
    monkeypatch.setattr(budget, "kernel_root", lambda: tmp_path)
    # instruction dir is kernel_root/log/instruction
    log = tmp_path / "log"
    log.mkdir()
    (log / "instruction").mkdir()
    for name in ("t-codex", "t-nano"):
        src = inst / f"{name}.json"
        (log / "instruction" / f"{name}.json").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )
    (log / "trace_state.json").write_text(state_path.read_text(encoding="utf-8"))

    # budget reads trace_state from kernel_root/log/trace_state.json
    monkeypatch.setattr(
        budget,
        "_load_active_trace_meta",
        lambda: {
            "t-codex": state["states"]["codex:u1"],
            "t-nano": state["states"]["fallback:u2"],
        },
    )
    monkeypatch.setattr(budget, "_instruction_dir", lambda: log / "instruction")

    matrix = budget.build_budget_matrix()
    assert "gpt-5.5" in matrix.models
    assert "codex" in matrix.agents

    gpt_codex = matrix.cell("gpt-5.5", "codex")
    assert gpt_codex.tokens == 150
    assert abs(gpt_codex.cost_usd - 0.015) < 1e-9
    assert gpt_codex.instr_count == 2

    claude_nano = matrix.cell("claude-sonnet-4-5-20250929", "nanobot")
    assert claude_nano.tokens == 200
    assert abs(claude_nano.cost_usd - 0.02) < 1e-9
    assert claude_nano.instr_count == 3

    # empty combo stays zero
    assert matrix.cell("gpt-5.5", "openclaw").tokens == 0

    # margins
    assert matrix.row_total("gpt-5.5").tokens == 150
    assert matrix.col_total("nanobot").tokens == 200
    assert matrix.grand_total().tokens == 350
