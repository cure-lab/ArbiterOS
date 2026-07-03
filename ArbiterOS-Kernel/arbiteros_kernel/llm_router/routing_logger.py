"""
Routing Logger for LiteLLM Callback

Logs routing decisions to a structured JSON file for feedback daemon consumption.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RoutingLogger:
    """Logs routing decisions to JSON lines format."""

    def __init__(self, log_path: Path | str = "logs/litellm_routing.jsonl"):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log_pre_call(
        self,
        request_id: str,
        messages: list[dict],
        routing_info: dict,
        timestamp: Optional[str] = None,
    ):
        """Log before calling the model."""
        entry = {
            "event": "pre_call",
            "request_id": request_id,
            "timestamp": timestamp or datetime.utcnow().isoformat(),
            "messages": self._sanitize_messages(messages),
            "routing_info": routing_info,
        }
        self._write_log(entry)

    def log_post_call(
        self,
        request_id: str,
        usage: dict,
        cost: float,
        latency_ms: float,
        model: str,
        timestamp: Optional[str] = None,
    ):
        """Log after model completion."""
        entry = {
            "event": "post_call",
            "request_id": request_id,
            "timestamp": timestamp or datetime.utcnow().isoformat(),
            "model": model,
            "usage": usage,
            "cost": cost,
            "latency_ms": latency_ms,
        }
        self._write_log(entry)

    def _sanitize_messages(self, messages: list[dict]) -> list[dict]:
        """Sanitize messages for logging (truncate long content)."""
        sanitized = []
        for msg in messages:
            msg_copy = dict(msg)
            content = msg_copy.get("content")
            if isinstance(content, str) and len(content) > 1000:
                msg_copy["content"] = content[:1000] + "...(truncated)"
            sanitized.append(msg_copy)
        return sanitized

    def _write_log(self, entry: dict):
        """Write a log entry as JSON line."""
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to write routing log: {e}")


# Global singleton instance
_routing_logger: Optional[RoutingLogger] = None


def get_routing_logger() -> RoutingLogger:
    """Get or create the global routing logger."""
    global _routing_logger
    if _routing_logger is None:
        _routing_logger = RoutingLogger()
    return _routing_logger


def log_routing_decision(
    request_id: str,
    messages: list[dict],
    routing_result: dict,
) -> None:
    """
    Log a routing decision from LiteLLM callback.

    Args:
        request_id: Unique request ID
        messages: Request messages
        routing_result: Dict containing:
            - model_name: Selected model
            - node_id: DSL rule node ID
            - rule_path: Human-readable rule path
            - scores: Thompson sampling scores (optional)
    """
    logger = get_routing_logger()

    routing_info = {
        "node_id": routing_result.get("node_id", "_default_"),
        "model_id": routing_result.get("model_name", "unknown"),
        "selected_model": routing_result.get("model_name", "unknown"),
        "rule_path": routing_result.get("rule_path", "default"),
        "scores": routing_result.get("scores", {}),
    }

    logger.log_pre_call(
        request_id=request_id,
        messages=messages,
        routing_info=routing_info,
    )


def log_completion_result(
    request_id: str,
    usage: dict,
    cost: float,
    latency_ms: float,
    model: str,
) -> None:
    """
    Log completion results from LiteLLM callback.

    Args:
        request_id: Unique request ID
        usage: Token usage dict {prompt_tokens, completion_tokens, total_tokens}
        cost: Actual cost in USD
        latency_ms: Latency in milliseconds
        model: Model used
    """
    logger = get_routing_logger()
    logger.log_post_call(
        request_id=request_id,
        usage=usage,
        cost=cost,
        latency_ms=latency_ms,
        model=model,
    )
