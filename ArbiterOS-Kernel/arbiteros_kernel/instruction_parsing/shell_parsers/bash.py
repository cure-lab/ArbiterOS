"""
Shell command analysis helpers for instruction parsing.

Uses tree-sitter (via tree-sitter-bash) for proper AST-based shell parsing,
giving accurate pipeline splitting, executable identification, and file-path
extraction without fragile quote-tracking heuristics.

Public API:
    analyze_command(command)  → CommandAnalysis
    CommandAnalysis           — dataclass with all derived fields
"""

import logging
import os
import shlex
from typing import Any, List, Optional, Tuple

from ._base import CommandAnalysis  # noqa: F401 — re-exported for callers

import tree_sitter
import tree_sitter_bash

from ..registries.linux import classify_exe, classify_exe_risk

logger = logging.getLogger(__name__)

# Instruction-type priority used when folding across pipeline segments.
_ITYPE_PRIORITY = {"EXEC": 3, "WRITE": 2, "READ": 1}

# Redirect types whose target is a file we write to.
_WRITE_REDIRECT_TYPES = {">", ">>", "&>", "&>>"}

# Shell operators that propagate the current shell's working directory to the
# next command (sequential execution).  Pipe and || do not: each side of a
# pipe runs in a subshell, and || only runs its right side when the left fails
# (so a successful `cd` means the right side never executes).
_CD_PROPAGATING_OPS = {"&&", ";", "\n"}


_BASH_PARSER: tree_sitter.Parser = tree_sitter.Parser(
    tree_sitter.Language(tree_sitter_bash.language())
)

# Node types that represent executable content (vs. operators / punctuation).
_CONTENT_TYPES = frozenset(
    {
        "command",
        "redirected_statement",
        "pipeline",
        "list",
        "subshell",
        "compound_statement",
        "if_statement",
        "while_statement",
        "for_statement",
        "function_definition",
        "case_statement",
    }
)

# Word-like argument node types in the tree-sitter bash grammar.
_WORD_NODE_TYPES = frozenset(
    {
        "word",
        "raw_string",
        "string",
        "number",
        "concatenation",
        "simple_expansion",
        "expansion",
        "command_substitution",
        "process_substitution",
        "arithmetic_expansion",
    }
)


# ---------------------------------------------------------------------------
# Path heuristic
# ---------------------------------------------------------------------------


def _is_path_like(token: str) -> bool:
    """Heuristic: does this shell token look like a filesystem path or URL?"""
    return (
        token.startswith(("/", "~/", "./", "../", "~", "http://", "https://", "ftp://"))
        or "\\" in token  # Windows path
        or ("/" in token and not token.startswith("-"))
    )


# ---------------------------------------------------------------------------
# tree-sitter AST helpers
# ---------------------------------------------------------------------------


def _ts_iter_flat(node: Any, src: bytes) -> List[Tuple[str, str]]:
    """Recursively walk a tree-sitter bash AST node.

    Returns ``[(kind, value), ...]`` where *kind* is ``'seg'`` (a command's
    original text) or ``'op'`` (a shell operator string).  Order is preserved
    left-to-right so operators naturally interleave segments.
    """
    result: List[Tuple[str, str]] = []
    ntype: str = node.type

    if ntype == "program":
        # Top-level children are separated by explicit operators (';', '&') or
        # implicit newlines (adjacent content nodes with no operator between them).
        last_was_content = False
        for child in node.children:
            if getattr(child, "is_extra", False):
                continue
            if child.type in (";", "&"):
                result.append(("op", child.type))
                last_was_content = False
            elif child.type in _CONTENT_TYPES:
                if last_was_content:
                    result.append(("op", "\n"))
                result.extend(_ts_iter_flat(child, src))
                last_was_content = True
            # skip punctuation, whitespace, ERROR nodes, etc.

    elif ntype == "pipeline":
        # Children alternate: content | content | content …
        for child in node.children:
            if child.type == "|":
                result.append(("op", "|"))
            elif child.type in _CONTENT_TYPES:
                # Each content node in a pipeline is a leaf segment.
                result.append(("seg", src[child.start_byte : child.end_byte].decode()))

    elif ntype == "list":
        # Children alternate: content op content op content …
        # The op can be '&&', '||', ';', or '&'.
        for child in node.children:
            if child.type in ("&&", "||", ";", "&"):
                result.append(("op", child.type))
            elif child.type in _CONTENT_TYPES:
                result.extend(_ts_iter_flat(child, src))

    elif ntype in ("command", "redirected_statement"):
        # Leaf: this is a single command (possibly with redirections).
        result.append(("seg", src[node.start_byte : node.end_byte].decode()))

    elif ntype == "subshell":
        # Recurse into the body, skipping the '(' and ')' delimiters.
        for child in node.children:
            if child.type not in ("(", ")") and not getattr(child, "is_extra", False):
                result.extend(_ts_iter_flat(child, src))

    else:
        # Exotic constructs (if/while/for/function) — treat as a single segment
        # so they are at least classified rather than silently dropped.
        text = src[node.start_byte : node.end_byte].decode().strip()
        if text:
            result.append(("seg", text))

    return result


def _ts_command_words(cmd_node: Any, src: bytes) -> List[str]:
    """Return the word strings of a tree-sitter ``command`` node."""
    words: List[str] = []
    for child in cmd_node.children:
        if child.type == "command_name":
            words.append(src[child.start_byte : child.end_byte].decode())
        elif child.type in _WORD_NODE_TYPES:
            words.append(src[child.start_byte : child.end_byte].decode())
    return words


def _ts_first_command(node: Any) -> Optional[Any]:
    """Return the first ``command`` node reachable from *node* (DFS)."""
    if node.type == "command":
        return node
    for child in node.children:
        found = _ts_first_command(child)
        if found is not None:
            return found
    return None


def _ts_find_exec_units(node: Any) -> List[Tuple[Any, List[Any]]]:
    """Return ``[(command_node, [redirect_nodes]), …]`` for all commands.

    Each "exec unit" is either:
    • a bare ``command`` node (no redirections), or
    • the inner ``command`` node of a ``redirected_statement`` together with
      its sibling ``file_redirect`` / ``heredoc_redirect`` nodes.
    """
    result: List[Tuple[Any, List[Any]]] = []
    ntype = node.type

    if ntype == "command":
        result.append((node, []))
    elif ntype == "redirected_statement":
        cmd: Optional[Any] = None
        redirs: List[Any] = []
        for child in node.children:
            if child.type == "command":
                cmd = child
            elif child.type in (
                "file_redirect",
                "heredoc_redirect",
                "herestring_redirect",
            ):
                redirs.append(child)
        if cmd is not None:
            result.append((cmd, redirs))
    else:
        for child in node.children:
            result.extend(_ts_find_exec_units(child))

    return result


def _ts_collect_from_exec_unit(
    cmd_node: Any,
    redirect_nodes: List[Any],
    src: bytes,
    seg_itype: str,
) -> Tuple[List[str], List[str]]:
    """Extract path tokens and write targets from one exec unit."""
    path_tokens: List[str] = []
    write_targets: List[str] = []

    words = _ts_command_words(cmd_node, src)

    # Executable word: include only when it is itself a path-like file.
    if words and seg_itype == "EXEC" and _is_path_like(words[0]):
        path_tokens.append(words[0])

    # Argument words (after the executable).
    for token in words[1:]:
        if _is_path_like(token):
            path_tokens.append(token)
            if seg_itype == "WRITE":
                write_targets.append(token)
        elif not token.startswith("-") and seg_itype in ("READ", "WRITE"):
            path_tokens.append(token)
            if seg_itype == "WRITE":
                write_targets.append(token)

    # Redirect targets: tree-sitter surfaces these as file_redirect children of
    # the enclosing redirected_statement node.
    for redir in redirect_nodes:
        op: Optional[str] = None
        target: Optional[str] = None
        for child in redir.children:
            if child.type in (">", ">>", "<", "&>", "&>>", ">&", "<<", "<<<"):
                op = child.type
            elif child.type in _WORD_NODE_TYPES:
                target = src[child.start_byte : child.end_byte].decode()
        if target is not None:
            path_tokens.append(target)
            if op in _WRITE_REDIRECT_TYPES:
                write_targets.append(target)

    return path_tokens, write_targets


# ---------------------------------------------------------------------------
# Pipeline splitting, segment classification, path collection
# ---------------------------------------------------------------------------


def _split_pipeline(command: str) -> Tuple[List[str], List[str]]:
    """Parse *command* and split on shell operators (||, &&, |, ;, &, newline).

    Uses tree-sitter for accurate quote-aware parsing; each segment is sliced
    from the original string so quotes are preserved verbatim.

    Returns:
        segments:  one string per command in pipeline / list order.
        operators: shell operators interleaving the segments.
    """
    if not command.strip():
        return [], []

    src = command.encode()
    flat = _ts_iter_flat(_BASH_PARSER.parse(src).root_node, src)
    segments = [v for k, v in flat if k == "seg"]
    operators = [v for k, v in flat if k == "op"]
    return segments, operators


def _classify_segment(seg_str: str) -> str:
    """Return instruction type (EXEC/WRITE/READ) for a single command string.

    Parses *seg_str* with tree-sitter to locate the executable and optional
    subcommand, then delegates to :func:`linux_registry.classify_exe`.
    Falls back to ``shlex.split`` when tree-sitter cannot parse the input.
    """
    if not seg_str.strip():
        return "READ"

    src = seg_str.strip().encode()
    cmd_node = _ts_first_command(_BASH_PARSER.parse(src).root_node)
    words: List[str] = _ts_command_words(cmd_node, src) if cmd_node is not None else []

    if not words:
        raw = seg_str.strip().strip("()")
        try:
            words = shlex.split(raw)
        except ValueError:
            words = raw.split()

    if not words:
        return "READ"

    exe = os.path.basename(words[0])
    subcommand: Optional[str] = None
    if len(words) > 1 and not words[1].startswith("-"):
        subcommand = words[1]
    return classify_exe(exe, subcommand)


def _classify_segment_risk(seg_str: str) -> str:
    """Return risk level (HIGH/LOW/UNKNOWN) for a single command string.

    Parses *seg_str* with tree-sitter to locate all command nodes, then
    delegates each to :func:`linux_registry.classify_exe_risk`.
    Falls back to ``shlex.split`` when tree-sitter cannot parse the input.

    When multiple commands are present in the segment (e.g. subshell groups
    not split by :func:`_split_pipeline`), the highest risk wins:
    HIGH > LOW > UNKNOWN.
    Returns "UNKNOWN" for empty or unparseable segments.
    """
    if not seg_str.strip():
        return "UNKNOWN"

    src = seg_str.strip().encode()
    words_list: List[List[str]] = [
        ws
        for cmd_node, _ in _ts_find_exec_units(_BASH_PARSER.parse(src).root_node)
        if (ws := _ts_command_words(cmd_node, src))
    ]

    if not words_list:
        raw = seg_str.strip().strip("()")
        try:
            words_list = [shlex.split(raw)]
        except ValueError:
            words_list = [raw.split()]

    if not words_list:
        logger.debug("_classify_segment_risk: no words in %r → UNKNOWN", seg_str)
        return "UNKNOWN"

    # HIGH > UNKNOWN > LOW
    # any UNKNOWN prevents a LOW result.
    found_any = False
    all_low = True
    for words in words_list:
        if not words:
            continue
        found_any = True
        exe = os.path.basename(words[0])
        subcommand: Optional[str] = None
        if len(words) > 1 and not words[1].startswith("-"):
            subcommand = words[1]
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
        if cmd_risk != "LOW":  # UNKNOWN taints: prevents LOW result
            all_low = False

    if not found_any:
        logger.debug(
            "_classify_segment_risk: all word lists empty in %r → UNKNOWN", seg_str
        )
        return "UNKNOWN"
    return "LOW" if all_low else "UNKNOWN"


def _parse_cd_dir(seg_str: str) -> Optional[str]:
    """If *seg_str* is a ``cd <dir>`` command, return the expanded absolute
    directory; return None for non-cd segments or relative (unresolvable) dirs.
    """
    try:
        words = shlex.split(seg_str.strip())
    except ValueError:
        words = seg_str.strip().split()
    if not words or words[0] != "cd" or len(words) < 2:
        return None
    expanded = os.path.expanduser(words[1])
    return expanded if os.path.isabs(expanded) else None


def _resolve_path(p: str, cd_dir: str) -> str:
    """Resolve *p* relative to *cd_dir* when *p* is a relative filesystem path.

    Absolute paths, URLs, and ``~/…`` expansions that are already absolute
    after ``expanduser`` are returned unchanged.
    """
    if p.startswith(("/", "http://", "https://", "ftp://")):
        return p
    expanded = os.path.expanduser(p)
    if os.path.isabs(expanded):
        return expanded
    resolved = os.path.normpath(os.path.join(cd_dir, expanded))
    logger.debug("_resolve_path: %r → %r (cd=%r)", p, resolved, cd_dir)
    return resolved


def _collect_exec_path_tokens(
    seg_strings: List[str], itypes: List[str], operators: Optional[List[str]] = None
) -> Tuple[List[str], List[str]]:
    """Collect file-path tokens and write targets from all pipeline segments.

    For each segment:
    • EXEC  — include the executable only when it is a path-like file
              (e.g. ``~/downloads/malware.sh``); other arguments are also
              included when path-like.
    • READ/WRITE — also include bare non-flag arguments so that e.g.
                   ``cat input.txt`` factors in trustworthiness.
    • Any segment — redirect output targets (``>``, ``>>``, …) are always
                   collected as write targets.

    When *operators* is provided, ``cd <dir>`` segments update a running
    working-directory context that is used to resolve relative path tokens in
    subsequent segments.  The context propagates across ``&&``, ``;``, and
    newline operators; it is reset to *None* after ``|`` or ``||`` because
    those operators run each side in a subshell or conditionally.
    Only absolute (or ``~/…``-expanded) ``cd`` targets can be tracked; a
    bare relative ``cd subdir`` leaves the context unchanged (unknown base).

    Returns:
        path_tokens:   all paths used for security classification (conf/trust).
        write_targets: paths that receive written data; registered in the user
                       registry so future reads inherit the correct taint.
    """
    path_tokens: List[str] = []
    write_targets: List[str] = []
    ops: List[str] = operators or []

    # Tracks the effective working directory as we walk through segments.
    cd_dir: Optional[str] = None

    for i, (seg_str, seg_itype) in enumerate(zip(seg_strings, itypes)):
        op_after: Optional[str] = ops[i] if i < len(ops) else None

        # If this segment is a cd command, update the context and skip
        # token collection (cd itself produces no meaningful path tokens).
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
            tree = _BASH_PARSER.parse(src)
            for cmd_node, redir_nodes in _ts_find_exec_units(tree.root_node):
                pt, wt = _ts_collect_from_exec_unit(
                    cmd_node, redir_nodes, src, seg_itype
                )
                if cd_dir:
                    pt = [_resolve_path(p, cd_dir) for p in pt]
                    wt = [_resolve_path(p, cd_dir) for p in wt]
                path_tokens.extend(pt)
                write_targets.extend(wt)
        except Exception:
            logger.warning(
                "tree-sitter failed for segment %r; skipping path extraction", seg_str
            )

        # A non-propagating operator after this segment breaks the cd context
        # for whatever comes next.
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
            "analyze_command: multi-line command; newlines treated as separators: %r",
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
    logger.debug("analyze_command: segment_risks=%r → risk=%s", risks, risk)

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
