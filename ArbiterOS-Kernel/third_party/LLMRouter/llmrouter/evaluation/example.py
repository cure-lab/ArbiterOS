"""
Example: How to register and use custom evaluation metrics

This file demonstrates the recommended pattern for registering custom metrics,
following the same style as decorator_sample.py.
"""

# ============================================================================
# Method 1: Using the @evaluation_metric decorator (RECOMMENDED)
# ============================================================================

# Step 1: Create a file with your custom metric (e.g., custom_metrics/my_metrics.py)
# custom_metrics/my_metrics.py
from llmrouter.evaluation import evaluation_metric


@evaluation_metric('length_based')
def length_based_metric(prediction: str, ground_truth: str, min_length: int = 5, **kwargs) -> float:
    """
    Custom metric that checks if prediction length meets minimum requirement.
    
    Args:
        prediction: The predicted text
        ground_truth: The ground truth text
        min_length: Minimum length threshold
        **kwargs: Additional arguments from data dictionary
    
    Returns:
        Score (1.0 if length >= min_length, 0.0 otherwise)
    """
    return 1.0 if len(prediction) >= min_length else 0.0


@evaluation_metric('contains_keywords')
def contains_keywords_metric(prediction: str, ground_truth: str, keywords: list = None, **kwargs) -> float:
    """
    Custom metric that checks if prediction contains required keywords.
    
    Args:
        prediction: The predicted text
        ground_truth: The ground truth text
        keywords: List of keywords that must be present
        **kwargs: Additional arguments from data dictionary
    
    Returns:
        Score (1.0 if all keywords found, 0.0 otherwise)
    """
    if keywords is None:
        keywords = []
    prediction_lower = prediction.lower()
    return 1.0 if all(kw.lower() in prediction_lower for kw in keywords) else 0.0


# ============================================================================
# Method 2: Using register_custom_metric() function
# ============================================================================

def similarity_metric(prediction: str, ground_truth: str, threshold: float = 0.8, **kwargs) -> float:
    """Simple similarity metric based on character overlap"""
    if not prediction or not ground_truth:
        return 0.0
    
    # Simple character-based similarity
    pred_chars = set(prediction.lower())
    gt_chars = set(ground_truth.lower())
    
    if len(gt_chars) == 0:
        return 0.0
    
    similarity = len(pred_chars & gt_chars) / len(gt_chars)
    return 1.0 if similarity >= threshold else 0.0


# Register it programmatically
from llmrouter.evaluation import register_custom_metric
register_custom_metric('similarity', similarity_metric)


# ============================================================================
# Usage Example
# ============================================================================

if __name__ == "__main__":
    # Import the evaluation function
    from llmrouter.evaluation import evaluate_batch
    
    # IMPORTANT: Import your custom metric modules to trigger decorator registration
    # This is the key step - the import causes the decorators to execute and register the metrics
    # import custom_metrics.my_metrics  # Uncomment when you create your custom_metrics module
    
    # Example data with different metrics
    data = [
        {
            'prediction': 'This is a long prediction',
            'ground_truth': 'short',
            'metric': 'length_based',
            'min_length': 10  # Passed as kwargs to the metric function
        },
        {
            'prediction': 'The answer contains important keywords',
            'ground_truth': 'irrelevant',
            'metric': 'contains_keywords',
            'keywords': ['answer', 'contains']  # Passed as kwargs
        },
        {
            'prediction': 'hello world',
            'ground_truth': 'hello',
            'metric': 'similarity',
            'threshold': 0.5
        },
        {
            'prediction': 'exact match',
            'ground_truth': 'exact match',
            'metric': 'em'  # Built-in metric
        }
    ]
    
    # Evaluate the batch
    results = evaluate_batch(data)
    
    # Print results
    for result in results:
        print(f"Metric: {result['metric']}, Score: {result['score']}")
    
    # Check available metrics
    from llmrouter.evaluation import get_available_metrics
    print(f"\nAvailable metrics: {get_available_metrics()}")

