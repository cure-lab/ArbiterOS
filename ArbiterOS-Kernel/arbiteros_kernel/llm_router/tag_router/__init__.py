"""
TagRouter - 自适应 LLM 路由引擎
基于小型 LLM 标签提取 + DSL 规则匹配 + Thompson Sampling 动态路由
"""
from .tag_router import TagRouter

__all__ = ["TagRouter"]
