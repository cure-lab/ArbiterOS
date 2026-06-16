import os
import sys
import json
import yaml
import copy
import subprocess
import ast

import llmrouter
from llmrouter.data.data_generation import generate_query_data, save_query_data_jsonl
from llmrouter.data.generate_llm_embeddings import generate_llm_embeddings

SAMPLE_CONFIG_PATH = os.path.join(os.path.dirname(llmrouter.__file__), "data", "sample_config.yaml")
PROJECT_ROOT = os.path.dirname(os.path.dirname(llmrouter.__file__))

def get_default_paths():
    with open(SAMPLE_CONFIG_PATH, 'r') as f:
        config = yaml.safe_load(f)
    data_path = config["data_path"]
    llm_config = os.path.join(PROJECT_ROOT, data_path["llm_data"])
    output_dir = os.path.join(PROJECT_ROOT, "data", "example_data", "comfyui_generated")
    return llm_config, output_dir

DEFAULT_LLM_CONFIG, DEFAULT_OUTPUT_DIR = get_default_paths()
print(f"[LLMRouter] Default output directory set to: {DEFAULT_OUTPUT_DIR}")

# ─── Standard file names inside data_dir ─────────────────────────────────────
DATA_FILES = {
    "query_data_train": "query_train.jsonl",
    "query_data_test": "query_test.jsonl",
    "routing_data_train": "routing_train.jsonl",
    "routing_data_test": "routing_test.jsonl",
    "query_embedding_data": "query_embeddings_longformer.pt",
    "llm_data": "default_llm.json",
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _load_default_config(router_name):
    config_name_map = {"routerdc": "dcrouter"}
    fname = config_name_map.get(router_name, router_name)
    train_path = os.path.join(PROJECT_ROOT, "configs", "model_config_train", f"{fname}.yaml")
    test_path = os.path.join(PROJECT_ROOT, "configs", "model_config_test", f"{fname}.yaml")
    config_path = train_path if os.path.exists(train_path) else test_path
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def _save_runtime_config(router_name, cfg):
    config_dir = os.path.join(PROJECT_ROOT, "configs", "comfyui_generated")
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, f"{router_name}.yaml")
    with open(config_path, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False)
    return config_path


def _apply_data_dir(cfg, data_dir):
    """Populate all data_path entries from a single data directory."""
    dp = cfg.setdefault("data_path", {})
    for key, filename in DATA_FILES.items():
        dp[key] = os.path.join(data_dir, filename)


def _train_and_evaluate(router_name, config_path, device="cpu"):
    from llmrouter.cli.router_train import load_router_and_trainer, ROUTER_TRAINER_REGISTRY, UNSUPPORTED_ROUTERS

    results = {"router": router_name, "config": config_path}
    router_name_lower = router_name.lower()

    if router_name_lower in UNSUPPORTED_ROUTERS:
        results["train"] = "skipped (not trainable)"
    elif router_name_lower in ROUTER_TRAINER_REGISTRY:
        print(f"{'='*60}\nTraining {router_name}...\n{'='*60}")
        router_instance, trainer_instance = load_router_and_trainer(router_name, config_path, device)
        trainer_instance.train()
        results["train"] = "completed"
    else:
        results["train"] = "skipped (unknown)"

    print(f"\n{'='*60}\nEvaluating {router_name}...\n{'='*60}")
    from llmrouter.cli.router_inference import load_router as load_router_for_inference, ROUTER_REGISTRY
    if router_name_lower not in ROUTER_REGISTRY:
        results["evaluate"] = "skipped (not in inference registry)"
        return json.dumps(results, indent=2)

    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f)
    test_config_path = config_path
    model_path_section = cfg.get("model_path", {})
    save_path = model_path_section.get("save_model_path", "")
    if save_path and "load_model_path" not in model_path_section:
        cfg_copy = copy.deepcopy(cfg)
        cfg_copy.setdefault("model_path", {})["load_model_path"] = save_path
        test_config_path = _save_runtime_config(f"{router_name}_eval", cfg_copy)

    router_instance = load_router_for_inference(router_name, test_config_path)

    if hasattr(router_instance, "query_data_test") and router_instance.query_data_test:
        for row in router_instance.query_data_test:
            if isinstance(row, dict) and "choices" in row and isinstance(row["choices"], str):
                try:
                    row["choices"] = json.loads(row["choices"])
                except Exception:
                    row["choices"] = ast.literal_eval(row["choices"])

    batch_results = router_instance.route_batch()
    total = len(batch_results)
    successful = sum(1 for r in batch_results if r.get("success", True))
    performances = [r.get("task_performance", 0.0) for r in batch_results if "task_performance" in r]
    avg_perf = sum(performances) / len(performances) if performances else 0.0
    model_counts = {}
    for r in batch_results:
        model_counts[r.get("model_name", "unknown")] = model_counts.get(r.get("model_name", "unknown"), 0) + 1
    results["evaluate"] = {"total_queries": total, "successful": successful, "avg_performance": round(avg_perf, 4)}
    results["routing_distribution"] = model_counts
    summary = json.dumps(results, indent=2)
    print(f"\nResults for {router_name}:\n{summary}")
    return summary


def _evaluate_only(router_name, config_path):
    from llmrouter.cli.router_inference import load_router as load_router_for_inference
    print(f"{'='*60}\nEvaluating {router_name}...\n{'='*60}")
    router_instance = load_router_for_inference(router_name, config_path)

    if hasattr(router_instance, "query_data_test") and router_instance.query_data_test:
        for row in router_instance.query_data_test:
            if isinstance(row, dict) and "choices" in row and isinstance(row["choices"], str):
                try:
                    row["choices"] = json.loads(row["choices"])
                except Exception:
                    row["choices"] = ast.literal_eval(row["choices"])

    batch_results = router_instance.route_batch()
    total = len(batch_results)
    successful = sum(1 for r in batch_results if r.get("success", True))
    performances = [r.get("task_performance", 0.0) for r in batch_results if "task_performance" in r]
    avg_perf = sum(performances) / len(performances) if performances else 0.0
    model_counts = {}
    for r in batch_results:
        model_counts[r.get("model_name", "unknown")] = model_counts.get(r.get("model_name", "unknown"), 0) + 1
    results = {"router": router_name, "total_queries": total, "successful": successful,
               "avg_performance": round(avg_perf, 4), "routing_distribution": model_counts}
    summary = json.dumps(results, indent=2)
    print(f"\nResults:\n{summary}")
    return summary


# ═══════════════════════════════════════════════════════════════════════════════
# DATA PIPELINE NODES
# ═══════════════════════════════════════════════════════════════════════════════

class DatasetSelector:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "natural_qa": ("BOOLEAN", {"default": True}), "trivia_qa": ("BOOLEAN", {"default": True}),
            "mmlu": ("BOOLEAN", {"default": True}), "gpqa": ("BOOLEAN", {"default": True}),
            "mbpp": ("BOOLEAN", {"default": True}), "human_eval": ("BOOLEAN", {"default": True}),
            "gsm8k": ("BOOLEAN", {"default": True}), "commonsense_qa": ("BOOLEAN", {"default": True}),
            "math": ("BOOLEAN", {"default": True}), "openbook_qa": ("BOOLEAN", {"default": True}),
            "arc_challenge": ("BOOLEAN", {"default": True}), "geometry3k": ("BOOLEAN", {"default": True}),
            "mathvista": ("BOOLEAN", {"default": True}),
            "charades_ego_activity": ("BOOLEAN", {"default": False}),
            "charades_ego_object": ("BOOLEAN", {"default": False}),
            "charades_ego_verb": ("BOOLEAN", {"default": False}),
            "charades_ego_path": ("STRING", {"default": "", "multiline": False}),
        }}
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("selected_datasets", "charades_ego_path")
    FUNCTION = "get_dataset_list"
    CATEGORY = "LLMRouter/Data"

    def get_dataset_list(self, **kwargs):
        charades_path = kwargs.pop("charades_ego_path", "")
        selected = [k for k, v in kwargs.items() if v]
        return (",".join(selected), charades_path)


class LLMSelector:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "llm_config_path": ("STRING", {"default": DEFAULT_LLM_CONFIG}),
            "qwen2.5-7b-instruct": ("BOOLEAN", {"default": True}),
            "llama-3.1-8b-instruct": ("BOOLEAN", {"default": True}),
            "mistral-7b-instruct-v0.3": ("BOOLEAN", {"default": True}),
            "llama-3.3-nemotron-super-49b-v1": ("BOOLEAN", {"default": True}),
            "llama3-70b-instruct": ("BOOLEAN", {"default": True}),
            "mixtral-8x7b-instruct-v0.1": ("BOOLEAN", {"default": True}),
            "mixtral-8x22b-instruct-v0.1": ("BOOLEAN", {"default": True}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("llms",)
    FUNCTION = "select_llms"
    CATEGORY = "LLMRouter/Data"

    def select_llms(self, llm_config_path, **kwargs):
        with open(llm_config_path, 'r') as f:
            data = json.load(f)
        selected_models = [k for k, v in kwargs.items() if v]
        filtered = {k: v for k, v in data.items() if k in selected_models}
        output_path = os.path.join(os.path.dirname(llm_config_path), "comfyui_llm.json")
        with open(output_path, 'w') as f:
            json.dump(filtered, f, indent=2)
        return (output_path,)


class GenerateData:
    """Unified data generation node: generates query data, runs API evaluation
    pipeline (routing data), and generates LLM embeddings. Outputs a single
    data_dir containing all generated files."""
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
            "selected_datasets": ("STRING", {"forceInput": True}),
            "charades_ego_path": ("STRING", {"forceInput": False}),
            "llms": ("STRING", {"forceInput": True}),
            "sample_size": ("INT", {"default": 10, "min": 1, "max": 10000}),
            "workers": ("INT", {"default": 10, "min": 1, "max": 128}),
            "output_dir": ("STRING", {"default": DEFAULT_OUTPUT_DIR}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("data_dir",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Data"
    OUTPUT_NODE = True

    def run(self, selected_datasets, charades_ego_path, llms, sample_size, workers, output_dir):
        os.makedirs(output_dir, exist_ok=True)

        # --- Check if generation is needed (Caching) ---
        try:
            with open(llms, 'r') as f:
                llms_content = json.load(f)
        except Exception:
            llms_content = {}  # Trigger regeneration if LLM file is invalid

        current_config = {
            "selected_datasets": selected_datasets,
            "charades_ego_path": charades_ego_path,
            "llms_content": llms_content,
            "sample_size": sample_size,
        }

        metadata_path = os.path.join(output_dir, "generation_metadata.json")
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, 'r') as f:
                    saved_config = json.load(f)
                
                if saved_config == current_config:
                    required_files = [
                        DATA_FILES.get("query_data_train"),
                        DATA_FILES.get("query_data_test"),
                        DATA_FILES.get("routing_data_train"),
                        DATA_FILES.get("routing_data_test"),
                        DATA_FILES.get("query_embedding_data"),
                        DATA_FILES.get("llm_data")
                    ]
                    if all(os.path.exists(os.path.join(output_dir, str(fname))) for fname in required_files if fname):
                        print(f"[LLMRouter] Configuration unchanged and data exists. Skipping regeneration in {output_dir}")
                        return (output_dir,)
            except Exception as e:
                print(f"[LLMRouter] Warning: Metadata check failed ({e}), regenerating data...")

        # --- Step 1: Generate query data ---
        dataset_list = selected_datasets.split(',')
        if any("charades_ego" in d for d in dataset_list) and not charades_ego_path:
            raise ValueError("Charades-Ego datasets are selected but path is missing.")

        query_train_path = os.path.join(output_dir, DATA_FILES["query_data_train"])
        query_test_path = os.path.join(output_dir, DATA_FILES["query_data_test"])

        print(f"{'='*60}\nStep 1: Generating query data...\n{'='*60}")
        train_data, test_data = generate_query_data(
            sample_size=sample_size, datasets=dataset_list, charades_ego_path=charades_ego_path)
        save_query_data_jsonl(train_data, query_train_path)
        save_query_data_jsonl(test_data, query_test_path)
        print(f"Query data saved to {output_dir}")

        # --- Step 2: Copy LLM config into data_dir for self-contained output ---
        llm_dest = os.path.join(output_dir, DATA_FILES["llm_data"])
        with open(llms, 'r') as f:
            llm_data = json.load(f)
        with open(llm_dest, 'w') as f:
            json.dump(llm_data, f, indent=2)

        # --- Step 3: Run routing data pipeline (API calling + evaluation) ---
        print(f"\n{'='*60}\nStep 2: Generating routing data (API evaluation)...\n{'='*60}")
        config = {"data_path": {
            "query_data_train": query_train_path, "query_data_test": query_test_path,
            "llm_data": llm_dest,
            "query_embedding_data": os.path.join(output_dir, DATA_FILES["query_embedding_data"]),
            "routing_data_train": os.path.join(output_dir, DATA_FILES["routing_data_train"]),
            "routing_data_test": os.path.join(output_dir, DATA_FILES["routing_data_test"]),
        }}
        config_path = os.path.join(output_dir, "run_config.yaml")
        with open(config_path, 'w') as f:
            yaml.dump(config, f)

        script_path = os.path.join(os.path.dirname(llmrouter.__file__), "data", "api_calling_evaluation.py")
        cmd = [sys.executable, script_path, "--config", config_path, "--workers", str(workers)]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
        for line in process.stdout:
            print(line, end='')
        process.wait()

        # --- Step 4: Generate LLM embeddings ---
        print(f"\n{'='*60}\nStep 3: Generating LLM embeddings...\n{'='*60}")
        emb_output = os.path.join(output_dir, "llm_embeddings.json")
        generate_llm_embeddings(llm_data, emb_output)
        print(f"LLM embeddings saved to {emb_output}")

        # --- Step 5: Save metadata ---
        with open(metadata_path, 'w') as f:
            json.dump(current_config, f, indent=2)

        print(f"\n{'='*60}\nAll data generated in: {output_dir}\n{'='*60}")
        return (output_dir,)


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLE-ROUND ROUTERS
# ═══════════════════════════════════════════════════════════════════════════════

class SmallestLLMNode:
    @classmethod
    def INPUT_TYPES(s):
        w = _load_default_config("smallest_llm").get("metric", {}).get("weights", {})
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "metric_performance": ("FLOAT", {"default": w.get("performance", 1.0), "min": 0.0, "max": 1.0, "step": 0.1}),
            "metric_cost": ("FLOAT", {"default": w.get("cost", 0.0), "min": 0.0, "max": 1.0, "step": 0.1}),
            "metric_llm_judge": ("FLOAT", {"default": w.get("llm_judge", 0.0), "min": 0.0, "max": 1.0, "step": 0.1}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Single-Round"
    OUTPUT_NODE = True

    def run(self, data_dir, metric_performance, metric_cost, metric_llm_judge):
        cfg = _load_default_config("smallest_llm")
        _apply_data_dir(cfg, data_dir)
        cfg["metric"]["weights"] = {"performance": metric_performance, "cost": metric_cost, "llm_judge": metric_llm_judge}
        config_path = _save_runtime_config("smallest_llm", cfg)
        return (_evaluate_only("smallest_llm", config_path),)


class LargestLLMNode:
    @classmethod
    def INPUT_TYPES(s):
        w = _load_default_config("largest_llm").get("metric", {}).get("weights", {})
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "metric_performance": ("FLOAT", {"default": w.get("performance", 1.0), "min": 0.0, "max": 1.0, "step": 0.1}),
            "metric_cost": ("FLOAT", {"default": w.get("cost", 0.0), "min": 0.0, "max": 1.0, "step": 0.1}),
            "metric_llm_judge": ("FLOAT", {"default": w.get("llm_judge", 0.0), "min": 0.0, "max": 1.0, "step": 0.1}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Single-Round"
    OUTPUT_NODE = True

    def run(self, data_dir, metric_performance, metric_cost, metric_llm_judge):
        cfg = _load_default_config("largest_llm")
        _apply_data_dir(cfg, data_dir)
        cfg["metric"]["weights"] = {"performance": metric_performance, "cost": metric_cost, "llm_judge": metric_llm_judge}
        config_path = _save_runtime_config("largest_llm", cfg)
        return (_evaluate_only("largest_llm", config_path),)


class KNNRouterNode:
    @classmethod
    def INPUT_TYPES(s):
        hp = _load_default_config("knnrouter").get("hparam", {})
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "n_neighbors": ("INT", {"default": hp.get("n_neighbors", 5), "min": 1, "max": 100}),
            "weights": (["uniform", "distance"], {"default": hp.get("weights", "uniform")}),
            "algorithm": (["auto", "ball_tree", "kd_tree", "brute"], {"default": hp.get("algorithm", "auto")}),
            "leaf_size": ("INT", {"default": hp.get("leaf_size", 30), "min": 1, "max": 200}),
            "p": ("INT", {"default": hp.get("p", 2), "min": 1, "max": 5}),
            "knn_metric": (["minkowski", "cosine", "euclidean", "manhattan"], {"default": hp.get("metric", "minkowski")}),
            "n_jobs": ("INT", {"default": hp.get("n_jobs", -1), "min": -1, "max": 64}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Single-Round"
    OUTPUT_NODE = True

    def run(self, data_dir, n_neighbors, weights, algorithm, leaf_size, p, knn_metric, n_jobs):
        cfg = _load_default_config("knnrouter")
        _apply_data_dir(cfg, data_dir)
        cfg["hparam"] = {"n_neighbors": n_neighbors, "weights": weights, "algorithm": algorithm,
                         "leaf_size": leaf_size, "p": p, "metric": knn_metric, "n_jobs": n_jobs}
        config_path = _save_runtime_config("knnrouter", cfg)
        return (_train_and_evaluate("knnrouter", config_path),)


class SVMRouterNode:
    @classmethod
    def INPUT_TYPES(s):
        hp = _load_default_config("svmrouter").get("hparam", {})
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "kernel": (["rbf", "linear", "poly", "sigmoid"], {"default": hp.get("kernel", "rbf")}),
            "C": ("FLOAT", {"default": hp.get("C", 1.0), "min": 0.001, "max": 100.0, "step": 0.1}),
            "gamma": (["scale", "auto"], {"default": hp.get("gamma", "scale")}),
            "probability": ("BOOLEAN", {"default": hp.get("probability", True)}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Single-Round"
    OUTPUT_NODE = True

    def run(self, data_dir, kernel, C, gamma, probability):
        cfg = _load_default_config("svmrouter")
        _apply_data_dir(cfg, data_dir)
        cfg["hparam"] = {"kernel": kernel, "C": C, "gamma": gamma, "probability": probability}
        config_path = _save_runtime_config("svmrouter", cfg)
        return (_train_and_evaluate("svmrouter", config_path),)


class MLPRouterNode:
    @classmethod
    def INPUT_TYPES(s):
        hp = _load_default_config("mlprouter").get("hparam", {})
        hidden = hp.get("hidden_layer_sizes", [128, 64])
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "hidden_layer_1": ("INT", {"default": hidden[0] if len(hidden) > 0 else 128, "min": 8, "max": 1024}),
            "hidden_layer_2": ("INT", {"default": hidden[1] if len(hidden) > 1 else 64, "min": 8, "max": 1024}),
            "activation": (["relu", "tanh", "logistic", "identity"], {"default": hp.get("activation", "relu")}),
            "lr": ("FLOAT", {"default": hp.get("lr", 0.001), "min": 1e-5, "max": 1.0, "step": 0.0001}),
            "epochs": ("INT", {"default": hp.get("epochs", 100), "min": 1, "max": 1000}),
            "batch_size": ("INT", {"default": hp.get("batch_size", 32), "min": 1, "max": 512}),
            "alpha": ("FLOAT", {"default": hp.get("alpha", 0.0001), "min": 0.0, "max": 1.0, "step": 0.0001}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Single-Round"
    OUTPUT_NODE = True

    def run(self, data_dir, hidden_layer_1, hidden_layer_2, activation, lr, epochs, batch_size, alpha):
        cfg = _load_default_config("mlprouter")
        _apply_data_dir(cfg, data_dir)
        cfg["hparam"] = {"hidden_layer_sizes": [hidden_layer_1, hidden_layer_2], "activation": activation,
                         "lr": lr, "epochs": epochs, "batch_size": batch_size, "alpha": alpha}
        config_path = _save_runtime_config("mlprouter", cfg)
        return (_train_and_evaluate("mlprouter", config_path),)


class MFRouterNode:
    @classmethod
    def INPUT_TYPES(s):
        hp = _load_default_config("mfrouter").get("hparam", {})
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "latent_dim": ("INT", {"default": hp.get("latent_dim", 128), "min": 8, "max": 512}),
            "text_dim": ("INT", {"default": hp.get("text_dim", 768), "min": 64, "max": 4096}),
            "lr": ("FLOAT", {"default": hp.get("lr", 0.001), "min": 1e-5, "max": 1.0, "step": 0.0001}),
            "epochs": ("INT", {"default": hp.get("epochs", 5), "min": 1, "max": 200}),
            "batch_size": ("INT", {"default": hp.get("batch_size", 64), "min": 1, "max": 512}),
            "noise_alpha": ("FLOAT", {"default": hp.get("noise_alpha", 0.0), "min": 0.0, "max": 1.0, "step": 0.01}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Single-Round"
    OUTPUT_NODE = True

    def run(self, data_dir, latent_dim, text_dim, lr, epochs, batch_size, noise_alpha):
        cfg = _load_default_config("mfrouter")
        _apply_data_dir(cfg, data_dir)
        cfg["hparam"] = {"latent_dim": latent_dim, "text_dim": text_dim, "lr": lr,
                         "epochs": epochs, "batch_size": batch_size, "noise_alpha": noise_alpha}
        config_path = _save_runtime_config("mfrouter", cfg)
        return (_train_and_evaluate("mfrouter", config_path),)


class EloRouterNode:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {"data_dir": ("STRING", {"forceInput": True})}}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Single-Round"
    OUTPUT_NODE = True

    def run(self, data_dir):
        cfg = _load_default_config("elorouter")
        _apply_data_dir(cfg, data_dir)
        config_path = _save_runtime_config("elorouter", cfg)
        return (_train_and_evaluate("elorouter", config_path),)


class DCRouterNode:
    @classmethod
    def INPUT_TYPES(s):
        default_cfg = _load_default_config("routerdc")
        hp = default_cfg.get("hparam", {})
        mp = default_cfg.get("model_path", {})
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "backbone_model": ("STRING", {"default": mp.get("backbone_model", "microsoft/mdeberta-v3-base")}),
            "hidden_state_dim": ("INT", {"default": hp.get("hidden_state_dim", 768), "min": 64, "max": 4096}),
            "similarity_function": (["cos", "inner"], {"default": hp.get("similarity_function", "cos")}),
            "batch_size": ("INT", {"default": hp.get("batch_size", 32), "min": 1, "max": 256}),
            "training_steps": ("INT", {"default": hp.get("training_steps", 500), "min": 10, "max": 10000}),
            "learning_rate": ("FLOAT", {"default": hp.get("learning_rate", 5e-5), "min": 1e-7, "max": 1e-2, "step": 1e-6}),
            "top_k": ("INT", {"default": hp.get("top_k", 3), "min": 1, "max": 20}),
            "last_k": ("INT", {"default": hp.get("last_k", 3), "min": 1, "max": 20}),
            "temperature": ("FLOAT", {"default": hp.get("temperature", 1.0), "min": 0.01, "max": 10.0, "step": 0.1}),
            "device": (["cpu", "cuda"], {"default": hp.get("device", "cpu")}),
            "seed": ("INT", {"default": hp.get("seed", 1), "min": 0, "max": 9999}),
            "n_clusters": ("INT", {"default": hp.get("n_clusters", 3), "min": 1, "max": 50}),
            "source_max_token_len": ("INT", {"default": hp.get("source_max_token_len", 512), "min": 32, "max": 2048}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Single-Round"
    OUTPUT_NODE = True

    def run(self, data_dir, backbone_model, hidden_state_dim, similarity_function, batch_size,
            training_steps, learning_rate, top_k, last_k, temperature, device, seed,
            n_clusters, source_max_token_len):
        cfg = _load_default_config("routerdc")
        _apply_data_dir(cfg, data_dir)
        cfg["model_path"]["backbone_model"] = backbone_model
        cfg["hparam"].update({
            "hidden_state_dim": hidden_state_dim, "similarity_function": similarity_function,
            "batch_size": batch_size, "training_steps": training_steps, "learning_rate": learning_rate,
            "top_k": top_k, "last_k": last_k, "temperature": temperature, "device": device,
            "seed": seed, "n_clusters": n_clusters, "source_max_token_len": source_max_token_len,
            "target_max_token_len": source_max_token_len,
        })
        config_path = _save_runtime_config("routerdc", cfg)
        return (_train_and_evaluate("routerdc", config_path, device=device),)


class AutomixRouterNode:
    @classmethod
    def INPUT_TYPES(s):
        hp = _load_default_config("automix").get("hparam", {})
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "routing_method": (["POMDP", "Threshold", "SelfConsistency"], {"default": hp.get("routing_method", "POMDP")}),
            "num_bins": ("INT", {"default": hp.get("num_bins", 8), "min": 2, "max": 64}),
            "small_model_cost": ("INT", {"default": hp.get("small_model_cost", 1), "min": 1, "max": 100}),
            "large_model_cost": ("INT", {"default": hp.get("large_model_cost", 50), "min": 1, "max": 1000}),
            "verifier_cost": ("INT", {"default": hp.get("verifier_cost", 1), "min": 1, "max": 100}),
            "verbose": ("BOOLEAN", {"default": hp.get("verbose", True)}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Single-Round"
    OUTPUT_NODE = True

    def run(self, data_dir, routing_method, num_bins, small_model_cost, large_model_cost, verifier_cost, verbose):
        cfg = _load_default_config("automix")
        _apply_data_dir(cfg, data_dir)
        cfg["hparam"].update({"routing_method": routing_method, "num_bins": num_bins,
            "small_model_cost": small_model_cost, "large_model_cost": large_model_cost,
            "verifier_cost": verifier_cost, "verbose": verbose})
        config_path = _save_runtime_config("automix", cfg)
        return (_train_and_evaluate("automix", config_path),)


class HybridLLMNode:
    @classmethod
    def INPUT_TYPES(s):
        default_cfg = _load_default_config("hybrid_llm")
        hp = default_cfg.get("hparam", {})
        hidden = hp.get("hidden_layer_sizes", [128, 64])
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "router_mode": (["deterministic", "probabilistic", "transformed"],
                            {"default": default_cfg.get("router_mode", "probabilistic")}),
            "router_tau": ("FLOAT", {"default": default_cfg.get("router_tau", 0.1), "min": 0.01, "max": 10.0, "step": 0.01}),
            "router_threshold": ("FLOAT", {"default": default_cfg.get("router_threshold", 0.5), "min": 0.0, "max": 1.0, "step": 0.05}),
            "hidden_layer_1": ("INT", {"default": hidden[0] if len(hidden) > 0 else 128, "min": 8, "max": 1024}),
            "hidden_layer_2": ("INT", {"default": hidden[1] if len(hidden) > 1 else 64, "min": 8, "max": 1024}),
            "activation": (["relu", "tanh", "logistic", "identity"], {"default": hp.get("activation", "relu")}),
            "solver": (["adam", "sgd", "lbfgs"], {"default": hp.get("solver", "adam")}),
            "max_iter": ("INT", {"default": hp.get("max_iter", 300), "min": 10, "max": 5000}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Single-Round"
    OUTPUT_NODE = True

    def run(self, data_dir, router_mode, router_tau, router_threshold,
            hidden_layer_1, hidden_layer_2, activation, solver, max_iter):
        cfg = _load_default_config("hybrid_llm")
        _apply_data_dir(cfg, data_dir)
        cfg["router_mode"] = router_mode
        cfg["router_tau"] = router_tau
        cfg["router_threshold"] = router_threshold
        cfg["hparam"] = {"hidden_layer_sizes": [hidden_layer_1, hidden_layer_2],
                         "activation": activation, "solver": solver, "max_iter": max_iter}
        config_path = _save_runtime_config("hybrid_llm", cfg)
        return (_train_and_evaluate("hybrid_llm", config_path),)


class GraphRouterNode:
    @classmethod
    def INPUT_TYPES(s):
        hp = _load_default_config("graphrouter").get("hparam", {})
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "hidden_dim": ("INT", {"default": hp.get("hidden_dim", 64), "min": 8, "max": 512}),
            "learning_rate": ("FLOAT", {"default": hp.get("learning_rate", 0.001), "min": 1e-6, "max": 1.0, "step": 0.0001}),
            "weight_decay": ("FLOAT", {"default": hp.get("weight_decay", 0.0001), "min": 0.0, "max": 1.0, "step": 0.0001}),
            "train_epoch": ("INT", {"default": hp.get("train_epoch", 100), "min": 1, "max": 1000}),
            "batch_size": ("INT", {"default": hp.get("batch_size", 4), "min": 1, "max": 128}),
            "train_mask_rate": ("FLOAT", {"default": hp.get("train_mask_rate", 0.3), "min": 0.0, "max": 1.0, "step": 0.05}),
            "val_split_ratio": ("FLOAT", {"default": hp.get("val_split_ratio", 0.2), "min": 0.0, "max": 0.5, "step": 0.05}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Single-Round"
    OUTPUT_NODE = True

    def run(self, data_dir, hidden_dim, learning_rate, weight_decay, train_epoch,
            batch_size, train_mask_rate, val_split_ratio):
        cfg = _load_default_config("graphrouter")
        _apply_data_dir(cfg, data_dir)
        cfg["hparam"] = {"hidden_dim": hidden_dim, "learning_rate": learning_rate,
                         "weight_decay": weight_decay, "train_epoch": train_epoch,
                         "batch_size": batch_size, "train_mask_rate": train_mask_rate,
                         "val_split_ratio": val_split_ratio}
        config_path = _save_runtime_config("graphrouter", cfg)
        return (_train_and_evaluate("graphrouter", config_path),)


class CausalLMRouterNode:
    @classmethod
    def INPUT_TYPES(s):
        hp = _load_default_config("causallm_router").get("hparam", {})
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "base_model": ("STRING", {"default": hp.get("base_model", "meta-llama/Llama-2-7b-hf")}),
            "use_lora": ("BOOLEAN", {"default": hp.get("use_lora", True)}),
            "lora_r": ("INT", {"default": hp.get("lora_r", 16), "min": 1, "max": 128}),
            "lora_alpha": ("INT", {"default": hp.get("lora_alpha", 32), "min": 1, "max": 256}),
            "lora_dropout": ("FLOAT", {"default": hp.get("lora_dropout", 0.1), "min": 0.0, "max": 0.5, "step": 0.05}),
            "num_epochs": ("INT", {"default": hp.get("num_epochs", 3), "min": 1, "max": 100}),
            "batch_size": ("INT", {"default": hp.get("batch_size", 4), "min": 1, "max": 64}),
            "learning_rate": ("FLOAT", {"default": hp.get("learning_rate", 2e-5), "min": 1e-7, "max": 1e-2, "step": 1e-6}),
            "max_length": ("INT", {"default": hp.get("max_length", 512), "min": 64, "max": 4096}),
            "fp16": ("BOOLEAN", {"default": hp.get("fp16", True)}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Single-Round"
    OUTPUT_NODE = True

    def run(self, data_dir, base_model, use_lora, lora_r, lora_alpha, lora_dropout,
            num_epochs, batch_size, learning_rate, max_length, fp16):
        cfg = _load_default_config("causallm_router")
        _apply_data_dir(cfg, data_dir)
        cfg["hparam"].update({"base_model": base_model, "use_lora": use_lora, "lora_r": lora_r,
            "lora_alpha": lora_alpha, "lora_dropout": lora_dropout, "num_epochs": num_epochs,
            "batch_size": batch_size, "learning_rate": learning_rate, "max_length": max_length, "fp16": fp16})
        config_path = _save_runtime_config("causallm_router", cfg)
        return (_train_and_evaluate("causallm_router", config_path),)


# ═══════════════════════════════════════════════════════════════════════════════
# MULTI-ROUND ROUTERS
# ═══════════════════════════════════════════════════════════════════════════════

class RouterR1Node:
    @classmethod
    def INPUT_TYPES(s):
        hp = _load_default_config("router_r1").get("hparam", {})
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "model_id": (["ulab-ai/Router-R1-Qwen2.5-3B-Instruct",
                          "ulab-ai/Router-R1-Qwen2.5-3B-Instruct-Alpha0.9",
                          "ulab-ai/Router-R1-Llama-3.2-3B-Instruct",
                          "ulab-ai/Router-R1-Llama-3.2-3B-Instruct-Alpha0.9"],
                         {"default": hp.get("model_id", "ulab-ai/Router-R1-Qwen2.5-3B-Instruct")}),
            "api_base": ("STRING", {"default": hp.get("api_base", "")}),
            "api_key": ("STRING", {"default": hp.get("api_key", "")}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Multi-Round"
    OUTPUT_NODE = True

    def run(self, data_dir, model_id, api_base, api_key):
        cfg = _load_default_config("router_r1")
        _apply_data_dir(cfg, data_dir)
        cfg.setdefault("hparam", {}).update({"model_id": model_id, "api_base": api_base, "api_key": api_key})
        config_path = _save_runtime_config("router_r1", cfg)
        return (_evaluate_only("router_r1", config_path),)


# ═══════════════════════════════════════════════════════════════════════════════
# PERSONALIZED ROUTERS
# ═══════════════════════════════════════════════════════════════════════════════

class GMTRouterNode:
    @classmethod
    def INPUT_TYPES(s):
        default_cfg = _load_default_config("gmtrouter")
        gmt = default_cfg.get("gmt_config", {})
        train = default_cfg.get("train", {})
        ds = default_cfg.get("dataset", {})
        return {"required": {
            "training_set": ("STRING", {"forceInput": True}),
            "test_set": ("STRING", {"forceInput": True}),
            "dataset_name": (["mt_bench", "chatbot_arena", "gsm8k", "mmlu"],
                             {"default": ds.get("name", "mt_bench")}),
            "num_gnn_layers": ("INT", {"default": gmt.get("num_gnn_layers", 2), "min": 1, "max": 6}),
            "hidden_dim": ("INT", {"default": gmt.get("hidden_dim", 128), "min": 16, "max": 1024}),
            "dropout": ("FLOAT", {"default": gmt.get("dropout", 0.1), "min": 0.0, "max": 0.5, "step": 0.05}),
            "personalization": ("BOOLEAN", {"default": gmt.get("personalization", True)}),
            "epochs": ("INT", {"default": train.get("epochs", 350), "min": 1, "max": 2000}),
            "lr": ("FLOAT", {"default": train.get("lr", 5e-4), "min": 1e-6, "max": 1e-1, "step": 1e-5}),
            "prediction_count": ("INT", {"default": train.get("prediction_count", 256), "min": 1, "max": 1024}),
            "objective": (["auc", "accuracy"], {"default": train.get("objective", "auc")}),
            "seed": ("INT", {"default": train.get("seed", 136), "min": 0, "max": 9999}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Personalized"
    OUTPUT_NODE = True

    def run(self, training_set, test_set, dataset_name, num_gnn_layers, hidden_dim, dropout,
            personalization, epochs, lr, prediction_count, objective, seed):
        cfg = _load_default_config("gmtrouter")
        cfg["data_path"]["training_set"] = training_set
        cfg["data_path"]["test_set"] = test_set
        cfg["dataset"]["name"] = dataset_name
        cfg["gmt_config"].update({"num_gnn_layers": num_gnn_layers, "hidden_dim": hidden_dim,
                                   "dropout": dropout, "personalization": personalization})
        cfg["train"].update({"epochs": epochs, "lr": lr, "prediction_count": prediction_count,
                             "objective": objective, "seed": seed})
        config_path = _save_runtime_config("gmtrouter", cfg)
        return (_train_and_evaluate("gmtrouter", config_path),)


# ═══════════════════════════════════════════════════════════════════════════════
# AGENTIC ROUTERS
# ═══════════════════════════════════════════════════════════════════════════════

class KNNMultiRoundRouterNode:
    @classmethod
    def INPUT_TYPES(s):
        default_cfg = _load_default_config("knnmultiroundrouter")
        hp = default_cfg.get("hparam", {})
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "n_neighbors": ("INT", {"default": hp.get("n_neighbors", 5), "min": 1, "max": 100}),
            "weights": (["uniform", "distance"], {"default": hp.get("weights", "uniform")}),
            "algorithm": (["auto", "ball_tree", "kd_tree", "brute"], {"default": hp.get("algorithm", "auto")}),
            "leaf_size": ("INT", {"default": hp.get("leaf_size", 30), "min": 1, "max": 200}),
            "p": ("INT", {"default": hp.get("p", 2), "min": 1, "max": 5}),
            "knn_metric": (["minkowski", "cosine", "euclidean", "manhattan"], {"default": hp.get("metric", "minkowski")}),
            "base_model": ("STRING", {"default": default_cfg.get("base_model", "Qwen/Qwen2.5-3B-Instruct")}),
            "use_local_llm": ("BOOLEAN", {"default": default_cfg.get("use_local_llm", False)}),
            "api_endpoint": ("STRING", {"default": default_cfg.get("api_endpoint", "https://integrate.api.nvidia.com/v1")}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Agentic"
    OUTPUT_NODE = True

    def run(self, data_dir, n_neighbors, weights, algorithm, leaf_size, p, knn_metric,
            base_model, use_local_llm, api_endpoint):
        cfg = _load_default_config("knnmultiroundrouter")
        _apply_data_dir(cfg, data_dir)
        cfg["hparam"] = {"n_neighbors": n_neighbors, "weights": weights, "algorithm": algorithm,
                         "leaf_size": leaf_size, "p": p, "metric": knn_metric, "n_jobs": -1}
        cfg["base_model"] = base_model
        cfg["use_local_llm"] = use_local_llm
        cfg["api_endpoint"] = api_endpoint
        config_path = _save_runtime_config("knnmultiroundrouter", cfg)
        return (_train_and_evaluate("knnmultiroundrouter", config_path),)


class LLMMultiRoundRouterNode:
    @classmethod
    def INPUT_TYPES(s):
        default_cfg = _load_default_config("llmmultiroundrouter")
        return {"required": {
            "data_dir": ("STRING", {"forceInput": True}),
            "base_model": ("STRING", {"default": default_cfg.get("base_model", "meta/llama-3.1-8b-instruct")}),
            "use_local_llm": ("BOOLEAN", {"default": default_cfg.get("use_local_llm", False)}),
            "api_endpoint": ("STRING", {"default": default_cfg.get("api_endpoint", "https://integrate.api.nvidia.com/v1")}),
        }}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("results",)
    FUNCTION = "run"
    CATEGORY = "LLMRouter/Agentic"
    OUTPUT_NODE = True

    def run(self, data_dir, base_model, use_local_llm, api_endpoint):
        cfg = _load_default_config("llmmultiroundrouter")
        _apply_data_dir(cfg, data_dir)
        cfg["base_model"] = base_model
        cfg["use_local_llm"] = use_local_llm
        cfg["api_endpoint"] = api_endpoint
        config_path = _save_runtime_config("llmmultiroundrouter", cfg)
        return (_evaluate_only("llmmultiroundrouter", config_path),)


# ═══════════════════════════════════════════════════════════════════════════════
# NODE REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════════

NODE_CLASS_MAPPINGS = {
    "DatasetSelector": DatasetSelector, "LLMSelector": LLMSelector,
    "GenerateData": GenerateData,
    "SmallestLLMNode": SmallestLLMNode, "LargestLLMNode": LargestLLMNode,
    "KNNRouterNode": KNNRouterNode, "SVMRouterNode": SVMRouterNode,
    "MLPRouterNode": MLPRouterNode, "MFRouterNode": MFRouterNode,
    "EloRouterNode": EloRouterNode, "DCRouterNode": DCRouterNode,
    "AutomixRouterNode": AutomixRouterNode, "HybridLLMNode": HybridLLMNode,
    "GraphRouterNode": GraphRouterNode, "CausalLMRouterNode": CausalLMRouterNode,
    "RouterR1Node": RouterR1Node, "GMTRouterNode": GMTRouterNode,
    "KNNMultiRoundRouterNode": KNNMultiRoundRouterNode, "LLMMultiRoundRouterNode": LLMMultiRoundRouterNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DatasetSelector": "Select Datasets", "LLMSelector": "Select LLMs",
    "GenerateData": "Generate Data",
    "SmallestLLMNode": "Smallest LLM (Baseline)", "LargestLLMNode": "Largest LLM (Baseline)",
    "KNNRouterNode": "KNN Router", "SVMRouterNode": "SVM Router",
    "MLPRouterNode": "MLP Router", "MFRouterNode": "Matrix Factorization Router",
    "EloRouterNode": "Elo Router", "DCRouterNode": "RouterDC (Dual Contrastive)",
    "AutomixRouterNode": "AutoMix Router", "HybridLLMNode": "Hybrid LLM Router",
    "GraphRouterNode": "Graph Router (GNN)", "CausalLMRouterNode": "CausalLM Router",
    "RouterR1Node": "Router-R1", "GMTRouterNode": "GMT Router (Personalized)",
    "KNNMultiRoundRouterNode": "KNN Multi-Round Router (Agentic)",
    "LLMMultiRoundRouterNode": "LLM Multi-Round Router (Agentic)",
}
