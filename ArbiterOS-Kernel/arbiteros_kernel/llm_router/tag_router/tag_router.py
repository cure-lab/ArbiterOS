"""
TagRouter: Adaptive LLM Routing Engine

Routes user queries using:
1. Lightweight LLM tagger → semantic tags
2. Bayesian Logistic Bandit → Thompson Sampling over tags
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .tagger import LightweightTagger
from .bayesian_bandit import BayesianLogisticBandit

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    selected_model: str
    tags: dict[str, Any]
    candidate_models: list[str]
    selection_info: dict[str, Any]
    latency_ms: float


class TagRouter:
    """
    Adaptive LLM Routing Engine.

    On startup: reads tagged prompt database → fits Bayesian Logistic Bandit
    On route:  tags prompt → Thompson samples best model

    Implements MetaRouter-compatible interface (route_single).

    Usage:
        router = TagRouter.from_yaml("configs/tag_router.yaml")
        result = router.route_single({"query": "Cancel my flight"})
        print(result["model_name"])
    """

    def __init__(self, config: dict[str, Any], config_path: Optional[str] = None):
        self.config = config
        self.config_path = config_path

        # Tagger
        tagger_cfg = config.get("tagger", {})
        tag_schema = config.get("tag_schema", {})
        self.tagger = LightweightTagger(tagger_cfg, tag_schema=tag_schema)

        # Candidate models and costs
        self.candidate_models: list[str] = config.get("candidate_models", [])
        self.model_costs: dict[str, float] = config.get("model_costs", {})

        # Bandit
        bb_cfg = config.get("bayesian_bandit", {})
        feature_names = list(tag_schema.keys())
        self.bandit = BayesianLogisticBandit(
            feature_names=feature_names,
            candidate_models=list(self.candidate_models),
            prior_var=bb_cfg.get("prior_var", 1.0),
            cost_weight=bb_cfg.get("cost_weight", 0.3),
            model_costs=self.model_costs,
        )

        # Load database and fit
        db_path_rel = config.get("database_path", "")
        if db_path_rel:
            if config_path and not os.path.isabs(db_path_rel):
                db_path = str(Path(config_path).parent / db_path_rel)
            else:
                db_path = db_path_rel
            database = json.loads(Path(db_path).read_text())
            self.bandit.fit_all(database)
            logger.info(f"[TagRouter] Fitted bandit from {len(database)} records in {db_path}")
        else:
            logger.warning("[TagRouter] No database_path in config — using uninformative prior")

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "TagRouter":
        import yaml

        def resolve_env(obj: Any) -> Any:
            if isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
                return os.environ.get(obj[2:-1], obj)
            if isinstance(obj, dict):
                return {k: resolve_env(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [resolve_env(v) for v in obj]
            return obj

        with open(yaml_path) as f:
            cfg = yaml.safe_load(f)
        cfg = resolve_env(cfg) if cfg else {}
        return cls(cfg, config_path=yaml_path)

    def route_single(self, query_input: dict[str, Any]) -> dict[str, Any]:
        query = query_input.get("query", "")
        result = self._route(query)
        return {
            "model_name": result.selected_model,
            "tags": result.tags,
            "latency_ms": result.latency_ms,
            **result.selection_info,
        }

    def _route(self, query: str) -> RouteResult:
        start = time.perf_counter()

        # Step 1: Extract tags
        tag_result = self.tagger.extract_tags(query)
        tags = tag_result

        # Step 2: Thompson sample
        candidates = self.candidate_models or list(self.model_costs.keys()) or ["default"]
        selected_model, selection_info = self.bandit.select(tags, candidates)

        latency_ms = (time.perf_counter() - start) * 1000
        return RouteResult(
            selected_model=selected_model,
            tags=tags,
            candidate_models=candidates,
            selection_info=selection_info,
            latency_ms=latency_ms,
        )

    def get_stats(self) -> dict[str, Any]:
        return self.bandit.get_stats_summary()
