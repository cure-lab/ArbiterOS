# KNN Router

## Overview

The **KNN Router** (K-Nearest Neighbors Router) is an instance-based routing method that selects the optimal LLM based on similarity to historical queries. It requires no explicit training phase, making it simple, interpretable, and ideal for scenarios with limited labeled data.

## Paper Reference

This router implements the **K-Nearest Neighbors (KNN)** algorithm for LLM routing, as described in:

- **[FusionFactory: Fusing LLM Capabilities with Multi-LLM Log Data](https://arxiv.org/abs/2507.10540)**
  - Feng, T., Zhang, H., Lei, Z., et al. (2025). arXiv:2507.10540.
  - Proposes query-level fusion via tailored LLM routers including KNN-based approaches.

- **Original KNN Concept**:
  - Cover, T., & Hart, P. (1967). "Nearest neighbor pattern classification." IEEE Transactions on Information Theory.

- **Application to Routing**: KNN is a lazy learning algorithm that makes routing decisions by finding the K most similar historical queries and voting for the best LLM based on their performance.

## How It Works

### Architecture

```
Query → Embedding → Find K-Nearest Neighbors → Majority Vote → LLM Selection
                    (Distance Calculation)
```

### Routing Mechanism

1. **Query Embedding**: Convert the input query into a fixed-size vector using Longformer embeddings
2. **Distance Calculation**: Compute distances between the query embedding and all training query embeddings
3. **Neighbor Selection**: Select the K closest historical queries based on distance metric (Euclidean, cosine, etc.)
4. **Voting**: The K neighbors "vote" for their best-performing LLM (uniform or distance-weighted voting)
5. **Selection**: Return the LLM with the most votes

### Key Characteristics

- **Lazy Learning**: No training phase — the algorithm simply stores all training examples
- **Instance-Based**: Decisions based on local similarity, not global patterns
- **Non-Parametric**: No assumptions about data distribution
- **Interpretable**: You can inspect which similar queries influenced each routing decision

### "Training" Process

KNN doesn't have traditional training. The "training" step simply:
1. Stores query embeddings from historical data
2. Stores corresponding best-performing LLM labels
3. Builds an efficient search index (Ball Tree, KD Tree, or brute force)

During inference, the stored examples are used directly for routing decisions.

## Configuration Parameters

### Hyperparameters (`hparam` in config)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `n_neighbors` | int | `5` | **Most important parameter**: Number of neighbors (K) to consider. Small K (1-3) → sensitive to noise. Large K (10-20) → smoother boundaries but may miss local patterns. Typical range: 3-10. |
| `weights` | str | `"uniform"` | Weight function for voting. `"uniform"`: all neighbors vote equally. `"distance"`: closer neighbors have more influence (weight = 1/distance). Use `"distance"` when closer examples are more reliable. |
| `algorithm` | str | `"auto"` | Algorithm for computing neighbors. `"auto"`: automatically chooses best method. `"ball_tree"`: efficient for high dimensions. `"kd_tree"`: efficient for low dimensions. `"brute"`: exhaustive search (slow but always works). |
| `leaf_size` | int | `30` | Leaf size for Ball Tree or KD Tree. Affects speed and memory of tree construction. Larger values → faster construction but slower queries. Typical range: 20-50. |
| `p` | int | `2` | Power parameter for Minkowski metric. `p=1`: Manhattan distance (L1). `p=2`: Euclidean distance (L2, recommended). Higher p → focus on largest differences. |
| `metric` | str | `"minkowski"` | Distance metric. Options: `"minkowski"` (general, use with p parameter), `"euclidean"`, `"manhattan"`, `"cosine"` (good for text embeddings), `"chebyshev"`. For text, consider `"cosine"`. |
| `n_jobs` | int | `-1` | Number of parallel jobs. `-1`: use all CPU cores. `1`: single-threaded. Higher values speed up queries significantly. |

### Data Paths

| Parameter | Description |
|-----------|-------------|
| `query_data_train` | Historical training queries in JSONL format |
| `routing_data_train` | Historical routing performance data (query-LLM pairs with scores) |
| `query_embedding_data` | Pre-computed query embeddings (PyTorch tensor file) |
| `llm_data` | LLM candidate information (models, API names, metadata) |

### Model Paths

| Parameter | Purpose | Usage |
|-----------|---------|-------|
| `ini_model_path` | Pre-trained model to initialize from | Optional: leave empty to build from scratch |
| `save_model_path` | Where to save the KNN model | Training: model (embeddings + labels) saved here |
| `load_model_path` | Model to load for inference | Testing: path to saved `.pkl` file |

### Inference/Testing Parameters

During inference:
- The KNN model loads stored embeddings and labels
- Uses the same distance metric and K value as "training"
- Performs nearest neighbor search for each new query
- No retraining needed — can add new examples instantly

## CLI Usage

The KNN Router can be used via the `llmrouter` command-line interface:

### Training

```bash
# "Train" the KNN router (builds search index)
llmrouter train --router knnrouter --config configs/model_config_train/knnrouter.yaml

# Train with quiet mode
llmrouter train --router knnrouter --config configs/model_config_train/knnrouter.yaml --quiet
```

### Inference

```bash
# Route a single query
llmrouter infer --router knnrouter --config configs/model_config_test/knnrouter.yaml \
    --query "What are the ethical implications of AI?"

# Route queries from a file
llmrouter infer --router knnrouter --config configs/model_config_test/knnrouter.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router knnrouter --config configs/model_config_test/knnrouter.yaml \
    --query "Explain neural networks" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router knnrouter --config configs/model_config_test/knnrouter.yaml

# Launch with custom port
llmrouter chat --router knnrouter --config configs/model_config_test/knnrouter.yaml --port 8080

# Create a public shareable link
llmrouter chat --router knnrouter --config configs/model_config_test/knnrouter.yaml --share
```

---

## Usage Examples

### "Training" the KNN Router

```python
from llmrouter.models import KNNRouter, KNNRouterTrainer

# Initialize router with training configuration
router = KNNRouter(yaml_path="configs/model_config_train/knnrouter.yaml")

# Create trainer (actually just stores the data)
trainer = KNNRouterTrainer(router=router, device="cpu")

# "Train" the model (builds search index)
trainer.train()
# Model will be saved to the path specified in save_model_path
```

**Command Line Training:**
```bash
python tests/train_test/test_knnrouter.py --yaml_path configs/model_config_train/knnrouter.yaml
```

### Inference: Routing a Single Query

```python
from llmrouter.models import KNNRouter

# Initialize router with test configuration (loads stored examples)
router = KNNRouter(yaml_path="configs/model_config_test/knnrouter.yaml")

# Route a single query
query = {"query": "What are the ethical implications of AI?"}
result = router.route_single(query)

print(f"Selected Model: {result['model_name']}")
```

### Inference: Batch Routing with API Execution

```python
from llmrouter.models import KNNRouter

# Initialize router
router = KNNRouter(yaml_path="configs/model_config_test/knnrouter.yaml")

# Prepare batch of queries
queries = [
    {"query": "Explain neural networks", "ground_truth": "..."},
    {"query": "Debug this Python code: ...", "ground_truth": "..."}
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

### Using with Specific Tasks

```python
# Route queries for benchmark tasks
queries = [
    {
        "query": "Which element has atomic number 6?",
        "choices": ["A. Nitrogen", "B. Carbon", "C. Oxygen", "D. Hydrogen"],
        "ground_truth": "B"
    }
]

results = router.route_batch(batch=queries, task_name="mmlu")
```

## YAML Configuration Example

**Training Configuration** (`configs/model_config_train/knnrouter.yaml`):

```yaml
data_path:
  query_data_train: 'data/example_data/query_data/default_query_train.jsonl'
  routing_data_train: 'data/example_data/routing_data/default_routing_train_data.jsonl'
  query_embedding_data: 'data/example_data/routing_data/query_embeddings_longformer.pt'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  ini_model_path: ''  # Leave empty to build from scratch
  save_model_path: 'saved_models/knnrouter/knnrouter.pkl'

hparam:
  n_neighbors: 5        # K value - number of neighbors to consider
  weights: "uniform"    # Voting weight: "uniform" or "distance"
  algorithm: "auto"     # Neighbor search algorithm
  leaf_size: 30         # Tree algorithm parameter
  p: 2                  # Distance metric power (2 = Euclidean)
  metric: "minkowski"   # Distance metric
  n_jobs: -1            # Use all CPU cores

metric:
  weights:
    performance: 1
    cost: 0
    llm_judge: 0
```

**Testing Configuration** (`configs/model_config_test/knnrouter.yaml`):

```yaml
data_path:
  query_data_test: 'data/example_data/query_data/default_query_test.jsonl'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  load_model_path: 'saved_models/knnrouter/knnrouter.pkl'

# Same hyperparameters as training
hparam:
  n_neighbors: 5
  weights: "uniform"
  algorithm: "auto"
  metric: "minkowski"
  p: 2
```

## Choosing K (n_neighbors)

The value of K has significant impact on performance:

### Small K (1-3)
- **Advantages**: Captures local patterns, fast queries
- **Disadvantages**: Sensitive to noise and outliers
- **Use When**: Data is clean, need fine-grained distinctions

### Medium K (4-10) - **Recommended**
- **Advantages**: Balanced between noise robustness and sensitivity
- **Disadvantages**: May blur decision boundaries
- **Use When**: General-purpose routing, typical use case

### Large K (10-30)
- **Advantages**: Robust to noise, smooth decision boundaries
- **Disadvantages**: May miss local patterns, slower queries
- **Use When**: Noisy data, need stable predictions

### Rule of Thumb
- Start with K = sqrt(N) where N is number of training examples
- Use odd K to avoid ties (e.g., 3, 5, 7)
- Validate with cross-validation to find optimal K

## Distance Metric Selection

### Euclidean Distance (`metric="minkowski", p=2`)
- **Default choice**, works well for general embeddings
- Measures straight-line distance in embedding space
- Sensitive to feature scaling

### Manhattan Distance (`metric="minkowski", p=1`)
- Sum of absolute differences
- More robust to outliers than Euclidean
- Good for sparse data

### Cosine Distance (`metric="cosine"`)
- **Recommended for text embeddings**
- Measures angle between vectors, ignores magnitude
- Good when query length varies significantly

### Other Metrics
- `"chebyshev"`: Maximum difference across dimensions
- `"hamming"`: For binary features
- Custom metrics: Can define your own distance function

## Hyperparameter Tuning Tips

### Tuning n_neighbors (K)
```python
# Try multiple K values and use cross-validation
for k in [3, 5, 7, 10, 15]:
    # Evaluate routing accuracy with this K
    # Choose K with best validation performance
```

### Tuning weights
- Use `"uniform"` when all neighbors are equally trustworthy
- Use `"distance"` when closer examples should matter more
- `"distance"` often works better for KNN routing

### Tuning algorithm
- `"auto"` is usually fine
- For large datasets (>10k examples), try `"ball_tree"`
- For low-dimensional embeddings (<20 dims), try `"kd_tree"`

### Tuning metric
- For text embeddings: Try `"cosine"` first
- For normalized embeddings: `"euclidean"` works well
- Experiment and validate on held-out set

## Advantages

- ✅ **No Training Required**: Simply stores examples, no optimization needed
- ✅ **Incremental Learning**: Can add new examples instantly without retraining
- ✅ **Interpretable**: Can inspect which historical queries influenced each decision
- ✅ **Non-Parametric**: No assumptions about data distribution
- ✅ **Simple**: Easy to understand and implement
- ✅ **Effective with Small Data**: Works well even with few training examples
- ✅ **Adapts to Local Patterns**: Sensitive to local structure in data

## Limitations

- ❌ **Memory Intensive**: Stores all training examples in memory
- ❌ **Slow Inference**: Must compute distance to all examples (O(N) per query)
- ❌ **Curse of Dimensionality**: Performance degrades in very high dimensions
- ❌ **Sensitive to Irrelevant Features**: All embedding dimensions matter equally
- ❌ **No Feature Learning**: Cannot learn which features are important
- ❌ **Requires Good Embeddings**: Performance depends heavily on embedding quality

## When to Use KNN Router

**Good Use Cases:**
- Small to medium datasets (10s to 10,000s of examples)
- Need quick prototyping without training
- Want interpretable routing decisions
- Data distribution changes frequently (easy to update)
- Need to add new examples incrementally
- Limited computational resources for training

**Consider Alternatives When:**
- Very large datasets (>100k examples) → Use MLP or SVM Router
- High-dimensional embeddings with many irrelevant features → Use dimensionality reduction first
- Need fast inference at scale → Use trained models (MLP/SVM)
- Want to learn complex patterns → Use neural network-based routers
- Have imbalanced classes → Use weighted KNN or other methods

## Comparison with Other Routers

| Aspect | KNN Router | MLP Router | SVM Router |
|--------|------------|------------|------------|
| Training Time | None (instant) | Minutes to hours | Minutes |
| Inference Speed | Slow (O(N)) | Fast (O(1)) | Fast (O(#support_vectors)) |
| Memory Usage | High (all data) | Low (weights only) | Medium (support vectors) |
| Interpretability | High | Low | Medium |
| Handles Non-linearity | Yes (local) | Yes (layers) | Yes (kernels) |
| Incremental Learning | Easy | Difficult | Difficult |
| Small Data Performance | Excellent | Poor | Good |

## Implementation Details

- **Framework**: scikit-learn's `KNeighborsClassifier`
- **Embedding Model**: Longformer (for query vectorization)
- **Input Dimension**: Determined by embedding size (typically 768 or 1024)
- **Output**: Categorical prediction (LLM name as string)
- **Serialization**: Models saved as `.pkl` files using pickle
- **Search Optimization**: Uses Ball Tree or KD Tree for efficient neighbor search

## Tips for Best Performance

1. **Embedding Quality**:
   - Use domain-appropriate embeddings (Longformer for long texts)
   - Consider normalizing embeddings before KNN
   - Experiment with different embedding models

2. **Hyperparameter Selection**:
   - Start with K=5, weights="distance", metric="cosine"
   - Use cross-validation to find optimal K
   - Try different distance metrics for your data

3. **Data Preprocessing**:
   - Remove duplicate queries
   - Filter noisy or mislabeled examples
   - Balance class distribution if possible

4. **Scaling**:
   - For large datasets, use `algorithm="ball_tree"`
   - Set `n_jobs=-1` to use all CPU cores
   - Consider sampling if dataset is very large

5. **Incremental Updates**:
   - Easy to add new examples: just append to training data and re-save
   - No expensive retraining needed
   - Great for continual learning scenarios

## Advanced Usage

### Distance-Weighted Voting

```yaml
hparam:
  n_neighbors: 5
  weights: "distance"  # Closer neighbors have more influence
  metric: "cosine"     # Good for text embeddings
```

### Custom Distance Metrics

For specialized routing needs, you can define custom distance functions and use them with KNN Router by extending the scikit-learn interface.

### Handling Imbalanced Classes

If some LLMs appear much more frequently in training data:
- Use `weights="distance"` to give more influence to closer examples
- Consider stratified sampling to balance classes
- Use class-weighted voting (requires custom implementation)

## Related Routers

- **KNN Multi-Round Router**: Extends KNN for multi-turn conversations
- **MLP Router**: Parametric alternative with faster inference
- **SVM Router**: Kernel-based alternative with better scalability
- **Hybrid Router**: Combines KNN with other routing methods

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
