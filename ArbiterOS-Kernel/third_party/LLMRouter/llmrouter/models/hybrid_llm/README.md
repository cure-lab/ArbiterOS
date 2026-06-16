# Hybrid LLM Router

## Overview

The **Hybrid LLM Router** intelligently balances between a small (cheap) and large (expensive) model by learning to predict when the small model's quality will be sufficient. It uses MLP regression to estimate the quality gap and makes routing decisions based on cost-quality trade-offs.

## Paper Reference

Based on the **Hybrid LLM** approach:

- **[Hybrid LLM: Cost-Efficient and Quality-Aware Query Routing](https://arxiv.org/abs/2404.14618)**
  - Ding, Y., et al. (2024). arXiv:2404.14618.
  - Proposes MLP-based quality gap prediction for cost-aware routing.

- **Key Idea**: Route to small model when quality gap is small, large model otherwise.

## How It Works

### Architecture

```
Query → Longformer Embedding → MLP Regressor → Quality Gap Score → Routing Decision
                                                        ↓
                                        (Compare to threshold)
                                                        ↓
                                    Small Model (score ≥ threshold)
                                    Large Model (score < threshold)
```

### Routing Modes

The router supports three decision strategies:

#### 1. Deterministic Mode
- Label: `y = 1` if `q(Small) ≥ q(Large)`, else `y = 0`
- Decision: Route to small if `score ≥ 0.5`

#### 2. Probabilistic Mode
- Label: `y = sigmoid((q(Small) - q(Large)) / tau)`
- Soft labels based on quality gap
- More nuanced than hard binary

#### 3. Transformed Mode
- Find optimal threshold `t*` that maximizes label separation
- Label: `y = 1` if `q(Small) ≥ q(Large) - t*`
- Automatically balanced classes

## Configuration Parameters

### Router Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `router_mode` | str | `"deterministic"` | Mode: `"deterministic"`, `"probabilistic"`, or `"transformed"` |
| `router_tau` | float | `0.1` | Temperature for probabilistic mode |
| `router_threshold` | float | `0.5` | Decision threshold |

### MLP Hyperparameters (`hparam`)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `hidden_layer_sizes` | list[int] | `[128, 64]` | MLP architecture |
| `activation` | str | `"relu"` | Activation function |
| `solver` | str | `"adam"` | Optimizer |
| `max_iter` | int | `300` | Training iterations |

## CLI Usage

The Hybrid LLM Router can be used via the `llmrouter` command-line interface:

### Training

```bash
# Train the Hybrid LLM router
llmrouter train --router hybrid_llm --config configs/model_config_train/hybrid_llm.yaml

# Train with quiet mode
llmrouter train --router hybrid_llm --config configs/model_config_train/hybrid_llm.yaml --quiet
```

### Inference

```bash
# Route a single query
llmrouter infer --router hybrid_llm --config configs/model_config_test/hybrid_llm.yaml \
    --query "What is photosynthesis?"

# Route queries from a file
llmrouter infer --router hybrid_llm --config configs/model_config_test/hybrid_llm.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router hybrid_llm --config configs/model_config_test/hybrid_llm.yaml \
    --query "Explain neural networks" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router hybrid_llm --config configs/model_config_test/hybrid_llm.yaml

# Launch with custom port
llmrouter chat --router hybrid_llm --config configs/model_config_test/hybrid_llm.yaml --port 8080

# Create a public shareable link
llmrouter chat --router hybrid_llm --config configs/model_config_test/hybrid_llm.yaml --share
```

---

## Usage Examples

### Training

```python
from llmrouter.models import HybridLLMRouter, HybridLLMTrainer

router = HybridLLMRouter(yaml_path="configs/model_config_train/hybrid_llm.yaml")
trainer = HybridLLMTrainer(router=router)
trainer.train()
```

### Inference

```python
from llmrouter.models import HybridLLMRouter

router = HybridLLMRouter(yaml_path="configs/model_config_test/hybrid_llm.yaml")
result = router.route_single({"query": "What is photosynthesis?"})

print(f"Routed to: {result['model_name']}")
print(f"Router Score: {result['router_score']}")  # Predicted quality gap
```

## YAML Configuration Example

```yaml
router_mode: "probabilistic"
router_tau: 0.1
router_threshold: 0.5

hparam:
  hidden_layer_sizes: [128, 64]
  activation: relu
  solver: adam
  max_iter: 300

model_path:
  save_model_path: "saved_models/hybrid_llm/hybrid_trained.pkl"
```

## Advantages

- ✅ **Cost-Quality Balance**: Optimizes trade-off between cost and performance
- ✅ **Learned Policy**: Adapts to data patterns
- ✅ **Multiple Modes**: Three strategies for different use cases
- ✅ **Two-Model Focus**: Simpler than multi-model routing

## Limitations

- ❌ **Two Models Only**: Routes between exactly 2 models (small and large)
- ❌ **Requires Both**: Needs historical data with both model performances
- ❌ **Model Selection**: Automatically picks smallest and largest (no manual control)
- ❌ **Training Needed**: Supervised learning approach

## When to Use

**Good For:**
- Clear small-large model pair (e.g., 3B vs 70B)
- Want to optimize cost-quality trade-off
- Have training data with both model performances
- Binary routing decision is acceptable

**Alternatives:**
- 3+ models → MLP/SVM/KNN Router
- No training data → Automix Router (self-verification)
- Always small → Smallest LLM Router
- Always large → Largest LLM Router

## Related Routers

- **Automix Router**: Similar cost-quality goal but uses self-verification
- **Smallest/Largest LLM Routers**: Extreme versions (always one model)
- **MLP Router**: General multi-class classifier

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
