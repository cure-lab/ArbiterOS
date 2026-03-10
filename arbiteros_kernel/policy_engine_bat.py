from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Tuple

try:
    import jsonschema  # type: ignore
except Exception:
    jsonschema = None


# -----------------------------
# Data structures
# -----------------------------


@dataclass
class PolicyDecision:
    """
    Backward compatible:
      - allow/reason/trace_id are preserved.

    Extended fields for instruction-aware + EFSM:
      - effect: ALLOW/BLOCK/WARN/LOG_ONLY/REQUIRE_APPROVAL/TRANSFORM
      - next_state: EFSM next state (if enabled)
      - matched_transition: transition id (debug/audit)
      - meta: extra info for debugging / UI
    """

    allow: bool
    reason: str = ""
    trace_id: str = ""  # stable id (op_id) for user-facing correlation / approvals
    effect: str = "ALLOW"
    next_state: Optional[str] = None
    matched_transition: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


class PolicyState:
    """
    Existing minimal state:
    - consecutive tool repetition
    - rolling window rate limits
    - taint tracking for provenance checks

    Extended state:
    - recent instruction history (for binding / audits)
    - EFSM runtime state + vars
    - event dedupe (for retries)
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()

        # rate-limit state
        self.last_tool: Dict[str, str] = {}
        self.repeat_count: Dict[str, int] = defaultdict(int)
        self.calls_window: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)

        # taint state (session-scoped)
        self.last_untrusted_ts: Dict[str, float] = {}
        self.last_untrusted_source: Dict[str, str] = {}
        self.taint_snippets: Dict[str, Deque[str]] = defaultdict(lambda: deque())

        # dedupe taint events per session
        self.taint_event_keys: Dict[str, Deque[str]] = defaultdict(deque)
        self.taint_event_key_set: Dict[str, set[str]] = defaultdict(set)

        # instruction history: session -> deque[instruction]
        self.instr_hist: Dict[str, Deque[Dict[str, Any]]] = defaultdict(deque)

        # EFSM runtime
        self.efsm_state: Dict[str, str] = {}  # session -> state
        self.efsm_vars: Dict[
            str, Dict[str, Any]
        ] = {}  # session -> vars (extended memory)

        # Event dedupe per session (to survive retries)
        self.event_keys: Dict[str, Deque[str]] = defaultdict(deque)
        self.event_key_set: Dict[str, set[str]] = defaultdict(set)


# -----------------------------
# Helpers
# -----------------------------


def _now() -> float:
    return time.time()


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()


def _safe_json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_json_loads(s: Any) -> Any:
    if not isinstance(s, str):
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _expand_home(p: str) -> str:
    if not isinstance(p, str):
        return ""
    return os.path.expandvars(os.path.expanduser(p))


def normalize_path(p: str) -> str:
    p2 = os.path.expandvars(os.path.expanduser((p or "").strip()))
    p2 = p2.replace("\\", "/")
    p2 = re.sub(r"/+", "/", p2)
    p2 = os.path.normpath(p2)
    if p2 == ".":
        p2 = ""
    return p2


def canonicalize_args(args: Any) -> Any:
    if isinstance(args, dict):
        out: Dict[str, Any] = {}
        for k in sorted(args.keys(), key=lambda x: str(x)):
            v = args[k]
            if isinstance(v, str) and str(k).lower() in (
                "path",
                "file_path",
                "file",
                "filename",
                "src",
                "dst",
                "directory",
                "dir",
            ):
                out[k] = normalize_path(v)
            else:
                out[k] = canonicalize_args(v)
        return out
    if isinstance(args, list):
        return [canonicalize_args(x) for x in args]
    return args


def redact_args(
    args: Any, redact_keys: Tuple[str, ...] = ("api_key", "token", "password", "secret")
) -> Any:
    if isinstance(args, dict):
        out: Dict[str, Any] = {}
        for k, v in args.items():
            k_lower = str(k).lower()
            if any(rk in k_lower for rk in redact_keys):
                out[k] = "***REDACTED***"
            else:
                out[k] = redact_args(v, redact_keys)
        return out
    if isinstance(args, list):
        return [redact_args(x, redact_keys) for x in args]
    return args


def _lower_contains_any(hay: str, needles: List[str]) -> bool:
    h = (hay or "").lower()
    for n in needles:
        nn = (n or "").strip().lower()
        if nn and nn in h:
            return True
    return False


def _extract_snippet_variants(line: str) -> List[str]:
    """
    Produce multiple variants for Mode-B matching.
    Example:
      "exec: echo INJECTED_FROM_FILE" -> also store "echo INJECTED_FROM_FILE"
    """
    t = re.sub(r"\s+", " ", (line or "")).strip()
    if not t:
        return []

    variants: List[str] = [t]

    # strip quoting
    tq = t.strip("`\"' ")
    if tq and tq != t:
        variants.append(tq)

    # "<tool>: <rhs>" -> keep rhs
    m = re.match(r"^(?P<tool>[A-Za-z_][\w-]*)\s*:\s*(?P<rhs>.+)$", t)
    if m:
        rhs = m.group("rhs").strip()
        if rhs:
            variants.append(rhs)
            variants.append(rhs.strip("`\"' "))

    # "... exec: <cmd> ..." (end-of-line)
    m2 = re.search(r"\bexec\s*:\s*(?P<cmd>[^\n\r]+)$", t, flags=re.IGNORECASE)
    if m2:
        cmd = m2.group("cmd").strip()
        if cmd:
            variants.append(cmd)
            variants.append(cmd.strip("`\"' "))

    # normalize whitespace + lowercase variant (useful for loose matching)
    variants.append(re.sub(r"\s+", " ", t).strip().lower())

    # de-dup
    seen: set[str] = set()
    out: List[str] = []
    for v in variants:
        vv = (v or "").strip()
        if not vv:
            continue
        if vv in seen:
            continue
        seen.add(vv)
        out.append(vv)
    return out


def _collect_paths_from_args(args: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    if not isinstance(args, dict):
        return paths
    for k, v in args.items():
        if isinstance(v, str) and str(k).lower() in (
            "path",
            "file_path",
            "file",
            "filename",
            "src",
            "dst",
            "directory",
            "dir",
        ):
            if v.strip():
                paths.append(normalize_path(v))
    return paths


_QUOTED_RE = re.compile(r"[\"“”'`「」](.{3,180})[\"“”'`「」]")
_ABS_PATH_RE = re.compile(r"(?:(?:~|/)[\w.\-~/]+)")
_REL_PATH_RE = re.compile(r"(?:\b[\w.\-]+(?:/[\w.\-]+)+\b)")


def _extract_paths_from_text(text: str, max_items: int = 32) -> List[str]:
    """
    Best-effort path extraction from PLAN text / user message.
    Not perfect, but good enough for plan-to-act binding.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    t = text.strip()
    out: List[str] = []

    def _push(p: str) -> None:
        if not p or not isinstance(p, str):
            return
        pp = normalize_path(p)
        if not pp:
            return
        if pp not in out:
            out.append(pp)

    # quoted substrings (often paths)
    for m in _QUOTED_RE.finditer(t):
        _push(m.group(1))

    # absolute-like (~ or /)
    for m in _ABS_PATH_RE.finditer(t):
        _push(m.group(0))

    # relative a/b/c
    for m in _REL_PATH_RE.finditer(t):
        _push(m.group(0))

    return out[:max_items]


def _as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


# -----------------------------
# Policy Engine + EFSM
# -----------------------------


class PolicyEngine:
    def __init__(self, config: Dict[str, Any], audit_path: str = "") -> None:
        self.cfg = config or {}
        self.state = PolicyState()
        self.audit_path = audit_path or ""

        # Guard/action dispatch tables for EFSM
        self._guards: Dict[str, Callable[[Dict[str, Any]], bool]] = {
            "always": lambda ctx: True,
            "has_recent_plan": self._g_has_recent_plan,
            "path_in_recent_plan": self._g_path_in_recent_plan,
            "tool_in_recent_plan": self._g_tool_in_recent_plan,
            "approved_file_present": self._g_approved_file_present,
            "user_mentions_trace_id": self._g_user_mentions_trace_id,
            "tainted_recently": self._g_tainted_recently,
        }
        self._actions: Dict[str, Callable[[Dict[str, Any]], None]] = {
            "cache_plan": self._a_cache_plan,
            "clear_plan": self._a_clear_plan,
            "set_pending": self._a_set_pending,
            "clear_pending": self._a_clear_pending,
            "mark_last_user": self._a_mark_last_user,
        }

    # -------------------------
    # EFSM config
    # -------------------------

    def _efsm_cfg(self) -> Dict[str, Any]:
        ef = self.cfg.get("efsm", {}) or {}
        return ef if isinstance(ef, dict) else {}

    def _efsm_enabled(self) -> bool:
        return bool(self._efsm_cfg().get("enabled", False))

    def _efsm_initial(self) -> str:
        v = self._efsm_cfg().get("initial", "IDLE")
        return str(v).strip() if isinstance(v, str) and v.strip() else "IDLE"

    def _efsm_hist_max(self) -> int:
        v = self._efsm_cfg().get("instruction_history_max", 128)
        try:
            n = int(v)
            return max(16, min(n, 4096))
        except Exception:
            return 128

    def _efsm_event_dedupe_max(self) -> int:
        v = self._efsm_cfg().get("event_dedupe_max", 1024)
        try:
            n = int(v)
            return max(64, min(n, 8192))
        except Exception:
            return 1024

    def _efsm_transitions(self) -> List[Dict[str, Any]]:
        """
        User-configured transitions, else a safe default EFSM:
          IDLE --PLAN--> PLANNED (cache_plan)
          PLANNED --WRITE/EXEC--> EXECUTING if path/tool in plan
          otherwise REQUIRE_APPROVAL
          any --USER_MESSAGE--> (mark_last_user) (no state change)
        """
        ef = self._efsm_cfg()
        trs = ef.get("transitions")
        if isinstance(trs, list) and trs:
            cleaned: List[Dict[str, Any]] = []
            for t in trs:
                if isinstance(t, dict):
                    cleaned.append(dict(t))
            cleaned.sort(key=lambda x: int(x.get("priority", 0) or 0), reverse=True)
            return cleaned

        return [
            {
                "id": "idle_plan",
                "from": "IDLE",
                "event": "PLAN",
                "to": "PLANNED",
                "actions": ["cache_plan"],
                "effect": "ALLOW",
            },
            {
                "id": "any_user_msg",
                "from": "*",
                "event": "USER_MESSAGE",
                "to": "*",
                "actions": ["mark_last_user"],
                "effect": "ALLOW",
            },
            {
                "id": "planned_write_ok",
                "from": "PLANNED",
                "event": ["WRITE", "EXEC"],
                "to": "EXECUTING",
                "guard": "path_in_recent_plan",
                "effect": "ALLOW",
            },
            {
                "id": "planned_tool_ok",
                "from": "PLANNED",
                "event": ["WRITE", "EXEC"],
                "to": "EXECUTING",
                "guard": "tool_in_recent_plan",
                "effect": "ALLOW",
            },
            {
                "id": "planned_write_need_approval",
                "from": "PLANNED",
                "event": ["WRITE", "EXEC"],
                "to": "WAIT_APPROVAL",
                "effect": "REQUIRE_APPROVAL",
                "actions": ["set_pending"],
            },
            {
                "id": "idle_write_need_approval",
                "from": "IDLE",
                "event": ["WRITE", "EXEC"],
                "to": "WAIT_APPROVAL",
                "effect": "REQUIRE_APPROVAL",
                "actions": ["set_pending"],
            },
            {
                "id": "tainted_respond_block",
                "from": "*",
                "event": "RESPOND",
                "to": "*",
                "guard": "tainted_recently",
                "effect": "BLOCK",
            },
        ]

    # -------------------------
    # Instruction/tool inference
    # -------------------------

    def _infer_instruction_type_for_tool(self, tool: str) -> str:
        t = (tool or "").strip().lower()
        mapping = self.cfg.get("tool_to_instruction_type", {}) or {}
        if isinstance(mapping, dict):
            v = mapping.get(t)
            if isinstance(v, str) and v.strip():
                return v.strip().upper()

        if t in {"write", "fs_write", "file_write"}:
            return "WRITE"
        if t in {"read", "fs_read", "file_read"}:
            return "READ"
        if t in {"exec", "shell", "run", "cmd"}:
            return "EXEC"
        return "EXEC"

    def _infer_category_for_instruction(
        self, instruction_type: Optional[str]
    ) -> Optional[str]:
        it = (instruction_type or "").strip().upper()
        if not it:
            return None
        if it in {"REASON", "PLAN", "CRITIQUE"}:
            return "COGNITIVE.Reasoning"
        if it in {"RESPOND", "ASK", "USER_MESSAGE"}:
            return "EXECUTION.Human"
        if it in {"READ", "WRITE", "EXEC", "WAIT"}:
            return "EXECUTION.Env"
        if it in {"HANDOFF"}:
            return "EXECUTION.Agent"
        return None

    @staticmethod
    def _is_tool_instruction(it: str, content: Any) -> bool:
        if it not in {"EXEC", "READ", "WRITE"}:
            return False
        if not isinstance(content, dict):
            return False
        return isinstance(content.get("tool_name"), str) and isinstance(
            content.get("arguments"), dict
        )

    @staticmethod
    def _tool_and_args_from_content(
        content: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        tool = (
            content.get("tool_name")
            if isinstance(content.get("tool_name"), str)
            else ""
        ) or "unknown_tool"
        args = (
            content.get("arguments")
            if isinstance(content.get("arguments"), dict)
            else {}
        )
        return tool.strip() or "unknown_tool", canonicalize_args(args)

    # -------------------------
    # Taint config compatibility
    # -------------------------

    def _taint_sources(self) -> List[str]:
        ta = self.cfg.get("taint", {}) or {}
        if isinstance(ta.get("sources"), list):
            return [str(x).strip() for x in ta.get("sources") if str(x).strip()]
        us = ta.get("untrusted_sources", {}) or {}
        tools = us.get("tools")
        if isinstance(tools, list):
            return [str(x).strip() for x in tools if str(x).strip()]
        return []

    def _taint_sinks(self) -> List[str]:
        ta = self.cfg.get("taint", {}) or {}
        if isinstance(ta.get("sinks"), list):
            return [str(x).strip() for x in ta.get("sinks") if str(x).strip()]
        hr = ta.get("high_risk_tools")
        if isinstance(hr, list):
            return [str(x).strip() for x in hr if str(x).strip()]
        return []

    def _taint_instruction_sinks(self) -> List[str]:
        ta = self.cfg.get("taint", {}) or {}
        v = ta.get("instruction_sinks")
        if isinstance(v, list):
            return [
                str(x).strip().upper() for x in v if isinstance(x, str) and x.strip()
            ]
        return []

    def _taint_patterns(self) -> List[str]:
        ta = self.cfg.get("taint", {}) or {}
        us = ta.get("untrusted_sources", {}) or {}
        pats = us.get("patterns")
        if isinstance(pats, list):
            return [str(x) for x in pats if isinstance(x, str) and x.strip()]
        return []

    # -------------------------
    # Approval (out-of-band) config
    # -------------------------

    def _approval_cfg(self) -> Dict[str, Any]:
        ta = self.cfg.get("taint", {}) or {}
        ap = ta.get("approval", {}) or {}
        return ap if isinstance(ap, dict) else {}

    def _approval_mode(self) -> str:
        ap = self._approval_cfg()
        mode = ap.get("mode")
        return str(mode).strip().lower() if isinstance(mode, str) else "none"

    def _approval_dir(self) -> str:
        ap = self._approval_cfg()
        d = ap.get("dir")
        if isinstance(d, str) and d.strip():
            return _expand_home(d.strip())
        return _expand_home("~/.arbiteros/approvals")

    def _approval_ttl(self) -> int:
        ap = self._approval_cfg()
        ttl = ap.get("ttl_seconds")
        try:
            v = int(ttl)
            return v if v > 0 else 120
        except Exception:
            return 120

    def _approval_delete_on_use(self) -> bool:
        ap = self._approval_cfg()
        v = ap.get("delete_on_use")
        return bool(v) if isinstance(v, bool) else True

    def _approval_show_instructions(self) -> bool:
        ap = self._approval_cfg()
        v = ap.get("show_instructions")
        return bool(v) if isinstance(v, bool) else True

    def _approval_file_candidates(self, trace_id: str, scope: str) -> List[str]:
        d = self._approval_dir()
        scope = (scope or "op").strip() or "op"
        return [
            os.path.join(d, f"{trace_id}.{scope}.allow"),
            os.path.join(d, f"{trace_id}.allow"),
        ]

    def _approval_granted(self, trace_id: str, scope: str) -> bool:
        if self._approval_mode() != "file":
            return False
        ttl = self._approval_ttl()
        now = _now()
        for fp in self._approval_file_candidates(trace_id, scope):
            try:
                st = os.stat(fp)
            except FileNotFoundError:
                continue
            except Exception:
                continue

            if ttl > 0 and (now - float(st.st_mtime)) > ttl:
                continue

            if self._approval_delete_on_use():
                try:
                    os.remove(fp)
                except Exception:
                    pass
            return True
        return False

    def _format_approval_hint(self, *, trace_id: str, scope: str, base: str) -> str:
        if not self._approval_show_instructions():
            return base
        if self._approval_mode() == "file":
            d = self._approval_dir()
            ttl = self._approval_ttl()
            fp = os.path.join(d, f"{trace_id}.{scope}.allow")
            return (
                f"{base}. If you intended this, approve once locally and retry: "
                f"`mkdir -p {d} && touch {fp}` (valid ~{ttl}s)."
            )
        return base

    # -------------------------
    # Session reset
    # -------------------------

    def reset_session(self, session_id: str) -> None:
        s = (session_id or "").strip()
        if not s:
            return
        with self.state.lock:
            self.state.last_untrusted_ts.pop(s, None)
            self.state.last_untrusted_source.pop(s, None)
            self.state.taint_snippets.pop(s, None)
            self.state.taint_event_keys.pop(s, None)
            self.state.taint_event_key_set.pop(s, None)

            self.state.instr_hist.pop(s, None)
            self.state.efsm_state.pop(s, None)
            self.state.efsm_vars.pop(s, None)

            self.state.event_keys.pop(s, None)
            self.state.event_key_set.pop(s, None)

    # -------------------------
    # Taint runtime APIs
    # -------------------------

    def observe_untrusted_output(
        self,
        *,
        session_id: str,
        source_tool: str,
        tool_call_id: Optional[str],
        output_text: str,
    ) -> None:
        taint_cfg = self.cfg.get("taint", {}) or {}
        if not bool(taint_cfg.get("enabled", False)):
            return

        sources = set(self._taint_sources())
        src_tool = (source_tool or "").strip() or "unknown_source"
        if src_tool not in sources:
            return

        if not isinstance(output_text, str) or not output_text.strip():
            return

        patterns = self._taint_patterns()
        if patterns:
            if not _lower_contains_any(output_text, patterns):
                return

        s = (session_id or "").strip() or "unknown_session"
        event_dedupe_max = int(taint_cfg.get("event_dedupe_max", 512) or 512)
        snippet_cache_max = int(taint_cfg.get("snippet_cache_max", 80) or 80)

        key_material = f"{tool_call_id or 'none'}::{output_text[:4096]}"
        event_key = _sha256(key_material)

        min_len = int(taint_cfg.get("min_snippet_len", 6) or 6)
        max_len = int(taint_cfg.get("max_snippet_len", 512) or 512)
        max_add = int(taint_cfg.get("max_snippets_per_event", 50) or 50)

        with self.state.lock:
            if event_key in self.state.taint_event_key_set[s]:
                return
            self.state.taint_event_key_set[s].add(event_key)
            dq_keys = self.state.taint_event_keys[s]
            dq_keys.append(event_key)
            while len(dq_keys) > event_dedupe_max:
                oldest = dq_keys.popleft()
                self.state.taint_event_key_set[s].discard(oldest)

            self.state.last_untrusted_ts[s] = _now()
            self.state.last_untrusted_source[s] = src_tool

            dq_snip = self.state.taint_snippets[s]
            while len(dq_snip) > snippet_cache_max:
                dq_snip.popleft()

            added = 0
            for raw_line in output_text.splitlines():
                for v in _extract_snippet_variants(raw_line):
                    if len(v) < min_len or len(v) > max_len:
                        continue
                    dq_snip.append(v)
                    added += 1
                    if added >= max_add:
                        break
                if added >= max_add:
                    break

            while len(dq_snip) > snippet_cache_max:
                dq_snip.popleft()

    # -------------------------
    # Instruction observation (bridge to InstructionBuilder)
    # -------------------------

    def observe_instruction(
        self, *, session_id: str, instruction: Dict[str, Any]
    ) -> None:
        """
        Call this when your InstructionBuilder emits an instruction.
        Bridge: instruction -> engine (history + EFSM events).
        """
        s = (session_id or "").strip() or "unknown_session"
        if not isinstance(instruction, dict):
            return

        hist_max = self._efsm_hist_max()
        with self.state.lock:
            dq = self.state.instr_hist[s]
            dq.append(dict(instruction))
            while len(dq) > hist_max:
                dq.popleft()

            if s not in self.state.efsm_vars:
                self.state.efsm_vars[s] = {}
            if s not in self.state.efsm_state:
                self.state.efsm_state[s] = self._efsm_initial()

        itype = (instruction.get("instruction_type") or "").strip().upper()
        if not itype:
            return

        cat = instruction.get("instruction_category")
        content = instruction.get("content")

        payload: Dict[str, Any] = {
            "instruction_type": itype,
            "category": cat,
            "content": content,
        }

        # IMPORTANT: for tool-like instruction, unpack tool/args for EFSM guards.
        if self._is_tool_instruction(itype, content):
            tool, args = self._tool_and_args_from_content(content)  # canonicalized
            payload["tool"] = tool
            payload["args"] = args

        # Optional: pass stable trace_id if upstream attaches one
        if isinstance(instruction.get("trace_id"), str) and instruction.get("trace_id"):
            payload["trace_id"] = instruction["trace_id"]

        self.on_event(session_id=s, event=itype, payload=payload)

    def observe_user_message(
        self, *, session_id: str, text: str, fingerprint: Optional[str] = None
    ) -> None:
        s = (session_id or "").strip() or "unknown_session"
        payload = {"text": text, "fingerprint": fingerprint}
        self.on_event(session_id=s, event="USER_MESSAGE", payload=payload)

    # -------------------------
    # Backward compatible tool pre
    # -------------------------

    def pre(
        self,
        *,
        session_id: str,
        tool: str,
        args: Dict[str, Any],
        instruction_type: Optional[str] = None,
        category: Optional[str] = None,
        user_key: Optional[str] = None,
    ) -> PolicyDecision:
        """
        Backward compatibility:
        Convert tool-call into an instruction and evaluate via pre_instruction().
        """
        tool_norm = (tool.strip() if isinstance(tool, str) else "") or "unknown_tool"
        session_norm = (
            session_id.strip() if isinstance(session_id, str) else ""
        ) or "unknown_session"

        canon_args = canonicalize_args(args if isinstance(args, dict) else {})
        canon_json = _safe_json_dumps(canon_args)

        inferred_type = self._infer_instruction_type_for_tool(tool_norm)
        inferred_cat = self._infer_category_for_instruction(inferred_type)
        prefer_inferred = bool(
            self.cfg.get("prefer_inferred_instruction_type_for_tools", True)
        )
        eff_type = (
            inferred_type
            if (
                prefer_inferred
                and inferred_type
                and inferred_type != (instruction_type or "").strip().upper()
            )
            else (instruction_type or inferred_type)
        )
        eff_type = (eff_type or inferred_type or "EXEC").strip().upper()
        eff_cat = category or inferred_cat or "EXECUTION.Env"

        # Stable decision id (same as old pre())
        trace_id = _sha256(f"{session_norm}|{tool_norm}|{canon_json}")[:24]

        tool_instr: Dict[str, Any] = {
            "id": "",  # optional
            "instruction_type": eff_type,
            "instruction_category": eff_cat,
            "content": {
                "tool_name": tool_norm,
                "tool_call_id": None,
                "arguments": canon_args,
            },
            "trace_id": trace_id,
        }

        return self.pre_instruction(
            session_id=session_norm,
            instruction_type=eff_type,
            category=eff_cat,
            content=tool_instr["content"],
            instruction=tool_instr,
            user_key=user_key,
        )

    # -------------------------
    # Unified instruction-level pre-check
    # -------------------------

    def pre_instruction(
        self,
        *,
        session_id: str,
        instruction_type: str,
        category: Optional[str],
        content: Any,
        instruction: Optional[Dict[str, Any]] = None,
        user_key: Optional[str] = None,
    ) -> PolicyDecision:
        """
        Unified entrypoint for instruction-based policy:
        - Non-tool atomic actions: PLAN/RESPOND/ASK/USER_MESSAGE/...
        - Tool-like actions represented as instructions: READ/WRITE/EXEC with content.tool_name/arguments

        Enforces (in order):
        1) static allow/deny (tool + instruction_type + category)
        2) security label policy (instruction.security_type)
        3) taint sinks (instruction-level sinks, and tool sinks for tool instructions)
        4) EFSM gate (plan-to-act binding, approvals, etc.)
        5) tool-argument checks (schema/path/budget/rate/user-agg) for tool instructions
        """
        session_norm = (session_id or "").strip() or "unknown_session"
        it = (instruction_type or "").strip().upper() or "REASON"
        cat = category or self._infer_category_for_instruction(it)

        # Stable trace_id: prefer instruction.trace_id, else compute.
        trace_id: str = ""
        if (
            isinstance(instruction, dict)
            and isinstance(instruction.get("trace_id"), str)
            and instruction.get("trace_id")
        ):
            trace_id = str(instruction["trace_id"])
        else:
            # tool instruction uses stable hash(session|tool|canon_args); non-tool uses (session|@instr|canon_payload)
            if self._is_tool_instruction(it, content):
                tool0, args0 = self._tool_and_args_from_content(content)
                canon_json0 = _safe_json_dumps(args0)
                trace_id = _sha256(f"{session_norm}|{tool0}|{canon_json0}")[:24]
            else:
                canon_payload = _safe_json_dumps(
                    {"it": it, "cat": cat, "content": content}
                )
                trace_id = _sha256(f"{session_norm}|@instr|{canon_payload}")[:24]

        audit_id = uuid.uuid4().hex

        is_tool_instr = self._is_tool_instruction(it, content)
        tool_name = "@instruction"
        tool_args: Dict[str, Any] = {}
        if is_tool_instr:
            tool_name, tool_args = self._tool_and_args_from_content(content)

        # 1) static allow/deny
        ok, reason = self._check_static_allowdeny(
            tool_name if is_tool_instr else "@instruction", it, cat
        )
        if not ok:
            self._audit(
                "pre" if is_tool_instr else "instr",
                trace_id,
                session_norm,
                tool_name if is_tool_instr else "@instruction",
                tool_args
                if is_tool_instr
                else {"instruction_type": it, "category": cat},
                "BLOCK",
                reason,
                extra={"audit_id": audit_id, "instruction_type": it, "category": cat},
            )
            return PolicyDecision(False, reason, trace_id, effect="BLOCK")

        # 2) security policy (label-aware)
        sec = None
        if isinstance(instruction, dict):
            st = instruction.get("security_type")
            if isinstance(st, dict):
                sec = st
        ok, reason = self._check_security_policy(sec)
        if not ok:
            self._audit(
                "pre" if is_tool_instr else "instr",
                trace_id,
                session_norm,
                tool_name if is_tool_instr else "@instruction",
                {
                    "instruction_type": it,
                    "category": cat,
                    "security_type": redact_args(sec or {}),
                },
                "BLOCK",
                reason,
                extra={"audit_id": audit_id},
            )
            return PolicyDecision(False, reason, trace_id, effect="BLOCK")

        # 3) taint sinks
        ta = self.cfg.get("taint", {}) or {}
        if bool(ta.get("enabled", False)):
            # instruction-level outward sinks (RESPOND etc.)
            instr_sinks = set(self._taint_instruction_sinks())
            if bool(ta.get("treat_respond_as_sink", False)):
                instr_sinks.add("RESPOND")

            if it in instr_sinks:
                window_sec = float(ta.get("sink_block_window_seconds", 0) or 0.0)
                if window_sec > 0:
                    with self.state.lock:
                        last_ts = self.state.last_untrusted_ts.get(session_norm)
                        src = self.state.last_untrusted_source.get(session_norm)
                    if last_ts is not None and (_now() - last_ts) <= window_sec:
                        if self._approval_granted(trace_id, it):
                            return PolicyDecision(
                                True,
                                "taint override approved",
                                trace_id,
                                effect="ALLOW",
                            )
                        msg = f"taint: instruction sink '{it}' blocked within {window_sec}s after untrusted source '{src or 'unknown'}'"
                        msg = self._format_approval_hint(
                            trace_id=trace_id, scope=it, base=msg
                        )
                        self._audit(
                            "instr",
                            trace_id,
                            session_norm,
                            "@instruction",
                            {"instruction_type": it, "category": cat},
                            "BLOCK",
                            msg,
                            extra={"audit_id": audit_id},
                        )
                        return PolicyDecision(False, msg, trace_id, effect="BLOCK")

            # tool sinks (only for tool instructions)
            if is_tool_instr:
                ok, reason = self._check_taint_sinks(
                    trace_id, session_norm, tool_name, tool_args
                )
                if not ok:
                    self._audit(
                        "pre",
                        trace_id,
                        session_norm,
                        tool_name,
                        tool_args,
                        "BLOCK",
                        reason,
                        extra={
                            "audit_id": audit_id,
                            "instruction_type": it,
                            "category": cat,
                        },
                    )
                    return PolicyDecision(False, reason, trace_id, effect="BLOCK")

        # 4) EFSM gate (both tool and non-tool instructions)
        if self._efsm_enabled():
            payload: Dict[str, Any] = {
                "instruction_type": it,
                "category": cat,
                "content": content,
                "trace_id": trace_id,
            }
            if is_tool_instr:
                payload["tool"] = tool_name
                payload["args"] = tool_args

            gate = self.on_event(session_id=session_norm, event=it, payload=payload)
            if not gate.allow:
                self._audit(
                    "pre" if is_tool_instr else "instr",
                    trace_id,
                    session_norm,
                    tool_name if is_tool_instr else "@instruction",
                    tool_args
                    if is_tool_instr
                    else {"instruction_type": it, "category": cat},
                    "BLOCK",
                    gate.reason,
                    extra={
                        "audit_id": audit_id,
                        "efsm_effect": gate.effect,
                        "efsm_next_state": gate.next_state,
                        "efsm_transition": gate.matched_transition,
                        "instruction_type": it,
                        "category": cat,
                    },
                )
                return PolicyDecision(
                    False,
                    gate.reason,
                    trace_id,
                    effect=gate.effect,
                    next_state=gate.next_state,
                    matched_transition=gate.matched_transition,
                    meta=gate.meta,
                )

        # 5) tool-argument checks (only for tool instructions)
        if is_tool_instr:
            ok, reason = self._check_schema(tool_name, tool_args)
            if not ok:
                self._audit(
                    "pre",
                    trace_id,
                    session_norm,
                    tool_name,
                    tool_args,
                    "BLOCK",
                    reason,
                    extra={
                        "audit_id": audit_id,
                        "instruction_type": it,
                        "category": cat,
                    },
                )
                return PolicyDecision(False, reason, trace_id, effect="BLOCK")

            ok, reason = self._check_path_and_budget(tool_name, tool_args)
            if not ok:
                self._audit(
                    "pre",
                    trace_id,
                    session_norm,
                    tool_name,
                    tool_args,
                    "BLOCK",
                    reason,
                    extra={
                        "audit_id": audit_id,
                        "instruction_type": it,
                        "category": cat,
                    },
                )
                return PolicyDecision(False, reason, trace_id, effect="BLOCK")

            ok, reason = self._check_rate_limits(session_norm, tool_name)
            if not ok:
                self._audit(
                    "pre",
                    trace_id,
                    session_norm,
                    tool_name,
                    tool_args,
                    "BLOCK",
                    reason,
                    extra={
                        "audit_id": audit_id,
                        "instruction_type": it,
                        "category": cat,
                    },
                )
                return PolicyDecision(False, reason, trace_id, effect="BLOCK")

            agg_key = (
                user_key.strip() if isinstance(user_key, str) else ""
            ) or session_norm
            ok, reason = self._check_user_aggregate(agg_key, tool_name)
            if not ok:
                self._audit(
                    "pre",
                    trace_id,
                    session_norm,
                    tool_name,
                    tool_args,
                    "BLOCK",
                    reason,
                    extra={
                        "audit_id": audit_id,
                        "instruction_type": it,
                        "category": cat,
                    },
                )
                return PolicyDecision(False, reason, trace_id, effect="BLOCK")

            if bool(self.cfg.get("audit", {}).get("log_allow", True)):
                self._audit(
                    "pre",
                    trace_id,
                    session_norm,
                    tool_name,
                    tool_args,
                    "ALLOW",
                    "ok",
                    extra={
                        "audit_id": audit_id,
                        "args_hash": _sha256(_safe_json_dumps(tool_args)),
                        "instruction_type": it,
                        "category": cat,
                    },
                )
            return PolicyDecision(True, "ok", trace_id, effect="ALLOW")

        # non-tool instruction allow
        self._audit(
            "instr",
            trace_id,
            session_norm,
            "@instruction",
            {"instruction_type": it, "category": cat},
            "ALLOW",
            "ok",
            extra={"audit_id": audit_id},
        )
        return PolicyDecision(True, "ok", trace_id, effect="ALLOW")

    # -------------------------
    # EFSM entrypoint
    # -------------------------

    def on_event(
        self, *, session_id: str, event: str, payload: Dict[str, Any]
    ) -> PolicyDecision:
        """
        Main EFSM hook:
          - Dedup events per session (prefer trace_id)
          - Apply transition table
          - Execute actions
          - Enforce REQUIRE_APPROVAL via file approval
        """
        if not self._efsm_enabled():
            return PolicyDecision(True, "efsm disabled", "", effect="ALLOW")

        s = (session_id or "").strip() or "unknown_session"
        ev = (event or "").strip().upper() or "UNKNOWN"
        payload = payload if isinstance(payload, dict) else {}

        with self.state.lock:
            if s not in self.state.efsm_state:
                self.state.efsm_state[s] = self._efsm_initial()
            if s not in self.state.efsm_vars:
                self.state.efsm_vars[s] = {}

        # Dedup: prefer stable trace_id (avoid uuid in instruction dict breaking dedupe)
        trace_id = payload.get("trace_id")
        if isinstance(trace_id, str) and trace_id.strip():
            event_key = _sha256(f"{s}|{ev}|{trace_id.strip()}")  # stable
        else:
            event_key = _sha256(
                _safe_json_dumps({"s": s, "ev": ev, "p": payload})[:4096]
            )

        dedupe_max = self._efsm_event_dedupe_max()
        with self.state.lock:
            if event_key in self.state.event_key_set[s]:
                return PolicyDecision(True, "event deduped", "", effect="ALLOW")
            self.state.event_key_set[s].add(event_key)
            dq = self.state.event_keys[s]
            dq.append(event_key)
            while len(dq) > dedupe_max:
                old = dq.popleft()
                self.state.event_key_set[s].discard(old)

        return self._efsm_step(session_id=s, event=ev, payload=payload)

    def _efsm_step(
        self, *, session_id: str, event: str, payload: Dict[str, Any]
    ) -> PolicyDecision:
        with self.state.lock:
            cur = self.state.efsm_state.get(session_id, self._efsm_initial())
            vars_ = self.state.efsm_vars.get(session_id, {})
            self.state.efsm_vars[session_id] = vars_

        trace_id = payload.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id.strip():
            trace_id = _sha256(
                f"{session_id}|@event|{event}|{_safe_json_dumps(payload)[:4096]}"
            )[:24]

        ctx: Dict[str, Any] = {
            "session_id": session_id,
            "event": event,
            "payload": payload,
            "trace_id": trace_id,
            "state": cur,
            "vars": vars_,
        }

        matched: Optional[Dict[str, Any]] = None

        for tr in self._efsm_transitions():
            fr = tr.get("from", "*")
            ev = tr.get("event", "*")
            to = tr.get("to", "*")

            from_ok = (
                (fr == "*")
                or (isinstance(fr, str) and fr == cur)
                or (isinstance(fr, list) and cur in fr)
            )
            if not from_ok:
                continue

            ev_list = _as_list(ev) if ev != "*" else ["*"]
            ev_ok = ("*" in ev_list) or (
                event in [str(x).strip().upper() for x in ev_list if str(x).strip()]
            )
            if not ev_ok:
                continue

            guard_name = tr.get("guard")
            if isinstance(guard_name, str) and guard_name.strip():
                g = self._guards.get(guard_name.strip())
                if g is None:
                    continue
                try:
                    if not bool(g(ctx)):
                        continue
                except Exception:
                    continue

            matched = tr
            break

        if matched is None:
            return PolicyDecision(
                True, "efsm: no transition", trace_id, effect="ALLOW", next_state=cur
            )

        to_raw = matched.get("to", "*")
        next_state = (
            cur if (to_raw == "*" or to_raw is None) else (str(to_raw).strip() or cur)
        )

        actions = matched.get("actions", [])
        if isinstance(actions, str):
            actions = [actions]
        if isinstance(actions, list):
            for a_name in actions:
                if not isinstance(a_name, str) or not a_name.strip():
                    continue
                act = self._actions.get(a_name.strip())
                if act is None:
                    continue
                try:
                    act(ctx)
                except Exception:
                    pass

        effect = matched.get("effect", "ALLOW")
        effect = str(effect).strip().upper() if isinstance(effect, str) else "ALLOW"
        if effect not in {
            "ALLOW",
            "BLOCK",
            "WARN",
            "LOG_ONLY",
            "REQUIRE_APPROVAL",
            "TRANSFORM",
        }:
            effect = "ALLOW"

        with self.state.lock:
            self.state.efsm_state[session_id] = next_state

        if effect == "REQUIRE_APPROVAL":
            scope = (
                payload.get("tool") if isinstance(payload.get("tool"), str) else event
            ) or event
            scope = str(scope).strip() or event
            if self._approval_granted(trace_id, scope):
                return PolicyDecision(
                    True,
                    "efsm: approved",
                    trace_id,
                    effect="ALLOW",
                    next_state=next_state,
                    matched_transition=matched.get("id"),
                    meta={"approved_scope": scope},
                )

            msg = f"efsm: action requires approval (event={event}, scope={scope})"
            msg = self._format_approval_hint(trace_id=trace_id, scope=scope, base=msg)
            return PolicyDecision(
                False,
                msg,
                trace_id,
                effect="REQUIRE_APPROVAL",
                next_state=next_state,
                matched_transition=matched.get("id"),
                meta={"scope": scope},
            )

        allow = effect in {"ALLOW", "WARN", "LOG_ONLY", "TRANSFORM"}
        reason = f"efsm: {effect.lower()} via {matched.get('id') or 'transition'}"
        if effect == "BLOCK":
            reason = f"efsm: blocked via {matched.get('id') or 'transition'}"
        return PolicyDecision(
            allow=allow,
            reason=reason,
            trace_id=trace_id,
            effect=effect,
            next_state=next_state,
            matched_transition=matched.get("id"),
            meta={"from": cur, "to": next_state, "event": event},
        )

    # -------------------------
    # EFSM guards (built-in)
    # -------------------------

    def _g_has_recent_plan(self, ctx: Dict[str, Any]) -> bool:
        vars_ = ctx.get("vars", {})
        ts = vars_.get("plan_ts")
        if not isinstance(ts, (int, float)):
            return False
        ttl = float(self._efsm_cfg().get("plan_ttl_seconds", 600) or 600.0)
        return (_now() - float(ts)) <= ttl

    def _g_path_in_recent_plan(self, ctx: Dict[str, Any]) -> bool:
        vars_ = ctx.get("vars", {})
        planned = vars_.get("planned_paths")
        if not isinstance(planned, (set, list)):
            return False
        planned_set = set(planned) if isinstance(planned, list) else planned

        payload = ctx.get("payload", {}) or {}
        args = payload.get("args")
        if not isinstance(args, dict):
            return False
        paths = _collect_paths_from_args(args)
        if not paths:
            return False
        return any(p in planned_set for p in paths)

    def _g_tool_in_recent_plan(self, ctx: Dict[str, Any]) -> bool:
        vars_ = ctx.get("vars", {})
        tools = vars_.get("planned_tools")
        if not isinstance(tools, (set, list)):
            return False
        toolset = set(tools) if isinstance(tools, list) else tools
        payload = ctx.get("payload", {}) or {}
        t = payload.get("tool")
        if not isinstance(t, str) or not t.strip():
            return False
        return t.strip() in toolset

    def _g_approved_file_present(self, ctx: Dict[str, Any]) -> bool:
        trace_id = ctx.get("trace_id")
        payload = ctx.get("payload", {}) or {}
        scope = (
            payload.get("tool")
            if isinstance(payload.get("tool"), str)
            else ctx.get("event")
        )
        if not isinstance(trace_id, str) or not trace_id.strip():
            return False
        if not isinstance(scope, str) or not scope.strip():
            scope = "op"
        return self._approval_granted(trace_id, scope)

    def _g_user_mentions_trace_id(self, ctx: Dict[str, Any]) -> bool:
        payload = ctx.get("payload", {}) or {}
        text = payload.get("text")
        trace_id = ctx.get("trace_id")
        if not isinstance(text, str) or not isinstance(trace_id, str):
            return False
        return trace_id[:12] in text

    def _g_tainted_recently(self, ctx: Dict[str, Any]) -> bool:
        s = ctx.get("session_id")
        if not isinstance(s, str):
            return False
        ta = self.cfg.get("taint", {}) or {}
        if not bool(ta.get("enabled", False)):
            return False
        window_sec = float(ta.get("sink_block_window_seconds", 0) or 0.0)
        if window_sec <= 0:
            return False
        with self.state.lock:
            last_ts = self.state.last_untrusted_ts.get(s)
        if last_ts is None:
            return False
        return (_now() - float(last_ts)) <= window_sec

    # -------------------------
    # EFSM actions (built-in)
    # -------------------------

    def _a_cache_plan(self, ctx: Dict[str, Any]) -> None:
        vars_ = ctx.get("vars", {})
        payload = ctx.get("payload", {}) or {}
        content = payload.get("content")
        plan_text = ""
        if isinstance(content, str):
            plan_text = content
        elif isinstance(content, dict):
            plan_text = _safe_json_dumps(content)

        paths = _extract_paths_from_text(plan_text)
        vars_["plan_ts"] = _now()
        vars_["plan_text"] = plan_text[:4000]
        vars_["planned_paths"] = set(paths)

        tool = payload.get("tool")
        tools: set[str] = set(vars_.get("planned_tools") or [])
        if isinstance(tool, str) and tool.strip():
            tools.add(tool.strip())
        vars_["planned_tools"] = tools

    def _a_clear_plan(self, ctx: Dict[str, Any]) -> None:
        vars_ = ctx.get("vars", {})
        for k in ("plan_ts", "plan_text", "planned_paths", "planned_tools"):
            vars_.pop(k, None)

    def _a_set_pending(self, ctx: Dict[str, Any]) -> None:
        vars_ = ctx.get("vars", {})
        vars_["pending_trace_id"] = ctx.get("trace_id")
        vars_["pending_event"] = ctx.get("event")
        payload = ctx.get("payload", {}) or {}
        if isinstance(payload.get("tool"), str):
            vars_["pending_scope"] = payload.get("tool")
        else:
            vars_["pending_scope"] = ctx.get("event")

    def _a_clear_pending(self, ctx: Dict[str, Any]) -> None:
        vars_ = ctx.get("vars", {})
        for k in ("pending_trace_id", "pending_event", "pending_scope"):
            vars_.pop(k, None)

    def _a_mark_last_user(self, ctx: Dict[str, Any]) -> None:
        vars_ = ctx.get("vars", {})
        payload = ctx.get("payload", {}) or {}
        fp = payload.get("fingerprint")
        text = payload.get("text")
        if isinstance(fp, str):
            vars_["last_user_fingerprint"] = fp
        if isinstance(text, str):
            vars_["last_user_text"] = text[:2000]
        vars_["last_user_ts"] = _now()

    # -------------------------
    # Checks
    # -------------------------

    def _check_schema(self, tool: str, args: Dict[str, Any]) -> Tuple[bool, str]:
        schema_map = self.cfg.get("schemas", {}) or {}
        schema = schema_map.get(tool)
        if not schema:
            return True, "no schema"

        if jsonschema is None:
            required = schema.get("required", [])
            if isinstance(required, list) and required:
                missing = [k for k in required if k not in args]
                if missing:
                    return False, f"args missing required fields: {missing}"
            return True, "schema ok (minimal)"

        try:
            jsonschema.validate(instance=args, schema=schema)
            return True, "schema ok"
        except Exception as e:
            return False, f"args schema invalid: {e!r}"

    def _check_static_allowdeny(
        self,
        tool: str,
        instruction_type: Optional[str],
        category: Optional[str],
    ) -> Tuple[bool, str]:
        deny = self.cfg.get("deny", {}) or {}
        allow = self.cfg.get("allow", {}) or {}

        it = (
            instruction_type.strip().upper()
            if isinstance(instruction_type, str)
            else None
        )
        cat = category.strip() if isinstance(category, str) else None

        # Tool allow/deny ONLY applies to real tools (avoid allow.tools blocking all instructions)
        if tool and tool != "@instruction":
            deny_tools = set(deny.get("tools", []) or [])
            allow_tools = set(allow.get("tools", []) or [])
            if tool in deny_tools:
                return False, f"tool denied: {tool}"
            if allow_tools and tool not in allow_tools:
                return False, f"tool not in allowlist: {tool}"

        allow_types = set((allow.get("instruction_types", []) or []))
        if allow_types and it and it not in allow_types:
            return False, f"instruction_type not in allowlist: {it}"
        allow_cats = set((allow.get("categories", []) or []))
        if allow_cats and cat and cat not in allow_cats:
            return False, f"category not in allowlist: {cat}"

        deny_types = set(deny.get("instruction_types", []) or [])
        if it and it in deny_types:
            return False, f"instruction_type denied: {it}"

        deny_cats = set(deny.get("categories", []) or [])
        if cat and cat in deny_cats:
            return False, f"category denied: {cat}"

        return True, "static allow/deny ok"

    def _check_security_policy(
        self, security_type: Optional[Dict[str, Any]]
    ) -> Tuple[bool, str]:
        sec_cfg = self.cfg.get("security", {}) or {}
        if not isinstance(sec_cfg, dict) or not sec_cfg:
            return True, "no security policy"

        if not isinstance(security_type, dict):
            if bool(sec_cfg.get("require_security_type", False)):
                return False, "missing security_type for instruction"
            return True, "no security_type"

        min_conf = sec_cfg.get("min_confidence")
        try:
            if min_conf is not None:
                mc = float(min_conf)
                c = security_type.get("confidence")
                if isinstance(c, (int, float)) and float(c) < mc:
                    return False, f"security.confidence too low: {c}<{mc}"
        except Exception:
            pass

        deny_auth = sec_cfg.get("deny_authoritys")
        if isinstance(deny_auth, list) and deny_auth:
            al = security_type.get("authority")
            if isinstance(al, str) and al in set(str(x) for x in deny_auth):
                return False, f"security.authority denied: {al}"

        allow_auth = sec_cfg.get("allow_authoritys")
        if isinstance(allow_auth, list) and allow_auth:
            al = security_type.get("authority")
            if isinstance(al, str) and al not in set(str(x) for x in allow_auth):
                return False, f"security.authority not allowed: {al}"

        return True, "security ok"

    def _check_taint_sinks(
        self, trace_id: str, session_id: str, tool: str, args: Dict[str, Any]
    ) -> Tuple[bool, str]:
        ta = self.cfg.get("taint", {}) or {}
        if not bool(ta.get("enabled", False)):
            return True, "taint disabled"

        sinks = set(self._taint_sinks())
        if tool not in sinks:
            return True, "not a sink"

        window_sec = float(ta.get("sink_block_window_seconds", 0) or 0.0)
        with self.state.lock:
            last_ts = self.state.last_untrusted_ts.get(session_id)
            src = self.state.last_untrusted_source.get(session_id)
            snippets = list(self.state.taint_snippets.get(session_id, []))

        if window_sec > 0 and last_ts is not None:
            if (_now() - last_ts) <= window_sec:
                if self._approval_granted(trace_id, tool):
                    return True, "taint override approved"
                msg = f"taint: sink '{tool}' blocked within {window_sec}s after untrusted source '{src or 'unknown'}'"
                return False, self._format_approval_hint(
                    trace_id=trace_id, scope=tool, base=msg
                )

        def _collect_strings(x: Any, out: List[str]) -> None:
            if isinstance(x, str):
                if x.strip():
                    out.append(x)
                return
            if isinstance(x, dict):
                for v in x.values():
                    _collect_strings(v, out)
                return
            if isinstance(x, list):
                for v in x:
                    _collect_strings(v, out)
                return

        strings: List[str] = []
        _collect_strings(args, strings)
        if not strings or not snippets:
            return True, "taint ok"

        for st in strings:
            hay_raw = st if len(st) <= 4000 else st[:4000]
            hay = re.sub(r"\s+", " ", hay_raw).strip()
            hay_l = hay.lower()
            for sn in snippets:
                if not sn:
                    continue
                sn_norm = re.sub(r"\s+", " ", sn).strip()
                sn_l = sn_norm.lower()
                if sn_norm in hay or hay in sn_norm or sn_l in hay_l or hay_l in sn_l:
                    if self._approval_granted(trace_id, tool):
                        return True, "taint override approved"
                    msg = "taint: sink args contain untrusted snippet from prior source"
                    return False, self._format_approval_hint(
                        trace_id=trace_id, scope=tool, base=msg
                    )

        return True, "taint ok"

    def _check_path_and_budget(
        self, tool: str, args: Dict[str, Any]
    ) -> Tuple[bool, str]:
        path_cfg = self.cfg.get("paths", {}) or {}
        deny_prefixes = [
            normalize_path(p) for p in (path_cfg.get("deny_prefixes", []) or [])
        ]
        allow_prefixes = [
            normalize_path(p) for p in (path_cfg.get("allow_prefixes", []) or [])
        ]

        paths = _collect_paths_from_args(args)

        for p in paths:
            for dp in deny_prefixes:
                if dp and p.startswith(dp):
                    return False, f"path denied by prefix: {p}"
            if allow_prefixes:
                if not any(ap and p.startswith(ap) for ap in allow_prefixes):
                    return False, f"path not in allow_prefixes: {p}"

        in_budget = self.cfg.get("input_budget", {}) or {}
        max_str = int(in_budget.get("max_str_len", 0) or 0)
        if max_str > 0:
            for k, v in args.items():
                if isinstance(v, str) and len(v) > max_str:
                    return False, f"arg too long: {k} len={len(v)}>{max_str}"

        return True, "path/budget ok"

    def _check_rate_limits(self, session_id: str, tool: str) -> Tuple[bool, str]:
        rl = self.cfg.get("rate_limit", {}) or {}
        max_repeat = int(rl.get("max_consecutive_same_tool", 0) or 0)
        window_sec = float(rl.get("window_seconds", 0) or 0.0)
        max_in_window = int(rl.get("max_calls_per_window", 0) or 0)

        now = _now()
        with self.state.lock:
            last = self.state.last_tool.get(session_id)
            if last == tool:
                self.state.repeat_count[session_id] += 1
            else:
                self.state.last_tool[session_id] = tool
                self.state.repeat_count[session_id] = 1

            if max_repeat > 0 and self.state.repeat_count[session_id] > max_repeat:
                return (
                    False,
                    f"too many consecutive repeated tool calls: {self.state.repeat_count[session_id]}>{max_repeat}",
                )

            if window_sec > 0 and max_in_window > 0:
                dq = self.state.calls_window[(session_id, tool)]
                dq.append(now)
                cutoff = now - window_sec
                while dq and dq[0] < cutoff:
                    dq.popleft()
                if len(dq) > max_in_window:
                    return (
                        False,
                        f"rate limit exceeded: {len(dq)}>{max_in_window} in {window_sec}s for tool={tool}",
                    )

        return True, "rate ok"

    def _check_user_aggregate(self, user_key: str, tool: str) -> Tuple[bool, str]:
        agg = self.cfg.get("user_aggregate", {}) or {}
        tools = set(agg.get("tools", []) or [])
        if tools and tool not in tools:
            return True, "user agg not applied"

        window_sec = float(agg.get("window_seconds", 0) or 0.0)
        max_events = int(agg.get("max_events", 0) or 0)
        if window_sec <= 0 or max_events <= 0:
            return True, "user agg disabled"

        now = _now()
        key = (f"USER::{user_key}", tool)
        with self.state.lock:
            dq = self.state.calls_window[key]
            dq.append(now)
            cutoff = now - window_sec
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) > max_events:
                return (
                    False,
                    f"user aggregate exceeded: {len(dq)}>{max_events} in {window_sec}s",
                )

        return True, "user agg ok"

    # -------------------------
    # Audit
    # -------------------------

    def _audit(
        self,
        phase: str,
        trace_id: str,
        session_id: str,
        tool: str,
        args: Any,
        decision: str,
        reason: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.audit_path:
            return
        rec: Dict[str, Any] = {
            "phase": phase,
            "ts": _now(),
            "trace_id": trace_id,
            "session_id": session_id,
            "tool": tool,
            "decision": decision,
            "reason": reason,
            "args": redact_args(args),
        }
        if extra:
            rec.update(extra)

        line = _safe_json_dumps(rec)
        try:
            os.makedirs(os.path.dirname(self.audit_path) or ".", exist_ok=True)
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
