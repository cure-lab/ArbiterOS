"""
Complete Example: Custom Task with Template and Metric

This file demonstrates a complete custom task implementation showing:
1. Task name registration
2. YAML template loading
3. Custom metric registration
"""

from llmrouter.utils.prompting import register_prompt
from llmrouter.evaluation import evaluation_metric
from llmrouter.prompts import load_prompt_template


# ============================================================================
# Step 1: Register Task Name and Create Formatter
# ============================================================================

@register_prompt('sentiment_analysis', default_metric='sentiment_exact_match')  # <-- TASK NAME + DEFAULT METRIC
def format_sentiment_analysis_prompt(sample_data):
    """
    Format prompt for sentiment analysis task.
    
    This function:
    - Loads the YAML template from task_prompts/task_sentiment_analysis.yaml
    - Formats the user query from sample_data
    - Returns {"system": str, "user": str} dict
    
    Args:
        sample_data: Dictionary containing:
            - 'text': str - Text to analyze
    
    Returns:
        dict: {"system": system_prompt, "user": user_query}
    """
    # Load system prompt from YAML template (unified loader searches both custom and built-in)
    system_prompt = load_prompt_template("task_sentiment_analysis")
    
    # Extract data
    text = sample_data.get("text", "")
    
    # Format user query
    user_query = f"""Analyze the sentiment of the following text:

{text}

Sentiment:"""
    
    return {"system": system_prompt, "user": user_query}


# ============================================================================
# Step 2: Register Custom Metric Function
# ============================================================================

@evaluation_metric('sentiment_exact_match')  # <-- METRIC NAME: Use this in calculate_task_performance()
def sentiment_exact_match(prediction: str, ground_truth: str, **kwargs) -> float:
    """
    Custom evaluation metric for sentiment analysis.
    
    This metric checks if the predicted sentiment exactly matches the ground truth.
    Handles cases where prediction might be in a sentence.
    
    Args:
        prediction: The model's predicted output (e.g., "positive" or "The sentiment is positive")
        ground_truth: The expected correct output (e.g., "positive")
        **kwargs: Additional parameters (optional)
    
    Returns:
        float: Score between 0.0 and 1.0 (1.0 for exact match, 0.0 otherwise)
    """
    # Normalize inputs
    pred_clean = prediction.strip().lower()
    gt_clean = ground_truth.strip().lower()
    
    # Valid sentiment values
    valid_sentiments = ['positive', 'negative', 'neutral']
    
    # Extract sentiment from prediction (might be in a sentence)
    pred_sentiment = None
    for sentiment in valid_sentiments:
        if sentiment in pred_clean:
            pred_sentiment = sentiment
            break
    
    # If we found a sentiment in prediction, compare with ground truth
    if pred_sentiment and pred_sentiment == gt_clean:
        return 1.0
    
    # Fallback: direct comparison
    if pred_clean == gt_clean:
        return 1.0
    
    return 0.0


# ============================================================================
# Usage Example (for reference - not executed on import)
# ============================================================================

"""
# In your main script:

# 1. Import this module to register task and metric
import custom_tasks.complete_example

# 2. Use the task
from llmrouter.utils import generate_task_query

prompt = generate_task_query('sentiment_analysis', {
    'text': 'I love this product!'
})
# Returns: {
#     "system": "You are an expert at analyzing sentiment...",
#     "user": "Analyze the sentiment of the following text:\n\nI love this product!\n\nSentiment:"
# }

# 3. Evaluate using the custom metric
from llmrouter.utils.evaluation import calculate_task_performance

score = calculate_task_performance(
    prediction="positive",
    ground_truth="positive",
    metric="sentiment_exact_match"  # Your custom metric name
)
# Returns: 1.0

# Or use in data generation/evaluation pipeline:
data = {
    'query': 'I love this product!',
    'ground_truth': 'positive',
    'task_name': 'sentiment_analysis',
    'metric': 'sentiment_exact_match'
}
"""

