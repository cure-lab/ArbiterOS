"""
InstructionBuilder — converts LLM outputs into a unified Instruction list.

Supports two input paths:
  - add_from_structured_output(): structured LLM output with { intent, content }
  - add_from_tool_call():         LLM tool call, parsed via TOOL_PARSER_REGISTRY
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .tool_parsers import parse_tool_instruction
from .tool_agent_config import get_tool_agent
from .types import (
    INSTRUCTION_TYPE_TO_CATEGORY,
    Instruction,
    RuleType,
    SecurityType,
    TaintStatus,
    compute_prop_taint_for_instruction,
    make_security_type,
)


_HERMES_BROWSER_TOOL_TO_ACTION: Dict[str, str] = {
    "browser_navigate": "open",
    "browser_click": "act",
    "browser_type": "act",
    "browser_press": "act",
    "browser_scroll": "act",
    "browser_back": "act",
    "browser_snapshot": "snapshot",
    "browser_console": "console",
    "browser_get_images": "snapshot",
    "browser_vision": "screenshot",
}


def _canonicalize_tool_for_policy(
    tool_name: str, arguments: Dict[str, Any]
) -> tuple[str, Dict[str, Any]]:
    """
    Pre-policy normalization for Hermse split browser tools.

    Keep parser input unchanged (handled by caller), but normalize instruction
    content so policies that expect OpenClaw-style browser(action=...) semantics
    can still classify flow kinds consistently.
    """
    if get_tool_agent() != "hermes":
        return tool_name, arguments

    action = _HERMES_BROWSER_TOOL_TO_ACTION.get(tool_name)
    if action is None:
        return tool_name, arguments

    out_args = dict(arguments)
    out_args.setdefault("action", action)
    out_args.setdefault("_arbiteros_raw_tool_name", tool_name)
    return "browser", out_args


class InstructionBuilder:
    """Accumulates Instructions for a single trace, ready to be serialised."""

    def __init__(self, trace_id: Optional[str] = None) -> None:
        self.trace_id = trace_id or str(uuid.uuid4())
        self._runtime_step = 0
        self.instructions: List[Instruction] = []
        self._root_source_message_id: Optional[str] = None
        self._last_instruction_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_step(self) -> int:
        self._runtime_step += 1
        return self._runtime_step

    @staticmethod
    def _new_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _build(
        self,
        *,
        content: Any,
        parent_id: Optional[str],
        source_message_id: Optional[str],
        runtime_step: Optional[int] = None,
        security_type: Optional[SecurityType] = None,
        rule_types: Optional[List[RuleType]] = None,
        instruction_category: Optional[str] = None,
        instruction_type: Optional[str] = None,
    ) -> Instruction:
        return {
            "id": self._new_id(),
            "content": content,
            "runtime_step": runtime_step
            if runtime_step is not None
            else self._next_step(),
            "parent_id": parent_id,
            "source_message_id": source_message_id,
            "security_type": security_type,
            "rule_types": rule_types or [],
            "instruction_category": instruction_category,
            "instruction_type": instruction_type or "REASON",
        }

    def _commit(self, instr: Instruction) -> Instruction:
        """Wire source_message_id linkage, append to the list, and set cumulative prop_*."""
        if self._root_source_message_id is None:
            self._root_source_message_id = instr["id"]
        if instr.get("source_message_id") is None:
            instr["source_message_id"] = self._root_source_message_id
        self._last_instruction_id = instr["id"]
        self.instructions.append(instr)

        # prop_*: tool call = aggregate self + reference_tool_ids (all entries);
        # pure text = own conf/trust only
        taint = compute_prop_taint_for_instruction(self.instructions, instr)
        st = instr.get("security_type")
        if isinstance(st, dict):
            st["prop_confidentiality"] = taint.confidentiality
            st["prop_trustworthiness"] = taint.trustworthiness

        return instr

    def get_taint_status(self) -> TaintStatus:
        """Return the taint status across all accumulated instructions."""
        return compute_prop_taint_for_instruction(self.instructions, self.instructions[-1] if self.instructions else {})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_from_structured_output(
        self,
        *,
        structured: Dict[str, Any],
        parent_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
        runtime_step: Optional[int] = None,
        security_type: Optional[SecurityType] = None,
        rule_types: Optional[List[RuleType]] = None,
    ) -> Instruction:
        """Build an Instruction from a structured LLM output { intent, content }."""
        intent = structured.get("intent")
        action_type = intent
        category = (
            INSTRUCTION_TYPE_TO_CATEGORY.get(action_type)
            if action_type is not None
            else None
        )

        if security_type is None:
            security_type = make_security_type(
                confidentiality="LOW",
                trustworthiness="HIGH",
                confidence="UNKNOWN",
                reversible=True,
                authority="UNKNOWN",
            )

        return self._commit(
            self._build(
                content=structured.get("content"),
                parent_id=parent_id
                if parent_id is not None
                else self._last_instruction_id,
                source_message_id=source_message_id,
                runtime_step=runtime_step,
                security_type=security_type,
                rule_types=rule_types,
                instruction_category=category,
                instruction_type=action_type or "REASON",
            )
        )

    def add_from_tool_call(
        self,
        *,
        tool_name: str,
        tool_call_id: Optional[str],
        arguments: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
        parent_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
        runtime_step: Optional[int] = None,
    ) -> Instruction:
        """Build an Instruction from a tool call, delegating to the registered parser."""
        policy_tool_name, policy_arguments = _canonicalize_tool_for_policy(
            tool_name, arguments
        )
        content: Dict[str, Any] = {
            "tool_name": policy_tool_name,
            "tool_call_id": tool_call_id,
            "arguments": policy_arguments,
        }
        if result is not None:
            content["result"] = result

        taint = self.get_taint_status()
        parsed = parse_tool_instruction(tool_name, arguments, taint_status=taint)
        return self._commit(
            self._build(
                content=content,
                parent_id=parent_id
                if parent_id is not None
                else self._last_instruction_id,
                source_message_id=source_message_id,
                runtime_step=runtime_step,
                security_type=parsed.security_type,
                instruction_category=INSTRUCTION_TYPE_TO_CATEGORY.get(
                    parsed.instruction_type, "EXECUTION.Env"
                ),
                instruction_type=parsed.instruction_type,
            )
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self, *, indent: int = 2, ensure_ascii: bool = False) -> str:
        payload = {
            "trace_id": self.trace_id,
            "created_at": self._now_iso(),
            "instructions": self.instructions,
        }
        return json.dumps(payload, indent=indent, ensure_ascii=ensure_ascii)
