"""
Helper utilities for routers to use system/user prompt format
"""

def format_api_request_with_task(
    query_text,
    task_name,
    api_endpoint,
    model_name,
    api_model_name,
    choices=None
):
    """
    Format API request with proper system/user prompts for task-specific formatting.

    Args:
        query_text (str): The original query text
        task_name (str, optional): Task name for formatting (e.g., "mmlu", "gsm8k")
        api_endpoint (str): API endpoint URL
        model_name (str): Model identifier name
        api_model_name (str): Actual API model name
        choices: Optional choices for multiple choice tasks

    Returns:
        dict: Request dictionary with 'query', 'system_prompt', 'api_endpoint',
              'model_name', 'api_name' fields
    """
    from llmrouter.utils import generate_task_query

    request = {
        "api_endpoint": api_endpoint,
        "model_name": model_name,
        "api_name": api_model_name
    }

    if task_name:
        try:
            sample_data = {
                "query": query_text,
                "choices": choices
            }
            formatted = generate_task_query(task_name, sample_data)
            request["query"] = formatted["user"]
            request["system_prompt"] = formatted["system"]
        except (ValueError, KeyError) as e:
            print(f"Warning: Failed to format query with task '{task_name}': {e}. Using original query.")
            request["query"] = query_text
            request["system_prompt"] = None
    else:
        request["query"] = query_text
        request["system_prompt"] = None

    return request
