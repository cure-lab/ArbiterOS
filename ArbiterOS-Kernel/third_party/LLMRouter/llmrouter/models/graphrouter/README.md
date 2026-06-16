# Graph Router (GNN-Based Router)

## Overview

The **Graph Router** uses Graph Neural Networks (GNNs) to make routing decisions by modeling queries and LLMs as nodes in a heterogeneous graph. It learns routing patterns by propagating information through the graph structure, capturing complex relationships between queries, LLMs, and their performance characteristics.

## Paper Reference

This router implements the **GraphRouter** approach:

- **[GraphRouter: A Graph-based Router for LLM Selections](https://arxiv.org/abs/2410.03834)**
  - (2024). arXiv:2410.03834.
  - Constructs heterogeneous graph with task, query, and LLM nodes for routing.

- **GNN Foundations**: Kipf, T. N., & Welling, M. (2017). "Semi-supervised classification with graph convolutional networks." ICLR.
- **Application**: Treats LLM routing as link prediction in a bipartite query-model graph.

## How It Works

### Graph Structure

```
Query Nodes ─── edges(performance) ──→ LLM Nodes

        GNN Message Passing
              ↓
         Predictions
```

**Node Types:**
- **Query Nodes**: Each query is a node with Longformer embedding features
- **LLM Nodes**: Each LLM is a node with learned/provided embeddings
- **Edges**: Connect queries to all LLMs, weighted by performance scores

### Routing Mechanism

1. **Graph Construction**:
   - Create bipartite graph: queries on one side, LLMs on the other
   - Add edges from each query to all LLMs
   - Edge features: performance scores (or 0 for new queries)

2. **GNN Forward Pass**:
   - Aggregate information from neighboring nodes
   - Update node representations using message passing
   - Apply graph attention or convolution layers

3. **Prediction**:
   - For each query-LLM edge, predict suitability score
   - Select LLM with highest predicted score

### Training Strategy

Uses **edge masking** for training:
- Mask a portion of edges (e.g., 30%)
- Train GNN to predict performance on masked edges
- Evaluation on validation set with different masked edges

## Configuration Parameters

### Training Hyperparameters (`hparam` in config)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `hidden_dim` | int | `64` | Hidden layer dimension for GNN. Controls model capacity. Range: 32-256. |
| `learning_rate` | float | `0.001` | Learning rate for AdamW optimizer. Range: 0.0001-0.01. |
| `weight_decay` | float | `0.0001` | L2 regularization weight decay. Prevents overfitting. |
| `train_epoch` | int | `100` | Number of training epochs. Increase for larger graphs. |
| `batch_size` | int | `4` | Number of masked samples per gradient step. |
| `train_mask_rate` | float | `0.3` | Fraction of edges to mask during training (0.0-1.0). |
| `val_split_ratio` | float | `0.2` | Ratio of training data used for validation. |
| `random_state` | int | `42` | Random seed for reproducibility. |

### Data Paths

| Parameter | Description |
|-----------|-------------|
| `routing_data_train` | Training query-LLM performance data (JSONL) |
| `query_embedding_data` | Pre-computed Longformer query embeddings (PyTorch tensor) |
| `llm_data` | LLM information with optional embeddings (JSON) |

### Model Paths

| Parameter | Purpose |
|-----------|---------|
| `save_model_path` | Where to save trained GNN model |
| `load_model_path` | Model to load for inference |

## CLI Usage

The Graph Router can be used via the `llmrouter` command-line interface:

### Training

```bash
# Train the Graph router (GPU recommended)
llmrouter train --router graphrouter --config configs/model_config_train/graphrouter.yaml --device cuda

# Train with quiet mode
llmrouter train --router graphrouter --config configs/model_config_train/graphrouter.yaml --device cuda --quiet
```

### Inference

```bash
# Route a single query
llmrouter infer --router graphrouter --config configs/model_config_test/graphrouter.yaml \
    --query "Explain quantum mechanics"

# Route queries from a file
llmrouter infer --router graphrouter --config configs/model_config_test/graphrouter.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router graphrouter --config configs/model_config_test/graphrouter.yaml \
    --query "What is machine learning?" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router graphrouter --config configs/model_config_test/graphrouter.yaml

# Launch with custom port
llmrouter chat --router graphrouter --config configs/model_config_test/graphrouter.yaml --port 8080

# Create a public shareable link
llmrouter chat --router graphrouter --config configs/model_config_test/graphrouter.yaml --share
```

---

## Usage Examples

### Training

```python
from llmrouter.models import GraphRouter, GraphRouterTrainer

router = GraphRouter(yaml_path="configs/model_config_train/graphrouter.yaml")
trainer = GraphRouterTrainer(router=router, device="cuda")
trainer.train()
```

### Inference

```python
from llmrouter.models import GraphRouter

router = GraphRouter(yaml_path="configs/model_config_test/graphrouter.yaml")
query = {"query": "Explain quantum mechanics"}
result = router.route_single(query)
print(f"Selected: {result['model_name']}")
```

## YAML Configuration Example

```yaml
data_path:
  routing_data_train: 'data/example_data/routing_data/default_routing_train_data.jsonl'
  query_embedding_data: 'data/example_data/routing_data/query_embeddings_longformer.pt'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  save_model_path: 'saved_models/graphrouter/graphrouter.pt'

hparam:
  hidden_dim: 64
  learning_rate: 0.001
  weight_decay: 0.0001
  train_epoch: 100
  batch_size: 4
  train_mask_rate: 0.3
  val_split_ratio: 0.2

metric:
  weights:
    performance: 1
```

## Advantages

- ✅ **Relational Learning**: Captures complex query-model relationships
- ✅ **Graph Structure**: Leverages network effects and transitivity
- ✅ **Flexible**: Can incorporate additional node/edge features
- ✅ **Semi-Supervised**: Can predict on partially observed data

## Limitations

- ❌ **Computational Cost**: GNN training slower than simpler methods
- ❌ **Graph Construction**: Requires building full bipartite graph
- ❌ **Cold Start**: New queries/models need graph re-construction
- ❌ **Hyperparameter Sensitivity**: Many architectural choices

## When to Use Graph Router

**Good Use Cases:**
- Large datasets with rich relational structure
- Query-model relationships exhibit network effects
- Have LLM embeddings or features beyond performance
- Want to model higher-order interactions

**Alternatives:**
- Simple relationships → Use MLP/SVM Router
- Small datasets → Use KNN Router
- Need fast training → Use ELO Router

## Related Routers

- **RouterDC**: Also uses structured learning but with contrastive loss
- **MF Router**: Learns latent spaces but without graph structure
- **MLP Router**: Standard neural network, no graph

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
