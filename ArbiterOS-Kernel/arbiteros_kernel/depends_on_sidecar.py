"""Optional sidecar LLM pass to declare depends_on for plain-text RESPOND instructions."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

import litellm

from arbiteros_kernel.instruction_depends_on import (
    DEPENDS_ON_STATIC_SCHEMA_HINT,
    build_allowed_depends_on_instruction_ids,
    build_depends_on_schema_description,
    build_step_catalog_with_previews,
    normalize_depends_on_declarations,
)

logger = logging.getLogger(__name__)

_SIDECAR_METADATA_FLAG = "arbiteros_depends_on_sidecar"
_DEFAULT_TIMEOUT_SECONDS = 45.0


def _litellm_config_yaml_path() -> Path:
    return Path(__file__).resolve().parent.parent / "litellm_config.yaml"


def _read_litellm_config_yaml() -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception:
        return {}
    path = _litellm_config_yaml_path()
    if not path.exists():
        return {}
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def read_depends_on_sidecar_enabled() -> bool:
    cfg = _read_litellm_config_yaml()
    arb_cfg = cfg.get("arbiteros_config") if isinstance(cfg, dict) else {}
    if not isinstance(arb_cfg, dict):
        return False
    block = arb_cfg.get("depends_on_sidecar")
    if not isinstance(block, dict):
        return False
    return block.get("enabled") is True


def is_depends_on_sidecar_internal_request(request_data: Any) -> bool:
    if not isinstance(request_data, dict):
        return False
    metadata = request_data.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return metadata.get(_SIDECAR_METADATA_FLAG) is True


def is_respond_text_instruction(instr: dict[str, Any]) -> bool:
    if not isinstance(instr, dict):
        return False
    if str(instr.get("instruction_type") or "").strip() != "RESPOND":
        return False
    content = instr.get("content")
    return isinstance(content, str) and bool(content.strip())


def _lookup_litellm_params_for_model(model: str) -> dict[str, Any]:
    requested = (model or "").strip()
    if not requested:
        return {}
    candidates = {requested}
    if ";" in requested:
        candidates.add(requested.split(";", 1)[0].strip())
    cfg = _read_litellm_config_yaml()
    model_list = cfg.get("model_list") if isinstance(cfg, dict) else None
    if not isinstance(model_list, list):
        return {}
    for entry in model_list:
        if not isinstance(entry, dict):
            continue
        model_name = str(entry.get("model_name") or "").strip()
        params = entry.get("litellm_params")
        if not isinstance(params, dict):
            continue
        upstream = str(params.get("model") or "").strip()
        if model_name not in candidates and upstream not in candidates:
            continue
        return dict(params)
    return {}


def build_sidecar_depends_on_entry_schema(allowed_ids: list[str]) -> dict[str, Any]:
    """Anthropic structured output rejects minimum/maximum on number fields."""
    instruction_id_schema: dict[str, Any] = {
        "type": "string",
        "description": "Prior instruction id from an [ARBITEROS_REF ...] marker.",
    }
    if allowed_ids:
        instruction_id_schema["enum"] = allowed_ids
    return {
        "type": "object",
        "properties": {
            "instruction_id": instruction_id_schema,
            "confidence": {
                "type": "number",
                "description": "How direct and necessary this causal link is (0-1).",
            },
            "counterfactual": {
                "type": "string",
                "description": (
                    "One short sentence: if that predecessor had not occurred or produced "
                    "its output, how would this step likely change?"
                ),
            },
        },
        "required": ["instruction_id", "confidence", "counterfactual"],
        "additionalProperties": False,
    }


def build_sidecar_response_format(
    instructions: list[dict[str, Any]],
    *,
    current_runtime_step: Optional[int] = None,
) -> dict[str, Any]:
    allowed_ids = build_allowed_depends_on_instruction_ids(
        instructions, current_runtime_step=current_runtime_step
    )
    items_schema = build_sidecar_depends_on_entry_schema(allowed_ids)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "depends_on_sidecar",
            "schema": {
                "type": "object",
                "properties": {
                    "depends_on": {
                        "type": "array",
                        "items": items_schema,
                        "description": DEPENDS_ON_STATIC_SCHEMA_HINT,
                    }
                },
                "required": ["depends_on"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def build_sidecar_messages(
    *,
    instructions: list[dict[str, Any]],
    respond_content: str,
    current_runtime_step: Optional[int] = None,
) -> list[dict[str, str]]:
    catalog = build_step_catalog_with_previews(instructions)
    rules = build_depends_on_schema_description(
        instructions, current_runtime_step=current_runtime_step
    )
    system_text = (
        "You declare causal depends_on for an assistant RESPOND step. "
        "Return JSON matching the response schema. "
        f"{rules}\n\n{catalog}"
    )
    user_text = (
        "Assistant RESPOND content for this turn:\n"
        f"{respond_content.strip()}\n\n"
        "Declare depends_on for this RESPOND step only."
    )
    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": user_text},
    ]


def parse_sidecar_depends_on_payload(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, str) or not content.strip():
        return []
    text = content.strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    return normalize_depends_on_declarations(parsed.get("depends_on"))


def _extract_completion_text(response: Any) -> str:
    if response is None:
        return ""
    choices = getattr(response, "choices", None)
    if isinstance(choices, list) and choices:
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is not None:
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content
        delta = getattr(choice, "delta", None)
        if delta is not None:
            content = getattr(delta, "content", None)
            if isinstance(content, str):
                return content
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                message = choice.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message["content"]
    return ""


def invoke_depends_on_sidecar(
    *,
    model: str,
    instructions: list[dict[str, Any]],
    respond_content: str,
    current_runtime_step: Optional[int] = None,
    trace_id: Optional[str] = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    completion_fn: Callable[..., Any] = litellm.completion,
    log_hook: Optional[Callable[[str, dict[str, Any]], None]] = None,
) -> list[dict[str, Any]]:
    """Call the same model to produce depends_on declarations; return [] on failure."""
    model_name = (model or "").strip()
    if not model_name or not respond_content.strip():
        return []

    messages = build_sidecar_messages(
        instructions=instructions,
        respond_content=respond_content,
        current_runtime_step=current_runtime_step,
    )
    response_format = build_sidecar_response_format(
        instructions, current_runtime_step=current_runtime_step
    )
    params = _lookup_litellm_params_for_model(model_name)
    kwargs: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "response_format": response_format,
        "metadata": {_SIDECAR_METADATA_FLAG: True},
        "timeout": timeout_seconds,
    }
    if log_hook is not None:
        log_hook(
            "depends_on_sidecar_request",
            {
                "trace_id": trace_id,
                "model": model_name,
                "upstream_model": params.get("model"),
                "message_count": len(messages),
                "allowed_id_count": len(
                    build_allowed_depends_on_instruction_ids(
                        instructions, current_runtime_step=current_runtime_step
                    )
                ),
            },
        )
    if params.get("api_key"):
        kwargs["api_key"] = params["api_key"]
    if params.get("api_base"):
        kwargs["api_base"] = params["api_base"]
    for key in ("temperature", "max_tokens"):
        if key in params:
            kwargs[key] = params[key]

    try:
        response = completion_fn(**kwargs)
    except Exception as exc:
        logger.warning(
            "depends_on sidecar failed trace_id=%s model=%s error=%s",
            trace_id or "",
            model_name,
            exc,
        )
        if log_hook is not None:
            log_hook(
                "depends_on_sidecar_error",
                {
                    "trace_id": trace_id,
                    "model": model_name,
                    "error": str(exc),
                },
            )
        return []

    text = _extract_completion_text(response)
    raw = parse_sidecar_depends_on_payload(text)
    if log_hook is not None:
        log_hook(
            "depends_on_sidecar",
            {
                "trace_id": trace_id,
                "model": model_name,
                "respond_content_preview": respond_content[:240],
                "raw_depends_on": raw,
                "response_preview": text[:500],
            },
        )
    return raw
