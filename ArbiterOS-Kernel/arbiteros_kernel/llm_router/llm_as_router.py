"""
LLMasRouter: 使用 LLM 本身来做路由决策

通过调用一个专门的 LLM，让它根据对话内容返回结构化输出（JSON schema），
指定应该使用哪个模型来处理这个请求。

配置示例（arbiteros_kernel/llm_router/configs/llm_as_router.yaml）:
  router_llm:
    model: "gpt-4o-mini"  # 用于路由决策的模型
    api_base: "https://api.openai.com/v1"
    api_key: "${OPENAI_API_KEY}"  # 支持环境变量
    temperature: 0.0
    max_tokens: 100

  rule: "path/to/routing_rules.md"  # 路由规则文件（可选）

  available_models:
    - name: "claude-sonnet-4-6"
      description: "强大的代码和推理模型，适合复杂任务"
    - name: "gpt-5.5"
      description: "多模态模型，支持图像生成和视觉理解"

  default_model: "claude-sonnet-4-6"
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class LLMasRouter:
    """使用 LLM 进行路由决策的路由器。"""

    def __init__(self, config: dict):
        """
        Args:
            config: 路由配置字典，包含：
              - router_llm: dict 路由 LLM 配置（model, api_base, api_key 等）
              - rule: str 路由规则文件路径（可选）
              - available_models: list[dict] 可选模型列表，每项包含 name, description
              - default_model: str 默认模型名（LLM 调用失败时使用）
        """
        self.config = config
        self.router_llm_config = config.get("router_llm", {})
        self.rule_path = config.get("rule")
        self.available_models = config.get("available_models", [])
        self.default_model = config.get("default_model", "")

        # 解析环境变量
        self._resolve_env_vars()

        # 加载路由规则
        self.routing_rules = self._load_routing_rules()

        # 构建模型选项描述
        self.model_descriptions = "\n".join(
            f"- {m['name']}: {m.get('description', 'No description')}"
            for m in self.available_models
        )
        self.model_names = [m["name"] for m in self.available_models]

        logger.info(
            f"[LLMasRouter] 已加载，路由模型: {self.router_llm_config.get('model')}, "
            f"可选模型: {self.model_names}, "
            f"规则文件: {self.rule_path or 'None'}"
        )

    def _resolve_env_vars(self):
        """解析配置中的环境变量（${VAR} 格式）。"""
        def resolve(value):
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                env_var = value[2:-1]
                return os.environ.get(env_var, "")
            return value

        for key, val in self.router_llm_config.items():
            self.router_llm_config[key] = resolve(val)

    def _load_routing_rules(self) -> str:
        """加载路由规则文件内容。"""
        if not self.rule_path:
            return ""

        rule_file = Path(self.rule_path)
        if not rule_file.is_absolute():
            # 相对路径：相对于配置文件所在目录
            config_dir = Path(__file__).parent / "configs"
            rule_file = config_dir / self.rule_path

        if not rule_file.exists():
            logger.warning(f"[LLMasRouter] 规则文件不存在: {rule_file}")
            return ""

        try:
            with open(rule_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            logger.info(f"[LLMasRouter] 已加载规则文件: {rule_file} ({len(content)} chars)")
            return content
        except Exception as e:
            logger.error(f"[LLMasRouter] 读取规则文件失败: {e}")
            return ""

    def route_single(self, query: dict[str, Any]) -> dict[str, Any]:
        """
        调用路由 LLM 来决策使用哪个模型。

        Args:
            query: 包含 "query" 键的字典

        Returns:
            dict: {"model_name": str, "reasoning": Optional[str]}
        """
        query_text = query.get("query", "")
        if not isinstance(query_text, str):
            query_text = str(query_text)

        try:
            routed_model = self._call_router_llm(query_text)
            result = {**query, "model_name": routed_model}
            logger.debug(f"[LLMasRouter] LLM 路由决策 → {routed_model}")
            return result
        except Exception as e:
            logger.warning(f"[LLMasRouter] LLM 调用失败，使用默认模型: {e}")
            result = {**query, "model_name": self.default_model}
            return result

    def _call_router_llm(self, query_text: str) -> str:
        """调用路由 LLM 获取模型选择。"""
        try:
            import litellm
        except ImportError:
            raise ImportError("需要 litellm: pip install litellm")

        # 构建 messages
        messages = []

        # System prompt: 规则文件内容
        if self.routing_rules:
            messages.append({"role": "system", "content": self.routing_rules})

        # User prompt: 用户查询 + 可选模型列表
        user_prompt = self._build_routing_prompt(query_text)
        messages.append({"role": "user", "content": user_prompt})

        # 构建 JSON schema（enum 约束可选模型）
        response_schema = {
            "type": "object",
            "properties": {
                "selected_model": {
                    "type": "string",
                    "enum": self.model_names,
                    "description": "The model to use for this query"
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief explanation for the choice"
                }
            },
            "required": ["selected_model", "reasoning"],
            "additionalProperties": False
        }

        # 调用 LLM
        response = litellm.completion(
            model=self.router_llm_config.get("model", "gpt-4o-mini"),
            messages=messages,
            api_base=self.router_llm_config.get("api_base"),
            api_key=self.router_llm_config.get("api_key"),
            temperature=self.router_llm_config.get("temperature", 0.0),
            max_tokens=self.router_llm_config.get("max_tokens", 100),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "router_decision",
                    "schema": response_schema,
                    "strict": True
                }
            }
        )

        content = response.choices[0].message.content
        parsed = json.loads(content)
        selected = parsed.get("selected_model", self.default_model)
        reasoning = parsed.get("reasoning", "")

        if reasoning:
            logger.debug(f"[LLMasRouter] 决策理由: {reasoning}")

        return selected

    def _build_routing_prompt(self, query_text: str) -> str:
        """构建发送给路由 LLM 的 prompt。"""
        return f"""You are a model router. Given a user query, select the most appropriate model to handle it.

Available models:
{self.model_descriptions}

User query:
{query_text}

Select the best model and briefly explain why."""

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "LLMasRouter":
        """从 YAML 文件加载配置并创建路由器实例。"""
        try:
            import yaml
        except ImportError:
            raise ImportError("需要 pyyaml: pip install pyyaml")
        with open(yaml_path) as f:
            config = yaml.safe_load(f) or {}
        return cls(config)
