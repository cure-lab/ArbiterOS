# ArbiterOS-Kernel

## What the LiteLLM Proxy Does

- **Request logging**: `pre_call` writes each request’s `model`, `messages`, and `tools` to `log/api_calls.jsonl`.
- **Response logging**: `post_call_success` writes the **raw** response (full structure including `category` / `content`) to the same jsonl for later analysis.
- **Response transform**: Before returning to the client, if the assistant `content` is a JSON string `{"category":"...","content":"..."}`, only the inner `content` is returned (same for streaming and non-streaming). Messages with `tool_calls` are left unchanged.
- **Policy confirmation**: When a policy blocks a response (e.g. blocks a tool call), the kernel returns a confirmation message and waits for the user to reply **Yes** or **No**. On the next request, pre-call detects the reply and returns the protected response (Yes) or original response (No) without calling the LLM. See [`../assets/docs/kernel.md`](../assets/docs/kernel.md) for details.
- **Live observability**: MLflow logging uses LiteLLM’s `mlflow` callbacks; Langfuse session tracing is emitted by `arbiteros_kernel.litellm_callback` when `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` are set (auto-loaded from `.env`). Langfuse nodes are also persisted to `log/langfuse_nodes.jsonl` for replay.
- **Instruction parse & registry**: For each trace, the kernel generates `log/{trace_id}.json` to parse and register the original LLM input/output. The `InstructionBuilder` (from `arbiteros_kernel.instruction_parsing`) converts:
  - **LLM structured output**: When the assistant returns `{"category":"...","content":"..."}`, it is parsed into an instruction with `instruction_category`, `instruction_type`, and `content`.
  - **Tool calls**: Each `tool_call` (name + arguments) from the assistant response is recorded; when tool results arrive, they are merged into the same instruction.
  - **Tool results**: Tool role messages with results are associated with their corresponding tool-call instructions.
  Each `log/{trace_id}.json` contains `trace_id`, `created_at`, and an `instructions` array. Instructions include `id`, `content`, `runtime_step`, `parent_id`, `source_message_id`, `security_type`, `rule_types`, `instruction_category`, and `instruction_type`. This enables downstream analysis and replay of the parsed instruction flow.

Configured in `litellm_config.yaml` ; Kernel's key logic lives in `_response_transform_content_only` in `arbiteros_kernel/litellm_callback.py`.

## Traces, sessions, and parallel runs

Kernel traces are keyed by **session identity** (`device_key`), not by individual HTTP requests. Instructions land in `log/instruction/{trace_id}.json` according to that identity.

- **Full agents** (Codex, Claude Code, OpenClaw, etc.) usually send session signals the kernel can read (e.g. Codex `prompt_cache_key`, Claude Code session metadata). One agent process → one trace; **run several agent sessions in parallel** → several traces.
- **Bare API calls** (only `model` + `messages` / `input`, with no session metadata) cannot be split into “many parallel jobs” vs “one client sending many requests”. The kernel **defaults to merging them into one anonymous trace**. That is intentional for high-volume bare API traffic (avoid hundreds of one-off trace files).

**If you parallelize from the command line:** start **multiple full agent sessions** (separate Codex/Claude Code/OpenClaw processes), not many parallel bare proxy calls. For custom clients, you may pass `metadata.arbiteros_device_key` or `metadata.arbiteros_trace_id` on each request to control grouping; see [`../assets/docs/kernel.md`](../assets/docs/kernel.md) for details.

## Said / Done defender (PreToolUse)

Kernel records Gateway-declared `TOOLCALL`s and a session→trace index under `log/said_done/`. The ArbiterOS defender hook (`hooks/defender/`) auto-allows PreToolUse actions that match a pending declaration on the same trace; unknown or mismatched actions escalate to ArbiterOS TUI (`attach <trace_id>` then Y=deny / N=allow), with optional `hooks/defender/watch.py` fallback. Codex and Claude Code share the same hook. See [`hooks/defender/README.md`](hooks/defender/README.md).

## Setup and Run

**1 Requirements**: Python 3.12+, [uv](https://docs.astral.sh/uv/).

```bash
# Enter the project
cd ArbiterOS-Kernel

# Install dependencies (creates .venv and installs poe task runner)
uv sync --group dev
```

**2 Set Config**: Copy `litellm_config.yaml.example` to `litellm_config.yaml` and add upstream models under `model_list`. Each entry:

- **`model_name`**: route name clients use as the first segment of `model` (e.g. `gpt-5.5` in `gpt-5.5;codex`)
- **`litellm_params.model`**: LiteLLM upstream id, e.g. `openai/gpt-5.2`
- **`litellm_params.api_key`** / **`api_base`**: upstream credentials

**Multi-agent**: clients must send `model` as `{route_model};{agent_name}` (optional `;role`). Per-agent behavior is in `agents/*.yaml`. See [`../assets/docs/multi_agent_routing.md`](../assets/docs/multi_agent_routing.md).

**Skill trust (optional)** — When classifying path trustworthiness, the kernel can run [cisco-ai-skill-scanner](https://pypi.org/project/cisco-ai-skill-scanner/) to analyze scan the skill before use them if you finish this following 3 steps.

1. **Point at your skills directory** — In `litellm_config.yaml`, set the parent of each skill package (the `skills` folder):
  ```yaml
   arbiteros_skill_trust:
     skills_root: /path/to/openclaw/skills
  ```
2. **Optional LLM analyzer** — To pass `--use-llm` to the scanner, add all three under `skill_scanner_llm` in `litellm_config.yaml`: `model`, `api_base`, `api_key`. If any is missing, scans use static + behavioral analyzers only.
3. **Cache** — Results are stored in `~/.arbiteros/instruction_parsing/linux_registry/skill_trust_by_name.json`

**3 Run**:

```bash
# Recommended product shell (starts Kernel if needed, then opens ArbiterOS TUI)
uv run poe arbiteros

# Original proxy-only mode (unchanged)
uv run poe litellm
```

- `poe arbiteros` **owns** the Kernel proxy: it frees `:4000` if needed, starts the proxy, then opens the shell (`list` / `attach <trace_id>` / `quit`). When you `quit`, the proxy is stopped too — so `running` / `offline` always match this session.
- Original proxy-only mode remains: `uv run poe litellm`. To attach a TUI without owning that process: `uv run python -m arbiteros_kernel.tui --no-start-proxy`.
- Proxy logs from the shell launcher go to `log/proxy.log` so the foreground stays clean.

Proxy URL: [http://localhost:4000](http://localhost:4000). Send client requests there to use this proxy with the logging and kernel above.

## Local Langfuse Setup (for Visualization)

### 1) Start local Langfuse

Use your local `langfuse` repo:

```bash
cd langfuse
pnpm run infra:dev:up
pnpm i
```

**First-time setup**:

1. **Initialize the database:**
  ```bash
   pnpm --filter=shared run db:deploy
  ```
2. **Create ClickHouse dev tables.** Choose one:
  - **With ClickHouse CLI:** `pnpm --filter=shared run ch:dev-tables`
  - **Without ClickHouse CLI (manual):** From the langfuse repo root:
    ```bash
    sed -n '70,688p' packages/shared/clickhouse/scripts/dev-tables.sh | \
    docker exec -i langfuse-clickhouse clickhouse-client --user clickhouse --password clickhouse --database default --multiquery

    sed -n '700,824p' packages/shared/clickhouse/scripts/dev-tables.sh | \
    docker exec -i langfuse-clickhouse clickhouse-client --user clickhouse --password clickhouse --database default --multiquery
    ```
3. **Start the dev server:**
  ```bash
   pnpm run dev:web
  ```

Langfuse UI: [http://localhost:3000](http://localhost:3000)

### 2) Create project API keys in Langfuse UI

In Langfuse UI, create (or open) a project and generate:

- `LANGFUSE_PUBLIC_KEY` (starts with `pk-lf-`)
- `LANGFUSE_SECRET_KEY` (starts with `sk-lf-`)

### 3) Configure env vars before running ArbiterOS-Kernel

Create a local env file:

```bash
cd ArbiterOS-Kernel
cp .env.example .env
```

Edit `.env` with real keys. `arbiteros_kernel.litellm_callback` and `arbiteros_kernel.langfuse_replay` both auto-load `.env` (so you don’t need to `export` manually), but exporting works too:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="http://localhost:3000"
# export LANGFUSE_HOST="http://localhost:3000" # Backward-compatible alias
```

Optional tuning knobs:

```bash
export ARBITEROS_LANGFUSE_TIMEOUT="15"
export ARBITEROS_LANGFUSE_FLUSH_AT="1"
export ARBITEROS_LANGFUSE_FLUSH_INTERVAL="1"
```

Then start the proxy:

```bash
cd ArbiterOS-Kernel
uv run poe litellm
```

## Verify It Works

1. Start Langfuse locally and set Langfuse env vars (in `.env` or exported).
2. Run `uv run poe litellm` and send one test request through the proxy.
3. Confirm `log/langfuse_nodes.jsonl` is being written (it logs nodes even if Langfuse keys are missing).
4. Open Langfuse UI (`localhost:3000`) and confirm new observations appear (requires Langfuse keys).
5. Run `uv run poe langfuse_replay -- --dry-run` to validate parsing/counters.
6. Run `uv run poe langfuse_replay` to import history and confirm additional traces/observations are visible.

### Apply your ArbiterOS Kernel on OpenClaw

You need [OpenClaw](https://docs.openclaw.ai/) installed first. Then add a local provider in your `openclaw.json` that points at the proxy. See [Model Providers – Local proxies (LM Studio, vLLM, LiteLLM, etc.)](https://docs.openclaw.ai/concepts/model-providers#local-proxies-lm-studio-vllm-litellm-etc).

1. Ensure the proxy is running (`uv run poe litellm`) and the model route is in `litellm_config.yaml`.
2. In `openclaw.json`, under `models.providers`, add a provider with `baseUrl: "http://127.0.0.1:4000/v1"` and list model IDs as `{route_model};openclaw` (e.g. `gpt-5.2;openclaw`).
3. Set `agents.defaults.model.primary` to `"<providerId>/<modelId>"` (e.g. `arbiteros/gpt-5.2;openclaw`).
4. After configuration, restart OpenClaw. In the UI, set **Model/auth provider** to **Skip for now** and **Filter models by provider** to `arbiteros` (or whatever provider name you used in `openclaw.json`).

Example snippet (full example: `config_example/openclaw.json`):

```json
{
  "models": {
    "providers": {
      "arbiteros": {
        "baseUrl": "http://127.0.0.1:4000/v1",
        "apiKey": "n/a",
        "api": "openai-completions",
        "authHeader": false,
        "models": [
          {
            "id": "gpt-5.2;openclaw",
            "name": "GPT-5.2",
            "reasoning": false,
            "input": ["text"],
            "cost": {
              "input": 0,
              "output": 0,
              "cacheRead": 0,
              "cacheWrite": 0
            },
            "contextWindow": 200000,
            "maxTokens": 8192,
            "compat": {
              "supportsStore": false
            }
          }
        ]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": { "primary": "arbiteros/gpt-5.2;openclaw" }
    }
  }
}
```

