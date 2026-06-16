"""
LLMRouter Serve Module
======================
Provides an OpenAI-compatible API service that integrates directly with OpenClaw and other frontends.

Usage:
    llmrouter serve --router randomrouter --config config.yaml --port 8000

Or in code:
    from llmrouter.serve import create_app, run_server
    app = create_app(router_name="randomrouter", config_path="config.yaml")
    run_server(app, port=8000)
"""

from .server import create_app, run_server
from .config import ServeConfig

__all__ = ["create_app", "run_server", "ServeConfig"]
