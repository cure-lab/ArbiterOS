"""
Batch evaluation system with decorator-based metric registration.

This module provides a high-level interface for evaluating batches of predictions
against ground truths using a decorator-based metric registration system.

The actual evaluation utility functions (f1_score, exact_match_score, etc.) are
imported from llmrouter.utils.evaluation, which contains the low-level evaluation utilities.
"""

import re
from typing import List, Optional, Dict, Callable, Any

# Import evaluation utility functions from utils.evaluation
from llmrouter.utils.evaluation import (
    exact_match_score, f1_score, cem_score, cemf1_score, get_bert_score
)


# ============================================================================
# Decorator-based evaluation system
# ============================================================================

# Registry for evaluation metrics
EVALUATION_METRICS = {}


def evaluation_metric(metric_name: str):
    """
    Decorator to register a custom evaluation metric.
    
    The decorated function should take at least two arguments:
    - prediction: str - The predicted text
    - ground_truth: str - The ground truth text
    Additional arguments can be passed via the data dictionary as kwargs.
    
    Args:
        metric_name: Name of the metric to register (e.g., 'em', 'f1', 'custom_metric')
    
    Returns:
        The decorated function (unchanged)
    
    Usage:
        To register a custom metric, create a Python file with your metric function:
        
        # custom_metrics/my_metric.py
        from llmrouter.evaluation import evaluation_metric
        
        @evaluation_metric('my_custom_metric')
        def my_eval_function(prediction: str, ground_truth: str, threshold: float = 0.5, **kwargs) -> float:
            # Your custom evaluation logic
            return 1.0 if len(prediction) > threshold else 0.0
        
        Then import the module to register it:
        
        # main.py
        from llmrouter.evaluation import evaluate_batch
        import custom_metrics.my_metric  # Import to trigger decorator registration
        
        data = [
            {'prediction': 'hello', 'ground_truth': 'world', 'metric': 'my_custom_metric', 'threshold': 3}
        ]
        results = evaluate_batch(data)
    """
    def decorator(func):
        EVALUATION_METRICS[metric_name] = func
        return func
    return decorator


# Register built-in evaluation metrics using utility functions from utils.evaluation
@evaluation_metric('em')
def _eval_exact_match(prediction: str, ground_truth: str, normal_method: str = "", **kwargs) -> float:
    """Exact match evaluation metric"""
    return float(exact_match_score(prediction, ground_truth, normal_method=normal_method))


@evaluation_metric('em_mc')
def _eval_exact_match_mc(prediction: str, ground_truth: str, **kwargs) -> float:
    """Multiple choice exact match evaluation metric"""
    return float(exact_match_score(prediction, ground_truth, normal_method="mc"))


@evaluation_metric('cem')
def _eval_cem(prediction: str, ground_truth: str, **kwargs) -> float:
    """Contains exact match evaluation metric"""
    return float(cem_score(prediction, ground_truth))


@evaluation_metric('cemf1')
def _eval_cemf1(prediction: str, ground_truth: str, **kwargs) -> float:
    """Contains exact match with F1 fallback evaluation metric"""
    return float(cemf1_score(prediction, ground_truth))


@evaluation_metric('f1')
def _eval_f1(prediction: str, ground_truth: str, **kwargs) -> float:
    """F1 score evaluation metric"""
    f1, _, _ = f1_score(prediction, ground_truth)
    return float(f1)


@evaluation_metric('bert_score')
def _eval_bert_score(prediction: str, ground_truth: str, **kwargs) -> float:
    """BERT score evaluation metric"""
    return float(get_bert_score([prediction], [ground_truth]))


@evaluation_metric('gsm8k')
def _eval_gsm8k(prediction: str, ground_truth: str, **kwargs) -> float:
    """GSM8K math problem evaluation metric"""
    ground_truth_processed = ground_truth.split("####")[-1].replace(',', '').replace('$', '').replace('.', '').strip()
    answer = re.findall("(\\-?[0-9\\.\\,]+)", prediction)
    if len(answer) == 0:
        return 0.0
    invalid_str = ['', '.']
    final_answer = None
    for final_answer in reversed(answer):
        if final_answer not in invalid_str:
            break
    if final_answer is None:
        return 0.0
    final_answer = final_answer.replace(',', '').replace('$', '').replace('.', '').strip()
    return 1.0 if final_answer == ground_truth_processed else 0.0


def evaluate_batch(data: List[Dict[str, Any]], default_metric: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Evaluate a batch of predictions against ground truths using registered metrics.
    
    Each input dictionary should contain:
    - 'prediction': str - The predicted text (required)
    - 'ground_truth': str - The ground truth text (required)
    - 'metric': str - The metric name to use (optional, can be specified per item or as default)
    - Additional fields will be passed to the evaluation function as kwargs
    
    Args:
        data: List of dictionaries, each containing at least 'prediction' and 'ground_truth'
        default_metric: Default metric to use if 'metric' is not specified in a dictionary.
                       If None and 'metric' is missing, raises ValueError.
    
    Returns:
        List of dictionaries, each containing:
        - All original fields from input
        - 'score': float - The evaluation score
    
    Example:
        data = [
            {'prediction': 'hello', 'ground_truth': 'hello', 'metric': 'em'},
            {'prediction': 'world', 'ground_truth': 'word', 'metric': 'f1'}
        ]
        results = evaluate_batch(data)
        # Returns: [
        #     {'prediction': 'hello', 'ground_truth': 'hello', 'metric': 'em', 'score': 1.0},
        #     {'prediction': 'world', 'ground_truth': 'word', 'metric': 'f1', 'score': 0.5}
        # ]
    """
    results = []
    
    for item in data:
        # Validate required fields
        if 'prediction' not in item:
            raise ValueError("Each dictionary must contain 'prediction' field")
        if 'ground_truth' not in item:
            raise ValueError("Each dictionary must contain 'ground_truth' field")
        
        # Get metric name
        metric_name = item.get('metric', default_metric)
        if metric_name is None:
            raise ValueError(
                "Metric must be specified either in the dictionary ('metric' key) "
                "or as default_metric parameter"
            )
        
        # Check if metric is registered
        if metric_name not in EVALUATION_METRICS:
            raise ValueError(
                f"Unknown metric '{metric_name}'. "
                f"Available metrics: {list(EVALUATION_METRICS.keys())}"
            )
        
        # Get evaluation function
        eval_func = EVALUATION_METRICS[metric_name]
        
        # Extract prediction and ground_truth
        prediction = item['prediction']
        ground_truth = item['ground_truth']
        
        # Prepare kwargs (exclude prediction, ground_truth, metric from kwargs)
        kwargs = {k: v for k, v in item.items() 
                 if k not in ['prediction', 'ground_truth', 'metric']}
        
        try:
            # Evaluate
            score = eval_func(prediction, ground_truth, **kwargs)
            
            # Create result dictionary with original fields plus score
            result = item.copy()
            result['score'] = float(score)
            results.append(result)
            
        except Exception as e:
            # If evaluation fails, set score to 0.0 and preserve original data
            result = item.copy()
            result['score'] = 0.0
            result['evaluation_error'] = str(e)
            results.append(result)
            print(f"Warning: Evaluation failed for item with metric '{metric_name}': {e}")
    
    return results


def get_available_metrics() -> List[str]:
    """
    Get list of all registered evaluation metrics.
    
    Returns:
        List of metric names
    """
    return list(EVALUATION_METRICS.keys())


def register_custom_metric(metric_name: str, eval_function: Callable) -> None:
    """
    Register a custom evaluation metric function programmatically.
    
    This is an alternative to using the @evaluation_metric decorator.
    Use this when you want to register metrics dynamically at runtime.
    
    Args:
        metric_name: Name of the metric
        eval_function: Function that takes (prediction: str, ground_truth: str, **kwargs) -> float
    
    Example:
        def my_custom_metric(prediction: str, ground_truth: str, threshold: float = 0.5, **kwargs) -> float:
            # Custom evaluation logic
            return 1.0 if len(prediction) > threshold else 0.0
        
        from llmrouter.evaluation import register_custom_metric
        register_custom_metric('my_metric', my_custom_metric)
        
        # Now you can use it
        from llmrouter.evaluation import evaluate_batch
        results = evaluate_batch([{'prediction': 'hi', 'ground_truth': 'hello', 'metric': 'my_metric'}])
    
    Note:
        The decorator approach (@evaluation_metric) is preferred for most use cases.
        See evaluation_metric() decorator documentation for the recommended pattern.
    """
    EVALUATION_METRICS[metric_name] = eval_function

