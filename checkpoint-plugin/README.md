# checkpoint-plugin

**Turn-boundary checkpointing for Claude Code, Codex, OpenCode, and similar agent CLIs.**

Automatically saves your agent's state at each turn: **provider environment, project files, and conversation trajectory**. Restore to a previous checkpoint with a diff preview and automatic backups, then open a native resumed session with an isolated provider home.

## Quick Start

```bash
# Install
pip install -e .

# Set up hooks (Claude Code, Codex, and OpenCode)
checkpoint hooks install

# Restart your agent, then verify
checkpoint list
```

## Features

- **Automatic turn checkpoints** — Claude Code, Codex, and OpenCode hooks save on session start, turn stop, and subagent stop.
- **State bundles** — each turn points at an environment snapshot, a filesystem snapshot, and a byte range of the provider transcript.
- **Diff-first restore** — `checkpoint resume` shows environment and filesystem changes before writing anything.
- **Safe restore modes** — restore in place or into a new workspace copy; changed files are backed up under the plugin home.
- **Native resumed sessions** — resume writes provider-native session artifacts, then `checkpoint resume-open <new-session-id>` launches the right provider command.
- **Isolated provider homes** — resumed Claude Code, Codex, and OpenCode config is written under `~/.checkpoint-plugin/env-state/<new-session-id>/` instead of your live provider home.
- **Fork and subagent lineage** — fork/resume metadata is preserved, and subagent runs are captured as derived sessions with parent links.
- **Secret-aware capture** — credential-shaped files are skipped, and secret-looking values in JSON/TOML config are redacted before blob storage.

## Configuration

**Storage location**: `~/.checkpoint-plugin/` (override with `CHECKPOINT_PLUGIN_HOME`)

**Hook management**:

```bash
checkpoint hooks install            # All providers (Claude Code, Codex, and OpenCode)
checkpoint hooks install claude     # Claude Code only
checkpoint hooks install codex      # Codex only
checkpoint hooks install opencode   # OpenCode only
checkpoint hooks uninstall          # remove all hooks
checkpoint hooks uninstall claude   # remove Claude Code hooks only
checkpoint hooks uninstall codex    # remove Codex hooks only
checkpoint hooks uninstall opencode # remove OpenCode plugin
```

Claude Code and Codex are installed by editing JSON hook files:

- Claude Code: `~/.claude/settings.json`
- Codex: `$CODEX_HOME/hooks.json` or `~/.codex/hooks.json`

OpenCode is installed by copying the TypeScript plugin to `$OPENCODE_HOME/plugins/checkpoint.ts` or `~/.config/opencode/plugins/checkpoint.ts`.

**Manual hook setup**: See [integrations/settings.example.json](integrations/settings.example.json) (Claude Code), [integrations/codex-settings.example.json](integrations/codex-settings.example.json) (Codex), or [integrations/opencode-plugin.example.ts](integrations/opencode-plugin.example.ts) (OpenCode).

## How It Works

Each turn, the plugin captures:

- **Environment** — provider name, model, permission/mode/effort hints, memory files, MCP config/status, skills, plugin metadata, settings, and project context.
- **Filesystem** — project files below the working directory, excluding `.git`, `node_modules`, virtualenv/build output, `.env*`, credential-like files, files over 10 MB, configured `exclude_patterns`, and ignored `.gitignore` entries.
- **Trajectory** — provider transcript byte ranges for Claude Code and Codex; OpenCode hook payloads and raw message data are stored in the turn metadata so resume can build an import file.

Manifests are written under a per-session file lock. Content is stored as SHA-256 blobs inside that session, so repeated file contents are deduplicated within the session.

On restore, the plugin shows a summary diff (enter `d` for detailed diffs), creates backups, restores the workspace and provider environment into the selected target, and writes a native provider session plus a `resume-open.json` launcher spec. Reopen it with the printed `checkpoint resume-open <new-session-id>` command.

## Common Commands

```bash
# Open the interactive session browser
checkpoint

# List sessions (hides empty sessions by default)
checkpoint list
checkpoint list --all                    # show all sessions including empty ones
checkpoint list --session <session-id>   # list turns for a specific session

# Inspect a checkpoint
checkpoint show <session-id>             # show session overview with all turns
checkpoint show <session-id> <turn>      # show specific turn details
checkpoint show <session-id> --metadata-only  # quick metadata check
checkpoint diff <session-id> <turn>      # preview restore changes

# Restore (shows diff + confirmation prompt)
checkpoint resume <session-id> <turn>
checkpoint resume <session-id> <turn> --yes  # skip confirmation
checkpoint resume <session-id> <turn> --target /absolute/path

# Open the newly restored provider session shown by resume
checkpoint resume-open <new-session-id>

# Cleanup
checkpoint clean --empty                 # remove empty/incomplete sessions
checkpoint clean --empty --dry-run       # preview what would be removed
checkpoint clean --keep-last 100         # keep only last N turns per session

# Manual checkpoint (automatic via hooks in normal use)
checkpoint save --session <session-id> --note "description"

# View or modify config
checkpoint config get .
checkpoint config set key value
```

### Interactive browser

`checkpoint` (no arguments) opens a terminal session browser grouped by provider. Navigate with arrow keys or vim bindings (`h`/`j`/`k`/`l`), `Enter` to expand, `/` to run a command (`/show`, `/diff`, `/resume`, `/quit`). `r` and `d` are shortcuts for resume and diff. When output is not a terminal, it prints a plain-text tree.

### Resume workflow

`checkpoint resume` shows a summary diff and prompts:

```text
y = restore checkpoint
n = cancel
d = view detailed environment and filesystem diffs
```

After confirming, choose to restore in place or into a new folder copy. Copy mode first copies your current workspace to the chosen target and applies the checkpoint there, leaving your current workspace untouched. `--target /absolute/path` plans and restores directly against that target path.

Resume also prints:

- `Provider session` — the native Claude Code, Codex, or OpenCode session artifact
- `Env state` — the copied provider config/env directory under `~/.checkpoint-plugin/env-state/<new-session-id>/`
- `Resume with` — a short opener command, for example `checkpoint resume-open ses_abc123`

`resume-open` loads that env-state copy and launches:

- Codex: `CODEX_HOME=<env-state>/codex codex resume ... <new-session-id>`
- Claude Code: `CLAUDE_CONFIG_DIR=<env-state>/claude CLAUDE_HOME=<env-state>/claude claude ... --resume <new-session-id>`
- OpenCode: imports the generated JSON, restores OpenCode metadata, then runs `opencode --session <new-session-id>`

Restored model/MCP/config state does not overwrite your live provider home. Auth-like local files such as `auth.json`, `credentials.json`, `oauth.json`, `.env`, and Claude's sibling `.claude.json` are copied into the isolated env-state when present, but credential-shaped files are not stored in checkpoint blobs.

## Storage Layout

Checkpoints live in `~/.checkpoint-plugin/sessions/<session-id>/` with:

- `metadata.json` — session metadata (provider, source, parent lineage, timestamps)
- `manifests/` — per-turn manifests plus `index.json`
- `env-snapshots/` — human-readable grouped environment snapshots, regenerated from manifests
- `blobs/` — content-addressed environment JSON, filesystem JSON, file contents, and fork-point trajectory data
- `trajectory.jsonl` — legacy/manual trajectory storage and copied OpenCode resume timelines
- `resume-open.json` — present on resumed sessions; stores the validated launcher command/env
- `.checkpoint.lock` — per-session write lock

Filesystem snapshots are JSON blobs: each snapshot records the original `cwd`, optional git state, and a map of relative file paths to content blob hashes. Fork/resume sessions may store `fork_point_trajectory_ref` blobs so inherited transcript prefixes survive parent transcript rewrites.

Other plugin state:

- `~/.checkpoint-plugin/backups/` — per-resume backups of files that were changed or deleted
- `~/.checkpoint-plugin/env-state/<new-session-id>/` — isolated provider home/config used by `resume-open`
- `~/.checkpoint-plugin/config.json` — plugin config, created from defaults on first use

## Troubleshooting

**Empty checkpoint list?**

1. Run `checkpoint hooks install` and restart your agent
2. Start a new session and send a prompt
3. Verify with `checkpoint list --all`

By default, `checkpoint list` hides empty or incomplete sessions. `--all` shows them, including subagent shells marked `[no capture]` when no sidechain transcript was available.

**Cannot resume a subagent?**

Subagent checkpoints are captured for audit/history, but they are not faithful standalone entry points. `checkpoint resume` refuses standalone subagent resumes and prints the parent session/turn to resume instead.

## Development

```bash
# Run tests
pytest tests

# Run from source (no install)
PYTHONPATH=src python3 -m checkpoint_plugin.cli --help

# Uninstall
pip uninstall checkpoint-plugin
```

## Extending

- **New providers**: Add to `src/checkpoint_plugin/env/providers.py`
- **New integrations**: Create an adapter in `src/checkpoint_plugin/integrations/` that calls `CheckpointCoordinator.on_session_start()` and `CheckpointCoordinator.on_turn_end()`

See [src/checkpoint_plugin/](src/checkpoint_plugin/) for architecture details.
