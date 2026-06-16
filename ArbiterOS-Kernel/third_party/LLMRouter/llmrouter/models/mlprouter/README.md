# MLP Router

## Overview

The **MLP Router** (Multi-Layer Perceptron Router) is a supervised learning-based routing method that uses a neural network classifier to predict the most suitable LLM for a given query based on learned patterns from training data.

## Paper Reference

This router implements a **Multi-Layer Perceptron (MLP)** approach for LLM routing, as described in:

- **[FusionFactory: Fusing LLM Capabilities with Multi-LLM Log Data](https://arxiv.org/abs/2507.10540)**
  - Feng, T., Zhang, H., Lei, Z., et al. (2025). arXiv:2507.10540.
  - Proposes query-level fusion via tailored LLM routers including MLP-based approaches.

- **Foundation**: Rumelhart, D. E., Hinton, G. E., & Williams, R. J. (1986). "Learning representations by back-propagating errors." Nature.

The MLP Router treats LLM selection as a multi-class classification problem where the goal is to predict the best-performing model for each input query.

## How It Works

### Architecture

```
Query → Embedding → MLP Classifier → LLM Selection
                    (Hidden Layers)
```

### Routing Mechanism

1. **Query Embedding**: Each input query is converted into a fixed-size vector representation using Longformer embeddings
2. **Feature Learning**: The MLP learns non-linear patterns in the embedding space during training
3. **Classification**: The trained network predicts which LLM is most likely to perform best for the query
4. **Selection**: The router selects the LLM with the highest predicted probability

### Training Process

1. **Data Preparation**:
   - Collect historical query-response pairs from different LLMs
   - Generate embeddings for each query
   - Label each query with the best-performing LLM (based on performance metrics)

2. **Model Training**:
   - Feed query embeddings as input features
   - Use LLM names as target labels
   - Train the MLP classifier using backpropagation and gradient descent

3. **Optimization**:
   - The model learns to map query embeddings to optimal LLM selections
   - Regularization prevents overfitting to training data

## Configuration Parameters

### Training Hyperparameters (`hparam` in config)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `hidden_layer_sizes` | list[int] | `[128, 64]` | Number of neurons in each hidden layer. Larger values increase model capacity but may cause overfitting. Example: `[128, 64]` creates a 2-layer network with 128 and 64 neurons. |
| `activation` | str | `"relu"` | Activation function for hidden layers. Options: `"identity"`, `"logistic"`, `"tanh"`, `"relu"`. ReLU is recommended for most cases. |
| `solver` | str | `"adam"` | Optimization algorithm. Options: `"lbfgs"` (good for small datasets), `"adam"` (robust for large datasets), `"sgd"` (with momentum). |
| `alpha` | float | `0.0001` | L2 regularization parameter. Higher values prevent overfitting but may reduce model capacity. Range: `0.0001` to `0.01`. |
| `learning_rate` | str | `"adaptive"` | Learning rate schedule. Options: `"constant"`, `"invscaling"`, `"adaptive"` (automatically adjusts based on training progress). |
| `learning_rate_init` | float | `0.001` | Initial learning rate for weight updates. Typical range: `0.0001` to `0.01`. |
| `max_iter` | int | `500` | Maximum number of training iterations/epochs. Increase if model hasn't converged. |
| `random_state` | int | `42` | Random seed for reproducibility. Set to any integer for consistent results across runs. |

### Data Paths

| Parameter | Description |
|-----------|-------------|
| `query_data_train` | Training queries in JSONL format |
| `routing_data_train` | Historical routing performance data (query-LLM pairs with performance scores) |
| `query_embedding_data` | Pre-computed query embeddings (PyTorch tensor file) |
| `llm_data` | LLM candidate information (models, API names, metadata) |

### Model Paths

| Parameter | Purpose | Usage |
|-----------|---------|-------|
| `ini_model_path` | Pre-trained model to initialize from | Optional: leave empty to train from scratch |
| `save_model_path` | Where to save the trained model | Training: model saved here after training completes |
| `load_model_path` | Model to load for inference | Testing: path to trained `.pkl` file |

### Inference/Testing Parameters

During inference, the router uses:
- **Loaded Model**: Pre-trained MLP classifier from `load_model_path`
- **API Configuration**: Endpoint and model mappings from `llm_data`
- **Task Formatting**: Optional `task_name` parameter to format queries for specific benchmarks

No hyperparameters are tuned during inference — all decisions are made by the trained model.

## CLI Usage

The MLP Router can be used via the `llmrouter` command-line interface:

### Training

```bash
# Train the MLP router
llmrouter train --router mlprouter --config configs/model_config_train/mlprouter.yaml

# Train with GPU acceleration
llmrouter train --router mlprouter --config configs/model_config_train/mlprouter.yaml --device cuda

# Train with quiet mode (less verbose output)
llmrouter train --router mlprouter --config configs/model_config_train/mlprouter.yaml --quiet
```

### Inference

```bash
# Route a single query
llmrouter infer --router mlprouter --config configs/model_config_test/mlprouter.yaml \
    --query "What is machine learning?"

# Route queries from a file
llmrouter infer --router mlprouter --config configs/model_config_test/mlprouter.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router mlprouter --config configs/model_config_test/mlprouter.yaml \
    --query "Explain neural networks" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router mlprouter --config configs/model_config_test/mlprouter.yaml

# Launch with custom port
llmrouter chat --router mlprouter --config configs/model_config_test/mlprouter.yaml --port 8080

# Create a public shareable link
llmrouter chat --router mlprouter --config configs/model_config_test/mlprouter.yaml --share
```

---

## Usage Examples

### Training the MLP Router

```python
from llmrouter.models import MLPRouter, MLPTrainer

# Initialize router with training configuration
router = MLPRouter(yaml_path="configs/model_config_train/mlprouter.yaml")

# Create trainer
trainer = MLPTrainer(router=router, device="cpu")

# Train the model
trainer.train()
# Model will be saved to the path specified in save_model_path
```

**Command Line Training:**
```bash
python tests/train_test/test_mlprouter.py --yaml_path configs/model_config_train/mlprouter.yaml
```

### Inference: Routing a Single Query

```python
from llmrouter.models import MLPRouter

# Initialize router with test configuration (loads trained model)
router = MLPRouter(yaml_path="configs/model_config_test/mlprouter.yaml")

# Route a single query
query = {"query": "What is the capital of France?"}
result = router.route_single(query)

print(f"Selected Model: {result['model_name']}")
```

### Inference: Batch Routing with API Execution

```python
from llmrouter.models import MLPRouter

# Initialize router
router = MLPRouter(yaml_path="configs/model_config_test/mlprouter.yaml")

# Prepare batch of queries
queries = [
    {"query": "Explain quantum computing", "ground_truth": "..."},
    {"query": "Write a Python function to sort a list", "ground_truth": "..."}
]

# Route and execute queries (includes API calls and performance evaluation)
results = router.route_batch(batch=queries, task_name="general")

for result in results:
    print(f"Query: {result['query']}")
    print(f"Routed to: {result['model_name']}")
    print(f"Response: {result['response']}")
    print(f"Performance: {result.get('task_performance', 'N/A')}")
    print("-" * 80)
```

### Using with Specific Tasks (e.g., MMLU, GSM8K)

```python
# Route queries for a specific benchmark task
queries = [
    {
        "query": "Question text here",
        "choices": ["A", "B", "C", "D"],
        "ground_truth": "A"
    }
]

# Task name triggers automatic prompt formatting
results = router.route_batch(batch=queries, task_name="mmlu")
```

### Command Line Testing

```bash
python tests/inference_test/test_mlprouter.py --yaml_path configs/model_config_test/mlprouter.yaml
```

## YAML Configuration Example

**Training Configuration** (`configs/model_config_train/mlprouter.yaml`):

```yaml
data_path:
  query_data_train: 'data/example_data/query_data/default_query_train.jsonl'
  routing_data_train: 'data/example_data/routing_data/default_routing_train_data.jsonl'
  query_embedding_data: 'data/example_data/routing_data/query_embeddings_longformer.pt'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  ini_model_path: ''  # Leave empty to train from scratch
  save_model_path: 'saved_models/mlprouter/mlprouter.pkl'

hparam:
  hidden_layer_sizes: [128, 64]
  activation: "relu"
  solver: "adam"
  alpha: 0.0001
  learning_rate: "adaptive"
  learning_rate_init: 0.001
  max_iter: 500
  random_state: 42

metric:
  weights:
    performance: 1    # Weight for task performance
    cost: 0           # Weight for cost (tokens)
    llm_judge: 0      # Weight for LLM-as-judge scores
```

**Testing Configuration** (`configs/model_config_test/mlprouter.yaml`):

```yaml
data_path:
  query_data_test: 'data/example_data/query_data/default_query_test.jsonl'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  load_model_path: 'saved_models/mlprouter/mlprouter.pkl'

# Note: hparam not needed for inference, but can be included for reference
```

## Advantages

- ✅ **Fast Inference**: Once trained, routing is extremely fast (single forward pass)
- ✅ **Scalable**: Handles large numbers of LLM candidates efficiently
- ✅ **Flexible**: Hyperparameters can be tuned for different dataset sizes
- ✅ **Interpretable**: Can analyze which features (embedding dimensions) influence routing decisions

## Limitations

- ❌ **Requires Training Data**: Needs historical performance data for supervised learning
- ❌ **Feature Dependency**: Performance depends heavily on the quality of query embeddings
- ❌ **Generalization**: May not generalize well to queries very different from training data
- ❌ **Binary Decision**: Selects only one LLM (no ensemble or fallback mechanisms)

## When to Use MLP Router

**Good Use Cases:**
- You have sufficient historical query-response data with performance labels
- Query distribution is relatively stable and predictable
- Need fast, low-latency routing decisions
- Want a simple, interpretable baseline for LLM routing

**Consider Alternatives When:**
- Limited training data available → Use KNN Router or heuristic methods
- Queries are highly diverse or out-of-distribution → Use LLM-based routers
- Need multi-model ensembles → Use hybrid approaches
- Require dynamic adaptation → Use online learning methods

## Tips for Best Performance

1. **Data Quality**: Ensure training data covers diverse query types and has accurate performance labels
2. **Hyperparameter Tuning**:
   - Start with default values
   - Increase `hidden_layer_sizes` if underfitting
   - Increase `alpha` if overfitting
   - Use `solver="lbfgs"` for small datasets (<1000 samples)
3. **Embedding Quality**: Use domain-appropriate embeddings (Longformer for long texts)
4. **Regular Retraining**: Periodically retrain with new data to adapt to changing query distributions
5. **Validation**: Always validate on held-out test set to check generalization

## Implementation Details

- **Framework**: scikit-learn's `MLPClassifier`
- **Embedding Model**: Longformer (for query vectorization)
- **Input Dimension**: Determined by embedding size (typically 768 or 1024)
- **Output**: Categorical prediction (LLM name as string)
- **Serialization**: Models saved as `.pkl` files using pickle

## Related Routers

- **KNN Router**: Instance-based learning alternative (no training needed)
- **SVM Router**: Another supervised learning approach with kernel tricks
- **LLM Router**: Uses language models for routing decisions
- **Graph Router**: Leverages graph neural networks for structured routing

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
