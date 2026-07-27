import json
import sys


def analyze(log_path):

    total_prompt = 0
    total_cached = 0

    with open(log_path, "r") as f:
        for line in f:
            row = json.loads(line)
            if row.get("hook") != "post_call_success":
                continue
            usage = row.get("data", {}).get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            cached_tokens = usage.get("prompt_tokens_details", {}).get(
                "cached_tokens", 0
            )
            total_prompt += prompt_tokens
            total_cached += cached_tokens

    cache_hit_rate = total_cached / total_prompt * 100 if total_prompt > 0 else 0

    print(f"Total prompt_tokens:  {total_prompt:>12,}")
    print(f"Total cached_tokens:  {total_cached:>12,}")
    print(f"Cache hit rate:       {cache_hit_rate:>11.2f}%")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/cache_hit_arbiter.py <log/api_calls.jsonl>")
        sys.exit(1)
    analyze(sys.argv[1])
