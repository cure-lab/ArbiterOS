"""Tag all prompts and save to the database.

First run: tags all prompts via LLM API → saves to the database path in YAML.
Later runs: reads from local cache, only updates the database (no API calls unless --retag).

The database is a JSON array of {prompt, model, success, tags}.
TagRouter reads this database at init time and fits the Bayesian Logistic Bandit.

Usage:
    uv run python scripts/bootstrap_router_state.py         # use cache if exists
    uv run python scripts/bootstrap_router_state.py --retag  # force re-tagging
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml
from dotenv import load_dotenv

KERNEL = Path("/home/yuang/ArbiterOS/ArbiterOS-Kernel")
TAU2 = KERNEL / "tau2-bench"
load_dotenv(KERNEL / ".env")
sys.path.insert(0, str(KERNEL))

from arbiteros_kernel.llm_router.tag_router.tagger import LightweightTagger

CONFIG = KERNEL / "arbiteros_kernel/llm_router/configs/tag_router.yaml"
GPT5 = TAU2 / "data/router/gpt5.json"
NANO = TAU2 / "data/router/gpt5nano.json"


def load_all(path, model_name):
    data = json.loads(Path(path).read_text())
    prompts = []
    for text in data["success_prompts"]:
        prompts.append({"model": model_name, "success": True, "prompt": text})
    for text in data["failure_prompts"]:
        prompts.append({"model": model_name, "success": False, "prompt": text})
    return prompts


def tag_one(tagger_cfg, tag_schema, item):
    try:
        tagger = LightweightTagger(tagger_cfg, tag_schema=tag_schema)
        tags = tagger.extract_tags(item["prompt"])
        item["tags"] = tags
        return item
    except Exception:
        return None


def main():
    force_retag = "--retag" in sys.argv

    config = yaml.safe_load(CONFIG.read_text())
    tag_schema = config["tag_schema"]
    tagger_cfg = dict(config["tagger"])
    db_path_rel = config.get("database_path", "tag_router_database.json")

    # Resolve relative to yaml config
    db_path = (CONFIG.parent / db_path_rel).resolve()
    print(f"Database target: {db_path}", flush=True)

    # Stage 1: Tag prompts (cache or API)
    if db_path.exists() and not force_retag:
        print(f"Database already exists at {db_path}", flush=True)
        print(f"  Use --retag to force re-tagging", flush=True)
        tagged = json.loads(db_path.read_text())
        print(f"  Loaded {len(tagged)} tagged prompts", flush=True)
    else:
        all_items = load_all(str(GPT5), "gpt-5") + load_all(str(NANO), "gpt-5-nano")
        print(f"Tagging {len(all_items)} prompts (calling API)...", flush=True)
        tagged = []
        errors = 0
        done = 0
        with ThreadPoolExecutor(max_workers=50) as pool:
            futures = [pool.submit(tag_one, tagger_cfg, tag_schema, item) for item in all_items]
            for f in as_completed(futures):
                done += 1
                r = f.result()
                if r:
                    tagged.append(r)
                else:
                    errors += 1
                if done % 100 == 0:
                    print(f"  [{done}/{len(all_items)}]", flush=True)
        print(f"  Tagged {len(tagged)}, errors={errors}", flush=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_text(json.dumps(tagged, ensure_ascii=False))
        print(f"  Written to {db_path}", flush=True)

    # Quick summary
    for model in ["gpt-5", "gpt-5-nano"]:
        rows = [r for r in tagged if r["model"] == model]
        succ = sum(1 for r in rows if r["success"])
        print(f"  {model}: {len(rows)} records, {succ} success ({succ/len(rows)*100:.0f}%)", flush=True)


if __name__ == "__main__":
    main()
