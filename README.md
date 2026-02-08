# ArbiterOS-Kernel

## What the LiteLLM Proxy Does

- **Request logging**: `pre_call` writes each request’s `model`, `messages`, and `tools` to `log/api_calls.jsonl`.
- **Response logging**: `post_call_success` writes the **raw** response (full structure including `category` / `content`) to the same jsonl for later analysis.
- **Response transform**: Before returning to the client, if the assistant `content` is a JSON string `{"category":"...","content":"..."}`, only the inner `content` is returned (same for streaming and non-streaming). Messages with `tool_calls` are left unchanged.

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
