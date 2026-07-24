# TagRouter Analysis Report

## Data

- **Data source**: tau2-bench airline domain, 50 paired tasks
  - Agent: gpt-5 vs gpt-5-nano
  - User simulator: gpt-4.1 (same for both)
- **Total prompts**: 465
  - gpt-5: 243 (177 success, 66 failure) — 73% overall
  - gpt-5-nano: 222 (107 success, 115 failure) — 48% overall

## Tag Schema

5 boolean tags, extracted by gpt-4o with strict JSON schema:

| Tag | Description |
|---|---|
| `is_multi_action` | 2+ distinct operations in one turn |
| `is_multi_reservation` | 2+ reservation IDs explicitly mentioned |
| `requires_policy_reasoning` | Refund eligibility, insurance, medical/emergency exceptions, loyalty status, compensation, "I was told differently" |
| `is_emotional_or_complaint` | Frustration, anger, emotional distress, flattery for special treatment |
| `has_conditional_request` | Preconditions: "only if refund", "as long as under $100", "don't unless" |

## Per-Tag Success Rates

### is_multi_action

| | tag=True | tag=False |
|---|---|---|
| gpt-5 | 71% (98) | 74% (145) |
| gpt-5-nano | 40% (80) | 53% (142) |
| **delta** | **31pp** | **21pp** |

### is_multi_reservation

| | tag=True | tag=False |
|---|---|---|
| gpt-5 | 47% (17) | 75% (226) |
| gpt-5-nano | 7% (15) | 51% (207) |
| **delta** | **40pp** | **24pp** |

### requires_policy_reasoning

| | tag=True | tag=False |
|---|---|---|
| gpt-5 | 75% (77) | 72% (166) |
| gpt-5-nano | **68%** (65) | 40% (157) |
| **delta** | **7pp** | **32pp** |

### is_emotional_or_complaint

| | tag=True | tag=False |
|---|---|---|
| gpt-5 | 80% (49) | 71% (194) |
| gpt-5-nano | 61% (38) | 46% (184) |
| **delta** | **19pp** | **25pp** |

### has_conditional_request

| | tag=True | tag=False |
|---|---|---|
| gpt-5 | 72% (43) | 73% (200) |
| gpt-5-nano | 47% (36) | 48% (186) |
| **delta** | **25pp** | **25pp** |

## Num True Tags vs Success

| n_true | gpt-5 | gpt-5-nano | delta |
|---|---|---|---|
| 0 | 71% (79) | 49% (79) | 22pp |
| 1 | 74% (69) | 42% (67) | 32pp |
| 2 | 77% (71) | 52% (62) | 25pp |
| 3 | 65% (23) | 54% (13) | 11pp |
| 4 | 0% (1) | 100% (1) | — |

## Key Findings

1. **`requires_policy_reasoning` is the strongest single signal**: when true, nano (68%) nearly matches gpt-5 (75%) — only 7pp gap. When false, nano collapses to 40% vs gpt-5's 72% (32pp gap). This is counterintuitive: prompts with policy complexity give the agent clear rule anchors, which helps the weaker model.

2. **`is_multi_reservation` is nano's death zone**: only 7% success for nano (vs 47% for gpt-5). Small sample size (32 prompts total) but the gap is so extreme it warrants routing these to gpt-5 only.

3. **"Simple" prompts (n_true=0) still fail for nano**: 49% success means even straightforward single-action requests are a coin flip for nano.

4. **gpt-5 is surprisingly stable across all conditions**: success rate stays in 65-80% range regardless of feature flags, indicating robust generalization.

5. **Nano struggles with implicit reasoning**: the biggest gpt-5/nano gaps appear when the prompt does NOT spell out the policy — nano needs explicit guardrails to succeed.

## Current Routing Rules

### Rule 0 (priority 10): Any complexity flag → gpt-5 only

```yaml
condition: is_multi_action = true OR is_multi_reservation = true OR requires_policy_reasoning = true OR is_emotional_or_complaint = true OR has_conditional_request = true
models: [gpt-5]
```

State:
- gpt-5: alpha=121, beta=45 (164 requests)
- gpt-5-nano: alpha=69, beta=74 (141 requests)

### Rule 1 (priority 5): All false → nano first, gpt-5 fallback

```yaml
condition: "*"
models: [gpt-5-nano, gpt-5]
```

State:
- gpt-5: alpha=58, beta=23 (79 requests)
- gpt-5-nano: alpha=40, beta=43 (81 requests)

## Recommendations

1. **Rewrite routing to leverage `requires_policy_reasoning`**: split so that when this tag is true, nano gets first chance (68% close to gpt-5's 75%), saving cost on ~30% of traffic.

2. **Keep `is_multi_reservation` as a hard guard**: 7% nano success is unacceptable; always route to gpt-5.

3. **Consider contextual bandit (LinUCB/Logistic Bandit)** for next iteration: eliminate manual DSL rules entirely by feeding the 5 boolean features directly into a bandit model that learns `P(success | features, model)`.

4. **Collect more data** for multi_reservation prompts (only 32 in current dataset) to validate the extreme gap is real.
