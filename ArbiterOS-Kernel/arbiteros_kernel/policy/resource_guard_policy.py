from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from arbiteros_kernel.policy_check import PolicyCheckResult
from arbiteros_kernel.policy_runtime import get_runtime

from .policy import Policy


@dataclass
class ResourceViolation:
    rule_id: str
    metric: str
    observed: float
    threshold: float
    detail: str


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_num(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}"


def _resource_guard_cfg() -> Dict[str, Any]:
    runtime = get_runtime()
    cfg = getattr(runtime, "cfg", {})
    if not isinstance(cfg, dict):
        return {}
    block = cfg.get("resource_guard")
    return block if isinstance(block, dict) else {}


def _friendly_block_message(
    *,
    trace_id: str,
    instruction_count: int,
    instruction_bytes: int,
    violations: List[ResourceViolation],
) -> str:
    lines = [
        "## ⚠️ 资源保护策略拦截确认",
        "",
        "### 1. 触发概览",
        f"- trace_id: `{trace_id}`",
        f"- 当前 instruction 总数: {instruction_count}",
        f"- 当前 instruction 总字节: {instruction_bytes} bytes",
        "",
        "### 2. 超限指标",
    ]
    for v in violations:
        lines.append(
            f"- [{v.rule_id}] `{v.metric}` 超阈值："
            f"{_fmt_num(v.observed)} > {_fmt_num(v.threshold)}（{v.detail}）"
        )
    lines.extend(
        [
            "",
            "### 3. 处理说明",
            "- 当前响应已被资源保护策略暂停，请先缩小上下文或开始新 trace 后重试。",
        ]
    )
    return "\n".join(lines)


class ResourceGuardPolicy(Policy):
    """Block when trace resource metrics exceed configured thresholds."""

    def check(
        self,
        instructions: List[Dict[str, Any]],
        current_response: Dict[str, Any],
        latest_instructions: List[Dict[str, Any]],
        trace_id: str,
        **kwargs: Any,
    ) -> PolicyCheckResult:
        cfg = _resource_guard_cfg()
        runtime_ctx = kwargs.get("policy_runtime_context")
        if not isinstance(runtime_ctx, dict):
            runtime_ctx = {}
        rg = runtime_ctx.get("resource_guard")
        if not isinstance(rg, dict):
            return PolicyCheckResult(
                modified=False,
                response=current_response,
                error_type=None,
                inactivate_error_type=None,
            )

        total_tokens = _to_float(rg.get("total_tokens"), 0.0)
        elapsed_seconds = _to_float(rg.get("elapsed_seconds"), 0.0)
        instruction_bytes = _to_float(rg.get("instruction_bytes"), 0.0)
        instruction_count = int(_to_float(rg.get("instruction_count"), 0.0))

        max_total_tokens = _to_float(cfg.get("max_total_tokens"), 0.0)
        max_trace_seconds = _to_float(cfg.get("max_trace_seconds"), 0.0)
        max_instruction_bytes = _to_float(cfg.get("max_instruction_bytes"), 0.0)

        violations: List[ResourceViolation] = []
        if max_total_tokens > 0 and total_tokens > max_total_tokens:
            violations.append(
                ResourceViolation(
                    rule_id="RG-001",
                    metric="trace_total_tokens",
                    observed=total_tokens,
                    threshold=max_total_tokens,
                    detail="累计 token 预算",
                )
            )
        if max_trace_seconds > 0 and elapsed_seconds > max_trace_seconds:
            violations.append(
                ResourceViolation(
                    rule_id="RG-002",
                    metric="trace_elapsed_seconds",
                    observed=elapsed_seconds,
                    threshold=max_trace_seconds,
                    detail="trace 运行时长预算（秒）",
                )
            )
        if max_instruction_bytes > 0 and instruction_bytes > max_instruction_bytes:
            violations.append(
                ResourceViolation(
                    rule_id="RG-003",
                    metric="trace_instruction_bytes",
                    observed=instruction_bytes,
                    threshold=max_instruction_bytes,
                    detail="instruction 内存预算（bytes）",
                )
            )

        if not violations:
            return PolicyCheckResult(
                modified=False,
                response=current_response,
                error_type=None,
                inactivate_error_type=None,
            )

        error_text = _friendly_block_message(
            trace_id=trace_id,
            instruction_count=instruction_count,
            instruction_bytes=int(instruction_bytes),
            violations=violations,
        )
        response = dict(current_response)
        response["tool_calls"] = None
        response["function_call"] = None
        response["content"] = error_text

        runtime = get_runtime()
        try:
            runtime.audit(
                phase="policy.resource_guard",
                trace_id=trace_id,
                tool="@resource_guard",
                decision="BLOCK",
                reason="; ".join(
                    f"{v.rule_id}:{v.metric}={_fmt_num(v.observed)}>{_fmt_num(v.threshold)}"
                    for v in violations
                ),
                args={},
                extra={
                    "violations": [
                        {
                            "rule_id": v.rule_id,
                            "metric": v.metric,
                            "observed": v.observed,
                            "threshold": v.threshold,
                            "detail": v.detail,
                        }
                        for v in violations
                    ],
                    "instruction_count": instruction_count,
                    "instruction_bytes": int(instruction_bytes),
                    "latest_instruction_count": len(latest_instructions or []),
                },
            )
        except Exception:
            pass

        return PolicyCheckResult(
            modified=True,
            response=response,
            error_type=error_text,
            inactivate_error_type=None,
        )
