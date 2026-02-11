# ArbiterOS-Kernel

## What the LiteLLM Proxy Does

- **Request logging**: `pre_call` writes each request’s `model`, `messages`, and `tools` to `log/api_calls.jsonl`.
- **Response logging**: `post_call_success` writes the **raw** response (full structure including `category` / `content`) to the same jsonl for later analysis.
- **Response transform**: Before returning to the client, if the assistant `content` is a JSON string `{"category":"...","content":"..."}`, only the inner `content` is returned (same for streaming and non-streaming). Messages with `tool_calls` are left unchanged.
- **Live observability**: successful/failed LiteLLM calls are sent to both `mlflow` and `langfuse` callbacks (when Langfuse env vars are configured).

Configured in `litellm_config.yaml` ; Kernel's key logic lives in `_response_transform_content_only` in `arbiteros_kernel/litellm_callback.py`.

## Setup and Run

**1 Requirements**: Python 3.12+, [uv](https://docs.astral.sh/uv/).

```bash
# Enter the project
cd ArbiterOS-Kernel

# Install dependencies (creates .venv and installs poe task runner)
uv sync --group dev
```

**2 Set Config**: Edit `litellm_config.yaml` to add your models. Each entry under `model_list` should specify:

- **`model_name`**: ID exposed to clients (used in OpenClaw as `models[].id`)
- **`litellm_params.model`**: LiteLLM format, e.g. `openai/gpt-5.2`
- **`litellm_params.api_key`**: Your API key for the upstream provider
- **`litellm_params.api_base`**:  API base URL

**3 Run**:

```bash
uv run poe litellm
```

Proxy URL: [http://localhost:4000](http://localhost:4000). Send client requests there to use this proxy with the logging and kernel above.

## Local Langfuse Setup (for Visualization)

### 1) Start local Langfuse

Use your local `langfuse` repo:

```bash
cd langfuse
pnpm run infra:dev:up
pnpm run dev
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

Edit `.env` with real keys. Or export directly in the terminal where you run LiteLLM:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_HOST="http://localhost:3000"
```

Then start the proxy:

```bash
cd ArbiterOS-Kernel
uv run poe litellm
```

## Replay Existing `api_calls.jsonl` into Langfuse

`ArbiterOS-Kernel` includes a replay utility at `arbiteros_kernel/langfuse_replay.py` to import historical JSONL logs.

### Dry run (parse + pair only, no upload)

```bash
cd ArbiterOS-Kernel
uv run poe langfuse_replay -- --dry-run
```

### Replay upload

```bash
cd ArbiterOS-Kernel
uv run poe langfuse_replay
```

You can also pass a custom file path:

```bash
uv run python -m arbiteros_kernel.langfuse_replay --input /path/to/api_calls.jsonl
```

The command prints JSON counters including `paired_calls`, `orphan_pre_calls`, and `orphan_post_calls`.

## How JSONL Maps to Langfuse

- `pre_call` + next `post_call_success` are paired into one Langfuse generation (`arbiteros_kernel.call`).
- `pre_call` without a following `post_call_success` is imported as event `arbiteros_kernel.orphan_pre_call`.
- `post_call_success` without a preceding `pre_call` is imported as event `arbiteros_kernel.orphan_post_call_success`.
- Each imported generation/event includes metadata pointing back to original JSONL line numbers and timestamps.

## Verify It Works

1. Start Langfuse locally and export Langfuse env vars.
2. Run `uv run poe litellm` and send one test request through the proxy.
3. Open Langfuse UI (`localhost:3000`) and confirm a new generation appears.
4. Run `uv run poe langfuse_replay -- --dry-run` to validate pairing.
5. Run `uv run poe langfuse_replay` to import history and confirm additional traces/events are visible.

### Apply your ArbiterOS Kernel on OpenClaw

You need [OpenClaw](https://docs.openclaw.ai/) installed first. Then add a local provider in your `openclaw.json` that points at the proxy. See [Model Providers – Local proxies (LM Studio, vLLM, LiteLLM, etc.)](https://docs.openclaw.ai/concepts/model-providers#local-proxies-lm-studio-vllm-litellm-etc).

1. Ensure the proxy is running (`uv run poe litellm`) and the model you want is defined in `litellm_config.yaml`.
2. In `openclaw.json`, under `models.providers`, add a provider with `baseUrl: "http://127.0.0.1:4000/v1"` and list the proxy’s model IDs in `models` (use the `model_name` from `litellm_config.yaml`, e.g. `gpt-5.2`).
3. Set `agents.defaults.model.primary` to `"<providerId>/<modelId>"` (e.g. `arbiteros/gpt-5.2` if the provider key is `arbiteros`).
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
            "id": "gpt-5.2",
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
      "model": { "primary": "arbiteros/gpt-5.2" }
    }
  }
}
```
