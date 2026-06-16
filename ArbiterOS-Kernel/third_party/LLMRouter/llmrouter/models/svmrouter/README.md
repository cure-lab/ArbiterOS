# SVM Router

## Overview

The **SVM Router** (Support Vector Machine Router) is a supervised learning-based routing method that uses kernel-based classification to select the optimal LLM for each query. It leverages the power of SVMs to find decision boundaries in high-dimensional embedding spaces.

## Paper Reference

This router implements **Support Vector Machine (SVM)** classification for LLM routing, as described in:

- **[FusionFactory: Fusing LLM Capabilities with Multi-LLM Log Data](https://arxiv.org/abs/2507.10540)**
  - Feng, T., Zhang, H., Lei, Z., et al. (2025). arXiv:2507.10540.
  - Proposes query-level fusion via tailored LLM routers including SVM-based approaches.

- **Original SVM Papers**:
  - Cortes, C., & Vapnik, V. (1995). "Support-vector networks." Machine Learning.

- **Application**: SVMs are particularly effective for high-dimensional data and non-linearly separable classes, making them well-suited for routing based on query embeddings.

## How It Works

### Architecture

```
Query → Embedding → SVM Classifier → LLM Selection
                    (Kernel Mapping)
```

### Routing Mechanism

1. **Query Embedding**: Convert the input query into a fixed-size vector representation using Longformer embeddings
2. **Kernel Transformation**: Map the embedding into a higher-dimensional feature space using a kernel function (e.g., RBF)
3. **Decision Boundary**: Use the trained SVM to classify which LLM is optimal based on hyperplane separation
4. **Selection**: Return the LLM with the highest decision function score

### Training Process

1. **Data Preparation**:
   - Collect historical query-response pairs from different LLMs
   - Generate embeddings for each query
   - Label each query with the best-performing LLM

2. **Kernel Selection**:
   - Choose appropriate kernel function (RBF, polynomial, linear, or sigmoid)
   - RBF kernel is default and works well for most cases

3. **Model Training**:
   - Find optimal hyperplane that maximizes margin between classes
   - Use support vectors (critical training examples) to define decision boundaries
   - Regularization parameter C controls trade-off between margin width and classification errors

4. **Optimization**:
   - SVM optimization is convex, guaranteeing global optimum
   - Kernel trick allows learning complex non-linear boundaries efficiently

## Key Advantages of SVM

- **Kernel Trick**: Can handle non-linearly separable data by mapping to higher dimensions
- **Margin Maximization**: Finds robust decision boundaries with good generalization
- **Memory Efficient**: Only stores support vectors (subset of training data)
- **Effective in High Dimensions**: Works well even when number of features exceeds number of samples

## Configuration Parameters

### Training Hyperparameters (`hparam` in config)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `kernel` | str | `"rbf"` | Kernel function type. Options: `"linear"` (linear decision boundary), `"poly"` (polynomial), `"rbf"` (radial basis function, recommended), `"sigmoid"`. RBF is most versatile for non-linear problems. |
| `C` | float | `1.0` | Regularization parameter. Controls trade-off between smooth decision boundary and classifying training points correctly. Lower C → wider margin, more regularization. Higher C → narrower margin, less misclassification tolerance. Range: `0.01` to `100`. |
| `gamma` | str/float | `"scale"` | Kernel coefficient for RBF/poly/sigmoid. Defines influence radius of a single training example. Options: `"scale"` (default: 1/(n_features * X.var())), `"auto"` (1/n_features), or float value. Higher gamma → tighter decision boundary (risk of overfitting). |
| `probability` | bool | `true` | Enable probability estimates. If true, `predict_proba()` can be used to get confidence scores. Slightly slower training but provides uncertainty estimates. |
| `random_state` | int | - | Random seed for reproducibility (optional). |

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

During inference, the router:
- Loads the pre-trained SVM model from `load_model_path`
- Uses the same kernel and parameters as training
- Applies the decision function to predict the optimal LLM
- No hyperparameters are tuned during inference

## CLI Usage

The SVM Router can be used via the `llmrouter` command-line interface:

### Training

```bash
# Train the SVM router
llmrouter train --router svmrouter --config configs/model_config_train/svmrouter.yaml

# Train with quiet mode
llmrouter train --router svmrouter --config configs/model_config_train/svmrouter.yaml --quiet
```

### Inference

```bash
# Route a single query
llmrouter infer --router svmrouter --config configs/model_config_test/svmrouter.yaml \
    --query "Explain the theory of relativity"

# Route queries from a file
llmrouter infer --router svmrouter --config configs/model_config_test/svmrouter.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router svmrouter --config configs/model_config_test/svmrouter.yaml \
    --query "What is machine learning?" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router svmrouter --config configs/model_config_test/svmrouter.yaml

# Launch with custom port
llmrouter chat --router svmrouter --config configs/model_config_test/svmrouter.yaml --port 8080

# Create a public shareable link
llmrouter chat --router svmrouter --config configs/model_config_test/svmrouter.yaml --share
```

---

## Usage Examples

### Training the SVM Router

```python
from llmrouter.models import SVMRouter, SVMRouterTrainer

# Initialize router with training configuration
router = SVMRouter(yaml_path="configs/model_config_train/svmrouter.yaml")

# Create trainer
trainer = SVMRouterTrainer(router=router, device="cpu")

# Train the model
trainer.train()
# Model will be saved to the path specified in save_model_path
```

**Command Line Training:**
```bash
python tests/train_test/test_svmrouter.py --yaml_path configs/model_config_train/svmrouter.yaml
```

### Inference: Routing a Single Query

```python
from llmrouter.models import SVMRouter

# Initialize router with test configuration (loads trained model)
router = SVMRouter(yaml_path="configs/model_config_test/svmrouter.yaml")

# Route a single query
query = {"query": "Explain the theory of relativity"}
result = router.route_single(query)

print(f"Selected Model: {result['model_name']}")
```

### Inference: Batch Routing with API Execution

```python
from llmrouter.models import SVMRouter

# Initialize router
router = SVMRouter(yaml_path="configs/model_config_test/svmrouter.yaml")

# Prepare batch of queries
queries = [
    {"query": "What is machine learning?", "ground_truth": "..."},
    {"query": "Write a sorting algorithm in Python", "ground_truth": "..."}
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

### Using with Specific Tasks

```python
# Route queries for benchmark tasks (e.g., MMLU)
queries = [
    {
        "query": "Which of the following is a prime number?",
        "choices": ["A. 4", "B. 6", "C. 7", "D. 8"],
        "ground_truth": "C"
    }
]

# Task name triggers automatic prompt formatting
results = router.route_batch(batch=queries, task_name="mmlu")
```

## YAML Configuration Example

**Training Configuration** (`configs/model_config_train/svmrouter.yaml`):

```yaml
data_path:
  query_data_train: 'data/example_data/query_data/default_query_train.jsonl'
  routing_data_train: 'data/example_data/routing_data/default_routing_train_data.jsonl'
  query_embedding_data: 'data/example_data/routing_data/query_embeddings_longformer.pt'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  ini_model_path: ''  # Leave empty to train from scratch
  save_model_path: 'saved_models/svmrouter/svmrouter.pkl'

hparam:
  kernel: "rbf"          # Radial basis function kernel
  C: 1.0                 # Regularization strength
  gamma: "scale"         # Automatic gamma calculation
  probability: true      # Enable probability estimates

metric:
  weights:
    performance: 1    # Weight for task performance
    cost: 0           # Weight for cost (tokens)
    llm_judge: 0      # Weight for LLM-as-judge scores
```

**Testing Configuration** (`configs/model_config_test/svmrouter.yaml`):

```yaml
data_path:
  query_data_test: 'data/example_data/query_data/default_query_test.jsonl'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  load_model_path: 'saved_models/svmrouter/svmrouter.pkl'

# Note: hparam section can be included for reference but is not used during inference
```

## Kernel Selection Guide

### Linear Kernel (`kernel="linear"`)
- **Use When**: Classes are linearly separable, high-dimensional data
- **Advantages**: Fast training, interpretable, no gamma tuning needed
- **Disadvantages**: Cannot handle non-linear patterns

### RBF Kernel (`kernel="rbf"`) - **Recommended**
- **Use When**: Unknown data distribution, general-purpose routing
- **Advantages**: Handles non-linear patterns, most versatile
- **Disadvantages**: Requires tuning gamma parameter

### Polynomial Kernel (`kernel="poly"`)
- **Use When**: Data has polynomial relationships
- **Advantages**: Can model complex interactions
- **Disadvantages**: More parameters to tune (degree, gamma, coef0)

### Sigmoid Kernel (`kernel="sigmoid"`)
- **Use When**: Neural network-like decision boundaries desired
- **Advantages**: Similar to neural network activation
- **Disadvantages**: May not converge well, less commonly used

## Hyperparameter Tuning Tips

### C Parameter (Regularization)
```
Low C (0.01-0.1):   Wide margin, more generalization, tolerates errors
Medium C (1.0):      Balanced (default)
High C (10-100):     Narrow margin, fits training data closely
```

### Gamma Parameter (RBF Kernel)
```
Low gamma (0.001):   Smooth decision boundary, far-reaching influence
Medium gamma:        Balanced (use "scale" or "auto")
High gamma (1-10):   Complex boundary, local influence (risk of overfitting)
```

### Recommended Tuning Strategy
1. Start with default values: `kernel="rbf"`, `C=1.0`, `gamma="scale"`
2. If underfitting: Increase C, increase gamma
3. If overfitting: Decrease C, decrease gamma, or use linear kernel
4. Use cross-validation to find optimal values

## Advantages

- ✅ **Strong Theoretical Foundation**: Margin maximization with provable generalization bounds
- ✅ **Kernel Flexibility**: Can model complex non-linear decision boundaries
- ✅ **Memory Efficient**: Only stores support vectors (typically small subset of data)
- ✅ **Robust to Outliers**: Regularization prevents overfitting to noise
- ✅ **Effective in High Dimensions**: Works well when features >> samples
- ✅ **Probability Estimates**: Can provide confidence scores for routing decisions

## Limitations

- ❌ **Slow on Large Datasets**: Training time scales poorly (O(n²) to O(n³))
- ❌ **Kernel Selection**: Performance depends heavily on choosing right kernel and parameters
- ❌ **Binary-Focused**: Originally designed for binary classification (extended to multi-class)
- ❌ **No Incremental Learning**: Must retrain from scratch with new data
- ❌ **Requires Training Data**: Needs labeled historical performance data
- ❌ **Black Box Kernels**: Non-linear kernels can be hard to interpret

## When to Use SVM Router

**Good Use Cases:**
- Medium-sized datasets (100s to 10,000s of samples)
- Non-linear routing patterns that simple models can't capture
- Need theoretical guarantees about generalization
- Want probability estimates for routing confidence
- Have well-balanced class distribution (similar number of examples per LLM)

**Consider Alternatives When:**
- Very large datasets (>100k samples) → Use MLP Router or ensemble methods
- Real-time training updates needed → Use online learning methods
- Highly imbalanced data → Use weighted SVM or other techniques
- Simple linear patterns → Use logistic regression or linear SVM
- Limited training data → Use KNN Router or heuristic methods

## Comparison with MLP Router

| Aspect | SVM Router | MLP Router |
|--------|------------|------------|
| Training Speed | Slower on large data | Faster with mini-batches |
| Inference Speed | Fast | Fast |
| Non-linear Modeling | Kernel trick | Multiple hidden layers |
| Memory Usage | Support vectors only | All weights stored |
| Hyperparameter Tuning | Kernel + C + gamma | Layers + activation + learning rate |
| Theoretical Guarantees | Strong (margin theory) | Weaker |
| Scalability | Poor (O(n²-n³)) | Better (O(n)) |

## Implementation Details

- **Framework**: scikit-learn's `SVC` (Support Vector Classification)
- **Embedding Model**: Longformer (for query vectorization)
- **Input Dimension**: Determined by embedding size (typically 768 or 1024)
- **Output**: Categorical prediction (LLM name as string)
- **Serialization**: Models saved as `.pkl` files using pickle
- **Multi-class Strategy**: One-vs-One or One-vs-Rest (automatic in scikit-learn)

## Tips for Best Performance

1. **Data Preprocessing**:
   - Ensure embeddings are normalized (SVM sensitive to feature scales)
   - Remove duplicate or very similar queries
   - Balance class distribution if possible

2. **Hyperparameter Selection**:
   - Start with RBF kernel and default C=1.0, gamma="scale"
   - Use grid search or random search for tuning
   - Validate on held-out test set

3. **Kernel Choice**:
   - Try linear kernel first (fast, interpretable)
   - Use RBF if linear doesn't work well
   - Consider polynomial for specific domains

4. **Performance Optimization**:
   - For large datasets, use `kernel="linear"` or sample subset
   - Enable probability=true only if needed (adds overhead)
   - Consider approximate methods for very large datasets

5. **Regular Retraining**:
   - Retrain periodically with new data
   - Monitor performance on validation set
   - Update embeddings if LLM capabilities change

## Related Routers

- **MLP Router**: Neural network-based alternative, better for very large datasets
- **KNN Router**: Instance-based, no training needed, good for small datasets
- **Linear Router**: Simplified version using logistic regression
- **Ensemble Router**: Combines SVM with other routing methods

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
