# Automix Router

## Overview

The **Automix Router** is a cost-effective routing method that uses self-verification to decide when to escalate queries from a small, inexpensive language model to a larger, more capable (and expensive) model. It employs self-consistency verification to assess the small model's confidence before deciding whether escalation is necessary.

## Paper Reference

This router implements the **Automix** framework from:

- **[AutoMix: Automatically Mixing Language Models](https://arxiv.org/abs/2310.12963)**
  - Aggarwal, P., Madaan, A., et al. (2023). arXiv:2310.12963. Published at NeurIPS 2024.
  - Proposes self-verification and POMDP-based routing for cost-effective LLM selection.

The Automix approach is based on the observation that many queries can be handled effectively by smaller models, and expensive large models should only be used when necessary.

## How It Works

### Architecture

```
Query → Small Model → Self-Verification → Decision → [Keep or Escalate to Large Model]
                      (Confidence Score)    (POMDP/Threshold/SelfConsistency)
```

### Routing Mechanism

1. **Initial Response**: The query is first sent to a small, cost-effective LLM
2. **Self-Verification**: The small model generates multiple verification samples to assess its own answer confidence
3. **Confidence Scoring**: A verification score is computed based on the consistency of verification samples
4. **Routing Decision**: Based on the verification score, the router decides:
   - **High confidence** → Use the small model's answer (cost-efficient)
   - **Low confidence** → Escalate to large model (quality-focused)
5. **Final Response**: Return either the small model's answer or the large model's answer

### Routing Methods

Automix supports three routing strategies:

#### 1. Threshold Method
- **Decision Rule**: Route to large model if `p_ver_slm < threshold`
- **Characteristics**: Simple, deterministic
- **Best for**: Clear confidence boundaries

#### 2. POMDP (Partially Observable Markov Decision Process)
- **Decision Rule**: Optimize expected reward using dynamic programming
- **Characteristics**: Theoretically optimal under cost constraints
- **Best for**: Balancing quality and cost with formal guarantees

#### 3. Self-Consistency
- **Decision Rule**: Route based on answer entropy across multiple samples
- **Characteristics**: Measures diversity of responses
- **Best for**: Detecting when the model is uncertain

### Self-Verification Process

The verification process works as follows:

1. Generate answer with small model: `answer_small`
2. Ask small model to verify its own answer (N times with sampling)
3. Parse verification responses (e.g., "True" or "False")
4. Compute verification score: `p_ver = fraction_of_positive_verifications`
5. Use `p_ver` as confidence signal for routing decision

## Configuration Parameters

### Training Hyperparameters (`hparam` in config)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `routing_method` | str | `"POMDP"` | Routing strategy. Options: `"Threshold"`, `"POMDP"`, `"SelfConsistency"`. POMDP is recommended for optimal cost-performance trade-off. |
| `num_bins` | int | `8` | Number of bins for discretizing verification scores (used by POMDP). Higher values increase precision but slow down optimization. |
| `small_model_cost` | float | `1` | Relative cost of calling the small model. Used for cost-benefit analysis. |
| `large_model_cost` | float | `50` | Relative cost of calling the large model. Typically 10-100x the small model cost. |
| `verifier_cost` | float | `1` | Cost of running verification (usually similar to small model cost). |
| `verbose` | bool | `true` | Whether to print detailed output during training/inference. |
| `cost_constraint` | tuple/null | `null` | Optional (min_cost, max_cost) tuple to constrain routing decisions. |
| `max_workers` | int | `1` | Number of parallel workers for API calls. Increase for faster processing (5-10 recommended). |
| `device` | str | `"cpu"` | Device for computation: `"cpu"` or `"cuda"`. |

### Data Paths

| Parameter | Description |
|-----------|-------------|
| `routing_data_train` | Training data with query-LLM performance pairs (JSONL format) |
| `routing_data_test` | Test data for evaluation |
| `llm_data` | LLM candidate information with model sizes (JSON) |

**Note**: Automix automatically detects the smallest and largest models from `llm_data` based on the `size` field.

### Model Paths

| Parameter | Purpose | Usage |
|-----------|---------|-------|
| `save_model_path` | Where to save the trained routing policy | Training: saves the learned POMDP policy or threshold |
| `load_model_path` | Model to load for inference | Testing: loads the trained routing policy |

### Inference Parameters

During inference:
- Calls small model first for every query
- Performs self-verification to get confidence score
- Applies learned routing policy to decide escalation
- Calls large model if needed
- No hyperparameters are tuned

## CLI Usage

The Automix Router can be used via the `llmrouter` command-line interface:

### Training

```bash
# Train the Automix router (learns optimal routing policy)
llmrouter train --router automix --config configs/model_config_train/automix.yaml

# Train with quiet mode
llmrouter train --router automix --config configs/model_config_train/automix.yaml --quiet
```

### Inference

```bash
# Route a single query (uses self-verification + POMDP policy)
llmrouter infer --router automix --config configs/model_config_test/automix.yaml \
    --query "Solve x^2 + 5x + 6 = 0"

# Route queries from a file
llmrouter infer --router automix --config configs/model_config_test/automix.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router automix --config configs/model_config_test/automix.yaml \
    --query "Explain quantum entanglement" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router automix --config configs/model_config_test/automix.yaml

# Launch with custom port
llmrouter chat --router automix --config configs/model_config_test/automix.yaml --port 8080

# Create a public shareable link
llmrouter chat --router automix --config configs/model_config_test/automix.yaml --share
```

---

## Usage Examples

### Training the Automix Router

```python
from llmrouter.models import AutomixRouter, AutomixTrainer

# Initialize router with training configuration
router = AutomixRouter(yaml_path="configs/model_config_train/automix.yaml")

# Create trainer
trainer = AutomixTrainer(router=router, device="cpu")

# Train the routing policy (learns optimal threshold or POMDP policy)
trainer.train()
# Policy will be saved to the path specified in save_model_path
```

**Command Line Training:**
```bash
python tests/train_test/test_automix.py --yaml_path configs/model_config_train/automix.yaml
```

### Inference: Routing a Single Query

```python
from llmrouter.models import AutomixRouter

# Initialize router with test configuration (loads trained policy)
router = AutomixRouter(yaml_path="configs/model_config_test/automix.yaml")

# Route a single query
query = {"query": "What is the capital of France?"}
result = router.route_single(query)

print(f"Model Used: {result['model_name']}")
print(f"Response: {result['response']}")
print(f"Verification Score: {result['verification_score']}")
print(f"Routed to Large Model: {result['route_to_llm']}")
```

### Inference: Batch Routing with API Execution

```python
from llmrouter.models import AutomixRouter

# Initialize router
router = AutomixRouter(yaml_path="configs/model_config_test/automix.yaml")

# Prepare batch of queries
queries = [
    {"query": "Solve x^2 + 5x + 6 = 0", "ground_truth": "x = -2 or x = -3"},
    {"query": "Explain quantum entanglement", "ground_truth": "..."},
    {"query": "Write a Python function to reverse a string", "ground_truth": "..."}
]

# Route and execute queries
results = router.route_batch(batch=queries, task_name="general")

for result in results:
    print(f"Query: {result['query']}")
    print(f"Model Used: {result['model_name']}")
    print(f"Response: {result['response']}")
    print(f"Verification Score: {result['verification_score']}")
    print(f"Escalated: {result['route_to_llm']}")
    print(f"Performance: {result.get('task_performance', 'N/A')}")
    print("-" * 80)
```

## YAML Configuration Example

**Training Configuration** (`configs/model_config_train/automix.yaml`):

```yaml
data_path:
  routing_data_train: 'data/example_data/routing_data/default_routing_train_data.jsonl'
  routing_data_test: 'data/example_data/routing_data/default_routing_test_data.jsonl'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  save_model_path: 'saved_models/automix/automix_model.pkl'

hparam:
  routing_method: "POMDP"      # Recommended for optimal performance
  num_bins: 8                  # Discretization granularity
  small_model_cost: 1          # Cost of small model
  large_model_cost: 50         # Cost of large model (50x more expensive)
  verifier_cost: 1             # Cost of verification
  verbose: true
  max_workers: 5               # Parallel API calls for speed

metric:
  weights:
    performance: 1
    cost: 0
    llm_judge: 0
```

**Testing Configuration** (`configs/model_config_test/automix.yaml`):

```yaml
data_path:
  query_data_test: 'data/example_data/query_data/default_query_test.jsonl'
  llm_data: 'data/example_data/llm_candidates/default_llm.json'

model_path:
  load_model_path: 'saved_models/automix/automix_model.pkl'

hparam:
  routing_method: "POMDP"
  num_bins: 8
  small_model_cost: 1
  large_model_cost: 50
  verifier_cost: 1
  max_workers: 5
```

## Advantages

- ✅ **Cost-Efficient**: Minimizes expensive large model API calls by using small models when possible
- ✅ **Quality-Aware**: Maintains high quality by escalating uncertain queries to large models
- ✅ **Self-Verification**: Uses the model's own confidence without external classifiers
- ✅ **Theoretically Grounded**: POMDP method provides optimal routing under cost constraints
- ✅ **Automatic Model Selection**: Auto-detects small and large models from configuration
- ✅ **Flexible Methods**: Three routing strategies for different use cases

## Limitations

- ❌ **Two-Model Constraint**: Only routes between exactly 2 models (smallest and largest)
- ❌ **Verification Overhead**: Self-verification requires additional API calls (2+ samples)
- ❌ **Binary Decision**: Either uses small or large model, no intermediate options
- ❌ **Assumes Self-Awareness**: Relies on models accurately assessing their own confidence
- ❌ **Cold Start**: Requires training data with performance metrics for both models
- ❌ **API Dependency**: Needs access to both small and large models via API

## When to Use Automix Router

**Good Use Cases:**
- Cost-sensitive applications where LLM API costs are a concern
- Mixed-difficulty workloads (some queries easy, some hard)
- You have access to both a small and large model via API
- Need to balance quality and cost dynamically
- Want a principled approach to model selection

**Consider Alternatives When:**
- Need to route among 3+ models → Use MLP/SVM/KNN Router
- All queries have similar difficulty → Use single model
- Cannot afford verification overhead → Use ELO Router or static selection
- No access to small model → Use Largest LLM Router
- Need maximum quality regardless of cost → Use Largest LLM Router

## Performance Tips

1. **Model Selection**:
   - Choose small and large models with clear capability gap (e.g., 3B vs 70B)
   - Ensure small model is 10-100x cheaper than large model
   - Verify models are accessible via the same API

2. **Routing Method Selection**:
   - **POMDP**: Best for optimal cost-performance balance
   - **Threshold**: Best for simple, interpretable routing
   - **SelfConsistency**: Best when verification is unreliable

3. **Hyperparameter Tuning**:
   - Adjust `small_model_cost` and `large_model_cost` to reflect actual API costs
   - Increase `num_bins` (to 16-32) for more precise POMDP optimization
   - Tune `verifier_cost` if verification uses different settings

4. **Data Preparation**:
   - Ensure training data has diverse query difficulties
   - Include ground truth for accurate performance evaluation
   - Pre-compute query embeddings if using multiple routing methods

5. **Optimization**:
   - Increase `max_workers` (5-10) for faster parallel API calls
   - Use `cost_constraint` to enforce budget limits
   - Monitor routing percentage (aim for 20-40% escalation for good balance)

## Implementation Details

- **Framework**: Custom implementation based on Automix paper
- **Routing Methods**: Threshold, POMDP (dynamic programming), SelfConsistency
- **Verification**: Self-consistency sampling (n=2 by default)
- **Model Detection**: Automatic based on `size` field in `llm_data`
- **Serialization**: Models saved as `.pkl` files using pickle

## Related Routers

- **Hybrid LLM Router**: Similar cost-quality trade-off but uses learned MLP predictor
- **Smallest LLM Router**: Always uses smallest model (maximizes cost savings)
- **Largest LLM Router**: Always uses largest model (maximizes quality)
- **MLP/SVM/KNN Routers**: Route among multiple models (not just 2)

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
