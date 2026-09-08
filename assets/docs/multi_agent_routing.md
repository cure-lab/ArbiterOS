# Multi-Agent Routing

One ArbiterOS Kernel can govern **multiple agent types in parallel** (e.g. Codex + Claude Code + OpenClaw). Each request declares its agent type in the `model` field; you do not restart the Kernel or edit a global `tool_agent` when switching clients.

## Request format

Clients send the OpenAI `model` field as:

```text
{route_model};{agent_name};{role}
```

| Segment | Required | Meaning |
|---------|----------|---------|
| `route_model` | yes | Must match `model_list[].model_name` in `litellm_config.yaml` |
| `agent_name` | yes | `openclaw` \| `nanobot` \| `hermes` \| `codex` \| `claude_code` \| `pi` |
| `role` | no | Policy role for this request only (third segment) |

Bare model names (without `;agent_name`) are rejected.

Examples:

```text
gpt-5.5;codex
gpt-5.2-chat-latest;openclaw
claude-sonnet-4-5-20250929;claude_code
gpt-5.6-terra;pi
gpt-5.5;codex;planner
```

### Per-request flow

1. Kernel strips `model` to `route_model` for LiteLLM upstream routing.
2. Kernel loads `agents/{agent_name}.yaml` (tool parser, sidecar, upstream_compat).
3. Optional `role` selects policy config for that request only.

## Configure `litellm_config.yaml`

Copy from `ArbiterOS-Kernel/litellm_config.yaml.example`. Holds **model routes** and **global Kernel settings**:

| Section | Purpose |
|---------|---------|
| `model_list` | `model_name` = client `route_model`; `litellm_params.model` = upstream provider id |
| `response_format` | Global `instruction_output` schema (shared by all agents) |
| `litellm_settings` | LiteLLM callbacks, `drop_params`, etc. |

```yaml
model_list:
  - model_name: gpt-5.5
    litellm_params:
      model: gpt-5.5
      api_key: os.environ/ARBITEROS_UPSTREAM_API_KEY
      api_base: https://your-provider.example/v1
  - model_name: claude-sonnet-4-5-20250929
    litellm_params:
      model: anthropic/claude-sonnet-4-5-20250929
      api_key: os.environ/ARBITEROS_UPSTREAM_API_KEY
      api_base: https://your-provider.example/v1
```

Different agents may share the same upstream model (same `litellm_params.model`, different `;agent_name` in the client).

## Configure `agents/*.yaml`

Per-agent behavior under `ArbiterOS-Kernel/agents/`:

| File | `agent_name` |
|------|--------------|
| `openclaw.yaml` | `openclaw` |
| `nanobot.yaml` | `nanobot` |
| `hermes.yaml` | `hermes` |
| `codex.yaml` | `codex` |
| `claude_code.yaml` | `claude_code` |
| `pi.yaml` | `pi` |

```yaml
agent_name: claude_code
depends_on_sidecar:
  enabled: true
upstream_compat:
  enabled: true
  rules:
    - match_model: "gpt-5.5"
      strip_metadata: true
      force_non_stream: true
```

- `depends_on_sidecar` — optional LLM pass for plain-text RESPOND `depends_on`
- `upstream_compat` — per-upstream-model tweaks (metadata strip, non-stream, etc.)

Do not set a global `arbiteros_config.tool_agent` in `litellm_config.yaml`; use request `agent_name` instead.

## Client examples

| Runtime | Example `model` |
|---------|-----------------|
| Codex | `gpt-5.5;codex` |
| OpenClaw | `gpt-5.2-chat-latest;openclaw` |
| Claude Code | `claude-sonnet-4-5-20250929;claude_code` |
| pi | `gpt-5.6-terra;pi` |
| With policy role | `gpt-5.5;codex;my_role` |

Base URL: `http://127.0.0.1:4000/v1`

## Parallel traces

- **Different agent types**: different `;agent_name` on the same Kernel.
- **Multiple instances of one agent**: each runtime’s session signals (Codex `prompt_cache_key`, Claude session id, pi Responses API `session_id`, OpenClaw `message_id`, etc.). Traces: `ArbiterOS-Kernel/log/instruction/{trace_id}.json`.

See also [`kernel.md`](./kernel.md) (session / `device_key`).
