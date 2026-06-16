# Custom Tasks

This directory contains user-defined custom task definitions for LLMRouter, including task names, prompt templates, and evaluation metrics.

## Quick Start

Add your custom task components, then import the module:

```python
import custom_tasks.my_tasks  # Import triggers registration

from llmrouter.utils import generate_task_query
from llmrouter.utils.evaluation import calculate_task_performance

# Use your custom task
prompt = generate_task_query('my_task_name', sample_data)

# Evaluate with automatic metric selection
score = calculate_task_performance(
    prediction="...",
    ground_truth="...",
    task_name="my_task_name"  # Metric automatically inferred
)
```

## Adding Custom Components

### 1. Task Name

Register a task name with a formatter function:

```python
# custom_tasks/my_tasks.py
from llmrouter.utils.prompting import register_prompt
from llmrouter.prompts import load_prompt_template

@register_prompt('my_task_name', default_metric='my_metric')
def format_my_task_name_prompt(sample_data):
    system_prompt = load_prompt_template("task_my_task_name")
    # Format user query from sample_data
    user_query = f"Question: {sample_data.get('query', '')}"
    return {"system": system_prompt, "user": user_query}
```

**Key Points:**
- Use `@register_prompt('task_name', default_metric='...')` decorator
- Function must return `{"system": str, "user": str}` dict
- `default_metric` links task to its evaluation metric (optional)

### 2. Prompt Template

Create a YAML file in `task_prompts/`:

```yaml
# task_prompts/task_my_task_name.yaml
template: |
  You are an expert at [task description]. [Instructions].
```

**Key Points:**
- File name: `task_{task_name}.yaml`
- Contains `template:` key with system prompt
- Automatically discovered by unified loader

### 3. Evaluation Metric

Register a custom metric function:

```python
# custom_tasks/my_tasks.py
from llmrouter.evaluation import evaluation_metric

@evaluation_metric('my_metric')
def my_metric(prediction: str, ground_truth: str, **kwargs) -> float:
    # Your evaluation logic
    return 1.0 if prediction == ground_truth else 0.0
```

**Key Points:**
- Use `@evaluation_metric('metric_name')` decorator
- Function signature: `(prediction: str, ground_truth: str, **kwargs) -> float`
- Returns score between 0.0 and 1.0

## How It Gets Picked Up

**Automatic Discovery:**
1. **Task Names**: Registered in `PROMPT_REGISTRY` when module is imported
   - `generate_task_query()` checks registry first
   - Custom tasks take precedence over built-in

2. **Metrics**: Registered in `EVALUATION_METRICS` when module is imported
   - `calculate_task_performance()` checks registry first
   - Works with any registered metric name

3. **Templates**: Unified loader searches `custom_tasks/task_prompts/` first
   - Custom templates override built-in ones with same name
   - Searches both custom and built-in locations automatically

4. **Task-to-Metric Mapping**: Registered in `TASK_METRIC_REGISTRY` via `default_metric` parameter
   - `calculate_task_performance()` automatically uses default metric if not specified
   - Falls back to built-in mappings if no custom mapping exists

**No Code Changes Needed:**
- Existing code automatically uses custom components after import
- All registries are checked before built-in fallbacks
- Import order: Custom → Built-in

## Example Files

- `example_custom_task.py` - Example task formatters
- `complete_example.py` - Complete example with task, template, and metric
- `task_prompts/task_code_refine.yaml` - Example prompt template

## Notes

- Import your module before using: `import custom_tasks.my_tasks`
- Custom components take precedence over built-in ones
- Templates are automatically searched in both custom and built-in locations
- Use built-in metrics (`cem`, `em_mc`, `f1`, etc.) if they fit your needs
