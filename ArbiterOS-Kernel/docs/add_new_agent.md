# How to add a new agent

**The main difference between agents is the tool list.** You need to make ArbiterOS understand your new agent’s tool definitions—a new toolset. Follow the same approach we use for nanobot. Concretely, do these things:

## Parsing (registry)

1. **Define a registry** — e.g. `arbiteros_kernel/instruction_parsing/tool_parsers/<agent>.py`, exporting `YOUR_AGENT_TOOL_PARSER_REGISTRY: Dict[str, ToolParser]` (tool name → parser function).
2. **`tool_parsers/__init__.py`** — Import the registry and handle the branch where `get_tool_agent() == "<agent>"` inside `parse_tool_instruction`.
3. **`tool_agent_config.py`** — Add the new agent id to **`_VALID`** (lowercase). Values not in the set are rejected and the runtime falls back to **`openclaw`**.
4. **Runtime selection** — Set `arbiteros_config.tool_agent` in `litellm_config.yaml`, or use the **`ARBITEROS_TOOL_AGENT`** environment variable (overrides YAML).

## Policy

- **`unary_gate.tool_aliases`** (in the policy JSON that is actually loaded, e.g. `policy.json`) — Maps **New tool names** to **canonical names** used in `unary_gate_rules.json` `selector.tool` (OpenClaw-style vocabulary). UnaryGate reads this from `RUNTIME.cfg["unary_gate"]["tool_aliases"]`.

## Do not

Do not change `openclaw.py` or `nanobot.py`, they are examples of how to add an agent.
