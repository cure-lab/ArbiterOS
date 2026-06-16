# System/User Prompt Format Update

## Overview

We've updated the prompt formatting system to properly separate **system prompts** (task instructions) from **user prompts** (actual queries). This provides better format control for API calls and improves LLM response quality.

## What Changed

### Before (Old Format)
Task-specific instructions were concatenated with the query as a single user message:

```python
# Old format - everything in user message
messages = [{"role": "user", "content": "Answer the following question...\nQuestion: What is 2+2?"}]
```

### After (New Format)
System prompt (instructions) and user query are separated:

```python
# New format - separated system and user messages
messages = [
    {"role": "system", "content": "Answer the following math question step by step."},
    {"role": "user", "content": "Question: What is 2+2?"}
]
```

## Benefits

1. **Better Format Control**: LLMs follow system prompts more strictly
2. **Clearer Separation**: Instructions vs. content are clearly distinguished
3. **API Compatibility**: Matches OpenAI's recommended message format
4. **Multi-Round Support**: System context persists across conversation turns

## Updated Functions

### 1. Prompt Formatting (`llmrouter/utils/prompting.py`)

All format functions now return `{"system": str, "user": str}` dictionaries:

```python
# Example: Multiple choice formatting
def format_mc_prompt(question, choices):
    return {
        "system": "Answer the multiple-choice question. Put your final answer in parenthesis.",
        "user": "## Question:\n{question}\n\n## Options:\n{choices}"
    }
```

**Supported Tasks:**
- `mmlu`, `gpqa` - Multiple choice
- `gsm8k`, `math` - Math problems
- `commonsense_qa`, `openbook_qa`, `arc_challenge` - QA tasks
- `mbpp`, `human_eval` - Code generation
- `natural_qa`, `trivia_qa` - Simple QA (no special system prompt)

### 2. API Calling (`llmrouter/utils/api_calling.py`)

`call_api()` now accepts optional `system_prompt` in request:

```python
request = {
    "api_endpoint": "https://api.example.com/v1",
    "query": "What is 2+2?",
    "system_prompt": "You are a helpful math tutor.",  # NEW
    "model_name": "model-name",
    "api_name": "api/model-path"
}

result = call_api(request)
```

### 3. Router Helper (`llmrouter/utils/router_helpers.py`)

New helper function to format requests with task-specific prompts:

```python
from llmrouter.utils import format_api_request_with_task

# Automatically handles system/user prompt separation
request = format_api_request_with_task(
    query_text="What is the capital of France?",
    task_name="mmlu",  # Applies MMLU-specific formatting
    api_endpoint="https://api.example.com/v1",
    model_name="gpt-4",
    api_model_name="openai/gpt-4",
    choices=["A. Paris", "B. London", "C. Berlin", "D. Madrid"]
)
# Returns: {"query": "...", "system_prompt": "...", ...}
```

## Migration Guide for Routers

### Old Router Code
```python
# Old: route_batch method
if row_task_name:
    formatted_query = generate_task_query(row_task_name, sample_data)
    query_text_for_execution = formatted_query  # Combined string
else:
    query_text_for_execution = original_query

request = {
    "api_endpoint": api_endpoint,
    "query": query_text_for_execution,  # No system_prompt
    "model_name": model_name,
    "api_name": api_model_name
}
```

### New Router Code (Option 1: Use Helper)
```python
# New: using helper function
from llmrouter.utils import format_api_request_with_task

request = format_api_request_with_task(
    query_text=original_query,
    task_name=row_task_name,
    api_endpoint=api_endpoint,
    model_name=model_name,
    api_model_name=api_model_name,
    choices=row_copy.get("choices")
)
# Automatically handles system/user separation
```

### New Router Code (Option 2: Manual)
```python
# New: manual handling
if row_task_name:
    formatted = generate_task_query(row_task_name, sample_data)
    query_text = formatted["user"]      # User query
    system_prompt = formatted["system"]  # System instructions
else:
    query_text = original_query
    system_prompt = None

request = {
    "api_endpoint": api_endpoint,
    "query": query_text,
    "system_prompt": system_prompt,  # NEW: system prompt
    "model_name": model_name,
    "api_name": api_model_name
}
```

## Backward Compatibility

- Routers that don't use task-specific formatting are **not affected**
- `system_prompt` is **optional** in API requests - if not provided, only user message is sent
- Existing routers will continue to work but should be updated to use the new format

## Testing

```python
# Test with task-specific formatting
from llmrouter.utils import generate_task_query

result = generate_task_query("gsm8k", {"query": "What is 2+2?"})
assert "system" in result
assert "user" in result
assert result["system"] == "Answer the following math question step by step."
assert "What is 2+2?" in result["user"]
```

## Example: Updated Router

See `llmrouter/utils/router_helpers.py` for the helper function implementation.

Routers can now provide better task-specific guidance through system prompts, leading to more accurate and well-formatted responses.

---

**Note**: This update improves prompt handling across all routers. For questions or issues, please open a GitHub issue.
