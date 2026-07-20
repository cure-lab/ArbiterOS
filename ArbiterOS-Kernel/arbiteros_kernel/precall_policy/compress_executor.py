"""Compression backends for Cost Doctor precall (rule / llm / truncate)."""

from __future__ import annotations

import os
from typing import Any, Callable

from flow_cost_doctor.openhands_adapter import _count_tokens
from flow_cost_doctor.runtime.rule_compress import (
    RuleCompressParams,
    compress_backend_from_rule_engine,
    compress_text_rule_based,
    rule_compress_params_from_rule_engine,
)

CompletionFn = Callable[..., Any]

_DEFAULT_MAX_ITERATIONS = 3
_COMPRESS_SYSTEM = (
    "You compress agent trace content for prompt budget. Preserve facts, file paths, "
    "errors, and decisions. Output ONLY the compressed text with no preamble."
)


def compress_max_iterations() -> int:
    raw = os.getenv("ARBITEROS_COST_DOWN_COMPRESS_MAX_ITERATIONS", "3").strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_ITERATIONS
    return max(1, min(value, 3))


def compress_model_name(fallback_model: str | None = None) -> str:
    model = os.getenv("ARBITEROS_COST_DOWN_COMPRESS_MODEL", "").strip()
    if model:
        return model
    if fallback_model and fallback_model.strip():
        return fallback_model.strip()
    return "gpt-4o-mini"


def resolve_compress_backend(rule_engine: dict[str, Any] | None = None) -> str:
    """Env ``ARBITEROS_COST_DOWN_COMPRESS_BACKEND`` overrides rule_engine."""
    env = os.getenv("ARBITEROS_COST_DOWN_COMPRESS_BACKEND", "").strip().lower()
    if env in {"rule", "llm", "truncate"}:
        return env
    return compress_backend_from_rule_engine(rule_engine)


def _length_ratio(original: str, compressed: str) -> float:
    if not original:
        return 1.0
    return len(compressed) / len(original)


def _token_ratio(original: str, compressed: str) -> float:
    orig_t = max(1, _count_tokens(original))
    comp_t = max(1, _count_tokens(compressed))
    return comp_t / orig_t


def _truncate_to_ratio(text: str, target_ratio: float) -> str:
    if target_ratio >= 1.0:
        return text
    target_len = max(1, int(len(text) * target_ratio))
    return text[:target_len]


def _extract_completion_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        message = getattr(choices[0], "message", None)
        if message is not None:
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content.strip()
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"].strip()
    return ""


def compress_text_with_llm(
    text: str,
    *,
    target_ratio: float,
    context_id: str,
    model: str | None = None,
    max_iterations: int | None = None,
    completion_fn: CompletionFn | None = None,
    cache: dict[str, str] | None = None,
) -> str:
    """Compress ``text`` toward ``target_ratio`` using up to ``max_iterations`` LLM passes."""
    if not text or not text.strip():
        return text
    if target_ratio >= 1.0:
        return text

    cache_key = f"llm:{context_id}:{target_ratio:.4f}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    max_iterations = max_iterations if max_iterations is not None else compress_max_iterations()
    target_ratio = max(0.01, min(float(target_ratio), 1.0))

    if completion_fn is None:
        try:
            import litellm

            completion_fn = litellm.completion
        except Exception:
            compressed = _truncate_to_ratio(text, target_ratio)
            if cache is not None:
                cache[cache_key] = compressed
            return compressed

    model_name = compress_model_name(model)
    current = text
    for iteration in range(max_iterations):
        ratio_now = _token_ratio(text, current)
        if ratio_now <= target_ratio * 1.05:
            break
        gap = ratio_now / target_ratio if target_ratio > 0 else ratio_now
        user_prompt = (
            f"Target length ratio: {target_ratio:.0%} of original.\n"
            f"Current ratio: {ratio_now:.0%}.\n"
            f"Compress further by approximately {gap:.1f}x while keeping critical facts.\n\n"
            f"--- CONTENT ---\n{current}"
        )
        if iteration == 0:
            user_prompt = (
                f"Summarize the following content to about {target_ratio:.0%} of its length. "
                f"Keep errors, paths, command outputs, and decisions.\n\n"
                f"--- CONTENT ---\n{text}"
            )
        try:
            response = completion_fn(
                model=model_name,
                messages=[
                    {"role": "system", "content": _COMPRESS_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=max(256, int(_count_tokens(text) * target_ratio * 2)),
                metadata={"arbiteros_cost_down_compress": True, "context_id": context_id},
            )
            candidate = _extract_completion_text(response)
        except Exception:
            candidate = ""
        if not candidate.strip():
            current = _truncate_to_ratio(text, target_ratio)
            break
        current = candidate.strip()
        if _length_ratio(text, current) <= target_ratio * 1.05:
            break

    if _token_ratio(text, current) > target_ratio * 1.1:
        current = _truncate_to_ratio(current, target_ratio)

    if cache is not None:
        cache[cache_key] = current
    return current


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _log_rule_compress(
    *,
    context_id: str,
    actions: tuple[str, ...] | list[str],
    tokens_before: int,
    tokens_after: int,
    target_ratio: float,
    fallback_truncate: bool,
) -> None:
    """Print a one-line terminal log when rule-based compression fires."""
    if not _env_flag("ARBITEROS_COST_DOWN_COMPRESS_LOG", True):
        return
    keep_ratio = tokens_after / max(1, tokens_before)
    saved_ratio = 1.0 - keep_ratio
    steps = ",".join(actions) if actions else "passthrough"
    fallback = " truncate_fallback=1" if fallback_truncate else ""
    print(
        "[CostDoctor][rule-compress] "
        f"context={context_id} "
        f"steps={steps} "
        f"tokens_before={tokens_before} "
        f"tokens_after={tokens_after} "
        f"keep_ratio={keep_ratio:.1%} "
        f"saved_ratio={saved_ratio:.1%} "
        f"target_ratio={float(target_ratio):.1%}"
        f"{fallback}",
        flush=True,
    )


def compress_text(
    text: str,
    *,
    target_ratio: float,
    context_id: str,
    backend: str | None = None,
    rule_engine: dict[str, Any] | None = None,
    rule_params: RuleCompressParams | None = None,
    progress_signal: str | None = None,
    source_type: str | None = None,
    model: str | None = None,
    max_iterations: int | None = None,
    completion_fn: CompletionFn | None = None,
    cache: dict[str, str] | None = None,
) -> str:
    """Dispatch midband COMPRESS to rule / llm / truncate backends.

    Only call this for contexts already decided as COMPRESS by ratio policy.
    """
    if not text or not text.strip():
        return text
    if target_ratio >= 1.0:
        return text

    resolved_backend = (backend or resolve_compress_backend(rule_engine)).strip().lower()
    cache_key = f"{resolved_backend}:{context_id}:{float(target_ratio):.4f}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    if resolved_backend == "truncate":
        compressed = _truncate_to_ratio(text, target_ratio)
    elif resolved_backend == "llm":
        compressed = compress_text_with_llm(
            text,
            target_ratio=target_ratio,
            context_id=context_id,
            model=model,
            max_iterations=max_iterations,
            completion_fn=completion_fn,
            cache=None,
        )
    else:
        params = rule_params or rule_compress_params_from_rule_engine(rule_engine)
        result = compress_text_rule_based(
            text,
            target_ratio=target_ratio,
            progress_signal=progress_signal,
            source_type=source_type,
            params=params,
            reason=f"midband_ratio_compress:{context_id}",
        )
        compressed = result.text
        fallback_truncate = False
        # Guarantee approximate target if rules could not shrink enough.
        if len(text) > 0 and len(compressed) > int(len(text) * target_ratio * 1.15):
            compressed = _truncate_to_ratio(compressed, target_ratio)
            fallback_truncate = True
        tokens_before = max(1, _count_tokens(text))
        tokens_after = max(1, _count_tokens(compressed))
        if result.changed or fallback_truncate or compressed != text:
            actions = list(result.actions)
            if fallback_truncate:
                actions.append("forced_truncate")
            _log_rule_compress(
                context_id=context_id,
                actions=tuple(actions),
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                target_ratio=target_ratio,
                fallback_truncate=fallback_truncate,
            )

    if cache is not None:
        cache[cache_key] = compressed
    return compressed
