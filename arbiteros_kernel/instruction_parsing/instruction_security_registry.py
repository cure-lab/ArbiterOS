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
    # -----------------------------------------------------------------------
    # 文件系统工具
    # -----------------------------------------------------------------------

    # read: 只读文件，可能读到敏感内容（密钥、配置），可信度依赖文件来源
    "read": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence=0.8,
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="read_sensitive_path_warn",
                message="Reading a file path that may contain sensitive data (e.g., .env, credentials). Flag for review.",
                effect="WARN",
            )
        ],
    },

    # edit: 原地修改文件，完整性要求高，不可逆
    "edit": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence=0.75,
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="edit_backup_before_change",
                message="Consider creating a backup or using version control before editing critical files.",
                effect="WARN",
            )
        ],
    },

    # write: 覆写或创建文件，不可逆（覆写会丢失原内容）
    "write": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence=0.75,
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="write_overwrite_risk",
                message="write() will overwrite existing file content. Verify target path before proceeding.",
                effect="WARN",
            )
        ],
    },

    # -----------------------------------------------------------------------
    # 进程 / Shell 执行
    # -----------------------------------------------------------------------

    # exec: 执行 shell 命令，高风险，不可逆，需人工授权
    "exec": {
        "security_type": make_security_type(
            confidentiality="HIGH",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence=0.6,
            reversible=False,
            confidentiality_label=True,
            authority_label="HUMAN_APPROVED",
        ),
        "rule_types": [
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
        ],
    },

    # process: 管理后台进程，风险取决于 action（kill 不可逆）
    "process": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence=0.7,
            reversible=False,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="process_kill_warn",
                message="Killing a process is irreversible. Confirm session ID before proceeding.",
                effect="WARN",
            )
        ],
    },

    # -----------------------------------------------------------------------
    # 浏览器控制
    # -----------------------------------------------------------------------

    # browser: 操作浏览器，可读页面（含敏感信息）、执行 UI 操作（不可逆）
    "browser": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence=0.65,
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
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
        ],
    },

    # -----------------------------------------------------------------------
    # Canvas（节点 UI 画布）
    # -----------------------------------------------------------------------

    "canvas": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence=0.7,
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [],
    },

    # -----------------------------------------------------------------------
    # 节点控制（物理/远程设备节点）
    # -----------------------------------------------------------------------

    # nodes: 访问远程节点（摄像头/位置/运行命令），高机密性，需人工授权
    "nodes": {
        "security_type": make_security_type(
            confidentiality="HIGH",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence=0.6,
            reversible=False,
            confidentiality_label=True,
            authority_label="HUMAN_APPROVED",
        ),
        "rule_types": [
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
        ],
    },

    # -----------------------------------------------------------------------
    # 定时任务（Cron）
    # -----------------------------------------------------------------------

    "cron": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence=0.7,
            reversible=False,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="cron_add_review",
                message="Adding a cron job creates persistent side effects. Review schedule and payload before confirming.",
                effect="WARN",
            )
        ],
    },

    # -----------------------------------------------------------------------
    # 消息通道
    # -----------------------------------------------------------------------

    # message: 向外部渠道发送/修改消息，不可逆，机密性视内容而定
    "message": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence=0.7,
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
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
        ],
    },

    # -----------------------------------------------------------------------
    # TTS（文字转语音）
    # -----------------------------------------------------------------------

    "tts": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="UNVERIFIED",
            confidence=0.9,
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [],
    },

    # -----------------------------------------------------------------------
    # Gateway 管理
    # -----------------------------------------------------------------------

    # gateway: 重启/更新/修改配置，高风险，部分操作不可逆
    "gateway": {
        "security_type": make_security_type(
            confidentiality="HIGH",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence=0.6,
            reversible=False,
            confidentiality_label=True,
            authority_label="HUMAN_APPROVED",
        ),
        "rule_types": [
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
        ],
    },

    # -----------------------------------------------------------------------
    # Agent / Session 管理
    # -----------------------------------------------------------------------

    # agents_list / sessions_list / sessions_history / session_status: 只读检索
    "agents_list": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="VERIFIED",
            confidence=0.95,
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [],
    },
    "sessions_list": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="VERIFIED",
            confidence=0.95,
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [],
    },
    "sessions_history": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="LOW",
            trustworthiness="VERIFIED",
            confidence=0.9,
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="sessions_history_sensitive_content",
                message="Session history may contain sensitive conversation data. Handle with care.",
                effect="LOG_ONLY",
            )
        ],
    },
    "session_status": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="VERIFIED",
            confidence=0.95,
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [],
    },

    # sessions_send: 向其他 session 发送消息（跨 agent 协作），需要谨慎
    "sessions_send": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence=0.7,
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="sessions_send_target_verify",
                message="Verify target session key/label before sending to avoid misdirected messages.",
                effect="WARN",
            )
        ],
    },

    # sessions_spawn: 启动子 agent，有持续性副作用，需审核 task 内容
    "sessions_spawn": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="UNVERIFIED",
            confidence=0.65,
            reversible=False,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="sessions_spawn_task_review",
                message="Spawned sub-agent runs autonomously. Review task prompt for safety and scope before spawning.",
                effect="WARN",
            )
        ],
    },

    # -----------------------------------------------------------------------
    # Web 访问（外部不可信来源）
    # -----------------------------------------------------------------------

    # web_search: 搜索外部网络，结果不可信，可能含 prompt injection
    "web_search": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="UNVERIFIED",
            confidence=0.5,
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="web_search_prompt_injection_risk",
                message="Web search results are untrusted. Sanitize returned snippets before using as context.",
                effect="WARN",
            )
        ],
    },

    # web_fetch: 抓取网页，内容完全不可信，prompt injection 风险更高
    "web_fetch": {
        "security_type": make_security_type(
            confidentiality="LOW",
            integrity="LOW",
            trustworthiness="UNVERIFIED",
            confidence=0.45,
            reversible=True,
            confidentiality_label=False,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="web_fetch_prompt_injection_risk",
                message="Fetched web content is fully untrusted and may contain prompt-injection payloads. Treat as adversarial input.",
                effect="WARN",
            )
        ],
    },

    # -----------------------------------------------------------------------
    # 图像感知
    # -----------------------------------------------------------------------

    "image": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="LOW",
            trustworthiness="UNVERIFIED",
            confidence=0.6,
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [
            make_simple_rule(
                rule_id="image_privacy_check",
                message="Image may contain PII or sensitive visual data. Ensure privacy compliance before processing.",
                effect="LOG_ONLY",
            )
        ],
    },

    # -----------------------------------------------------------------------
    # 记忆管理
    # -----------------------------------------------------------------------

    # memory_search: 语义检索 MEMORY.md，结果可信度较高（内部记忆）
    "memory_search": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="VERIFIED",
            confidence=0.85,
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [],
    },

    # memory_get: 按路径读取 memory 片段
    "memory_get": {
        "security_type": make_security_type(
            confidentiality="MID",
            integrity="MID",
            trustworthiness="VERIFIED",
            confidence=0.9,
            reversible=True,
            confidentiality_label=True,
            authority_label="AGENT_APPROVED",
        ),
        "rule_types": [],
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


# ---------------------------------------------------------------------------
# 3) Per-tool instruction parser registry
# ---------------------------------------------------------------------------
#
# 每个 tool 对应一个 parser 函数，签名统一为：
#
#   (arguments: Dict[str, Any]) -> ToolParseResult
#
# ToolParseResult 包含：
#   instruction_type  : str                      — 映射到的 atomic action
#   security_type     : Optional[SecurityType]   — None 表示沿用 TOOL_SECURITY_REGISTRY 默认值
#   rule_types        : Optional[List[RuleType]] — None 表示沿用 TOOL_SECURITY_REGISTRY 默认值
#
# 相比声明式字典的优势：
#   - 可以读取任意参数（不限于 action），例如 exec 的 elevated、browser 的 request.kind
#   - 可以基于参数值动态调整 security_type（例如 elevated=True 时提升权限要求）
#   - 逻辑自包含，易于单独测试和扩展
#
# InstructionType 与 instructions.md 中的 atomic action 保持一致：
#   READ / WRITE / EXEC / WAIT  (EXECUTION.Env)
#   ASK / RESPOND               (EXECUTION.Human)
#   HANDOFF                     (EXECUTION.Agent) — 对应文档中的 DELEGATE
#   RETRIEVE / STORE            (MEMORY.Management)
#   REASON / PLAN / CRITIQUE    (COGNITIVE.Reasoning)
#   SUBSCRIBE / RECEIVE         (EXECUTION.Perception)


from typing import Callable, NamedTuple


class ToolParseResult(NamedTuple):
    """
    单次 tool call 的解析结果。
    security_type / rule_types 为 None 时，调用方回退到 TOOL_SECURITY_REGISTRY 的工具级默认值。
    """
    instruction_type: str
    security_type: Optional[SecurityType] = None
    rule_types: Optional[List[RuleType]] = None


# 方便 parser 内部引用工具级默认安全属性的辅助函数
def _tool_defaults(tool_name: str) -> Tuple[Optional[SecurityType], List[RuleType]]:
    """从 TOOL_SECURITY_REGISTRY 取工具级默认 security_type 和 rule_types。"""
    return get_tool_security(tool_name)


# ---------------------------------------------------------------------------
# 文件系统工具
# ---------------------------------------------------------------------------

def _parse_read(args: Dict[str, Any]) -> ToolParseResult:
    """read: 只读文件，instruction_type 固定为 READ。"""
    return ToolParseResult("READ")


def _parse_edit(args: Dict[str, Any]) -> ToolParseResult:
    """edit: 原地修改文件，instruction_type 固定为 WRITE。"""
    return ToolParseResult("WRITE")


def _parse_write(args: Dict[str, Any]) -> ToolParseResult:
    """write: 覆写或创建文件，instruction_type 固定为 WRITE。"""
    return ToolParseResult("WRITE")


# ---------------------------------------------------------------------------
# 进程 / Shell 执行
# ---------------------------------------------------------------------------

_EXEC_READ_ACTIONS = {"status", "list", "poll", "log"}

def _parse_exec(args: Dict[str, Any]) -> ToolParseResult:
    """
    exec: 执行 shell 命令，instruction_type 固定为 EXEC。
    当 elevated=True 时提升 authority_label 为 HUMAN_APPROVED 并附加 BLOCK 规则。
    """
    sec, rules = _tool_defaults("exec")
    if args.get("elevated"):
        sec = make_security_type(
            confidentiality="HIGH",
            integrity="HIGH",
            trustworthiness="UNVERIFIED",
            confidence=0.4,
            reversible=False,
            confidentiality_label=True,
            authority_label="HUMAN_APPROVED",
        )
        rules = list(rules) + [
            make_simple_rule(
                rule_id="exec_elevated_explicit_block",
                message="Elevated (sudo/root) exec detected. Block until human explicitly approves.",
                effect="BLOCK",
            )
        ]
    return ToolParseResult("EXEC", sec, rules)


def _parse_process(args: Dict[str, Any]) -> ToolParseResult:
    """
    process: 管理后台进程会话。
    action 决定 instruction_type；kill 操作附加额外 WARN 规则。
    """
    action = args.get("action", "")
    if action in {"list"}:
        itype = "READ"
    elif action == "poll":
        itype = "WAIT"
    elif action == "log":
        itype = "READ"
    else:
        itype = "EXEC"  # write / send-keys / submit / paste / kill

    sec, rules = _tool_defaults("process")
    if action == "kill":
        rules = list(rules) + [
            make_simple_rule(
                rule_id="process_kill_confirm",
                message="kill action is irreversible. Double-check sessionId before proceeding.",
                effect="WARN",
            )
        ]
    return ToolParseResult(itype, sec, rules)


# ---------------------------------------------------------------------------
# 浏览器控制
# ---------------------------------------------------------------------------

_BROWSER_READ_ACTIONS  = {"status", "profiles", "tabs", "snapshot", "screenshot", "console", "pdf"}
_BROWSER_ASK_ACTIONS   = {"dialog"}

def _parse_browser(args: Dict[str, Any]) -> ToolParseResult:
    """
    browser: 控制浏览器。
    - READ 类：纯感知操作（快照、截图、状态查询）
    - ASK  类：弹窗交互，需要确认
    - EXEC 类：有副作用的操作（导航、点击、上传…）
    当 action=act 且 request.kind 为写操作时，附加额外 WARN 规则。
    """
    action = args.get("action", "")
    if action in _BROWSER_READ_ACTIONS:
        itype = "READ"
    elif action in _BROWSER_ASK_ACTIONS:
        itype = "ASK"
    else:
        itype = "EXEC"

    sec, rules = _tool_defaults("browser")

    # act 操作内嵌 request.kind，对提交类动作额外告警
    if action == "act":
        request_kind = (args.get("request") or {}).get("kind", "")
        if request_kind in {"fill", "select", "type", "press"}:
            rules = list(rules) + [
                make_simple_rule(
                    rule_id="browser_act_form_input_warn",
                    message=f"Browser act kind='{request_kind}' may submit sensitive form data. Verify before executing.",
                    effect="WARN",
                )
            ]

    return ToolParseResult(itype, sec, rules)


# ---------------------------------------------------------------------------
# Canvas（节点 UI 画布）
# ---------------------------------------------------------------------------

def _parse_canvas(args: Dict[str, Any]) -> ToolParseResult:
    """canvas: snapshot 为只读，其余（present/hide/navigate/eval/a2ui_*）有副作用。"""
    action = args.get("action", "")
    itype = "READ" if action == "snapshot" else "EXEC"
    return ToolParseResult(itype)


# ---------------------------------------------------------------------------
# 节点控制（物理/远程设备节点）
# ---------------------------------------------------------------------------

_NODES_READ_ACTIONS = {
    "status", "describe", "pending",
    "camera_snap", "camera_list", "camera_clip",
    "screen_record", "location_get",
}

def _parse_nodes(args: Dict[str, Any]) -> ToolParseResult:
    """
    nodes: 访问远程节点。
    - READ 类：感知（摄像头/位置/状态查询）
    - EXEC 类：有副作用（批准/拒绝/通知/运行命令/调用功能）
    摄像头/录屏操作附加隐私告警规则。
    """
    action = args.get("action", "")
    itype = "READ" if action in _NODES_READ_ACTIONS else "EXEC"

    sec, rules = _tool_defaults("nodes")
    if action in {"camera_snap", "camera_clip", "screen_record"}:
        rules = list(rules) + [
            make_simple_rule(
                rule_id="nodes_capture_consent_required",
                message=f"nodes action='{action}' captures media from remote device. Explicit user consent required.",
                effect="WARN",
            )
        ]
    return ToolParseResult(itype, sec, rules)


# ---------------------------------------------------------------------------
# 定时任务（Cron）
# ---------------------------------------------------------------------------

def _parse_cron(args: Dict[str, Any]) -> ToolParseResult:
    """
    cron: 管理定时任务。
    - READ  : status / list / runs
    - WRITE : add / update（持久化写入）
    - EXEC  : remove / run / wake（有副作用）
    add/update 操作附加 payload 审查规则。
    """
    action = args.get("action", "")
    if action in {"status", "list", "runs"}:
        itype = "READ"
    elif action in {"add", "update"}:
        itype = "WRITE"
    else:
        itype = "EXEC"

    sec, rules = _tool_defaults("cron")
    if action == "add":
        rules = list(rules) + [
            make_simple_rule(
                rule_id="cron_add_payload_review",
                message="New cron job payload will run autonomously. Review schedule and payload kind before confirming.",
                effect="WARN",
            )
        ]
    return ToolParseResult(itype, sec, rules)


# ---------------------------------------------------------------------------
# 消息通道
# ---------------------------------------------------------------------------

def _parse_message(args: Dict[str, Any]) -> ToolParseResult:
    """
    message: 向外部渠道发送/修改消息。
    - WRITE : edit（修改已发送消息）
    - EXEC  : send / broadcast / react / delete
    broadcast 操作附加多接收方告警。
    """
    action = args.get("action", "")
    itype = "WRITE" if action == "edit" else "EXEC"

    sec, rules = _tool_defaults("message")
    if action == "broadcast":
        rules = list(rules) + [
            make_simple_rule(
                rule_id="message_broadcast_multi_recipient",
                message="broadcast sends to multiple recipients simultaneously. Confirm target list and content.",
                effect="WARN",
            )
        ]
    return ToolParseResult(itype, sec, rules)


# ---------------------------------------------------------------------------
# TTS（文字转语音）
# ---------------------------------------------------------------------------

def _parse_tts(args: Dict[str, Any]) -> ToolParseResult:
    """tts: 文字转语音，产生音频输出，instruction_type 固定为 EXEC。"""
    return ToolParseResult("EXEC")


# ---------------------------------------------------------------------------
# Gateway 管理
# ---------------------------------------------------------------------------

def _parse_gateway(args: Dict[str, Any]) -> ToolParseResult:
    """
    gateway: 管理 gateway 配置与生命周期。
    - READ  : config.get / config.schema
    - WRITE : config.apply / config.patch
    - EXEC  : restart / update.run
    config.apply（全量替换）附加额外强告警。
    """
    action = args.get("action", "")
    if action in {"config.get", "config.schema"}:
        itype = "READ"
    elif action in {"config.apply", "config.patch"}:
        itype = "WRITE"
    else:
        itype = "EXEC"

    sec, rules = _tool_defaults("gateway")
    if action == "config.apply":
        rules = list(rules) + [
            make_simple_rule(
                rule_id="gateway_config_apply_full_replace",
                message="config.apply fully replaces the gateway config. Prefer config.patch for incremental changes.",
                effect="WARN",
            )
        ]
    if action == "restart":
        rules = list(rules) + [
            make_simple_rule(
                rule_id="gateway_restart_session_drop",
                message="Gateway restart will drop all active sessions immediately.",
                effect="WARN",
            )
        ]
    return ToolParseResult(itype, sec, rules)


# ---------------------------------------------------------------------------
# Agent / Session 管理
# ---------------------------------------------------------------------------

def _parse_agents_list(args: Dict[str, Any]) -> ToolParseResult:
    """agents_list: 列出可用 agent，纯检索，instruction_type 为 RETRIEVE。"""
    return ToolParseResult("RETRIEVE")


def _parse_sessions_list(args: Dict[str, Any]) -> ToolParseResult:
    """sessions_list: 列出 session，纯检索，instruction_type 为 RETRIEVE。"""
    return ToolParseResult("RETRIEVE")


def _parse_sessions_history(args: Dict[str, Any]) -> ToolParseResult:
    """
    sessions_history: 获取会话历史，instruction_type 为 RETRIEVE。
    includeTools=True 时消息量更大，附加敏感内容告警。
    """
    sec, rules = _tool_defaults("sessions_history")
    if args.get("includeTools"):
        rules = list(rules) + [
            make_simple_rule(
                rule_id="sessions_history_tool_content",
                message="includeTools=True exposes raw tool arguments/results in history. Handle with extra care.",
                effect="LOG_ONLY",
            )
        ]
    return ToolParseResult("RETRIEVE", sec, rules)


def _parse_sessions_send(args: Dict[str, Any]) -> ToolParseResult:
    """sessions_send: 向另一 session 发送消息，instruction_type 为 HANDOFF。"""
    return ToolParseResult("HANDOFF")


def _parse_sessions_spawn(args: Dict[str, Any]) -> ToolParseResult:
    """
    sessions_spawn: 启动子 agent，instruction_type 为 HANDOFF。
    runTimeoutSeconds 较大时附加长时运行告警。
    """
    sec, rules = _tool_defaults("sessions_spawn")
    timeout = args.get("runTimeoutSeconds") or args.get("timeoutSeconds")
    if timeout and timeout > 300:
        rules = list(rules) + [
            make_simple_rule(
                rule_id="sessions_spawn_long_running",
                message=f"Spawned sub-agent has a long timeout ({timeout}s). Ensure task scope is well-bounded.",
                effect="WARN",
            )
        ]
    return ToolParseResult("HANDOFF", sec, rules)


def _parse_session_status(args: Dict[str, Any]) -> ToolParseResult:
    """session_status: 查询本 session 状态，instruction_type 为 RETRIEVE。"""
    return ToolParseResult("RETRIEVE")


# ---------------------------------------------------------------------------
# Web 访问
# ---------------------------------------------------------------------------

def _parse_web_search(args: Dict[str, Any]) -> ToolParseResult:
    """web_search: 搜索外部网络，结果不可信，instruction_type 为 READ。"""
    return ToolParseResult("READ")


def _parse_web_fetch(args: Dict[str, Any]) -> ToolParseResult:
    """
    web_fetch: 抓取网页内容，instruction_type 为 READ。
    maxChars 较大时内容可能包含更多注入载荷，附加额外告警。
    """
    sec, rules = _tool_defaults("web_fetch")
    max_chars = args.get("maxChars")
    if max_chars and max_chars > 50_000:
        rules = list(rules) + [
            make_simple_rule(
                rule_id="web_fetch_large_payload_injection_risk",
                message=f"Fetching up to {max_chars} chars from an untrusted URL. Large payloads increase prompt-injection surface.",
                effect="WARN",
            )
        ]
    return ToolParseResult("READ", sec, rules)


# ---------------------------------------------------------------------------
# 图像感知
# ---------------------------------------------------------------------------

def _parse_image(args: Dict[str, Any]) -> ToolParseResult:
    """image: 图像分析（感知），instruction_type 为 READ。"""
    return ToolParseResult("READ")


# ---------------------------------------------------------------------------
# 记忆管理
# ---------------------------------------------------------------------------

def _parse_memory_search(args: Dict[str, Any]) -> ToolParseResult:
    """memory_search: 语义检索 MEMORY.md，instruction_type 为 RETRIEVE。"""
    return ToolParseResult("RETRIEVE")


def _parse_memory_get(args: Dict[str, Any]) -> ToolParseResult:
    """memory_get: 按路径读取 memory 片段，instruction_type 为 RETRIEVE。"""
    return ToolParseResult("RETRIEVE")


# ---------------------------------------------------------------------------
# Parser registry & unified entry point
# ---------------------------------------------------------------------------

ToolParser = Callable[[Dict[str, Any]], ToolParseResult]

TOOL_PARSER_REGISTRY: Dict[str, ToolParser] = {
    # 文件系统
    "read":             _parse_read,
    "edit":             _parse_edit,
    "write":            _parse_write,
    # 进程 / Shell
    "exec":             _parse_exec,
    "process":          _parse_process,
    # 浏览器
    "browser":          _parse_browser,
    # Canvas
    "canvas":           _parse_canvas,
    # 节点
    "nodes":            _parse_nodes,
    # Cron
    "cron":             _parse_cron,
    # 消息
    "message":          _parse_message,
    # TTS
    "tts":              _parse_tts,
    # Gateway
    "gateway":          _parse_gateway,
    # Session / Agent
    "agents_list":      _parse_agents_list,
    "sessions_list":    _parse_sessions_list,
    "sessions_history": _parse_sessions_history,
    "sessions_send":    _parse_sessions_send,
    "sessions_spawn":   _parse_sessions_spawn,
    "session_status":   _parse_session_status,
    # Web
    "web_search":       _parse_web_search,
    "web_fetch":        _parse_web_fetch,
    # 图像
    "image":            _parse_image,
    # 记忆
    "memory_search":    _parse_memory_search,
    "memory_get":       _parse_memory_get,
}


def parse_tool_instruction(
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
) -> ToolParseResult:
    """
    统一入口：根据 tool_name 找到对应的 parser，传入 arguments，返回 ToolParseResult。

    - 若 result.security_type 为 None，调用方应回退到 get_tool_security(tool_name) 的默认值。
    - 若 result.rule_types 为 None，同上。
    - 未注册的 tool 返回兜底结果 ("EXEC", None, None)。
    """
    parser = TOOL_PARSER_REGISTRY.get(tool_name)
    if parser is None:
        return ToolParseResult("EXEC", None, None)
    return parser(arguments or {})
