from __future__ import annotations

"""
Security registry for Instructions and tools.

该模块集中维护两类安全配置：
- 不同 atomic instruction_type 的默认 security_type / rule_types（INSTRUCTION_SECURITY_REGISTRY）；
- 每个 tool 的 parser 函数（TOOL_PARSER_REGISTRY）：接收完整 arguments，返回
  ToolParseResult(instruction_type, security_type, rule_types)，所有安全属性内联其中。

在运行时由 InstructionBuilder 调用，用户/系统可以直接编辑本文件进行维护。
"""

from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

SecurityType = Dict[str, Any]
RuleType = Dict[str, Any]


def make_security_type(
    *,
    confidentiality: str,
    integrity: str,
    trustworthiness: str,
    confidence: str,
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
    # Cognitive reasoning / planning: 中等完整性、可回滚（纯认知无副作用）、仅提示/记录
    "REASON": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=True,
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
            confidence="MID",
            reversible=True,
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
            confidence="MID",
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [],
    },
    # Memory management — RETRIEVE: 从长期记忆中检索，只读，可逆
    "RETRIEVE": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="VERIFIED",
            confidence="HIGH",
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="retrieve_memory_sensitive_content",
                message="Retrieved memory may contain sensitive personal or contextual data. Handle with care.",
                effect="LOG_ONLY",
            )
        ],
    },
    # Memory management — STORE: 持久化到长期记忆，不可逆
    "STORE": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="store_memory_integrity_warn",
                message="Writing to long-term memory is persistent. Verify content accuracy before storing.",
                effect="WARN",
            )
        ],
    },
    # Human-facing respond：完整性较低、可信度较低，需要人审
    "RESPOND": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="UNVERIFIED",
            confidence="LOW",
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
            confidence="MID",
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
            confidence="HIGH",
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
# 2) Per-tool instruction parser registry
# ---------------------------------------------------------------------------
#
# 每个 tool 对应一个 parser 函数，签名统一为：
#
#   (arguments: Dict[str, Any]) -> ToolParseResult
#
# ToolParseResult 包含：
#   instruction_type  : str                      — 映射到的 atomic action
#   security_type     : Optional[SecurityType]   — 安全属性，由 parser 直接内联
#   rule_types        : Optional[List[RuleType]] — 规则列表，由 parser 直接内联
#
# InstructionType 与 instructions.md 中的 atomic action 保持一致：
#   READ / WRITE / EXEC / WAIT  (EXECUTION.Env)
#   ASK / RESPOND               (EXECUTION.Human)
#   DELEGATE                    (EXECUTION.Agent) — 对应文档中的 DELEGATE
#   RETRIEVE / STORE            (MEMORY.Management)
#   REASON / PLAN / CRITIQUE    (COGNITIVE.Reasoning)
#   SUBSCRIBE / RECEIVE         (EXECUTION.Perception)


class ToolParseResult(NamedTuple):
    """
    单次 tool call 的解析结果。所有安全属性均由 parser 函数直接内联，无外部兜底。
    """

    instruction_type: str
    security_type: Optional[SecurityType] = None
    rule_types: Optional[List[RuleType]] = None


ToolParser = Callable[[Dict[str, Any]], ToolParseResult]


# ---------------------------------------------------------------------------
# 文件系统工具
# ---------------------------------------------------------------------------

# 这三个文件被视为 Agent 的长期记忆载体：
# 读取 → RETRIEVE（从记忆中召回），写入 → STORE（持久化经验）
_MEMORY_FILE_NAMES = {"SOUL.md", "MEMORY.md", "AGENTS.md"}


def _get_path_basename(args: Dict[str, Any]) -> str:
    """从 args 中提取文件名（兼容 path / file_path 两种参数名）。"""
    raw = args.get("path") or args.get("file_path") or ""
    return os.path.basename(str(raw))


def _is_memory_file(args: Dict[str, Any]) -> bool:
    """判断 tool 调用是否针对长期记忆文件。"""
    return _get_path_basename(args) in _MEMORY_FILE_NAMES


def _parse_read(args: Dict[str, Any]) -> ToolParseResult:
    """read: 普通读取 → READ；读取记忆文件（SOUL/MEMORY/AGENTS）→ RETRIEVE。"""
    if _is_memory_file(args):
        return ToolParseResult(
            "RETRIEVE",
            make_security_type(
                confidentiality="MID",
                integrity="MID",
                trustworthiness="VERIFIED",
                confidence="HIGH",
                reversible=True,
                confidentiality_label=True,
                authority_label="AGENT_APPROVED",
            ),
            [
                make_simple_rule(
                    rule_id="retrieve_memory_sensitive_content",
                    message="Retrieved memory may contain sensitive personal or contextual data. Handle with care.",
                    effect="LOG_ONLY",
                )
            ],
        )
    return ToolParseResult(
        "READ",
        make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence="HIGH",
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        [
            make_simple_rule(
                rule_id="read_sensitive_path_warn",
                message="Reading a file path that may contain sensitive data (e.g., .env, credentials). Flag for review.",
                effect="WARN",
            )
        ],
    )


def _parse_edit(args: Dict[str, Any]) -> ToolParseResult:
    """edit: 普通编辑 → WRITE；编辑记忆文件（SOUL/MEMORY/AGENTS）→ STORE。"""
    if _is_memory_file(args):
        return ToolParseResult(
            "STORE",
            make_security_type(
                confidentiality="MID",
                integrity="HIGH",
                trustworthiness="UNVERIFIED",
                confidence="MID",
                reversible=False,
                confidentiality_label=True,
                authority_label="AGENT_APPROVED",
            ),
            [
                make_simple_rule(
                    rule_id="store_memory_integrity_warn",
                    message="Writing to long-term memory is persistent. Verify content accuracy before storing.",
                    effect="WARN",
                )
            ],
        )
    return ToolParseResult(
        "WRITE",
        make_security_type(
            confidentiality="MID",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        [
            make_simple_rule(
                rule_id="edit_backup_before_change",
                message="Consider creating a backup or using version control before editing critical files.",
                effect="WARN",
            )
        ],
    )


def _parse_write(args: Dict[str, Any]) -> ToolParseResult:
    """write: 普通覆写 → WRITE；写入记忆文件（SOUL/MEMORY/AGENTS）→ STORE。"""
    if _is_memory_file(args):
        return ToolParseResult(
            "STORE",
            make_security_type(
                confidentiality="MID",
                integrity="HIGH",
                trustworthiness="UNVERIFIED",
                confidence="MID",
                reversible=False,
                confidentiality_label=True,
                authority_label="AGENT_APPROVED",
            ),
            [
                make_simple_rule(
                    rule_id="store_memory_integrity_warn",
                    message="Writing to long-term memory is persistent. Verify content accuracy before storing.",
                    effect="WARN",
                )
            ],
        )
    return ToolParseResult(
        "WRITE",
        make_security_type(
            confidentiality="MID",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        [
            make_simple_rule(
                rule_id="write_overwrite_risk",
                message="write() will overwrite existing file content. Verify target path before proceeding.",
                effect="WARN",
            )
        ],
    )


# ---------------------------------------------------------------------------
# 进程 / Shell 执行
# ---------------------------------------------------------------------------


def _parse_exec(args: Dict[str, Any]) -> ToolParseResult:
    """
    exec: 执行 shell 命令，instruction_type 固定为 EXEC。
    - 普通调用：HIGH 机密性，HUMAN_APPROVED，包含 WARN + BLOCK 规则。
    - elevated=True：额外降低 confidence 至 LOW，追加专项 BLOCK 规则。
    """
    base_rules = [
        make_simple_rule(
            rule_id="exec_require_human_review",
            message="Shell exec with side effects detected. Require human review for destructive or network commands.",
            effect="WARN",
        ),
        make_simple_rule(
            rule_id="exec_elevated_block",
            message="Elevated exec (sudo/root) must be explicitly approved before execution.",
            effect="BLOCK",
        ),
    ]
    if args.get("elevated"):
        return ToolParseResult(
            "EXEC",
            make_security_type(
                confidentiality="HIGH",
                integrity="HIGH",
                trustworthiness="UNVERIFIED",
                confidence="LOW",
                reversible=False,
                confidentiality_label=True,
                authority_label="HUMAN_APPROVED",
            ),
            base_rules
            + [
                make_simple_rule(
                    rule_id="exec_elevated_explicit_block",
                    message="Elevated (sudo/root) exec detected. Block until human explicitly approves.",
                    effect="BLOCK",
                )
            ],
        )
    return ToolParseResult(
        "EXEC",
        make_security_type(
            confidentiality="HIGH",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=False,
            confidentiality_label=True,
            authority_label="HUMAN_APPROVED",
        ),
        base_rules,
    )


def _parse_process(args: Dict[str, Any]) -> ToolParseResult:
    """
    process: 管理后台进程会话。
    - list / log → READ；poll → WAIT；其余 → EXEC。
    - kill 操作追加不可逆确认规则。
    """
    action = args.get("action", "")
    if action in {"list", "log"}:
        itype = "READ"
    elif action == "poll":
        itype = "WAIT"
    else:
        itype = "EXEC"

    rules = [
        make_simple_rule(
            rule_id="process_kill_warn",
            message="Killing a process is irreversible. Confirm session ID before proceeding.",
            effect="WARN",
        )
    ]
    if action == "kill":
        rules.append(
            make_simple_rule(
                rule_id="process_kill_confirm",
                message="kill action is irreversible. Double-check sessionId before proceeding.",
                effect="WARN",
            )
        )
    return ToolParseResult(
        itype,
        make_security_type(
            confidentiality="MID",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=False,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        rules,
    )


# ---------------------------------------------------------------------------
# 浏览器控制
# ---------------------------------------------------------------------------

_BROWSER_READ_ACTIONS = {
    "status",
    "profiles",
    "tabs",
    "snapshot",
    "screenshot",
    "console",
    "pdf",
}
_BROWSER_ASK_ACTIONS = {"dialog"}


def _parse_browser(args: Dict[str, Any]) -> ToolParseResult:
    """
    browser: 控制浏览器。
    - READ 类：纯感知操作（快照、截图、状态查询）
    - ASK  类：弹窗交互，需要确认
    - EXEC 类：有副作用的操作（导航、点击、上传…）
    action=act 且 request.kind 为输入类操作时，追加表单提交告警。
    """
    action = args.get("action", "")
    if action in _BROWSER_READ_ACTIONS:
        itype = "READ"
    elif action in _BROWSER_ASK_ACTIONS:
        itype = "ASK"
    else:
        itype = "EXEC"

    rules = [
        make_simple_rule(
            rule_id="browser_act_irreversible",
            message="Browser act/navigate actions may be irreversible (form submit, payment). Verify before executing.",
            effect="WARN",
        ),
        make_simple_rule(
            rule_id="browser_screenshot_privacy",
            message="Screenshots may capture sensitive on-screen data. Ensure output is handled securely.",
            effect="LOG_ONLY",
        ),
    ]
    if action == "act":
        request_kind = (args.get("request") or {}).get("kind", "")
        if request_kind in {"fill", "select", "type", "press"}:
            rules.append(
                make_simple_rule(
                    rule_id="browser_act_form_input_warn",
                    message=f"Browser act kind='{request_kind}' may submit sensitive form data. Verify before executing.",
                    effect="WARN",
                )
            )
    return ToolParseResult(
        itype,
        make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        rules,
    )


# ---------------------------------------------------------------------------
# Canvas（节点 UI 画布）
# ---------------------------------------------------------------------------


def _parse_canvas(args: Dict[str, Any]) -> ToolParseResult:
    """canvas: snapshot 为只读，其余（present/hide/navigate/eval/a2ui_*）有副作用。"""
    action = args.get("action", "")
    return ToolParseResult(
        "READ" if action == "snapshot" else "EXEC",
        make_security_type(
            confidentiality="LOW",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        [],
    )


# ---------------------------------------------------------------------------
# 节点控制（物理/远程设备节点）
# ---------------------------------------------------------------------------

_NODES_READ_ACTIONS = {
    "status",
    "describe",
    "pending",
    "camera_snap",
    "camera_list",
    "camera_clip",
    "screen_record",
    "location_get",
}


def _parse_nodes(args: Dict[str, Any]) -> ToolParseResult:
    """
    nodes: 访问远程节点。
    - READ 类：感知（摄像头/位置/状态查询）
    - EXEC 类：有副作用（批准/拒绝/通知/运行命令/调用功能）
    摄像头/录屏操作追加隐私同意规则。
    """
    action = args.get("action", "")
    rules = [
        make_simple_rule(
            rule_id="nodes_camera_privacy",
            message="Camera/screen capture on remote node may violate user privacy. Require explicit consent.",
            effect="WARN",
        ),
        make_simple_rule(
            rule_id="nodes_run_invoke_review",
            message="Executing commands on remote node is high-risk. Human review required.",
            effect="WARN",
        ),
    ]
    if action in {"camera_snap", "camera_clip", "screen_record"}:
        rules.append(
            make_simple_rule(
                rule_id="nodes_capture_consent_required",
                message=f"nodes action='{action}' captures media from remote device. Explicit user consent required.",
                effect="WARN",
            )
        )
    return ToolParseResult(
        "READ" if action in _NODES_READ_ACTIONS else "EXEC",
        make_security_type(
            confidentiality="HIGH",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=False,
            confidentiality_label=True,
            authority_label="HUMAN_APPROVED",
        ),
        rules,
    )


# ---------------------------------------------------------------------------
# 定时任务（Cron）
# ---------------------------------------------------------------------------


def _parse_cron(args: Dict[str, Any]) -> ToolParseResult:
    """
    cron: 管理定时任务。
    - READ  : status / list / runs
    - WRITE : add / update（持久化写入）
    - EXEC  : remove / run / wake（有副作用）
    add 操作追加 payload 自主运行审查规则。
    """
    action = args.get("action", "")
    if action in {"status", "list", "runs"}:
        itype = "READ"
    elif action in {"add", "update"}:
        itype = "WRITE"
    else:
        itype = "EXEC"

    rules = [
        make_simple_rule(
            rule_id="cron_add_review",
            message="Adding a cron job creates persistent side effects. Review schedule and payload before confirming.",
            effect="WARN",
        )
    ]
    if action == "add":
        rules.append(
            make_simple_rule(
                rule_id="cron_add_payload_review",
                message="New cron job payload will run autonomously. Review schedule and payload kind before confirming.",
                effect="WARN",
            )
        )

    # READ 操作可逆，WRITE/EXEC 操作不可逆
    is_reversible = itype == "READ"

    return ToolParseResult(
        itype,
        make_security_type(
            confidentiality="MID",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=is_reversible,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        rules,
    )


# ---------------------------------------------------------------------------
# 消息通道
# ---------------------------------------------------------------------------


def _parse_message(args: Dict[str, Any]) -> ToolParseResult:
    """
    message: 向外部渠道发送/修改消息。
    - WRITE : edit（修改已发送消息）
    - EXEC  : send / broadcast / react / delete
    broadcast 追加多接收方确认规则。
    """
    action = args.get("action", "")
    rules = [
        make_simple_rule(
            rule_id="message_send_broadcast_review",
            message="Sending/broadcasting messages to external channels is irreversible. Verify recipients and content.",
            effect="WARN",
        ),
        make_simple_rule(
            rule_id="message_delete_irreversible",
            message="Deleting messages is irreversible. Confirm message ID and intent.",
            effect="WARN",
        ),
    ]
    if action == "broadcast":
        rules.append(
            make_simple_rule(
                rule_id="message_broadcast_multi_recipient",
                message="broadcast sends to multiple recipients simultaneously. Confirm target list and content.",
                effect="WARN",
            )
        )
    return ToolParseResult(
        "WRITE" if action == "edit" else "EXEC",
        make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        rules,
    )


# ---------------------------------------------------------------------------
# TTS（文字转语音）
# ---------------------------------------------------------------------------


def _parse_tts(args: Dict[str, Any]) -> ToolParseResult:
    """tts: 文字转语音，产生音频输出，instruction_type 固定为 EXEC。"""
    return ToolParseResult(
        "EXEC",
        make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="UNVERIFIED",
            confidence="HIGH",
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        [],
    )


# ---------------------------------------------------------------------------
# Gateway 管理
# ---------------------------------------------------------------------------


def _parse_gateway(args: Dict[str, Any]) -> ToolParseResult:
    """
    gateway: 管理 gateway 配置与生命周期。
    - READ  : config.get / config.schema
    - WRITE : config.apply / config.patch
    - EXEC  : restart / update.run
    config.apply 追加全量替换告警；restart 追加会话中断告警。
    """
    action = args.get("action", "")
    if action in {"config.get", "config.schema"}:
        itype = "READ"
    elif action in {"config.apply", "config.patch"}:
        itype = "WRITE"
    else:
        itype = "EXEC"

    rules = [
        make_simple_rule(
            rule_id="gateway_restart_impact",
            message="Gateway restart will terminate all active sessions. Confirm before proceeding.",
            effect="WARN",
        ),
        make_simple_rule(
            rule_id="gateway_config_apply_overwrite",
            message="config.apply replaces the entire gateway config. Use config.patch for safer partial updates.",
            effect="WARN",
        ),
    ]
    if action == "config.apply":
        rules.append(
            make_simple_rule(
                rule_id="gateway_config_apply_full_replace",
                message="config.apply fully replaces the gateway config. Prefer config.patch for incremental changes.",
                effect="WARN",
            )
        )
    if action == "restart":
        rules.append(
            make_simple_rule(
                rule_id="gateway_restart_session_drop",
                message="Gateway restart will drop all active sessions immediately.",
                effect="WARN",
            )
        )
    return ToolParseResult(
        itype,
        make_security_type(
            confidentiality="HIGH",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=False,
            confidentiality_label=True,
            authority_label="HUMAN_APPROVED",
        ),
        rules,
    )


# ---------------------------------------------------------------------------
# Agent / Session 管理
# ---------------------------------------------------------------------------


def _parse_agents_list(args: Dict[str, Any]) -> ToolParseResult:
    """agents_list: 列出可用 agent，纯检索，instruction_type 为 RETRIEVE。"""
    return ToolParseResult(
        "RETRIEVE",
        make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="VERIFIED",
            confidence="HIGH",
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        [],
    )


def _parse_sessions_list(args: Dict[str, Any]) -> ToolParseResult:
    """sessions_list: 列出 session，纯检索，instruction_type 为 RETRIEVE。"""
    return ToolParseResult(
        "RETRIEVE",
        make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="VERIFIED",
            confidence="HIGH",
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        [],
    )


def _parse_sessions_history(args: Dict[str, Any]) -> ToolParseResult:
    """
    sessions_history: 获取会话历史，instruction_type 为 RETRIEVE。
    includeTools=True 时追加原始工具调用内容的敏感告警。
    """
    rules = [
        make_simple_rule(
            rule_id="sessions_history_sensitive_content",
            message="Session history may contain sensitive conversation data. Handle with care.",
            effect="LOG_ONLY",
        )
    ]
    if args.get("includeTools"):
        rules.append(
            make_simple_rule(
                rule_id="sessions_history_tool_content",
                message="includeTools=True exposes raw tool arguments/results in history. Handle with extra care.",
                effect="LOG_ONLY",
            )
        )
    return ToolParseResult(
        "RETRIEVE",
        make_security_type(
            confidentiality="MID",
            integrity="LOW",
            trustworthiness="VERIFIED",
            confidence="HIGH",
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        rules,
    )


def _parse_sessions_send(args: Dict[str, Any]) -> ToolParseResult:
    """sessions_send: 向另一 session 发送消息，instruction_type 为 DELEGATE。"""
    return ToolParseResult(
        "DELEGATE",
        make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        [
            make_simple_rule(
                rule_id="sessions_send_target_verify",
                message="Verify target session key/label before sending to avoid misdirected messages.",
                effect="WARN",
            )
        ],
    )


def _parse_sessions_spawn(args: Dict[str, Any]) -> ToolParseResult:
    """
    sessions_spawn: 启动子 agent，instruction_type 为 DELEGATE。
    runTimeoutSeconds 较大时追加长时运行告警。
    """
    rules = [
        make_simple_rule(
            rule_id="sessions_spawn_task_review",
            message="Spawned sub-agent runs autonomously. Review task prompt for safety and scope before spawning.",
            effect="WARN",
        )
    ]
    timeout = args.get("runTimeoutSeconds") or args.get("timeoutSeconds")
    if timeout and timeout > 300:
        rules.append(
            make_simple_rule(
                rule_id="sessions_spawn_long_running",
                message=f"Spawned sub-agent has a long timeout ({timeout}s). Ensure task scope is well-bounded.",
                effect="WARN",
            )
        )
    return ToolParseResult(
        "DELEGATE",
        make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        rules,
    )


def _parse_session_status(args: Dict[str, Any]) -> ToolParseResult:
    """session_status: 查询本 session 状态，instruction_type 为 RETRIEVE。"""
    return ToolParseResult(
        "RETRIEVE",
        make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="VERIFIED",
            confidence="HIGH",
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        [],
    )


# ---------------------------------------------------------------------------
# Web 访问
# ---------------------------------------------------------------------------


def _parse_web_search(args: Dict[str, Any]) -> ToolParseResult:
    """web_search: 搜索外部网络，结果不可信，instruction_type 为 READ。"""
    return ToolParseResult(
        "READ",
        make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="UNVERIFIED",
            confidence="LOW",
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        [
            make_simple_rule(
                rule_id="web_search_prompt_injection_risk",
                message="Web search results are untrusted. Sanitize returned snippets before using as context.",
                effect="WARN",
            )
        ],
    )


def _parse_web_fetch(args: Dict[str, Any]) -> ToolParseResult:
    """
    web_fetch: 抓取网页内容，instruction_type 为 READ。
    maxChars 较大时追加大载荷注入风险告警。
    """
    rules = [
        make_simple_rule(
            rule_id="web_fetch_prompt_injection_risk",
            message="Fetched web content is fully untrusted and may contain prompt-injection payloads. Treat as adversarial input.",
            effect="WARN",
        )
    ]
    max_chars = args.get("maxChars")
    if max_chars and max_chars > 50_000:
        rules.append(
            make_simple_rule(
                rule_id="web_fetch_large_payload_injection_risk",
                message=f"Fetching up to {max_chars} chars from an untrusted URL. Large payloads increase prompt-injection surface.",
                effect="WARN",
            )
        )
    return ToolParseResult(
        "READ",
        make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="UNVERIFIED",
            confidence="LOW",
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        rules,
    )


# ---------------------------------------------------------------------------
# 图像感知
# ---------------------------------------------------------------------------


def _parse_image(args: Dict[str, Any]) -> ToolParseResult:
    """image: 图像分析（感知），instruction_type 为 READ。"""
    return ToolParseResult(
        "READ",
        make_security_type(
            confidentiality="MID",
            integrity="LOW",
            trustworthiness="UNVERIFIED",
            confidence="MID",
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        [
            make_simple_rule(
                rule_id="image_privacy_check",
                message="Image may contain PII or sensitive visual data. Ensure privacy compliance before processing.",
                effect="LOG_ONLY",
            )
        ],
    )


# ---------------------------------------------------------------------------
# 记忆管理
# ---------------------------------------------------------------------------


def _parse_memory_search(args: Dict[str, Any]) -> ToolParseResult:
    """memory_search: 语义检索 MEMORY.md，instruction_type 为 RETRIEVE。"""
    return ToolParseResult(
        "RETRIEVE",
        make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="VERIFIED",
            confidence="HIGH",
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        [],
    )


def _parse_memory_get(args: Dict[str, Any]) -> ToolParseResult:
    """memory_get: 按路径读取 memory 片段，instruction_type 为 RETRIEVE。"""
    return ToolParseResult(
        "RETRIEVE",
        make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="VERIFIED",
            confidence="HIGH",
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        [],
    )


# ---------------------------------------------------------------------------
# Parser registry  &  unified entry point
# ---------------------------------------------------------------------------

TOOL_PARSER_REGISTRY: Dict[str, ToolParser] = {
    "read": _parse_read,
    "edit": _parse_edit,
    "write": _parse_write,
    "exec": _parse_exec,
    "process": _parse_process,
    "browser": _parse_browser,
    "canvas": _parse_canvas,
    "nodes": _parse_nodes,
    "cron": _parse_cron,
    "message": _parse_message,
    "tts": _parse_tts,
    "gateway": _parse_gateway,
    "agents_list": _parse_agents_list,
    "sessions_list": _parse_sessions_list,
    "sessions_history": _parse_sessions_history,
    "sessions_send": _parse_sessions_send,
    "sessions_spawn": _parse_sessions_spawn,
    "session_status": _parse_session_status,
    "web_search": _parse_web_search,
    "web_fetch": _parse_web_fetch,
    "image": _parse_image,
    "memory_search": _parse_memory_search,
    "memory_get": _parse_memory_get,
}


def parse_tool_instruction(
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> ToolParseResult:
    """
    统一入口：根据 tool_name 找到对应的 parser，传入 arguments，返回 ToolParseResult。

    所有安全属性均由 parser 函数直接返回，无外部兜底。
    未注册的 tool 返回兜底结果 ("EXEC", None, None)。
    """
    parser = TOOL_PARSER_REGISTRY.get(tool_name)
    if not parser:
        return ToolParseResult("EXEC")
    return parser(arguments or {})
