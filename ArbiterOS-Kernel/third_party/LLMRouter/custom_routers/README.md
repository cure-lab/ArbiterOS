# Custom Routers

This directory contains user-defined custom router implementations for LLMRouter.

## Quick Start

Use any custom router with:

```bash
# Inference
llmrouter infer --router <router_name> \
  --config custom_routers/<router_name>/config.yaml \
  --query "Your question here"

# Training (if supported)
llmrouter train --router <router_name> \
  --config custom_routers/<router_name>/config.yaml
```

## Available Custom Routers

### 1. RandomRouter

**Type:** Baseline (no training required)

**Description:** Randomly selects an LLM from available candidates. Useful as a baseline for comparison.

**Usage:**
```bash
llmrouter infer --router randomrouter \
  --config custom_routers/randomrouter/config.yaml \
  --query "What is AI?" \
  --route-only
```

**Features:**
- ✅ Simple implementation
- ✅ No training needed
- ✅ Good baseline for comparison
- ✅ Configurable random seed

### 2. ThresholdRouter

**Type:** Trainable router

**Description:** Routes based on estimated query difficulty. Uses a neural network to classify queries as easy/hard and routes accordingly.

**Key Concepts:**
- Easy queries → smaller/cheaper model
- Hard queries → larger/more capable model
- Learns difficulty estimation from historical routing data

**Training:**
```bash
llmrouter train --router thresholdrouter \
  --config custom_routers/thresholdrouter/config.yaml
```

**Inference:**
```bash
llmrouter infer --router thresholdrouter \
  --config custom_routers/thresholdrouter/config.yaml \
  --query "Explain quantum entanglement"
```

**Features:**
- ✅ Neural difficulty estimator
- ✅ Configurable threshold
- ✅ Full training pipeline
- ✅ Flexible model selection

**Hyperparameters:**
- `threshold`: Difficulty threshold (0.0 - 1.0)
- `small_model`: Name of efficient model
- `large_model`: Name of capable model
- `embedding_dim`: Query embedding dimension
- `hidden_dim`: Hidden layer size
- `learning_rate`: Training learning rate
- `train_epoch`: Number of training epochs

## Creating Your Own Router

See [CUSTOM_ROUTER_SUMMARY.md](../CUSTOM_ROUTER_SUMMARY.md) for detailed guide.

### Minimal Template

```python
# custom_routers/my_router/router.py
from llmrouter.models.meta_router import MetaRouter
import torch.nn as nn

class MyRouter(MetaRouter):
    def __init__(self, yaml_path: str):
        model = nn.Identity()
        super().__init__(model=model, yaml_path=yaml_path)
        self.llm_names = list(self.llm_data.keys())

    def route_single(self, query_input: dict) -> dict:
        # Your routing logic
        selected = self.llm_names[0]
        return {
            "query": query_input.get("query", ""),
            "model_name": selected,
            "predicted_llm": selected,
        }

    def route_batch(self, batch: list) -> list:
        return [self.route_single(q) for q in batch]
```

### Directory Structure

```
custom_routers/
├── README.md                    # This file
├── __init__.py                  # Package marker
├── randomrouter/                # Example 1: Simple baseline
│   ├── __init__.py
│   ├── router.py
│   ├── trainer.py
│   └── config.yaml
├── thresholdrouter/             # Example 2: Trainable router
│   ├── __init__.py
│   ├── router.py
│   ├── trainer.py
│   └── config.yaml
└── your_router/                 # Your custom router
    ├── __init__.py
    ├── router.py
    └── config.yaml
```

## Plugin Discovery

The plugin system automatically discovers routers in:

1. `./custom_routers/` (current directory) ⭐ Recommended
2. `~/.llmrouter/plugins/` (user home directory)
3. `$LLMROUTER_PLUGINS` environment variable

### Environment Variable

```bash
# Linux/Mac
export LLMROUTER_PLUGINS="/path/to/plugins1:/path/to/plugins2"

# Windows
set LLMROUTER_PLUGINS=C:\path\to\plugins1;C:\path\to\plugins2
```

## Verifying Your Router

```bash
# List all available routers (including custom)
llmrouter list-routers

# Test routing only (no API call)
llmrouter infer --router your_router \
  --config custom_routers/your_router/config.yaml \
  --query "test query" \
  --route-only

# Enable verbose output
LLMROUTER_DEBUG=1 llmrouter infer --router your_router \
  --config config.yaml --query "test" --verbose
```

## Testing Your Router

### Unit Testing

```python
# test_my_router.py
from custom_routers.my_router import MyRouter

def test_router():
    router = MyRouter("custom_routers/my_router/config.yaml")

    result = router.route_single({"query": "What is AI?"})

    assert "model_name" in result
    assert result["model_name"] in router.llm_names
    print("✅ Router test passed")

if __name__ == "__main__":
    test_router()
```

### Integration Testing

```bash
# Test with actual LLM API calls
llmrouter infer --router my_router \
  --config custom_routers/my_router/config.yaml \
  --query "Translate 'hello' to Spanish" \
  --verbose
```

## Router Design Patterns

### 1. Rule-Based Router

```python
def route_single(self, query_input):
    query = query_input['query'].lower()

    if 'code' in query or 'program' in query:
        return {"model_name": "code-specialized-model"}
    elif len(query) < 50:
        return {"model_name": "small-fast-model"}
    else:
        return {"model_name": "large-capable-model"}
```

### 2. Embedding-Based Router

```python
def route_single(self, query_input):
    embedding = self._get_embedding(query_input['query'])
    similarity_scores = self._compute_similarity(embedding)
    best_model = max(similarity_scores, key=similarity_scores.get)
    return {"model_name": best_model}
```

### 3. Cost-Aware Router

```python
def route_single(self, query_input):
    difficulty = self._estimate_difficulty(query_input)

    # Route to cheapest model that can handle the difficulty
    for model in sorted(self.llm_data.items(), key=lambda x: x[1]['cost']):
        if model[1]['capability'] >= difficulty:
            return {"model_name": model[0]}
```

### 4. Ensemble Router

```python
def route_single(self, query_input):
    # Get predictions from multiple sub-routers
    votes = [r.route_single(query_input) for r in self.sub_routers]

    # Majority voting
    from collections import Counter
    model_counts = Counter(v['model_name'] for v in votes)
    best_model = model_counts.most_common(1)[0][0]

    return {"model_name": best_model}
```

## Common Patterns

### Caching Embeddings

```python
class CachedEmbeddingRouter(MetaRouter):
    def __init__(self, yaml_path: str):
        super().__init__(...)
        self.embedding_cache = {}

    def _get_embedding(self, query):
        if query not in self.embedding_cache:
            self.embedding_cache[query] = compute_embedding(query)
        return self.embedding_cache[query]
```

### Logging Routing Decisions

```python
def route_single(self, query_input):
    result = self._do_routing(query_input)

    # Log decision
    with open('routing_log.jsonl', 'a') as f:
        log_entry = {
            'timestamp': time.time(),
            'query': query_input['query'],
            'routed_to': result['model_name'],
            'confidence': result.get('confidence')
        }
        f.write(json.dumps(log_entry) + '\n')

    return result
```

## Tips for Success

1. **Start with RandomRouter**: Understand the interface first
2. **Use Example Data**: Test with provided data before custom data
3. **Implement Incrementally**: Get basic routing working before optimization
4. **Add Logging**: Help debug and understand routing behavior
5. **Version Your Router**: Use git to track changes
6. **Document Decisions**: Comment why you route certain ways

## Sharing Your Router

Consider sharing your router if it:
- Solves a common problem
- Demonstrates a novel technique
- Achieves good performance

Submit via:
1. GitHub Pull Request
2. Community Slack channel
3. Separate package publication

## Support

- Documentation: [CUSTOM_ROUTER_SUMMARY.md](../CUSTOM_ROUTER_SUMMARY.md)
- GitHub Issues: Report bugs or request features
- Examples: Study `randomrouter` and `thresholdrouter`
- Community: Join our Slack for discussions

---

Happy routing! 🚀
