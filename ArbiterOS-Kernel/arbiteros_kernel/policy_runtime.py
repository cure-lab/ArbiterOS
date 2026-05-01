# arbiteros_kernel/policy_runtime.py
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import jsonschema  # type: ignore
except Exception:
    jsonschema = None

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

    for m in _QUOTED_RE.finditer(t):
        _push(m.group(1))
    for m in _ABS_PATH_RE.finditer(t):
        _push(m.group(0))
    for m in _REL_PATH_RE.finditer(t):
        _push(m.group(0))

    return out[:max_items]


def _lower_contains_any(hay: str, needles: List[str]) -> bool:
    h = (hay or "").lower()
    for n in needles:
        nn = (n or "").strip().lower()
        if nn and nn in h:
            return True
    return False


def _extract_snippet_variants(line: str) -> List[str]:
    t = re.sub(r"\s+", " ", (line or "")).strip()
    if not t:
        return []

    variants: List[str] = [t]
    tq = t.strip("`\"' ")
    if tq and tq != t:
        variants.append(tq)

    m = re.match(r"^(?P<tool>[A-Za-z_][\w-]*)\s*:\s*(?P<rhs>.+)$", t)
    if m:
        rhs = m.group("rhs").strip()
        if rhs:
            variants.append(rhs)
            variants.append(rhs.strip("`\"' "))

    m2 = re.search(r"\bexec\s*:\s*(?P<cmd>[^\n\r]+)$", t, flags=re.IGNORECASE)
    if m2:
        cmd = m2.group("cmd").strip()
        if cmd:
            variants.append(cmd)
            variants.append(cmd.strip("`\"' "))

    variants.append(re.sub(r"\s+", " ", t).strip().lower())

    seen: set[str] = set()
    out: List[str] = []
    for v in variants:
        vv = (v or "").strip()
        if not vv or vv in seen:
            continue
        seen.add(vv)
        out.append(vv)
    return out


@dataclass
class EfsmStepResult:
    allow: bool
    effect: str
    reason: str
    next_state: str
    matched_transition: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


@dataclass
class TaintState:
    # deterministic state derived from instructions
    last_untrusted_ts: Optional[float]
    last_untrusted_source: Optional[str]
    snippets: List[str]


@dataclass
class PlanState:
    plan_ts: Optional[float]
    plan_text: str
    planned_paths: set[str]
    planned_tools: set[str]


class KernelPolicyRuntime:
    """
    只做“运行时能力”：
    - cfg 读取
    - normalize/canonicalize/redact
    - schema/path/budget/allowdeny/security 检查函数
    - approval token
    - audit
    - 从 instructions 重建 taint/plan/efsm 状态（确定性）
    """

    def __init__(self, config: Dict[str, Any], audit_path: str = "") -> None:
        self.cfg = config or {}
        self.audit_path = audit_path or ""

    # -------------------------
    # Config loading
    # -------------------------

    @staticmethod
    def from_env() -> "KernelPolicyRuntime":
        # 1) ARBITEROS_POLICY_CONFIG_JSON: inline JSON
        inline = os.getenv("ARBITEROS_POLICY_CONFIG_JSON", "").strip()
        if inline:
            try:
                cfg = json.loads(inline)
            except Exception:
                cfg = {}
            audit_path = _resolve_policy_audit_path(cfg if isinstance(cfg, dict) else {})
            return KernelPolicyRuntime(
                cfg if isinstance(cfg, dict) else {}, audit_path=audit_path
            )

        # 2) ARBITEROS_POLICY_CONFIG: path to JSON
        path = os.getenv("ARBITEROS_POLICY_CONFIG", "").strip()
        if not path:
            # sensible default
            path = os.path.join(os.path.dirname(__file__), "policy.json")
        path = _expand_home(path)
        cfg: Dict[str, Any] = {}
        try:
            raw = open(path, "r", encoding="utf-8").read()
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                cfg = parsed
        except Exception:
            cfg = {}
        audit_path = _resolve_policy_audit_path(cfg)
        return KernelPolicyRuntime(cfg, audit_path=audit_path)

    # -------------------------
    # Common helpers
    # -------------------------

    def tool_to_instruction_type(self, tool_name: str) -> str:
        t = (tool_name or "").strip().lower()
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

    def instruction_type_to_category(self, instruction_type: str) -> Optional[str]:
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

    def compute_op_id(self, trace_id: str, tool: str, args: Dict[str, Any]) -> str:
        tool_norm = (tool or "").strip() or "unknown_tool"
        canon_args = canonicalize_args(args if isinstance(args, dict) else {})
        canon_json = _safe_json_dumps(canon_args)
        return _sha256(f"{trace_id}|{tool_norm}|{canon_json}")[:24]

    # -------------------------
    # Response tool_calls helpers
    # -------------------------

    def extract_tool_calls(self, response: Dict[str, Any]) -> List[Dict[str, Any]]:
        tcs = response.get("tool_calls")
        if isinstance(tcs, list):
            return [tc for tc in tcs if isinstance(tc, dict)]
        return []

    def parse_tool_call(
        self, tc: Dict[str, Any]
    ) -> Tuple[str, Optional[str], Dict[str, Any], bool]:
        """
        return: (tool_name, tool_call_id, args_dict, args_was_json_string)
        """
        tool_call_id = tc.get("id") if isinstance(tc.get("id"), str) else None
        fn = tc.get("function")
        if not isinstance(fn, dict):
            return ("unknown_tool", tool_call_id, {}, False)
        tool_name = fn.get("name")
        tool_name = (
            tool_name.strip()
            if isinstance(tool_name, str) and tool_name.strip()
            else "unknown_tool"
        )

        raw_args = fn.get("arguments")
        if isinstance(raw_args, str):
            parsed = _safe_json_loads(raw_args)
            args = parsed if isinstance(parsed, dict) else {}
            tool_name, args = self._canonicalize_tool_call_for_policy(tool_name, args)
            return (tool_name, tool_call_id, args, True)
        if isinstance(raw_args, dict):
            args = dict(raw_args)
            tool_name, args = self._canonicalize_tool_call_for_policy(tool_name, args)
            return (tool_name, tool_call_id, args, False)
        return (tool_name, tool_call_id, {}, False)

    def _canonicalize_tool_call_for_policy(
        self, tool_name: str, args: Dict[str, Any]
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Pre-policy tool normalization.

        Keep this runtime-level and agent-gated so we don't need policy-side
        code changes for Hermes split browser tools.
        """
        try:
            from arbiteros_kernel.instruction_parsing.tool_agent_config import (
                get_tool_agent,
            )
            agent = get_tool_agent()
        except Exception:
            agent = "openclaw"

        if agent != "hermes":
            return tool_name, args

        action = _HERMES_BROWSER_TOOL_TO_ACTION.get(tool_name)
        if action is None:
            return tool_name, args

        out_args = dict(args)
        out_args.setdefault("action", action)
        out_args.setdefault("_arbiteros_raw_tool_name", tool_name)
        return "browser", out_args

    def write_back_tool_args(
        self, tc: Dict[str, Any], args: Dict[str, Any], was_json_str: bool
    ) -> Dict[str, Any]:
        out = dict(tc)
        fn = out.get("function")
        if not isinstance(fn, dict):
            return out
        fn2 = dict(fn)
        if was_json_str:
            fn2["arguments"] = json.dumps(args, ensure_ascii=False)
        else:
            fn2["arguments"] = args
        out["function"] = fn2
        return out

    # -------------------------
    # Audit + approval
    # -------------------------

    def _audit_enabled(self) -> bool:
        return bool(self.audit_path)

    def audit(
        self,
        *,
        phase: str,
        trace_id: str,
        tool: str,
        decision: str,
        reason: str,
        args: Any,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._audit_enabled():
            return
        rec: Dict[str, Any] = {
            "phase": phase,
            "ts": _now(),
            "trace_id": trace_id,
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

    def _approval_cfg(self) -> Dict[str, Any]:
        ta = self.cfg.get("taint", {}) or {}
        ap = ta.get("approval", {}) or {}
        return ap if isinstance(ap, dict) else {}

    def _approval_mode(self) -> str:
        mode = self._approval_cfg().get("mode")
        return str(mode).strip().lower() if isinstance(mode, str) else "none"

    def _approval_dir(self) -> str:
        d = self._approval_cfg().get("dir")
        if isinstance(d, str) and d.strip():
            return _expand_home(d.strip())
        return _expand_home("~/.arbiteros/approvals")

    def _approval_ttl(self) -> int:
        ttl = self._approval_cfg().get("ttl_seconds")
        try:
            v = int(ttl)
            return v if v > 0 else 120
        except Exception:
            return 120

    def _approval_delete_on_use(self) -> bool:
        v = self._approval_cfg().get("delete_on_use")
        return bool(v) if isinstance(v, bool) else True

    def _approval_show_instructions(self) -> bool:
        v = self._approval_cfg().get("show_instructions")
        return bool(v) if isinstance(v, bool) else True

    def approval_granted(self, op_id: str, scope: str) -> bool:
        if self._approval_mode() != "file":
            return False
        ttl = self._approval_ttl()
        now = _now()
        d = self._approval_dir()
        scope = (scope or "op").strip() or "op"
        candidates = [
            os.path.join(d, f"{op_id}.{scope}.allow"),
            os.path.join(d, f"{op_id}.allow"),
        ]
        for fp in candidates:
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

    def approval_hint(self, *, op_id: str, scope: str, base: str) -> str:
        if not self._approval_show_instructions():
            return base
        if self._approval_mode() == "file":
            d = self._approval_dir()
            ttl = self._approval_ttl()
            fp = os.path.join(d, f"{op_id}.{scope}.allow")
            return (
                f"{base}. If you intended this, approve once locally and retry: "
                f"`mkdir -p {d} && touch {fp}` (valid ~{ttl}s)."
            )
        return base

    # -------------------------
    # Low-level checks (stateless)
    # -------------------------
    # --- add inside KernelPolicyRuntime class ---

    def _paths_cfg(self) -> dict[str, Any]:
        pc = self.cfg.get("paths", {}) or {}
        return pc if isinstance(pc, dict) else {}

    def _workspace_base(self) -> str:
        """
        Resolve relative paths against a stable base (workspace).
        Priority:
        1) cfg.paths.relative_base (explicit)
        2) first absolute allow_prefix containing '/.openclaw/workspace'
        3) first absolute allow_prefix
        4) empty (no rewrite)
        """
        pc = self._paths_cfg()
        rb = pc.get("relative_base")
        if isinstance(rb, str) and rb.strip():
            return normalize_path(_expand_home(rb.strip()))

        allow_prefixes = [
            normalize_path(p) for p in (pc.get("allow_prefixes", []) or [])
        ]
        # prefer workspace-like prefix
        for ap in allow_prefixes:
            if ap.startswith("/") and "/.openclaw/workspace" in ap:
                return ap
        # fallback: first absolute prefix
        for ap in allow_prefixes:
            if ap.startswith("/"):
                return ap
        return ""

    def _is_absish(self, p: str) -> bool:
        if not p:
            return False
        pp = p.strip()
        return pp.startswith("/") or pp.startswith("~")

    def resolve_path_for_policy(self, p: str) -> str:
        """
        Normalize + if relative, resolve into workspace_base (if configured/inferred).
        """
        pp = normalize_path(p)
        if not pp:
            return pp
        if self._is_absish(pp):
            return pp

        base = self._workspace_base()
        if not base:
            return pp
        return normalize_path(os.path.join(base, pp))

    def canonicalize_tool_args(self, args: Any) -> Any:
        """
        - ensure path/file_path compatibility
        - normalize path-like fields
        - resolve relative paths into workspace (so allow_prefixes matches and tools can open files)
        """
        if isinstance(args, dict):
            # make a shallow copy we can enrich
            a = dict(args)

            # bridge file_path -> path (OpenClaw tool schema usually requires 'path')
            fp = a.get("file_path")
            if "path" not in a and isinstance(fp, str) and fp.strip():
                a["path"] = fp

            # optionally keep symmetry (some tools accept file_path)
            p = a.get("path")
            if "file_path" not in a and isinstance(p, str) and p.strip():
                a["file_path"] = p

            out: Dict[str, Any] = {}
            for k in sorted(a.keys(), key=lambda x: str(x)):
                v = a[k]
                kl = str(k).lower()
                if isinstance(v, str) and kl in (
                    "path",
                    "file_path",
                    "file",
                    "filename",
                    "src",
                    "dst",
                    "directory",
                    "dir",
                ):
                    out[k] = self.resolve_path_for_policy(v)
                else:
                    out[k] = self.canonicalize_tool_args(v)
            return out

        if isinstance(args, list):
            return [self.canonicalize_tool_args(x) for x in args]

        return args

    def check_allow_deny(
        self, *, tool: str, instruction_type: Optional[str], category: Optional[str]
    ) -> Tuple[bool, str]:
        deny = self.cfg.get("deny", {}) or {}
        allow = self.cfg.get("allow", {}) or {}
        it = (
            instruction_type.strip().upper()
            if isinstance(instruction_type, str)
            else None
        )
        cat = category.strip() if isinstance(category, str) else None

        # tool allow/deny only for real tools
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

        return True, "allow/deny ok"

    def check_security(
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

    def check_schema(self, *, tool: str, args: Dict[str, Any]) -> Tuple[bool, str]:
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

    def check_path_and_budget(
        self, *, tool: str, args: Dict[str, Any]
    ) -> Tuple[bool, str]:
        path_cfg = self.cfg.get("paths", {}) or {}

        def _to_fs(p: str) -> str:
            """Normalize path to forward slashes for consistent matching (Windows compat)."""
            return (p or "").replace("\\", "/")

        def _norm_prefixes(xs: Any) -> List[str]:
            out: List[str] = []
            if not isinstance(xs, list):
                return out
            for x in xs:
                if not isinstance(x, str) or not x.strip():
                    continue
                out.append(_to_fs(normalize_path(_expand_home(x.strip()))))
            seen = set()
            dedup: List[str] = []
            for p in out:
                if p and p not in seen:
                    seen.add(p)
                    dedup.append(p)
            return dedup

        def _split_allow(raw: Any) -> Tuple[List[str], List[str]]:
            """Split allow_prefixes into literal prefixes and regex patterns (re:...)."""
            prefixes: List[str] = []
            regex_patterns: List[str] = []
            if not isinstance(raw, list):
                return prefixes, regex_patterns
            for x in raw:
                if not isinstance(x, str) or not x.strip():
                    continue
                s = x.strip()
                if s.startswith("re:"):
                    pat = s[3:].strip()
                    if pat and pat not in regex_patterns:
                        regex_patterns.append(pat)
                else:
                    p = _to_fs(normalize_path(_expand_home(s)))
                    if p and p not in prefixes:
                        prefixes.append(p)
            return prefixes, regex_patterns

        deny_prefixes = _norm_prefixes(path_cfg.get("deny_prefixes", []))
        allow_prefixes, allow_regex = _split_allow(path_cfg.get("allow_prefixes", []))
        allow_has_any = bool(allow_prefixes or allow_regex)

        # Compile regex patterns once
        allow_re_compiled: List[re.Pattern[str]] = []
        for pat in allow_regex:
            try:
                allow_re_compiled.append(re.compile(pat))
            except re.error:
                pass  # skip invalid patterns

        def _prefix_match(p: str, pref: str) -> bool:
            if not pref:
                return False
            if p == pref:
                return True
            return p.startswith(pref.rstrip("/") + "/")

        def _longest_prefix_hit(p: str, prefixes: List[str]) -> Optional[Tuple[str, int]]:
            hits = [(pref, len(pref)) for pref in prefixes if _prefix_match(p, pref)]
            return max(hits, key=lambda x: x[1]) if hits else None

        def _longest_regex_hit(p: str, patterns: List[re.Pattern[str]]) -> Optional[Tuple[str, int]]:
            best: Optional[Tuple[str, int]] = None
            for rx in patterns:
                m = rx.match(p)
                if m:
                    span_len = m.end()
                    if best is None or span_len > best[1]:
                        best = (rx.pattern, span_len)
            return best

        paths = _collect_paths_from_args(args)

        for p in paths:
            p_fs = _to_fs(p)
            prefix_hit = _longest_prefix_hit(p_fs, allow_prefixes) if allow_prefixes else None
            regex_hit = _longest_regex_hit(p_fs, allow_re_compiled) if allow_re_compiled else None
            allow_hit: Optional[Tuple[str, int]] = None
            if prefix_hit and regex_hit:
                allow_hit = prefix_hit if prefix_hit[1] >= regex_hit[1] else regex_hit
            elif prefix_hit:
                allow_hit = prefix_hit
            elif regex_hit:
                allow_hit = regex_hit

            deny_hit = _longest_prefix_hit(p_fs, deny_prefixes) if deny_prefixes else None

            # 1) allowlist non-empty: path must match at least one allow
            if allow_has_any and not allow_hit:
                return False, f"path not in allow_prefixes: {p}"

            # 2) Both deny and allow match: more specific wins (deny wins on tie)
            if deny_hit:
                if (not allow_hit) or (deny_hit[1] >= allow_hit[1]):
                    return False, f"path denied by prefix: {p}"

        in_budget = self.cfg.get("input_budget", {}) or {}
        max_str = int(in_budget.get("max_str_len", 0) or 0)
        if max_str > 0:
            for k, v in args.items():
                if isinstance(v, str) and len(v) > max_str:
                    return False, f"arg too long: {k} len={len(v)}>{max_str}"

        return True, "path/budget ok"

    # -------------------------
    # Deterministic taint derived from instructions
    # -------------------------

    def build_taint_state(self, instructions: List[Dict[str, Any]]) -> TaintState:
        ta = self.cfg.get("taint", {}) or {}
        if not bool(ta.get("enabled", False)):
            return TaintState(
                last_untrusted_ts=None, last_untrusted_source=None, snippets=[]
            )

        sources = set((ta.get("untrusted_sources", {}) or {}).get("tools", []) or [])
        sources = {str(x).strip() for x in sources if str(x).strip()}
        patterns = (ta.get("untrusted_sources", {}) or {}).get("patterns", []) or []
        patterns = [str(x) for x in patterns if isinstance(x, str) and x.strip()]

        min_len = int(ta.get("min_snippet_len", 6) or 6)
        max_len = int(ta.get("max_snippet_len", 512) or 512)
        max_add = int(ta.get("max_snippets_per_event", 50) or 50)
        snippet_cache_max = int(ta.get("snippet_cache_max", 200) or 200)

        snippets: List[str] = []
        last_ts: Optional[float] = None
        last_src: Optional[str] = None

        for ins in instructions:
            it = (ins.get("instruction_type") or "").strip().upper()
            content = ins.get("content")
            if it not in {"READ", "WRITE", "EXEC"} or not isinstance(content, dict):
                continue

            tool = content.get("tool_name")
            tool = tool.strip() if isinstance(tool, str) else ""
            if not tool or tool not in sources:
                continue

            # only tool_result instructions have "result" in content (as per your builder)
            result = content.get("result")
            output_text = ""
            if isinstance(result, dict):
                raw = result.get("raw")
                if isinstance(raw, str):
                    output_text = raw
                elif isinstance(result.get("content"), str):
                    output_text = result.get("content")
                else:
                    output_text = _safe_json_dumps(result)
            elif isinstance(result, str):
                output_text = result

            if not output_text.strip():
                continue

            if patterns and not _lower_contains_any(output_text, patterns):
                continue

            last_src = tool
            # optional ts field (if you later add ts into instruction)
            ts = ins.get("ts")
            if isinstance(ts, (int, float)):
                last_ts = float(ts)

            added = 0
            for line in output_text.splitlines():
                for v in _extract_snippet_variants(line):
                    if len(v) < min_len or len(v) > max_len:
                        continue
                    snippets.append(v)
                    added += 1
                    if added >= max_add:
                        break
                if added >= max_add:
                    break

            if len(snippets) > snippet_cache_max:
                snippets = snippets[-snippet_cache_max:]

        return TaintState(
            last_untrusted_ts=last_ts, last_untrusted_source=last_src, snippets=snippets
        )

    def check_taint_sink_for_tool(
        self, *, tool: str, args: Dict[str, Any], taint: TaintState, op_id: str
    ) -> Tuple[bool, str]:
        ta = self.cfg.get("taint", {}) or {}
        if not bool(ta.get("enabled", False)):
            return True, "taint disabled"

        sinks = set((ta.get("high_risk_tools", []) or []))
        sinks = {str(x).strip() for x in sinks if str(x).strip()}
        if tool not in sinks:
            return True, "not a sink"

        # Mode A: time-window block (only works if you have ts in instructions; your sample uses 0 so it’s disabled)
        window_sec = float(ta.get("sink_block_window_seconds", 0) or 0.0)
        if window_sec > 0 and taint.last_untrusted_ts is not None:
            if (_now() - float(taint.last_untrusted_ts)) <= window_sec:
                if self.approval_granted(op_id, tool):
                    return True, "taint override approved"
                msg = f"taint: sink '{tool}' blocked within {window_sec}s after untrusted source '{taint.last_untrusted_source or 'unknown'}'"
                return False, self.approval_hint(op_id=op_id, scope=tool, base=msg)

        # Mode B: args contains snippet
        snippets = taint.snippets
        if not snippets:
            return True, "taint ok"

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
        if not strings:
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
                if sn_norm in hay or sn_l in hay_l:
                    if self.approval_granted(op_id, tool):
                        return True, "taint override approved"
                    msg = "taint: sink args contain untrusted snippet from prior source"
                    return False, self.approval_hint(op_id=op_id, scope=tool, base=msg)

        return True, "taint ok"

    # -------------------------
    # Deterministic plan state (from latest PLAN instruction)
    # -------------------------

    def build_plan_state(self, instructions: List[Dict[str, Any]]) -> PlanState:
        ttl = float(
            (self.cfg.get("efsm", {}) or {}).get("plan_ttl_seconds", 600) or 600.0
        )

        plan_text = ""
        plan_ts: Optional[float] = None
        planned_paths: set[str] = set()
        planned_tools: set[str] = set()

        # find latest PLAN
        for ins in reversed(instructions):
            it = (ins.get("instruction_type") or "").strip().upper()
            if it != "PLAN":
                continue
            content = ins.get("content")
            if isinstance(content, str):
                plan_text = content
            else:
                plan_text = _safe_json_dumps(content)
            ts = ins.get("ts")
            if isinstance(ts, (int, float)):
                plan_ts = float(ts)
            # even if no ts, still cache paths
            planned_paths = set(_extract_paths_from_text(plan_text))
            break

        # optional ttl check if we have plan_ts
        if plan_ts is not None and ttl > 0:
            if (_now() - plan_ts) > ttl:
                plan_text = ""
                plan_ts = None
                planned_paths = set()
                planned_tools = set()

        return PlanState(
            plan_ts=plan_ts,
            plan_text=plan_text[:4000],
            planned_paths=planned_paths,
            planned_tools=planned_tools,
        )

    # -------------------------
    # EFSM interpreter (minimal but generic enough for your transition config)
    # -------------------------

    def efsm_enabled(self) -> bool:
        ef = self.cfg.get("efsm", {}) or {}
        return bool(ef.get("enabled", False))

    def efsm_initial(self) -> str:
        ef = self.cfg.get("efsm", {}) or {}
        v = ef.get("initial", "IDLE")
        return str(v).strip() if isinstance(v, str) and v.strip() else "IDLE"

    def efsm_transitions(self) -> List[Dict[str, Any]]:
        ef = self.cfg.get("efsm", {}) or {}
        trs = ef.get("transitions")
        if isinstance(trs, list) and trs:
            cleaned = [dict(t) for t in trs if isinstance(t, dict)]
            cleaned.sort(key=lambda x: int(x.get("priority", 0) or 0), reverse=True)
            return cleaned

        # fallback safe default
        return [
            {
                "id": "idle_plan",
                "from": "IDLE",
                "event": "PLAN",
                "to": "PLANNED",
                "actions": ["cache_plan"],
                "effect": "ALLOW",
                "priority": 100,
            },
            {
                "id": "planned_exec_ok_path",
                "from": "PLANNED",
                "event": "EXEC",
                "to": "EXECUTING",
                "guard": "path_in_recent_plan",
                "effect": "ALLOW",
                "priority": 70,
            },
            {
                "id": "planned_exec_need_approval",
                "from": "PLANNED",
                "event": "EXEC",
                "to": "WAIT_APPROVAL",
                "actions": ["set_pending"],
                "effect": "REQUIRE_APPROVAL",
                "priority": 60,
            },
            {
                "id": "idle_exec_need_approval",
                "from": "IDLE",
                "event": "EXEC",
                "to": "WAIT_APPROVAL",
                "actions": ["set_pending"],
                "effect": "REQUIRE_APPROVAL",
                "priority": 50,
            },
        ]

    def _efsm_guard(
        self, name: str, *, plan: PlanState, payload: Dict[str, Any]
    ) -> bool:
        name = (name or "").strip()
        if not name:
            return True
        if name == "always":
            return True
        if name == "path_in_recent_plan":
            args = payload.get("args")
            if not isinstance(args, dict):
                return False
            paths = _collect_paths_from_args(args)
            if not paths:
                return False
            return any(p in plan.planned_paths for p in paths)
        if name == "tool_in_recent_plan":
            tool = payload.get("tool")
            if not isinstance(tool, str) or not tool.strip():
                return False
            return tool.strip() in plan.planned_tools
        if name == "has_recent_plan":
            return bool(plan.plan_text)
        return False  # unknown guard => fail-closed

    def _efsm_apply_actions(
        self,
        actions: Any,
        *,
        vars_: Dict[str, Any],
        plan: PlanState,
        payload: Dict[str, Any],
    ) -> None:
        if isinstance(actions, str):
            actions = [actions]
        if not isinstance(actions, list):
            return
        for a in actions:
            if not isinstance(a, str) or not a.strip():
                continue
            if a == "cache_plan":
                # plan already computed deterministically; store snapshot
                vars_["plan_text"] = plan.plan_text
                vars_["planned_paths"] = list(plan.planned_paths)
                vars_["planned_tools"] = list(plan.planned_tools)
                vars_["plan_ts"] = plan.plan_ts
            elif a == "set_pending":
                vars_["pending"] = {
                    "event": payload.get("event"),
                    "tool": payload.get("tool"),
                }
            elif a == "clear_pending":
                vars_.pop("pending", None)

    def efsm_replay_history(
        self, instructions: List[Dict[str, Any]]
    ) -> Tuple[str, Dict[str, Any], PlanState]:
        """
        用历史指令“确定性回放”得到 (state, vars, plan_state)
        """
        state = self.efsm_initial()
        vars_: Dict[str, Any] = {}
        plan = self.build_plan_state(instructions)

        if not self.efsm_enabled():
            return state, vars_, plan

        trs = self.efsm_transitions()
        for ins in instructions:
            event = (ins.get("instruction_type") or "").strip().upper()
            if not event:
                continue

            payload: Dict[str, Any] = {"event": event}
            content = ins.get("content")

            if event in {"READ", "WRITE", "EXEC"} and isinstance(content, dict):
                tool = content.get("tool_name")
                args = content.get("arguments")

                if isinstance(tool, str) and tool.strip():
                    tool_norm = tool.strip()
                    payload["tool"] = tool_norm

                    # IMPORTANT: normalize event from tool name, because upstream may store tool events as EXEC
                    event = self.tool_to_instruction_type(tool_norm)
                    payload["event"] = event

                if isinstance(args, dict):
                    payload["args"] = canonicalize_args(args)

            step = self.efsm_step(
                current_state=state,
                vars_=vars_,
                plan=plan,
                event=event,
                payload=payload,
            )
            state = step.next_state
            # vars_ mutated inside step (actions)

            # if this event is PLAN, rebuild plan deterministically from full history so far
            if event == "PLAN":
                # recompute from suffix including this PLAN
                plan = self.build_plan_state(
                    instructions[: instructions.index(ins) + 1]
                )

        return state, vars_, plan

    def efsm_step(
        self,
        *,
        current_state: str,
        vars_: Dict[str, Any],
        plan: PlanState,
        event: str,
        payload: Dict[str, Any],
    ) -> EfsmStepResult:
        """
        单步 EFSM：按 transitions 优先级取第一条匹配
        """
        if not self.efsm_enabled():
            return EfsmStepResult(True, "ALLOW", "efsm disabled", current_state)

        state = current_state
        trs = self.efsm_transitions()
        matched: Optional[Dict[str, Any]] = None

        for tr in trs:
            fr = tr.get("from", "*")
            ev = tr.get("event", "*")

            from_ok = (
                (fr == "*")
                or (isinstance(fr, str) and fr == state)
                or (isinstance(fr, list) and state in fr)
            )
            if not from_ok:
                continue

            ev_list = (
                [ev] if isinstance(ev, str) else (ev if isinstance(ev, list) else ["*"])
            )
            ev_norm = [str(x).strip().upper() for x in ev_list if str(x).strip()]
            if "*" not in ev_norm and event not in ev_norm:
                continue

            guard_name = tr.get("guard")
            if isinstance(guard_name, str) and guard_name.strip():
                if not self._efsm_guard(guard_name.strip(), plan=plan, payload=payload):
                    continue

            matched = tr
            break

        if matched is None:
            return EfsmStepResult(True, "ALLOW", "efsm: no transition", state)

        to_raw = matched.get("to", "*")
        next_state = (
            state
            if (to_raw == "*" or to_raw is None)
            else (str(to_raw).strip() or state)
        )

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

        self._efsm_apply_actions(
            matched.get("actions"), vars_=vars_, plan=plan, payload=payload
        )

        allow = effect in {"ALLOW", "WARN", "LOG_ONLY", "TRANSFORM"}
        reason = f"efsm: {effect.lower()} via {matched.get('id') or 'transition'}"
        if effect == "BLOCK":
            reason = f"efsm: blocked via {matched.get('id') or 'transition'}"

        return EfsmStepResult(
            allow=allow,
            effect=effect,
            reason=reason,
            next_state=next_state,
            matched_transition=matched.get("id")
            if isinstance(matched.get("id"), str)
            else None,
            meta={"from": state, "to": next_state, "event": event},
        )

    # -------------------------
    # Deterministic rate limit from instructions (no wallclock dependency)
    # -------------------------

    def iter_tool_events(
        self, instructions: List[Dict[str, Any]]
    ) -> Iterable[Tuple[str, str]]:
        """
        yield (instruction_type, tool_name) for tool-like instructions.
        """
        for ins in instructions:
            it = (ins.get("instruction_type") or "").strip().upper()
            content = ins.get("content")
            if it not in {"READ", "WRITE", "EXEC"} or not isinstance(content, dict):
                continue
            tool = content.get("tool_name")
            if isinstance(tool, str) and tool.strip():
                yield (it, tool.strip())

    def check_consecutive_same_tool(
        self,
        *,
        history_instructions: List[Dict[str, Any]],
        tool: str,
    ) -> Tuple[bool, str]:
        rl = self.cfg.get("rate_limit", {}) or {}
        max_repeat = int(rl.get("max_consecutive_same_tool", 0) or 0)
        if max_repeat <= 0:
            return True, "rate ok"

        tool = (tool or "").strip()
        if not tool:
            return True, "rate ok"

        # NEW: count only if the *tail of instructions* are tool-events,
        # and stop counting as soon as we see any non-tool instruction (RESPOND/ASK/REASON/etc).
        streak = 0
        for ins in reversed(history_instructions):
            it = (ins.get("instruction_type") or "").strip().upper()

            # any non-tool instruction breaks the consecutive chain
            if it not in {"READ", "WRITE", "EXEC"}:
                break

            content = ins.get("content")
            if not isinstance(content, dict):
                break

            tname = content.get("tool_name")
            if not (isinstance(tname, str) and tname.strip()):
                break
            tname = tname.strip()

            if tname == tool:
                streak += 1
            else:
                break

        if streak + 1 > max_repeat:
            return (
                False,
                f"too many consecutive repeated tool calls: {streak + 1}>{max_repeat}",
            )
        return True, "rate ok"


def _resolve_policy_config_path() -> str:
    path = os.getenv("ARBITEROS_POLICY_CONFIG", "").strip()
    if not path:
        path = os.path.join(os.path.dirname(__file__), "policy.json")
    return _expand_home(path)


def _env_flag_true(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _resolve_policy_audit_path(cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = cfg if isinstance(cfg, dict) else {}
    audit_cfg = cfg.get("audit")
    audit_cfg = audit_cfg if isinstance(audit_cfg, dict) else {}

    env_enabled = _env_flag_true("ARBITEROS_POLICY_AUDIT_ENABLED")
    env_path = os.getenv("ARBITEROS_POLICY_AUDIT_PATH", "").strip()
    cfg_enabled = bool(audit_cfg.get("enabled", False))
    cfg_path = _expand_home(str(audit_cfg.get("path") or "").strip())

    if env_enabled is False:
        return ""
    if env_enabled is True:
        return _expand_home(env_path) if env_path else cfg_path
    if env_path:
        return _expand_home(env_path)
    if not cfg_enabled:
        return ""
    return cfg_path


def _runtime_reload_key() -> str:
    """
    Build a stable cache key for current runtime source.
    - Inline JSON: hash(json) + audit_path
    - File JSON: path + mtime_ns + size + audit_path
    """
    inline = os.getenv("ARBITEROS_POLICY_CONFIG_JSON", "").strip()
    audit_path = _resolve_policy_audit_path()

    if inline:
        return f"inline:{_sha256(inline)}|audit:{audit_path}"

    path = _resolve_policy_config_path()
    try:
        st = os.stat(path)
        return f"path:{path}|mtime_ns:{st.st_mtime_ns}|size:{st.st_size}|audit:{audit_path}"
    except FileNotFoundError:
        return f"path:{path}|missing|audit:{audit_path}"
    except Exception:
        return f"path:{path}|error|audit:{audit_path}"


class ReloadableRuntimeProxy:
    """
    Lightweight proxy:
    - keep old usage style: RUNTIME.xxx(...)
    - auto reload policy.json when source changes
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runtime: Optional[KernelPolicyRuntime] = None
        self._reload_key: Optional[str] = None

    def _get_runtime(self) -> KernelPolicyRuntime:
        key = _runtime_reload_key()

        with self._lock:
            if self._runtime is None or self._reload_key != key:
                self._runtime = KernelPolicyRuntime.from_env()
                self._reload_key = key
            return self._runtime

    def reload_now(self) -> KernelPolicyRuntime:
        with self._lock:
            self._runtime = KernelPolicyRuntime.from_env()
            self._reload_key = _runtime_reload_key()
            return self._runtime

    def current_reload_key(self) -> str:
        return _runtime_reload_key()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_runtime(), name)


def get_runtime() -> KernelPolicyRuntime:
    return RUNTIME._get_runtime()


# Global runtime proxy (policies import this)
RUNTIME = ReloadableRuntimeProxy()
