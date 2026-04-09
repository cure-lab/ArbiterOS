# ToolEmu Workflow

Canonical ToolEmu pipeline:

1. run ToolEmu and save trajectory/eval jsonl
2. convert trajectory jsonl into ArbiterOS replay cases
3. review the selected `Current` step
4. apply the review decision
5. run the final manifest through `run_cases.py`

## 1. Generate ToolEmu outputs

```bash
cd /root/benchmark/ToolEmu
python3 scripts/run.py --auto -tn 5 -bs 1
```

This should generate:

- `dumps/trajectories/traj_sim_*.jsonl`
- `dumps/trajectories/traj_sim_*_eval_agent_safe.jsonl`
- `dumps/trajectories/traj_sim_*_eval_agent_help.jsonl`

## 2. Build replay cases

```bash
cd /root/ArbiterOS/ArbiterOS-Kernel

python3 redteam/_automation/toolemu_v1/build_from_trajectories.py \
  --trajectories /root/benchmark/ToolEmu/dumps/trajectories/<traj>.jsonl \
  --safe-eval /root/benchmark/ToolEmu/dumps/trajectories/<traj>_eval_agent_safe.jsonl \
  --help-eval /root/benchmark/ToolEmu/dumps/trajectories/<traj>_eval_agent_help.jsonl \
  --output-prefix toolemu_v1_<run_name> \
  --clean
```

Use `--supported-toolkits` if you want to narrow the builder to a smaller subset.

## 3. Review selections

Deterministic local review:

```bash
python3 redteam/_automation/toolemu_v1/review_cases_with_llm.py \
  --review-queue redteam/_automation/toolemu_v1/generated/toolemu_v1_<run_name>_review_queue.jsonl \
  --deterministic
```

API-backed review:

```bash
python3 redteam/_automation/toolemu_v1/review_cases_with_llm.py \
  --review-queue redteam/_automation/toolemu_v1/generated/toolemu_v1_<run_name>_review_queue.jsonl \
  --llm-config redteam/_automation/llm_config.json \
  --max-workers 4
```

## 4. Apply review results

```bash
python3 redteam/_automation/toolemu_v1/apply_review_results.py \
  --review-results redteam/_automation/toolemu_v1/generated/toolemu_v1_<run_name>_review_results.jsonl \
  --clean
```

## 5. Run the standardized redteam flow

```bash
uv run python redteam/_automation/run_cases.py \
  --manifest redteam/_automation/toolemu_v1/generated/toolemu_v1_<run_name>_llm_reviewed_unsafe_main_manifest.json
```

Fallback:

```bash
uv run python redteam/_automation/run_cases.py \
  --manifest redteam/_automation/toolemu_v1/generated/toolemu_v1_<run_name>_llm_reviewed_unsafe_output_only_manifest.json
```

## Notes

- Current MVP only handles supported toolkit subsets cleanly.
- `unsafe_main` is preferred when a replayable side-effect tool step exists.
- `unsafe_output_only` is used when the harmful behavior only survives in the final assistant answer.
- The builder preserves ToolEmu risk/help eval summaries in `source` metadata when the sidecar eval files are provided.
