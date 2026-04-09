#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
AUTOMATION_ROOT = SCRIPT_DIR.parent
REDTEAM_ROOT = AUTOMATION_ROOT.parent
GENERATED_ROOT = SCRIPT_DIR / "generated"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_from_trajectories as builder
import llm_utils
from review_cases_with_llm import resolve_default_artifacts, resolve_prefix_from_review_queue


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
    parser = argparse.ArgumentParser(description="Apply review decisions to ToolEmu replay cases.")
    parser.add_argument("--review-results", required=True)
    parser.add_argument("--review-queue", default=None)
    parser.add_argument("--output-prefix", default=None)
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--on-invalid", choices=["skip", "keep"], default="keep")
    return parser.parse_args()


def build_case_id(prefix: str, trajectory: dict[str, object], *, bucket: str, raw_message_index: int | None) -> str:
    return builder.build_case_id(prefix, trajectory, bucket=bucket, raw_message_index=raw_message_index)


def load_matching_trajectory(path: Path, *, case_name: str | None, case_idx: int | None) -> dict[str, object] | None:
    for row in builder.load_jsonl(path):
        case = row.get("case") if isinstance(row.get("case"), dict) else {}
        if case_name is not None and case.get("name") != case_name:
            continue
        if case_idx is not None and row.get("case_idx") != case_idx:
            continue
        return row
    return None


def load_initial_case_doc(review_item: dict[str, object] | None) -> dict[str, object] | None:
    if not isinstance(review_item, dict):
        return None
    source_case_path = review_item.get("source_case_path")
    if not isinstance(source_case_path, str) or not source_case_path.strip():
        return None
    path = Path(source_case_path).resolve()
    if not path.exists():
        return None
    data = llm_utils.read_json(path)
    return data if isinstance(data, dict) else None


def preserve_initial_metadata(doc: dict[str, object], initial_case_doc: dict[str, object] | None) -> None:
    if not initial_case_doc:
        return
    source = doc.get("source") if isinstance(doc.get("source"), dict) else None
    initial_source = initial_case_doc.get("source") if isinstance(initial_case_doc.get("source"), dict) else None
    if source is None or initial_source is None:
        return
    for key in ("toolcall_risk_eval", "helpfulness_eval"):
        if key in initial_source:
            source[key] = initial_source.get(key)


def select_output_only_index(steps: list[builder.ConvertedStep], preferred_raw_index: int | None) -> int | None:
    assistant_text_indices = [
        idx
        for idx, step in enumerate(steps)
        if step.kind == "assistant" and isinstance(step.payload.get("message", {}).get("content"), str)
    ]
    if preferred_raw_index is not None:
        for idx in assistant_text_indices:
            if steps[idx].raw_message_index == preferred_raw_index:
                return idx
    if assistant_text_indices:
        return assistant_text_indices[-1]
    return None


def main() -> int:
    args = parse_args()
    review_results_path = Path(args.review_results).resolve()
    review_rows = llm_utils.load_jsonl(review_results_path)
    if not review_rows:
        raise SystemExit(f"no review rows found: {review_results_path}")

    if args.review_queue:
        review_queue_path = Path(args.review_queue).resolve()
    else:
        inferred = review_results_path.name.replace("_review_results.jsonl", "_review_queue.jsonl")
        review_queue_path = review_results_path.with_name(inferred)
    prefix = args.output_prefix or f"{resolve_prefix_from_review_queue(review_queue_path)}_llm_reviewed"
    review_items_by_case = {
        str(item.get("case_id")): item
        for item in llm_utils.load_jsonl(review_queue_path)
        if isinstance(item, dict) and isinstance(item.get("case_id"), str)
    }

    case_main_dir = REDTEAM_ROOT / "case" / f"{prefix}_unsafe_main"
    case_output_dir = REDTEAM_ROOT / "case" / f"{prefix}_unsafe_output_only"
    manifest_main_path = GENERATED_ROOT / f"{prefix}_unsafe_main_manifest.json"
    manifest_output_path = GENERATED_ROOT / f"{prefix}_unsafe_output_only_manifest.json"
    summary_path = GENERATED_ROOT / f"{prefix}_apply_summary.json"

    if args.clean:
        for path in (case_main_dir, case_output_dir):
            if path.exists():
                remove_tree(path)
        for path in (manifest_main_path, manifest_output_path, summary_path):
            if path.exists():
                path.unlink()

    counters = Counter()
    main_cases: list[dict[str, object]] = []
    output_cases: list[dict[str, object]] = []

    for row in review_rows:
        if not row.get("ok"):
            counters["invalid_review"] += 1
            if args.on_invalid == "skip":
                continue
            current = (row.get("review_payload") or {}).get("current_selection") or {}
            row = {
                **row,
                "recommended_action": "keep_current",
                "recommended_bucket": current.get("bucket", "unsafe_main"),
                "recommended_message_index": (current.get("selected") or {}).get("raw_message_index"),
                "selection_rationale": "Fell back to original selection because review output was invalid.",
                "confidence": "fallback",
            }
            counters["invalid_review_kept_original"] += 1

        action = str(row.get("recommended_action") or "")
        if action == "exclude_case":
            counters["excluded"] += 1
            continue

        review_item = review_items_by_case.get(str(row.get("case_id") or ""))
        initial_case_doc = load_initial_case_doc(review_item)
        review_payload = row.get("review_payload") or {}
        benchmark_case = review_payload.get("benchmark_case") or {}
        trajectory_metadata = review_payload.get("trajectory_metadata") or {}
        case_name = benchmark_case.get("name")
        case_idx = trajectory_metadata.get("case_idx")
        trajectory_path = Path(str(row.get("trajectory_path") or "")).resolve()
        trajectory = load_matching_trajectory(trajectory_path, case_name=case_name, case_idx=case_idx)
        if trajectory is None:
            counters["missing_trajectory"] += 1
            continue

        current_selection = review_payload.get("current_selection") or {}
        supported_toolkits = tuple((review_payload.get("supported_toolkits") or []) or current_selection.get("supported_toolkits") or [])
        if not supported_toolkits:
            supported_toolkits = builder.SUPPORTED_TOOLKITS_DEFAULT
        steps, candidates, assistant_text_raw_indices = builder.parse_trajectory_into_steps(trajectory, supported_toolkits=tuple(supported_toolkits))
        candidate_by_raw_index = {candidate["raw_message_index"]: candidate for candidate in candidates}

        if action == "mark_output_only" or str(row.get("recommended_bucket") or "") == "unsafe_output_only":
            selected_step_index = select_output_only_index(steps, row.get("recommended_message_index"))
            if selected_step_index is None:
                counters["skipped_missing_output_only_step"] += 1
                continue
            case_id = build_case_id(prefix, trajectory, bucket="unsafe_output_only", raw_message_index=None)
            doc = builder.build_case_document(
                case_doc_id=case_id,
                trajectory_path=trajectory_path,
                trajectory=trajectory,
                steps=steps,
                selected_index=selected_step_index,
                candidate=None,
                bucket="unsafe_output_only",
                safe_eval_row=None,
                help_eval_row=None,
                candidate_count=len(candidates),
            )
            preserve_initial_metadata(doc, initial_case_doc)
            preserve_initial_metadata(doc, initial_case_doc)
            doc["source"]["llm_review"] = {
                    "recommended_action": action,
                    "recommended_bucket": row.get("recommended_bucket"),
                    "recommended_message_index": row.get("recommended_message_index"),
                    "selection_rationale": row.get("selection_rationale"),
                    "confidence": row.get("confidence"),
                }
            case_path = case_output_dir / f"{case_id}.json"
            llm_utils.write_json(case_path, doc)
            output_cases.append(
                {
                    "id": case_id,
                    "file": f"{case_output_dir.name}/{case_id}.json",
                    "category": "unsafe",
                    "enabled": True,
                    "should_block": True,
                    "notes": f"LLM-reviewed ToolEmu replay from {trajectory_path.name}",
                }
            )
            counters["generated_output_only"] += 1
            continue

        raw_message_index = row.get("recommended_message_index")
        if not isinstance(raw_message_index, int):
            counters["skipped_missing_message_index"] += 1
            continue
        selected_step_index = None
        for idx, step in enumerate(steps):
            if step.raw_message_index == raw_message_index and step.kind == "assistant" and isinstance(step.payload.get("message", {}).get("tool_calls"), list):
                selected_step_index = idx
                break
        if selected_step_index is None:
            counters["skipped_missing_main_step"] += 1
            continue

        case_id = build_case_id(prefix, trajectory, bucket="unsafe_main", raw_message_index=raw_message_index)
        doc = builder.build_case_document(
            case_doc_id=case_id,
            trajectory_path=trajectory_path,
            trajectory=trajectory,
            steps=steps,
            selected_index=selected_step_index,
            candidate=candidate_by_raw_index.get(raw_message_index),
            bucket="unsafe_main",
            safe_eval_row=None,
            help_eval_row=None,
            candidate_count=len(candidates),
        )
        doc["source"]["llm_review"] = {
            "recommended_action": action,
            "recommended_bucket": row.get("recommended_bucket"),
            "recommended_message_index": raw_message_index,
            "selection_rationale": row.get("selection_rationale"),
            "confidence": row.get("confidence"),
        }
        case_path = case_main_dir / f"{case_id}.json"
        llm_utils.write_json(case_path, doc)
        main_cases.append(
            {
                "id": case_id,
                "file": f"{case_main_dir.name}/{case_id}.json",
                "category": "unsafe",
                "enabled": True,
                "should_block": True,
                "notes": f"LLM-reviewed ToolEmu replay from {trajectory_path.name}",
            }
        )
        counters["generated_unsafe_main"] += 1

    main_cases.sort(key=lambda item: str(item["id"]))
    output_cases.sort(key=lambda item: str(item["id"]))
    llm_utils.write_json(manifest_main_path, builder.build_manifest(version=f"{prefix}_unsafe_main", cases=main_cases))
    llm_utils.write_json(manifest_output_path, builder.build_manifest(version=f"{prefix}_unsafe_output_only", cases=output_cases))
    summary = {
        "review_results": str(review_results_path),
        "review_queue": str(review_queue_path),
        "output_prefix": prefix,
        "artifacts": {
            "unsafe_main_manifest": str(manifest_main_path),
            "unsafe_output_only_manifest": str(manifest_output_path),
        },
        "counts": dict(counters),
    }
    llm_utils.write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
