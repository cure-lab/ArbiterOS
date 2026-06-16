#!/usr/bin/env python3

"""
Convert Charades-Ego Dataset to Router Planner Format

This script converts the Charades-Ego dataset into the jsonl format used by LLMRouter.
It uses a VLM to describe a short time window of video frames (stitched as a filmstrip),
then builds a text query and evaluates multiple candidate LLMs to produce routing data.

Input: Charades-Ego dataset (CSV annotations + Video files)
Output: 
    - default_routing_train_data.jsonl
    - default_routing_test_data.jsonl
    - query_embeddings.pt
Note: outputs are written to the current working directory.

Usage:
    python charades_ego_to_json.py --data_root /path/to/data --sample_size 100 \
        --task_type activity --top_k 1 --num_frames 5
"""

import os
import json
import argparse
import time
import random
import re
import pandas as pd
import numpy as np
import cv2
from PIL import Image

from llmrouter.data import vlm_describe_images
from llmrouter.utils import (
    setup_environment,
    process_final_data, 
    call_api, 
    calculate_task_performance,
    get_longformer_embedding,
)
from llmrouter.data import batch_vlm_describe_images
from llmrouter.utils.prompting import format_charades_ego_prompt
from llmrouter.utils.evaluation import last_boxed_only_string, remove_boxed

# Setup environment
setup_environment()

# -----------------------------------------------------------------------------
# Charades(-Ego) annotation parsing helpers
# -----------------------------------------------------------------------------

def parse_action_segments(actions_value, classes):
    """
    Parse Charades(-Ego) `actions` field into time-stamped action segments.
    """
    segments = []
    if actions_value is None:
        return segments

    s = str(actions_value).strip()
    if not s or s.lower() == "nan":
        return segments

    for raw in s.split(";"):
        item = raw.strip()
        if not item:
            continue
        parts = item.split()
        if len(parts) < 3:
            continue

        cls_id = str(parts[0]).lower()
        if cls_id not in classes:
            continue

        try:
            start = float(parts[1])
            end = float(parts[2])
        except Exception:
            continue

        if end <= start:
            continue

        segments.append({
            "cls_id": cls_id,
            "label": classes[cls_id],
            "start": start,
            "end": end
        })

    return segments


def load_id_label_file(path: str) -> dict:
    """
    Load a mapping file with one entry per line: <id> <label...>
    Supports both prefixed ids (e.g. v03) and numeric ids (e.g. 3).
    """
    out = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                continue
            rid, label = parts
            # No normalization; use id as-is
            out[rid] = label.strip()
    return out

def load_action_to_verb_object(mapping_path: str) -> tuple[dict, dict]:
    """
    Load Charades action -> (object, verb) mapping from Charades_v1_mapping.txt.
    """
    action_to_verb: dict[str, str] = {}
    action_to_object: dict[str, str] = {}

    with open(mapping_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue

            a, o, v = parts[0], parts[1], parts[2]
            if a and v:
                action_to_verb[a] = v
            if a and o:
                action_to_object[a] = o

    return action_to_verb, action_to_object

# -----------------------------------------------------------------------------
# Video frame sampling (time-windowed)
# -----------------------------------------------------------------------------

def extract_frames_in_time_window(video_path, start_sec, end_sec, num_frames):
    """
    Extract frames ONLY within [start_sec, end_sec] of a video.
    Output: List of PIL Images
    """
    cap = cv2.VideoCapture(video_path)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    frames = []

    start_frame = int(max(0.0, start_sec) * fps)
    end_frame = int(max(0.0, end_sec) * fps)

    # Clamp and ensure at least 1 frame range
    start_frame = max(0, min(start_frame, total_frames - 1))
    end_frame = max(0, min(end_frame, total_frames - 1))
    if end_frame <= start_frame:
        end_frame = min(total_frames - 1, start_frame + max(1, num_frames))

    mid = int((start_frame + end_frame) / 2)
    window_frames = max(1, int(5.0 * fps))  # 5 seconds
    inner_start = max(0, mid - window_frames // 2)
    inner_end = min(total_frames - 1, inner_start + window_frames)

    indices = np.linspace(inner_start, inner_end, num_frames, dtype=int)
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ret, frame = cap.read()
        if ret:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(Image.fromarray(frame_rgb))

    cap.release()
    return frames

# -----------------------------------------------------------------------------
# Output parsing: extract label IDs (e.g., c### / v### / o###) from model outputs
# -----------------------------------------------------------------------------

def extract_ids(raw_response: str, valid_ids: list, top_k: int):
    """
    Extract up to top_k ids like c### / v### / o### from model output.
    Returns a de-duplicated list in appearance order.
    """
    valid_set = set(valid_ids or [])
    if not isinstance(raw_response, str) or top_k <= 0:
        return []

    found = []
    seen = set()
    for m in re.finditer(r"\b([cvoCVO]\d{1,4})\b", raw_response.strip()):
        rid = m.group(1).lower()
        if rid in valid_set and rid not in seen:
            found.append(rid)
            seen.add(rid)
            if len(found) >= top_k:
                break
    return found

# ============================================================================
# Main Conversion Logic
# ============================================================================

def load_charades_metadata(data_root):
    """
    Load all Charades-Ego metadata files.
    
    Returns:
        dict with keys: classes, verb_id_to_label, obj_id_to_label, 
                       action_to_verb, action_to_object, 
                       activity_ids_sorted, verb_ids_sorted, obj_ids_sorted
    """
    charades_meta_dir = os.path.join(data_root, "CharadesEgo")
    classes_path = os.path.join(charades_meta_dir, "Charades_v1_classes.txt")
    mapping_path = os.path.join(charades_meta_dir, "Charades_v1_mapping.txt")
    verbclasses_path = os.path.join(charades_meta_dir, "Charades_v1_verbclasses.txt")
    objectclasses_path = os.path.join(charades_meta_dir, "Charades_v1_objectclasses.txt")
    
    # Load classes
    classes = {}
    with open(classes_path, 'r') as f:
        for line in f:
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                classes[parts[0]] = parts[1]
    classes = {str(k).lower(): v for k, v in classes.items()}
    
    # Load verb/object mappings
    verb_id_to_label = load_id_label_file(verbclasses_path)
    obj_id_to_label = load_id_label_file(objectclasses_path)
    action_to_verb, action_to_object = load_action_to_verb_object(mapping_path)

    # --------------------------------------------------------------------
    # Build class id <-> label mapping
    # --------------------------------------------------------------------
    activity_ids_sorted = sorted(
        list(classes.keys()),
        key=lambda x: int(re.findall(r"\d+", x)[0]) if re.findall(r"\d+", x) else 10**9
    )
    verb_ids_sorted = sorted(verb_id_to_label.keys(), key=lambda x: int(re.findall(r"\d+", x)[0]))
    obj_ids_sorted = sorted(obj_id_to_label.keys(), key=lambda x: int(re.findall(r"\d+", x)[0]))
    
    return {
        'classes': classes,
        'verb_id_to_label': verb_id_to_label,
        'obj_id_to_label': obj_id_to_label,
        'action_to_verb': action_to_verb,
        'action_to_object': action_to_object,
        'activity_ids_sorted': activity_ids_sorted,
        'verb_ids_sorted': verb_ids_sorted,
        'obj_ids_sorted': obj_ids_sorted,
    }

def load_and_sample_paired_data(data_root, sample_size, random_seed=None):
    """
    Load and sample paired ego-exo video data.
    
    Returns:
        pd.DataFrame with paired samples
    """
    charades_meta_dir = os.path.join(data_root, "CharadesEgo")
    csv_ego_path = os.path.join(charades_meta_dir, "CharadesEgo_v1_test_only1st.csv")
    csv_exo_path = os.path.join(charades_meta_dir, "CharadesEgo_v1_test_only3rd.csv")
    
    if not os.path.exists(csv_ego_path) or not os.path.exists(csv_exo_path):
        raise FileNotFoundError(f"Annotations not found at {csv_ego_path} or {csv_exo_path}")
    
    # Load CSVs
    df_ego = pd.read_csv(csv_ego_path)
    df_exo = pd.read_csv(csv_exo_path)
    
    df_ego["base_id"] = df_ego["id"].apply(lambda vid: vid[:-3] if vid.endswith("EGO") else vid)
    df_exo["base_id"] = df_exo["id"]
    
    # Merge pairs
    pairs = pd.merge(df_ego, df_exo, on="base_id", suffixes=("_ego", "_exo"), how="inner")
    
    # Sample
    replace = sample_size > len(pairs)
    actual_sample_size = min(sample_size, len(pairs)) if not replace else sample_size
    pairs = pairs.sample(n=actual_sample_size, replace=replace, random_state=random_seed)
    
    return pairs

def align_and_extract_segment(row, classes):
    """
    Align ego and exo action segments and extract the best-aligned pair.
    
    Returns:
        tuple: (selected_ego_seg, selected_exo_seg) or (None, None) if no alignment found
    """
    ego_segments = parse_action_segments(row.get("actions_ego"), classes)
    exo_segments = parse_action_segments(row.get("actions_exo"), classes)
    
    if not ego_segments or not exo_segments:
        return None, None
    
    # Align segments
    exo_by_cls = {}
    for s in exo_segments:
        exo_by_cls.setdefault(s["cls_id"], []).append(s)
    
    candidates = []
    for s_ego in ego_segments:
        mid_ego = (float(s_ego["start"]) + float(s_ego["end"])) / 2.0
        for s_exo in exo_by_cls.get(s_ego["cls_id"], []):
            mid_exo = (float(s_exo["start"]) + float(s_exo["end"])) / 2.0
            diff = abs(mid_ego - mid_exo)
            if diff <= 1.0:
                candidates.append((diff, s_ego, s_exo))
    
    if not candidates:
        return None, None
    
    # Choose best-aligned pair
    _, selected_ego_seg, selected_exo_seg = min(candidates, key=lambda x: x[0])
    return selected_ego_seg, selected_exo_seg

# -----------------------------------------------------------------------------
# Data loading for query data generation (used by data_generation.py)
# -----------------------------------------------------------------------------

def load_charades_ego_samples(N=10, task_type="activity", data_root=None, random_seed=42, cache_dir=None):
    """
    Load N samples from Charades-Ego dataset for query data generation.
    """
    random.seed(random_seed)
    
    if data_root is None:
        raise ValueError("data_root must be provided for Charades-Ego dataset")
    
    # Load metadata using shared function
    metadata = load_charades_metadata(data_root)
    classes = metadata['classes']
    verb_id_to_label = metadata['verb_id_to_label']
    obj_id_to_label = metadata['obj_id_to_label']
    action_to_verb = metadata['action_to_verb']
    action_to_object = metadata['action_to_object']
    
    # Set up task-specific parameters
    if task_type == "activity":
        choices = metadata['activity_ids_sorted']
        id_to_label = classes
    elif task_type == "verb":
        choices = metadata['verb_ids_sorted']
        id_to_label = verb_id_to_label
    elif task_type == "object":
        choices = metadata['obj_ids_sorted']
        id_to_label = obj_id_to_label
    else:
        raise ValueError(f"Unknown task_type: {task_type}. Use one of: activity, verb, object")
    
    # Load and sample paired data using shared function
    pairs = load_and_sample_paired_data(data_root, N, random_seed)
    
    samples = []
    for idx, row in enumerate(pairs.to_dict("records")):
        ego_video_id = row.get("id_ego")
        exo_video_id = row.get("id_exo")
        base_id = row.get("base_id")
        
        # Align segments using shared function
        selected_ego_seg, selected_exo_seg = align_and_extract_segment(row, classes)
        if selected_ego_seg is None:
            continue
        
        selected_cls_id = selected_ego_seg["cls_id"][0]
        activity_id = str(selected_cls_id).lower()
        activity_label = selected_ego_seg["label"]
        
        # Determine ground truth based on task type
        if task_type == "activity":
            ground_truth = activity_id
        elif task_type == "verb":
            if activity_id not in action_to_verb:
                continue
            verb_id = action_to_verb[activity_id]
            ground_truth = verb_id
        elif task_type == "object":
            if activity_id not in action_to_object:
                continue
            obj_id = action_to_object[activity_id]
            ground_truth = obj_id
        
        # Randomly simulate missing modality
        selected_mode = random.choice(["ego", "exo", "both"])
        
        sample = {
            "video_id_ego": ego_video_id,
            "video_id_exo": exo_video_id,
            "base_id": base_id,
            "ground_truth": ground_truth,
            "ego_start": float(selected_ego_seg["start"]),
            "ego_end": float(selected_ego_seg["end"]),
            "exo_start": float(selected_exo_seg["start"]),
            "exo_end": float(selected_exo_seg["end"]),
            "selected_mode": selected_mode,
            "choices": choices,
            "id_to_label": id_to_label,
        }
        samples.append(sample)
        
        if len(samples) >= N:
            break
    
    return samples


def process_charades_ego_jsonl_samples(task_name, task_samples, charades_ego_path, vlm_name="gemma-3-27b-it", num_frames=5):
    """
    Process Charades-Ego samples into JSONL query format with VLM descriptions.
    """
    vlm_prompt = (
        "You are given multiple frames sampled from a short time window of a video.\n"
        "Return ONLY a JSON object with the following keys (no extra text):\n"
        "{\n"
        '  "motion": string,\n'
        '  "objects": [string],\n'
        '  "summary": string\n'
        "}\n"
        "Definitions:\n"
        "- motion: describe what the person is doing (hands/arms/body) in one short phrase.\n"
        "- objects: list the main target objects the person is acting on / interacting with (not background clutter).\n"
        "- summary: a short description of the scene in one sentence."
    )
    
    # Get data_root for video extraction
    if not charades_ego_path or not os.path.exists(charades_ego_path):
        print(f"Skipping {task_name}: charades_ego_path not set or invalid")
        return []
        
    data_root = charades_ego_path
        
    video_root = os.path.join(charades_ego_path, "CharadesEgo_v1_480")
    if not os.path.exists(video_root):
        video_root = os.path.join(charades_ego_path, "CharadesEgo_v1")
    
    data_all = []
    # Process each sample
    for sample in task_samples:
        # Randomly select view mode
        selected_mode = sample['selected_mode']
        ego_desc = "Not provided"
        exo_desc = "Not provided"
        
        # Helper to extract and describe
        def get_desc_for_video(video_id):
            video_path = os.path.join(video_root, f"{video_id}.mp4")
            if os.path.exists(video_path):
                frames = extract_frames_in_time_window(
                    video_path,
                    sample.get('ego_start' if video_id == sample.get('video_id_ego') else 'exo_start'),
                    sample.get('ego_end' if video_id == sample.get('video_id_ego') else 'exo_end'),
                    num_frames
                )
                if frames:
                    return vlm_describe_images(vlm_prompt, frames, model_name=vlm_name)
            return "Not provided"

        if selected_mode in ['ego', 'both']:
            ego_desc = get_desc_for_video(sample['video_id_ego'])
        
        if selected_mode in ['exo', 'both']:
            exo_desc = get_desc_for_video(sample['video_id_exo'])
        
        # Build query text
        user_query = (
            f"Determine the correct id using the structured descriptions below.\n\n"
            f"First-person view description: {ego_desc}\n"
            f"Third-person view description: {exo_desc}"
        )
        
        # Format choices for the query (id -> label mapping)
        choices_text = "\n".join([
            f"{choice_id}: {sample['id_to_label'][choice_id]}" 
            for choice_id in sample['choices'][:20]  # Show first 20 for brevity
        ])
        
        task_type_name = task_name.split('_')[1]  # 'activity', 'object', or 'verb'
        query = (
            f"Task: Identify the {task_type_name} from video descriptions.\n\n"
            f"{user_query}\n\n"
            f"Available {task_type_name}s:\n{choices_text}\n\n"
            f"Provide your answer as the ID (e.g., c001, v003, o012)."
        )
        
        case = {
            'task_name': task_name,
            'query': query,
            'ground_truth': sample['ground_truth'],
            'metric': 'em',
            'choices': sample['choices'],
            'task_id': f"{sample['base_id']}|{sample['ego_start']:.2f}-{sample['ego_end']:.2f}|{selected_mode}"
        }
        data_all.append(case)
    return data_all

# ============================================================================
# Main Conversion Logic
# ============================================================================

def convert_charades_ego(data_root, vlm_name, sample_size, task_type, top_k, num_frames):
    print("=== CONVERTING CHARADES-EGO DATASET ===")
    
    # Map task_type to task_name for consistency
    if task_type == "activity":
        task_name = "charades_ego_activity"
    elif task_type == "verb":
        task_name = "charades_ego_verb"
    elif task_type == "object":
        task_name = "charades_ego_object"
    else:
        raise ValueError(f"Unknown task_type: {task_type}")

    # 1. Load standardized samples
    print(f"Loading {sample_size} samples for task {task_type}...")
    samples = load_charades_ego_samples(
        N=sample_size, 
        task_type=task_type, 
        data_root=data_root,
        random_seed=42 # Default seed or None
    )
    print(f"Loaded {len(samples)} samples.")

    # 2. Add VLM descriptions and build queries
    print(f"Generating descriptions using VLM: {vlm_name}...")
    queries_data = process_charades_ego_jsonl_samples(
        task_name=task_name, 
        task_samples=samples, 
        charades_ego_path=data_root, 
        vlm_name=vlm_name, 
        num_frames=num_frames
    )
    
    converted_data = []

    # Load LLM Candidates
    llm_candidates_path = "../example_data/llm_candidates/default_llm.json"
    with open(llm_candidates_path, 'r') as f:
        llm_candidates = json.load(f)

    # 3. Evaluate LLMs
    print("Evaluating LLM candidates on generated queries...")
    for idx, item in enumerate(queries_data):
        query_prompt = item['query']
        ground_truth = item['ground_truth']
        choices = item['choices']
        task_id = item['task_id']
        
        # Retrieve extra metadata from original sample if needed
        original_sample = samples[idx]
        base_id = original_sample.get('base_id') # Should be in task_id roughly
        
        # Run Real Inference for each candidate model
        for model_key, model_info in llm_candidates.items():
            request = {
                "api_endpoint": model_info["api_endpoint"],
                "query": query_prompt,
                "model_name": model_key,
                "api_name": model_info["model"],
                "service": model_info["service"]
            }
            
            # Call API
            result = call_api(request, max_tokens=512, temperature=0.01, top_p=0.9)
            boxed_content = last_boxed_only_string(result["response"])
            extracted_text = remove_boxed(boxed_content)
            pred_ids = extract_ids(extracted_text, valid_ids=choices, top_k=top_k)
            
            final_pred = ",".join(pred_ids) if pred_ids else result["response"].strip()

            # For pass@k behavior we use CEM, for single-label we use EM
            metric = "cem" if top_k > 1 else "em"
            performance = calculate_task_performance(
                prediction=final_pred,
                ground_truth=ground_truth,
                task_name=task_name,
                metric=metric
            ) or 0.0
            
            converted_data.append({
                "task_name": task_name,
                "query": query_prompt,
                "gt": ground_truth,
                "metric": metric,
                "choices": choices,
                "task_id": f"{task_id}",
                "model_name": model_key,
                "response": final_pred,
                "performance": performance, 
                "token_num": result["token_num"],
                "input_tokens": result["prompt_tokens"], 
                "output_tokens": result["completion_tokens"],
                "response_time": result["response_time"],
                "api_key_used": "",
                "user_id": None,
                "fig_id": None
            })
        
        if (idx + 1) % 10 == 0:
            print(f"Processed {idx + 1}/{len(queries_data)} samples...")
    
    # Generate embeddings for queries
    rows = []
    total_samples = len(converted_data)
    
    for rid, row in enumerate(converted_data):
        emb = get_longformer_embedding(row['query'])
        row['query_embedding'] = emb.cpu().numpy()
        rows.append(row)
            
        if (rid + 1) % 10 == 0:
            print(f"Embeddings: {rid + 1}/{total_samples}...")
            
    df_all = pd.DataFrame(rows)
    df_train, df_test, embedding_dict = process_final_data(df_all)

    return df_train, df_test

def main():
    parser = argparse.ArgumentParser(description="Convert Charades-Ego to Router JSONL")

    # Data
    parser.add_argument("--data_root", type=str, required=True, help="Root directory containing CharadesEgo/ and CharadesEgo_v1_480/ (e.g. .../data/CharadesEgo)")
    parser.add_argument("--sample_size", type=int, default=100, help="Number of video scenes to sample")

    # Task setup
    parser.add_argument(
        "--task_type",
        type=str,
        default="activity",
        choices=["activity", "verb", "object"],
        help="Classification target to build",
    )
    parser.add_argument("--top_k", type=int, default=5, help="Model outputs top-k ids; scored as pass@k when k>1")

    # VLM + frame sampling
    parser.add_argument("--vlm_name", type=str, default="gemma-3-27b-it", help="VLM model ID")
    parser.add_argument("--num_frames", type=int, default=5, help="Frames to sample per view (within the time window)")
    
    args = parser.parse_args()
    
    start_time = time.time()
    train_df, test_df = convert_charades_ego(args.data_root, args.vlm_name, args.sample_size, args.task_type, args.top_k, args.num_frames)
    
    print(f"\nConversion completed in {time.time() - start_time:.1f}s")
    print(f"Train samples: {len(train_df)}")
    print(f"Test samples: {len(test_df)}")

if __name__ == "__main__":
    main()
