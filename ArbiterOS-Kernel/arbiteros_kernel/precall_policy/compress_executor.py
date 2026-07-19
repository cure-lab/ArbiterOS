"""LLM-based semantic compression for Cost Doctor precall (ratio-aware, max iterations)."""

from __future__ import annotations

import os
from typing import Any, Callable

from flow_cost_doctor.openhands_adapter import _count_tokens

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

    cache_key = f"{context_id}:{target_ratio:.4f}"
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
