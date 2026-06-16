"""
Custom task definitions for user-defined tasks.

This folder contains custom task definitions that extend the built-in tasks,
including prompt templates, formatters, and evaluation metrics.

Structure:
- task_prompts/ - YAML template files (mirrors llmrouter/prompts/task_prompts/)
- *.py - Python files with task formatters and metrics

To add a new custom task:
1. Create a YAML template file in task_prompts/task_{task_name}.yaml
2. Create a formatting function with @register_prompt('task_name', default_metric='...') decorator
3. Optionally create a metric function with @evaluation_metric('metric_name') decorator
4. Import this module in your main script to register it

See README.md for detailed instructions and examples.
"""
