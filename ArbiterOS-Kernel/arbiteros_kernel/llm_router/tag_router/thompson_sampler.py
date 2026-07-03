"""
Thompson Sampling implementation for adaptive LLM routing.

Uses Beta distribution to model success probability for each model,
with cost-aware scoring for ROI optimization.
"""

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class ModelStats:
    """Statistics for a single model in the candidate pool."""
    model_id: str
    alpha: float = 1.0  # Beta distribution alpha (successes + 1)
    beta: float = 1.0   # Beta distribution beta (failures + 1)
    total_cost: float = 0.0
    total_requests: int = 0
    total_successes: int = 0
    avg_latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.5
        return self.total_successes / self.total_requests

    @property
    def avg_cost_per_request(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_cost / self.total_requests

    def sample(self) -> float:
        """Sample from Beta distribution."""
        return random.betavariate(self.alpha, self.beta)

    def update(self, success: bool, cost: float, latency_ms: float):
        """Update statistics after a request."""
        self.total_requests += 1
        self.total_cost += cost
        self.avg_latency_ms = (
            (self.avg_latency_ms * (self.total_requests - 1) + latency_ms)
            / self.total_requests
        )
        if success:
            self.total_successes += 1
            self.alpha += 1
        else:
            self.beta += 1


@dataclass
class RoutingNodeStats:
    """Statistics for a DSL rule tree leaf node."""
    node_id: str
    rule_path: str  # e.g. "complexity=high,domain=code"
    model_stats: Dict[str, ModelStats] = field(default_factory=dict)
    total_requests: int = 0

    def get_or_create_model(self, model_id: str) -> ModelStats:
        if model_id not in self.model_stats:
            self.model_stats[model_id] = ModelStats(model_id=model_id)
        return self.model_stats[model_id]

    def total_cost(self) -> float:
        return sum(s.total_cost for s in self.model_stats.values())


class ThompsonSampler:
    """
    Cost-aware Thompson Sampling router.

    Selects models based on Thompson sampling with cost penalty,
    optimizing for ROI rather than pure success rate.
    """

    def __init__(
        self,
        cost_weight: float = 0.3,
        exploration_bonus: float = 0.1,
    ):
        """
        Args:
            cost_weight: Weight for cost penalty (0=ignore cost, 1=cost-only).
            exploration_bonus: Bonus for under-explored models.
        """
        self.cost_weight = cost_weight
        self.exploration_bonus = exploration_bonus
        self.nodes: Dict[str, RoutingNodeStats] = {}

    def get_or_create_node(self, node_id: str, rule_path: str = "") -> RoutingNodeStats:
        if node_id not in self.nodes:
            self.nodes[node_id] = RoutingNodeStats(
                node_id=node_id,
                rule_path=rule_path
            )
        return self.nodes[node_id]

    def select_model(
        self,
        candidate_models: List[str],
        node_id: str,
        rule_path: str = "",
        cost_estimates: Optional[Dict[str, float]] = None,
    ) -> Tuple[str, Dict]:
        """
        Select a model using Thompson Sampling with cost awareness.

        Args:
            candidate_models: List of model IDs to choose from.
            node_id: DSL leaf node ID for tracking.
            rule_path: Human-readable rule path for this node.
            cost_estimates: Optional cost estimates per model (per 1K tokens).

        Returns:
            Tuple of (selected_model_id, selection_info dict).
        """
        if not candidate_models:
            raise ValueError("No candidate models provided")

        node = self.get_or_create_node(node_id, rule_path)
        node.total_requests += 1

        cost_estimates = cost_estimates or {}

        # Compute actual costs: prefer observed avg_cost over initial estimate
        actual_costs = {}
        for model_id in candidate_models:
            model_stats = node.get_or_create_model(model_id)
            # Prefer actual observed avg_cost over initial estimate
            if model_stats.total_requests > 0:
                actual_costs[model_id] = model_stats.avg_cost_per_request
            else:
                actual_costs[model_id] = cost_estimates.get(model_id, 0.0)

        max_actual_cost = max(actual_costs.values()) if actual_costs else 1.0
        if max_actual_cost == 0:
            max_actual_cost = 1.0

        scores = {}
        for model_id in candidate_models:
            model_stats = node.get_or_create_model(model_id)

            # Thompson sample from Beta distribution
            sampled_success = model_stats.sample()

            # Exploration bonus for under-explored models
            exploration = 0.0
            if model_stats.total_requests < 5:
                exploration = self.exploration_bonus * (1.0 - model_stats.total_requests / 5)

            # Cost penalty using actual observed cost (normalized)
            cost = actual_costs[model_id]
            cost_penalty = self.cost_weight * (cost / max_actual_cost)

            scores[model_id] = sampled_success + exploration - cost_penalty

        selected = max(scores, key=lambda m: scores[m])

        selection_info = {
            "node_id": node_id,
            "rule_path": rule_path,
            "scores": scores,
            "selected": selected,
            "exploration_mode": node.total_requests < 10,
        }

        return selected, selection_info

    def record_outcome(
        self,
        node_id: str,
        model_id: str,
        success: bool,
        cost: float,
        latency_ms: float,
    ):
        """Record the outcome of a routing decision."""
        if node_id not in self.nodes:
            # Auto-create leaf node if it doesn't exist
            self.nodes[node_id] = RoutingNodeStats(
                node_id=node_id,
                rule_path=node_id  # Use node_id as fallback rule_path
            )

        node = self.nodes[node_id]
        model_stats = node.get_or_create_model(model_id)
        model_stats.update(success=success, cost=cost, latency_ms=latency_ms)

    def get_best_model(self, node_id: str) -> Optional[str]:
        """Get the model with highest success rate for a given node."""
        if node_id not in self.nodes:
            return None
        node = self.nodes[node_id]
        if not node.model_stats:
            return None
        return max(node.model_stats, key=lambda m: node.model_stats[m].success_rate)

    def get_stats_summary(self) -> Dict:
        """Get a summary of all node statistics."""
        summary = {}
        for node_id, node in self.nodes.items():
            summary[node_id] = {
                "rule_path": node.rule_path,
                "total_requests": node.total_requests,
                "total_cost": node.total_cost(),
                "models": {
                    mid: {
                        "success_rate": stats.success_rate,
                        "total_requests": stats.total_requests,
                        "avg_cost": stats.avg_cost_per_request,
                        "alpha": stats.alpha,
                        "beta": stats.beta,
                    }
                    for mid, stats in node.model_stats.items()
                },
            }
        return summary
