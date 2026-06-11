"""Diff-first checkpoint resume orchestration."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from ._utils import extract_sha_refs, is_sha_ref, read_metadata_json
from .env.collector import claude_mcp_statuses_from_trajectory, collect_environment, environment_from_blob
from .env.differ import diff_environments, render_diff
from .env.providers import (
    RESUME_RUNTIME_ARGS,
    RESUME_SESSION_ID,
    ProviderLayout,
    ProviderResumePolicy,
    layout_for_provider,
    resume_policy_for_provider,
)
from .env.restorer import restore_environment
from .env.hook_filter import is_hook_config_basename, merge_plugin_hooks
from .fs.ignore import IgnoreMatcher
from .fs.restorer import diff_filesystems, render_fs_diff, restore_cwd
from .fs.snapshot import filesystem_from_blob, snapshot_cwd
from .integrations._trajectory_slicer import claude_key, codex_key, jsonl_count_records, recover_trailing_tail
from .path_utils import mirror_path, path_within, rewrite_path_references_text
from .paths import backups_dir, ensure_home, load_config, session_dir
from .store import CheckpointStore, canonical_json
from .types import CheckpointManifest, ResumePlan, ResumeReport, TrajectoryReference


@dataclass(frozen=True)
class TrajectoryPrefix:
    data: bytes
    spans: dict[int, tuple[int, int, int]]
    # P6-2: provider per-turn key (codex turn_id / claude promptId) for each
    # manifest turn_id, so realign can re-tile the rewritten file by matching
    # record keys instead of trusting pre-rewrite record counts.
    turn_keys: dict[int, object] = field(default_factory=dict)
    # P7-5: number of leading inherited (pre-fork) records prepended before the
    # first captured turn. This is the inherited/captured boundary; deriving it by
    # scanning for the first promptId-bearing user record is wrong once the
    # inherited prefix itself contains forked turns (resume-of-resume), where
    # record 0 already carries a promptId.
    inherited_record_count: int = 0


@dataclass(frozen=True)
class ResumeOptions:
    proceed: bool
    target_cwd: Path | None = None


@dataclass(frozen=True)
class ResumeRuntime:
    root: Path
    provider: ProviderLayout
    env: dict[str, str]
    path_map: dict[str, str]


@dataclass(frozen=True)
class ResumeOpenSpec:
    provider: str
    session_id: str
    cwd: Path
    env: dict[str, str]
    preflight: list[list[str]]
    command: list[str]


class ResumeOrchestrator:
    def __init__(self, plugin_home: Path | None = None, cwd: Path | None = None) -> None:
        self.home = ensure_home(plugin_home)
        self.cwd = Path(cwd).expanduser().resolve() if cwd is not None else None

    def plan(self, session_id: str, turn_id: int) -> ResumePlan:
        store = CheckpointStore(session_dir(session_id, self.home))
        _refuse_subagent_resume(store, self.home)
        manifest = store.read_manifest(turn_id)
        target_env = environment_from_blob(manifest.env_ref, store)
        target_env = _target_env_with_provider_runtime(manifest, target_env, store)
        target_fs = filesystem_from_blob(manifest.fs_ref, store)
        cwd = self.cwd or Path(target_fs.cwd).expanduser().resolve()
        self.cwd = cwd
        provider = layout_for_provider(target_env.provider)
        current_env = collect_environment(cwd, provider, store)
        config = load_config(self.home)
        ignore = IgnoreMatcher(cwd, config.get("exclude_patterns") or [])
        current_fs = snapshot_cwd(cwd, store, ignore)
        ignore_plugin_hooks = bool(config.get("ignore_plugin_hook_diffs", True))
        env_diff = diff_environments(
            current_env,
            target_env,
            blob_loader=store.load_blob,
            ignore_plugin_hooks=ignore_plugin_hooks,
        )
        fs_diff = diff_filesystems(current_fs, target_fs)
        return ResumePlan(
            session_id=session_id,
            turn_id=turn_id,
            target_manifest=manifest,
            current_env=current_env,
            target_env=target_env,
            current_fs=current_fs,
            target_fs=target_fs,
            env_diff_text=render_diff(env_diff, current_env, target_env),
            fs_diff_text=render_fs_diff(fs_diff, target_fs.cwd),
            ignore_plugin_hooks=ignore_plugin_hooks,
        )

    def execute(self, plan: ResumePlan, confirm: Callable[[str], bool | ResumeOptions]) -> ResumeReport:
        rendered = plan.render()
        options = _coerce_resume_options(confirm(rendered))
        if not options.proceed:
            raise RuntimeError("Resume cancelled")
        original_store = CheckpointStore(session_dir(plan.session_id, self.home))
        backup_root = backups_dir(self.home) / f"{_stamp()}-{plan.session_id}-{uuid.uuid4().hex[:8]}"
        target_cwd = _prepare_resume_cwd(self.cwd, options.target_cwd)
        self.cwd = target_cwd
        source_provider = layout_for_provider(plan.target_env.provider)
        new_session_id = _new_resume_session_id(source_provider.name)
        runtime = _prepare_resume_runtime(source_provider, self.home, new_session_id, plan.target_env)
        runtime_env = _environment_for_runtime(plan.target_env, runtime.path_map, target_cwd)
        runtime = replace(
            runtime,
            env=_runtime_process_env(
                runtime.provider.name,
                runtime.provider.home,
                runtime.root,
                runtime_env,
                runtime.path_map,
            ),
        )
        env_report = restore_environment(
            runtime_env,
            runtime.provider,
            original_store,
            backup_root / "environment",
            ignore_plugin_hooks=plan.ignore_plugin_hooks,
            preserve_redacted_values=True,
        )
        if plan.ignore_plugin_hooks:
            _sync_runtime_plugin_hooks(source_provider, runtime.provider)
        _materialize_runtime_config(runtime.provider.name, runtime.provider.home, runtime_env)
        config = load_config(self.home)
        ignore = IgnoreMatcher(target_cwd, config.get("exclude_patterns") or [])
        fs_report = restore_cwd(
            plan.target_fs,
            target_cwd,
            original_store,
            backup_root / "filesystem",
            ignore,
        )
        if not target_cwd.is_dir():
            raise RuntimeError(f"Resume workspace was not created: {target_cwd}")
        trajectory = _trajectory_prefix(original_store, plan)
        source_meta = _codex_source_session_meta(plan) if runtime.provider.name == "codex" else None
        # P6-14: an inherited fork prefix is present when the earliest captured turn
        # anchors past byte 0 (records before it are pre-fork inherited history).
        has_inherited_prefix = _has_inherited_prefix(trajectory.spans, trajectory.data)
        provider_session_path = _write_provider_session(
            runtime.provider.name,
            runtime.provider.home,
            target_cwd,
            new_session_id,
            trajectory.data,
            runtime_env.model,
            runtime_env.effort,
            runtime_env.permission_mode,
            runtime_env.mode,
            source_meta,
            has_inherited_prefix,
            plan.session_id,
            trajectory.inherited_record_count,
        )
        _carry_provider_session_state(
            runtime.provider.name,
            source_provider.home,
            plan.session_id,
            new_session_id,
            target_cwd,
            dest_provider_home=runtime.provider.home,
        )
        if runtime.provider.name == "codex" and provider_session_path is not None:
            _append_codex_session_index(
                runtime.provider.home,
                new_session_id,
                _source_session_title(original_store) or _derive_session_title(original_store, plan),
            )
        resume_open = _resume_open_spec(
            runtime.provider.name,
            new_session_id,
            target_cwd,
            provider_session_path,
            runtime_env,
            runtime.env,
        )
        self._copy_session_prefix(
            original_store,
            plan,
            new_session_id,
            provider_session_path,
            trajectory,
            target_cwd,
            runtime,
            resume_open,
        )
        return ResumeReport(
            new_session_id=new_session_id,
            backup_dir=str(backup_root),
            env=env_report,
            fs=fs_report,
            provider_session_path=str(provider_session_path) if provider_session_path is not None else None,
            target_cwd=str(target_cwd),
            env_state_dir=str(runtime.root),
            resume_command=_resume_command(runtime.provider.name, new_session_id) if resume_open is not None else None,
        )

    def _copy_session_prefix(
        self,
        store: CheckpointStore,
        plan: ResumePlan,
        new_session_id: str,
        provider_session_path: Path | None,
        trajectory: TrajectoryPrefix,
        cwd: Path,
        runtime: ResumeRuntime,
        resume_open: ResumeOpenSpec | None,
    ) -> None:
        target_dir = session_dir(new_session_id, self.home)
        target_store = CheckpointStore(target_dir)
        _write_resumed_metadata(store, target_store, plan, new_session_id, provider_session_path, cwd, runtime)
        if resume_open is not None:
            _write_resume_open_spec(target_store, resume_open, runtime)
        # P4-3/P6-2: realign spans to the REWRITTEN provider file so resumed
        # manifests' byte offsets match the file their trajectory_ref points at
        # (otherwise a resume-of-a-resume reads stale raw-concat offsets and drops
        # records). Re-tile by per-turn provider key, not pre-rewrite counts.
        included = [m for m in store.list_manifests() if m.turn_id <= plan.turn_id]
        provider_name = _manifests_provider_name(included)
        manifest_session_path = provider_session_path
        realign_session_path = provider_session_path
        if provider_name == "opencode" and trajectory.data:
            # OpenCode needs a JSON import file for the external resume command, but
            # checkpoint manifests must keep referencing the plugin's JSONL timeline.
            manifest_session_path = target_store.trajectory_path
            realign_session_path = None
        realigned = replace(
            trajectory,
            spans=_realign_spans_to_provider_file(
                realign_session_path,
                trajectory.spans,
                provider_name=provider_name,
                turn_keys=trajectory.turn_keys,
            ),
        )
        for manifest in store.list_manifests():
            if manifest.turn_id <= plan.turn_id:
                _promote_manifest_blobs(store, manifest)
                target_store.write_manifest(
                    _resumed_manifest(manifest, new_session_id, manifest_session_path, realigned, target_store, cwd)
                )
        if trajectory.data:
            target_store._atomic_write(target_store.trajectory_path, trajectory.data)


def _promote_manifest_blobs(store: CheckpointStore, manifest: CheckpointManifest) -> None:
    for sha in _manifest_blob_refs(store, manifest):
        store.promote_legacy_blob(sha)


def _manifest_blob_refs(store: CheckpointStore, manifest: CheckpointManifest) -> set[str]:
    refs = {ref for ref in (manifest.env_ref, manifest.fs_ref) if is_sha_ref(ref)}
    metadata = _read_session_metadata(store)
    fork_point_ref = metadata.get("fork_point_trajectory_ref")
    if is_sha_ref(fork_point_ref):
        refs.add(fork_point_ref)
    for root_ref in (manifest.env_ref, manifest.fs_ref):
        if not is_sha_ref(root_ref):
            continue
        try:
            data = store.load_json_blob(root_ref)
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        refs.update(extract_sha_refs(data))
    return refs


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _refuse_subagent_resume(store: CheckpointStore, home: Path) -> None:
    """Refuse to resume a subagent checkpoint standalone, redirect to parent (H2).

    A subagent is never a real entry point: it is spawned by a parent `Task`
    tool_use, its runtime context is the Task prompt plus an agent-type system
    prompt that is not in the transcript, and the parent's context is absent.
    Synthesizing a standalone top-level session would fabricate a session the
    provider never produced and diverge immediately. Instead we point the user at
    the parent turn that spawned this subagent, where the subagent context is
    carried (H3) and the Task result already lives in the parent thread.
    """
    metadata = _read_session_metadata(store)
    lineage = metadata.get("lineage")
    if not isinstance(lineage, dict):
        return
    parent_session_id = lineage.get("parent_session_id")
    if not isinstance(parent_session_id, str) or not parent_session_id:
        return
    agent_id = lineage.get("agent_id") if isinstance(lineage.get("agent_id"), str) else None
    turn_id = _parent_turn_for_subagent(home, parent_session_id, agent_id, metadata)
    target = f"{parent_session_id} {turn_id}" if turn_id is not None else parent_session_id
    raise RuntimeError(
        "Cannot resume a subagent standalone; a subagent has no faithful "
        "standalone session. Resume its parent instead: "
        f"checkpoint resume {target}"
    )


def _read_session_metadata(store: CheckpointStore) -> dict[str, object]:
    return read_metadata_json(store.session_dir / "metadata.json")


def _parent_turn_for_subagent(
    home: Path,
    parent_session_id: str,
    agent_id: str | None,
    subagent_metadata: dict[str, object],
) -> int | None:
    """Best-effort parent turn that spawned this subagent (for the redirect).

    Prefer the parent turn whose trajectory slice references the subagent's
    agent_id (the `Task` tool_use that launched it). P6-6: when the agent_id is in
    no slice (e.g. the fork-parent edge), fall back to the EARLIEST turn that ended
    at or after the subagent started — that is the turn during which the subagent
    ran. The old "latest turn created <= start_ts" picked the turn that ended
    BEFORE the subagent started (off-by-one), redirecting to the wrong turn.
    """
    try:
        parent_store = CheckpointStore(session_dir(parent_session_id, home))
        manifests = parent_store.list_manifests()
    except OSError:
        return None
    if not manifests:
        return None
    if agent_id:
        for manifest in manifests:
            if _manifest_references_agent(manifest, agent_id):
                return manifest.turn_id
    start_ts = subagent_metadata.get("start_ts")
    if isinstance(start_ts, str):
        # The spawning turn is the earliest turn that had not yet finished when the
        # subagent started, i.e. the earliest turn with created_ts >= start_ts.
        running = [m for m in manifests if m.created_ts >= start_ts]
        if running:
            return min(running, key=lambda m: m.turn_id).turn_id
    return max(manifests, key=lambda m: m.turn_id).turn_id


def _manifest_references_agent(manifest: CheckpointManifest, agent_id: str) -> bool:
    ref = manifest.trajectory_ref
    if ref is None or not ref.transcript_path:
        return False
    path = Path(ref.transcript_path).expanduser()
    try:
        with path.open("rb") as handle:
            handle.seek(ref.start_offset)
            data = handle.read(max(0, ref.end_offset - ref.start_offset))
    except OSError:
        return False
    return agent_id.encode("utf-8") in data


def _new_resume_session_id(provider_name: str | None = None) -> str:
    # B1: native codex session ids are uuidv7 (time-ordered, version nibble 7), so the
    # rollout filename, `id`, and session_index entry sort chronologically in the
    # picker. uuid4 (version nibble 4) sorts randomly and is a byte-distinguishable
    # fingerprint. Native CLAUDE ids are uuid4 (verified), so only codex needs v7.
    # OpenCode uses `ses_` + hex-timestamp + random base62.
    if provider_name == "codex":
        return _uuid7()
    if provider_name == "opencode":
        return f"ses_{uuid.uuid4().hex[:26]}"
    return str(uuid.uuid4())


def _uuid7() -> str:
    """Generate a UUID version 7 (RFC 9562): 48-bit unix-ms timestamp + random.

    Python's stdlib gained `uuid.uuid7()` in 3.14; this back-fills it so resumed
    codex ids match native codex's time-ordered format on older interpreters.
    """
    if hasattr(uuid, "uuid7"):
        return str(uuid.uuid7())  # type: ignore[attr-defined]
    unix_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (unix_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version 7
    value |= rand_a << 64
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand_b
    return str(uuid.UUID(int=value))


def _coerce_resume_options(value: bool | ResumeOptions) -> ResumeOptions:
    if isinstance(value, ResumeOptions):
        return value
    return ResumeOptions(proceed=bool(value))


def _prepare_resume_cwd(current_cwd: Path | None, target_cwd: Path | None) -> Path:
    if current_cwd is None:
        raise RuntimeError("Resume cwd is not initialized")
    current_cwd = current_cwd.expanduser().resolve()
    if target_cwd is None:
        return current_cwd
    target_cwd = target_cwd.expanduser().resolve()
    if target_cwd == current_cwd:
        return current_cwd
    if target_cwd.exists():
        if any(target_cwd.iterdir()):
            raise RuntimeError(f"Target folder is not empty: {target_cwd}")
    else:
        target_cwd.parent.mkdir(parents=True, exist_ok=True)
    def _ignore_special_files(directory: str, entries: list[str]) -> set[str]:
        ignored = set()
        for entry in entries:
            path = Path(directory) / entry
            if path.is_socket() or path.is_fifo() or path.is_block_device() or path.is_char_device():
                ignored.add(entry)
        return ignored

    shutil.copytree(current_cwd, target_cwd, dirs_exist_ok=True, ignore=_ignore_special_files)
    return target_cwd


def _prepare_resume_runtime(
    source_provider: ProviderLayout,
    home: Path,
    new_session_id: str,
    target_env: object,
) -> ResumeRuntime:
    root = home / "env-state" / new_session_id
    runtime_home = root / source_provider.name
    path_map = _runtime_path_map(
        source_provider,
        root,
        runtime_home,
        captured_provider_home=_captured_provider_home(target_env),
        target_env=target_env,
    )
    runtime_provider = _provider_layout_with_path_map(source_provider, path_map)
    runtime_provider.home.mkdir(parents=True, exist_ok=True)
    _copy_runtime_seed_files(source_provider, runtime_provider, path_map)
    _link_runtime_secret_files(source_provider.name, source_provider.home, runtime_provider.home)
    env = _runtime_process_env(source_provider.name, runtime_provider.home, root, target_env, path_map)
    return ResumeRuntime(root=root, provider=runtime_provider, env=env, path_map=path_map)


def _runtime_path_map(
    source_provider: ProviderLayout,
    root: Path,
    runtime_home: Path,
    *,
    captured_provider_home: Path | None = None,
    target_env: object | None = None,
) -> dict[str, str]:
    path_map = {str(source_provider.home): str(runtime_home)}
    if captured_provider_home is not None and captured_provider_home.is_absolute():
        path_map[str(captured_provider_home)] = str(runtime_home)
    external_root = root / "external"
    for path in _provider_layout_paths(source_provider):
        if path is None or not path.is_absolute():
            continue
        if _mapped_path(path, path_map) != path:
            continue
        path_map[str(path.parent)] = str(external_root / mirror_path(path.parent))
    for path in _captured_plugin_file_roots(target_env):
        if not path.is_absolute():
            continue
        if _mapped_path(path, path_map) != path:
            continue
        path_map[str(path)] = str(external_root / mirror_path(path))
    return _validated_path_map(path_map, root)


def _captured_provider_home(env: object) -> Path | None:
    extra = getattr(env, "extra", None)
    if not isinstance(extra, dict):
        return None
    provider_home = _string_value(extra.get("provider_home"))
    if not provider_home:
        return None
    return Path(provider_home).expanduser()


def _captured_plugin_file_roots(env: object) -> list[Path]:
    extra = getattr(env, "extra", None)
    if not isinstance(extra, dict):
        return []
    raw = extra.get("plugin_file_roots")
    if not isinstance(raw, dict):
        return []
    roots: list[Path] = []
    for value in raw.values():
        if isinstance(value, str) and value:
            roots.append(Path(value).expanduser())
    return roots


def _validated_path_map(path_map: dict[str, str], root: Path) -> dict[str, str]:
    validated: dict[str, str] = {}
    for source, dest in path_map.items():
        source_path = Path(source)
        dest_path = Path(dest)
        if not source_path.is_absolute() or not dest_path.is_absolute():
            continue
        if not path_within(dest_path, root):
            continue
        validated[str(source_path)] = str(dest_path)
    return dict(sorted(validated.items(), key=lambda item: len(item[0]), reverse=True))


def _provider_layout_paths(provider: ProviderLayout) -> list[Path | None]:
    paths: list[Path | None] = [
        provider.home,
        provider.memory_dir,
        provider.mcp_config,
        *provider.mcp_config_files,
        *provider.settings_files,
        *provider.skills_dirs.values(),
    ]
    for item in provider.project_files:
        path = Path(item)
        if path.is_absolute():
            paths.append(path)
    return paths


def _provider_layout_with_path_map(provider: ProviderLayout, path_map: dict[str, str]) -> ProviderLayout:
    return replace(
        provider,
        home=_mapped_path(provider.home, path_map),
        memory_dir=_mapped_optional_path(provider.memory_dir, path_map),
        mcp_config=_mapped_optional_path(provider.mcp_config, path_map),
        mcp_config_files=[_mapped_path(path, path_map) for path in provider.mcp_config_files],
        settings_files=[_mapped_path(path, path_map) for path in provider.settings_files],
        skills_dirs={name: _mapped_path(path, path_map) for name, path in provider.skills_dirs.items()},
        project_files=[_mapped_project_file(item, path_map) for item in provider.project_files],
    )


def _mapped_optional_path(path: Path | None, path_map: dict[str, str]) -> Path | None:
    return _mapped_path(path, path_map) if path is not None else None


def _mapped_project_file(value: str, path_map: dict[str, str]) -> str:
    path = Path(value)
    if not path.is_absolute():
        return value
    mapped = str(_mapped_path(path, path_map))
    if value.endswith(("/", os.sep)) and not mapped.endswith(os.sep):
        return mapped + os.sep
    return mapped


def _mapped_path(path: Path, path_map: dict[str, str]) -> Path:
    if not path.is_absolute():
        return path
    text = str(path)
    for source, dest in path_map.items():
        if text != source and not text.startswith(source + os.sep):
            continue
        dest_path = Path(dest)
        if not dest_path.is_absolute():
            continue
        if text == source:
            return dest_path
        mapped = dest_path / text[len(source) + 1 :]
        if path_within(mapped, dest_path):
            return mapped
    return path


def _environment_for_runtime(env: object, path_map: dict[str, str], target_cwd: Path) -> object:
    extra = dict(getattr(env, "extra", {}) or {})
    source_cwd = _string_value(extra.get("cwd"))
    runtime_map = dict(path_map)
    if source_cwd:
        runtime_map[str(Path(source_cwd).expanduser())] = str(target_cwd)
        runtime_map = dict(sorted(runtime_map.items(), key=lambda item: len(item[0]), reverse=True))

    settings = _rewrite_path_keyed_map(getattr(env, "settings", {}) or {}, runtime_map)
    project_context = _rewrite_path_keyed_map(getattr(env, "project_context", {}) or {}, runtime_map)
    provider_home = extra.get("provider_home")
    if provider_home:
        extra["provider_home"] = str(_mapped_path(Path(str(provider_home)), path_map))
    else:
        extra["provider_home"] = provider_home

    plugin_file_roots = extra.get("plugin_file_roots")
    if isinstance(plugin_file_roots, dict):
        rewritten_roots: dict[str, str] = {}
        for name, root in plugin_file_roots.items():
            if not isinstance(name, str) or not isinstance(root, str) or not root:
                continue
            rewritten_roots[name] = str(_mapped_path(Path(root).expanduser(), runtime_map))
        extra["plugin_file_roots"] = rewritten_roots

    extra["cwd"] = str(target_cwd)
    extra["runtime_path_map"] = runtime_map

    runtime_env = extra.get("opencode_runtime_env")
    if isinstance(runtime_env, dict):
        extra["opencode_runtime_env"] = _rewrite_opencode_runtime_env(runtime_env, runtime_map)

    config_content = extra.get("opencode_config_content")
    if isinstance(config_content, str) and config_content:
        extra["opencode_config_content"] = rewrite_path_references_text(config_content, runtime_map)

    config_roots = extra.get("opencode_config_skill_roots")
    if isinstance(config_roots, list):
        extra["opencode_config_skill_roots"] = [
            str(_mapped_path(Path(root).expanduser(), runtime_map)) if isinstance(root, str) else root
            for root in config_roots
        ]

    return replace(env, settings=settings, project_context=project_context, extra=extra)


def _rewrite_path_keyed_map(values: dict[str, str], path_map: dict[str, str]) -> dict[str, str]:
    rewritten: dict[str, str] = {}
    for key, value in values.items():
        path = Path(key)
        new_key = str(_mapped_path(path.expanduser(), path_map)) if path.is_absolute() else key
        rewritten[new_key] = value
    return rewritten


def _rewrite_opencode_runtime_env(values: dict[object, object], path_map: dict[str, str]) -> dict[str, str]:
    path_keys = {"OPENCODE_CONFIG", "OPENCODE_CONFIG_DIR", "OPENCODE_TUI_CONFIG", "OPENCODE_DATA_DIR"}
    rewritten: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not _safe_env_name(key) or key == "OPENCODE_CONFIG_CONTENT":
            continue
        text = str(value)
        if key in path_keys:
            path = Path(text).expanduser()
            if path.is_absolute():
                text = str(_mapped_path(path, path_map))
        rewritten[key] = text
    return rewritten


def _resume_policy(provider: str, path: Path) -> ProviderResumePolicy:
    policy = resume_policy_for_provider(provider)
    if policy is None:
        raise RuntimeError(f"Invalid resume-open state field 'provider': {path}")
    return policy


def _build_resume_command_from_policy(
    policy: ProviderResumePolicy,
    session_id: str,
    target_env: object | None,
) -> list[str]:
    runtime_args = _provider_runtime_args(policy, target_env)
    command: list[str] = []
    for token in policy.command_template:
        if token == RESUME_SESSION_ID:
            command.append(session_id)
        elif token == RESUME_RUNTIME_ARGS:
            command.extend(runtime_args)
        else:
            command.append(token)
    return command


def _validate_resume_command_from_policy(
    policy: ProviderResumePolicy,
    session_id: str,
    command: list[str],
    path: Path,
) -> None:
    try:
        runtime_index = policy.command_template.index(RESUME_RUNTIME_ARGS)
    except ValueError:
        expected = _substitute_resume_command_template(policy.command_template, session_id)
        if command != expected:
            raise RuntimeError(f"Invalid resume-open state field 'command': {path}")
        return

    prefix = _substitute_resume_command_template(policy.command_template[:runtime_index], session_id)
    suffix = _substitute_resume_command_template(policy.command_template[runtime_index + 1 :], session_id)
    if command[: len(prefix)] != prefix:
        raise RuntimeError(f"Invalid resume-open state field 'command': {path}")
    if suffix and command[-len(suffix) :] != suffix:
        raise RuntimeError(f"Invalid resume-open state field 'command': {path}")
    runtime_end = len(command) - len(suffix) if suffix else len(command)
    if runtime_end < len(prefix):
        raise RuntimeError(f"Invalid resume-open state field 'command': {path}")
    _validate_provider_runtime_args(command[len(prefix) : runtime_end], policy, path)


def _substitute_resume_command_template(template: tuple[str, ...], session_id: str) -> list[str]:
    values: list[str] = []
    for token in template:
        if token == RESUME_SESSION_ID:
            values.append(session_id)
        elif token == RESUME_RUNTIME_ARGS:
            raise ValueError("runtime args placeholder must be handled by caller")
        else:
            values.append(token)
    return values


def _validate_provider_runtime_args(args: list[str], policy: ProviderResumePolicy, path: Path) -> None:
    value_options = {option for _field, option in policy.runtime_arg_fields}
    json_config_options: dict[str, set[str]] = {}
    for _field, option, config_key in policy.runtime_json_config_arg_fields:
        json_config_options.setdefault(option, set()).add(config_key)

    index = 0
    while index < len(args):
        option = args[index]
        if option in value_options and index + 1 < len(args) and not args[index + 1].startswith("-"):
            index += 2
            continue
        if option in json_config_options and index + 1 < len(args):
            if _valid_json_config_assignment(args[index + 1], json_config_options[option]):
                index += 2
                continue
        raise RuntimeError(f"Invalid resume-open state field 'command': {path}")


def _valid_json_config_assignment(value: str, allowed_keys: set[str]) -> bool:
    key, separator, raw = value.partition("=")
    if separator != "=" or key not in allowed_keys:
        return False
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, str) and bool(parsed)


def _runtime_policy_extra_env(
    policy: ProviderResumePolicy,
    target_env: object,
    path_map: dict[str, str],
) -> dict[str, str]:
    if policy.runtime_env_extra != "opencode_runtime_env":
        return {}
    env: dict[str, str] = {}
    for key, value in _opencode_runtime_env(target_env).items():
        if key in policy.runtime_env_skip_keys:
            continue
        if key in policy.path_env_keys:
            path = Path(value).expanduser()
            if path.is_absolute():
                value = str(_mapped_path(path, path_map))
        env[key] = value
    return env


def _runtime_process_env(
    provider_name: str,
    runtime_home: Path,
    root: Path,
    target_env: object,
    path_map: dict[str, str],
) -> dict[str, str]:
    policy = resume_policy_for_provider(provider_name)
    if policy is None:
        return {}
    env = {key: str(runtime_home) for key in policy.home_env_keys}
    if policy.data_dir_env_key is not None and policy.data_dir_name is not None:
        env[policy.data_dir_env_key] = str(root / policy.data_dir_name)
    env.update(_runtime_policy_extra_env(policy, target_env, path_map))
    return env


def _materialize_runtime_config(provider_name: str, runtime_home: Path, target_env: object) -> None:
    if provider_name == "claude":
        _materialize_claude_runtime_config(runtime_home, target_env)
        return
    if provider_name != "opencode":
        return
    content = _opencode_config_content(target_env, keep_redacted=True)
    if not content:
        return
    target = runtime_home / "opencode.json"
    wanted = json.loads(content)
    current = _load_json_value(target)
    if current is not None:
        wanted = _preserve_redacted_config_values(current, wanted)
    cleaned = _without_redacted_values(wanted)
    if cleaned is _REDACTED_CONFIG_VALUE:
        return
    wanted = cleaned
    runtime_home.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(wanted, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _materialize_claude_runtime_config(runtime_home: Path, target_env: object) -> None:
    if not _has_mcp_server_statuses(target_env):
        return
    inactive = _inactive_mcp_server_names(target_env)
    cwd = _target_env_cwd(target_env)
    if cwd is None:
        return
    target = runtime_home / ".claude.json"
    current = _json_object(_load_json_value(target))
    projects = _json_object(current.get("projects"))
    project = _json_object(projects.get(str(cwd)))
    project["disabledMcpServers"] = inactive
    projects[str(cwd)] = project
    current["projects"] = projects
    runtime_home.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _has_mcp_server_statuses(target_env: object) -> bool:
    mcp_servers = getattr(target_env, "mcp_servers", None)
    return isinstance(mcp_servers, dict) and bool(mcp_servers)


def _inactive_mcp_server_names(target_env: object) -> list[str]:
    mcp_servers = getattr(target_env, "mcp_servers", None)
    if not isinstance(mcp_servers, dict):
        return []
    return sorted(
        name
        for name, status in mcp_servers.items()
        if isinstance(name, str) and isinstance(status, str) and status == "inactive"
    )


def _target_env_cwd(target_env: object) -> Path | None:
    extra = getattr(target_env, "extra", None)
    if not isinstance(extra, dict):
        return None
    cwd = extra.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None
    return Path(cwd).expanduser()


def _sync_runtime_plugin_hooks(
    source_provider: ProviderLayout,
    runtime_provider: ProviderLayout,
) -> None:
    """Copy live checkpoint hook commands into an isolated runtime provider home.

    Hook commands reference the host Python + ``checkpoint_plugin`` module and
    must not be rewritten under env-state ``external/...`` mirrors.
    """
    pairs: list[tuple[Path, Path, str]] = []
    if source_provider.name == "codex":
        pairs.append(
            (source_provider.home / "hooks.json", runtime_provider.home / "hooks.json", "codex")
        )
    elif source_provider.name == "claude":
        for name in ("settings.json", "settings.local.json"):
            pairs.append((source_provider.home / name, runtime_provider.home / name, "claude"))

    for source_path, dest_path, provider_name in pairs:
        if not source_path.is_file():
            continue
        if not is_hook_config_basename(dest_path.name, provider_name):
            continue
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        current = dest_path.read_bytes() if dest_path.is_file() else b""
        merged = merge_plugin_hooks(current, source_path.read_bytes())
        if dest_path.exists() and dest_path.read_bytes() == merged:
            continue
        dest_path.write_bytes(merged)


def _copy_runtime_seed_files(
    source_provider: ProviderLayout,
    runtime_provider: ProviderLayout,
    path_map: dict[str, str],
) -> None:
    source_paths = [
        source_provider.mcp_config,
        *source_provider.mcp_config_files,
        *source_provider.settings_files,
    ]
    for source in source_paths:
        if source is None or not source.exists() or not source.is_file():
            continue
        dest = _mapped_path(source, path_map)
        if dest == source or dest.exists():
            continue
        _copy_file(source, dest)
    # Keep this reference used by callers/tests that inspect the runtime layout.
    runtime_provider.home.mkdir(parents=True, exist_ok=True)


def _copy_file(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)


def _link_runtime_secret_files(provider_name: str, source_home: Path, runtime_home: Path) -> None:
    pairs: list[tuple[Path, Path]] = [
        (source_home / "auth.json", runtime_home / "auth.json"),
        (source_home / "credentials.json", runtime_home / "credentials.json"),
        (source_home / "oauth.json", runtime_home / "oauth.json"),
        (source_home / ".env", runtime_home / ".env"),
    ]
    if provider_name == "claude":
        pairs.append((source_home.parent / ".claude.json", runtime_home / ".claude.json"))
    for source, dest in pairs:
        if _regular_file_no_symlink(source) and not dest.exists():
            _link_or_copy_file(source, dest)


def _link_or_copy_file(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _copy_regular_file_no_symlink(source, dest)


def _regular_file_no_symlink(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _copy_regular_file_no_symlink(source: Path, dest: Path) -> None:
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(source, flags)
    except OSError:
        return
    try:
        with os.fdopen(fd, "rb") as handle:
            stat_result = os.fstat(handle.fileno())
            if not _stat_is_regular(stat_result.st_mode):
                return
            mode = stat_result.st_mode & 0o777
            dest_fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            try:
                with os.fdopen(dest_fd, "wb") as output:
                    shutil.copyfileobj(handle, output)
                    _chmod_open_file_or_path(output.fileno(), dest, mode)
            except Exception:
                try:
                    os.unlink(dest)
                finally:
                    raise
    except FileExistsError:
        return


def _stat_is_regular(mode: int) -> bool:
    return stat.S_ISREG(mode)


def _chmod_open_file_or_path(fd: int, path: Path, mode: int) -> None:
    if hasattr(os, "fchmod"):
        os.fchmod(fd, mode)
    else:
        os.chmod(path, mode)


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _trajectory_resume_offset(plan: ResumePlan) -> int:
    if plan.target_manifest.trajectory_end_offset is not None:
        return plan.target_manifest.trajectory_end_offset
    return plan.target_manifest.trajectory_offset


def _codex_source_session_meta(plan: ResumePlan) -> dict[str, object] | None:
    ref = plan.target_manifest.trajectory_ref
    if ref is None or ref.provider != "codex" or not ref.transcript_path:
        return None
    path = Path(ref.transcript_path).expanduser()
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            first_line = handle.readline()
    except OSError:
        return None
    if not first_line.strip():
        return None
    try:
        record = json.loads(first_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict) or record.get("type") != "session_meta":
        return None
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else None


def _trajectory_prefix(store: CheckpointStore, plan: ResumePlan) -> TrajectoryPrefix:
    chunks: list[bytes] = []
    spans: dict[int, tuple[int, int, int]] = {}
    turn_keys: dict[int, object] = {}
    offset = 0
    manifests = [m for m in store.list_manifests() if m.turn_id <= plan.turn_id]
    key_extractor = _provider_key_extractor(manifests)
    # F3: a forked/resumed session's first captured turn anchors mid-transcript
    # (the new promptId), but the inherited pre-fork history lives inline in the
    # SAME transcript at [0:first_start_offset]. Prepend it so the resumed
    # provider session reproduces the full context rather than starting amnesiac.
    inherited = _inherited_fork_prefix(manifests, store)
    inherited_record_count = 0
    if inherited:
        chunks.append(inherited)
        offset = len(inherited)
        inherited_record_count = jsonl_count_records(inherited)
    for manifest in manifests:
        if manifest.trajectory_ref is None:
            continue
        is_latest = manifest.turn_id == plan.turn_id
        try:
            chunk = _read_trajectory_slice_for_manifest(store, manifest, extend_to_eof=is_latest)
        except (OSError, ValueError) as exc:
            print(f"Warning: trajectory unavailable for turn {manifest.turn_id}: {exc}", file=sys.stderr)
            continue
        if not chunk:
            continue
        chunks.append(chunk)
        end_offset = offset + len(chunk)
        spans[manifest.turn_id] = (offset, end_offset, jsonl_count_records(chunk))
        if key_extractor is not None:
            chunk_key = _first_record_key(chunk, key_extractor)
            if chunk_key is not None:
                turn_keys[manifest.turn_id] = chunk_key
        offset = end_offset
    if chunks:
        return TrajectoryPrefix(b"".join(chunks), spans, turn_keys, inherited_record_count)
    legacy = store.slice_trajectory(_trajectory_resume_offset(plan))
    if plan.target_manifest.trajectory_offset < len(legacy):
        spans[plan.turn_id] = (
            plan.target_manifest.trajectory_offset,
            len(legacy),
            jsonl_count_records(legacy[plan.target_manifest.trajectory_offset :]),
        )
    return TrajectoryPrefix(legacy, spans, turn_keys, inherited_record_count)


def _has_inherited_prefix(
    spans: dict[int, tuple[int, int, int]], data: bytes = b""
) -> bool:
    """True when the resume carries a fork-style inherited pre-fork prefix (P6-14).

    Two signals, because the byte-offset one does not survive a capture round-trip:
    1. The earliest captured turn anchors past byte 0 — records before it are
       inherited pre-fork history. This holds for a freshly-captured native fork.
    2. The trajectory already carries `forkedFrom` stamps (P7-3). When the plugin
       materialises a fork resume it stamps `forkedFrom` on the inherited records;
       if THAT session is later captured and resumed again, realign folds the
       inherited prefix back into turn 0 at byte 0, so signal (1) is lost. The
       `forkedFrom` marker persists in the bytes, so it keeps the inherited-prefix
       verdict idempotent across resume generations (otherwise a synthetic
       permission-mode is re-injected every hop — `_ensure_permission_mode_record`).
    """
    if spans:
        earliest_turn = min(spans)
        if spans[earliest_turn][0] > 0:
            return True
    return b'"forkedFrom"' in data


def _manifests_provider_name(manifests: list[CheckpointManifest]) -> str | None:
    """Provider name from the first manifest carrying a trajectory_ref (P6-2)."""
    for manifest in manifests:
        ref = manifest.trajectory_ref
        if ref is not None:
            return ref.provider
    return None


def _provider_key_extractor(manifests: list[CheckpointManifest]):
    """The per-turn key extractor for the provider these manifests belong to (P6-2)."""
    return _key_extractor_for(_manifests_provider_name(manifests))


def _first_record_key(chunk: bytes, key_extractor) -> object:
    """Key of the first keyed record in a turn's chunk = that turn's provider key."""
    for line in chunk.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(record, dict):
            key = key_extractor(record)
            if key is not None:
                return key
    return None


def _inherited_fork_prefix(manifests: list[CheckpointManifest], store: CheckpointStore) -> bytes:
    """Bytes of inherited history preceding the first captured turn (F3).

    When the earliest turn's slice begins past byte 0, the records before it are
    pre-fork context the provider wrote inline into the same transcript. We read
    `[0:start_offset]` from that transcript so resume restores the full thread.

    FORK-TRUNCATION recovery: If the parent file has been rewritten/truncated after
    fork (forked_at_offset > parent file size), attempt to recover from the
    fork_point_trajectory_ref blob stored at fork capture time.

    Returns b"" for normal sessions (first turn anchored at byte 0) or when the
    transcript is gone and no recovery blob exists.
    """
    first = next((m for m in manifests if m.trajectory_ref is not None), None)
    if first is None or first.trajectory_ref is None:
        return b""
    ref = first.trajectory_ref
    if not ref.transcript_path or ref.start_offset <= 0:
        return b""
    path = Path(ref.transcript_path).expanduser()

    # Try to read from parent file first
    prefix = b""
    truncation_detected = False
    try:
        with path.open("rb") as handle:
            # Check if we can read the expected range
            handle.seek(0, 2)  # Seek to end
            file_size = handle.tell()

            # FORK-TRUNCATION detection: forked_at_offset exceeds current file size
            if ref.start_offset > file_size:
                truncation_detected = True
                print(
                    f"Warning: Fork lineage truncation detected - parent file {path.name} "
                    f"is {file_size} bytes but fork point was at {ref.start_offset} bytes. "
                    f"Attempting recovery from stored fork-point blob...",
                    file=sys.stderr
                )
            else:
                handle.seek(0)
                prefix = handle.read(ref.start_offset)
    except OSError:
        truncation_detected = True

    # If truncation detected or file unavailable, try recovery from blob
    if truncation_detected or not prefix:
        metadata = _read_session_metadata(store)
        fork_point_ref = metadata.get("fork_point_trajectory_ref")

        if fork_point_ref and isinstance(fork_point_ref, str):
            try:
                # Load the fork-point trajectory blob
                fork_point_data = store.load_blob(fork_point_ref)

                # Extract the prefix up to start_offset
                if len(fork_point_data) >= ref.start_offset:
                    prefix = fork_point_data[:ref.start_offset]
                    print(
                        f"Note: Successfully recovered {len(prefix)} bytes of fork lineage "
                        f"from stored fork-point blob (ref: {fork_point_ref[:12]}...)",
                        file=sys.stderr
                    )
                else:
                    print(
                        f"Warning: Fork-point blob is {len(fork_point_data)} bytes, "
                        f"shorter than expected offset {ref.start_offset}",
                        file=sys.stderr
                    )
            except (FileNotFoundError, OSError) as exc:
                print(
                    f"Warning: Could not load fork-point trajectory blob {fork_point_ref}: {exc}",
                    file=sys.stderr
                )
        elif truncation_detected:
            print(
                "Warning: No fork_point_trajectory_ref found in metadata. "
                "Fork lineage cannot be recovered (session may have been captured "
                "before recovery feature was added).",
                file=sys.stderr
            )

    # start_offset is a line boundary; guard against a partial trailing line.
    if prefix and not prefix.endswith(b"\n"):
        cut = prefix.rfind(b"\n")
        prefix = prefix[: cut + 1] if cut >= 0 else b""
    return prefix


def _realign_spans_to_provider_file(
    provider_session_path: Path | None,
    spans: dict[int, tuple[int, int, int]],
    *,
    provider_name: str | None = None,
    turn_keys: dict[int, object] | None = None,
) -> dict[int, tuple[int, int, int]]:
    """Recompute turn spans as line-aligned byte ranges over the REWRITTEN file (P4-3/P6-2).

    `_write_provider_session` re-serializes the trajectory (sort_keys, uuid remap, a
    synthetic leading record) AND P6-3 may drop inlined-ancestor records anywhere, so
    the raw-concat spans no longer align to the file the resumed manifests point at.
    Reading them later (resume-of-a-resume) raw-seeks mid-line and drops records.

    P6-2: re-tile by matching each rewritten record's per-turn key (codex `turn_id` /
    claude `promptId`) against `turn_keys`, NOT by trusting pre-rewrite record counts
    (which mis-slice interior turns once a record is dropped in turn >= 2). A keyless
    record (session_meta, and any record without the per-turn key) attaches to the
    currently-open turn — the turn of the most recent keyed record; keyless records
    before the first keyed record fold into the earliest turn (inherited prefix).
    Falls back to count-based retiling when no key map is available (legacy path).
    """
    if provider_session_path is None or not spans:
        return spans
    try:
        data = provider_session_path.read_bytes()
    except OSError:
        return spans
    # (line_end_byte, parsed_record) for each non-blank line.
    parsed: list[tuple[int, dict | None]] = []
    offset = 0
    for line in data.splitlines(keepends=True):
        end = offset + len(line)
        if line.strip():
            record: dict | None
            try:
                loaded = json.loads(line)
                record = loaded if isinstance(loaded, dict) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                record = None
            parsed.append((end, record))
        offset = end
    total = len(parsed)
    if total == 0:
        return spans
    ordered = sorted(spans.items())
    extractor = _key_extractor_for(provider_name)
    if extractor is None or not turn_keys:
        return _realign_by_count(data, parsed, ordered)

    # Map each provider key -> owning turn_id (int). Turns with no distinct key
    # (None) can't be matched and will only collect keyless records via fall-through.
    key_to_turn: dict[object, int] = {}
    for turn_id, _ in ordered:
        key = turn_keys.get(turn_id)
        if key is not None:
            key_to_turn[key] = turn_id
    first_turn = ordered[0][0]
    # Walk records, assigning each to a turn. Keyed records that match a known turn
    # open that turn; keyless (or unknown-key) records attach to the open turn.
    counts: dict[int, int] = {turn_id: 0 for turn_id, _ in ordered}
    line_ends: list[int] = [end for end, _ in parsed]
    record_turn: list[int] = []
    open_turn = first_turn
    seen_keyed = False
    for _, record in parsed:
        key = extractor(record) if isinstance(record, dict) else None
        if key is not None and key in key_to_turn:
            open_turn = key_to_turn[key]
            seen_keyed = True
        elif not seen_keyed:
            open_turn = first_turn  # leading keyless inherited prefix
        record_turn.append(open_turn)
        counts[open_turn] += 1

    realigned: dict[int, tuple[int, int, int]] = {}
    consumed = 0
    start_byte = 0
    for turn_id, _ in ordered:
        take = counts[turn_id]
        consumed = min(consumed + take, total)
        end_byte = line_ends[consumed - 1] if consumed > 0 else start_byte
        realigned[turn_id] = (start_byte, end_byte, take)
        start_byte = end_byte
    # Safety net: the last turn always extends to EOF (covers any trailing tail).
    last_turn = ordered[-1][0]
    last_start, _, last_count = realigned[last_turn]
    realigned[last_turn] = (last_start, len(data), last_count)
    return realigned


def _key_extractor_for(provider_name: str | None):
    if provider_name == "codex":
        return codex_key
    if provider_name == "claude":
        return claude_key
    return None


def _realign_by_count(
    data: bytes,
    parsed: list[tuple[int, dict | None]],
    ordered: list[tuple[int, tuple[int, int, int]]],
) -> dict[int, tuple[int, int, int]]:
    """Legacy count-based retiling (no key map available)."""
    line_ends = [end for end, _ in parsed]
    total = len(line_ends)
    assigned = sum(count for _, (_, _, count) in ordered)
    leading = max(0, total - assigned)
    realigned: dict[int, tuple[int, int, int]] = {}
    consumed = 0
    start_byte = 0
    for idx, (turn_id, (_, _, count)) in enumerate(ordered):
        take = count + (leading if idx == 0 else 0)
        consumed = min(consumed + take, total)
        end_byte = line_ends[consumed - 1] if consumed > 0 else start_byte
        realigned[turn_id] = (start_byte, end_byte, take)
        start_byte = end_byte
    last_turn = ordered[-1][0]
    last_start, _, last_count = realigned[last_turn]
    realigned[last_turn] = (last_start, len(data), last_count)
    return realigned


def _read_trajectory_slice_for_manifest(
    store: CheckpointStore,
    manifest: CheckpointManifest,
    extend_to_eof: bool,
) -> bytes:
    ref = manifest.trajectory_ref
    if ref is None:
        return b""
    base = store.read_trajectory_slice(ref)
    if not extend_to_eof:
        return base
    tail = _recover_trailing_tail(ref)
    return base + tail


def _recover_trailing_tail(ref: TrajectoryReference) -> bytes:
    """Recover bytes flushed after the hook captured `end_offset`.

    Delegates to the shared `recover_trailing_tail`, whose guard is selected by
    `ref.boundary_mode`: per-turn-key for single-turn slices, session-boundary
    for multi-turn subagent slices (whose closing record carries the LAST turn's
    key, not the first). Coordinator's `_trailing_same_turn_tail` shares the same
    primitive so the stored manifest and a resume always agree.
    """
    return recover_trailing_tail(ref)


def _target_env_with_provider_runtime(
    manifest: CheckpointManifest,
    target_env: object,
    store: CheckpointStore,
) -> object:
    if getattr(target_env, "provider", None) == "claude":
        return _target_env_with_claude_runtime(manifest, target_env, store)
    if getattr(target_env, "provider", None) != "codex":
        return target_env
    runtime = _codex_runtime_from_manifest(manifest, store)
    if not runtime:
        return target_env
    return replace(target_env, **runtime)


def _target_env_with_claude_runtime(
    manifest: CheckpointManifest,
    target_env: object,
    store: CheckpointStore,
) -> object:
    ref = manifest.trajectory_ref
    if ref is None or ref.provider != "claude":
        return target_env
    try:
        trajectory = _read_trajectory_slice_for_manifest(store, manifest, extend_to_eof=True)
    except (OSError, ValueError):
        return target_env
    statuses = claude_mcp_statuses_from_trajectory(trajectory)
    if not statuses:
        return target_env
    current = getattr(target_env, "mcp_servers", None)
    mcp_servers = dict(current) if isinstance(current, dict) else {}
    mcp_servers.update(statuses)
    return replace(target_env, mcp_servers=mcp_servers)


def _codex_runtime_from_manifest(
    manifest: CheckpointManifest,
    store: CheckpointStore,
) -> dict[str, str]:
    ref = manifest.trajectory_ref
    if ref is None or ref.provider != "codex":
        return {}
    try:
        trajectory = store.read_trajectory_slice(ref)
    except (OSError, ValueError):
        return {}
    return _codex_runtime_from_trajectory(trajectory)


def _codex_runtime_from_trajectory(trajectory: bytes) -> dict[str, str]:
    runtime: dict[str, str] = {}
    for record in _iter_jsonl_records(trajectory):
        if record.get("type") != "turn_context":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        effort = _codex_turn_context_effort(payload)
        mode = _codex_turn_context_mode(payload)
        if effort:
            runtime["effort"] = effort
        if mode:
            runtime["mode"] = mode
    return runtime


def _codex_turn_context_effort(payload: dict[str, object]) -> str | None:
    value = _first_payload_string(
        payload,
        "model_reasoning_effort",
        "reasoning_effort",
        "thinking_effort",
        "thinkingEffort",
        "effort",
    )
    if value:
        return value
    collaboration_mode = payload.get("collaboration_mode")
    if isinstance(collaboration_mode, dict):
        settings = collaboration_mode.get("settings")
        if isinstance(settings, dict):
            return _first_payload_string(settings, "reasoning_effort", "thinking_effort", "thinkingEffort")
    return None


def _codex_turn_context_mode(payload: dict[str, object]) -> str | None:
    value = _first_payload_string(payload, "collaboration_mode_kind", "mode")
    if value:
        return value
    collaboration_mode = payload.get("collaboration_mode")
    if isinstance(collaboration_mode, dict):
        return _first_payload_string(collaboration_mode, "mode")
    return None


def _first_payload_string(payload: dict[object, object], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _write_resumed_metadata(
    source_store: CheckpointStore,
    target_store: CheckpointStore,
    plan: ResumePlan,
    new_session_id: str,
    provider_session_path: Path | None,
    cwd: Path,
    runtime: ResumeRuntime,
) -> None:
    metadata: dict[str, object] = {}
    metadata_path = source_store.session_dir / "metadata.json"
    if metadata_path.exists():
        try:
            raw_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw_metadata = {}
        if isinstance(raw_metadata, dict):
            metadata = raw_metadata
    metadata["session_id"] = new_session_id
    metadata["resumed_from_session_id"] = plan.session_id
    metadata["resumed_from_turn_id"] = plan.turn_id
    metadata["resumed_ts"] = _now()
    if provider_session_path is not None:
        metadata["provider_session_path"] = str(provider_session_path)
    else:
        metadata.pop("provider_session_path", None)
    metadata["cwd"] = str(cwd)
    metadata["resume_env_state_dir"] = str(runtime.root)
    # FK1: the source metadata is inherited wholesale, so a resume kept the SOURCE's
    # `source` (startup/fork), its stale `start_ts`, and — for a codex source — its
    # `forked_*` lineage fields. That made `list` (cli.py reads metadata["source"])
    # mislabel every resume as a fork/startup, and left a `forked_at_offset` that is a
    # boundary in the SOURCE file but overshoots both the resume's own file and the
    # parent it names. This function is provider-agnostic, so the bug hit codex (t1/t2)
    # AND claude (t3) resumes alike. Stamp the true resume identity: source="resume" and
    # a fresh start_ts (the resume moment), and drop the stale source-relative fork
    # anchor — the real lineage already lives in resumed_from_session_id /
    # resumed_from_turn_id (claude sources carry no forked_* fields, so the drops are a
    # harmless no-op there). session_title is left inherited: it describes the resumed
    # conversation prefix. session_env is rebuilt from the target environment so
    # resume-of-resume fallbacks pin the checkpointed model/effort/policy rather than
    # the source session's stale runtime hints.
    metadata["source"] = "resume"
    metadata["start_ts"] = _now()
    target_session_env = _session_env_from_environment(plan.target_env)
    if target_session_env:
        metadata["session_env"] = target_session_env
    else:
        metadata.pop("session_env", None)
    for stale_key in ("forked_from_transcript", "forked_at_offset", "forked_at_record_count"):
        metadata.pop(stale_key, None)
    target_store._atomic_write(
        target_store.session_dir / "metadata.json",
        canonical_json(metadata) + "\n",
    )


def _write_resume_open_spec(target_store: CheckpointStore, spec: ResumeOpenSpec, runtime: ResumeRuntime) -> None:
    data = {
        "provider": spec.provider,
        "session_id": spec.session_id,
        "cwd": str(spec.cwd),
        "env_state_dir": str(runtime.root),
        "provider_home": str(runtime.provider.home),
        "env": spec.env,
        "preflight": spec.preflight,
        "command": spec.command,
    }
    target_store._atomic_write(target_store.session_dir / "resume-open.json", canonical_json(data) + "\n")


def load_resume_open_spec(session_id: str, plugin_home: Path | None = None) -> ResumeOpenSpec:
    _require_resume_session_id(session_id, "session id", Path("resume-open"))
    path = session_dir(session_id, plugin_home) / "resume-open.json"
    if not path.exists():
        raise RuntimeError(
            f"No resume-open state found for session {session_id}; "
            "create it with `checkpoint resume <session> <turn>`."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid resume-open state for session {session_id}: {path}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid resume-open state for session {session_id}: {path}")
    provider = _required_string(data, "provider", path)
    spec_session_id = _required_string(data, "session_id", path)
    if spec_session_id != session_id:
        raise RuntimeError(f"Invalid resume-open state field 'session_id': {path}")
    cwd = Path(_required_string(data, "cwd", path)).expanduser()
    command = _validated_resume_open_command(
        provider,
        spec_session_id,
        _required_string_list(data.get("command"), "command", path),
        path,
    )
    preflight = [_required_string_list(item, "preflight", path) for item in _list_value(data.get("preflight"))]
    preflight = _validated_resume_open_preflight(provider, spec_session_id, preflight, path)
    env = _validated_resume_open_env(provider, data.get("env"), data, path)
    return ResumeOpenSpec(
        provider=provider,
        session_id=spec_session_id,
        cwd=cwd,
        env=env,
        preflight=preflight,
        command=command,
    )


def execute_resume_open(
    session_id: str,
    plugin_home: Path | None = None,
    *,
    exec_provider: bool = True,
    runner: Callable[..., object] | None = None,
    execvpe: Callable[[str, list[str], dict[str, str]], object] | None = None,
) -> int:
    spec = load_resume_open_spec(session_id, plugin_home)
    env = os.environ.copy()
    env.update(spec.env)
    cwd = spec.cwd.expanduser()
    if not cwd.is_dir():
        raise RuntimeError(f"Resume workspace is missing: {cwd}")
    run = runner or subprocess.run
    for command in spec.preflight:
        try:
            run(command, cwd=str(cwd), env=env, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise RuntimeError(f"Resume preflight failed: {_shell_join(command)}") from exc
    if not exec_provider:
        return 0
    try:
        os.chdir(cwd)
        (execvpe or os.execvpe)(spec.command[0], spec.command, env)
    except OSError as exc:
        raise RuntimeError(f"Failed to open resumed session: {_shell_join(spec.command)}") from exc
    return 0


def _resume_open_spec(
    provider_name: str,
    new_session_id: str,
    cwd: Path,
    provider_session_path: Path | None,
    target_env: object,
    env: dict[str, str],
) -> ResumeOpenSpec | None:
    if provider_session_path is None:
        return None
    policy = resume_policy_for_provider(provider_name)
    if policy is None:
        return None
    preflight = _resume_open_preflight(policy, provider_session_path, new_session_id)
    command = _build_resume_command_from_policy(policy, new_session_id, target_env)
    return ResumeOpenSpec(provider_name, new_session_id, cwd, dict(env), preflight, command)


def _resume_open_preflight(
    policy: ProviderResumePolicy,
    provider_session_path: Path,
    session_id: str,
) -> list[list[str]]:
    if policy.preflight_kind != "opencode_import":
        return []
    import_path = str(provider_session_path)
    return [
        ["opencode", "import", import_path],
        [sys.executable, "-m", "checkpoint_plugin.cli", "opencode-restore-metadata", import_path, session_id],
    ]


def _required_string(data: dict[str, object], key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Invalid resume-open state field {key!r}: {path}")
    return value


def _require_resume_session_id(value: str, key: str, path: Path) -> None:
    if not _valid_resume_session_id(value):
        raise RuntimeError(f"Invalid resume-open state field {key!r}: {path}")


def _valid_resume_session_id(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|ses_[A-Za-z0-9_-]{1,80})",
            value,
        )
    )


def _required_string_list(value: object, key: str, path: Path) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise RuntimeError(f"Invalid resume-open state field {key!r}: {path}")
    return list(value)


def _list_value(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {key: str(item) for key, item in value.items() if isinstance(key, str) and _safe_env_name(key)}


def _validated_resume_open_env(
    provider: str,
    value: object,
    data: dict[str, object],
    path: Path,
) -> dict[str, str]:
    env = _string_dict(value)
    policy = _resume_policy(provider, path)
    if set(env) - policy.allowed_env:
        raise RuntimeError(f"Invalid resume-open state field 'env': {path}")
    provider_home = _required_path_field(data, "provider_home", path)
    env_state_dir = _required_path_field(data, "env_state_dir", path)
    for key in policy.home_env_keys:
        if env.get(key) != str(provider_home):
            raise RuntimeError(f"Invalid resume-open state field 'env': {path}")
    if policy.data_dir_env_key is not None and policy.data_dir_name is not None:
        expected_data_dir = str(env_state_dir / policy.data_dir_name)
        if env.get(policy.data_dir_env_key) != expected_data_dir:
            raise RuntimeError(f"Invalid resume-open state field 'env': {path}")
    for key in policy.path_env_keys:
        if key in env and not Path(env[key]).expanduser().is_absolute():
            raise RuntimeError(f"Invalid resume-open state field 'env': {path}")
    return env


def _allowed_resume_open_env(provider: str, path: Path) -> set[str]:
    return set(_resume_policy(provider, path).allowed_env)


def _required_path_field(data: dict[str, object], key: str, path: Path) -> Path:
    raw = _required_string(data, key, path)
    value = Path(raw).expanduser()
    if not value.is_absolute():
        raise RuntimeError(f"Invalid resume-open state field {key!r}: {path}")
    return value


def _validated_resume_open_command(
    provider: str,
    session_id: str,
    command: list[str],
    path: Path,
) -> list[str]:
    _require_resume_session_id(session_id, "session_id", path)
    _validate_resume_command_from_policy(_resume_policy(provider, path), session_id, command, path)
    return command


def _validated_resume_open_preflight(
    provider: str,
    session_id: str,
    preflight: list[list[str]],
    path: Path,
) -> list[list[str]]:
    policy = _resume_policy(provider, path)
    if policy.preflight_kind == "none":
        if preflight:
            raise RuntimeError(f"Invalid resume-open state field 'preflight': {path}")
        return []
    if policy.preflight_kind != "opencode_import":
        raise RuntimeError(f"Invalid resume-open state field 'preflight': {path}")
    if len(preflight) != 2:
        raise RuntimeError(f"Invalid resume-open state field 'preflight': {path}")
    import_command, metadata_command = preflight
    if import_command[:2] != ["opencode", "import"] or len(import_command) != 3:
        raise RuntimeError(f"Invalid resume-open state field 'preflight': {path}")
    import_path = import_command[2]
    if not _valid_opencode_import_path(import_path, session_id):
        raise RuntimeError(f"Invalid resume-open state field 'preflight': {path}")
    expected_metadata = [sys.executable, "-m", "checkpoint_plugin.cli", "opencode-restore-metadata", import_path, session_id]
    if metadata_command != expected_metadata:
        raise RuntimeError(f"Invalid resume-open state field 'preflight': {path}")
    return preflight


def _valid_opencode_import_path(value: str, session_id: str) -> bool:
    path = Path(value).expanduser()
    return path.is_absolute() and path.name == f"{session_id}.json" and path.parent.name == "imports"


def _session_env_from_environment(env: object) -> dict[str, str]:
    fields = {
        "model": getattr(env, "model", None),
        "permission_mode": getattr(env, "permission_mode", None),
        "mode": getattr(env, "mode", None),
        "effort": getattr(env, "effort", None),
        "agent_type": getattr(env, "agent_type", None),
    }
    return {key: value for key, value in fields.items() if isinstance(value, str) and value}


def _resumed_manifest(
    manifest: CheckpointManifest,
    new_session_id: str,
    provider_session_path: Path | None,
    trajectory: TrajectoryPrefix,
    target_store: CheckpointStore,
    cwd: Path,
) -> CheckpointManifest:
    fs_ref = _rewrite_fs_ref_for_cwd(manifest.fs_ref, target_store, cwd)
    trajectory_ref = manifest.trajectory_ref
    if trajectory_ref is not None and provider_session_path is not None:
        start_offset, end_offset, record_count = trajectory.spans.get(
            manifest.turn_id,
            (manifest.trajectory_offset, manifest.trajectory_end_offset or trajectory_ref.end_offset, trajectory_ref.record_count),
        )
        trajectory_ref = TrajectoryReference(
            provider=trajectory_ref.provider,
            transcript_path=str(provider_session_path),
            start_offset=start_offset,
            end_offset=end_offset,
            record_count=record_count,
            boundary_mode=trajectory_ref.boundary_mode,
        )
        return replace(
            manifest,
            session_id=new_session_id,
            fs_ref=fs_ref,
            trajectory_offset=start_offset,
            trajectory_end_offset=end_offset,
            trajectory_ref=trajectory_ref,
        )
    return replace(manifest, session_id=new_session_id, fs_ref=fs_ref, trajectory_ref=trajectory_ref)


def _rewrite_fs_ref_for_cwd(fs_ref: str, store: CheckpointStore, cwd: Path) -> str:
    snapshot = filesystem_from_blob(fs_ref, store)
    rewritten = replace(snapshot, cwd=str(cwd))
    return store.store_json_blob(rewritten.to_json())


def _write_provider_session(
    provider_name: str,
    provider_home: Path,
    cwd: Path,
    new_session_id: str,
    trajectory: bytes,
    model: str | None,
    effort: str | None,
    permission_mode: str | None,
    mode: str | None,
    source_meta: dict[str, object] | None = None,
    has_inherited_prefix: bool = False,
    source_session_id: str | None = None,
    inherited_record_count: int = 0,
) -> Path | None:
    if not trajectory:
        return None
    if provider_name == "codex":
        return _write_codex_session(
            provider_home, cwd, new_session_id, trajectory, model, effort, permission_mode, mode,
            source_meta, inherited_record_count,
        )
    if provider_name == "claude":
        return _write_claude_session(
            provider_home, cwd, new_session_id, trajectory, model, permission_mode, mode,
            has_inherited_prefix, source_session_id, inherited_record_count,
        )
    if provider_name == "opencode":
        return _write_opencode_session(
            provider_home, cwd, new_session_id, trajectory,
        )
    return None


def _write_opencode_session(
    provider_home: Path,
    cwd: Path,
    new_session_id: str,
    trajectory: bytes,
) -> Path | None:
    """Write a JSON file that `opencode import` can ingest to restore the session.

    Parses the trajectory JSONL to extract the raw_messages (full OpenCode SDK
    format) stored by the hook. Falls back to reconstructing minimal messages
    from the turn records if raw data is unavailable (pre-fix captures).
    """
    records = _parse_jsonl(trajectory)
    if not records:
        return None
    # Extract session_info and raw_messages from the last turn's metadata
    session_info = None
    all_raw_messages: list[dict] | None = None
    session_messages: list[dict] = []
    todos: list[dict] = []
    for record in reversed(records):
        meta = record.get("metadata", {})
        hook_payload = meta.get("hook_payload", {}) if isinstance(meta, dict) else {}
        if not session_messages:
            session_messages = _opencode_hook_list(hook_payload, "session_messages", "sessionMessages")
        if not todos:
            todos = _opencode_hook_list(hook_payload, "todos")
        if all_raw_messages is None and hook_payload.get("raw_messages"):
            all_raw_messages = hook_payload["raw_messages"]
            if not session_info and hook_payload.get("session_info"):
                session_info = hook_payload["session_info"]
        if all_raw_messages is not None and session_messages and todos:
            break
    if all_raw_messages is None:
        # Pre-fix captures: reconstruct minimal messages from turn records
        all_raw_messages = _reconstruct_opencode_messages(records, new_session_id)
    if not all_raw_messages:
        return None
    # Build the export JSON structure that `opencode import` accepts
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    info = session_info or {}
    export_info = _opencode_export_info(info, new_session_id, cwd, now_ms)
    # Structure: {info, messages: [{info, parts}, ...]}
    # Remap all message/part IDs to avoid collisions with the source session
    # (opencode import uses onConflictDoNothing, so duplicate IDs are silently skipped).
    # Use time-ordered IDs so lexicographic ID comparison matches chronological
    # order. OpenCode's continuation logic relies on `id > latest.id` comparisons.
    # Format: timestamp (48-bit from uuid7) + sequential counter for strict ordering.
    base_timestamp = _uuid7().replace('-', '')[:12]  # First 48 bits of uuid7
    msg_id_map: dict[str, str] = {}
    export_messages = []
    for msg_idx, msg in enumerate(all_raw_messages):
        msg_info = msg.get("info", msg)
        msg_parts = msg.get("parts", [])
        if not isinstance(msg_info, dict):
            continue
        old_msg_id = msg_info.get("id", "")
        new_msg_id = f"msg_{base_timestamp}{msg_idx:012x}"
        msg_id_map[old_msg_id] = new_msg_id
        rewritten_info = {**msg_info, "id": new_msg_id, "sessionID": new_session_id}
        # Remap parentID to the new message ID
        if "parentID" in rewritten_info and rewritten_info["parentID"] in msg_id_map:
            rewritten_info["parentID"] = msg_id_map[rewritten_info["parentID"]]
        _rewrite_opencode_message_cwd(rewritten_info, cwd)
        rewritten_parts = []
        for part_idx, part in enumerate(msg_parts):
            if isinstance(part, dict):
                new_part_id = f"prt_{base_timestamp}{msg_idx:06x}{part_idx:06x}"
                rewritten_parts.append({
                    **part,
                    "id": new_part_id,
                    "messageID": new_msg_id,
                    "sessionID": new_session_id,
                })
            else:
                rewritten_parts.append(part)
        export_messages.append({"info": rewritten_info, "parts": rewritten_parts})
    export_data = {"info": export_info, "messages": export_messages}
    rewritten_session_messages = _rewrite_opencode_session_messages(session_messages, new_session_id)
    if rewritten_session_messages:
        export_data["session_messages"] = rewritten_session_messages
    rewritten_todos = _rewrite_opencode_todos(todos, new_session_id)
    if rewritten_todos:
        export_data["todos"] = rewritten_todos
    # Write to a temp file that the resume CLI can import
    import_dir = provider_home / "imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    import_path = import_dir / f"{new_session_id}.json"
    import_path.write_text(json.dumps(export_data, indent=2), encoding="utf-8")
    return import_path


def _opencode_hook_list(hook_payload: object, *keys: str) -> list[dict]:
    if not isinstance(hook_payload, dict):
        return []
    for key in keys:
        value = hook_payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _rewrite_opencode_session_messages(messages: list[dict], new_session_id: str) -> list[dict]:
    base_timestamp = _uuid7().replace("-", "")[:12]
    rewritten = []
    for idx, message in enumerate(messages):
        msg_type = message.get("type")
        if not isinstance(msg_type, str) or not msg_type:
            continue
        time_info = message.get("time") if isinstance(message.get("time"), dict) else {}
        created = _int_or_now(time_info.get("created") if isinstance(time_info, dict) else None)
        updated = _int_or_default(time_info.get("updated") if isinstance(time_info, dict) else None, created)
        rewritten.append(
            {
                **message,
                "id": f"evt_{base_timestamp}{idx:012x}",
                "sessionID": new_session_id,
                "type": msg_type,
                "time": {"created": created, "updated": updated},
            }
        )
    return rewritten


def _rewrite_opencode_todos(todos: list[dict], new_session_id: str) -> list[dict]:
    rewritten = []
    for idx, todo in enumerate(todos):
        content = todo.get("content")
        status = todo.get("status")
        priority = todo.get("priority")
        if not all(isinstance(value, str) and value for value in (content, status, priority)):
            continue
        time_info = todo.get("time") if isinstance(todo.get("time"), dict) else {}
        created = _int_or_now(time_info.get("created") if isinstance(time_info, dict) else None)
        updated = _int_or_default(time_info.get("updated") if isinstance(time_info, dict) else None, created)
        rewritten.append(
            {
                **todo,
                "sessionID": new_session_id,
                "content": content,
                "status": status,
                "priority": priority,
                "position": _int_or_default(todo.get("position"), idx),
                "time": {"created": created, "updated": updated},
            }
        )
    return rewritten


def _int_or_now(value: object) -> int:
    return _int_or_default(value, int(datetime.now(timezone.utc).timestamp() * 1000))


def _int_or_default(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    return default


def _opencode_export_info(info: dict, new_session_id: str, cwd: Path, now_ms: int) -> dict:
    """Build an OpenCode Session.Info object for `opencode import`.

    Start from captured session_info so runtime session fields (permission,
    metadata, workspaceID, share/revert, etc.) survive. Then stamp only the
    identity/location/time fields that must belong to the resumed session.
    """
    base = dict(info) if isinstance(info, dict) else {}
    source_time = base.get("time") if isinstance(base.get("time"), dict) else {}
    base.update(
        {
            "id": new_session_id,
            "slug": base.get("slug", _opencode_slug(new_session_id)),
            "projectID": base.get("projectID", "global"),
            "directory": str(cwd),
            "path": _opencode_session_path(base, cwd),
            "title": base.get("title", "Resumed session"),
            "version": base.get("version", "2"),
            "cost": base.get("cost", 0),
            "tokens": base.get(
                "tokens",
                {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            ),
            "time": {"created": source_time.get("created", now_ms), "updated": now_ms},
        }
    )
    base.pop("parentID", None)
    return {key: value for key, value in base.items() if value is not None}


def _opencode_session_path(info: dict, cwd: Path) -> str | None:
    source_directory = info.get("directory")
    source_path = info.get("path")
    if isinstance(source_directory, str) and isinstance(source_path, str):
        try:
            rel = Path(source_path)
            if not rel.is_absolute():
                source_root = Path(source_directory).expanduser().resolve() / rel
                resume_root = cwd.expanduser().resolve()
                return str(resume_root.relative_to(source_root))
        except (OSError, ValueError):
            pass
    return source_path if isinstance(source_path, str) else None


def _rewrite_opencode_message_cwd(info: dict, cwd: Path) -> None:
    path_info = info.get("path")
    if isinstance(path_info, dict):
        path_info["cwd"] = str(cwd)


def _reconstruct_opencode_messages(
    records: list[dict], session_id: str
) -> list[dict]:
    """Build minimal OpenCode message structures from turn records (fallback)."""
    messages: list[dict] = []
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    base_timestamp = _uuid7().replace('-', '')[:12]
    last_user_id: str | None = None
    msg_counter = 0
    for i, record in enumerate(records):
        if record.get("type") != "turn":
            continue
        user_text = record.get("user_message", "")
        assistant_text = record.get("assistant_text", "")
        ts = now_ms + i * 1000
        if user_text:
            user_id = f"msg_resume_{base_timestamp}{msg_counter:012x}"
            msg_counter += 1
            user_msg = {
                "info": {
                    "id": user_id,
                    "sessionID": session_id,
                    "role": "user",
                    "time": {"created": ts},
                    "agent": "build",
                },
                "parts": [{"id": f"prt_resume_{base_timestamp}{msg_counter:012x}", "messageID": user_id, "sessionID": session_id, "type": "text", "text": user_text}],
            }
            messages.append(user_msg)
            last_user_id = user_id
        if assistant_text:
            asst_id = f"msg_resume_{base_timestamp}{msg_counter:012x}"
            msg_counter += 1
            if user_text:
                parent_id = last_user_id
            elif messages:
                parent_id = messages[-1]["info"]["id"]
            else:
                parent_id = None
            asst_msg = {
                "info": {
                    "id": asst_id,
                    "sessionID": session_id,
                    "parentID": parent_id,
                    "role": "assistant",
                    "mode": "build",
                    "agent": "build",
                    "path": {"cwd": ".", "root": "/"},
                    "time": {"created": ts + 500, "completed": ts + 1000},
                    "finish": "stop",
                },
                "parts": [{"id": f"prt_resume_{base_timestamp}{msg_counter:012x}", "messageID": asst_id, "sessionID": session_id, "type": "text", "text": assistant_text}],
            }
            messages.append(asst_msg)
    return messages


def _opencode_slug(session_id: str) -> str:
    """Generate a simple slug from the session id."""
    return f"resumed-{session_id[-8:]}"


def _parse_jsonl(data: bytes) -> list[dict]:
    """Parse JSONL bytes into a list of dicts."""
    records = []
    for line in data.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return records


def _first_session_meta_id(records: list[dict[str, object]]) -> str | None:
    """Id of the first session_meta record = the original session id (P4-5)."""
    for record in records:
        if record.get("type") == "session_meta":
            payload = record.get("payload")
            if isinstance(payload, dict):
                value = payload.get("id")
                return value if isinstance(value, str) and value else None
            return None
    return None


def _source_session_title(store: CheckpointStore) -> str | None:
    """The source session's recorded title (for the codex resume index, M5)."""
    metadata_path = store.session_dir / "metadata.json"
    if not metadata_path.exists():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    title = metadata.get("session_title")
    return title if isinstance(title, str) and title else None


def _derive_session_title(store: CheckpointStore, plan: ResumePlan) -> str:
    """Non-null thread_name for the codex session_index (P6-5).

    Real index entries always carry a non-empty `thread_name`, so when the source
    has no recorded `session_title` we derive one. Selection rule (corrected): the
    TARGET turn's preview first (the turn being resumed names the session), else the
    nearest PRECEDING included turn with a non-empty preview, else a constant. We do
    NOT default to turn 0's preview on a later-turn resume — turn 0 can name
    unrelated inherited context.
    """
    preview_by_turn: dict[int, str] = {}
    for manifest in store.list_manifests():
        if manifest.turn_id <= plan.turn_id:
            preview = (manifest.user_message_preview or "").strip()
            if preview:
                preview_by_turn[manifest.turn_id] = preview
    # Walk from the target turn downward to the earliest included turn.
    for turn_id in range(plan.turn_id, -1, -1):
        preview = preview_by_turn.get(turn_id)
        if preview:
            return _cap_title(preview)
    return "Resumed session"


def _cap_title(preview: str, limit: int = 48) -> str:
    """Cap a derived thread_name at `limit` chars on a word boundary (F18).

    `preview[:50]` cut mid-word ("...it's a fork tes"), unlike native titles which
    are whole-word summaries (max len ~46 in real data). We can't summarize, but we
    can at least avoid the mid-word slice: trim to the last space within the cap. If
    there is no interior space (one long token), fall back to the hard cap.
    """
    if len(preview) <= limit:
        return preview
    head = preview[:limit]
    cut = head.rfind(" ")
    return head[:cut].rstrip() if cut > 0 else head


def _append_codex_session_index(
    codex_home: Path, new_session_id: str, title: str | None
) -> None:
    """Register the resumed codex session so the picker can discover it (M5).

    `~/.codex/session_index.jsonl` is a JSONL of `{id, thread_name, updated_at}`
    that drives the Codex resume picker. Resume writes the rollout file but never
    registered it here, so the new session was invisible in the list. Append an
    entry (rewriting the whole file atomically) so it shows up.
    """
    index_path = codex_home / "session_index.jsonl"
    entry = {"id": new_session_id, "thread_name": title, "updated_at": _zulu_now_us()}
    try:
        existing = index_path.read_bytes() if index_path.exists() else b""
    except OSError:
        existing = b""
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    _write_bytes_atomic(index_path, existing + _json_line(entry))


def _write_codex_session(
    codex_home: Path,
    cwd: Path,
    new_session_id: str,
    trajectory: bytes,
    model: str | None,
    effort: str | None,
    permission_mode: str | None,
    mode: str | None,
    source_meta: dict[str, object] | None,
    inherited_record_count: int = 0,
) -> Path:
    # F10: native rollout filenames + the YYYY/MM/DD bucket use LOCAL time (verified:
    # native 415c filename T20-07-30 for payload UTC 12:07:30 on a UTC+8 host), while
    # in-record timestamps stay UTC-Z. Using UTC for the filename both skews the stamp
    # and, near UTC-midnight, files the rollout in the wrong date directory (so it
    # sorts incorrectly in the picker). Build the path/filename from local time.
    now = datetime.now().astimezone()
    session_dir_path = codex_home / "sessions" / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
    path = session_dir_path / f"rollout-{now.strftime('%Y-%m-%dT%H-%M-%S')}-{new_session_id}.jsonl"
    _write_bytes_atomic(
        path,
        _rewrite_codex_trajectory(
            trajectory, new_session_id, cwd, model, permission_mode, mode, source_meta,
            inherited_record_count, is_resume=True, effort=effort,
        ),
    )
    return path


def _write_claude_session(
    claude_home: Path,
    cwd: Path,
    new_session_id: str,
    trajectory: bytes,
    model: str | None,
    permission_mode: str | None,
    mode: str | None,
    has_inherited_prefix: bool = False,
    source_session_id: str | None = None,
    inherited_record_count: int = 0,
) -> Path:
    path = claude_home / "projects" / _claude_project_dir_name(cwd) / f"{new_session_id}.jsonl"
    _write_bytes_atomic(
        path,
        _rewrite_claude_trajectory(
            trajectory, new_session_id, cwd, model, permission_mode, mode,
            has_inherited_prefix=has_inherited_prefix,
            source_session_id=source_session_id,
            inherited_record_count=inherited_record_count,
        ),
    )
    return path


def _rewrite_codex_trajectory(
    trajectory: bytes,
    new_session_id: str,
    cwd: Path,
    model: str | None,
    permission_mode: str | None,
    mode: str | None,
    source_meta: dict[str, object] | None,
    inherited_record_count: int = 0,
    is_resume: bool = False,
    effort: str | None = None,
) -> bytes:
    lines: list[bytes] = []
    records = _jsonl_records(trajectory)
    # P4-5: the new session forked FROM the original session, so the fresh head
    # meta's lineage points at the original id (the first session_meta's id).
    original_session_id = _first_session_meta_id(records)
    source_cwd = _codex_source_cwd(source_meta, records)
    now = _zulu_now()
    # F2: native codex resume/fork keeps the inlined ancestor session_meta chain and
    # PREPENDS a fresh head meta (verified: native depth-scaled count startup=1,
    # resume=2, fork-of-fork=3). The old code collapsed every chain to one meta. We
    # now (1) prepend a fresh head meta (new id, forked_from_id=source id, its own
    # payload timestamp = resume moment) and (2) keep each inlined source meta with
    # its ORIGINAL id, forked_from_id and payload timestamp, only re-stamping the
    # record-level timestamp to the resume moment (F9) — exactly mirroring native
    # a67e (record-ts=fork moment on all metas; payload-ts=each meta's creation).
    # RF2: is_resume=True suppresses forked_from_id in HEAD meta (native resume behavior).
    if records and records[0].get("type") == "session_meta":
        lines.append(_json_line(_codex_head_meta(new_session_id, cwd, source_meta, original_session_id, now, is_resume)))
    else:
        # No source meta to inline: emit the single synthetic head (legacy shape).
        lines.append(_json_line(_codex_session_meta(new_session_id, cwd, source_meta)))
    for record_index, record in enumerate(records):
        # TS1: spread the inherited prefix across adjacent ms at native burst density
        # (offset 0 for the first DENSITY records, then +1ms each block) instead of one
        # reused value. Monotonic and deterministic; small prefixes stay on a single ms.
        record_now = _bump_zulu_ms(now, record_index // _TS_BURST_DENSITY)
        # F11: native forks REPLAY thread_rolled_back verbatim — a67e (fork-of-fork)
        # keeps it at idx 35 & 55 in its inlined prefix, 8c17 at idx 34, and both
        # reload fine. The old M1 strip-everywhere both diverged from native and
        # erased the in-thread edit-and-resend seam inside captured turns (415c rec30,
        # between the version-1 and version-2 prompts). The most faithful behavior is
        # to keep these markers exactly as a native session would, so we no longer
        # strip them at all.
        payload = record.get("payload")
        if record.get("type") == "session_meta" and isinstance(payload, dict):
            # Keep this inlined ancestor meta verbatim in lineage (its own id +
            # forked_from_id), re-stamp only timestamps and cwd.
            _apply_preserved_meta_fields(payload, source_meta)
            _mark_codex_session_visible(payload)
            payload["cwd"] = str(cwd)
            record["timestamp"] = record_now  # F9: record-ts = resume moment (TS1: bursted)
            # payload["timestamp"] (the meta's original creation time) is preserved.
            lines.append(_json_line(record))
            continue
        if isinstance(payload, dict):
            if "id" in payload:
                payload["id"] = new_session_id
            if "thread_id" in payload:
                payload["thread_id"] = new_session_id
            if "cwd" in payload:
                payload["cwd"] = str(cwd)
            # P4-2: real Codex turn_context carries `type` at the RECORD level;
            # `payload` holds model/permission_profile/sandbox_policy but no `type`
            # key. Gate on record["type"] so this actually fires on live data
            # (the old payload["type"] check was dead code).
            if record.get("type") == "turn_context":
                if model:
                    payload["model"] = model
                if effort:
                    payload["model_reasoning_effort"] = effort
                # F1: turn_context.permission_profile is a STRUCTURED object
                # ({type, file_system, network, ...}) and sits alongside
                # sandbox_policy/approval_policy. The hook-derived permission_mode
                # is a bare string of a different vocabulary; assigning it here
                # corrupts the object and breaks Codex load. The captured turn
                # already holds the exact permission profile, so we preserve it
                # verbatim and only re-pin a string profile if the original was
                # itself a string (legacy/simple form).
                if permission_mode and isinstance(payload.get("permission_profile"), str):
                    payload["permission_profile"] = permission_mode
            # SA2: inject mode (collaboration_mode_kind) into task_started events
            if record.get("type") == "task_started" and mode:
                payload["collaboration_mode_kind"] = mode
        # F5: rewrite the SOURCE cwd to the resume cwd everywhere it is embedded, not
        # just payload["cwd"]: the structured sandbox/permission write-roots and the
        # environment_context / developer message bodies still named the source
        # workspace, which would (re)grant sandbox writes to the wrong directory on a
        # reloaded resume — a real correctness/safety gap, not cosmetic.
        if source_cwd:
            _rewrite_codex_record_cwd(record, source_cwd, str(cwd))
        if "id" in record:
            record["id"] = new_session_id
        if "session_id" in record:
            record["session_id"] = new_session_id
        # N5: native codex forks re-stamp every inlined body record's RECORD-level
        # `timestamp` to the fork moment (verified: native bf0/be9/bea each have 0/N
        # body records preceding the head meta; the whole inlined parent history is
        # bumped to the fork second, while genuine post-fork turns keep later times).
        # A plugin resume reconstructs a file that is ENTIRELY pre-resume history (no
        # live post-resume turns exist yet), so every body record is "inherited" and
        # must be bumped — otherwise captured turns keep their original (earlier)
        # times and sort BEFORE the head meta (temporal inversion). The payload-
        # internal timestamps/turn_ids are left untouched (native preserves them).
        if "timestamp" in record:
            record["timestamp"] = record_now
        lines.append(_json_line(record))
    return b"".join(lines)


def _codex_source_cwd(
    source_meta: dict[str, object] | None, records: list[dict[str, object]]
) -> str | None:
    """The source session's cwd, for F5 path-prefix rewriting.

    Prefer the captured `source_meta` (read before any per-record cwd is rewritten);
    fall back to the first session_meta payload cwd in the trajectory.
    """
    if source_meta:
        value = source_meta.get("cwd")
        if isinstance(value, str) and value:
            return value
    for record in records:
        if record.get("type") == "session_meta":
            payload = record.get("payload")
            if isinstance(payload, dict):
                value = payload.get("cwd")
                return value if isinstance(value, str) and value else None
    return None


def _rewrite_codex_record_cwd(record: dict[str, object], source_cwd: str, target_cwd: str) -> None:
    """Replace the source cwd with the resume cwd in the LIVE-STATE fields of one
    codex record (F5), leaving historical content untouched (ER2).

    F5's real safety goal is that a reloaded resume must not (re)grant sandbox writes
    to the source workspace. Codex derives those grants ONLY from the STRUCTURED
    `turn_context` sandbox/permission write-roots (+ `turn_context.cwd`) and the
    `session_meta.cwd`, so those are rewritten. `event_msg` `patch_apply` change-sets
    are keyed by absolute path (N4) and describe where edits land on reload, so those
    keys are rewritten too.

    ER2: the OLD code walked EVERY string leaf, so it also rewrote `function_call_output`
    output (e.g. a recorded `pwd` result), `function_call.arguments`, and message/
    developer text — falsifying command history with a path the command never produced
    and diverging from claude (which leaves historical content verbatim). Those are
    advisory/historical, never re-derived into OS grants (leaving them stale fails
    closed), so we now SKIP them and rewrite only the live-state fields above. This
    aligns codex with claude while preserving the F5 write-root rewrite and N4.
    """
    if source_cwd == target_cwd:
        return
    record_type = record.get("type")
    payload = record.get("payload")
    if record_type == "session_meta":
        # session_meta.cwd is re-pinned by the caller already; rewrite any residual
        # cwd embedded in the payload (defensive — keeps the meta self-consistent).
        if isinstance(payload, dict) and isinstance(payload.get("cwd"), str):
            payload["cwd"] = _rewrite_cwd_in_string(payload["cwd"], source_cwd, target_cwd)
        return
    if record_type == "turn_context" and isinstance(payload, dict):
        # The whole turn_context payload is live state: cwd + structured
        # sandbox_policy/permission_profile write-root entries. Walk it fully.
        _rewrite_cwd_in_value(payload, source_cwd, target_cwd)
        return
    if record_type == "event_msg" and isinstance(payload, dict):
        # N4: patch_apply_begin/end `changes` is keyed by absolute file path — the
        # edit targets on reload. Rewrite the changes map (keys + values); leave any
        # other event_msg text (e.g. task_complete summaries) historical.
        changes = payload.get("changes")
        if isinstance(changes, (dict, list)):
            _rewrite_cwd_in_value(changes, source_cwd, target_cwd)
        return
    # response_item (function_call / function_call_output / message) and everything
    # else is historical content: leave it verbatim (ER2), matching claude.


def _rewrite_cwd_in_value(value: object, source_cwd: str, target_cwd: str) -> object:
    if isinstance(value, dict):
        # N4: rewrite KEYS as well as values. Codex `patch_apply_begin/end.changes`
        # is keyed by absolute file path, so the source cwd survives as a dict key
        # (a residual F5 leak) unless the key itself is rewritten. Rebuild the dict
        # so key order is preserved while both keys and values are path-rewritten.
        rebuilt: dict[object, object] = {}
        for key, item in list(value.items()):
            new_key = _rewrite_cwd_in_string(key, source_cwd, target_cwd) if isinstance(key, str) else key
            rebuilt[new_key] = _rewrite_cwd_in_value(item, source_cwd, target_cwd)
        value.clear()
        value.update(rebuilt)
        return value
    if isinstance(value, list):
        for i, item in enumerate(value):
            value[i] = _rewrite_cwd_in_value(item, source_cwd, target_cwd)
        return value
    if isinstance(value, str):
        return _rewrite_cwd_in_string(value, source_cwd, target_cwd)
    return value


def _rewrite_cwd_in_string(text: str, source_cwd: str, target_cwd: str) -> str:
    """Rewrite source-cwd path occurrences in a string leaf via exact-prefix anchors.

    Handles two shapes: (1) the whole string IS a path (sandbox/permission entries:
    `path == source` or `path.startswith(source + "/")`); (2) the path is embedded in
    free text (`<cwd>/src/test</cwd>`, "writable roots are /src/test, ..."). For (2)
    we replace each occurrence of the source path only when it is followed by a path
    boundary (`/`, end, or a non-path char), so `/test` never matches inside
    `/test-checkpoint-copy`.
    """
    if source_cwd not in text:
        return text
    if text == source_cwd:
        return target_cwd
    if text.startswith(source_cwd + "/"):
        return target_cwd + text[len(source_cwd):]
    # Embedded in free text: rewrite occurrences at a path boundary.
    result: list[str] = []
    i = 0
    n = len(source_cwd)
    while True:
        j = text.find(source_cwd, i)
        if j < 0:
            result.append(text[i:])
            break
        result.append(text[i:j])
        after = text[j + n : j + n + 1]
        # Boundary: end of string, a path separator, or a non-path delimiter. NOT a
        # bare alnum/'-'/'_' which would mean a longer sibling dir (test-checkpoint…).
        if after == "" or after == "/" or not (after.isalnum() or after in "-_"):
            result.append(target_cwd)
        else:
            result.append(source_cwd)  # sibling like test-checkpoint-copy; leave as-is
        i = j + n
    return "".join(result)


def _codex_head_meta(
    new_session_id: str,
    cwd: Path,
    source_meta: dict[str, object] | None,
    original_session_id: str | None,
    now: str,
    is_resume: bool = False,
) -> dict[str, object]:
    """A fresh codex head session_meta forked from the source (F2/F14/N2/RF2).

    Native head metas place `forked_from_id` immediately after `id` (idx1), carry
    their own creation timestamp as payload `timestamp`, and serialize the remaining
    fields in a FIXED interleave (verified byte-for-byte against native bf0):
    `id, forked_from_id, timestamp, cwd, originator, cli_version, source,
    thread_source, model_provider, base_instructions, dynamic_tools`. The old
    two-phase fill (preserved fields then provenance defaults) emitted
    `cwd, cli_version, model_provider, …, originator, source, thread_source` — a
    byte-distinguishable order drift. Build the payload directly in native order.

    RF2: Only add `forked_from_id` for forks and subagents, NOT for resumes. A resume
    operation creates a fresh session with no fork lineage (native codex resume/startup
    sessions have no `forked_from_id` in HEAD meta). Use the same discriminator as
    `_codex_session_meta`: subagents have a structured `source` dict and need
    `forked_from_id` to maintain lineage; resumes are explicitly marked via is_resume.
    """
    payload: dict[str, object] = {"id": new_session_id}
    # RF2: discriminate subagent (needs forked_from_id) from resume (does not).
    # Subagents have source={subagent:{...}} dict; resumes are marked is_resume=True.
    source_value = source_meta.get("source") if source_meta else None
    is_subagent = isinstance(source_value, dict)
    if original_session_id and (is_subagent or not is_resume):
        payload["forked_from_id"] = original_session_id  # F14: right after id
    payload["timestamp"] = now
    payload["cwd"] = str(cwd)
    _fill_codex_meta_provenance_in_native_order(payload, source_meta)
    return {"timestamp": now, "type": "session_meta", "payload": payload}


# N2: native codex meta interleave for the provenance fields that follow `cwd`.
_CODEX_META_NATIVE_ORDER = (
    "originator",
    "cli_version",
    "source",
    "thread_source",
    "model_provider",
    "base_instructions",
    "dynamic_tools",
)
_CODEX_META_PROVENANCE_DEFAULTS = {
    "originator": "Codex Desktop",
    "source": "vscode",
    "thread_source": "user",
}


def _fill_codex_meta_provenance_in_native_order(
    payload: dict[str, object], source_meta: dict[str, object] | None
) -> None:
    """Append provenance fields after `cwd` in native key order (N2).

    Each field is taken from the source meta when present, else the
    Desktop/vscode/user default for the three provenance keys. Any other preserved
    field (e.g. `git`, `agent_nickname`) the source carried but native's canonical
    order doesn't enumerate is appended afterwards so no source data is dropped.
    """
    for key in _CODEX_META_NATIVE_ORDER:
        if key in payload:
            continue
        if source_meta and key in source_meta:
            payload[key] = source_meta[key]
        elif key in _CODEX_META_PROVENANCE_DEFAULTS:
            payload[key] = _CODEX_META_PROVENANCE_DEFAULTS[key]
    # Carry any remaining preserved fields the canonical order doesn't list.
    for key in _PRESERVED_CODEX_META_FIELDS:
        if key not in payload and source_meta and key in source_meta:
            payload[key] = source_meta[key]


def _codex_session_meta(
    new_session_id: str,
    cwd: Path,
    source_meta: dict[str, object] | None,
) -> dict[str, object]:
    now = _zulu_now()
    payload: dict[str, object] = {
        "id": new_session_id,
        "timestamp": now,
        "cwd": str(cwd),
    }
    # P6-11/MISS-5: Preserve forked_from_id for subagents (which inherit their parent's
    # fork ancestry), but NOT for resumes. A resume creates a fresh session with
    # source='resume' in metadata.json and no forked_* fields (FK1 fix at line 684),
    # so the trajectory's head meta should also have no forked_from_id. Subagents have
    # source={subagent:{...}} and DO need forked_from_id to maintain lineage.
    # Detect subagent by checking if source is a dict (structured subagent source).
    source_value = source_meta.get("source") if source_meta else None
    is_subagent = isinstance(source_value, dict)
    if is_subagent and source_meta and source_meta.get("forked_from_id"):
        payload["forked_from_id"] = source_meta["forked_from_id"]
    _apply_preserved_meta_fields(payload, source_meta)
    _mark_codex_session_visible(payload)
    return {
        "timestamp": now,
        "type": "session_meta",
        "payload": payload,
    }


_PRESERVED_CODEX_META_FIELDS = (
    "cli_version",
    "model_provider",
    "base_instructions",
    "dynamic_tools",
    "git",
    # P6-1 / P6-11: provenance fields are carried verbatim from the source meta so
    # we never clobber a structured subagent `source` dict (or a CLI/TUI
    # entrypoint's provenance) with the Desktop/vscode defaults below.
    "originator",
    "source",
    "thread_source",
    "agent_nickname",
)


def _apply_preserved_meta_fields(
    payload: dict[str, object], source_meta: dict[str, object] | None
) -> None:
    if not source_meta:
        return
    for key in _PRESERVED_CODEX_META_FIELDS:
        if key in payload:
            continue
        if key in source_meta:
            payload[key] = source_meta[key]


def _mark_codex_session_visible(payload: dict[str, object]) -> None:
    # P6-1: fill the Desktop/vscode/user provenance defaults ONLY when the field is
    # absent. `_apply_preserved_meta_fields` runs first, so a real `source` (string
    # OR a structured `{subagent:{...}}` dict), `originator`, or `thread_source` is
    # already present and must never be overwritten or coerced.
    payload.setdefault("originator", "Codex Desktop")
    payload.setdefault("source", "vscode")
    payload.setdefault("thread_source", "user")


_CLAUDE_POINTER_KEYS = ("messageId", "leafUuid")

# F4: the keyless provider-bookkeeping record types a native claude fork drops from
# its inherited region (verified: native b57f8e6f omits exactly these from 4be30374).
# `system` records carry uuids and are message content, so they are NOT in this set.
_CLAUDE_STRIPPED_FORK_TYPES = frozenset(
    {"mode", "permission-mode", "file-history-snapshot", "ai-title", "last-prompt"}
)


def _drop_dangling_trailing_pointers(
    records: list[dict[str, object]],
    uuid_map: dict[str, str],
) -> list[dict[str, object]]:
    """Drop trailing keyless records whose pointer targets a uuid absent here (M4).

    The latest turn's EOF tail can end with keyless claude records
    (file-history-snapshot, last-prompt) whose messageId/leafUuid references a
    message uuid in the NEXT turn — a forward reference outside this prefix.
    After the two-pass remap those pointers would dangle. We trim only from the
    END: stop at the first record that carries its own uuid (a real message) or
    whose pointer resolves within this file; an interior pointer is left intact.
    """
    end = len(records)
    while end > 0:
        record = records[end - 1]
        if isinstance(record.get("uuid"), str):
            break  # a real message record; stop trimming
        pointer = next(
            (record[key] for key in _CLAUDE_POINTER_KEYS if isinstance(record.get(key), str)),
            None,
        )
        if pointer is None:
            break  # not a pointer record; leave the tail intact
        if pointer in uuid_map:
            break  # resolvable within this file; keep it
        end -= 1  # dangling forward reference — drop it
    return records[:end]


def _rewrite_claude_trajectory(
    trajectory: bytes,
    new_session_id: str,
    cwd: Path,
    model: str | None,
    permission_mode: str | None,
    mode: str | None,
    *,
    has_inherited_prefix: bool = False,
    source_session_id: str | None = None,
    inherited_record_count: int = 0,
) -> bytes:
    """Rewrite a captured claude trajectory into a native fork-shaped transcript.

    F1/F4 (verified against native b57f8e6f, a `--resume` of the startup 4be30374):
    a native claude resume produces a FORK-shaped file, regardless of whether the
    source was itself a fork:
      * It keeps each inherited record's `uuid`/`parentUuid` BYTE-IDENTICAL to the
        source and stamps `forkedFrom = {sessionId: source, messageUuid: own_uuid}`,
        so 26/26 forkedFrom.messageUuid resolve INTO the parent session — the
        cross-session thread link Claude uses for fork navigation/rewind. The old
        code remapped every uuid via uuid4() then pointed forkedFrom at the REMAPPED
        uuid, so it resolved into the source 0/26 (link severed). P7-2's
        "own == messageUuid" was true-but-insufficient (it never checked resolution).
      * It STRIPS the source's keyless records (mode, permission-mode,
        file-history-snapshot, ai-title, last-prompt) from the inherited region and
        begins at the first uuid-bearing record; native b57f8e6f drops exactly the 10
        keyless records 4be30374 carried.
      * It does NOT synthesize a leading permission-mode record.

    A resume WRITE captures the source's records through the resumed turn; there are
    no genuinely-new turns yet (those happen live after the resume loads), so the
    ENTIRE captured prefix is inherited and every uuid-bearing record is fork-stamped
    with its preserved uuid. `inherited_record_count`/`has_inherited_prefix` are
    retained for signature compatibility but the inherited region is now the whole
    captured set.
    """
    permission_mode = _normalize_permission_mode(permission_mode)
    records = _jsonl_records(trajectory)
    is_fork_shaped = bool(source_session_id)
    if is_fork_shaped:
        # F4: a native fork inherits only message records; it drops the source's
        # keyless provider-bookkeeping records. Strip exactly the types native
        # b57f8e6f dropped from 4be30374's inherited region (mode, permission-mode,
        # file-history-snapshot, ai-title, last-prompt) — NOT every keyless record:
        # `system` records carry uuids and are kept, and any other keyless content is
        # left intact rather than guessed away.
        records = [r for r in records if r.get("type") not in _CLAUDE_STRIPPED_FORK_TYPES]
    else:
        # Legacy path (no source id): keep the old synthetic-permission-mode behavior.
        records = _ensure_permission_mode_record(
            records, permission_mode, new_session_id, has_inherited_prefix=has_inherited_prefix
        )
    # Build the old->new uuid map. For a fork-shaped resume the inherited records keep
    # their uuids byte-identical (identity map) so forkedFrom.messageUuid resolves into
    # the parent; only a non-fork (legacy) resume remaps to fresh uuids.
    uuid_map: dict[str, str] = {}
    for record in records:
        old_uuid = record.get("uuid")
        if isinstance(old_uuid, str) and old_uuid not in uuid_map:
            uuid_map[old_uuid] = old_uuid if is_fork_shaped else str(uuid.uuid4())
    records = _drop_dangling_trailing_pointers(records, uuid_map)
    # P7-6: native sessions carry a SINGLE uniform CLI `version`. Re-pin every
    # versioned record to the most recent version present (uniform, like native).
    latest_version = _latest_claude_version(records)
    last_uuid: str | None = None
    lines: list[bytes] = []
    # SA2: inject mode record at the beginning if mode is provided
    if mode:
        mode_record = {
            "type": "mode",
            "mode": mode,
            "sessionId": new_session_id,
        }
        if latest_version:
            mode_record["version"] = latest_version
        lines.append(_json_line(mode_record))
    for record in records:
        # F8: file-history-snapshot/summary records carry no sessionId natively; gate
        # the re-pin on field presence so we don't add a non-native key.
        if "sessionId" in record:
            record["sessionId"] = new_session_id
        if latest_version and "version" in record:
            record["version"] = latest_version
        if "cwd" in record:
            record["cwd"] = str(cwd)
        # F2: Claude model lives at message.model on assistant records, not top-level.
        if model:
            if "model" in record:
                record["model"] = model
            message = record.get("message")
            if record.get("type") == "assistant" and isinstance(message, dict) and "model" in message:
                message["model"] = model
        if permission_mode and record.get("type") == "permission-mode":
            record["permissionMode"] = permission_mode
        if isinstance(record.get("uuid"), str):
            record["uuid"] = uuid_map[str(record["uuid"])]
        for pointer_key in ("messageId", "leafUuid"):
            value = record.get(pointer_key)
            if isinstance(value, str) and value in uuid_map:
                record[pointer_key] = uuid_map[value]
        # P6-7: file-history-snapshot nests snapshot.messageId; remap it too. (Under
        # the fork-shaped identity map this is a no-op; it still matters on the legacy
        # remap path.)
        if record.get("type") == "file-history-snapshot":
            snapshot = record.get("snapshot")
            if isinstance(snapshot, dict):
                nested = snapshot.get("messageId")
                if isinstance(nested, str) and nested in uuid_map:
                    snapshot["messageId"] = uuid_map[nested]
        # N1: a native claude resume LINEARIZES the inherited region into a single
        # parent spine — every uuid-bearing record's parentUuid points at the
        # immediately-PRECEDING emitted uuid record (verified byte-for-byte against
        # native 62a9ea3c: 0/42 records deviate from "parent == previous uuid record
        # of any content type"). The source can branch (parallel subagents, an
        # edit-and-resend); a real --resume follows the active leaf's ancestry and
        # re-tiles it into one chain. The identity uuid_map preserves every source
        # parentUuid verbatim, so on the fork path we MUST re-point to `last_uuid`
        # (the previous emitted record) instead of keeping the mapped source parent —
        # otherwise the resumed file keeps the branch (2 leaves) where native has one.
        # The legacy (non-fork, remap) path keeps following the remapped source DAG.
        if isinstance(record.get("parentUuid"), str):
            if is_fork_shaped:
                record["parentUuid"] = last_uuid if last_uuid is not None else str(record["parentUuid"])
            else:
                record["parentUuid"] = uuid_map.get(str(record["parentUuid"]), last_uuid)
        elif "parentUuid" in record and record.get("type") not in {"summary", "permission-mode"}:
            record["parentUuid"] = last_uuid
        # Advance the spine pointer. Native chains through ALL content record types
        # (user/assistant/system/attachment), so re-pointing only across user/assistant
        # would mis-parent an interior system/attachment record (verified: native idx8
        # `system` parents the previous `system`, not the last assistant). Exclude only
        # the summary/permission-mode meta records, preserving the summary-skip rule.
        if isinstance(record.get("uuid"), str) and record.get("type") not in {"summary", "permission-mode"}:
            last_uuid = str(record["uuid"])
        # F1: stamp forkedFrom on every inherited record, pointing messageUuid at the
        # record's OWN (preserved) uuid so it resolves INTO the parent session — the
        # native invariant. record["uuid"] is byte-identical to the source here.
        own_uuid = record.get("uuid")
        if is_fork_shaped and isinstance(own_uuid, str) and "forkedFrom" not in record:
            record["forkedFrom"] = {"sessionId": source_session_id, "messageUuid": own_uuid}
        # RF1: A forkedFrom carried over from a prior generation (resume-of-fork or
        # resume-of-resume) must point sessionId at the IMMEDIATE parent, not the
        # grandparent. Native resume rewrites ALL forkedFrom.sessionId to the source.
        # Under the identity map messageUuid needs no remap, but on the legacy remap
        # path re-point it so it doesn't dangle.
        existing_fork = record.get("forkedFrom")
        if isinstance(existing_fork, dict):
            if is_fork_shaped and source_session_id:
                existing_fork["sessionId"] = source_session_id
            mu = existing_fork.get("messageUuid")
            if isinstance(mu, str) and mu in uuid_map:
                existing_fork["messageUuid"] = uuid_map[mu]
        lines.append(_json_line(record))
    return b"".join(lines)


_CLAUDE_PERMISSION_MODES = (
    "default",
    "acceptEdits",
    "plan",
    "auto",
    "dontAsk",
    "bypassPermissions",
)


def _normalize_permission_mode(permission_mode: str | None) -> str | None:
    """Validate against Claude's permissionMode enum, falling back to 'default' (P6-14).

    An unknown mode (provider drift, a typo in captured env) would make Claude
    reject the synthetic/re-pinned record, so coerce anything off-enum to 'default'.
    """
    if not permission_mode:
        return permission_mode
    if permission_mode in _CLAUDE_PERMISSION_MODES:
        return permission_mode
    return "default"


def _ensure_permission_mode_record(
    records: list[dict[str, object]],
    permission_mode: str | None,
    new_session_id: str,
    *,
    has_inherited_prefix: bool = False,
) -> list[dict[str, object]]:
    if not permission_mode:
        return records
    if any(record.get("type") == "permission-mode" for record in records):
        return records
    # P6-14: a native fork-style resume (one that inherits a pre-fork prefix) does
    # NOT carry a synthetic lone permission-mode record, so injecting one diverges
    # from a real fork. Only inject for a normal new-session resume (no inherited
    # prefix — turn 0 at byte 0). The resume-of-resume count-parity path is a
    # normal resume and keeps injecting.
    if has_inherited_prefix:
        return records
    synthetic = {
        "type": "permission-mode",
        "permissionMode": permission_mode,
        "sessionId": new_session_id,
    }
    insert_at = 0
    for idx, record in enumerate(records):
        if record.get("type") == "user":
            insert_at = idx
            break
        insert_at = idx + 1
    return [*records[:insert_at], synthetic, *records[insert_at:]]


def _latest_claude_version(records: list[dict[str, object]]) -> str | None:
    """The most recent CLI `version` appearing in the trajectory (P7-6).

    Records are in chronological order, so the LAST `version` is the newest client
    that wrote this thread. Used to make a resumed transcript carry one uniform
    version like a native session (rather than mixing an inherited prefix's older
    version with the captured turns'). Returns None when no record carries a version.
    """
    latest: str | None = None
    for record in records:
        value = record.get("version")
        if isinstance(value, str) and value:
            latest = value
    return latest


def _claude_project_dir_name(cwd: Path) -> str:
    return str(cwd).replace("/", "-")


def _iter_jsonl_records(data: bytes) -> Iterable[dict[str, object]]:
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _jsonl_records(data: bytes) -> list[dict[str, object]]:
    return list(_iter_jsonl_records(data))


def _json_line(record: dict[str, object]) -> bytes:
    # P7-1: provider transcripts (codex rollouts, claude .jsonl, the codex
    # session_index) are re-serialized through here. Native records preserve
    # INSERTION order (e.g. codex meta payload `id, timestamp, cwd, ...`; claude
    # `type, mode, sessionId`), never alphabetical. `json.loads` already preserves
    # source key order and the synthetic records we build are constructed in native
    # order, so emit WITHOUT sort_keys — alphabetizing every key was a 100%-vs-0%
    # fingerprint distinguishing resumed transcripts from native ones.
    # P8-F3: native rollouts/transcripts serialize with COMPACT separators (`,`/`:`,
    # no spaces). Python's default `(', ', ': ')` injected a space after every comma
    # and colon — another 100%-vs-0% fingerprint (and it shifted every downstream
    # manifest byte offset vs a native file). Emit compact to match native bytes.
    return (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _zulu_now() -> str:
    """RFC3339 UTC timestamp with a `Z` suffix and millisecond precision (P6-4/N3).

    Codex writes `...Z` in both `session_meta.timestamp` and the
    `session_index.jsonl` `updated_at` field; `_now()`'s `+00:00` form would be a
    representation drift from native entries the picker reads.

    N3: native codex timestamps carry 3-digit MILLISECONDS (`…653Z`), but
    `datetime.isoformat()` emits 6-digit microseconds (`…006924Z`) — a 100%-vs-0%
    fingerprint distinguishing resumed records from native ones. Truncate to ms.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


# TS1: native codex re-stamps an inlined fork prefix to a tight BURST of adjacent
# milliseconds (write-time stamping packs ~15–46 records per ms), not one identical
# value. Approximate that density so a resumed prefix is not byte-distinguishable by a
# single-valued timestamp block. Deterministic by record index (no wall-clock in the
# loop — that stays sub-ms on fast interpreters and would both fail to spread and break
# the no-inversion invariant flakily). Records 0..DENSITY-1 share `now` (offset 0), so
# small prefixes keep a single ms exactly like a short native fork.
_TS_BURST_DENSITY = 20


def _bump_zulu_ms(zulu: str, millis: int) -> str:
    """Return a `_zulu_now()`-shaped stamp advanced by `millis` ms (TS1).

    Parses the `...THH:MM:SS.mmmZ` form, adds the offset via datetime (so second/
    minute rollover is correct), and re-emits 3-digit ms with the `Z` suffix. On any
    parse failure (defensive) returns the input unchanged.
    """
    if millis <= 0:
        return zulu
    try:
        base = datetime.strptime(zulu, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return zulu
    bumped = base + timedelta(milliseconds=millis)
    return bumped.strftime("%Y-%m-%dT%H:%M:%S.") + f"{bumped.microsecond // 1000:03d}Z"


def _zulu_now_us() -> str:
    """RFC3339 UTC timestamp matching native codex `session_index.jsonl` precision.

    Native codex `updated_at` is serialized by serde_json/chrono, which formats the
    fractional second at microsecond precision and then strips ALL trailing zeros
    (and omits the fraction entirely when microseconds are zero). Verified against
    the live index: of 296 modern uuidv7 entries, ZERO end in a trailing-zero digit;
    the distribution is 5- and 6-digit fractions only. So `.500000`→`.5Z`,
    `.172580`→`.17258Z`, `.505647`→`.505647Z`, and `.000000`→`Z` (no fraction).

    TS2: the old code always emitted 6 digits, so ~10% of resumed entries (those whose
    microseconds ended in a zero) were a byte-distinguishable fingerprint vs native.
    A "strip one trailing zero" rule would still diverge ~1% AND create a never-native
    `…0Z` shape, so we mirror native exactly with rstrip('0').
    """
    now = datetime.now(timezone.utc)
    fraction = f"{now.microsecond:06d}".rstrip("0")
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{stamp}.{fraction}Z" if fraction else f"{stamp}Z"


def _resume_command(
    provider_name: str,
    new_session_id: str,
    provider_session_path: Path | None = None,
    target_env: object | None = None,
) -> str | None:
    if resume_policy_for_provider(provider_name) is not None:
        return _shell_join(["checkpoint", "resume-open", new_session_id])
    return None


def _provider_runtime_args(policy: ProviderResumePolicy, target_env: object | None) -> list[str]:
    args: list[str] = []
    for field_name, option in policy.runtime_arg_fields:
        value = _string_attr(target_env, field_name)
        if value:
            args.extend([option, value])
    for field_name, option, config_key in policy.runtime_json_config_arg_fields:
        value = _string_attr(target_env, field_name)
        if value:
            args.extend([option, f"{config_key}={json.dumps(value)}"])
    return args


def _string_attr(value: object | None, name: str) -> str | None:
    raw = getattr(value, name, None)
    return raw if isinstance(raw, str) and raw else None


def _shell_join(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def restore_opencode_metadata(import_path: Path, session_id: str) -> tuple[int, int]:
    """Restore OpenCode sidecar metadata ignored by `opencode import`.

    Current OpenCode import JSON only consumes `info` and `messages`. The checkpoint
    exporter keeps session timeline events and todos in ignored sidecar fields, then
    this post-import step inserts them directly into the local SQLite tables.
    """
    try:
        data = json.loads(import_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return (0, 0)
    if not isinstance(data, dict):
        return (0, 0)
    session_messages = _opencode_hook_list(data, "session_messages", "sessionMessages")
    todos = _opencode_hook_list(data, "todos")
    if not session_messages and not todos:
        return (0, 0)
    db_path = _opencode_db_path()
    if not db_path.exists():
        return (0, 0)
    try:
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        try:
            session_message_count = _restore_opencode_session_messages(conn, session_id, session_messages)
            todo_count = _restore_opencode_todos(conn, session_id, todos)
            conn.commit()
            return (session_message_count, todo_count)
        finally:
            conn.close()
    except Exception:
        return (0, 0)


def _opencode_db_path() -> Path:
    data_home = Path(
        os.environ.get("OPENCODE_DATA_DIR")
        or os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    )
    return data_home.expanduser() / "opencode" / "opencode.db"


def _restore_opencode_session_messages(conn: object, session_id: str, messages: list[dict]) -> int:
    if not messages or not _sqlite_table_exists(conn, "session_message"):
        return 0
    count = 0
    for message in messages:
        msg_id = message.get("id")
        msg_type = message.get("type")
        if not isinstance(msg_id, str) or not msg_id or not isinstance(msg_type, str) or not msg_type:
            continue
        time_info = message.get("time") if isinstance(message.get("time"), dict) else {}
        created = _int_or_now(time_info.get("created") if isinstance(time_info, dict) else None)
        updated = _int_or_default(time_info.get("updated") if isinstance(time_info, dict) else None, created)
        conn.execute(
            "INSERT INTO session_message (id, session_id, type, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "session_id = excluded.session_id, type = excluded.type, "
            "time_created = excluded.time_created, time_updated = excluded.time_updated, data = excluded.data",
            (msg_id, session_id, msg_type, created, updated, _opencode_json_text(message.get("data", {}))),
        )
        count += 1
    return count


def _restore_opencode_todos(conn: object, session_id: str, todos: list[dict]) -> int:
    if not todos or not _sqlite_table_exists(conn, "todo"):
        return 0
    count = 0
    for idx, todo in enumerate(todos):
        content = todo.get("content")
        status = todo.get("status")
        priority = todo.get("priority")
        if not all(isinstance(value, str) and value for value in (content, status, priority)):
            continue
        time_info = todo.get("time") if isinstance(todo.get("time"), dict) else {}
        created = _int_or_now(time_info.get("created") if isinstance(time_info, dict) else None)
        updated = _int_or_default(time_info.get("updated") if isinstance(time_info, dict) else None, created)
        position = _int_or_default(todo.get("position"), idx)
        conn.execute(
            "INSERT INTO todo (session_id, content, status, priority, position, time_created, time_updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id, position) DO UPDATE SET "
            "content = excluded.content, status = excluded.status, priority = excluded.priority, "
            "time_created = excluded.time_created, time_updated = excluded.time_updated",
            (session_id, content, status, priority, position, created, updated),
        )
        count += 1
    return count


def _sqlite_table_exists(conn: object, table_name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _opencode_json_text(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, separators=(",", ":"))
    except (TypeError, ValueError):
        return "{}"


def _opencode_runtime_env(target_env: object | None) -> dict[str, str]:
    extra = getattr(target_env, "extra", None)
    if not isinstance(extra, dict):
        return {}
    raw = extra.get("opencode_runtime_env")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not _safe_env_name(key):
            continue
        if key == "OPENCODE_CONFIG_CONTENT":
            continue
        if isinstance(value, (str, int, float, bool)):
            result[key] = str(value)
    return result


def _opencode_config_content(target_env: object | None, *, keep_redacted: bool = False) -> str:
    base = _opencode_config_content_base(target_env, keep_redacted=keep_redacted)
    mcp = _opencode_mcp_overlay(target_env)
    if not base and not mcp:
        return ""
    content = _json_object(base)
    if mcp:
        content = _deep_merge_dicts(content, {"mcp": mcp})
    return json.dumps(content, separators=(",", ":"))


_REDACTED_CONFIG_VALUE = object()
_MISSING_CONFIG_VALUE = object()


def _opencode_config_content_base(target_env: object | None, *, keep_redacted: bool = False) -> object:
    extra = getattr(target_env, "extra", None)
    if not isinstance(extra, dict):
        return {}
    raw = extra.get("opencode_config_content")
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if keep_redacted else _without_redacted_values(data)


def _without_redacted_values(value: object) -> object:
    if value == "***redacted***":
        return _REDACTED_CONFIG_VALUE
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            cleaned = _without_redacted_values(item)
            if cleaned is not _REDACTED_CONFIG_VALUE:
                result[str(key)] = cleaned
        return result
    if isinstance(value, list):
        return [item for item in (_without_redacted_values(item) for item in value) if item is not _REDACTED_CONFIG_VALUE]
    return value


def _load_json_value(path: Path) -> object | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _preserve_redacted_config_values(current: object, wanted: object) -> object:
    if wanted == "***redacted***":
        return wanted if current is _MISSING_CONFIG_VALUE else current
    if isinstance(wanted, dict):
        current_dict = current if isinstance(current, dict) else {}
        return {
            str(key): _preserve_redacted_config_values(current_dict.get(str(key), _MISSING_CONFIG_VALUE), value)
            for key, value in wanted.items()
        }
    if isinstance(wanted, list):
        current_list = current if isinstance(current, list) else []
        return [
            _preserve_redacted_config_values(
                current_list[index] if index < len(current_list) else _MISSING_CONFIG_VALUE,
                value,
            )
            for index, value in enumerate(wanted)
        ]
    return wanted


def _opencode_mcp_overlay(target_env: object | None) -> dict[str, dict[str, bool]]:
    mcp_servers = getattr(target_env, "mcp_servers", None)
    if not isinstance(mcp_servers, dict):
        return {}
    mcp: dict[str, dict[str, bool]] = {}
    for name, status in mcp_servers.items():
        if not isinstance(name, str) or not isinstance(status, str):
            continue
        if status == "active":
            mcp[name] = {"enabled": True}
        elif status == "inactive":
            mcp[name] = {"enabled": False}
    return mcp


def _json_object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _deep_merge_dicts(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    result = dict(base)
    for key, value in overlay.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = _deep_merge_dicts(existing, value)
        else:
            result[key] = value
    return result


def _safe_env_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name))


def _carry_provider_session_state(
    provider_name: str,
    provider_home: Path,
    old_session_id: str,
    new_session_id: str,
    cwd: Path,
    *,
    dest_provider_home: Path | None = None,
) -> None:
    """Reuse the original session's append-only state under the new session id.

    Hardlinks (not copies) so the resumed session forks cleanly: shared baseline
    blobs cost zero extra disk, and any new writes by the resumed session land
    on new inodes without touching the original.
    """
    if provider_name != "claude":
        return
    target_home = dest_provider_home or provider_home
    _hardlink_tree(
        provider_home / "file-history" / old_session_id,
        target_home / "file-history" / new_session_id,
    )
    _hardlink_todos_to(provider_home / "todos", target_home / "todos", old_session_id, new_session_id)
    _carry_claude_subagents(provider_home, old_session_id, new_session_id, cwd, dest_provider_home=target_home)


def _carry_claude_subagents(
    provider_home: Path,
    old_session_id: str,
    new_session_id: str,
    cwd: Path,
    *,
    dest_provider_home: Path | None = None,
) -> None:
    """Carry a session's subagent transcripts to the resumed session (B4).

    Claude stores subagents under `projects/<project>/<session>/subagents/`.
    Carrying them under the new session id lets a resumed run still see the
    subagent context that the parent turn depended on. Each carried record's
    `sessionId` is rewritten to the new parent id (H3) — hardlinking verbatim
    left the content pointing at the OLD parent, so Claude couldn't associate
    the sidechain with the resumed session.

    P11-SUBAGENT-CARRY-1: the destination project_dir must be computed from the
    TARGET cwd (not inherited from the source), so that `--target` resumes place
    subagents where Claude will look for them.
    """
    projects_root = provider_home / "projects"
    if not projects_root.exists() or not projects_root.is_dir():
        return
    dst_projects_root = (dest_provider_home or provider_home) / "projects"
    dst_project_dir = dst_projects_root / _claude_project_dir_name(cwd)
    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir():
            continue
        src = project_dir / old_session_id / "subagents"
        if src.exists() and src.is_dir():
            _carry_subagent_tree(src, dst_project_dir / new_session_id / "subagents", new_session_id, cwd)


def _carry_subagent_tree(src: Path, dst: Path, new_session_id: str, cwd: Path) -> None:
    """Copy a subagent tree, rewriting each record's sessionId and cwd (H3/P6-8).

    A subagent transcript is a self-contained sidechain: its internal
    uuid/parentUuid are independent. Verified against real sidechains:
    `sourceToolAssistantUUID`, where present, is an INTRA-sidechain pointer into the
    subagent file's own uuid namespace (it resolves to a uuid inside the same file,
    never the parent main transcript), so it must NOT be remapped through the
    parent's uuid map — that would be a no-op at best and corrupting at worst. The
    correct carry therefore rewrites `sessionId` (to the new parent id) AND `cwd`
    (every real subagent record carries cwd; a stale cwd would point the resumed
    sidechain at the old working directory), and leaves every other field — all
    uuids included — byte-identical. Non-jsonl entries (rare) keep the cheap
    hardlink/copy path.
    """
    if not src.exists() or not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.rglob("*"):
        target = dst / entry.relative_to(src)
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not entry.is_file() or target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.suffix == ".jsonl":
            try:
                records = _jsonl_records(entry.read_bytes())
            except OSError:
                continue
            for record in records:
                if "sessionId" in record:
                    record["sessionId"] = new_session_id
                if "cwd" in record:
                    record["cwd"] = str(cwd)
            _write_bytes_atomic(target, b"".join(_json_line(record) for record in records))
            continue
        try:
            os.link(entry, target)
        except OSError:
            shutil.copy2(entry, target)


def _hardlink_tree(src: Path, dst: Path) -> None:
    if not src.exists() or not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for entry in src.rglob("*"):
        rel = entry.relative_to(src)
        target = dst / rel
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not entry.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            continue
        try:
            os.link(entry, target)
        except OSError:
            shutil.copy2(entry, target)


def _hardlink_todos_to(src_todos_dir: Path, dst_todos_dir: Path, old_session_id: str, new_session_id: str) -> None:
    if not src_todos_dir.exists() or not src_todos_dir.is_dir():
        return
    dst_todos_dir.mkdir(parents=True, exist_ok=True)
    for entry in src_todos_dir.glob(f"{old_session_id}-*"):
        if not entry.is_file():
            continue
        target = dst_todos_dir / entry.name.replace(old_session_id, new_session_id, 1)
        if target.exists():
            continue
        try:
            os.link(entry, target)
        except OSError:
            shutil.copy2(entry, target)
