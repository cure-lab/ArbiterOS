"""Cost Doctor precall policy for ArbiterOS Kernel."""

from __future__ import annotations

import copy
from typing import Any

from arbiteros_kernel.precall_policy.policy import PreCallPolicy
from arbiteros_kernel.precall_policy_check import PreCallPolicyCheckResult
from arbiteros_kernel.precall_policy.cost_doctor_runtime import (
    append_decision_log,
    cost_down_enabled,
    cost_down_phase,
    get_offline_steps,
    get_or_create_live_policy,
    get_or_create_state,
    get_rule_engine,
    persist_live_policy,
)
from arbiteros_kernel.precall_policy.prompt_mutator import apply_context_actions_to_request

from flow_cost_doctor.runtime.actions import decide_prompt_actions, next_llm_step_index
from flow_cost_doctor.runtime.context_map import (
    build_prompt_items_from_request,
    sync_context_registry_from_instructions,
)
from flow_cost_doctor.runtime.payload_index import build_payload_index
from flow_cost_doctor.runtime.policy import append_session_step, validate_offline_step


class CostDoctorPreCallPolicy(PreCallPolicy):
    """Apply Cost Doctor cumulative-ratio context optimization before upstream LLM calls."""

    def check(
        self,
        instructions: list[dict[str, Any]],
        current_request: dict[str, Any],
        trace_id: str,
        **kwargs: Any,
    ) -> PreCallPolicyCheckResult:
        if not cost_down_enabled() or not trace_id:
            return PreCallPolicyCheckResult(modified=False, request=current_request)

        request = copy.deepcopy(current_request)
        phase = cost_down_phase()
        state = get_or_create_state(trace_id)
        rule_engine = get_rule_engine()

        # Live registry is rebuilt only from the current instruction history.
        # Offline context_seeds / live_mode are intentionally not accepted
        # (would leak instance-level offline diagnostics into decisions).
        sync_context_registry_from_instructions(state, instructions)

        payload_index = build_payload_index(
            instructions,
            state.instruction_to_context,
            context_aliases=state.context_aliases,
            call_id_to_context_ids=state.call_id_to_context_ids,
        )
        state.payload_index = payload_index

        step_index = next_llm_step_index(state)
        stage = state.stage or "planning"

        prompt_items = build_prompt_items_from_request(
            state,
            request,
            step_index=step_index,
        )
        if not prompt_items:
            return PreCallPolicyCheckResult(modified=False, request=request)

        step_record, strategies = decide_prompt_actions(
            state,
            step_index=step_index,
            stage=stage,
            prompt_items=prompt_items,
            rule_engine=rule_engine,
        )

        offline_steps = get_offline_steps()
        validation_diffs: list[str] = []
        if step_index < len(offline_steps):
            validation_diffs = validate_offline_step(step_record, offline_steps[step_index])

        append_decision_log(
            trace_id,
            {
                "step_index": step_index,
                "stage": stage,
                "phase": phase,
                "context_actions": step_record.get("context_actions"),
                "cumulative_ratio_snapshot": step_record.get("cumulative_ratio_snapshot"),
                "prompt_tokens": step_record.get("prompt_tokens"),
                "validation_diffs": validation_diffs,
            },
        )

        if phase == "D":
            live_doc = get_or_create_live_policy(trace_id)
            append_session_step(live_doc, step_record)
            persist_live_policy(trace_id, live_doc)

        upstream_model = str(request.get("model") or "")
        mutated_request, modified = apply_context_actions_to_request(
            request,
            instruction_to_context=state.instruction_to_context,
            context_actions=step_record.get("context_actions") or {},
            strategies_by_context=strategies,
            step_index=step_index,
            stage=stage,
            phase=phase,
            payload_index=payload_index,
            compress_cache=state.compress_cache,
            upstream_model=upstream_model,
            context_aliases=state.context_aliases,
            rule_engine=rule_engine,
        )

        if phase == "A":
            return PreCallPolicyCheckResult(modified=False, request=request)

        return PreCallPolicyCheckResult(
            modified=modified,
            request=mutated_request,
            policy_names=["CostDoctorPreCallPolicy"],
        )
