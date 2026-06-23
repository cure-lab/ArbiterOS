"""
KeywordRouter: 基于关键词匹配进行模型路由

配置示例（arbiteros_kernel/llm_router/configs/keyword_router.yaml）:
  routing_rules:
    - keywords: ["代码", "code", "编程", "programming", "python", "javascript"]
      model: claude-sonnet-4-6
      priority: 10
    - keywords: ["图像", "image", "图片", "picture", "视觉", "vision"]
      model: gpt-5.5
      priority: 5
  default_model: claude-sonnet-4-6
  case_sensitive: false
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class KeywordRouter:
    """基于关键词匹配的简单路由器，不依赖 llmrouter-lib MetaRouter。"""

    def __init__(self, config: dict):
        """
        Args:
            config: 路由配置字典，包含：
              - routing_rules: list[dict] 规则列表，每项包含 keywords, model, priority
              - default_model: str 默认模型名
              - case_sensitive: bool 是否区分大小写（默认 False）
        """
        self.config = config
        self.rules = sorted(
            config.get("routing_rules", []),
            key=lambda r: r.get("priority", 0),
            reverse=True,
        )
        self.default_model = config.get("default_model", "")
        self.case_sensitive = config.get("case_sensitive", False)
        logger.info(
            f"[KeywordRouter] 已加载 {len(self.rules)} 条规则，"
            f"默认模型: {self.default_model}"
        )

    def route_single(self, query: dict[str, Any]) -> dict[str, Any]:
        """
        根据 query 内容匹配关键词，返回目标模型名。

        Args:
            query: 包含 "query" 键的字典

        Returns:
            dict: {"model_name": str, "matched_rule": Optional[dict]}
        """
        query_text = query.get("query", "")
        if not isinstance(query_text, str):
            query_text = str(query_text)

        search_text = query_text if self.case_sensitive else query_text.lower()

        for rule in self.rules:
            keywords = rule.get("keywords", [])
            target_model = rule.get("model")
            if not keywords or not target_model:
                continue
            for kw in keywords:
                kw_normalized = kw if self.case_sensitive else kw.lower()
                if kw_normalized in search_text:
                    result = {**query, "model_name": target_model}
                    logger.debug(
                        f"[KeywordRouter] 匹配关键词 '{kw}' → {target_model}"
                    )
                    return result

        result = {**query, "model_name": self.default_model}
        logger.debug(f"[KeywordRouter] 无匹配，使用默认模型 → {self.default_model}")
        return result

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "KeywordRouter":
        """从 YAML 文件加载配置并创建路由器实例。"""
        try:
            import yaml
        except ImportError:
            raise ImportError("需要 pyyaml: pip install pyyaml")
        with open(yaml_path) as f:
            config = yaml.safe_load(f) or {}
        return cls(config)
