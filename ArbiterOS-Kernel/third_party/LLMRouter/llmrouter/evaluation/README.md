# Evaluation Package

The `evaluation` package provides a high-level, decorator-based interface for batch evaluation of model predictions against ground truths. It uses a flexible metric registration system that allows you to easily add custom evaluation metrics.

## Overview

This package separates concerns:
- **`llmrouter.utils.evaluation`**: Low-level utility functions (scoring functions, normalization, embeddings, etc.)
- **`llmrouter.evaluation`**: High-level batch evaluation interface with decorator-based metric registration

## Quick Start

```python
from llmrouter.evaluation import evaluate_batch

# Prepare your data
data = [
    {'prediction': 'hello world', 'ground_truth': 'hello', 'metric': 'f1'},
    {'prediction': 'exact match', 'ground_truth': 'exact match', 'metric': 'em'}
]

# Evaluate
results = evaluate_batch(data)

# Results include original data plus 'score' field
for result in results:
    print(f"Score: {result['score']}")
```

## Built-in Metrics

The following metrics are registered by default:

- `'em'` - Exact match
- `'em_mc'` - Multiple choice exact match
- `'cem'` - Contains exact match
- `'cemf1'` - Contains exact match with F1 fallback
- `'f1'` - F1 score
- `'bert_score'` - BERT-based semantic similarity
- `'gsm8k'` - GSM8K math problem evaluation

## How It Works

### Architecture

The evaluation system uses a **decorator pattern** with a **registry**:

1. **Metric Registry**: A global dictionary (`EVALUATION_METRICS`) stores all registered metrics
2. **Decorator**: `@evaluation_metric(name)` registers functions as metrics
3. **Batch Evaluator**: `evaluate_batch()` looks up metrics from the registry and applies them

### Workflow

```
Input Data → Metric Lookup → Metric Function → Score → Output Data
     ↓              ↓              ↓            ↓          ↓
  [dict]    EVALUATION_METRICS   eval_func    float    [dict+score]
```

1. Each item in the input list specifies a `metric` name
2. `evaluate_batch()` looks up the metric function in `EVALUATION_METRICS`
3. The metric function is called with `prediction`, `ground_truth`, and any additional kwargs
4. The score is added to the result dictionary

## Registering Custom Metrics

### Method 1: Using the Decorator (Recommended)

Create a Python file with your custom metric:

```python
# custom_metrics/my_metrics.py
from llmrouter.evaluation import evaluation_metric

@evaluation_metric('my_custom_metric')
def my_eval_function(prediction: str, ground_truth: str, threshold: float = 0.5, **kwargs) -> float:
    """
    Your custom evaluation logic.
    
    Args:
        prediction: The predicted text
        ground_truth: The ground truth text
        threshold: Custom parameter (passed via data dictionary)
        **kwargs: Any additional parameters from the data dictionary
    
    Returns:
        Evaluation score (float)
    """
    # Your evaluation logic here
    return 1.0 if len(prediction) > threshold else 0.0
```

Then import the module to register it:

```python
# main.py
from llmrouter.evaluation import evaluate_batch
import custom_metrics.my_metrics  # Import triggers decorator registration

data = [
    {'prediction': 'hello', 'ground_truth': 'world', 'metric': 'my_custom_metric', 'threshold': 3}
]
results = evaluate_batch(data)
```

**Important**: You must import the module for the decorator to execute and register the metric.

### Method 2: Programmatic Registration

```python
from llmrouter.evaluation import register_custom_metric, evaluate_batch

def my_metric(prediction: str, ground_truth: str, **kwargs) -> float:
    return 1.0 if prediction == ground_truth else 0.0

register_custom_metric('my_metric', my_metric)

# Now use it
results = evaluate_batch([
    {'prediction': 'hi', 'ground_truth': 'hello', 'metric': 'my_metric'}
])
```

## Metric Function Signature

All metric functions must follow this signature:

```python
def metric_function(
    prediction: str,      # Required: predicted text
    ground_truth: str,     # Required: ground truth text
    **kwargs               # Optional: additional parameters from data dict
) -> float:               # Required: return a float score
    ...
```

Additional parameters from the data dictionary (besides `prediction`, `ground_truth`, and `metric`) are passed as `**kwargs`.

## Examples

### Basic Usage

```python
from llmrouter.evaluation import evaluate_batch

data = [
    {'prediction': 'hello', 'ground_truth': 'hello', 'metric': 'em'},
    {'prediction': 'world', 'ground_truth': 'word', 'metric': 'f1'}
]

results = evaluate_batch(data)
# [
#     {'prediction': 'hello', 'ground_truth': 'hello', 'metric': 'em', 'score': 1.0},
#     {'prediction': 'world', 'ground_truth': 'word', 'metric': 'f1', 'score': 0.5}
# ]
```

### Using Default Metric

```python
results = evaluate_batch(data, default_metric='em')
# All items without 'metric' key will use 'em'
```

### Custom Metric with Parameters

```python
# Register custom metric
@evaluation_metric('length_check')
def length_metric(prediction: str, ground_truth: str, min_length: int = 5, **kwargs) -> float:
    return 1.0 if len(prediction) >= min_length else 0.0

# Use it
data = [
    {
        'prediction': 'This is a long text',
        'ground_truth': 'short',
        'metric': 'length_check',
        'min_length': 10  # Passed as kwargs
    }
]
results = evaluate_batch(data)
```

## Checking Available Metrics

```python
from llmrouter.evaluation import get_available_metrics

print(get_available_metrics())
# ['em', 'em_mc', 'cem', 'cemf1', 'f1', 'bert_score', 'gsm8k', ...]
```

## Error Handling

If evaluation fails for an item:
- The score is set to `0.0`
- An `evaluation_error` field is added with the error message
- The original data is preserved
- A warning is printed

## Relationship with `utils.evaluation`

The `evaluation` package uses utility functions from `llmrouter.utils.evaluation`:

- Built-in metrics wrap utility functions like `f1_score()`, `exact_match_score()`, etc.
- You can also use these utilities directly in your custom metrics:

```python
from llmrouter.utils.evaluation import f1_score, exact_match_score

@evaluation_metric('my_hybrid_metric')
def hybrid_metric(prediction: str, ground_truth: str, **kwargs) -> float:
    f1, _, _ = f1_score(prediction, ground_truth)
    em = exact_match_score(prediction, ground_truth)
    return (f1 + float(em)) / 2.0
```

## More Examples

See `example.py` in this directory for comprehensive examples including:
- Multiple custom metric registration methods
- Using different built-in metrics
- Passing custom parameters to metrics

## API Reference

### Main Functions

- `evaluate_batch(data, default_metric=None)` - Evaluate a batch of predictions
- `evaluation_metric(name)` - Decorator to register a metric
- `register_custom_metric(name, func)` - Programmatically register a metric
- `get_available_metrics()` - List all registered metrics

### Registry

- `EVALUATION_METRICS` - Dictionary mapping metric names to functions

