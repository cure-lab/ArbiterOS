# ELO Router

## Overview

The **ELO Router** is a rating-based routing method that ranks LLMs using the Elo rating system, originally developed for chess. It converts historical performance data into pairwise comparisons and computes a global ranking. All queries are routed to the single highest-rated LLM.

## Paper Reference

This router is inspired by the **Elo Rating System** and **RouteLLM**:

- **[RouteLLM: Learning to Route LLMs with Preference Data](https://arxiv.org/abs/2406.18665)**
  - Ong, I., et al. (2024). arXiv:2406.18665. Published at ICLR 2025.
  - Implements `sw_ranking` router using weighted Elo calculation.

- **Original Elo System**:
  - Elo, A. E. (1978). "The Rating of Chessplayers, Past and Present." Arco Publishing.

- **Application to LLMs**:
  - Zheng, L., et al. (2023). "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena." NeurIPS.
  - Bradley-Terry Model: Bradley, R. A., & Terry, M. E. (1952). "Rank Analysis of Incomplete Block Designs." Biometrika.

## How It Works

### Architecture

```
Historical Data → Pairwise Battles → Elo Computation → Single Best Model Selection
                  (winner/loser)     (Logistic Regression MLE)
```

### Routing Mechanism

1. **Training Phase**:
   - For each query in training data, identify the best-performing LLM
   - Create pairwise "battles": winner (best LLM) vs. each loser (other LLMs)
   - Use logistic regression to estimate Elo scores via Maximum Likelihood Estimation
   - Save Elo scores to disk

2. **Inference Phase**:
   - Load precomputed Elo scores
   - **Always select the LLM with the highest Elo rating**
   - Route ALL queries to this single model (query-independent routing)

### Key Characteristics

- **Global Ranking**: Computes a single global rating for each LLM
- **Query-Independent**: Unlike KNN/MLP/SVM, ignores query content during inference
- **Pairwise Comparisons**: Based on relative performance, not absolute scores
- **Statistical Foundation**: Grounded in Bradley-Terry model and MLE

### Elo Computation Formula

For each pairwise battle (model A vs. model B):

```
P(A wins) = 1 / (1 + 10^((Rating_B - Rating_A) / 400))
```

The trainer uses logistic regression to find Elo ratings that maximize the likelihood of observed battle outcomes.

## Training Process

### 1. Build Battle Data

For each query:
- Identify the best-performing model (winner)
- Create battles: winner vs. all other models (losers)
- Generate symmetric battles (A vs B and B vs A) for balanced training

Example:
```
Query: "Explain gravity"
Performance: GPT-4 (0.95), Claude (0.85), Llama (0.70)

Battles created:
  GPT-4 vs Claude → GPT-4 wins
  GPT-4 vs Llama  → GPT-4 wins
  Claude vs GPT-4 → Claude loses
  Llama vs GPT-4  → Llama loses
```

### 2. Estimate Elo Scores

Uses logistic regression MLE to find Elo ratings that best explain battle outcomes:
- Initialize all models at 1000 rating
- Fit logistic regression to predict battle winners
- Convert coefficients to Elo scores (scaled by 400)

### 3. Save Rankings

Saves Elo scores as a dictionary: `{"GPT-4": 1250, "Claude": 1180, "Llama": 950}`

## Configuration Parameters

### Training Parameters

No hyperparameters to tune! The Elo computation is deterministic given the training data.

**Fixed Constants** (in trainer code):
- `SCALE`: 400.0 - Standard Elo scale factor
- `BASE`: 10.0 - Elo probability base
- `INIT_RATING`: 1000.0 - Starting rating for all models

### Data Paths

| Parameter | Description |
|-----------|-------------|
| `query_data_train` | Training queries in JSONL format |
| `routing_data_train` | Historical routing performance data (query-LLM pairs with performance scores) |
| `llm_data` | LLM candidate information (models, API names, metadata) |

### Model Paths

| Parameter | Purpose | Usage |
|-----------|---------|-------|
| `save_model_path` | Where to save computed Elo scores | Training: saves `{model_name: elo_score}` dictionary |
| `load_model_path` | Elo scores to load for inference | Testing: path to saved `.pkl` file |

### Inference Parameters

During inference:
- Loads Elo scores from `load_model_path`
- Selects the model with the highest rating
- Routes **all queries** to this single model
- No query-specific routing decisions

## CLI Usage

The ELO Router can be used via the `llmrouter` command-line interface:

### Training

```bash
# Compute Elo rankings
llmrouter train --router elorouter --config configs/model_config_train/elorouter.yaml

# Train with quiet mode
llmrouter train --router elorouter --config configs/model_config_train/elorouter.yaml --quiet
```

### Inference

```bash
# Route a single query (always selects highest-rated model)
llmrouter infer --router elorouter --config configs/model_config_test/elorouter.yaml \
    --query "What is the meaning of life?"

# Route queries from a file
llmrouter infer --router elorouter --config configs/model_config_test/elorouter.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router elorouter --config configs/model_config_test/elorouter.yaml \
    --query "Explain quantum mechanics" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router elorouter --config configs/model_config_test/elorouter.yaml

# Launch with custom port
llmrouter chat --router elorouter --config configs/model_config_test/elorouter.yaml --port 8080

# Create a public shareable link
llmrouter chat --router elorouter --config configs/model_config_test/elorouter.yaml --share
```

---

## Usage Examples

### Training the ELO Router

```python
from llmrouter.models import EloRouter, EloRouterTrainer

# Initialize router with training configuration
router = EloRouter(yaml_path="configs/model_config_train/elorouter.yaml")

# Create trainer
trainer = EloRouterTrainer(router=router, device="cpu")

# Compute Elo scores
trainer.train()
# Elo scores will be saved to the path specified in save_model_path

# View the computed rankings
print("Elo Rankings:")
for model, score in sorted(router.elo_scores.items(), key=lambda x: -x[1]):
    print(f"  {model}: {score:.2f}")
```

**Command Line Training:**
```bash
python tests/train_test/test_elorouter.py --yaml_path configs/model_config_train/elorouter.yaml
```

### Inference: Routing Queries

```python
from llmrouter.models import EloRouter

# Initialize router with test configuration (loads Elo scores)
router = EloRouter(yaml_path="configs/model_config_test/elorouter.yaml")

# Route a single query
query = {"query": "What is the meaning of life?"}
result = router.route_single(query)

print(f"Selected Model: {result['model_name']}")
# Note: This will ALWAYS be the same model (highest Elo rating)
```

### Batch Routing with API Execution

```python
from llmrouter.models import EloRouter

# Initialize router
router = EloRouter(yaml_path="configs/model_config_test/elorouter.yaml")

# Prepare batch of queries
queries = [
    {"query": "Explain quantum mechanics", "ground_truth": "..."},
    {"query": "Write a poem about AI", "ground_truth": "..."},
    {"query": "Solve x^2 + 5x + 6 = 0", "ground_truth": "..."}
]

# Route and execute (all queries go to the same best model)
results = router.route_batch(batch=queries, task_name="general")

# All queries routed to the same model
unique_models = set(r['model_name'] for r in results)
print(f"Number of unique models used: {len(unique_models)}")  # Always 1
```

## YAML Configuration Example

**Training Configuration** (`configs/model_config_train/elorouter.yaml`):

```yaml
data_path:
  query_data_train: 'data/example_data/query_data/default_query_train.jsonl'
  routing_data_train: 'data/example_data/routing_data/default_routing_train_data.jsonl'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  ini_model_path: ''
  save_model_path: 'saved_models/elorouter/elorouter.pkl'

metric:
  weights:
    performance: 1    # Primary criterion for determining winners
    cost: 0
    llm_judge: 0
```

**Testing Configuration** (`configs/model_config_test/elorouter.yaml`):

```yaml
data_path:
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  load_model_path: 'saved_models/elorouter/elorouter.pkl'
```

## Advantages

- ✅ **Simple and Interpretable**: Single global ranking that's easy to understand
- ✅ **Statistically Grounded**: Based on Bradley-Terry model and MLE
- ✅ **No Hyperparameters**: No tuning required, fully deterministic
- ✅ **Handles Imbalanced Comparisons**: Elo naturally handles varying numbers of battles per model
- ✅ **Battle-Tested**: Proven system used in chess, sports, and now LLM leaderboards
- ✅ **Fast Inference**: Just a dictionary lookup (O(1))

## Limitations

- ❌ **Query-Agnostic**: Ignores query content, always routes to the same model
- ❌ **No Specialization**: Cannot leverage model strengths for specific query types
- ❌ **Single Model**: Cannot distribute load or use ensembles
- ❌ **Assumes Transitivity**: Assumes if A > B and B > C, then A > C (may not hold for LLMs)
- ❌ **Static Rankings**: Must retrain to update Elo scores
- ❌ **No Cost-Performance Trade-off**: Always chooses highest-rated model regardless of cost
- ❌ **Data Hungry**: Needs sufficient pairwise comparisons for accurate rankings

## When to Use ELO Router

**Good Use Cases:**
- Want a simple baseline that always uses the "best" model
- Need a global ranking of LLM capabilities
- Have abundant training data with consistent evaluation metrics
- Don't need query-specific routing (all queries are similar)
- Want interpretable, explainable routing (just show the Elo ranking)

**NOT Recommended When:**
- Queries have diverse types (coding, math, creative writing, etc.)
- Need to optimize cost (Elo always picks highest-rated, often most expensive model)
- Want to leverage specialized model strengths
- Need to distribute load across multiple models
- Have limited training data

## Understanding Elo Scores

### Interpretation

```
Elo Score    Meaning
---------    -------
1400+        Dominant model, wins most battles
1200-1400    Strong model, competitive
1000-1200    Average model, mixed performance
800-1000     Weak model, loses most battles
<800         Very weak model, rarely wins
```

### Elo Difference and Win Probability

```
Elo Diff     Expected Win Rate
--------     -----------------
0            50%
100          64%
200          76%
400          91%
```

If Model A has Elo 1200 and Model B has Elo 1000 (diff = 200), Model A is expected to win ~76% of battles.

## Comparison with Other Routers

| Aspect | ELO Router | KNN Router | MLP/SVM Router |
|--------|------------|------------|----------------|
| Query-Specific | ❌ No | ✅ Yes | ✅ Yes |
| Training Speed | Fast | None | Medium |
| Inference Speed | Instant | Medium | Fast |
| Interpretability | High (rankings) | High (neighbors) | Low |
| Model Diversity | Single model | Multiple models | Multiple models |
| Hyperparameters | None | Few | Many |
| Data Efficiency | Medium | High | Medium |

## Implementation Details

- **Framework**: Custom implementation using scikit-learn's LogisticRegression
- **Battle Generation**: Symmetric battles (A vs B and B vs A) for balanced training
- **MLE Solver**: LBFGS optimizer for logistic regression
- **Output**: Dictionary mapping model names to Elo scores
- **Serialization**: Saved as `.pkl` files using pickle

## Tips for Best Performance

1. **Training Data Quality**:
   - Ensure performance metrics are reliable and consistent
   - Include diverse queries to avoid bias
   - Need sufficient queries (50+ recommended) for stable rankings

2. **Performance Metric Selection**:
   - Use `metric.weights.performance = 1` for accuracy-based ranking
   - Can incorporate cost if needed (but defeats purpose of pure ranking)
   - Ensure metric is comparable across different query types

3. **Model Pool**:
   - Works best with 3-10 models
   - Too few models → limited routing value
   - Too many models → sparse battle data per pair

4. **Retraining Strategy**:
   - Retrain periodically as new data arrives
   - Monitor if model capabilities change over time
   - Update when adding new models to the pool

5. **Use as Baseline**:
   - ELO Router is excellent as a baseline for comparison
   - Compare query-specific routers against ELO to measure value of personalization

## Relation to Chatbot Arena

This router is directly inspired by **Chatbot Arena** (LMSYS):
- Chatbot Arena uses Elo ratings to rank LLMs based on human preferences
- Users vote on pairwise comparisons → Elo scores computed
- Creates public LLM leaderboard

**Key Difference**:
- Chatbot Arena: Human preference battles
- ELO Router: Automated performance metric battles

## Advanced Usage

### Custom Elo Parameters

While the defaults work well, you can modify the Elo constants in `trainer.py`:

```python
# Larger SCALE → bigger rating differences
elo_scores = compute_elo_mle(battles_df, SCALE=500.0, BASE=10.0, INIT_RATING=1500.0)
```

### Incorporating Costs

You can modify the battle generation to consider cost-adjusted performance:

```python
# In custom trainer
df["adjusted_performance"] = df["performance"] / (df["cost"] ** 0.5)
# Then use adjusted_performance to determine winners
```

### Multi-Metric Elo

Compute separate Elo rankings for different metrics (accuracy, speed, cost-efficiency) and combine them.

## Related Routers

- **Largest LLM Router**: Always picks the largest model (simpler heuristic)
- **Smallest LLM Router**: Always picks the smallest model (cost-focused)
- **Hybrid LLM Router**: Weighted combination of multiple routing strategies
- **Matrix Factorization Router**: Learns query-model affinity (query-specific alternative)

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
