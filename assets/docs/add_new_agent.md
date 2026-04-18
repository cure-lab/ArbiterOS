# How to add a new agent

Scope: teach the kernel to support a new tool name set (→ `instruction_type` / `security_type`). Not about changing the LLM model (LiteLLM / provider config).

## What you do

Same layout as `nanobot.py` / `hermes.py`: new registry module + wire it in. For each tool in that agent’s list: if it matches an OpenClaw tool or behavior (read/write/exec/browser/…), add a parser that yields the same `instruction_type` / `security_type` patterns as `openclaw.py`; if it doesn’t map cleanly, don’t register it and let the default fallback apply (`EXEC` + conservative `UNKNOWN` security).

## What to change


| Place                                          | Change                                                                                             |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `instruction_parsing/tool_parsers/<agent>.py`  | New file: export `<AGENT>_TOOL_PARSER_REGISTRY`.                                                   |
| `instruction_parsing/tool_parsers/__init__.py` | Import registry; in `parse_tool_instruction`, branch on `get_tool_agent() == "<agent>"`.           |
| `instruction_parsing/tool_agent_config.py`     | Add `"<agent>"` to `_VALID`.                                                                       |
| `litellm_config.yaml` or env                   | `arbiteros_config.tool_agent: <agent>` or `ARBITEROS_TOOL_AGENT` (env overrides YAML).             |
| Loaded policy JSON (e.g. `policy.json`)        | `unary_gate.tool_aliases`: map your API tool names → OpenClaw-style names for UnaryGate selectors. |


## Notes

- Parsers are `.py` only; `openclaw.json` is not used by `parse_tool_instruction`.
- Don’t refactor `openclaw.py` / `nanobot.py` / `hermes.py` to add an agent—add a new file and hook it up.

