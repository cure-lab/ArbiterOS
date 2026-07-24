"""Extract user prompts from results.json, grouped by task success/failure."""

import argparse
import json
from pathlib import Path


def is_real_user_msg(msg: dict) -> bool:
    """Filter out terminal signals like ###TRANSFER### and ###STOP###."""
    if msg.get("role") != "user":
        return False
    content = (msg.get("content") or "").strip()
    if content in ("###TRANSFER###", "###STOP###"):
        return False
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Extract user prompts from simulation results, grouped by task success/failure."
    )
    parser.add_argument("results_path", help="Path to results.json")
    parser.add_argument(
        "-o", "--output", default=None, help="Output JSON path (default: print to stdout)"
    )
    args = parser.parse_args()

    with open(args.results_path) as f:
        data = json.load(f)

    success_prompts = []
    failure_prompts = []

    for sim in data["simulations"]:
        reward = sim["reward_info"]["reward"]
        target = success_prompts if reward == 1 else failure_prompts
        for msg in sim["messages"]:
            if is_real_user_msg(msg):
                target.append(msg["content"])

    result = {
        "source": str(Path(args.results_path).resolve()),
        "n_simulations": len(data["simulations"]),
        "n_success": sum(1 for s in data["simulations"] if s["reward_info"]["reward"] == 1),
        "n_failure": sum(1 for s in data["simulations"] if s["reward_info"]["reward"] == 0),
        "n_success_prompts": len(success_prompts),
        "n_failure_prompts": len(failure_prompts),
        "success_prompts": success_prompts,
        "failure_prompts": failure_prompts,
    }

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Saved to {args.output}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
