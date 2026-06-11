"""OpenCode hook adapter.

The hook reads the JSON event payload from stdin and writes checkpoints through
the shared coordinator. It maps OpenCode hook fields onto the provider-neutral
checkpoint lifecycle.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from checkpoint_plugin.coordinator import CheckpointCoordinator, TurnRecord
from checkpoint_plugin.types import TrajectoryReference

from ._hook_common import first_string as _first_string
from ._hook_common import parent_session_env as _parent_session_env
from ._hook_common import read_payload as _read_payload
from ._hook_common import recording_enabled as _recording_enabled
from ._trajectory_slicer import codex_key, jsonl_after_leading_metas, jsonl_ref_for_turn

# OpenCode uses similar architecture to Codex: SubagentStop fires before the turn-closing
# record is flushed. Apply the same settle timeout optimization with read-time tail recovery.
_SETTLE_TIMEOUT_ENV = "CHECKPOINT_SIDECHAIN_SETTLE_TIMEOUT"
_SETTLE_POLL_ENV = "CHECKPOINT_SIDECHAIN_SETTLE_POLL"
_RUNTIME_ENV_KEYS = (
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_DIR",
    "OPENCODE_TUI_CONFIG",
    "OPENCODE_DISABLE_PROJECT_CONFIG",
    "OPENCODE_DISABLE_EXTERNAL_SKILLS",
    "OPENCODE_DISABLE_CLAUDE_CODE",
    "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS",
    "OPENCODE_DISABLE_AUTOCOMPACT",
    "OPENCODE_DISABLE_PRUNE",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS",
    "OPENCODE_PURE",
    "OPENCODE_WORKSPACE_ID",
    "OPENCODE_EXPERIMENTAL_WORKSPACES",
    "OPENCODE_DATA_DIR",
)


def _settle_timeout_s() -> float:
    try:
        return float(os.environ.get(_SETTLE_TIMEOUT_ENV, "1.0"))
    except ValueError:
        return 1.0


def _settle_poll_s() -> float:
    try:
        return float(os.environ.get(_SETTLE_POLL_ENV, "0.1"))
    except ValueError:
        return 0.1


def main(argv: list[str] | None = None) -> int:
    if not _recording_enabled():
        _write_ok()
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("event", nargs="?", choices=["session_start", "turn_end", "subagent_end"])
    args = parser.parse_args(argv)
    payload = _sanitize_payload(_read_payload())
    event = args.event or _event_from_payload(payload)
    cwd = Path(_first_string(payload, "cwd", "directory") or Path.cwd())
    session_id = (
        os.environ.get("OPENCODE_SESSION_ID")
        or _first_string(payload, "session_id", "sessionID", "sessionId")
        or "opencode-session"
    )
    _seed_opencode_env(session_id, payload)

    if event == "subagent_end" or _is_subagent_stop_event(payload):
        _on_subagent_end(payload, cwd, session_id)
        _write_ok()
        return 0

    _attach_opencode_sqlite_state(payload, session_id)

    # OpenCode subagents are regular sessions with parent_session_id set.
    # Detect via the agent_type field the TS plugin passes.
    is_subagent = _first_string(payload, "agent_type") == "subagent"
    parent_session_id = _first_string(payload, "parent_session_id", "parentSessionId") if is_subagent else None

    # OpenCode forks have no parent_id; the TS plugin detects them via title
    # pattern and passes source="fork" + forked_from_session_id.
    is_fork = _first_string(payload, "source") == "fork"
    forked_from_session_id = _first_string(payload, "forked_from_session_id", "forkedFromSessionId") if is_fork else None

    coordinator = CheckpointCoordinator(session_id=session_id, cwd=cwd)

    if event == "session_start":
        source = _first_string(payload, "source") or ("subagent" if is_subagent else None)
        lineage: dict[str, Any] | None = None
        # OC1: Inherit session_env from parent/source session for forks/subagents.
        # Fork/subagent SessionStart payloads often omit runtime fields (model,
        # effort) that only arrive at the primary session's SessionStart. Inherit
        # them so resumed metadata is complete, matching how subagent_end inherits.
        inherited_env: dict[str, str] = {}
        if is_subagent and parent_session_id:
            lineage = {
                "parent_session_id": parent_session_id,
                "agent_type": _first_string(payload, "agent_type", "agentType"),
            }
            inherited_env = _parent_session_env(parent_session_id)
        elif is_fork and forked_from_session_id:
            lineage = {
                "forked_from_session_id": forked_from_session_id,
            }
            inherited_env = _parent_session_env(forked_from_session_id)
        # Payload fields override inherited, so explicit nulls/changes are respected
        session_env = {**inherited_env, **_session_env(payload)}
        coordinator.on_session_start(
            source=source,
            session_env=session_env,
            source_transcript_path=_first_string(payload, "transcript_path", "transcriptPath"),
            lineage=lineage,
        )
        _write_ok()
        return 0

    if not _is_stop_event(payload):
        _write_ok()
        return 0

    turn_record = _turn_record(payload)
    coordinator.on_turn_end(turn_record, _trajectory_ref(payload, provider="opencode"))
    _write_ok()
    return 0


def _on_subagent_end(payload: dict[str, Any], cwd: Path, parent_session_id: str) -> None:
    """Checkpoint a finished OpenCode subagent as its own session.

    OpenCode subagent hooks carry the parent session id; we derive a distinct
    plugin session keyed by agent id (falling back to the transcript stem) so the
    subagent gets its own checkpoint timeline without disturbing the parent.
    """
    agent_id = _first_string(payload, "agent_id", "agentId")
    # OpenCode SubagentStop carries the subagent's own rollout in `agent_transcript_path`;
    # the common `transcript_path` is the PARENT rollout. Slice the subagent file.
    transcript_path = _first_string(payload, "agent_transcript_path", "agentTranscriptPath") or _first_string(
        payload, "transcript_path", "transcriptPath"
    )
    if agent_id is None and transcript_path is None:
        return
    suffix = agent_id or (Path(transcript_path).stem if transcript_path else "unknown")
    sub_session_id = f"{parent_session_id}--subagent-{suffix}"
    coordinator = CheckpointCoordinator(session_id=sub_session_id, cwd=cwd)
    # Inherit the parent's pinned session_env (model/effort) for fields the
    # subagent Stop payload may omit.
    sub_env = {**_parent_session_env(parent_session_id), **_session_env(payload)}
    coordinator.on_session_start(
        source="subagent",
        session_env=sub_env,
        lineage={
            "parent_session_id": parent_session_id,
            "agent_id": agent_id,
            "agent_type": _first_string(payload, "agent_type", "agentType"),
        },
    )
    if transcript_path is not None:
        _settle_subagent_rollout(Path(transcript_path))
    ref = _subagent_trajectory_ref(payload, transcript_path)
    coordinator.on_turn_end(_turn_record(payload), ref)


def _settle_subagent_rollout(transcript_path: Path) -> None:
    """Block (bounded) for opencode's turn-closing `task_complete` to flush.

    LATENCY OPTIMIZATION ONLY — not a correctness mechanism. The subagent's final
    event lands moments after SubagentStop, and the hook fires before opencode
    even enqueues it, so this poll cannot guarantee the record is present. Read-time
    tail recovery (`recover_trailing_tail` on every read path) is what guarantees
    completeness; this merely front-loads it so a `show`/`list` immediately after
    capture is already at EOF without a recovery write. Poll until the last non-blank
    record is a `task_complete`, or the (short) timeout elapses.
    """
    if _settle_timeout_s() <= 0:
        return
    deadline = time.monotonic() + _settle_timeout_s()
    poll = _settle_poll_s()
    while True:
        if _subagent_tail_is_complete(transcript_path):
            return
        if not transcript_path.exists():
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(poll)


def _subagent_tail_is_complete(transcript_path: Path) -> bool:
    """True when the rollout's last record is a `task_complete` event (turn closed)."""
    try:
        data = transcript_path.read_bytes()
    except OSError:
        return False
    if not data.endswith(b"\n"):
        return False
    for line in reversed(data.splitlines()):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        payload = record.get("payload") if isinstance(record, dict) else None
        return isinstance(payload, dict) and payload.get("type") == "task_complete"
    return False


def _event_from_payload(payload: dict[str, Any]) -> str:
    hook_event_name = _first_string(payload, "hook_event_name", "hookEventName")
    if hook_event_name == "SessionStart":
        return "session_start"
    if hook_event_name == "SubagentStop":
        return "subagent_end"
    # Check event_metadata for hook_event_name
    event_meta = payload.get("event_metadata", {})
    if isinstance(event_meta, dict):
        meta_event = event_meta.get("hook_event_name")
        if meta_event == "SessionStart":
            return "session_start"
        if meta_event == "SubagentStop":
            return "subagent_end"
    return "turn_end"


def _is_stop_event(payload: dict[str, Any]) -> bool:
    hook_event_name = _first_string(payload, "hook_event_name", "hookEventName")
    if hook_event_name == "Stop":
        return True
    # Check event_metadata
    event_meta = payload.get("event_metadata", {})
    if isinstance(event_meta, dict):
        return event_meta.get("hook_event_name") == "Stop"
    return False


def _is_subagent_stop_event(payload: dict[str, Any]) -> bool:
    return _first_string(payload, "hook_event_name", "hookEventName") == "SubagentStop"


def _seed_opencode_env(session_id: str, payload: dict[str, Any]) -> None:
    os.environ["CHECKPOINT_PROVIDER"] = "opencode"
    os.environ.setdefault("OPENCODE_SESSION_ID", session_id)
    model = _first_string(payload, "model")
    if model:
        os.environ["OPENCODE_MODEL"] = model
    effort = _opencode_effort(payload)
    if effort:
        os.environ["OPENCODE_EFFORT"] = effort
    permission_mode = _first_string(payload, "permission_mode", "permissionMode")
    if permission_mode:
        os.environ["OPENCODE_PERMISSION_MODE"] = permission_mode
    mode = _opencode_mode(payload)
    if mode:
        os.environ["OPENCODE_MODE"] = mode
    agent_type = _first_string(payload, "agent_type", "agentType")
    if agent_type:
        os.environ["OPENCODE_AGENT_TYPE"] = agent_type
    mcp_status = payload.get("mcp_status") or payload.get("mcpStatus")
    if isinstance(mcp_status, dict):
        try:
            os.environ["OPENCODE_MCP_STATUS"] = json.dumps(mcp_status, separators=(",", ":"))
        except (TypeError, ValueError):
            pass
    resolved_config = payload.get("resolved_config") or payload.get("resolvedConfig")
    if isinstance(resolved_config, dict):
        try:
            config = _opencode_apply_mcp_status(_redact_secret_object(resolved_config), mcp_status)
            os.environ["OPENCODE_RESOLVED_CONFIG"] = json.dumps(config, separators=(",", ":"))
        except (TypeError, ValueError):
            pass


def _session_env(payload: dict[str, Any]) -> dict[str, str]:
    """Provider fields recorded at SessionStart for fallback at later turns.

    Capture opencode's approval/sandbox policy when the hook payload carries it.
    The authoritative policy lives in the rollout's `turn_context` (replayed
    verbatim on resume), but recording it in `session_env` makes the checkpoint
    metadata self-describing. Best-effort: opencode does not always deliver
    these at the hook, so they're included only when present.
    """
    fields = {
        "model": _first_string(payload, "model"),
        "effort": _opencode_effort(payload),
        "permission_mode": _first_string(payload, "permission_mode", "permissionMode"),
        "mode": _opencode_mode(payload),
        "agent_type": _first_string(payload, "agent_type", "agentType"),
        "approval_policy": _first_string(payload, "approval_policy", "approvalPolicy"),
        "sandbox_mode": _first_string(payload, "sandbox_mode", "sandboxMode", "sandbox_policy", "sandboxPolicy"),
    }
    # OC2: Extract permission array from session_info if present (subagents with
    # task deny policies, for example). Serialize to JSON for storage as string.
    session_info = payload.get("session_info")
    if isinstance(session_info, dict) and "permission" in session_info:
        try:
            fields["permission"] = json.dumps(session_info["permission"])
        except (TypeError, ValueError):
            pass  # Skip if not serializable
    mcp_status = payload.get("mcp_status") or payload.get("mcpStatus")
    if isinstance(mcp_status, dict):
        try:
            fields["mcp_status"] = json.dumps(mcp_status, separators=(",", ":"))
        except (TypeError, ValueError):
            pass
    resolved_config = payload.get("resolved_config") or payload.get("resolvedConfig")
    if isinstance(resolved_config, dict):
        try:
            config = _opencode_apply_mcp_status(_redact_secret_object(resolved_config), mcp_status)
            fields["resolved_config"] = json.dumps(config, separators=(",", ":"))
        except (TypeError, ValueError):
            pass
    config_content = _opencode_config_content(
        resolved_config if isinstance(resolved_config, dict) else None,
        mcp_status if isinstance(mcp_status, dict) else None,
    )
    if config_content:
        fields["opencode_config_content"] = config_content
    permission = os.environ.get("OPENCODE_PERMISSION")
    if permission:
        fields["opencode_permission"] = permission
    runtime_env = _opencode_runtime_env()
    if runtime_env:
        fields["opencode_runtime_env"] = json.dumps(runtime_env, separators=(",", ":"))
    config_skill_roots = _resolved_config_skill_paths(resolved_config if isinstance(resolved_config, dict) else None)
    if config_skill_roots:
        fields["opencode_config_skill_roots"] = json.dumps(config_skill_roots, separators=(",", ":"))
    return {key: value for key, value in fields.items() if value}


def _opencode_effort(payload: dict[str, Any]) -> str | None:
    value = _first_string(payload, "effort", "thinking_effort", "thinkingEffort", "variant")
    if value:
        return value
    value = _opencode_model_variant(payload)
    if value:
        return value
    for message in reversed(_opencode_raw_messages(payload)):
        info = message.get("info")
        if isinstance(info, dict):
            value = _first_string(info, "effort", "thinking_effort", "thinkingEffort", "variant")
            if value:
                return value
            value = _opencode_model_variant(info)
            if value:
                return value
    session_info = payload.get("session_info")
    if isinstance(session_info, dict):
        return _first_string(session_info, "effort", "thinking_effort", "thinkingEffort", "variant") or _opencode_model_variant(
            session_info
        )
    return None


def _opencode_model_variant(value: dict[str, Any]) -> str | None:
    model = value.get("model")
    if not isinstance(model, dict):
        return None
    variant = model.get("variant")
    return variant if isinstance(variant, str) and variant else None


def _opencode_mode(payload: dict[str, Any]) -> str | None:
    value = _first_string(payload, "collaboration_mode_kind", "collaborationModeKind", "mode")
    if value:
        return value
    for message in reversed(_opencode_raw_messages(payload)):
        info = message.get("info")
        if isinstance(info, dict):
            role = info.get("role")
            mode = info.get("mode")
            if role == "assistant" and isinstance(mode, str) and mode:
                return mode
    session_info = payload.get("session_info")
    if isinstance(session_info, dict):
        return _first_string(session_info, "mode")
    return None


def _opencode_raw_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages = payload.get("raw_messages") or payload.get("rawMessages")
    if not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, dict)]


def _opencode_config_content(
    resolved_config: dict[str, Any] | None,
    mcp_status: dict[str, Any] | None = None,
) -> str | None:
    if resolved_config:
        try:
            config = _opencode_apply_mcp_status(_redact_secret_object(resolved_config), mcp_status)
            return json.dumps(config, separators=(",", ":"))
        except (TypeError, ValueError):
            pass
    config_content = os.environ.get("OPENCODE_CONFIG_CONTENT")
    if config_content:
        redacted = _redact_secret_text(config_content)
        if not mcp_status:
            return redacted
        try:
            config = json.loads(redacted)
        except json.JSONDecodeError:
            return redacted
        if isinstance(config, dict):
            try:
                return json.dumps(_opencode_apply_mcp_status(config, mcp_status), separators=(",", ":"))
            except (TypeError, ValueError):
                return redacted
        return redacted
    return None


def _opencode_apply_mcp_status(config: object, mcp_status: dict[str, Any] | None) -> object:
    if not isinstance(config, dict) or not isinstance(mcp_status, dict):
        return config
    result = dict(config)
    existing_mcp = result.get("mcp")
    mcp = dict(existing_mcp) if isinstance(existing_mcp, dict) else {}
    for name, value in mcp_status.items():
        enabled = _opencode_mcp_enabled(value)
        if enabled is None:
            continue
        existing_server = mcp.get(str(name))
        server = dict(existing_server) if isinstance(existing_server, dict) else {}
        server["enabled"] = enabled
        mcp[str(name)] = server
    if mcp:
        result["mcp"] = mcp
    return result


def _opencode_mcp_enabled(value: object) -> bool | None:
    status = value.get("status") if isinstance(value, dict) else value
    if status in {"connected", "active"}:
        return True
    if status in {"disabled", "inactive"}:
        return False
    return None


def _attach_opencode_sqlite_state(payload: dict[str, Any], session_id: str) -> None:
    state = _opencode_sqlite_state(session_id)
    if state.get("session_messages"):
        payload["session_messages"] = state["session_messages"]
    if state.get("todos"):
        payload["todos"] = state["todos"]


def _opencode_sqlite_state(session_id: str) -> dict[str, list[dict[str, Any]]]:
    db_path = _opencode_db_path()
    if not db_path.exists():
        return {}
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            session_messages = [
                {
                    "id": row[0],
                    "sessionID": row[1],
                    "type": row[2],
                    "time": {"created": row[3], "updated": row[4]},
                    "data": _json_or_text(row[5]),
                }
                for row in conn.execute(
                    "SELECT id, session_id, type, time_created, time_updated, data "
                    "FROM session_message WHERE session_id = ? ORDER BY time_created, id",
                    (session_id,),
                )
            ]
            todos = [
                {
                    "sessionID": row[0],
                    "content": row[1],
                    "status": row[2],
                    "priority": row[3],
                    "position": row[4],
                    "time": {"created": row[5], "updated": row[6]},
                }
                for row in conn.execute(
                    "SELECT session_id, content, status, priority, position, time_created, time_updated "
                    "FROM todo WHERE session_id = ? ORDER BY position",
                    (session_id,),
                )
            ]
        finally:
            conn.close()
    except Exception:
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    if session_messages:
        result["session_messages"] = session_messages
    if todos:
        result["todos"] = todos
    return result


def _opencode_db_path() -> Path:
    data_home = Path(
        os.environ.get("OPENCODE_DATA_DIR")
        or os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    )
    return data_home.expanduser() / "opencode" / "opencode.db"


def _json_or_text(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


_SECRET_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "bearer",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
)


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    for key in ("resolved_config", "resolvedConfig"):
        value = sanitized.get(key)
        if isinstance(value, dict):
            sanitized[key] = _redact_secret_object(value)
    return sanitized


def _redact_secret_object(value: Any, key: str | None = None) -> Any:
    if key and _secret_key(key):
        return "***redacted***"
    if isinstance(value, dict):
        return {str(k): _redact_secret_object(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_secret_object(item) for item in value]
    return value


def _redact_secret_text(text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    try:
        return json.dumps(_redact_secret_object(data), separators=(",", ":"))
    except (TypeError, ValueError):
        return text


def _secret_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _resolved_config_skill_paths(resolved_config: dict[str, Any] | None) -> list[str]:
    if not isinstance(resolved_config, dict):
        return []
    skills = resolved_config.get("skills")
    if not isinstance(skills, dict):
        return []
    paths = skills.get("paths")
    if not isinstance(paths, list):
        return []
    return [item for item in paths if isinstance(item, str) and item]


def _opencode_runtime_env() -> dict[str, str]:
    result = {key: os.environ[key] for key in _RUNTIME_ENV_KEYS if os.environ.get(key)}
    if os.environ.get("OPENCODE_PERMISSION"):
        result["OPENCODE_PERMISSION"] = os.environ["OPENCODE_PERMISSION"]
    return {key: _redact_secret_text(value) for key, value in result.items()}


def _turn_record(payload: dict[str, Any]) -> TurnRecord:
    # OpenCode plugin sends messages array; extract last user/assistant pair
    messages = payload.get("messages", [])
    user_message = ""
    assistant_text = ""

    if messages:
        # Find the last user message and last assistant message
        for msg in reversed(messages):
            if isinstance(msg, dict):
                role = msg.get("role")
                content = msg.get("content", "")
                if role == "user" and not user_message:
                    user_message = content if isinstance(content, str) else str(content)
                elif role == "assistant" and not assistant_text:
                    assistant_text = content if isinstance(content, str) else str(content)
            if user_message and assistant_text:
                break

    # Fallback to direct fields if messages array not available
    if not user_message:
        user_message = _first_string(payload, "prompt", "user_message", "userMessage", "input") or ""
    if not assistant_text:
        assistant_text = _first_string(
            payload,
            "last_assistant_message",
            "assistant_text",
            "assistantText",
            "response",
            "output",
        ) or ""

    return TurnRecord(
        user_message=user_message,
        assistant_text=assistant_text,
        tool_calls=_tool_calls(payload),
        metadata={"hook_payload": payload},
    )


def _tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tool_name = _first_string(payload, "tool_name", "toolName")
    if tool_name is None:
        return []
    call: dict[str, Any] = {"tool_name": tool_name}
    for source_key, target_key in (
        ("tool_use_id", "tool_use_id"),
        ("toolUseId", "tool_use_id"),
        ("tool_input", "tool_input"),
        ("toolInput", "tool_input"),
        ("tool_response", "tool_response"),
        ("toolResponse", "tool_response"),
    ):
        if source_key in payload:
            call[target_key] = payload[source_key]
    return [call]


def _trajectory_ref(payload: dict[str, Any], provider: str) -> TrajectoryReference | None:
    transcript_path = _first_string(payload, "transcript_path", "transcriptPath")
    if transcript_path is None:
        return None
    turn_id = payload.get("turn_id") or payload.get("turnId")
    return jsonl_ref_for_turn(provider, Path(transcript_path), turn_id, codex_key, claim_leading_keyless=True)


def _subagent_trajectory_ref(payload: dict[str, Any], transcript_path: str | None) -> TrajectoryReference | None:
    if transcript_path is None:
        return None
    # A subagent's dedicated rollout carries inherited ancestor session_meta
    # records at the head, then the subagent's OWN turns. Capture everything
    # after the leading meta block (the full subagent conversation), not just the
    # SubagentStop turn.
    return jsonl_after_leading_metas(
        "opencode",
        Path(transcript_path),
        is_leading_meta=lambda record: record.get("type") == "session_meta",
    )


def _write_ok() -> None:
    print("{}")


if __name__ == "__main__":
    raise SystemExit(main())
