"""
Feedback Daemon for TagRouter

Asynchronously processes LiteLLM logs to:
1. Parse completed conversation rounds
2. Judge success using LLM
3. Update Beta parameters via TagRouter.record_outcome()

Usage:
    python -m arbiteros_kernel.llm_router.feedback_daemon --config configs/tag_router.yaml
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Round:
    """A complete conversation round."""
    round_id: str
    messages: list[dict]
    routing_info: dict  # {node_id, model_id, selected_model, rule_path}
    tokens: dict  # {input_tokens, output_tokens, total_tokens}
    cost: float
    latency_ms: float
    timestamp: str
    raw_log_lines: list[str] = field(default_factory=list)

    def is_complete(self) -> bool:
        """Check if round is complete (last message has no tool calls)."""
        if not self.messages:
            return False
        last_msg = self.messages[-1]
        if last_msg.get("role") != "assistant":
            return False
        # Check if last message has no tool_calls
        return not last_msg.get("tool_calls")


class LogParser:
    """Parses structured JSON logs from LiteLLM (api_calls.jsonl and precall.jsonl)."""

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.api_calls_log = log_dir / "api_calls.jsonl"
        self.precall_log = log_dir / "precall.jsonl"
        self._last_positions = {
            "api_calls": 0,
            "precall": 0,
        }
        self._trace_cache: dict[str, dict] = {}  # trace_id -> accumulated data

    async def parse_new_rounds(self) -> AsyncIterator[Round]:
        """
        Parse new log lines from LiteLLM logs.

        Reads from:
        - api_calls.jsonl: hook events (token_usage_round, post_call_success)
        - precall.jsonl: request payloads (messages, model, metadata)

        Matches by trace_id to build complete rounds.
        """
        # Parse precall.jsonl for request messages and routing info
        await self._parse_precall_logs()

        # Parse api_calls.jsonl for completions
        async for round_obj in self._parse_api_calls_logs():
            yield round_obj

    async def _parse_precall_logs(self):
        """Parse precall.jsonl to extract messages and routing info."""
        if not self.precall_log.exists():
            return

        with open(self.precall_log, "r") as f:
            f.seek(self._last_positions["precall"])
            content = f.read()
            self._last_positions["precall"] = f.tell()

            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue

                try:
                    log_entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Extract trace_id and request data
                payload = log_entry.get("payload", {})
                metadata = payload.get("metadata", {})
                trace_id = metadata.get("arbiteros_trace_id") or metadata.get("trace_id")

                if not trace_id:
                    continue

                # Store request data by trace_id
                if trace_id not in self._trace_cache:
                    self._trace_cache[trace_id] = {}

                self._trace_cache[trace_id]["messages"] = payload.get("messages", [])
                self._trace_cache[trace_id]["model"] = payload.get("model", "unknown")
                self._trace_cache[trace_id]["timestamp"] = log_entry.get("ts", "")
                self._trace_cache[trace_id]["metadata"] = metadata

    async def _parse_api_calls_logs(self) -> AsyncIterator[Round]:
        """
        Parse api_calls.jsonl. post_call_success now includes usage/cost_usd directly.

        A complete round = consecutive LLM calls ending with a non-tool-call response.
        """
        if not self.api_calls_log.exists():
            return

        with open(self.api_calls_log, "r") as f:
            f.seek(self._last_positions["api_calls"])
            content = f.read()
            self._last_positions["api_calls"] = f.tell()

            round_seq = 0

            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue

                try:
                    log_entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                hook = log_entry.get("hook")
                if hook != "post_call_success":
                    continue

                data = log_entry.get("data", {})
                trace_id = data.get("trace_id")
                round_seq += 1

                if not trace_id or trace_id not in self._trace_cache:
                    continue

                cached = self._trace_cache[trace_id]
                response = data.get("response", {})

                # Append assistant response to messages
                messages = list(cached.get("messages", []))
                messages.append({
                    "role": "assistant",
                    "content": response.get("content", ""),
                    "tool_calls": response.get("tool_calls"),
                })
                cached["messages"] = messages

                # Check if round is complete (no tool calls)
                if response.get("tool_calls"):
                    continue

                # Extract token usage and cost (now embedded in post_call_success)
                usage = data.get("usage", {})
                tokens = {
                    "input_tokens": usage.get("prompt_tokens", 0),
                    "output_tokens": usage.get("completion_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                }
                cost = data.get("cost_usd") or tokens["total_tokens"] * 0.00001

                round_obj = Round(
                    round_id=f"{trace_id}_round{round_seq}",
                    messages=messages,
                    routing_info=self._extract_routing_info(cached.get("metadata", {})),
                    tokens=tokens,
                    cost=cost,
                    latency_ms=0.0,
                    timestamp=log_entry.get("ts", ""),
                )

                yield round_obj

    def _extract_routing_info(self, metadata: dict) -> dict:
        """Extract routing info from metadata if available."""
        # Check if routing info was stored in metadata
        routing_info = metadata.get("routing_info", {})

        # Fallback: construct from available data
        if not routing_info:
            routing_info = {
                "node_id": "_default_",
                "model_id": "unknown",
                "selected_model": "unknown",
                "rule_path": "default",
            }
        else:
            # Ensure node_id and model_id are set from routing_info
            # routing_info contains: node_id, selected (model), rule_path, tags, scores
            if "node_id" not in routing_info:
                routing_info["node_id"] = "_default_"
            if "model_id" not in routing_info:
                # Use 'selected' field as model_id
                routing_info["model_id"] = routing_info.get("selected", "unknown")
            if "selected_model" not in routing_info:
                routing_info["selected_model"] = routing_info.get("selected", "unknown")

        return routing_info


class SuccessJudge:
    """Uses LLM to judge if a conversation round was successful."""

    def __init__(self, judge_model: str = "gpt-4o-mini", api_key: Optional[str] = None):
        self.judge_model = judge_model
        self.api_key = api_key

    async def judge(self, round: Round) -> tuple[bool, str]:
        """
        Judge if the round was successful.

        Returns:
            (success: bool, reason: str)
        """
        try:
            import litellm

            # Build evaluation prompt
            messages_summary = self._summarize_messages(round.messages)

            prompt = f"""Evaluate if this conversation round was successful.

Conversation:
{messages_summary}

Criteria:
- Did the assistant provide a helpful, relevant response?
- Was the task completed or meaningful progress made?
- Were there any errors or failures?

Return ONLY a JSON object with this format:
{{"success": true/false, "reason": "brief explanation"}}
"""

            kwargs = {
                "model": self.judge_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 200,
            }

            if self.api_key:
                kwargs["api_key"] = self.api_key

            # Use litellm proxy if available
            if not self.api_key:
                kwargs["api_base"] = "http://localhost:4000"

            response = await litellm.acompletion(**kwargs)
            content = response.choices[0].message.content

            # Parse JSON response
            result = json.loads(content)
            success = result.get("success", False)
            reason = result.get("reason", "no reason provided")

            return success, reason

        except Exception as e:
            logger.error(f"Judge failed for round {round.round_id}: {e}")
            # Default to success if judge fails (conservative)
            return True, f"judge_error: {e}"

    def _summarize_messages(self, messages: list[dict], max_length: int = 2000) -> str:
        """Summarize messages for the judge prompt."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Handle multi-modal content
                content = str(content)
            lines.append(f"{role}: {content[:500]}")

        summary = "\n".join(lines)
        if len(summary) > max_length:
            summary = summary[:max_length] + "...(truncated)"
        return summary


class FeedbackUpdater:
    """Updates TagRouter parameters based on feedback."""

    def __init__(self, router_config_path: Path):
        from arbiteros_kernel.llm_router.tag_router import TagRouter

        self.router = TagRouter.from_yaml(str(router_config_path))
        self.update_count = 0

    async def update(self, round: Round, success: bool):
        """Update Beta parameters for this round."""
        routing_info = round.routing_info
        node_id = routing_info.get("node_id")
        model_id = routing_info.get("model_id")

        if not node_id or not model_id:
            logger.warning(f"Missing routing info in round {round.round_id}")
            return

        # Update parameters
        self.router.record_outcome(
            node_id=node_id,
            model_id=model_id,
            success=success,
            cost=round.cost,
            latency_ms=round.latency_ms,
        )

        self.update_count += 1
        logger.info(
            f"Updated round {round.round_id}: node={node_id}, model={model_id}, "
            f"success={success}, cost={round.cost:.6f}"
        )

    def get_stats(self) -> dict:
        """Get current router statistics."""
        return self.router.get_stats()


class FeedbackDaemon:
    """Main daemon process."""

    def __init__(
        self,
        log_dir: Path,
        router_config_path: Path,
        poll_interval: float = 5.0,
        judge_model: str = "gpt-4o-mini",
    ):
        self.log_dir = log_dir
        self.poll_interval = poll_interval

        self.parser = LogParser(log_dir)
        self.judge = SuccessJudge(judge_model=judge_model)
        self.updater = FeedbackUpdater(router_config_path)

        self.running = False
        self.stats = {
            "rounds_processed": 0,
            "rounds_succeeded": 0,
            "rounds_failed": 0,
        }

    async def run(self):
        """Main event loop."""
        self.running = True
        logger.info(f"Feedback daemon started, watching {self.log_dir}")

        while self.running:
            try:
                await self._process_new_rounds()
                await asyncio.sleep(self.poll_interval)
            except Exception as e:
                logger.error(f"Error in daemon loop: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval)

    async def _process_new_rounds(self):
        """Process all new rounds from log."""
        async for round in self.parser.parse_new_rounds():
            try:
                # Judge success
                success, reason = await self.judge.judge(round)

                # Update parameters
                await self.updater.update(round, success)

                # Update stats
                self.stats["rounds_processed"] += 1
                if success:
                    self.stats["rounds_succeeded"] += 1
                else:
                    self.stats["rounds_failed"] += 1

                logger.debug(
                    f"Processed round {round.round_id}: success={success}, reason={reason}"
                )

            except Exception as e:
                logger.error(f"Error processing round {round.round_id}: {e}")

    def stop(self):
        """Stop the daemon."""
        self.running = False
        logger.info("Feedback daemon stopped")

    def get_stats(self) -> dict:
        """Get daemon statistics."""
        return {
            **self.stats,
            "router_stats": self.updater.get_stats(),
        }


async def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="TagRouter Feedback Daemon")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("log"),
        help="Directory containing LiteLLM logs (api_calls.jsonl, precall.jsonl)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to TagRouter YAML config",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Log polling interval in seconds",
    )
    parser.add_argument(
        "--judge-model",
        type=str,
        default="gpt-4o-mini",
        help="Model to use for success judging",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Create and run daemon
    daemon = FeedbackDaemon(
        log_dir=args.log_dir,
        router_config_path=args.config,
        poll_interval=args.poll_interval,
        judge_model=args.judge_model,
    )

    try:
        await daemon.run()
    except KeyboardInterrupt:
        daemon.stop()
        logger.info("Daemon interrupted by user")


if __name__ == "__main__":
    asyncio.run(main())
