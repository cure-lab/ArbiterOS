# Prompt Templates Directory

This directory contains all prompt templates stored as YAML files, organized into subfolders by category.

## Directory Structure

```
llmrouter/prompts/
├── __init__.py                    # Loader utility functions
├── task_prompts/                  # Task-specific prompts for evaluation benchmarks
│   ├── task_mc.yaml               # Multiple choice task system prompt
│   ├── task_gsm8k.yaml            # GSM8K math task system prompt
│   ├── task_math.yaml             # MATH task system prompt
│   ├── task_mbpp.yaml             # MBPP code generation system prompt
│   └── task_humaneval.yaml        # HumanEval code completion system prompt
├── agentic_role/                  # Agent and multi-agent reasoning prompts
│   ├── agent_prompt.yaml          # Agent prompt for multi-agent reasoning
│   ├── agent_decomp_cot.yaml     # Chain-of-thought aggregation prompt
│   ├── agent_decomp.yaml         # Simple decomposition prompt
│   └── agent_decomp_route.yaml   # Decomposition + routing template
├── router_prompts/                # Router-specific prompt templates
│   ├── router_qwen.yaml           # Router_R1 prompt for Qwen models
│   └── router_llama.yaml          # Router_R1 prompt for LLaMA models
├── data_prompts/                  # Data conversion and processing prompts
│   └── data_conversion.yaml       # Data format conversion prompt
└── README.md                      # This file
```

## Usage

```python
from llmrouter.prompts import load_prompt_template

# Load a template (searches recursively in all subfolders)
template = load_prompt_template("task_mc")
prompt = template.format(question="What is 2+2?")

# Or specify the subfolder path explicitly
template = load_prompt_template("task_prompts/task_mc")
```

## Template Categories

### Task Prompts (`task_prompts/`)
System prompts for evaluation benchmarks and tasks:
- **task_mc.yaml**: Multiple choice questions (used by mmlu, gpqa, commonsense_qa, etc.)
- **task_gsm8k.yaml**: GSM8K math word problems
- **task_math.yaml**: MATH dataset problems
- **task_mbpp.yaml**: MBPP code generation
- **task_humaneval.yaml**: HumanEval code completion

### Agentic Role Prompts (`agentic_role/`)
Prompts for multi-agent reasoning and query decomposition:
- **agent_prompt.yaml**: Instructions for specialized assistant models in multi-agent reasoning
- **agent_decomp_cot.yaml**: Chain-of-thought prompt for aggregating decomposed responses
- **agent_decomp.yaml**: Simple query decomposition prompt
- **agent_decomp_route.yaml**: Template for decomposition + routing (filled at runtime)

### Router Prompts (`router_prompts/`)
Router-specific prompt templates:
- **router_qwen.yaml**: Router_R1 prompt template for Qwen model family
- **router_llama.yaml**: Router_R1 prompt template for LLaMA model family

**Note**: Router_R1 keeps its prompts in `llmrouter/models/Router_R1/prompt_pool.py` (hardcoded). These YAML files are available for reference or other routers.

### Data Prompts (`data_prompts/`)
Prompts for data format conversion and preprocessing:
- **data_conversion.yaml**: Prompt template for data format conversion

## YAML Format

Each YAML file follows this structure:

```yaml
template: |
  Your prompt template string here.
  Can span multiple lines.
  Use {placeholder} for formatting.
```

## Loading Templates

The `load_prompt_template()` function automatically searches all subfolders recursively. You can:

1. **Use just the filename** (recommended):
   ```python
   template = load_prompt_template("task_mc")  # Searches all subfolders
   ```

2. **Specify the subfolder path** (explicit):
   ```python
   template = load_prompt_template("task_prompts/task_mc")  # Searches specific subfolder
   ```

This ensures all prompt strings are centralized and easily editable without modifying Python code.
