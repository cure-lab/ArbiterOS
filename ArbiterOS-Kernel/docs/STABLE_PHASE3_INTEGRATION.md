# Stable Phase 3 Runtime Integration

This branch is the ArbiterOS execution companion for CostDoctor branch
`feat/stable-phase3-costdown`.

## Request path

```text
LiteLLM async_pre_call_hook
  -> compact repeated agent scaffold
  -> apply Phase 3 activation/risk guards
  -> build tool-call dependency evidence graph
  -> compact stale/expired/superseded evidence
  -> preserve recent, referenced, focused, test, and source evidence
  -> send optimized messages to the provider
  -> record provider usage and per-action telemetry
```

The implementation is split deliberately:

- `cost/prompt_compaction.py`: repeated scaffold only;
- `cost/phase3_compression.py`: deterministic context planning and rewriting;
- `cost/down.py`: CostDoctor policy/config adapter;
- `litellm_callback.py`: pre-call hook and apply-patch lowering;
- `cost/telemetry.py`: provider usage and compression telemetry.

No model-based compressor is enabled by the Stable profile. The public profile
and all thresholds live in the CostDoctor companion branch.

## Telemetry contract

Every provider response records actual `usage.prompt_tokens`,
`usage.completion_tokens`, and `usage.total_tokens`. Pre-call rewrites record
separate explanatory estimates under:

- `prompt_compaction.estimated_input_tokens_saved`;
- `runtime_cost_down.estimated_input_tokens_saved`;
- `runtime_cost_down.actions`.

The estimated fields explain where savings came from. They are not used as the
headline A/B token result; the A/B report sums provider `usage.total_tokens` on
matched cases.

## Focused verification

```bash
uv run pytest -q tests/test_stable_phase3.py
```

The focused suite verifies scaffold preservation, dependency-aware evidence
pruning, duplicate elision, and opt-in apply-patch lowering.
