"""
Threshold Router - Advanced Custom Router Example
==================================================

A router that uses difficulty estimation to route queries:
- Easy queries -> smaller/cheaper models
- Hard queries -> larger/more capable models

This demonstrates a more realistic custom router with actual training.
"""

from .router import ThresholdRouter
from .trainer import ThresholdRouterTrainer

__all__ = ["ThresholdRouter", "ThresholdRouterTrainer"]
