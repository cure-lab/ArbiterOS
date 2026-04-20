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
- Runtime normalization (if needed): if your agent either splits one canonical tool into multiple APIs (like browser in hermes) or merges multiple canonical tools into one API, consider adding agent-gated canonicalization in `arbiteros_kernel/policy_runtime.py` and/or `arbiteros_kernel/instruction_parsing/builder.py` so existing policies classify flows consistently.

## Validation method (recommended when adding a new agent)

- Redteam compatibility: decide whether to create `redteam/case_<agent>/`, generate mapping/manifest files under `redteam/_automation/`, and explicitly document why unmappable cases are dropped.
- Regression runs (A/B/C): run `agent native cases`, `openclaw matched subset`, and `mismatched parser` checks to confirm expected gaps and catch parser-policy mismatches early.
- Ops/docs hygiene: document how to switch `tool_agent` back after experiments (in litellm_config.json), and update the new-agent onboarding doc so future runs can reproduce the same setup.

