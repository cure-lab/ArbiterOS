"""
Shell command analysis helpers for instruction parsing.

Uses bashlex for proper AST-based shell parsing, giving accurate pipeline
splitting, executable identification, and file-path extraction without fragile
quote-tracking heuristics.

Public API (consumed by openclaw._parse_exec and tests):
    split_pipeline(command)         → (segments, operators)
    split_pipeline_str(command)     → segments
    extract_shell_operators(command) → operators
    classify_segment(seg_str)       → instruction type string
    collect_exec_path_tokens(segs, itypes) → (path_tokens, write_targets)
    is_path_like(token)             → bool
"""

import logging
import os
import shlex
from typing import Any, List, Optional, Tuple

import bashlex

from ..tool_parsers.linux_registry import classify_exe, classify_exe_risk

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


# ---------------------------------------------------------------------------
# Path heuristic
# ---------------------------------------------------------------------------


def is_path_like(token: str) -> bool:
    """Heuristic: does this shell token look like a filesystem path or URL?"""
    return (
        token.startswith(("/", "~/", "./", "../", "~", "http://", "https://", "ftp://"))
        or "\\" in token  # Windows path
        or ("/" in token and not token.startswith("-"))
    )


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _children(node) -> list:
    """Return the child nodes of a bashlex AST node.

    bashlex stores children in ``node.parts`` for most node types, but
    ``compound`` nodes (subshells, brace groups) use ``node.list`` instead.
    All other node kinds (operator, pipe, reservedword, …) have no children.
    """
    if node.kind == "compound":
        return node.list
    if hasattr(node, "parts"):
        return node.parts
    return []


def _iter_flat(nodes: list, command: str) -> List[Tuple[str, str]]:
    """Recursively flatten bashlex AST nodes into [(kind, value), ...].

    kind is ``'seg'`` (a command's original text) or ``'op'`` (a shell
    operator string).  The order is preserved left-to-right so operators
    naturally interleave segments.
    """
    result: List[Tuple[str, str]] = []
    for node in nodes:
        if node.kind == "command":
            result.append(("seg", command[node.pos[0] : node.pos[1]]))
        elif node.kind == "pipeline":
            for part in node.parts:
                if part.kind == "command":
                    result.append(("seg", command[part.pos[0] : part.pos[1]]))
                elif part.kind == "pipe":
                    result.append(("op", part.pipe))
        elif node.kind == "list":
            for part in node.parts:
                if part.kind == "operator":
                    result.append(("op", part.op))
                else:
                    result.extend(_iter_flat([part], command))
        elif node.kind == "compound":
            # Subshell/brace group — recurse so inner commands become segments.
            result.extend(_iter_flat(_children(node), command))
        # function defs and other exotic nodes are silently ignored.
    return result


def _first_words(nodes: list) -> List[str]:
    """Return the word strings of the first ``command`` node found in *nodes*."""
    for node in nodes:
        if node.kind == "command":
            return [p.word for p in node.parts if p.kind == "word"]
        words = _first_words(_children(node))
        if words:
            return words
    return []


def _find_command_nodes(nodes: list) -> list:
    """Return all ``command`` nodes reachable from *nodes* (depth-first)."""
    result = []
    for node in nodes:
        if node.kind == "command":
            result.append(node)
        else:
            result.extend(_find_command_nodes(_children(node)))
    return result


# ---------------------------------------------------------------------------
# Manual fallback (for commands bashlex cannot parse)
# ---------------------------------------------------------------------------


def _split_pipeline_manual(command: str) -> Tuple[List[str], List[str]]:
    """Quote-aware manual pipeline split used when bashlex raises."""
    segments: List[str] = []
    operators: List[str] = []
    in_single = in_double = escaped = False
    start = i = 0

    while i < len(command):
        c = command[i]
        if escaped:
            escaped = False
        elif c == "\\" and not in_single:
            escaped = True
        elif c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            two = command[i : i + 2]
            if two in ("||", "&&"):
                seg = command[start:i].strip()
                if seg:
                    segments.append(seg)
                operators.append(two)
                i += 2
                start = i
                continue
            if c in ("|", ";", "&", "\n"):
                seg = command[start:i].strip()
                if seg:
                    segments.append(seg)
                operators.append(c)
                start = i + 1
        i += 1

    seg = command[start:].strip()
    if seg:
        segments.append(seg)
    return segments, operators


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def split_pipeline(command: str) -> Tuple[List[str], List[str]]:
    """Parse *command* and split on shell operators (||, &&, |, ;, &, newline).

    Uses bashlex for accurate quote-aware parsing; each segment is sliced
    from the original string so quotes are preserved verbatim.

    Returns:
        segments:  one string per command in pipeline / list order.
        operators: shell operators interleaving the segments.
    """
    if not command.strip():
        return [], []

    try:
        parts = bashlex.parse(command)
    except Exception:
        logger.debug(
            "bashlex.parse failed for %r; falling back to manual split", command
        )
        return _split_pipeline_manual(command)

    flat: List[Tuple[str, str]] = []
    for i, part in enumerate(parts):
        if i > 0:
            # Top-level parts are separated by newlines (no explicit node).
            flat.append(("op", "\n"))
        flat.extend(_iter_flat([part], command))

    segments = [v for k, v in flat if k == "seg"]
    operators = [v for k, v in flat if k == "op"]
    return segments, operators


def split_pipeline_str(command: str) -> List[str]:
    """Return pipeline segments; see :func:`split_pipeline` for details."""
    segments, _ = split_pipeline(command)
    return segments


def extract_shell_operators(command: str) -> List[str]:
    """Return the shell operators found between pipeline segments."""
    _, operators = split_pipeline(command)
    return operators


def classify_segment(seg_str: str) -> str:
    """Return instruction type (EXEC/WRITE/READ) for a single command string.

    Parses *seg_str* with bashlex to locate the executable and optional
    subcommand, then delegates to :func:`linux_registry.classify_exe`.
    Falls back to ``shlex.split`` when bashlex cannot parse the input.
    """
    if not seg_str.strip():
        return "READ"

    words: List[str] = []
    try:
        parts = bashlex.parse(seg_str.strip())
        words = _first_words(parts)
    except Exception:
        logger.debug(
            "bashlex failed for segment %r; falling back to shlex", seg_str
        )
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


def classify_segment_risk(seg_str: str) -> str:
    """Return risk level (HIGH/LOW/UNKNOWN) for a single command string.

    Parses *seg_str* with bashlex to locate all command nodes, then
    delegates each to :func:`linux_registry.classify_exe_risk`.
    Falls back to ``shlex.split`` when bashlex cannot parse the input.

    When multiple commands are present in the segment (e.g. subshell groups
    not split by :func:`split_pipeline`), the highest risk wins:
    HIGH > LOW > UNKNOWN.
    Returns "UNKNOWN" for empty or unparseable segments.
    """
    if not seg_str.strip():
        return "UNKNOWN"

    words_list: List[List[str]] = []
    try:
        parts = bashlex.parse(seg_str.strip())
        cmd_nodes = _find_command_nodes(parts)
        words_list = [
            [p.word for p in node.parts if p.kind == "word"]
            for node in cmd_nodes
        ]
    except Exception:
        logger.debug(
            "bashlex failed for segment %r (risk); falling back to shlex", seg_str
        )
        raw = seg_str.strip().strip("()")
        try:
            words_list = [shlex.split(raw)]
        except ValueError:
            words_list = [raw.split()]

    if not words_list:
        logger.debug("classify_segment_risk: no words in %r → UNKNOWN", seg_str)
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
            "classify_segment_risk: segment=%r exe=%r sub=%r → %s",
            seg_str, exe, subcommand, cmd_risk,
        )
        if cmd_risk == "HIGH":
            return "HIGH"
        if cmd_risk != "LOW":  # UNKNOWN taints: prevents LOW result
            all_low = False

    if not found_any:
        logger.debug("classify_segment_risk: all word lists empty in %r → UNKNOWN", seg_str)
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


def _collect_from_command(
    cmd_node: Any, seg_itype: str
) -> Tuple[List[str], List[str]]:
    """Extract path tokens and write targets from a single bashlex command node."""
    path_tokens: List[str] = []
    write_targets: List[str] = []

    words = [p for p in cmd_node.parts if p.kind == "word"]
    redirects = [p for p in cmd_node.parts if p.kind == "redirect"]

    # Executable word: include only when it is itself a path-like file
    # (e.g. ~/downloads/malware.sh); its location determines trust.
    if words and seg_itype == "EXEC" and is_path_like(words[0].word):
        path_tokens.append(words[0].word)

    # Argument words (after the executable).
    for w in words[1:]:
        token = w.word
        if is_path_like(token):
            path_tokens.append(token)
            if seg_itype == "WRITE":
                write_targets.append(token)
        elif not token.startswith("-") and seg_itype in ("READ", "WRITE"):
            # Bare non-flag argument in a READ/WRITE segment (e.g. ``cat foo.txt``).
            path_tokens.append(token)
            if seg_itype == "WRITE":
                write_targets.append(token)

    # Redirect targets: bashlex gives us these directly — no manual scanning needed.
    for redir in redirects:
        # output is a WordNode for file redirects; an int for fd redirects (2>&1).
        if not hasattr(redir.output, "word"):
            continue
        target = redir.output.word
        path_tokens.append(target)
        if redir.type in _WRITE_REDIRECT_TYPES:
            write_targets.append(target)

    return path_tokens, write_targets


def collect_exec_path_tokens(
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
                "collect_exec_path_tokens: cd context → %r (op_after=%r)",
                cd_dir, op_after,
            )
            continue

        try:
            parts = bashlex.parse(seg_str.strip())
        except Exception:
            logger.warning(
                "bashlex failed for segment %r; skipping path extraction", seg_str
            )
        else:
            for cmd_node in _find_command_nodes(parts):
                pt, wt = _collect_from_command(cmd_node, seg_itype)
                if cd_dir:
                    pt = [_resolve_path(p, cd_dir) for p in pt]
                    wt = [_resolve_path(p, cd_dir) for p in wt]
                path_tokens.extend(pt)
                write_targets.extend(wt)

        # A non-propagating operator after this segment breaks the cd context
        # for whatever comes next.
        if op_after is not None and op_after not in _CD_PROPAGATING_OPS:
            cd_dir = None

    return path_tokens, write_targets
