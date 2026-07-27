#!/usr/bin/env python3
"""Analyze token cache hit rate from tau2-bench simulation results.json.

Usage:
    python3 scripts/cache_hit_tau.py <results.json>
"""

import json
import sys


def analyze(filepath):
    with open(filepath) as f:
        data = json.load(f)

    sims = data['simulations']

    total_asst = 0
    asst_with_raw = 0
    total_prompt = 0
    total_cached = 0
    total_completion = 0

    samples_shown = 0

    for sim in sims:
        for msg in sim['messages']:
            if msg['role'] != 'assistant':
                continue
            total_asst += 1
            raw = msg.get('raw_data')
            if raw is None:
                continue
            ru = raw.get('usage')
            if not isinstance(ru, dict):
                continue
            asst_with_raw += 1
            pt = ru.get('prompt_tokens', 0)
            details = ru.get('prompt_tokens_details', {})
            cached = details.get('cached_tokens', 0)
            ct = ru.get('completion_tokens', 0)
            total_prompt += pt
            total_cached += cached
            total_completion += ct

            if samples_shown < 3:
                samples_shown += 1
                print(f'Sample {samples_shown}: prompt_tokens={pt}, cached_tokens={cached}')
                print(f'  prompt_tokens_details={details}')

    print()
    print(f'File:                            {filepath}')
    print(f'Simulations:                     {len(sims)}')
    print(f'Total assistant messages:        {total_asst}')
    print(f'Assistant msgs with raw_data:    {asst_with_raw}')
    print(f'Total prompt_tokens:             {total_prompt:>12,d}')
    print(f'Total completion_tokens:         {total_completion:>12,d}')
    print(f'Total cached_tokens:             {total_cached:>12,d}')
    print(f'Total non-cached tokens:         {total_prompt - total_cached:>12,d}')
    print()
    if total_prompt > 0:
        hit_rate = total_cached / total_prompt * 100
        print(f'Cache HIT rate:                  {hit_rate:.2f}%')

    # Agent cost
    total_agent_cost = sum(sim.get('agent_cost', 0) for sim in sims)
    print(f'Total agent_cost:                ${total_agent_cost:.6f}')
    print(f'Per simulation avg:              ${total_agent_cost / len(sims):.6f}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python3 scripts/analyze_cache.py <results.json>')
        sys.exit(1)
    analyze(sys.argv[1])
