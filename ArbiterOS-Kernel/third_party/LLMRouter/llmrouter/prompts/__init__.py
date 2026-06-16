"""
Prompt template loader utility.

This module provides functions to load prompt templates from YAML files.
Templates are organized in subfolders:
- task_prompts/ - Task-specific prompts for evaluation benchmarks
- agentic_role/ - Agent and multi-agent reasoning prompts
- router_prompts/ - Router-specific prompt templates
- data_prompts/ - Data conversion and processing prompts

Also searches custom_tasks/ directory for user-defined templates.
Custom templates take precedence over built-in templates with the same name.
"""

import os
import yaml
from pathlib import Path

# Get the directory where this file is located
_PROMPTS_DIR = Path(__file__).parent

# Get custom tasks directory (custom_tasks relative to project root)
# Project root is assumed to be 3 levels up from llmrouter/prompts/__init__.py
_PROJECT_ROOT = _PROMPTS_DIR.parent.parent.parent
_CUSTOM_TASKS_DIR = _PROJECT_ROOT / "custom_tasks"


def load_prompt_template(template_name: str) -> str:
    """
    Load a prompt template from a YAML file.
    
    Searches in both custom_tasks/ and llmrouter/prompts/ directories.
    Custom templates take precedence over built-in templates with the same name.
    
    Search order:
    1. custom_tasks/task_prompts/ (custom templates - highest priority)
    2. llmrouter/prompts/ (built-in templates - fallback)
    
    You can specify either:
    - Just the filename: "task_mc" (searches all subfolders)
    - With subfolder path: "task_prompts/task_mc" (searches specific subfolder)
    
    Args:
        template_name: Name of the template file (without .yaml extension)
                      Can include subfolder path like "task_prompts/task_mc"
    
    Returns:
        The prompt template string
    
    Raises:
        FileNotFoundError: If the template file doesn't exist in either location
    """
    searched_locations = []
    
    # Helper function to load and validate template
    def _load_template_file(template_path: Path) -> str:
        """Load and validate a template file"""
        with open(template_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        if 'template' not in data:
            raise ValueError(f"YAML file {template_path} must contain a 'template' key")
        return data['template']
    
    # Step 1: Search in custom_tasks directory first (highest priority)
    if _CUSTOM_TASKS_DIR.exists():
        # Try direct path first (if subfolder is specified)
        if "/" in template_name or "\\" in template_name:
            custom_path = _CUSTOM_TASKS_DIR / f"{template_name}.yaml"
            if custom_path.exists():
                return _load_template_file(custom_path)
        
        # Search recursively in custom_tasks subfolders
        for root, dirs, files in os.walk(_CUSTOM_TASKS_DIR):
            root_path = Path(root)
            # Skip __pycache__ and other hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
            
            for file in files:
                if file == f"{template_name}.yaml":
                    custom_path = root_path / file
                    return _load_template_file(custom_path)
    
    # Step 2: Search in built-in prompts directory (fallback)
    # Try direct path first (if subfolder is specified)
    if "/" in template_name or "\\" in template_name:
        builtin_path = _PROMPTS_DIR / f"{template_name}.yaml"
        if builtin_path.exists():
            return _load_template_file(builtin_path)
    
    # Search recursively in built-in prompts subfolders
    for root, dirs, files in os.walk(_PROMPTS_DIR):
        root_path = Path(root)
        # Skip __pycache__ and other hidden directories
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
        
        for file in files:
            if file == f"{template_name}.yaml":
                builtin_path = root_path / file
                return _load_template_file(builtin_path)
    
    # If not found in either location, raise error
    searched_locations.append(str(_CUSTOM_TASKS_DIR) if _CUSTOM_TASKS_DIR.exists() else None)
    searched_locations.append(str(_PROMPTS_DIR))
    
    raise FileNotFoundError(
        f"Prompt template not found: {template_name}.yaml\n"
        f"Searched in: {[loc for loc in searched_locations if loc]}"
    )


def load_prompt_template_with_metadata(template_name: str) -> dict:
    """
    Load a prompt template with its metadata from a YAML file.
    
    Searches in both custom_tasks/ and llmrouter/prompts/ directories.
    Custom templates take precedence over built-in templates with the same name.
    
    Args:
        template_name: Name of the template file (without .yaml extension)
                      Can include subfolder path like "task_prompts/task_mc"
    
    Returns:
        Dictionary with 'template' and any other metadata keys
    """
    searched_locations = []
    
    # Step 1: Search in custom_tasks directory first (highest priority)
    if _CUSTOM_TASKS_DIR.exists():
        # Try direct path first (if subfolder is specified)
        if "/" in template_name or "\\" in template_name:
            custom_path = _CUSTOM_TASKS_DIR / f"{template_name}.yaml"
            if custom_path.exists():
                with open(custom_path, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f)
        
        # Search recursively in custom_tasks subfolders
        for root, dirs, files in os.walk(_CUSTOM_TASKS_DIR):
            root_path = Path(root)
            # Skip __pycache__ and other hidden directories
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
            
            for file in files:
                if file == f"{template_name}.yaml":
                    custom_path = root_path / file
                    with open(custom_path, 'r', encoding='utf-8') as f:
                        return yaml.safe_load(f)
    
    # Step 2: Search in built-in prompts directory (fallback)
    # Try direct path first (if subfolder is specified)
    if "/" in template_name or "\\" in template_name:
        builtin_path = _PROMPTS_DIR / f"{template_name}.yaml"
        if builtin_path.exists():
            with open(builtin_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
    
    # Search recursively in built-in prompts subfolders
    for root, dirs, files in os.walk(_PROMPTS_DIR):
        root_path = Path(root)
        # Skip __pycache__ and other hidden directories
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
        
        for file in files:
            if file == f"{template_name}.yaml":
                builtin_path = root_path / file
                with open(builtin_path, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f)
    
    # If not found in either location, raise error
    searched_locations.append(str(_CUSTOM_TASKS_DIR) if _CUSTOM_TASKS_DIR.exists() else None)
    searched_locations.append(str(_PROMPTS_DIR))
    
    raise FileNotFoundError(
        f"Prompt template not found: {template_name}.yaml\n"
        f"Searched in: {[loc for loc in searched_locations if loc]}"
    )
