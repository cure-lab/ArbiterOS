"""
TagRouter: Adaptive LLM Routing Engine

Routes user queries to the most appropriate LLM using:
1. Lightweight LLM tagger for contextual tag extraction
2. DSL rule engine for rule-based candidate selection
3. Thompson Sampling for exploration/exploitation
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from .tagger import LightweightTagger
from .dsl_engine import DSLRuleEngine
from .thompson_sampler import ThompsonSampler

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    """Result of a routing decision."""
    selected_model: str
    tags: dict[str, Any]
    matched_rule_id: Optional[str]
    candidate_models: list[str]
    selection_info: dict[str, Any]
    latency_ms: float


class TagRouter:
    """
    Adaptive LLM Routing Engine.

    Implements MetaRouter-compatible interface (route_single) so it can be
    used with ArbiterOSRouter.

    Usage:
        router = TagRouter.from_yaml("configs/tag_router.yaml")
        result = router.route_single({"query": "Write a Python sort function"})
        print(result["model_name"])
    """

    def __init__(self, config: dict[str, Any], config_path: Optional[str] = None):
        self.config = config
        self.config_path = config_path  # 保存配置文件路径，用于持久化

        # Tagger config — pass tag_schema so it builds prompt/schema dynamically
        tagger_cfg = config.get("tagger", {})
        tag_schema = config.get("tag_schema", {})
        self.tagger = LightweightTagger(tagger_cfg, tag_schema=tag_schema)

        # DSL rules
        rules = config.get("routing_matrix", [])
        self.dsl_engine = DSLRuleEngine(rules)

        # Default models fallback
        self.default_models: list[str] = config.get("default_models", [])

        # Thompson Sampler
        ts_cfg = config.get("thompson_sampler", {})
        self.sampler = ThompsonSampler(
            cost_weight=ts_cfg.get("cost_weight", 0.3),
            exploration_bonus=ts_cfg.get("exploration_bonus", 0.1),
        )

        # 从配置加载已有的状态
        self._load_state_from_config(config.get("state", {}))

        # Cost estimates per model
        self.cost_estimates: dict[str, float] = config.get("model_costs", {})

        # 持久化配置
        self._update_counter = 0
        self._persist_interval = 10  # 每 10 次更新持久化一次

        logger.info(f"[TagRouter] Initialized with {len(self.dsl_engine.roots)} DSL rules")

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "TagRouter":
        """Load TagRouter from YAML config file."""
        import yaml
        import os

        def resolve_env(obj: Any) -> Any:
            if isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
                var = obj[2:-1]
                return os.environ.get(var, obj)
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
        """
        MetaRouter-compatible interface.

        Args:
            query_input: dict with at least a "query" key

        Returns:
            dict with "model_name" key (and additional info)
        """
        query = query_input.get("query", "")
        result = self._route(query)
        return {
            "model_name": result.selected_model,
            "tags": result.tags,
            "matched_rule": result.matched_rule_id,
            "latency_ms": result.latency_ms,
            **result.selection_info,
        }

    def _route(self, query: str) -> RouteResult:
        start = time.perf_counter()

        # Step 1: Extract tags
        tag_result = self.tagger.extract_tags(query)
        # extract_tags returns either a TagResult or a plain dict (fallback)
        if isinstance(tag_result, dict):
            tags = tag_result
        else:
            tags = tag_result.tags if hasattr(tag_result, "tags") else tag_result

        # Step 2: DSL matching
        matched_node, candidate_models = self.dsl_engine.match(tags)
        matched_rule_id = matched_node.node_id if matched_node else None

        if not candidate_models:
            candidate_models = self.default_models or list(self.cost_estimates.keys())
        if not candidate_models:
            # Hard fallback
            candidate_models = ["default"]

        # Step 3: Thompson Sampling
        node_id = matched_rule_id or "_default_"
        selected_model, selection_info = self.sampler.select_model(
            candidate_models=candidate_models,
            node_id=node_id,
            rule_path=matched_rule_id or "default",
            cost_estimates=self.cost_estimates,
        )

        latency_ms = (time.perf_counter() - start) * 1000
        logger.debug(
            f"[TagRouter] tags={tags} rule={matched_rule_id} "
            f"candidates={candidate_models} selected={selected_model} "
            f"latency={latency_ms:.1f}ms"
        )

        return RouteResult(
            selected_model=selected_model,
            tags=tags,
            matched_rule_id=matched_rule_id,
            candidate_models=candidate_models,
            selection_info=selection_info,
            latency_ms=latency_ms,
        )

    def record_outcome(
        self,
        node_id: str,
        model_id: str,
        success: bool,
        cost: float,
        latency_ms: float = 0.0,
    ) -> None:
        """Record feedback for Thompson Sampling updates."""
        self.sampler.record_outcome(node_id, model_id, success, cost, latency_ms)

        # 定期持久化状态
        self._update_counter += 1
        if self._update_counter >= self._persist_interval:
            self._persist_state()
            self._update_counter = 0

    def get_stats(self) -> dict[str, Any]:
        """Get routing statistics."""
        return self.sampler.get_stats_summary()

    def _load_state_from_config(self, state: dict) -> None:
        """
        从配置加载 Thompson Sampling 状态。

        支持两种格式：
        1. 新格式：状态嵌入 routing_matrix 中（推荐）
        2. 旧格式：独立的 state.nodes 部分（向后兼容）
        """
        # 优先尝试新格式：从 routing_matrix 加载
        rules = self.config.get("routing_matrix", [])
        loaded_from_matrix = False

        for i, rule in enumerate(rules):
            rule_state = rule.get("state", {})
            if not rule_state:
                continue

            node_id = f"rule_{i}"
            node = self.sampler.get_or_create_node(node_id, rule_path=node_id)

            for model_id, model_data in rule_state.items():
                if model_id == "last_updated":  # 跳过元数据
                    continue

                model_stats = node.get_or_create_model(model_id)

                # 恢复状态
                alpha = model_data.get("alpha", 1.0)
                beta = model_data.get("beta", 1.0)
                total_requests = model_data.get("total_requests", 0)
                total_cost = model_data.get("total_cost", 0.0)

                model_stats.alpha = alpha
                model_stats.beta = beta
                model_stats.total_cost = total_cost
                model_stats.total_requests = total_requests
                model_stats.total_successes = int(alpha - 1)

                loaded_from_matrix = True

            # 重新计算 node 的 total_requests
            node.total_requests = sum(m.total_requests for m in node.model_stats.values())

        if loaded_from_matrix:
            logger.info(f"[TagRouter] Loaded state from routing_matrix (new format)")
            return

        # 回退到旧格式：从 state.nodes 加载（向后兼容）
        nodes = state.get("nodes", {})
        for node_id, node_data in nodes.items():
            models = node_data.get("models", {})

            node = self.sampler.get_or_create_node(node_id, rule_path=node_id)

            for model_id, model_data in models.items():
                model_stats = node.get_or_create_model(model_id)

                alpha = model_data.get("alpha", 1.0)
                beta = model_data.get("beta", 1.0)
                total_requests = model_data.get("total_requests", 0)
                total_cost = model_data.get("total_cost", 0.0)

                model_stats.alpha = alpha
                model_stats.beta = beta
                model_stats.total_cost = total_cost
                model_stats.total_requests = total_requests
                model_stats.total_successes = int(alpha - 1)

            node.total_requests = sum(m.total_requests for m in node.model_stats.values())

        if nodes:
            logger.info(f"[TagRouter] Loaded state from state.nodes (legacy format)")

    def _persist_state(self) -> None:
        """
        持久化 Thompson Sampling 状态到配置文件。

        新格式：状态直接嵌入 routing_matrix 中，更直观。
        """
        if not self.config_path:
            logger.warning("[TagRouter] Cannot persist state: config_path not set")
            return

        try:
            import yaml
            from datetime import datetime
            from pathlib import Path

            # 读取完整配置
            with open(self.config_path, "r") as f:
                config = yaml.safe_load(f) or {}

            # 获取 routing_matrix
            routing_matrix = config.get("routing_matrix", [])

            # 为每个 rule 添加 state
            for i, rule in enumerate(routing_matrix):
                node_id = f"rule_{i}"

                # 如果这个 node 有统计数据，则更新
                if node_id in self.sampler.nodes:
                    node = self.sampler.nodes[node_id]

                    # 构建状态字典
                    rule_state = {}
                    for model_id, model_stats in node.model_stats.items():
                        rule_state[model_id] = {
                            "alpha": float(model_stats.alpha),
                            "beta": float(model_stats.beta),
                            "total_cost": float(model_stats.total_cost),
                            "total_requests": model_stats.total_requests,
                            "avg_cost": float(model_stats.avg_cost_per_request),
                        }

                    # 添加更新时间戳
                    rule_state["last_updated"] = datetime.utcnow().isoformat() + "Z"

                    # 更新到 rule 中
                    rule["state"] = rule_state

            # 更新配置
            config["routing_matrix"] = routing_matrix

            # 原子写入
            temp_path = Path(self.config_path).with_suffix(".tmp")
            with open(temp_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

            temp_path.replace(self.config_path)

            logger.debug(f"[TagRouter] Persisted state to routing_matrix in {self.config_path}")

        except Exception as e:
            logger.error(f"[TagRouter] Failed to persist state: {e}")

    def shutdown(self) -> None:
        """关闭路由器，持久化最终状态。"""
        self._persist_state()
        logger.info("[TagRouter] Shutdown complete, state persisted")
