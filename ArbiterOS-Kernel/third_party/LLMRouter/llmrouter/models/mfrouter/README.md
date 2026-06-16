# MF Router (Matrix Factorization Router)

## Overview

The **MF Router** (Matrix Factorization Router) uses bilinear matrix factorization to learn latent embeddings for both queries and LLMs. It predicts the best model for each query by computing affinities in a shared latent space, similar to collaborative filtering techniques used in recommendation systems.

## Paper Reference

This router is inspired by **RouteLLM** and matrix factorization approaches:

- **[RouteLLM: Learning to Route LLMs with Preference Data](https://arxiv.org/abs/2406.18665)**
  - Ong, I., et al. (2024). arXiv:2406.18665. Published at ICLR 2025.
  - Proposes matrix factorization router trained on human preference data.

- **Matrix Factorization**: Koren, Y., Bell, R., & Volinsky, C. (2009). "Matrix factorization techniques for recommender systems." Computer.

The approach treats LLM routing as a recommendation problem: given a query, recommend the best-performing model.

## How It Works

### Architecture

```
Query → Longformer Embedding → Projection → Latent Space
                                             ↓
                                        Interaction
                                             ↓
Model Embeddings (Learned) ──────────→  Scoring → LLM Selection
```

### Bilinear Scoring Function

The router computes routing scores using:

```
δ(M, q) = w2^T (v_m ⊙ (W1 * v_q))
```

Where:
- `v_q`: Query embedding projected into latent space
- `v_m`: Learnable model embedding
- `⊙`: Element-wise product (Hadamard product)
- `W1`: Linear projection matrix
- `w2`: Final scoring vector

### Training Process

1. **Pairwise Sample Generation**:
   - For each query, identify the best-performing model (winner)
   - Create pairs: (query, winner, loser) for all other models

2. **Pairwise Ranking Loss**:
   - Optimize: `δ(winner, q) > δ(loser, q)`
   - Uses logistic loss for differentiability

3. **Latent Space Learning**:
   - Jointly learns query projection `W1` and model embeddings `P`
   - Normalized embeddings improve generalization

## Configuration Parameters

### Training Hyperparameters (`hparam` in config)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `latent_dim` | int | `128` | Dimension of latent embedding space for models. Higher values increase model capacity. Range: 64-256. |
| `text_dim` | int | `768` | Dimension of Longformer query embeddings (fixed at 768). |
| `lr` | float | `0.001` | Learning rate for Adam optimizer. Typical range: 0.0001-0.01. |
| `epochs` | int | `5` | Number of training epochs. Increase for larger datasets. |
| `batch_size` | int | `64` | Batch size for training. Larger values speed up training but use more memory. Range: 32-256. |
| `noise_alpha` | float | `0.0` | Optional Gaussian noise added to query embeddings for regularization. Range: 0.0-0.1. |

### Data Paths

| Parameter | Description |
|-----------|-------------|
| `routing_data_train` | Training data with query-LLM performance pairs (JSONL) |
| `query_embedding_data` | Pre-computed Longformer query embeddings (PyTorch tensor file) |
| `llm_data` | LLM candidate information |

### Model Paths

| Parameter | Purpose | Usage |
|-----------|---------|-------|
| `save_model_path` | Where to save trained model | Training: saves model state_dict as `.pkl` |
| `load_model_path` | Model to load for inference | Testing: loads trained model weights |

## CLI Usage

The MF Router can be used via the `llmrouter` command-line interface:

### Training

```bash
# Train the MF router
llmrouter train --router mfrouter --config configs/model_config_train/mfrouter.yaml

# Train with GPU acceleration
llmrouter train --router mfrouter --config configs/model_config_train/mfrouter.yaml --device cuda

# Train with quiet mode
llmrouter train --router mfrouter --config configs/model_config_train/mfrouter.yaml --quiet
```

### Inference

```bash
# Route a single query
llmrouter infer --router mfrouter --config configs/model_config_test/mfrouter.yaml \
    --query "Explain neural networks"

# Route queries from a file
llmrouter infer --router mfrouter --config configs/model_config_test/mfrouter.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router mfrouter --config configs/model_config_test/mfrouter.yaml \
    --query "What is deep learning?" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router mfrouter --config configs/model_config_test/mfrouter.yaml

# Launch with custom port
llmrouter chat --router mfrouter --config configs/model_config_test/mfrouter.yaml --port 8080

# Create a public shareable link
llmrouter chat --router mfrouter --config configs/model_config_test/mfrouter.yaml --share
```

---

## Usage Examples

### Training the MF Router

```python
from llmrouter.models import MFRouter, MFRouterTrainer

# Initialize router with training configuration
router = MFRouter(yaml_path="configs/model_config_train/mfrouter.yaml")

# Create trainer
trainer = MFRouterTrainer(router=router, device="cpu")

# Train the model
trainer.train()
# Model saved to save_model_path
```

**Command Line:**
```bash
python tests/train_test/test_mfrouter.py --yaml_path configs/model_config_train/mfrouter.yaml
```

### Inference

```python
from llmrouter.models import MFRouter

# Initialize router
router = MFRouter(yaml_path="configs/model_config_test/mfrouter.yaml")

# Route queries
query = {"query": "Explain neural networks"}
result = router.route_single(query)
print(f"Selected Model: {result['model_name']}")
```

## YAML Configuration Example

```yaml
data_path:
  routing_data_train: 'data/example_data/routing_data/default_routing_train_data.jsonl'
  query_embedding_data: 'data/example_data/routing_data/query_embeddings_longformer.pt'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  save_model_path: 'saved_models/mfrouter/mfrouter.pkl'

hparam:
  latent_dim: 128
  text_dim: 768
  lr: 0.001
  epochs: 5
  batch_size: 64
  noise_alpha: 0.0

metric:
  weights:
    performance: 1
    cost: 0
```

## Advantages

- ✅ **Latent Space Learning**: Learns meaningful query-model representations
- ✅ **Collaborative Filtering**: Leverages patterns across queries and models
- ✅ **Scalable**: Efficient inference with learned embeddings
- ✅ **Flexible Capacity**: Latent dimension tunable for different data sizes

## Limitations

- ❌ **Cold Start**: New models require retraining to get embeddings
- ❌ **Embedding Dependency**: Requires pre-computed query embeddings
- ❌ **Limited Interpretability**: Latent space is not easily interpretable
- ❌ **Pairwise Training**: Requires all model pairs for training

## When to Use MF Router

**Good Use Cases:**
- Medium to large datasets with diverse queries
- Multiple models with varying capabilities
- Want to learn query-model affinities
- Collaborative filtering mindset

**Consider Alternatives:**
- Small datasets → KNN Router
- Need interpretability → SVM/MLP Router
- Real-time model updates → Online learning methods

## Related Routers

- **Graph Router**: Also learns structured representations
- **MLP/SVM Routers**: Supervised alternatives
- **KNN Router**: Instance-based, no latent space

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
