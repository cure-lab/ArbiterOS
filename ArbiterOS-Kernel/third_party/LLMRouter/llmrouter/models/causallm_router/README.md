# Causal LM Router

## Overview

The **Causal LM Router** uses a finetuned causal language model to predict the best LLM for each query. Unlike traditional classifiers, it frames routing as a text generation task where the model generates the optimal LLM name based on the query content.

## Paper Reference

This router is inspired by **RouteLLM** and LLM-based classification:

- **[RouteLLM: Learning to Route LLMs with Preference Data](https://arxiv.org/abs/2406.18665)**
  - Ong, I., et al. (2024). arXiv:2406.18665. Published at ICLR 2025.
  - Implements `causal_llm` router using LLM-based classifier tuned on preference data.

- **LoRA**: Hu, E. J., et al. (2021). "LoRA: Low-Rank Adaptation of Large Language Models." ICLR.
- **Key Idea**: Treat routing as conditional text generation.

## How It Works

### Architecture

```
Query → Prompt Template → Finetuned LLM (vLLM) → Generated LLM Name → Parsing
```

### Routing Mechanism

1. **Training Phase**:
   - Build prompts: "Query: {query}\n\nBest LLM: {best_llm_name}"
   - Finetune base model (e.g., Llama-2-7B) using LoRA
   - Model learns to predict optimal LLM name from query

2. **Inference Phase**:
   - Format query into routing prompt
   - Generate LLM name using finetuned model with vLLM
   - Parse generated text to extract LLM name
   - Route query to the predicted LLM

## Configuration Parameters

### Training Hyperparameters (`hparam`)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `base_model` | str | `"meta-llama/Llama-2-7b-hf"` | Base LLM for finetuning |
| `use_lora` | bool | `true` | Whether to use LoRA for efficient finetuning |
| `lora_r` | int | `16` | LoRA rank (lower = fewer parameters) |
| `lora_alpha` | int | `32` | LoRA scaling factor |
| `lora_dropout` | float | `0.1` | Dropout for LoRA layers |
| `num_epochs` | int | `3` | Training epochs |
| `batch_size` | int | `4` | Per-device batch size |
| `learning_rate` | float | `0.00002` | Learning rate |
| `max_length` | int | `512` | Max sequence length |
| `merge_lora` | bool | `true` | Merge LoRA weights after training |

### Inference Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `tensor_parallel_size` | `1` | Number of GPUs for tensor parallelism |
| `max_new_tokens` | `32` | Max tokens to generate |
| `temperature` | `0.1` | Sampling temperature (low for deterministic) |

## CLI Usage

The Causal LM Router can be used via the `llmrouter` command-line interface:

### Training

```bash
# Train the Causal LM router (requires GPU)
llmrouter train --router causallmrouter --config configs/model_config_train/causallm_router.yaml --device cuda

# Train with quiet mode
llmrouter train --router causallmrouter --config configs/model_config_train/causallm_router.yaml --device cuda --quiet
```

### Inference

```bash
# Route a single query
llmrouter infer --router causallmrouter --config configs/model_config_test/causallm_router.yaml \
    --query "Explain photosynthesis"

# Route queries from a file
llmrouter infer --router causallmrouter --config configs/model_config_test/causallm_router.yaml \
    --input queries.jsonl --output results.json

# Route only (without calling LLM API)
llmrouter infer --router causallmrouter --config configs/model_config_test/causallm_router.yaml \
    --query "What is machine learning?" --route-only
```

### Interactive Chat

```bash
# Launch chat interface
llmrouter chat --router causallmrouter --config configs/model_config_test/causallm_router.yaml

# Launch with custom port
llmrouter chat --router causallmrouter --config configs/model_config_test/causallm_router.yaml --port 8080

# Create a public shareable link
llmrouter chat --router causallmrouter --config configs/model_config_test/causallm_router.yaml --share
```

---

## Usage Examples

### Training

```python
from llmrouter.models import CausalLMRouter, CausalLMRouterTrainer

router = CausalLMRouter(yaml_path="configs/model_config_train/causallm_router.yaml")
trainer = CausalLMRouterTrainer(router=router)
trainer.train()
```

### Inference

```python
from llmrouter.models import CausalLMRouter

router = CausalLMRouter(yaml_path="configs/model_config_test/causallm_router.yaml")
result = router.route_single({"query": "Explain photosynthesis"})
print(f"Routed to: {result['model_name']}")
```

## Advantages

- ✅ **LLM Reasoning**: Leverages language understanding for routing
- ✅ **No Feature Engineering**: Directly processes raw query text
- ✅ **Transfer Learning**: Benefits from pre-trained knowledge
- ✅ **Efficient with LoRA**: Trains only small adapter layers

## Limitations

- ❌ **GPU Required**: vLLM needs CUDA
- ❌ **Slow Training**: Finetuning LLMs is time-intensive
- ❌ **Large Model Size**: Base model is multi-GB
- ❌ **Parsing Errors**: Generated text may not match LLM names exactly

## When to Use

**Good For:**
- Large datasets where LLM finetuning is worthwhile
- Complex routing patterns that benefit from language understanding
- GPU resources available

**Alternatives:**
- Small datasets → KNN/SVM Router
- No GPU → MLP Router
- Need fast training → Simpler classifiers

## Related Routers

- **Router-R1**: Also uses LLM but with agentic reasoning
- **MLP/SVM Routers**: Simpler supervised alternatives
- **LLM Multi-Round Router**: Uses LLM for decomposition and routing

---

For questions or issues, please refer to the main LLMRouter documentation or open an issue on GitHub.
