"""
Random Router Plugin
====================

A simple example custom router that randomly selects from available LLMs.

This serves as a template for creating custom routers.
"""

from .router import RandomRouter

try:
    from .trainer import RandomRouterTrainer
except ImportError:
    RandomRouterTrainer = None

__all__ = ["RandomRouter", "RandomRouterTrainer"]
