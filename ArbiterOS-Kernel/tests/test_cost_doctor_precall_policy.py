"""Tests for CostDoctorPreCallPolicy integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arbiteros_kernel.precall_policy.cost_doctor_policy import CostDoctorPreCallPolicy
from arbiteros_kernel.precall_policy.cost_doctor_runtime import (
    get_or_create_state,
    record_post_call_attribution,
)
from arbiteros_kernel.precall_policy.defaults import get_precall_policy_classes
from arbiteros_kernel.precall_policy.prompt_mutator import apply_context_actions_to_request
from arbiteros_kernel.precall_policy_check import check_precall_policy

from flow_cost_doctor.cost_down import OptimizationStrategy, StrategyTarget
from flow_cost_doctor.runtime.cumulative import record_step_attribution
from flow_cost_doctor.runtime.payload_index import PayloadIndex


FIXTURE_POLICY = (
    Path(__file__).resolve().parents[2]
    / "reports/arbiteros_20260707/django_2/confidence_0.85/cost_down/policy.json"
)

RULE_ENGINE_TEMPLATE = (
    Path(__file__).resolve().parents[2] / "config" / "cost_down_rule_engine.json"
)


@pytest.fixture(autouse=True)
def _clear_cost_down_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ARBITEROS_COST_DOWN_POLICY", raising=False)
    monkeypatch.delenv("ARBITEROS_COST_DOWN_RULE_ENGINE", raising=False)
    monkeypatch.delenv("ARBITEROS_COST_DOWN_VALIDATION_POLICY", raising=False)
    monkeypatch.setenv("ARBITEROS_COST_DOWN_PHASE", "A")


def test_default_registry_empty_without_rule_engine_path():
    assert get_precall_policy_classes() == []


def test_registry_loads_cost_doctor_when_rule_engine_path_set(monkeypatch: pytest.MonkeyPatch):
    if RULE_ENGINE_TEMPLATE.exists():
        monkeypatch.setenv("ARBITEROS_COST_DOWN_RULE_ENGINE", str(RULE_ENGINE_TEMPLATE))
    else:
        monkeypatch.setenv("ARBITEROS_COST_DOWN_RULE_ENGINE", "/tmp/missing-rule-engine.json")
    assert get_precall_policy_classes() == [CostDoctorPreCallPolicy]


def test_legacy_policy_env_still_enables_precall(monkeypatch: pytest.MonkeyPatch):
    if FIXTURE_POLICY.exists():
        monkeypatch.setenv("ARBITEROS_COST_DOWN_POLICY", str(FIXTURE_POLICY))
        assert get_precall_policy_classes() == [CostDoctorPreCallPolicy]


def test_phase_a_does_not_modify_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    policy_path = tmp_path / "rule_engine.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "cost_down_rule_engine",
                "rule_engine": {
                    "decision_function": "decide_context_strategy_v2",
                    "threshold_keep_ratio": 0.5,
                    "threshold_drop_ratio": 0.25,
                    "protection_step_count": 4,
                    "threshold_min_token_burden": 50,
                    "protected_context_ids": ["sys_1", "goal_1"],
                },
                "metadata": {"runtime_consumer": "arbiteros_precall"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARBITEROS_COST_DOWN_RULE_ENGINE", str(policy_path))
    monkeypatch.setenv("ARBITEROS_COST_DOWN_PHASE", "A")
    monkeypatch.setenv("ARBITEROS_COST_DOWN_LIVE_POLICY_DIR", str(tmp_path / "live"))

    instructions = [
        {
            "id": "inst-sys",
            "runtime_step": 1,
            "arbiteros_ref_kind": "SYSTEMPROMPT",
            "content": "You are helpful.",
        },
        {
            "id": "inst-goal",
            "runtime_step": 2,
            "arbiteros_ref_kind": "USERINPUT",
            "content": "Fix bug",
        },
    ]
    request = {
        "model": "gpt-5.5",
        "messages": [
            {
                "role": "system",
                "content": "[ARBITEROS_REF id=inst-sys kind=SYSTEMPROMPT]\nYou are helpful.",
            },
            {
                "role": "user",
                "content": "[ARBITEROS_REF id=inst-goal kind=USERINPUT]\nFix bug",
            },
        ],
    }
    result = check_precall_policy(
        trace_id="trace-phase-a",
        current_request=request,
        instructions=instructions,
        policy_classes=[CostDoctorPreCallPolicy],
    )
    assert result.modified is False
    assert result.request == request
    assert result.policy_names == []


def test_phase_b_drop_removes_message(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARBITEROS_COST_DOWN_PHASE", "B")
    instruction_to_context = {"inst-old": "obs_0001_old_log"}
    drop_strategy = OptimizationStrategy(
        target=StrategyTarget(target_type="context_content", target_id="obs_0001_old_log"),
        action="DROP",
        reason="test",
        estimated_token_savings=100,
        confidence=0.8,
        risk_level="high",
        rule_id="DROP_LOW_VALUE",
        producer_step_index=0,
    )
    request = {
        "messages": [
            {
                "role": "user",
                "content": "[ARBITEROS_REF id=inst-old kind=TOOLRESULT]\n" + ("x" * 200),
            }
        ]
    }
    mutated, modified = apply_context_actions_to_request(
        request,
        instruction_to_context=instruction_to_context,
        context_actions={
            "obs_0001_old_log": {"effective_action": "DROP", "action": "drop"},
        },
        strategies_by_context={"obs_0001_old_log": drop_strategy},
        step_index=10,
        stage="planning",
        phase="B",
    )
    assert modified is True
    assert mutated["messages"] == []


def test_phase_c_compress_truncates_content(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARBITEROS_COST_DOWN_PHASE", "C")
    instruction_to_context = {"inst-old": "obs_0002_file"}
    compress_strategy = OptimizationStrategy(
        target=StrategyTarget(target_type="context_content", target_id="obs_0002_file"),
        action="COMPRESS",
        reason="test",
        estimated_token_savings=50,
        confidence=0.7,
        risk_level="medium",
        rule_id="COMPRESS_AND_GATE",
        compress_target_ratio=0.1,
        producer_step_index=0,
    )
    body = "abcdefghij" * 20
    request = {
        "messages": [
            {
                "role": "user",
                "content": f"[ARBITEROS_REF id=inst-old kind=TOOLRESULT]\n{body}",
            }
        ]
    }
    mutated, modified = apply_context_actions_to_request(
        request,
        instruction_to_context=instruction_to_context,
        context_actions={
            "obs_0002_file": {"effective_action": "COMPRESS", "action": "compress"},
        },
        strategies_by_context={"obs_0002_file": compress_strategy},
        step_index=10,
        stage="planning",
        phase="C",
    )
    assert modified is True
    new_content = mutated["messages"][0]["content"]
    assert len(new_content) < len(request["messages"][0]["content"])


def test_phase_d_persists_live_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    policy_path = tmp_path / "rule_engine.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "cost_down_rule_engine",
                "rule_engine": {
                    "decision_function": "decide_context_strategy_v2",
                    "threshold_keep_ratio": 0.5,
                    "threshold_drop_ratio": 0.25,
                    "protection_step_count": 4,
                    "threshold_min_token_burden": 50,
                    "protected_context_ids": ["sys_1", "goal_1"],
                },
                "metadata": {"runtime_consumer": "arbiteros_precall"},
            }
        ),
        encoding="utf-8",
    )
    live_dir = tmp_path / "live"
    monkeypatch.setenv("ARBITEROS_COST_DOWN_RULE_ENGINE", str(policy_path))
    monkeypatch.setenv("ARBITEROS_COST_DOWN_PHASE", "D")
    monkeypatch.setenv("ARBITEROS_COST_DOWN_LIVE_POLICY_DIR", str(live_dir))

    instructions = [
        {
            "id": "inst-sys",
            "runtime_step": 1,
            "arbiteros_ref_kind": "SYSTEMPROMPT",
            "content": "You are helpful.",
        },
        {
            "id": "inst-goal",
            "runtime_step": 2,
            "arbiteros_ref_kind": "USERINPUT",
            "content": "Fix bug",
        },
    ]
    request = {
        "model": "gpt-5.5",
        "messages": [
            {
                "role": "system",
                "content": "[ARBITEROS_REF id=inst-sys kind=SYSTEMPROMPT]\nYou are helpful.",
            },
            {
                "role": "user",
                "content": "[ARBITEROS_REF id=inst-goal kind=USERINPUT]\nFix bug",
            },
        ],
    }
    check_precall_policy(
        trace_id="trace-phase-d",
        current_request=request,
        instructions=instructions,
        policy_classes=[CostDoctorPreCallPolicy],
    )
    live_policy = live_dir / "trace-phase-d" / "policy.json"
    assert live_policy.exists()
    doc = json.loads(live_policy.read_text(encoding="utf-8"))
    assert doc["session"]["source"] == "live_precall"
    assert len(doc["session"]["steps"]) == 1


def test_post_call_attribution_updates_cumulative_state():
    state = get_or_create_state("trace-post")
    state.instruction_to_context = {"out-1": "decision_0001"}
    state.contexts["decision_0001"] = __import__(
        "flow_cost_doctor.runtime.state", fromlist=["ContextMetadata"]
    ).ContextMetadata(
        context_id="decision_0001",
        source_type="model-generated",
        token_count=40,
        producer_step_index=0,
    )
    state.pending_step_index = 0
    state.pending_prompt_items = [
        {"context_id": "decision_0001", "token_count": 40, "attribution_weight": 0.0},
    ]
    instructions = [
        {
            "id": "out-1",
            "runtime_step": 2,
            "arbiteros_ref_kind": "LLMOUTPUT",
            "content": "hello",
            "token_usage": {"llm_call_seq": 1},
            "depends_on": [],
        }
    ]
    record_post_call_attribution("trace-post", instructions=instructions)
    assert state.cum_tokens.get("decision_0001") == 40.0
    assert state.pending_prompt_items == []


def test_record_step_attribution_direct():
    state = get_or_create_state("trace-direct")
    record_step_attribution(
        state,
        [{"context_id": "goal_1", "token_count": 25, "attribution_weight": 1.0, "weight": 0.0}],
    )
    assert state.cum_tokens["goal_1"] == 25.0


def test_phase_b_drop_function_call_output(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARBITEROS_COST_DOWN_PHASE", "B")
    call_id = "call_drop_me"
    instruction_to_context = {"inst-tr": "obs_0009_big"}
    index = PayloadIndex(
        instruction_to_context=instruction_to_context,
        call_id_to_context_ids={call_id: ["obs_0009_big"]},
    )
    drop_strategy = OptimizationStrategy(
        target=StrategyTarget(target_type="context_content", target_id="obs_0009_big"),
        action="DROP",
        reason="test",
        estimated_token_savings=100,
        confidence=0.8,
        risk_level="high",
        rule_id="DROP_LOW_VALUE",
        producer_step_index=0,
    )
    request = {
        "input": [
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": "x" * 500,
            }
        ]
    }
    mutated, modified = apply_context_actions_to_request(
        request,
        instruction_to_context=instruction_to_context,
        context_actions={"obs_0009_big": {"effective_action": "DROP", "action": "drop"}},
        strategies_by_context={"obs_0009_big": drop_strategy},
        step_index=10,
        stage="planning",
        phase="B",
        payload_index=index,
    )
    assert modified is True
    assert mutated["input"] == []


def test_compress_executor_ratio_fallback_without_llm():
    from arbiteros_kernel.precall_policy.compress_executor import compress_text_with_llm

    body = "abcdefghij" * 50
    out = compress_text_with_llm(
        body,
        target_ratio=0.1,
        context_id="obs_test",
        completion_fn=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("no llm")),
        max_iterations=1,
    )
    assert len(out) < len(body)
    assert len(out) <= int(len(body) * 0.11) + 5
