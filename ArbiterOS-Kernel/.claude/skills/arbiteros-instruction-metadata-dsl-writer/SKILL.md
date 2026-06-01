---
name: arbiteros-instruction-metadata-dsl-writer
description: >
  Guides users through creating, validating, and registering a new ArbiterOS instruction-metadata DSL YAML file.
  Use this skill whenever a user wants to add a new instruction metadata parser for ArbiterOS, write a DSL for a new agent
  type, classify instruction calls for a new agent integration, add support for a new agent's instructions to ArbiterOS,
  or validate an existing DSL file against the instruction_parsers schema. Also trigger for phrases like "add new
  agent parser", "create DSL for X agent", "register instruction parsers", "write instruction_parsers yaml", or when
  the user describes instructions from an agent that ArbiterOS doesn't know about yet.
---

# ArbiterOS DSL Writer

This skill creates a validated, registered ArbiterOS tool-parser DSL YAML file from scratch. It gathers
context, drafts the YAML, validates it, tests it with pytest, and registers it — without guessing.

## What this skill does

ArbiterOS classifies every tool call an agent makes into an `instruction_type` (READ, WRITE, EXEC, etc.)
and security metadata (confidentiality, trustworthiness, risk, reversible). The DSL is a YAML file that
encodes the parsing rules for one agent's tool set. This skill walks the user through producing that file.

## Phase 1 — Gather the agent tool schema

Ask the user to provide their **agent tool schema** file — an OpenAI-compatible JSON array of tool
definitions (e.g. `tool_schemas/openclaw.json`). This is the ground truth for which tools exist and what
arguments they accept. The schema reveals:

- Every tool name (maps to a DSL `tool:` entry)
- Each tool's argument names and types (used to target `arg:` fields in passes)

If the user doesn't have such a file, ask them to describe the tools manually (name + key args).

## Phase 2 — Gather domain requirements

After reading the schema, ask:

1. **What does each tool do semantically?** (Read data? Write state? Execute side effects? Delegate? Interact with user?)
   This determines the baseline `instruction_type` for each tool.

2. **Are there action-dispatched tools?** Many tools have an `action` argument that changes the
   semantics (e.g. `action: list` → READ, `action: delete` → EXEC). For each such tool, ask which
   action values map to which instruction types.

3. **Custom metadata needed?** For domain-specific context (e.g. a finance agent: `action ∈ {transfer, refund, trade}`),
   ask whether any tool calls should carry extra `custom:` key-value pairs in the result — fields beyond
   the four standard metadata fields (`confidentiality`, `trustworthiness`, `risk`, `reversible`).

4. **What are the security properties?**
   - Which tools read untrusted external data (low trustworthiness)?
   - Which tools touch sensitive data (high confidentiality)?
   - Which tools have irreversible side effects (`reversible: false`)?
   - Which tools are known safe/read-only (`risk: LOW`) vs destructive (`risk: HIGH`)?

5. **Does any tool operate on file paths?** If so, those args should use a `path` pass so the
   registry auto-classifies confidentiality and trustworthiness.

6. **Does any tool execute shell commands?** Use a `shell` pass for the command arg.

Consolidate answers before proceeding. Re-ask anything that is still ambiguous.

## Phase 3 — Draft the DSL YAML

Write the new YAML file to:

```
arbiteros_kernel/instruction_parsing/tool_parsers/dsl/<agent_name>.yaml
```

### DSL structure quick reference

```yaml
- tool: <tool_name>
  passes:
    # 1. Always start with a default pass (baseline)
    - match_type: default
      result:
        instruction_type: <EXEC|READ|WRITE|...>
        metadata:
          confidentiality: <HIGH|LOW|UNKNOWN>
          trustworthiness: <HIGH|LOW|UNKNOWN>
          risk: <HIGH|LOW|UNKNOWN> # optional
          reversible: <true|false>

    # 2. Path pass: auto-classify a file/URL arg via the registry
    - match_type: path
      arg: <arg_name> # e.g. "path", "file_path", "url"

    # 3. Regex pass: override result based on arg value pattern
    - match_type: regex
      cases:
        - arg: <arg_name> # dot-notation ok: "request.kind"
          pattern: "^(list|get)$"
          result:
            instruction_type: READ
            metadata:
              reversible: true

    # 4. Numeric pass: override based on numeric arg comparison
    - match_type: numeric
      cases:
        - arg: amount
          op: gt # eq, neq, lt, lte, gt, gte
          value: 10000
          result:
            custom:
              high_value_transfer: true

    # 5. Shell pass: delegate command analysis to the shell parser
    - match_type: shell
      arg: command
```

**Pass execution order:** default → regex/numeric/path/shell (in YAML order). Each matching pass
deep-merges into the accumulated result — later passes win on overlapping fields.

**Design principles:**

- Every tool entry must have at least one `default` pass.
- Use `path` passes for any arg that carries a file system path or URL.
- Use `shell` for any arg that is a raw shell command string.
- Use `custom:` for domain-specific metadata that has no standard field (e.g. `{action_kind: transfer}`).
- Worst-case wins for trustworthiness (LOW contaminates); highest wins for confidentiality (HIGH dominates).

## Phase 4 — Validate the DSL

After writing the YAML, validate it against the schema:

```bash
cd <repo_root>
python - <<'EOF'
import yaml, sys
from arbiteros_kernel.instruction_parsing.tool_parsers.validator import validate
defs = yaml.safe_load(open("arbiteros_kernel/instruction_parsing/tool_parsers/dsl/<agent_name>.yaml"))
validate(defs, "<agent_name>.yaml")
print("Validation passed")
EOF
```

Fix any errors reported and re-validate until it passes.

## Phase 5 — Test loop (subagent-driven)

Generate a pytest file at:

```
tests/instruction_parsing/tool_parsers/test_<agent_name>_dsl.py
```

Each test case should:

1. Load the YAML via `engine.load_registry()`
2. Call `registry[<tool_name>](<args_dict>)`
3. Assert `result.instruction_type == <expected>`
4. Assert `result.security_type["reversible"] == <expected>`
5. Assert custom metadata fields if applicable

Derive test cases directly from the requirements gathered in Phase 2 — one test per distinct
behaviour the user described. Parametrize where it simplifies things.

**Test–fix loop:**

Spawn a subagent with this instruction:

```
Run: pytest tests/instruction_parsing/tool_parsers/test_<agent_name>_dsl.py -v
Report back: which tests passed, which failed, and the full failure output for each failure.
```

Read the subagent's report. For each failure:

- Understand why the DSL produces a different result than expected.
- Edit the YAML to fix the gap.
- Re-validate (Phase 4).
- Re-spawn the subagent.

Repeat until all tests pass.

## Phase 6 — Register the new agent

Open `arbiteros_kernel/instruction_parsing/tool_agent_config.py`.

1. Add the new agent name to `_VALID`:

   ```python
   _VALID = frozenset({"openclaw", "nanobot", "hermes", "<agent_name>"})
   ```

2. Update the `_DEFAULT_AGENT` only if appropriate (usually leave it as-is).

Confirm that `get_tool_agent()` can now return the new name.

## Completion checklist

- [ ] DSL YAML written to `dsl/<agent_name>.yaml`
- [ ] Schema validation passes (Phase 4)
- [ ] All pytest cases pass (Phase 5)
- [ ] `tool_agent_config.py` updated with the new name

Report to the user once all items are checked.
