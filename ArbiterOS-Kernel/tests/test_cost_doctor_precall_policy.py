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


def test_registry_loads_cost_doctor_when_rule_engine_path_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from arbiteros_kernel.precall_policy import defaults as precall_defaults

    registry_path = tmp_path / "precall_policy_registry.json"
    registry_path.write_text(
        json.dumps(
            [
                {
                    "name": "CostDoctorPreCallPolicy",
                    "enabled": True,
                    "description": "test",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARBITEROS_PRECALL_POLICY_REGISTRY", str(registry_path))
    precall_defaults.get_precall_policy_registry(force_reload=True)

    fcd_rule_engine = (
        Path(__file__).resolve().parents[3] / "config" / "cost_down_rule_engine.json"
    )
    if fcd_rule_engine.exists():
        monkeypatch.setenv("ARBITEROS_COST_DOWN_RULE_ENGINE", str(fcd_rule_engine))
    elif RULE_ENGINE_TEMPLATE.exists():
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
    monkeypatch.setenv("ARBITEROS_COST_DOWN_COMPRESS_BACKEND", "rule")
    instruction_to_context = {"inst-old": "obs_0002_file"}
    compress_strategy = OptimizationStrategy(
        target=StrategyTarget(target_type="context_content", target_id="obs_0002_file"),
        action="COMPRESS",
        reason="test",
        estimated_token_savings=50,
        confidence=0.7,
        risk_level="medium",
        rule_id="COMPRESS_MIDBAND",
        compress_target_ratio=0.1,
        compress_mode="rule_based",
        producer_step_index=0,
    )
    body = "FAILED test_foo.py::test_bar AssertionError: boom\n" + ("noise line\n" * 80)
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
            "obs_0002_file": {
                "effective_action": "COMPRESS",
                "action": "compress",
                "progress_signal": "error_signal",
                "source_type": "tool",
            },
        },
        strategies_by_context={"obs_0002_file": compress_strategy},
        step_index=10,
        stage="planning",
        phase="C",
        rule_engine={"compress_backend": "rule", "enable_midband_compress": True},
    )
    assert modified is True
    new_content = mutated["messages"][0]["content"]
    assert len(new_content) < len(request["messages"][0]["content"])
    assert "FAILED" in new_content or "AssertionError" in new_content or "compacted" in new_content


def test_phase_c_rule_backend_does_not_require_llm(monkeypatch: pytest.MonkeyPatch, capsys):
    from arbiteros_kernel.precall_policy.compress_executor import compress_text

    monkeypatch.setenv("ARBITEROS_COST_DOWN_COMPRESS_BACKEND", "rule")
    monkeypatch.setenv("ARBITEROS_COST_DOWN_COMPRESS_LOG", "1")
    text = "assert True\n" + ("xxxx\n" * 200)
    out = compress_text(
        text,
        target_ratio=0.15,
        context_id="obs_test",
        backend="rule",
        rule_engine={"compress_backend": "rule"},
    )
    assert len(out) < len(text)
    logged = capsys.readouterr().out
    assert "[CostDoctor][rule-compress]" in logged
    assert "context=obs_test" in logged
    assert "tokens_before=" in logged
    assert "tokens_after=" in logged
    assert "keep_ratio=" in logged
    assert "saved_ratio=" in logged


def test_phase_c_keep_and_drop_unaffected_by_rule_compress(monkeypatch: pytest.MonkeyPatch):
    """Rule compress must only rewrite COMPRESS carriers; KEEP intact, DROP removed."""
    monkeypatch.setenv("ARBITEROS_COST_DOWN_PHASE", "C")
    monkeypatch.setenv("ARBITEROS_COST_DOWN_COMPRESS_BACKEND", "rule")

    keep_body = "KEEP_ME_UNCHANGED " + ("k" * 120)
    drop_body = "DROP_ME " + ("d" * 120)
    compress_body = "FAILED test_x.py::test_y AssertionError: boom\n" + ("noise\n" * 100)

    keep_strategy = OptimizationStrategy(
        target=StrategyTarget(target_type="context_content", target_id="obs_keep"),
        action="KEEP",
        reason="high value",
        estimated_token_savings=0,
        confidence=0.9,
        risk_level="low",
        rule_id="KEEP_HIGH_VALUE",
        producer_step_index=0,
    )
    drop_strategy = OptimizationStrategy(
        target=StrategyTarget(target_type="context_content", target_id="obs_drop"),
        action="DROP",
        reason="low value",
        estimated_token_savings=100,
        confidence=0.8,
        risk_level="high",
        rule_id="DROP_LOW_VALUE",
        producer_step_index=0,
    )
    compress_strategy = OptimizationStrategy(
        target=StrategyTarget(target_type="context_content", target_id="obs_mid"),
        action="COMPRESS",
        reason="midband",
        estimated_token_savings=50,
        confidence=0.7,
        risk_level="medium",
        rule_id="COMPRESS_MIDBAND",
        compress_target_ratio=0.15,
        compress_mode="rule_based",
        producer_step_index=0,
    )
    request = {
        "messages": [
            {
                "role": "user",
                "content": f"[ARBITEROS_REF id=inst-keep kind=TOOLRESULT]\n{keep_body}",
            },
            {
                "role": "user",
                "content": f"[ARBITEROS_REF id=inst-drop kind=TOOLRESULT]\n{drop_body}",
            },
            {
                "role": "user",
                "content": f"[ARBITEROS_REF id=inst-mid kind=TOOLRESULT]\n{compress_body}",
            },
        ]
    }
    mutated, modified = apply_context_actions_to_request(
        request,
        instruction_to_context={
            "inst-keep": "obs_keep",
            "inst-drop": "obs_drop",
            "inst-mid": "obs_mid",
        },
        context_actions={
            "obs_keep": {"effective_action": "KEEP", "action": "keep"},
            "obs_drop": {"effective_action": "DROP", "action": "drop"},
            "obs_mid": {
                "effective_action": "COMPRESS",
                "action": "compress",
                "progress_signal": "error_signal",
                "source_type": "tool",
            },
        },
        strategies_by_context={
            "obs_keep": keep_strategy,
            "obs_drop": drop_strategy,
            "obs_mid": compress_strategy,
        },
        step_index=10,
        stage="planning",
        phase="C",
        rule_engine={"compress_backend": "rule", "enable_midband_compress": True},
    )
    assert modified is True
    contents = [msg["content"] for msg in mutated["messages"]]
    assert len(contents) == 2  # DROP removed
    assert any(keep_body in c for c in contents)
    assert all(drop_body not in c for c in contents)
    mid = next(c for c in contents if "inst-mid" in c or "FAILED" in c or "compacted" in c)
    assert len(mid) < len(request["messages"][2]["content"])
    assert keep_body in next(c for c in contents if "KEEP_ME_UNCHANGED" in c)


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


def test_two_step_precall_postcall_ratio_accumulates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """End-to-end: precall leaves pending items; postcall folds depends_on into cum_*."""
    from flow_cost_doctor.runtime.cumulative import cumulative_ratio_before_step

    policy_path = tmp_path / "rule_engine.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "cost_down_rule_engine",
                "rule_engine": {
                    "decision_function": "decide_context_strategy_v2",
                    "threshold_keep_ratio": 0.7,
                    "threshold_drop_ratio": 0.25,
                    "protection_step_count": 2,
                    "threshold_min_token_burden": 50,
                    "protected_context_ids": ["sys_1", "goal_1"],
                    "enable_midband_compress": True,
                    "scaffold": {"enabled": False},
                    "hygiene": {"enabled": False},
                },
                "metadata": {"runtime_consumer": "arbiteros_precall"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ARBITEROS_COST_DOWN_RULE_ENGINE", str(policy_path))
    monkeypatch.setenv("ARBITEROS_COST_DOWN_PHASE", "C")

    call_id = "call_ratio_2step"
    obs_body = "traceback " + ("x" * 400)
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
        {
            "id": "tc-1",
            "runtime_step": 3,
            "arbiteros_ref_kind": "TOOLCALL",
            "content": {
                "tool_name": "terminal",
                "tool_call_id": call_id,
                "arguments": {"command": "ls"},
            },
            "token_usage": {"llm_call_seq": 1},
        },
        {
            "id": "tr-1",
            "runtime_step": 4,
            "arbiteros_ref_kind": "TOOLRESULT",
            "content": {
                "tool_name": "terminal",
                "tool_call_id": call_id,
                "result": obs_body,
            },
            "depends_on": [
                {"instruction_id": "tc-1", "ref": "tc-1", "confidence": 1.0},
            ],
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
            {
                "role": "user",
                "content": f"[ARBITEROS_REF id=tr-1 kind=TOOLRESULT]\n{obs_body}",
            },
        ],
    }

    trace_id = "trace-ratio-2step"
    result1 = check_precall_policy(
        trace_id=trace_id,
        current_request=request,
        instructions=instructions,
        policy_classes=[CostDoctorPreCallPolicy],
    )
    assert result1.modified in {True, False}

    state = get_or_create_state(trace_id)
    obs_id = next(cid for cid in state.available_context_ids if cid.startswith("obs_"))
    cum_tokens_before = state.cum_tokens.get(obs_id, 0.0)
    assert state.pending_prompt_items, "precall should stage pending attribution"

    instructions_after_llm1 = [
        *instructions,
        {
            "id": "out-1",
            "runtime_step": 5,
            "arbiteros_ref_kind": "LLMOUTPUT",
            "content": "I read the traceback.",
            "token_usage": {"llm_call_seq": 1},
            "depends_on": [{"instruction_id": "tr-1", "confidence": 0.9}],
        },
    ]
    record_post_call_attribution(trace_id, instructions=instructions_after_llm1)
    assert state.pending_prompt_items == []
    assert state.cum_tokens.get(obs_id, 0.0) > cum_tokens_before
    assert state.cum_weighted.get(obs_id, 0.0) > 0.0
    ratio_after_post = cumulative_ratio_before_step(
        obs_id, state.cum_tokens, state.cum_weighted
    )

    instructions_after_llm1.append(
        {
            "id": "out-2",
            "runtime_step": 6,
            "arbiteros_ref_kind": "LLMOUTPUT",
            "content": "Next step reasoning.",
            "token_usage": {"llm_call_seq": 2},
            "depends_on": [],
        }
    )
    request2 = {
        **request,
        "messages": [
            *request["messages"],
            {
                "role": "assistant",
                "content": "[ARBITEROS_REF id=out-1 kind=LLMOUTPUT]\nI read the traceback.",
            },
        ],
    }
    result2 = check_precall_policy(
        trace_id=trace_id,
        current_request=request2,
        instructions=instructions_after_llm1,
        policy_classes=[CostDoctorPreCallPolicy],
    )
    assert result2.modified in {True, False}
    snap = state.last_step_record.get("cumulative_ratio_snapshot") or {}
    assert obs_id in snap
    assert snap[obs_id] == pytest.approx(ratio_after_post, rel=1e-4)


def test_litellm_post_call_success_invokes_record_post_call_attribution():
    """Guard: async_post_call_success_hook must call record_post_call_attribution."""
    from pathlib import Path as _Path

    callback_path = (
        _Path(__file__).resolve().parents[1]
        / "arbiteros_kernel"
        / "litellm_callback.py"
    )
    source = callback_path.read_text(encoding="utf-8")
    assert "async def async_post_call_success_hook" in source
    hook_start = source.index("async def async_post_call_success_hook")
    hook_end = source.index("async def async_post_call_streaming_hook", hook_start)
    hook_body = source[hook_start:hook_end]
    assert "record_post_call_attribution(" in hook_body
    assert "cost_down_phase() in {\"B\", \"C\", \"D\"}" in hook_body.replace("'", '"') or (
        '{"B", "C", "D"}' in hook_body
    )

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
