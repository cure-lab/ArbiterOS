"""
Shell command analysis helpers for PowerShell (pwsh / Windows PowerShell).

Uses tree-sitter (via tree-sitter-powershell) for proper AST-based parsing,
giving accurate pipeline splitting, cmdlet identification, and file-path
extraction without fragile heuristics.

Public API:
    analyze_command(command)  → CommandAnalysis
    CommandAnalysis           — dataclass with all derived fields (from _base)
"""

import logging
import os
from typing import Any, List, Optional, Tuple

import tree_sitter
import tree_sitter_powershell

from ..registries.windows import classify_exe, classify_exe_risk
from ._base import CommandAnalysis  # noqa: F401 — re-exported for callers

logger = logging.getLogger(__name__)

# Instruction-type priority used when folding across pipeline segments.
_ITYPE_PRIORITY = {"EXEC": 3, "WRITE": 2, "READ": 1}

# PowerShell redirect operators that write to a file.
_WRITE_REDIRECT_TYPES = {">", ">>", "2>", "2>>", "*>", "*>>"}

# Shell operators that propagate the current working directory to the next
# command (sequential execution).  Pipe | and || do NOT propagate.
_CD_PROPAGATING_OPS = {"&&", ";", "\n"}

# PowerShell aliases for Set-Location (cd equivalent).
_PS_CD_ALIASES = frozenset({"cd", "set-location", "sl", "chdir"})

_PS_PARSER: tree_sitter.Parser = tree_sitter.Parser(
    tree_sitter.Language(tree_sitter_powershell.language())
)


# ---------------------------------------------------------------------------
# Path heuristic
# ---------------------------------------------------------------------------


def _is_path_like(token: str) -> bool:
    """Heuristic: does this PowerShell token look like a filesystem path?"""
    norm = token.replace("\\", "/")
    if norm.startswith(("/", "~/", "./", "../", "~")):
        return True
    if norm.startswith(("http://", "https://", "ftp://")):
        return True
    # Windows drive-letter path: C:/..., D:/...
    if len(norm) >= 3 and norm[1] == ":" and norm[2] == "/" and norm[0].isalpha():
        return True
    # UNC path: //server/share
    if norm.startswith("//"):
        return True
    # Relative paths containing slashes (but not flags like -Flag)
    if "/" in norm and not token.startswith("-"):
        return True
    return False


def _is_windows_abs(p: str) -> bool:
    """Return True for Windows absolute paths after backslash normalisation."""
    norm = p.replace("\\", "/")
    if os.path.isabs(norm):
        return True
    return len(norm) >= 3 and norm[1] == ":" and norm[2] == "/" and norm[0].isalpha()


# ---------------------------------------------------------------------------
# tree-sitter AST helpers
# ---------------------------------------------------------------------------


def _ps_iter_flat(node: Any, src: bytes) -> List[Tuple[str, str]]:
    """Walk a tree-sitter PowerShell AST and return [(kind, value), ...].

    *kind* is ``'seg'`` (a single command's source text) or ``'op'`` (a shell
    operator string: ``|``, ``&&``, ``||``, ``;``, or ``\\n``).  Order is
    preserved left-to-right so operators naturally interleave segments.

    PowerShell grammar hierarchy used here:
        program → statement_list
            pipeline (separated by empty_statement ";")
                pipeline_chain           ← first/only chain
                pipeline_chain_tail      ← "&&" or "||"
                pipeline_chain           ← chain after &&/||
                    command (| command)*
    """
    result: List[Tuple[str, str]] = []
    ntype: str = node.type

    if ntype == "program":
        for child in node.children:
            if child.type == "statement_list":
                result.extend(_ps_iter_flat(child, src))

    elif ntype == "statement_list":
        last_was_pipeline = False
        for child in node.children:
            if child.type == "empty_statement":
                result.append(("op", ";"))
                last_was_pipeline = False
            elif child.type == "pipeline":
                if last_was_pipeline:
                    result.append(("op", "\n"))
                result.extend(_ps_iter_flat(child, src))
                last_was_pipeline = True

    elif ntype == "pipeline":
        # Children: pipeline_chain  (pipeline_chain_tail  pipeline_chain)*
        chains = [c for c in node.children if c.type == "pipeline_chain"]
        tails = [c for c in node.children if c.type == "pipeline_chain_tail"]
        for i, chain in enumerate(chains):
            result.extend(_ps_iter_flat(chain, src))
            if i < len(tails):
                for tc in tails[i].children:
                    if tc.type in ("&&", "||"):
                        result.append(("op", tc.type))

    elif ntype == "pipeline_chain":
        # Children: command  ('|'  command)*
        first = True
        for child in node.children:
            if child.type == "command":
                if not first:
                    result.append(("op", "|"))
                result.append(("seg", src[child.start_byte:child.end_byte].decode()))
                first = False

    else:
        # Exotic constructs (if/while/for/switch) — treat as a single segment.
        text = src[node.start_byte:node.end_byte].decode().strip()
        if text:
            result.append(("seg", text))

    return result


def _ps_command_words(cmd_node: Any, src: bytes) -> List[str]:
    """Return the word strings of a PowerShell command node.

    Extracts the command name and all non-parameter argument tokens.
    Strips surrounding quotes from simple string literals.
    Skips ``command_parameter`` nodes (flags like ``-Force``, ``-Path``).
    """
    words: List[str] = []
    for child in cmd_node.children:
        if child.type == "command_name":
            words.append(src[child.start_byte:child.end_byte].decode())
        elif child.type == "command_elements":
            for elem in child.children:
                if elem.type == "generic_token":
                    words.append(src[elem.start_byte:elem.end_byte].decode())
                elif elem.type == "array_literal_expression":
                    raw = src[elem.start_byte:elem.end_byte].decode().strip()
                    # Simple double-quoted string: "..."
                    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
                        words.append(raw[1:-1])
                    # Simple single-quoted string: '...'
                    elif raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
                        words.append(raw[1:-1])
                    # Variables / expressions — not useful as path tokens; skip.
                # Skip: command_parameter (-Flag), command_argument_sep, redirection
    return words


def _ps_first_command(node: Any) -> Optional[Any]:
    """Return the first ``command`` node reachable from *node* (DFS)."""
    if node.type == "command":
        return node
    for child in node.children:
        found = _ps_first_command(child)
        if found is not None:
            return found
    return None


def _ps_find_exec_units(node: Any) -> List[Tuple[Any, List[Any]]]:
    """Return ``[(command_node, [redirection_nodes]), …]`` for all commands.

    In the PowerShell grammar, redirections live *inside* the command node
    (as children of ``command_elements``), unlike bash where they are siblings
    of the command in a ``redirected_statement``.
    """
    result: List[Tuple[Any, List[Any]]] = []
    if node.type == "command":
        redirs: List[Any] = []
        for child in node.children:
            if child.type == "command_elements":
                for elem in child.children:
                    if elem.type == "redirection":
                        redirs.append(elem)
        result.append((node, redirs))
    else:
        for child in node.children:
            result.extend(_ps_find_exec_units(child))
    return result


def _ps_collect_from_exec_unit(
    cmd_node: Any,
    redirect_nodes: List[Any],
    src: bytes,
    seg_itype: str,
) -> Tuple[List[str], List[str]]:
    """Extract path tokens and write targets from one PowerShell exec unit."""
    path_tokens: List[str] = []
    write_targets: List[str] = []

    words = _ps_command_words(cmd_node, src)

    # Executable word: include only when it is itself a path-like file.
    if words and seg_itype == "EXEC" and _is_path_like(words[0]):
        path_tokens.append(words[0])

    # Argument words (after the cmdlet name).
    for token in words[1:]:
        if _is_path_like(token):
            path_tokens.append(token)
            if seg_itype == "WRITE":
                write_targets.append(token)
        elif not token.startswith("-") and seg_itype in ("READ", "WRITE"):
            path_tokens.append(token)
            if seg_itype == "WRITE":
                write_targets.append(token)

    # Redirect targets: file_redirection_operator > redirected_file_name > generic_token
    for redir in redirect_nodes:
        op: Optional[str] = None
        target: Optional[str] = None
        for child in redir.children:
            if child.type == "file_redirection_operator":
                op = src[child.start_byte:child.end_byte].decode().strip()
            elif child.type == "redirected_file_name":
                for sub in child.children:
                    if sub.type == "generic_token":
                        target = src[sub.start_byte:sub.end_byte].decode()
        if target is not None:
            path_tokens.append(target)
            if op in _WRITE_REDIRECT_TYPES:
                write_targets.append(target)

    return path_tokens, write_targets


# ---------------------------------------------------------------------------
# Pipeline splitting, segment classification, path collection
# ---------------------------------------------------------------------------


def _split_pipeline(command: str) -> Tuple[List[str], List[str]]:
    """Parse *command* and split on shell operators.

    Returns:
        segments:  one string per command in pipeline / list order.
        operators: shell operators interleaving the segments.
    """
    if not command.strip():
        return [], []

    src = command.encode()
    flat = _ps_iter_flat(_PS_PARSER.parse(src).root_node, src)
    segments = [v for k, v in flat if k == "seg"]
    operators = [v for k, v in flat if k == "op"]
    return segments, operators


def _classify_segment(seg_str: str) -> str:
    """Return instruction type (EXEC/WRITE/READ) for a single command string."""
    if not seg_str.strip():
        return "READ"

    src = seg_str.strip().encode()
    cmd_node = _ps_first_command(_PS_PARSER.parse(src).root_node)
    words: List[str] = _ps_command_words(cmd_node, src) if cmd_node is not None else []

    if not words:
        return "READ"

    # PowerShell is case-insensitive — normalise to lowercase for registry lookup.
    exe = words[0].lower()
    subcommand: Optional[str] = None
    if len(words) > 1 and not words[1].startswith("-"):
        subcommand = words[1].lower()
    return classify_exe(exe, subcommand)


def _classify_segment_risk(seg_str: str) -> str:
    """Return risk level (HIGH/UNKNOWN/LOW) for a single command string."""
    if not seg_str.strip():
        return "UNKNOWN"

    src = seg_str.strip().encode()
    words_list: List[List[str]] = [
        ws
        for cmd_node, _ in _ps_find_exec_units(_PS_PARSER.parse(src).root_node)
        if (ws := _ps_command_words(cmd_node, src))
    ]

    if not words_list:
        return "UNKNOWN"

    found_any = False
    all_low = True
    for words in words_list:
        if not words:
            continue
        found_any = True
        exe = words[0].lower()
        subcommand: Optional[str] = None
        if len(words) > 1 and not words[1].startswith("-"):
            subcommand = words[1].lower()
        cmd_risk = classify_exe_risk(exe, subcommand)
        logger.debug(
            "_classify_segment_risk: segment=%r exe=%r sub=%r → %s",
            seg_str,
            exe,
            subcommand,
            cmd_risk,
        )
        if cmd_risk == "HIGH":
            return "HIGH"
        if cmd_risk != "LOW":
            all_low = False

    if not found_any:
        return "UNKNOWN"
    return "LOW" if all_low else "UNKNOWN"


def _parse_cd_dir(seg_str: str) -> Optional[str]:
    """If *seg_str* is a Set-Location / cd command, return the target directory.

    Returns the path string when it is absolute (POSIX or Windows drive-letter);
    returns None for relative paths, non-cd commands, or unparseable segments.
    """
    src = seg_str.strip().encode()
    cmd_node = _ps_first_command(_PS_PARSER.parse(src).root_node)
    if cmd_node is None:
        return None
    words = _ps_command_words(cmd_node, src)
    if not words or words[0].lower() not in _PS_CD_ALIASES:
        return None

    for w in words[1:]:
        if w.startswith("-"):
            continue
        # Normalise and check if absolute.
        norm = w.replace("\\", "/")
        expanded = os.path.expanduser(norm)
        if os.path.isabs(expanded) or _is_windows_abs(norm):
            return expanded if os.path.isabs(expanded) else norm
        return None  # relative path — cannot resolve without CWD context
    return None


def _resolve_path(p: str, cd_dir: str) -> str:
    """Resolve *p* relative to *cd_dir* when *p* is a relative filesystem path."""
    norm = p.replace("\\", "/")
    if norm.startswith(("http://", "https://", "ftp://")):
        return p
    if norm.startswith("/") or _is_windows_abs(norm):
        return norm
    expanded = os.path.expanduser(norm)
    if os.path.isabs(expanded):
        return expanded
    resolved = os.path.normpath(os.path.join(cd_dir, expanded))
    logger.debug("_resolve_path: %r → %r (cd=%r)", p, resolved, cd_dir)
    return resolved


def _collect_exec_path_tokens(
    seg_strings: List[str],
    itypes: List[str],
    operators: Optional[List[str]] = None,
) -> Tuple[List[str], List[str]]:
    """Collect file-path tokens and write targets from all pipeline segments."""
    path_tokens: List[str] = []
    write_targets: List[str] = []
    ops: List[str] = operators or []
    cd_dir: Optional[str] = None

    for i, (seg_str, seg_itype) in enumerate(zip(seg_strings, itypes)):
        op_after: Optional[str] = ops[i] if i < len(ops) else None

        new_cd = _parse_cd_dir(seg_str)
        if new_cd is not None:
            cd_dir = new_cd if op_after in _CD_PROPAGATING_OPS else None
            logger.debug(
                "_collect_exec_path_tokens: cd context → %r (op_after=%r)",
                cd_dir,
                op_after,
            )
            continue

        try:
            src = seg_str.strip().encode()
            tree = _PS_PARSER.parse(src)
            for cmd_node, redir_nodes in _ps_find_exec_units(tree.root_node):
                pt, wt = _ps_collect_from_exec_unit(
                    cmd_node, redir_nodes, src, seg_itype
                )
                if cd_dir:
                    pt = [_resolve_path(p, cd_dir) for p in pt]
                    wt = [_resolve_path(p, cd_dir) for p in wt]
                path_tokens.extend(pt)
                write_targets.extend(wt)
        except Exception:
            logger.warning(
                "tree-sitter failed for segment %r; skipping path extraction",
                seg_str,
            )

        if op_after is not None and op_after not in _CD_PROPAGATING_OPS:
            cd_dir = None

    return path_tokens, write_targets


# ---------------------------------------------------------------------------
# High-level analysis
# ---------------------------------------------------------------------------


def analyze_command(command: str) -> CommandAnalysis:
    """Parse *command* and return all derived analysis fields in one call.

    Encapsulates pipeline splitting, per-segment classification, risk and
    instruction-type folding, and path-token collection.  Callers only need to
    perform registry lookups (confidentiality / trustworthiness) on the
    returned *path_tokens* and register *write_targets*.

    For empty commands all list fields are empty and itype/risk use safe
    defaults (EXEC / UNKNOWN).
    """
    if "\n" in command:
        logger.warning(
            "analyze_command (powershell): multi-line command; newlines treated"
            " as separators: %r",
            command,
        )

    if not command.strip():
        return CommandAnalysis(
            command=command,
            segments=[],
            operators=[],
            itypes=[],
            itype="EXEC",
            risks=[],
            risk="UNKNOWN",
            path_tokens=[],
            write_targets=[],
        )

    segments, operators = _split_pipeline(command)
    if not segments:
        segments = [command]

    itypes = [_classify_segment(s) for s in segments]
    itype = max(itypes, key=lambda t: _ITYPE_PRIORITY.get(t, 0))

    risks = [_classify_segment_risk(s) for s in segments]
    risk = "HIGH" if "HIGH" in risks else "UNKNOWN" if "UNKNOWN" in risks else "LOW"
    logger.debug(
        "analyze_command (powershell): segment_risks=%r → risk=%s", risks, risk
    )

    path_tokens, write_targets = _collect_exec_path_tokens(segments, itypes, operators)

    return CommandAnalysis(
        command=command,
        segments=segments,
        operators=operators,
        itypes=itypes,
        itype=itype,
        risks=risks,
        risk=risk,
        path_tokens=path_tokens,
        write_targets=write_targets,
    )
