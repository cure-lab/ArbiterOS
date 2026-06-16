# KNN Multi-Round Router

## Overview

The **KNN Multi-Round Router** extends the standard KNN router with a multi-round pipeline: it decomposes complex queries into sub-queries, routes each sub-query using KNN, executes them with the routed models, and aggregates responses into a final answer.

## Paper Reference

This router implements multi-round routing as described in:

- **[Router-R1: Teaching LLMs Multi-Round Routing and Aggregation via Reinforcement Learning](https://arxiv.org/abs/2506.09033)**
  - Zhang, H., Feng, T., & You, J. (2025). arXiv:2506.09033.
  - Proposes multi-round routing with decomposition and aggregation.

Combines **K-Nearest Neighbors** with **query decomposition**:
- **KNN**: Instance-based learning, no training required
- **Query Decomposition**: Break complex queries into simpler sub-tasks
- **Multi-Agent**: Delegate sub-queries to specialized models

## How It Works

### Architecture

```
Query → Decomposition → [Sub-Query 1, Sub-Query 2, ...] 
           ↓               ↓ (KNN Route)      ↓ (KNN Route)
      Base LLM          Model A Execute    Model B Execute
           ↓                     ↓                 ↓
       Aggregation ← [Response 1, Response 2, ...]
           ↓
    Final Answer
```

### Pipeline

1. **Decomposition**: Local LLM breaks query into 1-4 sub-queries
2. **Routing**: Each sub-query routed via KNN to best-matching model
3. **Execution**: Sub-queries executed with routed models via API
4. **Aggregation**: Base LLM combines sub-responses into final answer

## Configuration Parameters

### KNN Hyperparameters (`hparam`)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `n_neighbors` | int | `5` | Number of nearest neighbors |
| `weights` | str | `"distance"` | Weight function: `"uniform"` or `"distance"` |
| `metric` | str | `"cosine"` | Distance metric for KNN |

### Multi-Round Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_model` | str | `"Qwen/Qwen2.5-3B-Instruct"` | Base model for decomposition/aggregation |
| `use_local_llm` | bool | `false` | Use local vLLM (true) or API (false) |
| `api_endpoint` | str | - | API endpoint for sub-query execution |

## CLI Usage

The KNN Multi-Round Router can be used via the `llmrouter` command-line interface:

### Training

```bash
# Train the KNN Multi-Round router (builds KNN index)
llmrouter train --router knnmultiroundrouter --config configs/model_config_train/knnmultiroundrouter.yaml

# Train with quiet mode
llmrouter train --router knnmultiroundrouter --config configs/model_config_train/knnmultiroundrouter.yaml --quiet
```

### Inference

```bash
# Route a single query with decomposition
llmrouter infer --router knnmultiroundrouter --config configs/model_config_test/knnmultiroundrouter.yaml \
    --query "Explain climate change and its causes"

# Route queries from a file
llmrouter infer --router knnmultiroundrouter --config configs/model_config_test/knnmultiroundrouter.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router knnmultiroundrouter --config configs/model_config_test/knnmultiroundrouter.yaml \
    --query "What causes earthquakes?" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router knnmultiroundrouter --config configs/model_config_test/knnmultiroundrouter.yaml

# Launch with custom port
llmrouter chat --router knnmultiroundrouter --config configs/model_config_test/knnmultiroundrouter.yaml --port 8080

# Create a public shareable link
llmrouter chat --router knnmultiroundrouter --config configs/model_config_test/knnmultiroundrouter.yaml --share
```

---

## Usage Examples

### Inference (Chat Mode)

```python
from llmrouter.models import KNNMultiRoundRouter

router = KNNMultiRoundRouter(yaml_path="configs/model_config_test/knnmultiroundrouter.yaml")

# Simple string query returns response only
response = router.route_single("Explain climate change and its causes")
print(response)
```

### Inference (Evaluation Mode)

```python
# Dict query returns full metrics
query = {
    "query": "What causes earthquakes and how are they measured?",
    "ground_truth": "...",
    "task_name": "general"
}
result = router.route_single(query)

print(f"Response: {result['response']}")
print(f"Tokens: {result['prompt_tokens'] + result['completion_tokens']}")
print(f"Performance: {result.get('task_performance', 'N/A')}")
```

## Advantages

- ✅ **No Training**: KNN requires no training, just load data
- ✅ **Decomposition**: Handles complex multi-faceted queries
- ✅ **Specialized Routing**: Each sub-query gets optimal model
- ✅ **Flexible**: Supports both local and API-based execution

## Limitations

- ❌ **High Latency**: Multiple API calls increase response time
- ❌ **High Cost**: Decomposition + routing + aggregation tokens
- ❌ **Complexity**: More moving parts than simple routing
- ❌ **Local LLM Option**: Requires vLLM and GPU if use_local_llm=true

## When to Use

**Good For:**
- Complex queries requiring multi-step reasoning
- Diverse sub-tasks benefiting from specialized models
- Have training data for KNN routing

**Alternatives:**
- Simple queries → Standard KNN Router
- No decomposition needed → Single-round routers
- Need LLM-based decomposition → LLM Multi-Round Router

## Related Routers

- **LLM Multi-Round Router**: Uses LLM for routing instead of KNN
- **KNN Router**: Single-round KNN without decomposition
- **Router-R1**: Agentic multi-round with different approach

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
