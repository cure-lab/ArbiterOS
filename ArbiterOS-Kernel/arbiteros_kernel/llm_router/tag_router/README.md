# TagRouter: Adaptive LLM Routing Engine

## Overview

TagRouter routes each user query to either **gpt-5** (strong, expensive) or **gpt-5-nano** (cheap, weaker) based on semantic features extracted from the prompt. It uses a **Bayesian Logistic Bandit** -- per-model logistic regression with Laplace-approximated posterior, selecting via Thompson Sampling.

The key insight: not all prompts need gpt-5. Simple, straightforward requests can be handled by gpt-5-nano at a fraction of the cost. TagRouter learns *which* prompt characteristics predict model success from historical data.

## Architecture

```
User Prompt
    │
    ▼
┌──────────────┐
│  Lightweight  │  gpt-4o extracts 5 boolean semantic tags
│   Tagger      │
└──────────────┘
    │
    │ tags: {is_multi_action: false, is_emotional: true, ...}
    ▼
┌──────────────┐
│  Bayesian     │  Per-model logistic regression
│  Logistic     │  w ~ N(mu, Sigma)  →  p = σ(w·[x, 1])
│  Bandit       │
└──────────────┘
    │
    │ score = p_success - cost_weight × (cost / max_cost)
    ▼
  gpt-5  or  gpt-5-nano
```

## Mathematical Model

### 1. Semantic Tagging

The tagger extracts 5 boolean features from the user prompt:

| Tag | Meaning |
|-----|---------|
| `is_multi_action` | 2+ distinct operations in one turn |
| `is_multi_reservation` | 2+ reservation IDs mentioned |
| `requires_policy_reasoning` | Refund eligibility, insurance, medical exceptions, loyalty rules, compensation, disputes |
| `is_emotional_or_complaint` | Frustration, anger, emotional distress, flattery, escalation demands |
| `has_conditional_request` | Preconditions like "only if I get a refund" |

Each prompt produces a feature vector **x** ∈ {0,1}⁵.

### 2. Per-Model Logistic Regression

For each model *m* (gpt-5, gpt-5-nano), we model success probability as:

$$P(\text{success} \mid \mathbf{x}, m) = \sigma(\mathbf{w}_m \cdot [\mathbf{x}, 1])$$

where $\sigma(z) = 1/(1 + e^{-z})$ is the sigmoid function, and $\mathbf{w}_m \in \mathbb{R}^6$ (5 feature weights + 1 intercept).

### 3. Prior and Posterior (Laplace Approximation)

**Prior**: Each model's weights have independent Gaussian priors:

$$\mathbf{w}_m \sim \mathcal{N}(\mathbf{0}, \sigma_0^2 \mathbf{I})$$

where $\sigma_0^2$ is `prior_var` (default 1.0). This is a weakly informative prior centered at zero -- models start with no assumed relationship between features and success.

**Likelihood**: For *n* labeled examples $\{(\mathbf{x}_i, y_i)\}_{i=1}^n$ where $y_i \in \{0,1\}$:

$$p(\mathbf{y} \mid \mathbf{w}, \mathbf{X}) = \prod_{i=1}^n \sigma(\mathbf{w} \cdot [\mathbf{x}_i, 1])^{y_i} \cdot (1 - \sigma(\mathbf{w} \cdot [\mathbf{x}_i, 1]))^{1 - y_i}$$

**Posterior**: The posterior is not analytically tractable (non-conjugate). We use **Laplace approximation** to fit a Gaussian:

$$\mathbf{w}_m \mid \text{data} \approx \mathcal{N}(\boldsymbol{\mu}_m, \boldsymbol{\Sigma}_m)$$

**Laplace Approximation Procedure** (per model):

1. **MAP estimate**: Find $\mathbf{w}_{\text{MAP}}$ by minimizing the negative log posterior with L-BFGS:

   $$\mathbf{w}_{\text{MAP}} = \arg\min_{\mathbf{w}} \left[ \sum_i \log(1 + e^{\mathbf{w}\cdot[\mathbf{x}_i,1]}) - y_i(\mathbf{w}\cdot[\mathbf{x}_i,1]) + \frac{1}{2\sigma_0^2}\|\mathbf{w}\|^2 \right]$$

2. **Hessian at MAP**: The observed Fisher information:

   $$\mathbf{H} = \mathbf{X}^T \mathbf{D} \mathbf{X} + \frac{1}{\sigma_0^2}\mathbf{I}$$

   where $\mathbf{D} = \text{diag}(p_i(1 - p_i))$ and $p_i = \sigma(\mathbf{w}_{\text{MAP}} \cdot [\mathbf{x}_i, 1])$.

3. **Covariance**: $\boldsymbol{\Sigma} = \mathbf{H}^{-1}$ (with numerical stabilization for near-singularity)

### 4. Thompson Sampling

For each routing decision, given feature vector **x**:

1. **Sample weights** from each model's posterior:

   $$\tilde{\mathbf{w}}_m \sim \mathcal{N}(\boldsymbol{\mu}_m, \boldsymbol{\Sigma}_m)$$

   If no data exists for a model, sample from the prior: $\tilde{\mathbf{w}}_m \sim \mathcal{N}(\mathbf{0}, \sigma_0^2 \mathbf{I})$

2. **Predict success**:

   $$\hat{p}_m = \sigma(\tilde{\mathbf{w}}_m \cdot [\mathbf{x}, 1])$$

3. **Cost-aware scoring**:

   $$\text{score}_m = \hat{p}_m - \lambda \cdot \frac{c_m}{\max_k c_k}$$

   where $\lambda$ is `cost_weight` (default 0.3) and $c_m$ is `model_costs[m]`.

4. **Select** $m^* = \arg\max_m \text{score}_m$

### 5. Thompson Sampling Intuition

Thompson Sampling balances **exploration and exploitation** through posterior uncertainty:

- **Large Sigma** (few data): sampled w varies widely → different models win on different draws → natural exploration
- **Small Sigma** (lots of data): sampled w close to mu → nearly deterministic → exploitation

The variance $\boldsymbol{\Sigma}_m$ encodes *how much we don't know* about the relationship between features and success for model *m*. As the database grows, uncertainty shrinks, and TS converges to the optimal routing policy.

### 6. Cost Penalty

The cost penalty $\lambda \cdot (c_m / \max_k c_k)$ is subtracted from the predicted success probability. With `cost_weight=0.3`, `gpt-5=10`, `gpt-5-nano=0.4`:

- gpt-5 penalty: $0.3 \times (10/10) = 0.30$
- gpt-5-nano penalty: $0.3 \times (0.4/10) = 0.012$

This means gpt-5-nano only needs a success probability ~0.29 higher than gpt-5 to be selected -- the cost asymmetry is built into the decision.

### 7. Weight Interpretation

Weights can be interpreted directly. A positive weight on feature *j* means that feature increases the predicted success probability. A negative weight means it decreases it.

Example (from current data):

```
gpt-5:       w = [is_multi_action, is_multi_reservation, requires_policy, is_emotional, has_conditional, intercept]
             w = [-0.01,          -1.29,               +0.04,           +0.24,       +0.07,           +1.02]
gpt-5-nano:  w = [-0.30,          -1.41,               +0.93,           +0.11,       -0.03,           -0.17]
```

- **Intercept**: gpt-5 baseline success is +1.02 (high), nano is -0.17 (low). All else equal, gpt-5 is preferred.
- **is_multi_reservation**: Both models drop sharply (~ -1.3) -- multi-reservation prompts are hard for both.
- **requires_policy_reasoning**: gpt-5-nano gets +0.93 (strong positive), gpt-5 gets +0.04 (neutral). Policy-heavy prompts are where nano is most competitive.
- **is_emotional**: Both slightly positive, gpt-5 more so (+0.24 vs +0.11).

## Usage

### 1. Bootstrap the Database

First, tag all historical prompts and build the database:

```bash
cd tau2-bench
uv run python scripts/bootstrap_router_state.py         # use existing database
uv run python scripts/bootstrap_router_state.py --retag  # force re-tagging via API
```

This reads `data/router/gpt5.json` and `data/router/gpt5nano.json` (prompts grouped by task-level success), calls the tagger LLM on each, and writes to the path specified by `database_path` in the YAML config.

### 2. Configuration

`tag_router.yaml`:
```yaml
tag_schema:
  is_multi_action: {type: bool, required: true, description: "..."}
  # ... (5 boolean tags)

candidate_models: [gpt-5, gpt-5-nano]
model_costs: {gpt-5: 10, gpt-5-nano: 0.4}

bayesian_bandit:
  prior_var: 1.0     # Prior variance -- higher = more initial exploration
  cost_weight: 0.3   # 0 = ignore cost, 1 = cost-dominated

database_path: tag_router_tau2_airline.json  # relative to this YAML file

tagger:
  model: gpt-4o
  api_base: ${OPENAI_API_BASE}
  api_key: ${OPENAI_API_KEY}
  temperature: 0.0
  max_tokens: 256
  strict_json: true
```

### 3. Using TagRouter in Code

```python
from arbiteros_kernel.llm_router.tag_router import TagRouter

# Initialize (reads database, fits bandit)
router = TagRouter.from_yaml("configs/tag_router.yaml")

# Route a query
result = router.route_single({"query": "Cancel my flight XEHM4B"})
print(result["model_name"])  # "gpt-5" or "gpt-5-nano"
print(result["tags"])        # {is_multi_action: false, ...}
print(result["scores"])      # {gpt-5: 0.3348, gpt-5-nano: 0.5225}

# Inspect learned weights
stats = router.get_stats()
for model, info in stats.items():
    print(f"{model}: w_mean={info['w_mean']}, w_std={info['w_std']}")
```

### 4. Updating the Database

When new simulation results are available:

```bash
# 1. Re-extract prompts
uv run python scripts/extract_user_prompts.py

# 2. Re-tag and rebuild database
uv run python scripts/bootstrap_router_state.py --retag
```

The TagRouter reads the database fresh on every `from_yaml()` call, so no restart is needed beyond re-instantiating the router.

## File Layout

```
arbiteros_kernel/llm_router/
├── configs/
│   ├── tag_router.yaml              # Routing config
│   ├── tag_router.schema.json       # JSON Schema for config validation
│   └── tag_router_tau2_airline.json # Tagged prompt database
└── tag_router/
    ├── __init__.py                  # Exports TagRouter
    ├── tag_router.py                # Main router class
    ├── bayesian_bandit.py           # Bayesian Logistic Bandit
    └── tagger.py                    # Lightweight LLM tagger

tau2-bench/
├── data/router/
│   ├── gpt5.json                    # Raw gpt-5 prompts by success/failure
│   └── gpt5nano.json                # Raw gpt-5-nano prompts
└── scripts/
    ├── extract_user_prompts.py      # Extract prompts from simulation results
    └── bootstrap_router_state.py    # Tag prompts → build database
```

## Tuning Guide

| Parameter | Effect | When to adjust |
|-----------|--------|---------------|
| `prior_var` | Higher = more exploration with small data | Increase if routing seems too deterministic early on |
| `cost_weight` | Higher = prefer cheaper model more | Increase to save cost, decrease if quality matters more |
| `model_costs` | Relative cost of each model | Keep proportional to actual API pricing ratios |
| `tag_schema` | Features the bandit learns from | Add/remove tags based on domain knowledge |
