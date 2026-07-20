"""Apply Cost Doctor context actions to LiteLLM request payloads."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from flow_cost_doctor.cost_down import OptimizationStrategy
from flow_cost_doctor.runtime.apply import applied_prompt_tokens
from flow_cost_doctor.runtime.payload_index import PayloadIndex

from arbiteros_kernel.precall_policy.compress_executor import compress_text

_REF_MARKER_RE = re.compile(
    r"^\[ARBITEROS_REF id=([^\s\]]+) kind=([A-Z_]+)\]\s*\n?",
)

# Tools JSON schema is static agent config — not yet in FCD context_id model (see docs).
_UNIMPLEMENTED_CARRIERS = ("tools",)


def apply_context_actions_to_request(
    request: dict[str, Any],
    *,
    instruction_to_context: dict[str, str],
    context_actions: dict[str, dict[str, Any]],
    strategies_by_context: dict[str, OptimizationStrategy],
    step_index: int,
    stage: str,
    phase: str,
    payload_index: PayloadIndex | None = None,
    compress_cache: dict[str, str] | None = None,
    upstream_model: str | None = None,
    context_aliases: dict[str, str] | None = None,
    rule_engine: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Mutate request according to phase and decided context actions."""
    if phase == "A":
        return request, False

    modified = False
    updated = copy.deepcopy(request)
    index = payload_index or PayloadIndex(instruction_to_context=instruction_to_context)
    ctx = _MutationContext(
        instruction_to_context=instruction_to_context,
        context_actions=context_actions,
        strategies_by_context=strategies_by_context,
        step_index=step_index,
        stage=stage,
        phase=phase,
        payload_index=index,
        compress_cache=compress_cache or {},
        upstream_model=upstream_model,
        context_aliases=context_aliases or {},
        rule_engine=rule_engine or {},
    )

    if isinstance(updated.get("messages"), list):
        new_messages, changed = _mutate_messages(updated["messages"], ctx=ctx)
        if changed:
            updated["messages"] = new_messages
            modified = True

    if isinstance(updated.get("input"), list):
        new_input, changed = _mutate_responses_input(updated["input"], ctx=ctx)
        if changed:
            updated["input"] = new_input
            modified = True

    instructions_text = updated.get("instructions")
    if isinstance(instructions_text, str) and instructions_text.strip():
        new_instructions, changed, drop = _mutate_plain_carrier(
            instructions_text,
            context_id="sys_1",
            ctx=ctx,
        )
        if drop:
            updated.pop("instructions", None)
            modified = True
        elif changed:
            updated["instructions"] = new_instructions
            modified = True

    return updated, modified


class _MutationContext:
    def __init__(
        self,
        *,
        instruction_to_context: dict[str, str],
        context_actions: dict[str, dict[str, Any]],
        strategies_by_context: dict[str, OptimizationStrategy],
        step_index: int,
        stage: str,
        phase: str,
        payload_index: PayloadIndex,
        compress_cache: dict[str, str],
        upstream_model: str | None,
        context_aliases: dict[str, str],
        rule_engine: dict[str, Any],
    ) -> None:
        self.instruction_to_context = instruction_to_context
        self.context_actions = context_actions
        self.strategies_by_context = strategies_by_context
        self.step_index = step_index
        self.stage = stage
        self.phase = phase
        self.payload_index = payload_index
        self.compress_cache = compress_cache
        self.upstream_model = upstream_model
        self.context_aliases = context_aliases
        self.rule_engine = rule_engine


def _canonical_context_id(context_id: str | None, ctx: _MutationContext) -> str | None:
    if not context_id:
        return None
    return ctx.context_aliases.get(context_id, context_id)


def _effective_action(context_id: str, ctx: _MutationContext) -> str:
    entry = ctx.context_actions.get(context_id) or {}
    return str(entry.get("effective_action") or entry.get("action") or "KEEP").upper()


def _strategy_for_context(context_id: str, ctx: _MutationContext) -> OptimizationStrategy | None:
    return ctx.strategies_by_context.get(context_id)


def _should_drop(context_id: str, ctx: _MutationContext, *, meter_len: int) -> bool:
    if ctx.phase not in {"B", "C", "D"}:
        return False
    strategy = _strategy_for_context(context_id, ctx)
    effective = _effective_action(context_id, ctx)
    applied = int(
        applied_prompt_tokens(max(1, meter_len), ctx.step_index, ctx.stage, strategy)
    )
    return applied <= 0 or effective == "DROP"


def _compress_body(
    body: str,
    *,
    context_id: str,
    strategy: OptimizationStrategy,
    ctx: _MutationContext,
) -> str:
    ratio = strategy.compress_target_ratio or 0.1
    if ctx.phase in {"C", "D"}:
        progress_signal = None
        source_type = None
        meta = ctx.context_actions.get(context_id) or {}
        if isinstance(meta, dict):
            progress_signal = meta.get("progress_signal")
            source_type = meta.get("source_type")
        return compress_text(
            body,
            target_ratio=float(ratio),
            context_id=context_id,
            rule_engine=ctx.rule_engine,
            progress_signal=str(progress_signal) if progress_signal else None,
            source_type=str(source_type) if source_type else None,
            model=ctx.upstream_model,
            cache=ctx.compress_cache,
        )
    target_len = max(1, int(len(body) * ratio))
    return body[:target_len]


def _mutate_plain_carrier(
    text: str,
    *,
    context_id: str,
    ctx: _MutationContext,
) -> tuple[str, bool, bool]:
    strategy = _strategy_for_context(context_id, ctx)
    effective = _effective_action(context_id, ctx)
    if _should_drop(context_id, ctx, meter_len=len(text)):
        return "", True, True
    if (
        ctx.phase in {"C", "D"}
        and effective in {"COMPRESS", "GATE", "ROUTE"}
        and strategy is not None
        and strategy.action == "COMPRESS"
    ):
        compressed = _compress_body(text, context_id=context_id, strategy=strategy, ctx=ctx)
        return compressed, compressed != text, False
    return text, False, False


def _mutate_text_block(
    text: str,
    *,
    context_id: str,
    ctx: _MutationContext,
) -> tuple[str, bool, bool]:
    match = _REF_MARKER_RE.match(text)
    marker = match.group(0) if match else ""
    body = _REF_MARKER_RE.sub("", text, count=1) if match else text

    if _should_drop(context_id, ctx, meter_len=len(text)):
        return text, True, True

    strategy = _strategy_for_context(context_id, ctx)
    effective = _effective_action(context_id, ctx)
    if (
        ctx.phase in {"C", "D"}
        and effective in {"COMPRESS", "GATE", "ROUTE"}
        and strategy is not None
        and strategy.action == "COMPRESS"
        and strategy.compress_target_ratio
    ):
        compressed = _compress_body(body, context_id=context_id, strategy=strategy, ctx=ctx)
        return f"{marker}{compressed}", True, False
    return text, False, False


def _context_from_ref_text(text: str, ctx: _MutationContext) -> str | None:
    match = _REF_MARKER_RE.match(text)
    if not match:
        return None
    instruction_id = match.group(1)
    return _canonical_context_id(ctx.instruction_to_context.get(instruction_id), ctx)


def _mutate_messages(
    messages: list[dict[str, Any]],
    *,
    ctx: _MutationContext,
) -> tuple[list[dict[str, Any]], bool]:
    changed = False
    kept: list[dict[str, Any]] = []
    for message in messages:
        new_message, message_changed, drop = _mutate_message(message, ctx=ctx)
        if drop:
            changed = True
            continue
        if message_changed:
            changed = True
        kept.append(new_message)
    return kept, changed


def _mutate_message(
    message: dict[str, Any],
    *,
    ctx: _MutationContext,
) -> tuple[dict[str, Any], bool, bool]:
    content = message.get("content")
    if isinstance(content, str):
        context_id = _context_from_ref_text(content, ctx)
        if not context_id:
            return message, False, False
        new_text, changed, drop = _mutate_text_block(content, context_id=context_id, ctx=ctx)
        if drop:
            return message, True, True
        if changed:
            return {**message, "content": new_text}, True, False
        return message, False, False

    if isinstance(content, list):
        new_blocks: list[Any] = []
        block_changed = False
        for block in content:
            if isinstance(block, dict) and block.get("type") in {"text", "input_text"}:
                text = str(block.get("text") or "")
                context_id = _context_from_ref_text(text, ctx)
                if not context_id:
                    new_blocks.append(block)
                    continue
                new_text, changed, drop = _mutate_text_block(text, context_id=context_id, ctx=ctx)
                if drop:
                    block_changed = True
                    continue
                if changed:
                    block_changed = True
                    new_blocks.append({**block, "text": new_text})
                else:
                    new_blocks.append(block)
            else:
                new_blocks.append(block)
        if not new_blocks:
            return message, True, True
        return {**message, "content": new_blocks}, block_changed, False

    return message, False, False


def _resolve_call_context(call_id: str, ctx: _MutationContext) -> str | None:
    if call_id.startswith("fc_"):
        call_id = call_id[3:]
    return _canonical_context_id(ctx.payload_index.primary_context_for_call_id(call_id), ctx)


def _mutate_responses_input(
    items: list[dict[str, Any]],
    *,
    ctx: _MutationContext,
) -> tuple[list[dict[str, Any]], bool]:
    from flow_cost_doctor.runtime.payload_index import link_reasoning_items_in_request

    link_reasoning_items_in_request(ctx.payload_index, items)
    changed = False
    kept: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        item_type = str(item.get("type") or "")
        if item_type == "message":
            new_item, item_changed, drop = _mutate_responses_message_item(item, ctx=ctx)
            if drop:
                changed = True
                continue
            if item_changed:
                changed = True
            kept.append(new_item)
            continue
        if item_type == "function_call_output":
            new_item, item_changed, drop = _mutate_function_call_output(item, ctx=ctx)
            if drop:
                changed = True
                continue
            if item_changed:
                changed = True
            kept.append(new_item)
            continue
        if item_type == "function_call":
            new_item, item_changed, drop = _mutate_function_call(item, ctx=ctx)
            if drop:
                changed = True
                continue
            if item_changed:
                changed = True
            kept.append(new_item)
            continue
        if item_type == "reasoning":
            new_item, item_changed, drop = _mutate_reasoning_item(item, ctx=ctx)
            if drop:
                changed = True
                continue
            if item_changed:
                changed = True
            kept.append(new_item)
            continue
        kept.append(item)
    return kept, changed


def _mutate_responses_message_item(
    item: dict[str, Any],
    *,
    ctx: _MutationContext,
) -> tuple[dict[str, Any], bool, bool]:
    content = item.get("content")
    if not isinstance(content, list):
        return item, False, False
    new_content: list[Any] = []
    item_changed = False
    for block in content:
        if isinstance(block, dict) and block.get("type") in {"input_text", "text", "output_text"}:
            text = str(block.get("text") or "")
            context_id = _context_from_ref_text(text, ctx)
            if context_id:
                new_text, changed, drop = _mutate_text_block(text, context_id=context_id, ctx=ctx)
                if drop:
                    item_changed = True
                    continue
                if changed:
                    item_changed = True
                    new_content.append({**block, "text": new_text})
                else:
                    new_content.append(block)
                continue
        new_content.append(block)
    if not new_content:
        return item, True, True
    if item_changed:
        return {**item, "content": new_content}, True, False
    return item, False, False


def _mutate_function_call_output(
    item: dict[str, Any],
    *,
    ctx: _MutationContext,
) -> tuple[dict[str, Any], bool, bool]:
    call_id = str(item.get("call_id") or "").strip()
    context_id = _resolve_call_context(call_id, ctx)
    if not context_id:
        return item, False, False
    output = item.get("output")
    body = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    if _should_drop(context_id, ctx, meter_len=len(body)):
        return item, True, True
    strategy = _strategy_for_context(context_id, ctx)
    effective = _effective_action(context_id, ctx)
    if (
        ctx.phase in {"C", "D"}
        and effective in {"COMPRESS", "GATE", "ROUTE"}
        and strategy is not None
        and strategy.action == "COMPRESS"
    ):
        compressed = _compress_body(body, context_id=context_id, strategy=strategy, ctx=ctx)
        if isinstance(output, str):
            return {**item, "output": compressed}, True, False
        try:
            return {**item, "output": json.loads(compressed)}, True, False
        except json.JSONDecodeError:
            return {**item, "output": compressed}, True, False
    return item, False, False


def _mutate_function_call(
    item: dict[str, Any],
    *,
    ctx: _MutationContext,
) -> tuple[dict[str, Any], bool, bool]:
    call_id = str(item.get("call_id") or item.get("id") or "").strip()
    context_id = _resolve_call_context(call_id, ctx)
    if not context_id:
        return item, False, False
    args = item.get("arguments")
    body = args if isinstance(args, str) else json.dumps(args or {}, ensure_ascii=False)
    meter_len = len(body) + len(str(item.get("name") or ""))
    if _should_drop(context_id, ctx, meter_len=meter_len):
        return item, True, True
    strategy = _strategy_for_context(context_id, ctx)
    effective = _effective_action(context_id, ctx)
    if (
        ctx.phase in {"C", "D"}
        and effective in {"COMPRESS", "GATE", "ROUTE"}
        and strategy is not None
        and strategy.action == "COMPRESS"
    ):
        compressed = _compress_body(body, context_id=context_id, strategy=strategy, ctx=ctx)
        if isinstance(args, str):
            return {**item, "arguments": compressed}, True, False
        return item, False, False
    return item, False, False


def _mutate_reasoning_item(
    item: dict[str, Any],
    *,
    ctx: _MutationContext,
) -> tuple[dict[str, Any], bool, bool]:
    rid = str(item.get("id") or "").strip()
    context_id = ctx.payload_index.context_for_reasoning_id(rid)
    if not context_id:
        return item, False, False
    encrypted = str(item.get("encrypted_content") or "")
    if not encrypted:
        return item, False, False
    if _should_drop(context_id, ctx, meter_len=len(encrypted)):
        return item, True, True
    strategy = _strategy_for_context(context_id, ctx)
    effective = _effective_action(context_id, ctx)
    if (
        ctx.phase in {"C", "D"}
        and effective in {"COMPRESS", "GATE", "ROUTE"}
        and strategy is not None
        and strategy.action == "COMPRESS"
    ):
        compressed = _compress_body(encrypted, context_id=f"reasoning:{context_id}", strategy=strategy, ctx=ctx)
        return {**item, "encrypted_content": compressed}, True, False
    return item, False, False
