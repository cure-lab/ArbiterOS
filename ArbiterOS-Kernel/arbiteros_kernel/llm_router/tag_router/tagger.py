"""
Lightweight Tagger: 使用小型 LLM 从用户请求中提取语义标签。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


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
    Small LLM-based tagger that extracts semantic tags from user queries.

    Accepts a ``tag_schema`` dict (from the YAML ``tag_schema:`` section)
    and uses it to dynamically build the system prompt and JSON output schema.
    """

    def __init__(self, config: dict, tag_schema: dict):
        self.config = config
        self.model = config.get("model", "gpt-4o-mini")
        self.api_base = _resolve_env(config.get("api_base", ""))
        self.api_key = _resolve_env(config.get("api_key", ""))
        self.temperature = config.get("temperature", 0.0)
        self.max_tokens = config.get("max_tokens", 256)
        self.tag_schema = tag_schema

        self.system_prompt = _build_prompt_from_schema(tag_schema)
        self._json_schema = _build_json_schema_from_tag_schema(tag_schema)
        logger.debug(f"[Tagger] schema tags: {list(tag_schema.keys())}")

    def extract_tags(self, query: str) -> dict[str, Any]:
        """Extract tags from query. Raises exception if LLM fails."""
        return self._llm_extract_tags(query)

    def _log_tagger_call(self, query: str, result: dict[str, Any], raw_response: str, elapsed_ms: float, error: Optional[str] = None) -> None:
        """Log tagger request and response to JSONL for debugging."""
        import json as _json
        from datetime import datetime as _dt
        from pathlib import Path as _Path

        log_dir = _Path(__file__).resolve().parent.parent.parent.parent / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "tagger_calls.jsonl"

        entry = {
            "ts": _dt.now().isoformat(),
            "query": query,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "raw_response": raw_response,
            "tags": result,
            "elapsed_ms": round(elapsed_ms, 2),
        }
        if error:
            entry["error"] = error

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")

    def _llm_extract_tags(self, query: str) -> dict[str, Any]:
        import litellm
        import time as _time

        response_schema = self._json_schema

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": f"Classify this query: {query}"},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key

        # Only use strict json_schema if the model supports it
        strict_json = self.config.get("strict_json", False)
        if strict_json:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "query_tags",
                    "schema": response_schema,
                    "strict": True,
                },
            }

        t_start = _time.perf_counter()
        resp = litellm.completion(**kwargs)
        elapsed = (_time.perf_counter() - t_start) * 1000

        content = resp.choices[0].message.content
        raw_response = content or ""
        if not content:
            self._log_tagger_call(query, {}, raw_response, elapsed, error="empty response")
            raise RuntimeError(f"Tagger LLM returned empty content (model={self.model})")

        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        try:
            tags = json.loads(content)
        except json.JSONDecodeError as e:
            self._log_tagger_call(query, {}, raw_response, elapsed, error=f"JSON parse error: {e}")
            raise

        self._log_tagger_call(query, tags, raw_response, elapsed)
        logger.debug(f"[Tagger] LLM tags: {tags}")
        return tags
