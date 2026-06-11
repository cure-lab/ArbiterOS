"""Command-line interface for checkpoint-plugin."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from ._utils import read_metadata_json
from .coordinator import CheckpointCoordinator, TurnRecord, reanchor_last_turn_to_eof, resolve_session_title
from .env.collector import environment_from_blob
from .fs.snapshot import filesystem_from_blob
from .integrations.hook_installer import install_hooks, uninstall_hooks
from .paths import config_path, load_config, sessions_dir, write_config
from .resume import ResumeOptions, ResumeOrchestrator, execute_resume_open, restore_opencode_metadata
from .retention import clean_empty_sessions, clean_keep_last, compact_legacy_blobs
from .store import CheckpointStore
from .types import ResumePlan
from .ui.diff_viewer import show_diff_viewer
from .ui.session_browser import BrowserAction, show_session_browser


def _supports_color(stream: Any = sys.stdout) -> bool:
    """True when ANSI color is safe to emit (a real TTY, color not disabled)."""
    # Honor the NO_COLOR convention (https://no-color.org) and non-TTY pipes/files.
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _colorize(text: str, style: str, *, stream: Any = sys.stdout) -> str:
    """Wrap text in an ANSI style, but only when the stream supports color."""
    codes = {"bold": "1", "green": "32", "cyan": "36", "yellow": "33"}
    if not _supports_color(stream):
        return text
    prefix = "".join(f"\033[{codes[part]}m" for part in style.split() if part in codes)
    return f"{prefix}{text}\033[0m" if prefix else text



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="checkpoint")
    sub = parser.add_subparsers(dest="command")

    save = sub.add_parser("save", help="Manual checkpoint of current state")
    save.add_argument("--session")
    save.add_argument("--note", default="")

    list_cmd = sub.add_parser("list", help="List sessions or turns")
    list_cmd.add_argument("--session")
    list_cmd.add_argument("--all", action="store_true", help="Show all sessions including empty ones")

    show = sub.add_parser("show", help="Show a checkpoint or session metadata")
    show.add_argument("session")
    show.add_argument("turn", type=int, nargs="?", help="Turn number (omit to show session overview)")
    show.add_argument("--metadata-only", action="store_true", help="Show only session metadata, no turn details")

    diff = sub.add_parser("diff", help="Diff current state against a checkpoint")
    diff.add_argument("session")
    diff.add_argument("turn", type=int)

    resume = sub.add_parser("resume", help="Restore a checkpoint")
    resume.add_argument("session")
    resume.add_argument("turn", type=int)
    resume.add_argument("--yes", action="store_true")
    resume.add_argument("--target")

    resume_open = sub.add_parser("resume-open", help="Open a resumed provider session")
    resume_open.add_argument("session")

    clean = sub.add_parser("clean", help="Apply retention or remove empty sessions")
    clean.add_argument("--keep-last", type=int, help="Keep only last N turns per session")
    clean.add_argument("--empty", action="store_true", help="Remove sessions with no captured turns")
    clean.add_argument("--blobs", action="store_true", help="Compact legacy per-session blobs into global storage")
    clean.add_argument("--dry-run", action="store_true", help="Show what would be removed without removing")

    hooks = sub.add_parser("hooks", help="Install or uninstall agent lifecycle hooks")
    hooks.add_argument("action", choices=["install", "uninstall"])
    hooks.add_argument("provider", nargs="?", default="all", help="claude, codex, opencode, or all")

    config = sub.add_parser("config", help="Read/write plugin config")
    config.add_argument("action", choices=["get", "set"])
    config.add_argument("key")
    config.add_argument("value", nargs="?")

    opencode_restore = sub.add_parser("opencode-restore-metadata", help=argparse.SUPPRESS)
    opencode_restore.add_argument("file")
    opencode_restore.add_argument("session")

    args = parser.parse_args(argv)
    return int(_dispatch(args))


def _dispatch(args: argparse.Namespace) -> int:
    if args.command is None:
        return _cmd_checkpoint()
    if args.command == "save":
        coordinator = CheckpointCoordinator(session_id=args.session)
        coordinator.on_session_start()
        manifest = coordinator.on_turn_end(TurnRecord(user_message=args.note, metadata={"source": "cli"}))
        print(f"Saved checkpoint {manifest.session_id} turn {manifest.turn_id}")
        return 0
    if args.command == "list":
        return _cmd_list(args.session, show_all=getattr(args, "all", False))
    if args.command == "show":
        return _cmd_show(args.session, args.turn, metadata_only=getattr(args, "metadata_only", False))
    if args.command == "diff":
        # F13: a terminal/forked session never restarts under its own id, so its
        # last stored turn may trail EOF. Reanchor on read so the diff reflects the
        # full final turn.
        reanchor_last_turn_to_eof(CheckpointStore(sessions_dir() / args.session))
        orchestrator = ResumeOrchestrator()
        try:
            print(orchestrator.plan(args.session, args.turn).render())
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    if args.command == "resume":
        cwd = Path(args.target).expanduser() if args.target else None
        orchestrator = ResumeOrchestrator(cwd=cwd)
        store = CheckpointStore(sessions_dir() / args.session)
        # F2: warn when resuming a turn that an edit-send superseded — the target
        # reconstructs the pre-edit world, which is valid but easy to pick by
        # mistake. Surfaced as a note; the resume still proceeds.
        replaced_by = _edit_send_replaced_turns(store, store.list_manifests()).get(args.turn)
        if replaced_by is not None:
            print(
                _colorize(
                    f"Note: turn {args.turn} was replaced by turn {replaced_by} via edit-send "
                    f"(resuming it restores the pre-edit state).",
                    "yellow",
                )
            )
        try:
            plan = orchestrator.plan(args.session, args.turn)
            confirm = _auto_confirm if args.yes else lambda text: _interactive_resume_confirm(text, plan, store)
            report = orchestrator.execute(plan, confirm)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Restored into new session {report.new_session_id}")
        if report.target_cwd is not None:
            print(f"Workspace: {report.target_cwd}")
        print(f"Backup: {report.backup_dir}")
        print(f"Changed files: {len(report.changed_files)}")
        # Surface the provider session + the command to resume it (P4-6): the
        # report carries this but it was previously never shown to the user.
        if report.provider_session_path is not None:
            print(f"Provider session: {report.provider_session_path}")
        if report.env_state_dir is not None:
            print(f"Env state: {report.env_state_dir}")
        if report.resume_command is not None:
            print(f"Resume with: {_colorize(report.resume_command, 'bold green')}")
        return 0
    if args.command == "resume-open":
        try:
            return execute_resume_open(args.session)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if args.command == "clean":
        if args.empty:
            result = clean_empty_sessions(dry_run=args.dry_run)
            if result["removed"]:
                action = "Would remove" if args.dry_run else "Removed"
                print(f"{action} {len(result['removed'])} empty session(s):")
                for session in result["removed"]:
                    print(f"  - {session}")
            else:
                print("No empty sessions found")
            if result["kept"]:
                print(f"Kept {len(result['kept'])} session(s) with data")
            if result["errors"]:
                print("Errors:")
                for error in result["errors"]:
                    print(f"  - {error}")
            return 0
        elif args.blobs:
            result = compact_legacy_blobs(dry_run=args.dry_run)
            action = "Would compact" if args.dry_run else "Compacted"
            print(
                f"{action} {result['removed']} legacy blob(s); "
                f"promoted {result['promoted']}; missing {result['missing']}"
            )
            return 0
        elif args.keep_last is not None:
            removed = clean_keep_last(args.keep_last)
            print(f"Removed {removed} old manifest(s)")
            return 0
        else:
            print("Error: must specify --empty, --keep-last, or --blobs", file=sys.stderr)
            return 1
    if args.command == "hooks":
        return _cmd_hooks(args.action, args.provider)
    if args.command == "config":
        return _cmd_config(args.action, args.key, args.value)
    if args.command == "opencode-restore-metadata":
        session_messages, todos = restore_opencode_metadata(Path(args.file), args.session)
        if session_messages or todos:
            print(f"Restored OpenCode metadata: {session_messages} session event(s), {todos} todo item(s)")
        return 0
    raise AssertionError(args.command)


def _cmd_checkpoint() -> int:
    action = show_session_browser()
    if action is None:
        return 0
    return _dispatch_browser_action(action)


def _dispatch_browser_action(action: BrowserAction) -> int:
    if action.session_id is None:
        return 0
    if action.command == "show":
        # Browser actions always have turn_id when command is show
        return _cmd_show(action.session_id, action.turn_id)
    if action.command == "diff":
        reanchor_last_turn_to_eof(CheckpointStore(sessions_dir() / action.session_id))
        orchestrator = ResumeOrchestrator()
        try:
            print(orchestrator.plan(action.session_id, action.turn_id).render())
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    if action.command == "resume":
        return _dispatch(
            argparse.Namespace(command="resume", session=action.session_id, turn=action.turn_id, yes=False, target=None)
        )
    return 0


def _is_empty_session(session_dir: Path, metadata: dict[str, Any]) -> bool:
    """Check if a session is empty/dirty and should be hidden by default."""
    try:
        store = CheckpointStore(session_dir)
        manifests = store.list_manifests()

        # No turns at all
        if not manifests:
            return True

        # A session is non-empty if any turn has trajectory records OR a
        # non-trivial user message (opencode passes messages via payload, not a
        # transcript file, so trajectory_ref.record_count stays 0 even for real turns).
        for manifest in manifests:
            traj_ref = manifest.trajectory_ref
            if traj_ref and traj_ref.record_count > 0:
                return False
            if manifest.user_message_preview:
                return False

        return True
    except Exception:
        # On error, show the session to be safe
        return False


def _cmd_list(session: str | None, show_all: bool = False) -> int:
    root = sessions_dir()
    if session is None:
        if not root.exists():
            return 0
        for child in sorted(root.iterdir()):
            if child.is_dir():
                metadata = _read_session_metadata(child)

                # Filter out empty/dirty sessions unless --all is specified
                if not show_all and _is_empty_session(child, metadata):
                    continue

                title = _display_metadata_value(
                    metadata.get("session_title") or resolve_session_title(metadata)
                )
                source = _display_metadata_value(metadata.get("source"))
                marker = _session_marker(metadata, child)
                suffix = f"  {_colorize(marker, 'yellow')}" if marker else ""
                print(f"{child.name}  {title}  {source}{suffix}")
        return 0

    store = CheckpointStore(root / session)
    # F1: a terminal/forked/subagent session whose last turn trailed EOF at capture
    # time (the provider flushed its turn-closing record just after the Stop hook
    # read the file) leaves the STORED manifest short. show/diff/resume already
    # reanchor on read; `list` was the one read path that did not. Recover here too
    # so every read path is consistent. This is timeout-free: by the time anything
    # reads, the transcript is fully flushed (codex writes to the OS page cache, which
    # a co-located reader sees immediately), so the settle timeout is not relied upon.
    reanchor_last_turn_to_eof(store)
    manifests = store.list_manifests()
    replaced = _edit_send_replaced_turns(store, manifests)
    carries_pre_fork = _turns_carrying_pre_fork_rollback(manifests, replaced)
    for manifest in manifests:
        preview = manifest.user_message_preview.replace("\n", " ")
        if manifest.turn_id in replaced:
            marker = _colorize("  [replaced by turn {}]".format(replaced[manifest.turn_id]), "yellow")
        elif manifest.turn_id in carries_pre_fork:
            # ES3: this turn's slice carries a thread_rolled_back whose victim turns
            # live in the uncaptured inherited prefix (a forked resume), so they have
            # no manifest row to mark. Note the inherited relationship instead.
            marker = _colorize("  [carries pre-fork rolled-back turn(s)]", "yellow")
        else:
            marker = ""
        print(f"{manifest.turn_id:04d}  {manifest.created_ts}  {preview}{marker}")
    return 0


def _edit_send_replaced_turns(store: CheckpointStore, manifests: list[Any]) -> dict[int, int]:
    """Map each edit-send-replaced turn_id to the turn_id that replaced it (F2).

    When a user edits an already-sent message and resends ("edit-send"), codex
    records a `thread_rolled_back` event at the head of the replacement turn's
    slice with `num_turns=K`: the K turns immediately preceding it are dead and
    were superseded by this turn. The plugin stores those turns linearly (matching
    native codex), so resuming the replaced turn reconstructs the pre-edit world
    and resuming the replacement reconstructs the post-edit world — both are valid.
    `list` surfaces the relationship so a replaced turn is not mistaken for live.

    Returns {replaced_turn_id: replacing_turn_id}. Empty when no rollback is found
    (the common case) or for non-codex sessions (claude edit-send has no marker).
    """
    replaced: dict[int, int] = {}
    by_turn = {m.turn_id: m for m in manifests}
    for manifest in manifests:
        num_turns = _rolled_back_count(manifest)
        if num_turns <= 0:
            continue
        # The K turns with ids strictly below this one, nearest first, are dead.
        dead = sorted((tid for tid in by_turn if tid < manifest.turn_id), reverse=True)[:num_turns]
        for tid in dead:
            replaced[tid] = manifest.turn_id
    return replaced


def _turns_carrying_pre_fork_rollback(
    manifests: list[Any], replaced: dict[int, int]
) -> set[int]:
    """Turns whose `thread_rolled_back` rolled back MORE turns than are captured (ES3).

    On a forked resume, codex replays a `thread_rolled_back num_turns=K` at the head of
    a captured turn whose K victims were edit-sent away BEFORE the fork — so they live
    in the uncaptured inherited prefix, not in any manifest row. `_edit_send_replaced_turns`
    can only mark victims that exist as captured turns (ids below the marker turn); when
    K exceeds that count, the surplus victims are invisible. We flag the carrier turn so
    `list` can note the inherited relationship rather than silently dropping it.

    Returns the set of carrier turn_ids whose rollback reaches into the inherited prefix.
    Empty for the common case (a fully-captured in-session edit-send, already covered by
    `replaced`).
    """
    carriers: set[int] = set()
    ids = sorted(m.turn_id for m in manifests)
    for manifest in manifests:
        num_turns = _rolled_back_count(manifest)
        if num_turns <= 0:
            continue
        captured_below = sum(1 for tid in ids if tid < manifest.turn_id)
        if num_turns > captured_below:
            carriers.add(manifest.turn_id)
    return carriers


def _rolled_back_count(manifest: Any) -> int:
    """`num_turns` from a `thread_rolled_back` event inside this turn's slice (F2)."""
    ref = manifest.trajectory_ref
    if ref is None or ref.provider != "codex" or not ref.transcript_path:
        return 0
    path = Path(ref.transcript_path).expanduser()
    try:
        with path.open("rb") as handle:
            handle.seek(ref.start_offset)
            data = handle.read(max(0, ref.end_offset - ref.start_offset))
    except OSError:
        return 0
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = record.get("payload") if isinstance(record, dict) else None
        if isinstance(payload, dict) and payload.get("type") == "thread_rolled_back":
            count = payload.get("num_turns")
            return count if isinstance(count, int) and count > 0 else 0
    return 0


def _read_session_metadata(session_dir: Path) -> dict[str, Any]:
    return read_metadata_json(session_dir / "metadata.json")


def _display_metadata_value(value: Any) -> str:
    return str(value) if value not in (None, "") else "-"


def _session_marker(metadata: dict[str, Any], session_path: Path) -> str:
    """Return a short marker for sessions with no meaningful content (P11-ZOMBIE-2).

    Only marks subagent sessions that have capture_status=no_sidechain_file.
    Regular sessions without manifests are normal (session_start fired, no turn yet).
    """
    lineage = metadata.get("lineage") or {}
    if lineage.get("capture_status") == "no_sidechain_file":
        return "[no capture]"
    return ""


def _cmd_show(session: str, turn: int | None, metadata_only: bool = False) -> int:
    session_dir = sessions_dir() / session
    if not session_dir.exists():
        print(f"Session not found: {session}", file=sys.stderr)
        return 1

    # Always show session metadata first
    metadata = _read_session_metadata(session_dir)
    print(_colorize("Session Metadata:", "bold"))
    print(json.dumps(metadata, indent=2, sort_keys=True))
    print()

    store = CheckpointStore(session_dir)
    # F13: reanchor the last turn to EOF on read so `show` of a terminal/forked
    # session reports the full final turn rather than the capture-time under-count.
    reanchor_last_turn_to_eof(store)
    manifests = store.list_manifests()

    # Show turn summary
    print(_colorize("Turns:", "bold"))
    if not manifests:
        print("  (no turns captured)")
    else:
        replaced = _edit_send_replaced_turns(store, manifests)
        carries_pre_fork = _turns_carrying_pre_fork_rollback(manifests, replaced)
        for manifest in manifests:
            preview = manifest.user_message_preview.replace("\n", " ")[:80]
            marker = ""
            if manifest.turn_id in replaced:
                marker = _colorize(" [replaced by turn {}]".format(replaced[manifest.turn_id]), "yellow")
            elif manifest.turn_id in carries_pre_fork:
                marker = _colorize(" [carries pre-fork rolled-back turn(s)]", "yellow")
            print(f"  {manifest.turn_id:04d}  {manifest.created_ts}  {preview}{marker}")
    print()

    # If no specific turn requested or metadata-only, stop here
    if turn is None or metadata_only:
        if not manifests:
            marker = _session_marker(metadata, session_dir)
            if marker:
                print(_colorize(f"Note: {marker}", "yellow"))
        return 0

    # Show specific turn details
    try:
        manifest = store.read_manifest(turn)
    except (FileNotFoundError, KeyError):
        print(f"Turn {turn} not found in session {session}", file=sys.stderr)
        return 1

    env = environment_from_blob(manifest.env_ref, store)
    fs = filesystem_from_blob(manifest.fs_ref, store)

    print(_colorize(f"Turn {turn} Details:", "bold"))
    print(json.dumps(manifest.to_json(), indent=2, sort_keys=True))
    print()
    print(_colorize("Environment:", "bold"))
    print(json.dumps(env.to_json(), indent=2, sort_keys=True))
    print()
    print(_colorize("Filesystem:", "bold"))
    print(f"{len(fs.files)} files at {fs.cwd}")
    return 0


def _cmd_config(action: str, key: str, value: str | None) -> int:
    config = load_config()
    if action == "get":
        if key == ".":
            print(json.dumps(config, indent=2, sort_keys=True))
        else:
            print(json.dumps(_get_nested(config, key), indent=2, sort_keys=True))
        return 0
    if value is None:
        print("checkpoint config set requires VALUE", file=sys.stderr)
        return 2
    _set_nested(config, key, _parse_value(value))
    write_config(config)
    print(f"Updated {config_path()}")
    return 0


def _cmd_hooks(action: str, provider: str) -> int:
    try:
        results = install_hooks(provider) if action == "install" else uninstall_hooks(provider)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for result in results:
        state = "updated" if result.changed else "already current"
        print(f"{result.provider}: {state} {result.path}")
    return 0


def _interactive_resume_confirm(text: str, plan: ResumePlan, store: CheckpointStore) -> bool | ResumeOptions:
    print(text)
    while True:
        answer = input("Proceed? [y/N/d] ")
        if answer.lower() in {"y", "yes"}:
            return _interactive_resume_options(plan)
        if answer.lower() in {"d", "diff"}:
            show_diff_viewer(plan, store)
            continue
        return False


def _interactive_resume_options(plan: ResumePlan) -> ResumeOptions:
    answer = input("Restore where? [i]n-place/[c]opy (default: in-place) ")
    if answer.lower() not in {"c", "copy"}:
        return ResumeOptions(proceed=True)
    default_path = _default_copy_path(Path(plan.target_fs.cwd), plan.turn_id)
    raw_path = input(f"Copy folder (Enter for default, or type an absolute path) [{default_path}]: ").strip()
    if not raw_path:
        return ResumeOptions(proceed=True, target_cwd=default_path)
    target_cwd = Path(raw_path).expanduser()
    if not target_cwd.is_absolute():
        raise RuntimeError(f"Copy folder must be an absolute path: {raw_path}")
    return ResumeOptions(proceed=True, target_cwd=target_cwd)


def _default_copy_path(cwd: Path, turn_id: int) -> Path:
    suffix = f"checkpoint-copy-{turn_id}-{uuid.uuid4().hex[:6]}"
    return cwd.parent / f"{cwd.name}-{suffix}"


def _auto_confirm(text: str) -> bool:
    print(text)
    return True


def _get_nested(data: dict[str, Any], key: str) -> Any:
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _set_nested(data: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    current = data
    for part in parts[:-1]:
        next_value = current.setdefault(part, {})
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def _parse_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


if __name__ == "__main__":
    raise SystemExit(main())
