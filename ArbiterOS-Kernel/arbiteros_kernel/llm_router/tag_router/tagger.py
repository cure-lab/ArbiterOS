"""
Lightweight Tagger: 使用小型 LLM 从用户请求中提取语义标签。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


from dataclasses import dataclass, field
from typing import List


@dataclass
class TagResult:
    """Result from the tagger."""
    tags: List[str]
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)

_DEFAULT_TAGGER_PROMPT = """You are a query classifier. Analyze the user's request and extract semantic tags.

Return ONLY a JSON object with these fields:
- domain: one of [code, math, writing, vision, general, data, reasoning]
- complexity: one of [low, medium, high]
- language: the primary language (e.g., "chinese", "english", "mixed")
- requires_vision: true/false
- is_creative: true/false

Example output:
{"domain": "code", "complexity": "high", "language": "english", "requires_vision": false, "is_creative": false}
"""


def _build_prompt_from_schema(tag_schema: dict) -> str:
    """Dynamically build tagger system prompt from tag_schema definition."""
    lines = [
        "You are a query classifier. Analyze the user's request and extract semantic tags.",
        "",
        "Return ONLY a JSON object with these fields:",
    ]
    example: dict = {}
    for tag_name, spec in tag_schema.items():
        tag_type = spec.get("type", "string")
        desc = spec.get("description", "").strip().replace("\n", " ")
        required = spec.get("required", True)
        req_label = "" if required else " (optional)"

        if tag_type == "enum":
            values = spec.get("values", [])
            vals_str = ", ".join(str(v).split("#")[0].strip() for v in values)
            lines.append(f"- {tag_name}{req_label}: one of [{vals_str}]  — {desc}")
            example[tag_name] = str(values[0]).split("#")[0].strip() if values else ""
        elif tag_type == "bool":
            lines.append(f"- {tag_name}{req_label}: true/false  — {desc}")
            example[tag_name] = False
        elif tag_type == "int":
            lines.append(f"- {tag_name}{req_label}: integer  — {desc}")
            example[tag_name] = 0
        else:  # string
            lines.append(f"- {tag_name}{req_label}: string  — {desc}")
            example[tag_name] = "english"

    import json as _json
    lines.append("")
    lines.append("Example output:")
    lines.append(_json.dumps(example, ensure_ascii=False))
    return "\n".join(lines)


def _build_json_schema_from_tag_schema(tag_schema: dict) -> dict:
    """Build a JSON Schema object for structured LLM output from tag_schema."""
    properties: dict = {}
    required_fields: list = []

    for tag_name, spec in tag_schema.items():
        tag_type = spec.get("type", "string")
        desc = spec.get("description", "").strip().replace("\n", " ")

        if tag_type == "enum":
            raw_values = spec.get("values", [])
            # Strip inline YAML comments (e.g. "code  # Programming" → "code")
            values = [str(v).split("#")[0].strip() for v in raw_values]
            prop = {"type": "string", "enum": values}
        elif tag_type == "bool":
            prop = {"type": "boolean"}
        elif tag_type == "int":
            prop = {"type": "integer"}
        else:
            prop = {"type": "string"}

        if desc:
            prop["description"] = desc
        properties[tag_name] = prop

        if spec.get("required", True):
            required_fields.append(tag_name)

    return {
        "type": "object",
        "properties": properties,
        "required": required_fields,
        "additionalProperties": False,
    }


def _resolve_env(value: str) -> str:
    """Resolve ${VAR} environment variable references."""
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        var_name = value[2:-1]
        return os.environ.get(var_name, value)
    return value


class LightweightTagger:
    """
    Small LLM-based tagger that extracts contextual tags from user queries.
    Falls back to rule-based tagging if LLM call fails.

    Accepts an optional ``tag_schema`` dict (from the YAML ``tag_schema:`` section)
    and uses it to dynamically build the system prompt and JSON output schema.
    When no schema is provided, falls back to built-in defaults.
    """

    def __init__(self, config: dict, tag_schema: Optional[dict] = None):
        self.config = config
        self.model = config.get("model", "gpt-4o-mini")
        self.api_base = _resolve_env(config.get("api_base", ""))
        self.api_key = _resolve_env(config.get("api_key", ""))
        self.temperature = config.get("temperature", 0.0)
        self.max_tokens = config.get("max_tokens", 256)
        self.tag_schema = tag_schema or {}

        # Build system prompt: use YAML-defined tag_schema if available, else static default
        if self.tag_schema:
            self.system_prompt = _build_prompt_from_schema(self.tag_schema)
            self._json_schema = _build_json_schema_from_tag_schema(self.tag_schema)
        else:
            self.system_prompt = config.get("system_prompt", _DEFAULT_TAGGER_PROMPT)
            self._json_schema = {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "enum": ["code", "math", "writing", "vision", "general", "data", "reasoning"]},
                    "complexity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "language": {"type": "string"},
                    "requires_vision": {"type": "boolean"},
                    "is_creative": {"type": "boolean"},
                },
                "required": ["domain", "complexity", "language", "requires_vision", "is_creative"],
                "additionalProperties": False,
            }
        logger.debug(f"[Tagger] schema tags: {list(self.tag_schema.keys()) or 'built-in defaults'}")

    def extract_tags(self, query: str) -> dict[str, Any]:
        """Extract tags from query. Falls back to rule-based if LLM fails."""
        try:
            return self._llm_extract_tags(query)
        except Exception as e:
            logger.warning(f"[Tagger] LLM tagging failed, using rule-based fallback: {e}")
            return self._rule_based_tags(query)

    def _llm_extract_tags(self, query: str) -> dict[str, Any]:
        import litellm

        response_schema = self._json_schema

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"Classify this query: {query}"},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "query_tags",
                    "schema": response_schema,
                    "strict": True,
                },
            },
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key

        resp = litellm.completion(**kwargs)
        content = resp.choices[0].message.content
        tags = json.loads(content)
        logger.debug(f"[Tagger] LLM tags: {tags}")
        return tags

    def _rule_based_tags(self, query: str) -> dict[str, Any]:
        """Simple keyword-based fallback tagging."""
        q = query.lower()
        domain = "general"
        if any(w in q for w in ["code", "python", "javascript", "debug", "function", "algorithm", "program", "代码"]):
            domain = "code"
        elif any(w in q for w in ["image", "picture", "photo", "draw", "vision", "图片", "图像"]):
            domain = "vision"
        elif any(w in q for w in ["math", "calculate", "equation", "数学", "计算"]):
            domain = "math"
        elif any(w in q for w in ["write", "essay", "story", "poem", "写作", "文章"]):
            domain = "writing"
        elif any(w in q for w in ["data", "analysis", "statistics", "数据"]):
            domain = "data"

        complexity = "medium"
        if len(query) < 50:
            complexity = "low"
        elif len(query) > 300 or any(w in q for w in ["complex", "advanced", "detailed", "复杂"]):
            complexity = "high"

        # Detect language
        has_chinese = any('一' <= c <= '鿿' for c in query)
        language = "chinese" if has_chinese else "english"

        return {
            "domain": domain,
            "complexity": complexity,
            "language": language,
            "requires_vision": domain == "vision",
            "is_creative": domain == "writing",
        }
