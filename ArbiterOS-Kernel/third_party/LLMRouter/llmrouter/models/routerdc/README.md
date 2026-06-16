# RouterDC (Dual-Contrastive Router)

## Overview

The **RouterDC** is a sophisticated routing method that uses dual-contrastive learning to make routing decisions. It combines a pre-trained encoder (mDeBERTa) with learnable LLM embeddings and employs three complementary contrastive learning objectives to learn effective routing representations.

## Paper Reference

This router implements the **RouterDC** approach from:

- **[RouterDC: Query-Based Router by Dual Contrastive Learning for Assembling Large Language Models](https://arxiv.org/abs/2409.19886)**
  - Chen, S., Jiang, W., Lin, B., Kwok, J., & Zhang, Y. (2024). arXiv:2409.19886. Published at NeurIPS 2024.
  - Proposes dual contrastive learning for query-to-LLM routing.

The router uses three types of contrastive learning:
1. **Sample-LLM Contrastive Loss**: Learn query-to-model affinities
2. **Sample-Sample Contrastive Loss**: Group similar queries together (task-level)
3. **Cluster Contrastive Loss**: Leverage query clustering for better representations

## How It Works

### Architecture

```
Query → mDeBERTa Encoder → Hidden State → Similarity Computation → LLM Selection
                                            ↓
                                   Learnable LLM Embeddings
```

### Routing Mechanism

1. **Query Encoding**: Input query is encoded using mDeBERTa (multilingual DeBERTa v3)
2. **LLM Embeddings**: Each LLM has a learnable embedding vector
3. **Similarity Computation**: Compute cosine similarity or inner product between query encoding and LLM embeddings
4. **Scoring**: Apply temperature-scaled softmax to get routing scores
5. **Selection**: Select the LLM with the highest score

### Training Process

The model is trained using three contrastive learning objectives:

#### 1. Sample-LLM Contrastive Loss
- Pulls query embeddings closer to embeddings of well-performing LLMs
- Pushes query embeddings away from poorly-performing LLMs
- Uses top-k LLMs as positive samples, last-k as negative samples

#### 2. Sample-Sample Contrastive Loss (Task-Level)
- Groups queries from the same task together
- Separates queries from different tasks
- Helps learn task-specific routing patterns

#### 3. Cluster Contrastive Loss
- Clusters training queries using K-means
- Learns cluster-aware representations
- Improves generalization to diverse query types

### Dual-Contrastive Strategy

The "dual" in RouterDC refers to two complementary contrastive mechanisms:
1. **Query-Model Contrast**: Aligns queries with suitable models
2. **Query-Query Contrast**: Groups similar queries (via tasks and clusters)

This dual approach ensures the router learns both:
- Which models work well for which queries
- What makes queries similar or different

## Configuration Parameters

### Training Hyperparameters (`hparam` in config)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `hidden_state_dim` | int | `768` | Hidden state dimension of the backbone encoder (mDeBERTa base = 768). |
| `similarity_function` | str | `"cos"` | Similarity function for computing query-LLM affinity. Options: `"cos"` (cosine similarity), `"inner"` (inner product). Cosine is recommended. |
| `batch_size` | int | `32` | Training batch size. Larger values improve training stability but require more memory. |
| `training_steps` | int | `500` | Total number of training steps. Increase for larger datasets. |
| `learning_rate` | float | `5.0e-5` | Learning rate for AdamW optimizer. Use lower values (1e-5) for larger models. |
| `top_k` | int | `3` | Number of top-performing LLMs to use as positive samples. |
| `last_k` | int | `3` | Number of worst-performing LLMs to use as negative samples. |
| `temperature` | float | `1.0` | Temperature for softmax in contrastive loss. Lower values (0.1-0.5) sharpen distributions. |
| `sample_loss_weight` | float | `0.0` | Weight for sample-sample contrastive loss. Set to 1.0 to enable task-level grouping. |
| `cluster_loss_weight` | float | `1.0` | Weight for cluster contrastive loss. Higher values emphasize cluster-aware learning. |
| `H` | int | `3` | Number of negative samples per positive in contrastive loss. |
| `gradient_accumulation` | int | `1` | Gradient accumulation steps for larger effective batch sizes. |
| `n_clusters` | int | `3` | Number of clusters for K-means clustering of training queries. |
| `max_test_samples` | int | `500` | Maximum number of test samples to use (null for all). Useful for quick evaluation. |
| `source_max_token_len` | int | `512` | Maximum token length for queries. Longer queries are truncated. |
| `target_max_token_len` | int | `512` | Maximum token length for LLM names/descriptions. |
| `device` | str | `"cpu"` | Device for training: `"cpu"` or `"cuda"`. GPU strongly recommended. |
| `seed` | int | `1` | Random seed for reproducibility. |
| `eval_steps` | int | `50` | Evaluate model every N training steps. |

### Inference Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `inference_batch_size` | int | `64` | Batch size for inference. Larger values speed up batch routing. |
| `inference_temperature` | float | `1.0` | Temperature for routing scores during inference. Use 1.0 for standard softmax. |

### Data Paths

| Parameter | Description |
|-----------|-------------|
| `routing_data_train` | Training data with query-LLM performance pairs (JSONL format) |
| `routing_data_test` | Test data for evaluation |
| `llm_data` | LLM candidate information |

### Model Paths

| Parameter | Purpose | Usage |
|-----------|---------|-------|
| `backbone_model` | Pre-trained encoder to initialize from | Usually `"microsoft/mdeberta-v3-base"` |
| `save_model_path` | Where to save the trained model | Training: saves best model checkpoint |
| `load_model_path` | Model to load for inference | Testing: loads trained model weights |

## CLI Usage

RouterDC can be used via the `llmrouter` command-line interface:

### Training

```bash
# Train the RouterDC (GPU recommended)
llmrouter train --router routerdc --config configs/model_config_train/dcrouter.yaml --device cuda

# Train with quiet mode
llmrouter train --router routerdc --config configs/model_config_train/dcrouter.yaml --device cuda --quiet
```

### Inference

```bash
# Route a single query
llmrouter infer --router routerdc --config configs/model_config_test/dcrouter.yaml \
    --query "Explain the concept of machine learning"

# Route queries from a file
llmrouter infer --router routerdc --config configs/model_config_test/dcrouter.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router routerdc --config configs/model_config_test/dcrouter.yaml \
    --query "What is deep learning?" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router routerdc --config configs/model_config_test/dcrouter.yaml

# Launch with custom port
llmrouter chat --router routerdc --config configs/model_config_test/dcrouter.yaml --port 8080

# Create a public shareable link
llmrouter chat --router routerdc --config configs/model_config_test/dcrouter.yaml --share
```

---

## Usage Examples

### Training the RouterDC

```python
from llmrouter.models import DCRouter, DCRouterTrainer

# Initialize router with training configuration
router = DCRouter(yaml_path="configs/model_config_train/dcrouter.yaml")

# Create trainer
trainer = DCRouterTrainer(router=router, device="cuda")

# Train the model
trainer.train()
# Best model will be saved to save_model_path
```

**Command Line Training:**
```bash
python tests/train_test/test_dcrouter.py --yaml_path configs/model_config_train/dcrouter.yaml
```

### Inference: Routing a Single Query

```python
from llmrouter.models import DCRouter

# Initialize router with test configuration (loads trained model)
router = DCRouter(yaml_path="configs/model_config_test/dcrouter.yaml")

# Route a single query
query = {"query": "Explain the concept of machine learning"}
result = router.route_single(query)

print(f"Selected Model: {result['model_name']}")
```

### Inference: Batch Routing with API Execution

```python
from llmrouter.models import DCRouter

# Initialize router
router = DCRouter(yaml_path="configs/model_config_test/dcrouter.yaml")

# Prepare batch of queries
queries = [
    {"query": "What is deep learning?", "ground_truth": "..."},
    {"query": "Solve 2x + 5 = 15", "ground_truth": "x = 5"},
    {"query": "Write a Python function to calculate factorial", "ground_truth": "..."}
]

# Route and execute queries
results = router.route_batch(batch=queries, task_name="general")

for result in results:
    print(f"Query: {result['query']}")
    print(f"Routed to: {result['model_name']}")
    print(f"Response: {result['response']}")
    print(f"Performance: {result.get('task_performance', 'N/A')}")
    print("-" * 80)
```

## YAML Configuration Example

**Training Configuration** (`configs/model_config_train/dcrouter.yaml`):

```yaml
data_path:
  routing_data_train: 'data/example_data/routing_data/default_routing_train_data.jsonl'
  routing_data_test: 'data/example_data/routing_data/default_routing_test_data.jsonl'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  save_model_path: 'saved_models/dcrouter/dcrouter_model.pth'
  backbone_model: 'microsoft/mdeberta-v3-base'

hparam:
  # Model architecture
  hidden_state_dim: 768
  similarity_function: "cos"

  # Training
  batch_size: 32
  training_steps: 500
  learning_rate: 5.0e-5

  # Contrastive loss configuration
  top_k: 3                          # Top performers as positives
  last_k: 3                         # Worst performers as negatives
  temperature: 1.0
  sample_loss_weight: 0.0           # Disable task-level loss
  cluster_loss_weight: 1.0          # Enable cluster loss
  H: 3                              # Negative samples per positive

  # Data preprocessing
  n_clusters: 3
  max_test_samples: 500

  # Device
  device: "cuda"                    # GPU recommended

metric:
  weights:
    performance: 1
    cost: 0
    llm_judge: 0
```

**Testing Configuration** (`configs/model_config_test/dcrouter.yaml`):

```yaml
data_path:
  query_data_test: 'data/example_data/query_data/default_query_test.jsonl'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  load_model_path: 'saved_models/dcrouter/best_model.pth'
  backbone_model: 'microsoft/mdeberta-v3-base'

hparam:
  hidden_state_dim: 768
  similarity_function: "cos"
  inference_batch_size: 64
  inference_temperature: 1.0
  device: "cuda"
```

## Advantages

- ✅ **State-of-the-Art Encoder**: Uses mDeBERTa, a powerful multilingual pre-trained model
- ✅ **Multi-Level Learning**: Combines sample, task, and cluster-level contrastive signals
- ✅ **Flexible Similarity**: Supports both cosine similarity and inner product
- ✅ **Cluster-Aware**: Leverages query clustering for better generalization
- ✅ **Multilingual**: mDeBERTa supports 100+ languages
- ✅ **End-to-End Learnable**: Jointly learns query and LLM embeddings

## Limitations

- ❌ **Requires GPU**: Training is slow on CPU due to transformer encoder
- ❌ **Hyperparameter Sensitive**: Many hyperparameters to tune (loss weights, temperature, k values)
- ❌ **Training Data Needed**: Requires substantial routing performance data
- ❌ **Large Model Size**: mDeBERTa-base has ~280M parameters
- ❌ **Complex Architecture**: More complex than simpler methods like KNN or SVM
- ❌ **Cold Start**: New LLMs require retraining to get embeddings

## When to Use RouterDC

**Good Use Cases:**
- Large-scale routing applications with GPU resources
- Multilingual routing scenarios
- When you have abundant training data (1000+ samples)
- Need state-of-the-art routing performance
- Query distribution has clear clusters/groups

**Consider Alternatives When:**
- Limited GPU resources → Use MLP/SVM/KNN Router
- Small training dataset (<500 samples) → Use KNN Router
- Need fast training → Use ELO Router or heuristic methods
- Interpretability is critical → Use simpler models
- Single-language routing → Standard BERT may suffice

## Hyperparameter Tuning Tips

1. **Loss Weights**:
   - Start with `cluster_loss_weight=1.0`, `sample_loss_weight=0.0`
   - Add `sample_loss_weight=1.0` if queries have clear task labels
   - Balance weights if both are used (e.g., 0.5 each)

2. **Contrastive Parameters**:
   - Increase `top_k` and `last_k` (to 5-7) for more robust learning
   - Lower `temperature` (0.1-0.5) for sharper distributions
   - Increase `H` (to 5-10) for more negative samples

3. **Training**:
   - Use `batch_size=32` for 16GB GPU, `batch_size=16` for 8GB GPU
   - Increase `training_steps` (to 1000+) for larger datasets
   - Use `gradient_accumulation` if GPU memory is limited

4. **Clustering**:
   - Set `n_clusters` ≈ sqrt(num_training_queries) / 10
   - Try 3-10 clusters depending on data diversity

5. **Evaluation**:
   - Monitor validation accuracy every `eval_steps`
   - Use early stopping if validation loss plateaus

## Implementation Details

- **Framework**: PyTorch + Hugging Face Transformers
- **Backbone**: mDeBERTa-v3-base (microsoft/mdeberta-v3-base)
- **Optimizer**: AdamW with linear warmup
- **Loss**: Multi-objective contrastive loss (sample-LLM + cluster)
- **Serialization**: PyTorch state_dict saved as `.pth` files

## Related Routers

- **Graph Router**: Also uses structured representations but with GNNs
- **MLP Router**: Simpler neural network approach, faster training
- **Causal LM Router**: Uses finetuned LLM instead of encoder
- **MF Router**: Matrix factorization approach, lighter weight

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
