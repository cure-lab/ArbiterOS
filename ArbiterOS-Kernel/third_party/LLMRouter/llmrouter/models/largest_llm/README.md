# Largest LLM Router

## Overview

The **Largest LLM Router** is a simple heuristic router that always selects the largest available LLM based on model size. It prioritizes maximum quality over cost, making it ideal for quality-critical applications.

## Paper Reference

This router is a baseline method described in:

- **[GraphRouter: A Graph-based Router for LLM Selections](https://arxiv.org/abs/2410.03834)**
  - (2024). arXiv:2410.03834.
  - Uses smallest/largest LLM as baseline comparison for routing methods.

## How It Works

### Routing Logic

1. Load all LLMs from `llm_data`
2. Filter models with size ending in 'B' (billions of parameters)
3. Parse sizes (e.g., "7B" → 7.0, "70B" → 70.0)
4. Select model with maximum size
5. Route ALL queries to this single model

### No Training Required

This is a zero-shot heuristic - no training needed.

## Configuration

Requires only `llm_data` with model sizes:

```json
{
  "Qwen2.5-3B": {"size": "3B", "model": "qwen/qwen2.5-3b-instruct"},
  "Qwen2.5-7B": {"size": "7B", "model": "qwen/qwen2.5-7b-instruct"},
  "Llama-70B": {"size": "70B", "model": "meta/llama-3.1-70b-instruct"}
}
```

Router will select "Llama-70B" (largest).

## CLI Usage

The Largest LLM Router can be used via the `llmrouter` command-line interface:

### Inference

> **Note**: This router does not require training - it's a zero-shot heuristic.

```bash
# Route a single query (always selects largest model)
llmrouter infer --router largest_llm --config configs/model_config_test/largest_llm.yaml \
    --query "What is machine learning?"

# Route queries from a file
llmrouter infer --router largest_llm --config configs/model_config_test/largest_llm.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router largest_llm --config configs/model_config_test/largest_llm.yaml \
    --query "Explain neural networks" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router largest_llm --config configs/model_config_test/largest_llm.yaml

# Launch with custom port
llmrouter chat --router largest_llm --config configs/model_config_test/largest_llm.yaml --port 8080

# Create a public shareable link
llmrouter chat --router largest_llm --config configs/model_config_test/largest_llm.yaml --share
```

---

## Usage

```python
from llmrouter.models import LargestLLM

router = LargestLLM(yaml_path="configs/model_config_test/largest_llm.yaml")

# All queries routed to largest model
queries = [
    {"query": "Simple question"},
    {"query": "Complex question requiring reasoning"}
]

results = router.route_batch(queries)
# Both use same largest model
```

## Advantages

- ✅ **Maximum Quality**: Always uses most capable model
- ✅ **Simple**: No training, no hyperparameters
- ✅ **Fast**: Instant routing decision
- ✅ **Reliable**: Best model for all tasks

## Limitations

- ❌ **Maximum Cost**: Always uses most expensive model
- ❌ **Wasteful**: Overkill for simple queries
- ❌ **No Adaptation**: Cannot improve with data
- ❌ **Single Model**: No load balancing

## When to Use

**Good For:**
- Quality is paramount, cost secondary
- All queries are complex/challenging
- Baseline for maximum performance
- Production where failures are costly

**Alternatives:**
- Need cost savings → Smallest LLM Router
- Balance cost-quality → Hybrid LLM Router  
- Query-specific → KNN/MLP/SVM Router

## Related Routers

- **Smallest LLM Router**: Opposite strategy (max cost savings)
- **Hybrid LLM Router**: Balances small and large models
- **ELO Router**: Data-driven single model selection

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
