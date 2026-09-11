from __future__ import annotations

from typing import Any

from arbiteros_kernel.content_scan import load_content_scan_config, redact_request
from arbiteros_kernel.policy_runtime import RUNTIME
from arbiteros_kernel.precall_policy.policy import PreCallPolicy


class ContentScanPrecallPolicy(PreCallPolicy):
    """Redact sensitive spans in every context message before the model sees them."""

    def check(
        self,
        instructions: list[dict[str, Any]],
        current_request: dict[str, Any],
        trace_id: str,
        **kwargs: Any,
    ):
        from arbiteros_kernel.precall_policy_check import PreCallPolicyCheckResult

        _ = instructions, kwargs
        from arbiteros_kernel.content_scan import content_scan_registry_enabled

        if not content_scan_registry_enabled():
            return PreCallPolicyCheckResult(modified=False, request=current_request)

        cfg = load_content_scan_config(RUNTIME.cfg.get("content_scan"))
        request, changed, hits = redact_request(
            current_request, cfg, trace_id=trace_id or ""
        )
        if not changed:
            return PreCallPolicyCheckResult(modified=False, request=current_request)

        try:
            RUNTIME.audit(
                phase="policy.content_scan",
                trace_id=trace_id or "",
                tool="@input",
                decision="REDACT",
                reason="sensitive content replaced in precall context",
                args={},
                extra={"hits": hits, "side": "input"},
            )
        except Exception:
            pass

        return PreCallPolicyCheckResult(modified=True, request=request)
