from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .instruction_security_registry import (
    SecurityType as RegistrySecurityType,
    RuleType as RegistryRuleType,
    get_instruction_security,
    get_tool_security,
    parse_tool_instruction,
    ToolParseResult,
)


Instruction = Dict[str, Any]
RuleType = RegistryRuleType
SecurityType = Optional[RegistrySecurityType]


DEFAULT_SECURITY_TYPE: SecurityType = None


INSTRUCTION_TYPE_TO_CATEGORY = {
    # Cognitive
    "REASON": "COGNITIVE.Reasoning",
    "PLAN": "COGNITIVE.Reasoning",
    "CRITIQUE": "COGNITIVE.Reasoning",
    # Memory
    "STORE": "MEMORY.Management",
    "RETRIEVE": "MEMORY.Management",
    "COMPRESS": "MEMORY.Management",
    "PRUNE": "MEMORY.Management",
    # Env execution / I/O
    "READ": "EXECUTION.Env",
    "WRITE": "EXECUTION.Env",
    "EXEC": "EXECUTION.Env",
    "WAIT": "EXECUTION.Env",
    # Human interaction
    "ASK": "EXECUTION.Human",
    "RESPOND": "EXECUTION.Human",
    "USER_MESSAGE": "EXECUTION.Human",
    # Agent collaboration
    "HANDOFF": "EXECUTION.Agent",
    # Perception / events
    "SUBSCRIBE": "EXECUTION.Perception",
    "RECEIVE": "EXECUTION.Perception",
}


class InstructionBuilder:
    """
    将每一步结构化输出（包括 tool call）转换为统一的 Instruction 结构，
    并维护一个独立的 instruction list，最终可以落盘到 JSON 文件。
    """

    def __init__(self, trace_id: Optional[str] = None) -> None:
        # 用于跨一次对话 / 一次运行的全局 trace
        self.trace_id = trace_id or str(uuid.uuid4())
        self._runtime_step = 0
        self.instructions: List[Instruction] = []
        # 链接用：根源 message id 与上一条 instruction id
        self._root_source_message_id: Optional[str] = None
        self._last_instruction_id: Optional[str] = None

    # ------------------------------------------------------------------
    # 基础构建逻辑
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

    def _build_base_instruction(
        self,
        *,
        content: Any,
        parent_id: Optional[str],
        source_message_id: Optional[str],
        runtime_step: Optional[int] = None,
        security_type: SecurityType = DEFAULT_SECURITY_TYPE,
        rule_types: Optional[List[RuleType]] = None,
        instruction_category: Optional[str] = None,
        instruction_type: Optional[str] = None,
    ) -> Instruction:
        """
        构造一个基础的 Instruction 对象，不包含 MessageMetadata / ActionMetadata，
        只聚焦于你目前给出的 Instruction schema。
        """
        step = runtime_step if runtime_step is not None else self._next_step()

        instruction_id = self._new_id()

        instruction: Instruction = {
            "id": instruction_id,
            "content": content,
            "runtime_step": step,
            "parent_id": parent_id,
            "source_message_id": source_message_id,
            "security_type": security_type,
            "rule_types": rule_types or [],
            "instruction_category": instruction_category,
            "instruction_type": instruction_type or "REASON",
        }

        return instruction

    # ------------------------------------------------------------------
    # 针对普通 LLM 结构化输出（intent + content）
    # ------------------------------------------------------------------
    def add_from_structured_output(
        self,
        *,
        structured: Dict[str, Any],
        parent_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
        runtime_step: Optional[int] = None,
        security_type: SecurityType = DEFAULT_SECURITY_TYPE,
        rule_types: Optional[List[RuleType]] = None,
        explicit_category: Optional[str] = None,
        explicit_type: Optional[str] = None,
    ) -> Instruction:
        """
        将 { intent, content } 的结构化输出转换为 Instruction。

        - intent 会被映射到 atomic_action_category / atomic_action_type；
        - content 直接进入 Instruction.content。
        """
        # 现在 intent 字段直接等于 atomic instruction_type（例如：REASON / PLAN / RESPOND ...）
        intent = structured.get("intent")
        content = structured.get("content")

        # instruction_type 直接用 intent，除非显式覆盖
        action_type = explicit_type or intent
        # 根据 instruction_type 推导大类（instruction_category），仍然允许显式覆盖
        inferred_category = (
            INSTRUCTION_TYPE_TO_CATEGORY.get(action_type) if action_type is not None else None
        )
        category = explicit_category or inferred_category

        # 默认 parent_id 串为上一条 instruction
        if parent_id is None:
            parent_id = self._last_instruction_id

        # 如果调用方没有显式给 security / rules，则从注册表中按 instruction_type/category 补全
        if security_type is DEFAULT_SECURITY_TYPE and rule_types is None:
            reg_security, reg_rules = get_instruction_security(action_type, category)
            if reg_security is not None:
                security_type = reg_security
            if rule_types is None and reg_rules:
                rule_types = reg_rules

        instr = self._build_base_instruction(
            content=content,
            parent_id=parent_id,
            source_message_id=source_message_id,
            runtime_step=runtime_step,
            security_type=security_type,
            rule_types=rule_types,
            instruction_category=category,
            instruction_type=action_type or "REASON",
        )

        # 维护 root source_message_id & 链接
        if self._root_source_message_id is None:
            self._root_source_message_id = instr["id"]
        if instr.get("source_message_id") is None:
            instr["source_message_id"] = self._root_source_message_id

        self._last_instruction_id = instr["id"]
        self.instructions.append(instr)
        return instr

    # ------------------------------------------------------------------
    # 针对 tool call（包括参数与结果）
    # ------------------------------------------------------------------
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
        security_type: SecurityType = DEFAULT_SECURITY_TYPE,
        rule_types: Optional[List[RuleType]] = None,
        explicit_category: Optional[str] = None,
        explicit_type: Optional[str] = None,
    ) -> Instruction:
        """
        将一次 tool 调用（包括参数与结果）转换为 Instruction。
        content 会以一个结构化 payload 的形式记录：
        {
          "tool_name": ...,
          "tool_call_id": ...,
          "arguments": {...},
          "result": {...}  # 可选
        }

        instruction_type 的推断顺序：
        1. explicit_type（调用方显式指定，最高优先级）
        2. TOOL_PARSER_REGISTRY[tool_name](arguments).instruction_type
        3. "EXEC"（兜底）

        security_type / rule_types 的推断顺序：
        1. 调用方显式传入的非 DEFAULT 值（最高优先级）
        2. TOOL_PARSER_REGISTRY[tool_name](arguments) 返回的非 None 值
        3. get_tool_security(tool_name)（工具级默认值）
        """
        content: Dict[str, Any] = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
        }
        if result is not None:
            content["result"] = result

        # ── 调用 per-tool parser，一次得到 instruction_type + security + rules ──
        parsed: ToolParseResult = parse_tool_instruction(tool_name, arguments)

        # ── 1. 推断 instruction_type ──────────────────────────────────────
        resolved_type: str = explicit_type or parsed.instruction_type

        # ── 2. 推断 instruction_category ─────────────────────────────────
        resolved_category: str = (
            explicit_category
            or INSTRUCTION_TYPE_TO_CATEGORY.get(resolved_type, "EXECUTION.Env")
        )

        # 默认 parent_id 串为上一条 instruction
        if parent_id is None:
            parent_id = self._last_instruction_id

        # ── 3. 推断 security_type / rule_types ───────────────────────────
        if security_type is DEFAULT_SECURITY_TYPE and rule_types is None:
            # 优先用 parser 依据参数动态计算的结果
            if parsed.security_type is not None:
                security_type = parsed.security_type
            if parsed.rule_types is not None:
                rule_types = parsed.rule_types
            # parser 返回 None 时，回退到工具级静态默认值（TOOL_SECURITY_REGISTRY）
            if security_type is DEFAULT_SECURITY_TYPE or rule_types is None:
                reg_security, reg_rules = get_tool_security(tool_name)
                if security_type is DEFAULT_SECURITY_TYPE and reg_security is not None:
                    security_type = reg_security
                if rule_types is None and reg_rules:
                    rule_types = reg_rules

        instr = self._build_base_instruction(
            content=content,
            parent_id=parent_id,
            source_message_id=source_message_id,
            runtime_step=runtime_step,
            security_type=security_type,
            rule_types=rule_types,
            instruction_category=resolved_category,
            instruction_type=resolved_type,
        )

        # 复用根 source_message_id
        if self._root_source_message_id is None:
            self._root_source_message_id = instr["id"]
        if instr.get("source_message_id") is None:
            instr["source_message_id"] = self._root_source_message_id

        self._last_instruction_id = instr["id"]
        self.instructions.append(instr)
        return instr

    # ------------------------------------------------------------------
    # 序列化 & 写文件
    # ------------------------------------------------------------------
    def to_list(self) -> List[Instruction]:
        return list(self.instructions)

    def to_json(self, *, indent: int = 2, ensure_ascii: bool = False) -> str:
        payload = {
            "trace_id": self.trace_id,
            "created_at": self._now_iso(),
            "instructions": self.instructions,
        }
        return json.dumps(payload, indent=indent, ensure_ascii=ensure_ascii)

    def save_to_file(self, path: Optional[str] = None) -> str:
        """
        将当前 instruction list 持久化为一个 JSON 文件。
        默认写到 instruction_parsing 目录下的 instructions_output.json。
        """
        if path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(base_dir, "instructions_output.json")

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())

        return path
