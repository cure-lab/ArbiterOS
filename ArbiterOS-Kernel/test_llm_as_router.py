#!/usr/bin/env python
"""
LLMasRouter 测试脚本

使用前需要设置环境变量:
  export OPENAI_API_KEY=your-api-key

或者直接修改 llm_as_router.yaml 中的 api_key 字段
"""
import os
import sys

# 确保设置了 API key
if not os.environ.get("OPENAI_API_KEY"):
    print("请设置 OPENAI_API_KEY 环境变量")
    print("  export OPENAI_API_KEY=your-api-key")
    sys.exit(1)

from arbiteros_kernel.llm_router.router import ArbiterOSRouter
import yaml

# 临时修改配置使用 llm_as_router
with open("litellm_config.yaml") as f:
    cfg = yaml.safe_load(f)

cfg["llm_routing"]["strategy"] = "llm_as_router"

import tempfile
with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
    yaml.dump(cfg, tmp)
    tmp_path = tmp.name

try:
    router = ArbiterOSRouter.from_yaml(tmp_path)

    test_cases = [
        "帮我写一段Python代码实现快速排序",
        "生成一张美丽的风景图片",
        "解释一下量子计算的基本原理",
        "帮我分析这段JavaScript代码的性能问题",
    ]

    print("=" * 60)
    print("LLMasRouter 测试")
    print("=" * 60)

    for query in test_cases:
        model, info = router.route_with_info(query, "gpt-5.5")
        print(f"\nQuery: {query[:50]}...")
        print(f"Routed to: {model}")
        print(f"Info: {info}")

finally:
    os.unlink(tmp_path)
