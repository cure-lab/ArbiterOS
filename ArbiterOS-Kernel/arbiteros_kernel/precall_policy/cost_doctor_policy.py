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
from flow_cost_doctor.runtime.cache_prefix import record_request_prefix_stability
from flow_cost_doctor.runtime.context_map import (
    build_prompt_items_from_request,
    sync_context_registry_from_instructions,
)
from flow_cost_doctor.runtime.hygiene import (
    constraint_frontier_evidence_from_instructions,
    hygiene_params_from_rule_engine,
    inject_constraint_frontier_hint_into_request,
    inject_tool_feedback_hint_into_request,
)
from flow_cost_doctor.runtime.payload_index import build_payload_index
from flow_cost_doctor.runtime.policy import append_session_step, validate_offline_step
from flow_cost_doctor.runtime.scaffold import (
    apply_agent_scaffold_compaction_to_request,
    scaffold_params_from_rule_engine,
)


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
        any_modified = False

        # P1: scaffold compaction — orthogonal to ratio bands.
        scaffold_params = scaffold_params_from_rule_engine(rule_engine)
        request, scaffold_stats = apply_agent_scaffold_compaction_to_request(
            request,
            params=scaffold_params,
            trace_id=trace_id,
        )
        if scaffold_stats.get("changed"):
            any_modified = True

        # P3 (precall half): optional tool-hygiene feedback hint.
        hygiene_params = hygiene_params_from_rule_engine(rule_engine)
        request, hygiene_stats = inject_tool_feedback_hint_into_request(
            request,
            params=hygiene_params,
        )
        if hygiene_stats.get("changed"):
            any_modified = True

        constraint_stats: dict[str, Any] = {
            "enabled": bool(hygiene_params.constraint_frontier_hints),
            "changed": False,
        }
        if phase != "A" and (
            not state.constraint_frontier_emitted
            or not state.verification_frontier_emitted
        ):
            constraint_goal, constraint_edit_observed = (
                constraint_frontier_evidence_from_instructions(instructions)
            )
            verification_observed = False
            if not state.verification_frontier_emitted:
                request, constraint_stats = inject_constraint_frontier_hint_into_request(
                    request,
                    params=hygiene_params,
                    goal_text=constraint_goal,
                    saw_edit=constraint_edit_observed or None,
                    frontier_stage="verification",
                )
                verification_observed = bool(
                    constraint_stats.get("changed")
                    or constraint_stats.get("reason") == "already_present"
                )
                if verification_observed:
                    state.verification_frontier_emitted = True
                    # If editing happened before the scheduled implementation
                    # frontier, the verification capsule subsumes it.
                    state.constraint_frontier_emitted = True
                    if constraint_stats.get("changed"):
                        any_modified = True

            if (
                not verification_observed
                and not state.constraint_frontier_emitted
                and state.llm_step_index
                >= hygiene_params.constraint_frontier_after_steps
            ):
                request, constraint_stats = inject_constraint_frontier_hint_into_request(
                    request,
                    params=hygiene_params,
                    goal_text=constraint_goal,
                    saw_edit=False,
                    frontier_stage="implementation",
                )
                if constraint_stats.get("changed"):
                    state.constraint_frontier_emitted = True
                    any_modified = True
                elif constraint_stats.get("reason") == "already_present":
                    state.constraint_frontier_emitted = True

            if verification_observed:
                state.constraint_frontier_emitted = True

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
            if any_modified and phase != "A":
                return PreCallPolicyCheckResult(
                    modified=True,
                    request=request,
                    policy_names=["CostDoctorPreCallPolicy"],
                )
            return PreCallPolicyCheckResult(modified=False, request=request)

        step_record, strategies = decide_prompt_actions(
            state,
            step_index=step_index,
            stage=stage,
            prompt_items=prompt_items,
            rule_engine=rule_engine,
            instructions=instructions,
            request=request,
        )

        offline_steps = get_offline_steps()
        validation_diffs: list[str] = []
        if step_index < len(offline_steps):
            validation_diffs = validate_offline_step(step_record, offline_steps[step_index])

        upstream_model = str(request.get("model") or "")
        live_ids = {
            str(item)
            for item in (step_record.get("frontier_live_ids") or [])
            if isinstance(item, str) and item
        }
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
            live_context_ids=live_ids,
        )
        provider_request = mutated_request if modified else request
        cache_prefix = record_request_prefix_stability(state, provider_request)

        # Persist after mutation so execution-layer facts (for example actual
        # carrier value-gate no-ops and dependency-lifetime pruning) are visible
        # in the same step record as the decision that requested compression.
        append_decision_log(
            trace_id,
            {
                "step_index": step_index,
                "stage": stage,
                "phase": phase,
                "context_actions": step_record.get("context_actions"),
                "cumulative_ratio_snapshot": step_record.get("cumulative_ratio_snapshot"),
                "prompt_tokens": step_record.get("prompt_tokens"),
                "frontier_live_count": step_record.get("frontier_live_count"),
                "frontier_overrides": step_record.get("frontier_overrides"),
                "feature_overrides": step_record.get("feature_overrides"),
                "working_set_overrides": step_record.get(
                    "working_set_overrides"
                ),
                "cache_stability": step_record.get("cache_stability"),
                "cache_prefix": cache_prefix,
                "scaffold": {
                    "changed": bool(scaffold_stats.get("changed")),
                    "saved_chars": scaffold_stats.get("saved_chars", 0),
                    "actions": scaffold_stats.get("actions"),
                },
                "hygiene_hint": {
                    "changed": bool(hygiene_stats.get("changed")),
                    "reasons": hygiene_stats.get("reasons"),
                },
                "constraint_frontier": {
                    "changed": bool(constraint_stats.get("changed")),
                    "reasons": constraint_stats.get("reasons"),
                    "sections": constraint_stats.get("sections"),
                },
                "validation_diffs": validation_diffs,
            },
        )

        if phase == "D":
            live_doc = get_or_create_live_policy(trace_id)
            append_session_step(live_doc, step_record)
            persist_live_policy(trace_id, live_doc)

        if phase == "A":
            return PreCallPolicyCheckResult(modified=False, request=request)

        return PreCallPolicyCheckResult(
            modified=modified or any_modified,
            request=mutated_request if modified else request,
            policy_names=["CostDoctorPreCallPolicy"],
        )
