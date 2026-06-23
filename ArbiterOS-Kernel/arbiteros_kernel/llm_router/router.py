"""
ArbiterOS LLM Router
在 LiteLLM pre-call 阶段动态选择最合适的模型。

完全基于 llmrouter-lib 的接口，自动发现 llmrouter/models/ 下的所有路由器子包。
配置写在 litellm_config.yaml 的 llm_routing 块：

  llm_routing:
    enabled: true
    strategy: smallest_llm  # 策略名即 llmrouter/models/ 下的子包名
"""
from __future__ import annotations

import importlib
import inspect
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_registry_cache: Optional[dict[str, Any]] = None
_PRICES_JSON = Path(__file__).resolve().parents[2] / "model_prices_and_context_window.json"


def _lookup_output_cost(litellm_model: str, model_name: str, prices: dict) -> Optional[float]:
    """从 prices JSON 查找 output_cost_per_token；找不到时返回 None。"""
    for key in (litellm_model, model_name):
        val = prices.get(key, {})
        if val.get("output_cost_per_token") is not None:
            return float(val["output_cost_per_token"])
    bare = litellm_model.split("/", 1)[-1] if "/" in litellm_model else litellm_model
    if bare:
        for key, val in prices.items():
            if bare in key and val.get("output_cost_per_token") is not None:
                return float(val["output_cost_per_token"])
    return None


def _get_registry() -> dict[str, Any]:
    """自动扫描 llmrouter.models 子包，发现所有 MetaRouter 子类。"""
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache

    try:
        import llmrouter.models as _m
        from llmrouter.models.meta_router import MetaRouter
    except ImportError:
        _registry_cache = {}
        return {}

    result: dict[str, Any] = {}
    models_path = Path(_m.__file__).parent

    for item in sorted(models_path.iterdir()):
        if not item.is_dir() or not (item / "__init__.py").exists() or item.name.startswith("_"):
            continue
        strategy_name = item.name
        try:
            submod = importlib.import_module(f"llmrouter.models.{strategy_name}")
            for _, cls in inspect.getmembers(submod, inspect.isclass):
                if issubclass(cls, MetaRouter) and cls is not MetaRouter:
                    result[strategy_name] = cls
                    break
        except Exception:
            pass

    _registry_cache = result
    return result


class ArbiterOSRouter:
    """统一路由入口，完全依靠 llmrouter-lib 的接口完成模型路由。"""

    def __init__(self, config: dict):
        self.config = config
        self.strategy = config.get("strategy", "smallest_llm")
        self._router: Any = None  # 懒加载 LLMRouter 实例
        self._model_list: list = []

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ArbiterOSRouter":
        try:
            import yaml
        except ImportError:
            raise ImportError("pyyaml 未安装，请 pip install pyyaml")
        with open(path) as f:
            cfg = yaml.safe_load(f)
        # 支持 litellm_config.yaml（llm_routing 键）和独立配置文件（routing 键）
        routing_cfg = cfg.get("llm_routing") or cfg.get("routing") or {}
        obj = cls(routing_cfg)
        obj._model_list = cfg.get("model_list") or []
        return obj

    def route(self, query: str, current_model: str) -> str:
        """根据 query 返回目标模型名，失败时回退到 current_model。"""
        model, _ = self.route_with_info(query, current_model)
        return model

    def route_with_info(self, query: str, current_model: str) -> tuple[str, str]:
        """返回 (目标模型名, 路由详情说明字符串)，详情供日志使用。"""
        if not self.config.get("enabled", True):
            return current_model, "routing disabled"

        try:
            return self._route_with_info(query, current_model)
        except Exception as e:
            logger.error(f"[LLMRouter] 路由决策失败，回退到原模型 ({current_model}): {e}")
            return current_model, f"error: {e}"

    def _route_with_info(self, query: str, fallback: str) -> tuple[str, str]:
        if self._router is None:
            self._router = self._load_router(self.strategy)
        if self._router is None:
            return fallback, f"router '{self.strategy}' unavailable → {fallback}"
        result = self._router.route_single({"query": query})
        target = result.get("model_name", fallback)
        score = result.get("score") or result.get("confidence") or result.get("predicted_llm")
        info = f"{self.strategy}"
        if score is not None and score != target:
            info += f" score={score}"
        info += f" → {target}"
        return target, info

    def _load_router(self, router_name: str) -> Optional[Any]:
        """从 llmrouter.models 或 arbiteros_kernel.llm_router 加载路由器实例。"""
        # 优先尝试加载自定义路由器（keyword_router 等）
        custom_router = self._try_load_custom_router(router_name)
        if custom_router is not None:
            return custom_router

        # 回退到 llmrouter-lib 的路由器
        registry = _get_registry()
        if not registry:
            logger.error(
                f"[LLMRouter] llmrouter-lib 未安装，无法加载路由器 '{router_name}'。"
                "请运行: pip install llmrouter-lib"
            )
            return None

        router_cls = registry.get(router_name)
        if router_cls is None:
            known = ", ".join(registry.keys())
            logger.error(
                f"[LLMRouter] 未知路由器 '{router_name}'。可用路由器: {known}"
            )
            return None

        # 约定路径：third_party/LLMRouter/configs/<strategy>.yaml
        _llmrouter_root = Path(__file__).resolve().parents[2] / "third_party" / "LLMRouter"
        default_cfg = _llmrouter_root / "configs" / f"{router_name}.yaml"
        config_path = str(default_cfg) if default_cfg.exists() else None

        try:
            instance = router_cls(yaml_path=config_path)
            llm_data = self._build_llm_data()
            if llm_data:
                instance.llm_data = llm_data
                logger.debug(f"[LLMRouter] 注入 llm_data: {list(llm_data.keys())}")
            logger.info(f"[LLMRouter] 路由器 '{router_name}' 加载成功")
            return instance
        except Exception as e:
            logger.error(f"[LLMRouter] 加载路由器 '{router_name}' 失败: {e}")
            return None

    def _try_load_custom_router(self, router_name: str) -> Optional[Any]:
        """尝试加载 arbiteros_kernel.llm_router 下的自定义路由器。"""
        if router_name == "keyword_router":
            try:
                from arbiteros_kernel.llm_router.keyword_router import KeywordRouter
                config_path = Path(__file__).parent / "configs" / "keyword_router.yaml"
                if not config_path.exists():
                    logger.warning(f"[KeywordRouter] 配置文件不存在: {config_path}")
                    return None
                instance = KeywordRouter.from_yaml(str(config_path))
                logger.info("[KeywordRouter] 加载成功")
                return instance
            except Exception as e:
                logger.error(f"[KeywordRouter] 加载失败: {e}")
                return None
        elif router_name == "llm_as_router":
            try:
                from arbiteros_kernel.llm_router.llm_as_router import LLMasRouter
                config_path = Path(__file__).parent / "configs" / "llm_as_router.yaml"
                if not config_path.exists():
                    logger.warning(f"[LLMasRouter] 配置文件不存在: {config_path}")
                    return None
                instance = LLMasRouter.from_yaml(str(config_path))
                logger.info("[LLMasRouter] 加载成功")
                return instance
            except Exception as e:
                logger.error(f"[LLMasRouter] 加载失败: {e}")
                return None
        return None

    def _build_llm_data(self) -> dict:
        """从 model_list + model_prices_and_context_window.json 动态构建 llm_data。
        用 input_cost_per_token 作为模型大小的代理：价格越高 = 模型越大/越强。
        找不到价格时赋极大值，避免被 smallest_llm 误选为最小模型。
        """
        if not self._model_list:
            return {}
        prices: dict = {}
        if _PRICES_JSON.exists():
            with open(_PRICES_JSON) as f:
                prices = json.load(f)
        result = {}
        for entry in self._model_list:
            name = entry.get("model_name", "")
            if not name:
                continue
            litellm_model = entry.get("litellm_params", {}).get("model", "")
            cost = _lookup_output_cost(litellm_model, name, prices)
            # 将纳元/token 转为整数伪参数量（保持价格排序关系）
            # 价格未知时视为高价位大模型，不让 smallest_llm 误选
            size_val = max(1, int(cost * 1e9)) if cost is not None else 999999
            result[name] = {"size": f"{size_val}B"}
        return result

    @staticmethod
    def list_routers() -> list[str]:
        """列出 llmrouter-lib 支持的所有路由器名称。"""
        return list(_get_registry().keys())
