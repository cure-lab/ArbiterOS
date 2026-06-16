"""
OpenClaw Router
===============
OpenAI-compatible API server with intelligent LLM routing.

Supports:
- Built-in strategies: rules, random, round_robin, llm
- LLMRouter ML-based routers: knnrouter, mlprouter, thresholdrouter, etc.

Usage:
    llmrouter serve --config configs/openclaw_example.yaml

Or directly:
    cd openclaw_router && python server.py --config config.yaml
"""

from .config import OpenClawConfig


def create_app(*args, **kwargs):
    """Lazy import FastAPI app factory to avoid hard dependency at import time."""
    from .server import create_app as _create_app
    return _create_app(*args, **kwargs)


def run_server(*args, **kwargs):
    """Lazy import server runner to avoid hard dependency at import time."""
    from .server import run_server as _run_server
    return _run_server(*args, **kwargs)


__all__ = ["create_app", "run_server", "OpenClawConfig"]
