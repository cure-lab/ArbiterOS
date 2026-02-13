from __future__ import annotations

"""
Security registry for Instructions and tools.

该模块是一个"注册表配置文件"，用于集中维护：
- 不同 atomic instruction_type 的默认 security_type / rule_types；
- 不同 tool_name 的默认 security_type / rule_types。

在运行时由 InstructionBuilder 调用，用户/系统可以直接编辑本文件进行维护。
"""

from typing import Any, Dict, List, Optional, Tuple

SecurityType = Dict[str, Any]
RuleType = Dict[str, Any]


def make_security_type(
    *,
    confidentiality: str,
    integrity: str,
    trustworthiness: str,
    confidence: float,
    reversible: bool,
    confidentiality_label: bool,
    authority_label: str,
    custom: Optional[Dict[str, Any]] = None,
) -> SecurityType:
    return {
        "confidentiality": confidentiality,
        "integrity": integrity,
        "trustworthiness": trustworthiness,
        "confidence": confidence,
        "reversible": reversible,
        "confidentiality_label": confidentiality_label,
        "authority_label": authority_label,
        "custom": custom or {},
    }


def make_simple_rule(
    *,
    rule_id: str,
    message: str,
    effect: str = "WARN",
) -> RuleType:
    """
    一个简化的 RuleType 构造器：
    - 不对 condition 做约束（由上层后续扩展）；
    - 只设置 action.effect / action.message。
    """
    return {
        "id": rule_id,
        "scope": "NodeSelf",
        "condition": {
            "custom": {},
        },
        "action": {
            "effect": effect,
            "message": message,
            "remediation": {},
        },
    }


# ---------------------------------------------------------------------------
# 1) LLM Instruction (instruction_type) 级别的默认安全属性
# ---------------------------------------------------------------------------

INSTRUCTION_SECURITY_REGISTRY: Dict[str, Dict[str, Any]] = {
    # Cognitive reasoning / planning: 中等完整性、不可回滚、仅提示/记录
    "REASON": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence=0.7,
            reversible=False,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [],
    },
    "PLAN": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence=0.7,
            reversible=False,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [],
    },
    "CRITIQUE": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence=0.6,
            reversible=False,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [],
    },
    # Human-facing respond：完整性较低、可信度较低，需要人审
    "RESPOND": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="UNVERIFIED",
            confidence=0.5,
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="respond_log_only",
                effect="LOG_ONLY",
                message="Log LLM RESPOND content for potential human review.",
            )
        ],
    },
    "ASK": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence=0.6,
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [],
    },
    "USER_MESSAGE": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence=1.0,
            reversible=False,
            confidentiality_label=True,
            authority_label="HUMAN_APPROVED",
        ),
        "rule_types": [],
    },
}


def get_instruction_security(
    instruction_type: Optional[str],
    instruction_category: Optional[str] = None,
) -> Tuple[Optional[SecurityType], List[RuleType]]:
    """
    根据 instruction_type（优先）/ instruction_category 获取默认安全属性。
    当前实现只基于 type；category 预留给将来的更细粒度配置。
    """
    if not instruction_type:
        return None, []

    entry = INSTRUCTION_SECURITY_REGISTRY.get(instruction_type)
    if not entry:
        return None, []

    return entry.get("security_type"), list(entry.get("rule_types") or [])


# ---------------------------------------------------------------------------
# 2) Tool 级别的默认安全属性
# ---------------------------------------------------------------------------

TOOL_SECURITY_REGISTRY: Dict[str, Dict[str, Any]] = {
    # get_weather: 对外部 API 的只读查询，机密性/完整性中等，可信度一般
    "get_weather": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence=0.6,
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="get_weather_retry_with_alternative_source",
                message="If weather provider fails, retry with an alternative source before failing.",
                effect="WARN",
            )
        ],
    },
    # math_add: 纯计算工具，完整性要求高、可信度高
    "math_add": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="HIGH",
            trustworthiness="VERIFIED",
            confidence=0.95,
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="math_add_fallback_calculator2",
                message="If math_add fails, fallback to `calculator_2` or another verified calculator service.",
                effect="WARN",
            )
        ],
    },
}


def get_tool_security(tool_name: str) -> Tuple[Optional[SecurityType], List[RuleType]]:
    """
    根据 tool_name 获取默认的 security_type / rule_types。
    """
    entry = TOOL_SECURITY_REGISTRY.get(tool_name)
    if not entry:
        return None, []
    return entry.get("security_type"), list(entry.get("rule_types") or [])
