# Personalized Router (GNN-Based Personalized Router)

## Overview

The **Personalized Router** uses Graph Neural Networks (GNNs) to make personalized routing decisions for different users. It extends the Graph Router by incorporating user features into the graph structure, allowing the model to learn user-specific routing preferences.

## Paper Reference

This router implements the **PersonalizedRouter** approach:

- **[PersonalizedRouter: Personalized LLM Routing via Graph-based User Preference Modeling](https://arxiv.org/abs/2511.16883)**
  - Dai, Z., et al. (2025). arXiv:2511.16883.
  - Constructs heterogeneous graph with task, query, user, and LLM nodes for personalized routing.
  - Learns user-specific routing patterns through personalized message passing.

## Important Notice

**PersonalizedRouter uses a dedicated dataset and data format that differs from other routers.**

- **Dataset Source**: PersonaRoute-Bench
- **Download Link**:
  ```text
  https://huggingface.co/datasets/ulab-ai/PersonaRoute-Bench
  ```
- **Available Files**: The dataset provides single-file CSVs and train/val/test splits (e.g., `router_user_data.csv`, `router_user_train_data.csv`, `router_user_val_data.csv`, `router_user_test_data.csv`).

## Data Format

PersonaRoute-Bench is provided as **CSV**.

### Columns

| Column | Description |
|--------|-------------|
| `user_id` | User identifier for personalization. |
| `performance_preference` | User preference weight between performance and cost. |
| `task_id` | Task identifier. |
| `query` | Query text. |
| `query_embedding` | Query embedding vector. |
| `effect` | Performance effect/score for the query-LLM pair. |
| `cost` | Cost signal for the query-LLM pair. |
| `ground_truth` | Ground-truth outcome or label. |
| `metric` | Metric name or category for the row. |
| `llm` | LLM name for the row. |
| `task_description` | Task description text. |
| `task_description_embedding` | Task description embedding vector. |
| `response` | Model response text. |
| `reward` | Reward score. |
| `best_llm` | Best LLM indicator for the query. |

## Data Preparation

### Step 1: Download the dataset

Use one of the following approaches:

```bash
# Using Hugging Face CLI (recommended)
huggingface-cli download ulab-ai/PersonaRoute-Bench --repo-type dataset --local-dir data/personaroute_bench
```

```python
# Using datasets library
from datasets import load_dataset
ds = load_dataset("ulab-ai/PersonaRoute-Bench")
```

### Step 2: Choose the CSV files

You can use either:

- A single CSV (e.g., `router_user_data.csv`)
- Or train/val/test splits (e.g., `router_user_train_data.csv`, `router_user_val_data.csv`, `router_user_test_data.csv`)

The dataset file list is available on the Hugging Face dataset page.

### Step 3: Point your config to the data

Set the `data_path` fields in your YAML config to the downloaded CSVs (see the example in this README).

## How It Works

### Graph Structure

```
                      User Nodes
                            |
                            |
        Query Nodes ─── edges ──→ LLM Nodes
                            |
                            |
                      Task Nodes

              GNN Message Passing
                    ↓
         Personalized Predictions
```

**Node Types:**
- **Query Nodes**: Each query is a node with embedding features
- **LLM Nodes**: Each LLM is a node with learned/provided embeddings
- **User Nodes**: Each user is a node, whose embedding represents the user’s preference features.
- **Task Nodes**: Each task has an embedding
- **Edges**: Connect queries to LLMs, weighted by scores

### Routing Mechanism

PersonalizedRouter models LLM routing as a heterogeneous graph learning problem that adapts model selection to individual user preferences.
- Construct a global heterogeneous graph containing users, queries, tasks, and LLMs
- Learn latent user preference embeddings directly from interaction data
- Use heterogeneous GNN message passing to jointly encode user, task, and LLM characteristics
- For each (user, query) pair, estimate a personalized utility score over candidate LLMs and route to the most suitable model

This formulation enables the same query to be routed differently across users and supports efficient generalization across users and tasks.

### Training Strategy

Uses **edge masking** for training with personalization:
- Mask a portion of edges (e.g., 30%) for each user
- Train GNN to predict performance on masked edges
- Evaluation on validation set with different masked edges
- Same query can have different optimal LLMs for different users

## Configuration Parameters

### Training Hyperparameters (`hparam` in config)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `embedding_dim` | int | `64` | Hidden layer dimension for GNN. Controls model capacity. Range: 32-256. |
| `user_num` | int | `1000` | Number of users for personalization. Each user gets a unique node. |
| `num_task` | int | `4` | Number of tasks for multi-task learning. |
| `learning_rate` | float | `0.001` | Learning rate for AdamW optimizer. Range: 0.0001-0.01. |
| `weight_decay` | float | `0.0001` | L2 regularization weight decay. Prevents overfitting. |
| `train_epoch` | int | `100` | Number of training epochs. Increase for larger graphs. |
| `batch_size` | int | `4` | Number of masked samples per gradient step. |
| `train_mask_rate` | float | `0.3` | Fraction of edges to mask during training (0.0-1.0). |
| `split_ratio` | list | `[0.6, 0.2, 0.2]` | Ratio for train/val/test split. |
| `llm_family` | list | `[]` | List of LLM families for additional edges (e.g., ["gpt", "claude"]). |
| `random_state` | int | `42` | Random seed for reproducibility. |

### Data Paths

| Parameter | Description |
|-----------|-------------|
| `routing_data_path` | Routing data CSV path or a directory containing a single CSV. |
| `routing_data_train` | Training routing data CSV. |
| `routing_data_val` | Validation routing data CSV. |
| `routing_data_test` | Test routing data CSV. |
| `llm_data` | LLM metadata (JSON). |
| `llm_embedding_data` | Pre-computed LLM embeddings (pickle / `.pkl`). |

### Model Paths

| Parameter | Purpose |
|-----------|---------|
| `save_model_path` | Where to save trained GNN model |
| `load_model_path` | Model to load for inference |
| `ini_model_path` | Initial model weights (optional) |

## CLI Usage

The Personalized Router can be used via the `llmrouter` command-line interface:

### Training

```bash
# Train the Personalized router (GPU recommended)
llmrouter train --router personalizedrouter --config configs/model_config_train/personalizedrouter.yaml --device cuda

# Train with quiet mode
llmrouter train --router personalizedrouter --config configs/model_config_train/personalizedrouter.yaml --device cuda --quiet
```

### Inference

```bash
# Route a single query with user_id
llmrouter infer --router personalizedrouter --config configs/model_config_test/personalizedrouter.yaml \
    --query "Explain quantum mechanics"

# Route queries from a file (with user_id in each query)
llmrouter infer --router personalizedrouter --config configs/model_config_test/personalizedrouter.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router personalizedrouter --config configs/model_config_test/personalizedrouter.yaml \
    --query "What is machine learning?" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router personalizedrouter --config configs/model_config_test/personalizedrouter.yaml

# Launch with custom port
llmrouter chat --router personalizedrouter --config configs/model_config_test/personalizedrouter.yaml --port 8080

# Create a public shareable link
llmrouter chat --router personalizedrouter --config configs/model_config_test/personalizedrouter.yaml --share
```

---

## Usage Examples

### Training

```python
from llmrouter.models import PersonalizedRouter
from llmrouter.models.personalizedrouter.trainer import PersonalizedRouterTrainer

router = PersonalizedRouter(yaml_path="configs/model_config_train/personalizedrouter.yaml")
trainer = PersonalizedRouterTrainer(router=router, device="cuda")
trainer.train()
```

### Inference

```python
from llmrouter.models import PersonalizedRouter

router = PersonalizedRouter(yaml_path="configs/model_config_test/personalizedrouter.yaml")

# Single query with user personalization
query = {"query": "Explain quantum mechanics", "user_id": 0}
result = router.route_single(query)
print(f"Selected for user 0: {result['model_name']}")

# Different user might get different recommendation
query2 = {"query": "Explain quantum mechanics", "user_id": 1}
result2 = router.route_single(query2)
print(f"Selected for user 1: {result2['model_name']}")
```

### Batch Inference

```python
from llmrouter.models import PersonalizedRouter

router = PersonalizedRouter(yaml_path="configs/model_config_test/personalizedrouter.yaml")

# Batch queries with different users
batch = [
    {"query": "What is the capital of France?", "user_id": 0},
    {"query": "Who wrote Romeo and Juliet?", "user_id": 1},
    {"query": "How does photosynthesis work?", "user_id": 2},
]

results = router.route_batch(batch=batch)
for result in results:
    print(f"User {result.get('user_id')}: {result['query'][:30]}... -> {result['model_name']}")
```

## YAML Configuration Example

```yaml
data_path:
  routing_data_path: 'data/personaroute_bench/router_user_data_v1.csv'
  routing_data_train: 'data/personaroute_bench/router_user_train_data_v1.csv'
  routing_data_val: 'data/personaroute_bench/router_user_val_data_v1.csv'
  routing_data_test: 'data/personaroute_bench/router_user_test_data_v1.csv'
  llm_data: 'data/personaroute_bench/LLM_Descriptions_large.json'
  llm_embedding_data: 'data/personaroute_bench/llm_description_embedding_large.pkl'

model_path:
  save_model_path: 'saved_models/personalizedrouter/personalizedrouter.pt'
  load_model_path: 'saved_models/personalizedrouter/personalizedrouter.pt'

hparam:
  embedding_dim: 64
  edge_dim: 1
  user_num: 1000
  num_task: 4
  learning_rate: 0.001
  weight_decay: 0.0001
  train_epoch: 100
  batch_size: 4
  train_mask_rate: 0.3
  split_ratio: [0.6, 0.2, 0.2]
  llm_family: []
  random_state: 42

metric:
  weights:
    performance: 1
```

## Advantages

- ✅ **Personalization**: Learns different routing strategies for different users
- ✅ **User Features**: Incorporates user-specific information into routing decisions
- ✅ **Multi-task Support**: Supports multiple tasks with task embeddings
- ✅ **Relational Learning**: Captures complex query-model relationships per user
- ✅ **Graph Structure**: Leverages network effects and transitivity
- ✅ **Flexible**: Can incorporate additional node/edge features

## Limitations

- ❌ **Computational Cost**: GNN training slower than simpler methods
- ❌ **Cold Start**: New users need to be added to the graph
- ❌ **Memory Usage**: Requires storing embeddings for all users
- ❌ **Hyperparameter Sensitivity**: Many architectural choices

## When to Use Personalized Router

**Good Use Cases:**
- Multiple users with distinct preferences
- Want to learn user-specific routing patterns
- Have user interaction history data
- Need multi-task learning support
- Query-model relationships vary by user

**Alternatives:**
- Single user scenario → Use Graph Router
- Simple relationships → Use MLP/SVM Router
- Small datasets → Use KNN Router
- Need fast training → Use ELO Router

## Related Routers

- **Graph Router**: Base GNN router without personalization
- **RouterDC**: Also uses structured learning but with contrastive loss
- **MF Router**: Learns latent spaces but without graph structure
- **MLP Router**: Standard neural network, no graph

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
