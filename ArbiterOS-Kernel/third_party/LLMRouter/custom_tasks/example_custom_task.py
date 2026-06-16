"""
Example custom prompt formatter.

This file demonstrates how to create a custom prompt formatter for a new task
following the same patterns as built-in tasks:
- YAML template files in task_prompts/ subdirectory
- Formatting functions that return {"system": str, "user": str} dict
- Proper integration with the existing prompt system

The unified loader automatically searches both custom_tasks/ and built-in
prompts/ directories, with custom templates taking precedence.
"""

from llmrouter.utils.prompting import register_prompt
from llmrouter.prompts import load_prompt_template


@register_prompt('code_refine')
def format_code_refine_prompt(sample_data):
    """
    Format prompt for code refinement task.
    
    This follows the same pattern as built-in tasks:
    - Loads YAML template from task_prompts/task_code_refine.yaml
    - Returns {"system": system_prompt, "user": user_query} dict
    
    Args:
        sample_data: Dictionary containing task data with keys:
            - 'code': str - The code to refine
            - 'instruction': str (optional) - Custom instruction
    
    Returns:
        dict: {"system": system_prompt, "user": user_query}
    """
    # Load system prompt from YAML template (unified loader searches both custom and built-in)
    system_prompt = load_prompt_template("task_code_refine")
    
    # Extract data
    code = sample_data.get("code", "")
    instruction = sample_data.get("instruction", "Refine the following code.")
    
    # Format user query
    user_query = f"""{instruction}

Code:
{code}

Please output the refined version below:"""
    
    return {"system": system_prompt, "user": user_query}
