"""
Prompt formatting utilities for LLMRouter scripts
"""

from typing import Optional
from llmrouter.prompts import load_prompt_template

# Registry for custom prompt formatters (for extensibility)
PROMPT_REGISTRY = {}

# Registry for task-to-metric mappings (for automatic metric selection)
TASK_METRIC_REGISTRY = {}


def register_prompt(task_name: str, default_metric: Optional[str] = None):
    """
    Decorator to register a custom prompt formatter.
    
    Args:
        task_name: Name of the task to register (e.g., 'my_custom_task')
        default_metric: Optional default metric name for this task. If provided,
                       this metric will be automatically used when task_name is
                       specified but metric is not in calculate_task_performance().
    
    Returns:
        Decorator function
    
    Example:
        @register_prompt('sentiment_analysis', default_metric='sentiment_exact_match')
        def format_sentiment_analysis_prompt(sample_data):
            # ...
    """
    def decorator(func):
        PROMPT_REGISTRY[task_name] = func
        # Register task-to-metric mapping if metric is provided
        if default_metric is not None:
            TASK_METRIC_REGISTRY[task_name] = default_metric
        return func
    return decorator


def register_task_metric(task_name: str, metric_name: str):
    """
    Register a default metric for a task (can be called separately if needed).
    
    Args:
        task_name: Name of the task
        metric_name: Name of the metric to use by default for this task
    
    Example:
        register_task_metric('sentiment_analysis', 'sentiment_exact_match')
    """
    TASK_METRIC_REGISTRY[task_name] = metric_name


def format_mc_prompt(question, choices):
    """Format prompt for multiple choice tasks

    Returns:
        dict: {"system": system_prompt, "user": user_query}
    """
    formatted_choices = ""
    options = ["A", "B", "C", "D"]

    for i, choice in enumerate(choices):
        formatted_choices += f"{options[i]}. {choice}\n"

    system_prompt = load_prompt_template("task_mc")
    user_query = f"""## Question:
{question}

## Options:
{formatted_choices}"""

    return {"system": system_prompt, "user": user_query}


def format_gsm8k_prompt(query):
    """Format prompt for GSM8K math tasks

    Returns:
        dict: {"system": system_prompt, "user": user_query}
    """
    system_prompt = load_prompt_template("task_gsm8k")
    user_query = f"Question: {query}"
    return {"system": system_prompt, "user": user_query}


def format_math_prompt(query):
    """Format prompt for MATH tasks

    Returns:
        dict: {"system": system_prompt, "user": user_query}
    """
    system_prompt = load_prompt_template("task_math")
    user_query = f"Question: {query}"
    return {"system": system_prompt, "user": user_query}


def format_commonsense_qa_prompt(query, choices):
    """Format prompt for commonsense QA tasks

    Returns:
        dict: {"system": system_prompt, "user": user_query}
    """
    label = choices["label"]
    text = choices["text"]
    choice_text = ""
    for i, j in zip(label, text):
        choice_text += "\n" + "(" + i + ")" + " " + j

    system_prompt = load_prompt_template("task_mc")  # Uses same MC format
    user_query = f"Question: {query}\n{choice_text}"
    return {"system": system_prompt, "user": user_query}


def format_mbpp_prompt(text, tests):
    """Format prompt for MBPP code generation tasks

    Returns:
        dict: {"system": system_prompt, "user": user_query}
    """
    tests_str = "\n".join(tests)
    system_prompt = load_prompt_template("task_mbpp")
    user_query = f"Task: {text}\n\nYour code should pass these tests:\n\n{tests_str}"
    return {"system": system_prompt, "user": user_query}


def format_humaneval_prompt(prompt):
    """Format prompt for HumanEval code generation tasks

    Returns:
        dict: {"system": system_prompt, "user": user_query}
    """
    system_prompt = load_prompt_template("task_humaneval")
    user_query = f"Complete the following function:\n\n{prompt}"
    return {"system": system_prompt, "user": user_query}


def format_charades_ego_prompt(query, choices, id_to_label, task_type, top_k):
    """Format prompt for Charades-Ego video classification tasks

    Returns:
        dict: {"system": system_prompt, "user": user_query}
    """
    # Example ids for different task types
    example_ids = {
        "activity": "c104",
        "verb": "v003",
        "object": "o012"
    }
    
    # Mapping titles
    mapping_titles = {
        "activity": "Activity id mapping (id: name):",
        "verb": "Verb id mapping (id: name):",
        "object": "Object id mapping (id: name):"
    }
    
    mapping_lines = "\n".join([f"{cid}: {id_to_label.get(cid, '')}" for cid in choices])
    system_prompt = f"""You are a strict classifier for Charades-Ego video action recognition.
Task: output the top-{top_k} most likely ids from the mapping below (example: {example_ids[task_type]}).

Output format:
- Output ONLY ids separated by commas. No spaces. No extra words.
- Output exactly {top_k} ids.
- Put your final answer inside \\boxed{{}}.

How to use the description:
- Focus on the person's motion and the main target objects they act on.
- Ignore static background items unless they are being used.
- Prefer the MOST specific label supported by the evidence.
- If the descriptions mention multiple possibilities, pick the single best-supported id.

{mapping_titles[task_type]}
{mapping_lines}"""
    
    return {"system": system_prompt, "user": query}


def format_mathvista_prompt(query, choices, question_type):
    """
    Format prompt for MathVista tasks

    Returns:
        dict: {"system": system_prompt, "user": user_query}
    """
    system_prompt = load_prompt_template("task_math")
    
    if question_type == 'multi_choice':
        formatted_choices = "\n".join(f"{label}. {text}" for label, text in zip(choices['labels'], choices['text']))
        user_query = f"{query}\n\n## Options:\n{formatted_choices}"
    else:
        user_query = query
    
    return {"system": system_prompt, "user": user_query}


def generate_task_query(task_name, sample_data):
    """Generate query prompt based on task name and sample_data.

    Returns:
        dict: {"system": system_prompt, "user": user_query}
              For simple tasks without special formatting, system will be None.
    """
    # First check custom registry (user-defined formatters take precedence)
    if task_name in PROMPT_REGISTRY:
        formatter = PROMPT_REGISTRY[task_name]
        result = formatter(sample_data)
        # Ensure result is in correct format
        if isinstance(result, dict) and "system" in result and "user" in result:
            return result
        elif isinstance(result, str):
            # Legacy format: return as user query only
            return {"system": None, "user": result}
        else:
            # Try to convert to dict format
            return {"system": None, "user": str(result)}
    
    # Built-in task formatters
    if task_name in ["natural_qa", "trivia_qa"]:
        # No special system prompt for these tasks
        return {"system": None, "user": sample_data['query']}
    elif task_name in ["mmlu"]:
        return format_mc_prompt(sample_data['query'], sample_data['choices'])
    elif task_name == "gpqa":
        return format_mc_prompt(sample_data['query'], sample_data['choices'])
    elif task_name == "mbpp":
        return format_mbpp_prompt(sample_data['query'], sample_data['choices'])
    elif task_name == "human_eval":
        return format_humaneval_prompt(sample_data['query'])
    elif task_name == "gsm8k":
        return format_gsm8k_prompt(sample_data['query'])
    elif task_name == "commonsense_qa":
        return format_commonsense_qa_prompt(sample_data['query'], sample_data['choices'])
    elif task_name == "math":
        return format_math_prompt(sample_data['query'])
    elif task_name == "openbook_qa":
        return format_commonsense_qa_prompt(sample_data['query'], sample_data['choices'])
    elif task_name == "arc_challenge":
        return format_commonsense_qa_prompt(sample_data['query'], sample_data['choices'])
    elif task_name == "geometry3k":
        return format_math_prompt(sample_data['query'])
    elif task_name == "mathvista":
        return format_mathvista_prompt(sample_data['query'], sample_data['choices'], sample_data['question_type'])
    elif task_name.startswith("charades_ego"):
        task_type = task_name.split("_")[-1]  # 'activity', 'verb', or 'object'
        return format_charades_ego_prompt(
            query=sample_data['query'],
            choices=sample_data['choices'],
            id_to_label=sample_data['id_to_label'],
            task_type=task_type,
            top_k=sample_data['top_k']
        )
    else:
        raise ValueError(f"Unknown task name: {task_name}")
