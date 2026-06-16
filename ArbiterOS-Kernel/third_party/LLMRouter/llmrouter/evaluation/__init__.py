"""
Evaluation package for LLMRouter.

This package provides high-level evaluation interfaces for batch evaluation
of model predictions against ground truths using a decorator-based metric
registration system.
"""

from .batch_evaluator import (
    evaluate_batch,
    evaluation_metric,
    get_available_metrics,
    register_custom_metric,
    EVALUATION_METRICS
)

__all__ = [
    'evaluate_batch',
    'evaluation_metric',
    'get_available_metrics',
    'register_custom_metric',
    'EVALUATION_METRICS'
]

