#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import llm_utils
from tool_mapping import MappedToolCall, SUPPORTED_TOOLKITS_DEFAULT, map_tool_call, safe_json_loads, short_snippet, slugify


SCRIPT_DIR = Path(__file__).resolve().parent
AUTOMATION_ROOT = SCRIPT_DIR.parent
REDTEAM_ROOT = AUTOMATION_ROOT.parent
REPO_ROOT = REDTEAM_ROOT.parent
GENERATED_ROOT = SCRIPT_DIR / "generated"


@dataclass
class ConvertedStep:
    raw_message_index: int
    kind: str
    payload: dict[str, Any]
    step_kind: str | None
    flow_kind: str | None
    label: str | None
    target: str | None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def parse_tool_result_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=False)


def make_assistant_text_payload(content: str, *, topic: str = "toolemu_replay") -> str:
    payload = {
        "category": "COGNITIVE_CORE__RESPOND",
        "topic": topic,
        "content": content,
    }
    return json.dumps(payload, ensure_ascii=False)


def read_eval_rows(path: Path | None) -> dict[int, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    rows = load_jsonl(path)
    indexed: dict[int, dict[str, Any]] = {}
    for idx, row in enumerate(rows):
        eval_id = row.get("eval_id")
        key = eval_id if isinstance(eval_id, int) else idx
        indexed[key] = row
    return indexed


def normalize_eval_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "qualitative_label": row.get("Overall Qualitative Label"),
        "quantitative_score": row.get("Overall Quantitative Score"),
        "summary": row.get("Evaluator Log Summary"),
        "eval_scores": row.get("eval_scores"),
        "eval_id": row.get("eval_id"),
    }


def parse_trajectory_into_steps(
    trajectory: dict[str, Any],
    *,
    supported_toolkits: tuple[str, ...],
) -> tuple[list[ConvertedStep], list[dict[str, Any]], list[int]]:
    benchmark_case = trajectory.get("case") if isinstance(trajectory.get("case"), dict) else {}
    benchmark_case_name = str(benchmark_case.get("name") or f"case_{trajectory.get('case_idx', 'x')}")
    steps: list[ConvertedStep] = []
    candidates: list[dict[str, Any]] = []
    assistant_text_raw_indices: list[int] = []
    seen_tool_call_ids: list[str] = []

    for logical_index, item in enumerate(trajectory.get("intermediate_steps") or []):
        if not isinstance(item, list) or len(item) != 2:
            continue
        agent_part, simulator_part = item
        if not isinstance(agent_part, list) or not agent_part:
            continue
        action_name = agent_part[0]
        if not isinstance(action_name, str):
            continue
        tool_args = safe_json_loads(agent_part[1] if len(agent_part) > 1 else {})
        mapped: MappedToolCall = map_tool_call(
            tool_name=action_name,
            tool_args=tool_args,
            benchmark_case_name=benchmark_case_name,
            supported_toolkits=supported_toolkits,
        )
        tool_call_id = f"call_toolemu_{slugify(benchmark_case_name)}_{logical_index}_0"
        replay_args = dict(mapped.replay_arguments)
        replay_args["reference_tool_id"] = list(seen_tool_call_ids)
        steps.append(
            ConvertedStep(
                raw_message_index=logical_index,
                kind="assistant",
                payload={
                    "kind": "assistant",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": mapped.replay_tool_name,
                                    "arguments": json.dumps(replay_args, ensure_ascii=False),
                                },
                            }
                        ],
                        "tag": {},
                    },
                },
                step_kind=mapped.step_kind,
                flow_kind=mapped.flow_kind,
                label=mapped.label,
                target=mapped.target,
            )
        )
        candidates.append(
            {
                "raw_message_index": logical_index,
                "step_kind": mapped.step_kind,
                "flow_kind": mapped.flow_kind,
                "label": mapped.label,
                "target": mapped.target,
                "original_tool_name": action_name,
                "replay_tool_name": mapped.replay_tool_name,
                "dangerous_hint": mapped.dangerous_hint,
            }
        )
        seen_tool_call_ids.append(tool_call_id)

        simulator_observation = None
        if isinstance(simulator_part, list) and simulator_part:
            simulator_observation = simulator_part[0]
        tag: dict[str, Any] = {}
        if mapped.dangerous_hint:
            tag = {
                "security_type": {
                    "trustworthiness": "LOW",
                    "confidence": "MEDIUM",
                }
            }
        steps.append(
            ConvertedStep(
                raw_message_index=logical_index,
                kind="tool",
                payload={
                    "kind": "tool",
                    "tool_call_id": tool_call_id,
                    "tool_name": mapped.replay_tool_name,
                    "arguments": replay_args,
                    "result": parse_tool_result_text(simulator_observation),
                    "tag": tag,
                },
                step_kind=mapped.step_kind,
                flow_kind=mapped.flow_kind,
                label=mapped.label,
                target=mapped.target,
            )
        )

    output_text = str(trajectory.get("output") or "").strip()
    if output_text:
        raw_index = len(trajectory.get("intermediate_steps") or [])
        steps.append(
            ConvertedStep(
                raw_message_index=raw_index,
                kind="assistant",
                payload={
                    "kind": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": make_assistant_text_payload(output_text),
                        "tag": {},
                    },
                },
                step_kind="dangerous_reply",
                flow_kind="dangerous_reply",
                label="reply",
                target=short_snippet(output_text, 120),
            )
        )
        assistant_text_raw_indices.append(raw_index)

    return steps, candidates, assistant_text_raw_indices


def choose_initial_selection(
    *,
    candidates: list[dict[str, Any]],
    assistant_text_raw_indices: list[int],
) -> tuple[str, int | None, dict[str, Any] | None]:
    dangerous_candidates = [item for item in candidates if item.get("dangerous_hint")]
    if dangerous_candidates:
        selected = dangerous_candidates[0]
        return "unsafe_main", int(selected["raw_message_index"]), selected

    side_effect_candidates = [
        item
        for item in candidates
        if item.get("step_kind") not in {"read_context", "web_read"}
    ]
    if side_effect_candidates:
        selected = side_effect_candidates[0]
        return "unsafe_main", int(selected["raw_message_index"]), selected

    if assistant_text_raw_indices:
        return "unsafe_output_only", assistant_text_raw_indices[-1], None

    return "exclude", None, None


def build_case_id(prefix: str, trajectory: dict[str, Any], *, bucket: str, raw_message_index: int | None) -> str:
    benchmark_case = trajectory.get("case") if isinstance(trajectory.get("case"), dict) else {}
    case_name = slugify(str(benchmark_case.get("name") or f"case-{trajectory.get('case_idx', 'x')}"))
    case_idx = trajectory.get("case_idx")
    base = f"{prefix}_{case_name}"
    if isinstance(case_idx, int):
        base = f"{base}_case_{case_idx}"
    if bucket == "unsafe_output_only":
        return f"{base}_reply"
    if raw_message_index is None:
        raise ValueError("unsafe_main requires raw_message_index")
    return f"{base}_msg_{raw_message_index}"


def build_case_document(
    *,
    case_doc_id: str,
    trajectory_path: Path,
    trajectory: dict[str, Any],
    steps: list[ConvertedStep],
    selected_index: int,
    candidate: dict[str, Any] | None,
    bucket: str,
    safe_eval_row: dict[str, Any] | None,
    help_eval_row: dict[str, Any] | None,
    candidate_count: int,
) -> dict[str, Any]:
    current_step = steps[selected_index]
    if current_step.kind != "assistant":
        raise ValueError("selected current step must be assistant")
    benchmark_case = trajectory.get("case") if isinstance(trajectory.get("case"), dict) else {}
    prior = [step.payload for step in steps[:selected_index]]
    current = current_step.payload["message"]
    source = {
        "benchmark": "ToolEmu",
        "benchmark_protocol": "trajectory_replay_v1",
        "benchmark_case_id": trajectory.get("case_idx"),
        "benchmark_case_name": benchmark_case.get("name"),
        "source_trajectory_path": str(trajectory_path),
        "task_type": "unsafe",
        "selection_bucket": bucket,
        "selection_rationale": (
            f"Selected message {current_step.raw_message_index} as the initial replay Current step "
            f"for a ToolEmu trajectory."
        ),
        "current_step_kind": candidate["step_kind"] if candidate else "dangerous_reply",
        "current_flow_kind": candidate["flow_kind"] if candidate else "dangerous_reply",
        "current_step_label": candidate["label"] if candidate else "reply",
        "current_step_target": candidate["target"] if candidate else current_step.target,
        "candidate_count": candidate_count,
        "toolkits": benchmark_case.get("Toolkits") or [],
        "underspecifications": trajectory.get("underspecifications"),
        "risky_outcome": trajectory.get("risky_outcome"),
        "risky_actions": trajectory.get("risky_actions"),
        "expected_achievements": benchmark_case.get("Expected Achievements"),
        "potential_risky_outcomes": benchmark_case.get("Potential Risky Outcomes"),
        "potential_risky_actions": benchmark_case.get("Potential Risky Actions"),
        "unsafe_seed_filter": "supported_toolkits_only",
        "toolcall_risk_eval": normalize_eval_row(safe_eval_row),
        "helpfulness_eval": normalize_eval_row(help_eval_row),
    }
    notes = (
        "Fresh ToolEmu replay generated from benchmark trajectories. "
        "Current step may be refined by the per-case review workflow."
    )
    return {
        "trace_id": case_doc_id.replace("_", "-"),
        "prior": prior,
        "current": current,
        "source": source,
        "webui": {
            "mode": "single_prompt_manual_webui",
            "prompt": str(benchmark_case.get("User Instruction") or trajectory.get("input") or ""),
            "expected_result": "should_block",
            "notes": notes,
            "real_targets": [],
        },
    }


def build_manifest(*, version: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": version,
        "repo_root": str(REPO_ROOT),
        "cases_dir": str(REDTEAM_ROOT / "case"),
        "harness_module": "arbiteros_kernel.policy_test_harness",
        "harness_extra_args": [],
        "cases": cases,
    }


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    path.rmdir()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ArbiterOS replay cases from ToolEmu trajectories.")
    parser.add_argument("--trajectories", required=True)
    parser.add_argument("--safe-eval", default=None)
    parser.add_argument("--help-eval", default=None)
    parser.add_argument("--output-prefix", default=None)
    parser.add_argument("--supported-toolkits", nargs="*", default=list(SUPPORTED_TOOLKITS_DEFAULT))
    parser.add_argument("--case-name", action="append", default=[])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    trajectories_path = Path(args.trajectories).resolve()
    if not trajectories_path.exists():
        raise SystemExit(f"trajectory file not found: {trajectories_path}")
    safe_eval_path = Path(args.safe_eval).resolve() if args.safe_eval else None
    help_eval_path = Path(args.help_eval).resolve() if args.help_eval else None

    inferred_name = trajectories_path.stem
    prefix = args.output_prefix or f"toolemu_v1_{slugify(inferred_name)}"
    case_main_dir = REDTEAM_ROOT / "case" / f"{prefix}_unsafe_main"
    case_output_only_dir = REDTEAM_ROOT / "case" / f"{prefix}_unsafe_output_only"
    manifest_main_path = GENERATED_ROOT / f"{prefix}_unsafe_main_manifest.json"
    manifest_output_path = GENERATED_ROOT / f"{prefix}_unsafe_output_only_manifest.json"
    summary_path = GENERATED_ROOT / f"{prefix}_build_summary.json"
    review_queue_path = GENERATED_ROOT / f"{prefix}_review_queue.jsonl"

    if args.clean:
        for path in (case_main_dir, case_output_only_dir):
            if path.exists():
                remove_tree(path)
        for path in (manifest_main_path, manifest_output_path, summary_path, review_queue_path):
            if path.exists():
                path.unlink()

    trajectories = load_jsonl(trajectories_path)
    selected_names = {value for value in args.case_name if value}
    supported_toolkits = tuple(args.supported_toolkits)
    safe_eval_rows = read_eval_rows(safe_eval_path)
    help_eval_rows = read_eval_rows(help_eval_path)

    counters = Counter()
    manifest_main_cases: list[dict[str, Any]] = []
    manifest_output_cases: list[dict[str, Any]] = []
    review_records: list[dict[str, Any]] = []

    for row in trajectories:
        benchmark_case = row.get("case") if isinstance(row.get("case"), dict) else {}
        case_name = str(benchmark_case.get("name") or f"case_{row.get('case_idx', 'x')}")
        if selected_names and case_name not in selected_names:
            continue
        toolkits = benchmark_case.get("Toolkits") if isinstance(benchmark_case.get("Toolkits"), list) else []
        unsupported = [toolkit for toolkit in toolkits if toolkit not in supported_toolkits]
        if unsupported:
            counters["skipped_unsupported_toolkits"] += 1
            continue

        steps, candidates, assistant_text_raw_indices = parse_trajectory_into_steps(
            row,
            supported_toolkits=supported_toolkits,
        )
        if not steps:
            counters["skipped_empty_steps"] += 1
            continue

        bucket, selected_raw_index, selected_candidate = choose_initial_selection(
            candidates=candidates,
            assistant_text_raw_indices=assistant_text_raw_indices,
        )
        if bucket == "exclude" or selected_raw_index is None:
            counters["excluded_no_selection"] += 1
            continue

        selected_step_index = None
        for idx, step in enumerate(steps):
            if step.raw_message_index != selected_raw_index or step.kind != "assistant":
                continue
            is_tool_step = isinstance(step.payload.get("message", {}).get("tool_calls"), list)
            is_text_step = isinstance(step.payload.get("message", {}).get("content"), str)
            if bucket == "unsafe_main" and is_tool_step:
                selected_step_index = idx
                break
            if bucket == "unsafe_output_only" and is_text_step:
                selected_step_index = idx
                break
        if selected_step_index is None:
            counters["skipped_missing_selected_step"] += 1
            continue

        case_id = build_case_id(prefix, row, bucket=bucket, raw_message_index=selected_raw_index if bucket == "unsafe_main" else None)
        safe_eval_row = safe_eval_rows.get(int(row.get("case_idx"))) if isinstance(row.get("case_idx"), int) else None
        help_eval_row = help_eval_rows.get(int(row.get("case_idx"))) if isinstance(row.get("case_idx"), int) else None
        doc = build_case_document(
            case_doc_id=case_id,
            trajectory_path=trajectories_path,
            trajectory=row,
            steps=steps,
            selected_index=selected_step_index,
            candidate=selected_candidate,
            bucket=bucket,
            safe_eval_row=safe_eval_row,
            help_eval_row=help_eval_row,
            candidate_count=len(candidates),
        )

        if bucket == "unsafe_main":
            case_path = case_main_dir / f"{case_id}.json"
            llm_utils.write_json(case_path, doc)
            manifest_main_cases.append(
                {
                    "id": case_id,
                    "file": f"{case_main_dir.name}/{case_id}.json",
                    "category": "unsafe",
                    "enabled": True,
                    "should_block": True,
                    "notes": f"ToolEmu replay from {trajectories_path.name}",
                }
            )
            counters["generated_unsafe_main"] += 1
        else:
            case_path = case_output_only_dir / f"{case_id}.json"
            llm_utils.write_json(case_path, doc)
            manifest_output_cases.append(
                {
                    "id": case_id,
                    "file": f"{case_output_only_dir.name}/{case_id}.json",
                    "category": "unsafe",
                    "enabled": True,
                    "should_block": True,
                    "notes": f"ToolEmu output-only replay from {trajectories_path.name}",
                }
            )
            counters["generated_unsafe_output_only"] += 1

        review_records.append(
            {
                "case_id": case_id,
                "bucket": bucket,
                "trajectory_path": str(trajectories_path),
                "source_case_path": str(case_path),
                "benchmark_case_name": case_name,
                "benchmark_case_id": row.get("case_idx"),
                "selected": {
                    "raw_message_index": selected_raw_index,
                    "step_kind": selected_candidate["step_kind"] if selected_candidate else "dangerous_reply",
                    "flow_kind": selected_candidate["flow_kind"] if selected_candidate else "dangerous_reply",
                    "label": selected_candidate["label"] if selected_candidate else "reply",
                    "target": selected_candidate["target"] if selected_candidate else short_snippet(row.get("output") or "", 120),
                },
                "assistant_text_raw_indices": assistant_text_raw_indices,
                "candidates": candidates,
                "supported_toolkits": list(supported_toolkits),
            }
        )

        if args.limit is not None and sum(counters[key] for key in ("generated_unsafe_main", "generated_unsafe_output_only")) >= args.limit:
            break

    manifest_main_cases.sort(key=lambda item: item["id"])
    manifest_output_cases.sort(key=lambda item: item["id"])
    llm_utils.write_json(manifest_main_path, build_manifest(version=f"{prefix}_unsafe_main", cases=manifest_main_cases))
    llm_utils.write_json(manifest_output_path, build_manifest(version=f"{prefix}_unsafe_output_only", cases=manifest_output_cases))
    llm_utils.write_jsonl(review_queue_path, review_records)

    summary = {
        "trajectories": str(trajectories_path),
        "safe_eval": str(safe_eval_path) if safe_eval_path else None,
        "help_eval": str(help_eval_path) if help_eval_path else None,
        "output_prefix": prefix,
        "supported_toolkits": list(supported_toolkits),
        "artifacts": {
            "unsafe_main_manifest": str(manifest_main_path),
            "unsafe_output_only_manifest": str(manifest_output_path),
            "review_queue": str(review_queue_path),
        },
        "counts": dict(counters),
    }
    llm_utils.write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
