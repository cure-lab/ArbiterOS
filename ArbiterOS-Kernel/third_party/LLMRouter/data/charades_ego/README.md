# Charades-Ego Dataset Preparation

<div align="center">
  <img src="assets/charades-ego-demo.jpg" alt="Charades-Ego Example" width="800">
</div>

**Charades-Ego** is a unique large-scale dataset featuring **paired first-person (egocentric) and third-person videos**, enabling rich multimodal understanding and diverse action recognition tasks.

This directory provides the complete pipeline to convert Charades-Ego into the **LLMRouter** training format, effectively bridging complex video understanding with intelligent model routing.

## 1. Data Download

You can find download links and dataset details on the [Charades-Ego website](https://prior.allenai.org/projects/charades-ego).

**Archives:**
- **Annotations:** `https://ai2-public-datasets.s3-us-west-2.amazonaws.com/charades/CharadesEgo.zip`
- **Videos (480p):** `https://ai2-public-datasets.s3-us-west-2.amazonaws.com/charades/CharadesEgo_v1_480.tar`
- **Videos (original size):** `https://ai2-public-datasets.s3-us-west-2.amazonaws.com/charades/CharadesEgo_v1.tar`

## 2. Setup Structure

Extract the data so that your directory looks like this:

```
/path/to/data/
└── CharadesEgo/
    ├── CharadesEgo/                  # annotations + label spaces
    │   ├── CharadesEgo_v1_test_only1st.csv
    │   ├── CharadesEgo_v1_test_only3rd.csv
    │   ├── Charades_v1_classes.txt
    │   ├── Charades_v1_mapping.txt
    │   ├── Charades_v1_verbclasses.txt
    │   └── Charades_v1_objectclasses.txt
    ├── CharadesEgo_v1_480/            # videos (preferred)
    │   ├── <video_id>.mp4
    │   └── ...
    └── CharadesEgo_v1/                # videos (fallback if 480p not present)
        ├── <video_id>.mp4
        └── ...
```

## 3. Generate Training Data

Run the conversion script to generate `default_routing_train_data.jsonl`, `default_routing_test_data.jsonl`, and `query_embeddings.pt`.

This script will:
1. Load the dataset annotations.
2. Sample frames from a short time window and use a VLM API to describe them (first-person / third-person).
3. Construct a classification prompt and evaluate multiple candidate LLMs (for routing data).
4. Save the output in LLMRouter format.

### Task setup

You can build three task variants via `--task_type`:
- `activity`: predict Charades action id `c###`
- `verb`: predict verb id `v###`
- `object`: predict object id `o###`

```bash
python data/charades_ego/charades_ego_to_json.py \
  --data_root /path/to/data \
  --sample_size 100 \
  --task_type activity \
  --top_k 5 \
  --vlm_name gemma-3-27b-it \
  --num_frames 5
```
