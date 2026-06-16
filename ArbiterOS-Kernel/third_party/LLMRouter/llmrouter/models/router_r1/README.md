# Router-R1 (Multi-round Routing)

## Overview

The **Router-R1** is an advanced agentic routing system that combines iterative reasoning with dynamic routing. Unlike traditional routers that make a single routing decision, Router-R1 uses a reasoning loop where the model generates search queries, retrieves information from specialized routing pools, and iteratively refines its answer through multiple reasoning steps.

## Paper Reference

This router implements the **Router-R1** approach:

- **[Router-R1: Teaching LLMs Multi-Round Routing and Aggregation via Reinforcement Learning](https://arxiv.org/abs/2506.09033)**
  - Zhang, H., Feng, T., & You, J. (2025). arXiv:2506.09033.
  - Proposes RL-based multi-round routing with reasoning and aggregation.

- **Key Idea**: Combine reasoning steps with routing to external knowledge sources through reinforcement learning.

Rather than selecting a single model upfront, Router-R1 treats routing as an iterative search process where the model can query routing pools multiple times during reasoning.

## How It Works

### Architecture

```
Query -> vLLM Generation -> <search>Query, LLM candidate</search> -> Routing Pool -> <information>Results</information>
         |                                                                        |
    Reasoning Loop <--------------------------------------------------------------+
         |
    <answer>Final Answer</answer>
```

### Routing Mechanism

1. **Initial Prompt**: Format the user query with reasoning instructions
2. **Iterative Reasoning Loop** (up to 5 iterations):
   - **Generate**: Model generates reasoning text and optionally a `<search>query</search>`
   - **Route**: If search tag found, query the routing pool API for information
   - **Augment**: Append retrieved information as `<information>results</information>`
   - **Continue**: Feed augmented text back to the model for next iteration
3. **Termination**: Loop ends when model outputs `<answer>final answer</answer>`
4. **Output**: Return the complete reasoning trace with final answer

### Routing Pool

The routing pool is an external API that provides:
- Model recommendations based on query content
- Relevant information retrieval
- Domain-specific knowledge

The model learns when and what to search through the routing pool during training.

### Token Tracking

Router-R1 tracks three types of tokens:
- **prompt_tokens**: vLLM input tokens across all iterations
- **completion_tokens**: vLLM output tokens across all iterations
- **route_tokens**: External routing API tokens

Total cost = prompt_tokens + completion_tokens + route_tokens

## Configuration Parameters

### Hyperparameters (`hparam` in config)

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `model_id` | str | Yes | HuggingFace model ID for vLLM (e.g., `"Qwen/Qwen2.5-7B-Instruct"`) |
| `api_base` | str | Yes* | Base URL for the routing pool API |
| `api_key` | str | Yes* | API key for accessing the routing pool |

*Can be set via environment variables instead of YAML config.

### Environment Variables

Instead of setting `api_base` and `api_key` in the YAML config, you can use the same environment variables as other routers:

| Config Key | Environment Variable |
|------------|---------------------|
| `api_key` | `API_KEYS` |
| `api_base` | `API_BASE` |

**`API_KEYS` Format** (same as other routers):
```bash
# JSON Array Format (recommended for multiple keys)
export API_KEYS='["your-key-1", "your-key-2", "your-key-3"]'

# Comma-Separated Format
export API_KEYS='key1,key2,key3'

# Single Key
export API_KEYS='your-api-key'
```

**Example:**
```bash
export API_KEYS='your-api-key'
export API_BASE='https://api.openai.com/v1'

# Now you can run without setting api_key/api_base in YAML
llmrouter infer --router router_r1 --config configs/model_config_test/router_r1.yaml \
    --query "Explain transformers"
```

### Dependencies

Router-R1 requires:
- **vLLM==0.6.3**: For efficient local LLM inference with GPU (requires torch==2.4.0)
- **CUDA**: GPU required (CPU not supported)
- **openai>=1.0**: For routing pool API calls (in `route_service.py`)

Install with:
```bash
pip install vllm==0.6.3 torch==2.4.0 openai
```

### Model Support

Supports any vLLM-compatible chat model:
- **Qwen**: `Qwen/Qwen2.5-7B-Instruct`, `Qwen/Qwen2.5-14B-Instruct`
- **Llama**: `meta-llama/Llama-3.1-8B-Instruct`, etc.

The router automatically detects the model family and applies the appropriate prompt template.

## CLI Usage

Router-R1 can be used via the `llmrouter` command-line interface:

### Inference

> **Note**: This router does not require training - it's a zero-shot agentic system.

```bash
# Route a single query with agentic reasoning
CUDA_VISIBLE_DEVICES=1 llmrouter infer --router router_r1 --config configs/model_config_test/router_r1.yaml \
    --query "Explain how transformers work in machine learning"

# Route queries from a file
CUDA_VISIBLE_DEVICES=1 llmrouter infer --router router_r1 --config configs/model_config_test/router_r1.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
CUDA_VISIBLE_DEVICES=1 llmrouter infer --router router_r1 --config configs/model_config_test/router_r1.yaml \
    --query "What is quantum computing?" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router router_r1 --config configs/model_config_test/router_r1.yaml

# Launch with custom port
llmrouter chat --router router_r1 --config configs/model_config_test/router_r1.yaml --port 8080

# Create a public shareable link
llmrouter chat --router router_r1 --config configs/model_config_test/router_r1.yaml --share
```

---

## Usage Examples

### Inference: Routing a Single Query

```python
from llmrouter.models import RouterR1

# Initialize router with configuration
router = RouterR1(yaml_path="configs/model_config_test/router_r1.yaml")

# Route a single query
query = {"query": "Explain how transformers work in machine learning"}
result = router.route_single(query, return_details=True)

print(f"Model Used: {result['model_name']}")
print(f"Response (with reasoning trace):\n{result['response']}")
print(f"Total Tokens: {result['total_tokens']}")
print(f"  - Prompt Tokens: {result['prompt_tokens']}")
print(f"  - Completion Tokens: {result['completion_tokens']}")
print(f"  - Route Tokens: {result['route_tokens']}")
```

### Inference: Batch Routing

```python
from llmrouter.models import RouterR1

# Initialize router
router = RouterR1(yaml_path="configs/model_config_test/router_r1.yaml")

# Prepare batch of queries
queries = [
    {"query": "What is quantum computing?", "ground_truth": "..."},
    {"query": "Solve the equation x^2 - 4 = 0", "ground_truth": "x = 2 or x = -2"}
]

# Route and execute queries
results = router.route_batch(batch=queries, task_name="general")

for result in results:
    print(f"Query: {result['query']}")
    print(f"Model: {result['model_name']}")
    print(f"Response:\n{result['response']}")
    print(f"Total Tokens: {result['input_token'] + result['output_token']}")
    print(f"Performance: {result.get('task_performance', 'N/A')}")
    print("-" * 80)
```

## YAML Configuration Example

**Testing Configuration** (`configs/model_config_test/router_r1.yaml`):

```yaml
data_path:
  query_data_test: 'data/example_data/query_data/default_query_test.jsonl'

hparam:
  model_id: "Qwen/Qwen2.5-7B-Instruct"
  api_base: "https://api.example.com/v1"  # Your routing pool API
  api_key: "${ROUTING_API_KEY}"            # Use environment variable
```

**Note**: Router-R1 does not require training - it's a zero-shot agentic system.

## Advantages

- ✅ **Agentic Reasoning**: Iteratively refines answers through reasoning loops
- ✅ **Dynamic Routing**: Can query routing pools multiple times per query
- ✅ **Explainable**: Full reasoning trace shows decision-making process
- ✅ **Flexible**: Works with any vLLM-compatible chat model
- ✅ **Token Tracking**: Comprehensive tracking of all token costs
- ✅ **No Training Required**: Zero-shot system, no training data needed

## Limitations

- ❌ **GPU Required**: vLLM requires CUDA, no CPU support
- ❌ **High Latency**: Multiple generation iterations increase response time
- ❌ **High Cost**: Multiple API calls (vLLM + routing pool) are expensive
- ❌ **Complexity**: Harder to debug and control than simple routers
- ❌ **Routing Pool Dependency**: Requires external routing pool API
- ❌ **Variable Behavior**: Non-deterministic reasoning may vary across runs
- ❌ **Resource Intensive**: Requires significant GPU memory (8GB+ VRAM)

## When to Use Router-R1

**Good Use Cases:**
- Complex queries requiring multi-step reasoning
- Need explainable routing decisions with full reasoning traces
- Have access to high-quality routing pool API
- GPU resources available (8GB+ VRAM)
- Quality is more important than latency/cost
- Research or advanced applications

**Consider Alternatives When:**
- Simple routing tasks -> Use KNN/SVM/MLP Router
- No GPU available -> Use API-based routers
- Cost/latency sensitive -> Use simpler routers
- No routing pool API -> Use other routing methods
- Need deterministic behavior -> Use trained classifiers

## Performance Tips

1. **Model Selection**:
   - Use 7B models for balance of quality and speed
   - Use 14B+ models only when highest quality needed
   - Qwen models often perform well for reasoning tasks

2. **API Configuration**:
   - Ensure routing pool API has low latency (<500ms)
   - Use environment variables for API keys (security)
   - Monitor routing pool costs separately

3. **Prompt Engineering**:
   - Customize prompts in `prompt_pool.py` for your domain
   - Adjust stop sequences if needed
   - Experiment with temperature (default: 1.0)

4. **Resource Management**:
   - RouterR1 auto-selects `tensor_parallel_size` based on visible GPUs and attention head divisibility
   - Override with `LLMROUTER_TENSOR_PARALLEL_SIZE=<int>` to force a fixed value
   - Monitor VRAM usage and adjust model size accordingly
   - Consider batching queries for better GPU utilization

5. **Token Optimization**:
   - Limit max iterations (currently capped at 5)
   - Use shorter max_tokens for faster generation
   - Monitor token costs (prompt + completion + route)

## Implementation Details

- **Framework**: vLLM for local inference, OpenAI SDK for routing pool
- **Model Types**: Supports Qwen, Llama, and other instruction-tuned models
- **Prompt Templates**: Automatic detection based on model family
- **Token Counting**: Tracks vLLM tokens and external API tokens separately
- **Max Iterations**: 5 iterations (configurable in code)
- **Stop Sequences**: `["</search>", "</answer>"]`

## Example Reasoning Trace

```
[Generation 0] Output:
To answer this question, I need to understand what transformers are.
<search>What are transformers in machine learning?</search>

<information>
Transformers are neural network architectures that use self-attention mechanisms...
</information>

[Generation 1] Output:
Based on the information, transformers work by:
1. Using self-attention to process sequences
2. Computing attention scores between all tokens
3. Using multi-head attention for different representations
<answer>
Transformers are neural network architectures that use self-attention mechanisms
to process sequential data. They compute relationships between all positions
in a sequence simultaneously, making them highly parallelizable...
</answer>
```

## Related Routers

- **LLM Multi-Round Router**: Similar multi-round approach but with decomposition
- **Causal LM Router**: Uses finetuned LLM but single-pass routing
- **Automix Router**: Self-verification based multi-step routing
- **KNN/SVM/MLP Routers**: Single-pass supervised routing

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
