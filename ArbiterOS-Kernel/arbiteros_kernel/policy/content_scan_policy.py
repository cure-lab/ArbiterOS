from __future__ import annotations

from typing import Any

from arbiteros_kernel.content_scan import load_content_scan_config, redact_response
from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import RUNTIME

from .policy import Policy


def _hit_count(hits: list[dict[str, Any]]) -> int:
    total = 0
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        count = hit.get("count")
        if isinstance(count, int) and count > 0:
            total += count
        else:
            total += 1
    return total


def _friendly_block_message(*, replacement: str, hits: list[dict[str, Any]]) -> str:
    count = _hit_count(hits)
    return "\n".join(
        [
            "## ⚠️ 敏感信息扫描拦截确认",
            "",
            "### 1. 触发概览",
            "- 当前模型输出中检测到敏感信息（如电话号码、身份证号等）。",
            f"- 命中次数: {count}",
            "",
            "### 2. 处理说明",
            f"- 选择 Y：用 `{replacement}` 替换敏感片段后放行。",
            "- 选择 N：放行未打码的原文。",
        ]
    )


class ContentScanPolicy(Policy):
    """Redact sensitive spans in the current model output (RESPOND + tool args)."""

    def check(
        self,
        instructions: list[dict[str, Any]],
        current_response: dict[str, Any],
        latest_instructions: list[dict[str, Any]],
        trace_id: str,
        **kwargs: Any,
    ) -> PolicyCheckResult:
        _ = instructions, latest_instructions, kwargs
        from arbiteros_kernel.content_scan import content_scan_registry_enabled

        if not content_scan_registry_enabled():
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        cfg = load_content_scan_config(RUNTIME.cfg.get("content_scan"))
        response, changed, hits = redact_response(
            current_response, cfg, trace_id=trace_id or ""
        )
        if not changed:
            return PolicyCheckResult(modified=False, response=current_response, error_type=None)

        error_text = _friendly_block_message(replacement=cfg.replacement, hits=hits)
        try:
            RUNTIME.audit(
                phase="policy.content_scan",
                trace_id=trace_id or "",
                tool="@output",
                decision="BLOCK",
                reason="sensitive content replaced in current output",
                args={},
                extra={"hits": hits, "side": "output"},
            )
        except Exception:
            pass

        return PolicyCheckResult(modified=True, response=response, error_type=error_text)
