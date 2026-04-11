# ArbiterOS Kernel Architecture

## Supported Agents and Models

ArbiterOS Kernel supports agents that can **customize the LLM URL and API key** when making LLM calls. The agent can override these per request, enabling flexible routing and provider switching.

**Currently known supported models**:


| Provider | Models                                                                                                                                            |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| GPT      | gpt-5.2, gpt-5.2-chat-latest, gpt-5.1, gpt-5.1-chat-latest, gpt-5, gpt-5-mini, gpt-5-nano, gpt-5-chat-latest, gpt-4.1, gpt-4.1-mini, gpt-4.1-nano |
| O-series | o4-mini, o3, o3-mini, o1                                                                                                                          |
| GLM      | GLM series                                                                                                                                        |


---

## 1. Overview

ArbiterOS Kernel is an Agent protection and monitoring layer that runs on top of the LiteLLM proxy. It instructionizes model responses across the full Agent LLM request/response lifecycle, enforces policy protection, monitors execution, and visualizes various runtime information:

- **Request preprocessing**: Message truncation, category+topic wrapping, Trace management
- **Response postprocessing**: Category+topic stripping, instruction parsing, policy protection (with optional Yes/No confirmation)
- **Observability**: Langfuse tracing, log persistence

---

## 2. Summary: Overall Flow

```
LiteLLM Request
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  Pre-Call                                                    │
│  • Policy confirmation (Yes/No) detection                    │
│  • Resolve Trace ID                                          │
│  • Response format merge                                     │
│  • Category wrapping                                         │ 
│  • Topic hint                                                │
│  • Logging                                                   │
│  • Inject metadata                                           │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
  LLM API Call
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  Post-Call                                                   │
│  • Response extraction                                       │
│  • Response Transform                                        │
│  • Instruction Parsing                                       │
│  • Policy validation & Confirmation                          │
│  • Record instructions                                       │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
  Return to Agent
```

---

## 3. Code Architecture


| Module                 | Responsibility                                                              |
| ---------------------- | --------------------------------------------------------------------------- |
| `litellm_callback.py`  | Hook implementation, Trace state, Langfuse emission, instruction parsing    |
| `instruction_parsing/` | InstructionBuilder, Instruction schema, registry, path-based taint labels   |
| `policy_check.py`      | Policy orchestration entry point, optional taint ablation before policies   |
| `user_approval.py`     | Pre-policy copy: elevate trust for user-approved blocked tool calls         |
| `taint_ablation.py`    | Pre-policy copy: optional disable of propagated `prop_*` for ablation study |
| `policy/`              | Policy implementations                                                      |


---

## 4. Configuration (`litellm_config.yaml`)

The Kernel reads the LiteLLM config file (typically `ArbiterOS-Kernel/litellm_config.yaml`, or path from `ARBITEROS_LITELLM_CONFIG`). Common blocks:


| Block                    | Purpose                                                                                                                                                                                    |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `model_list`             | LiteLLM model aliases and upstream routing (`litellm_params`)                                                                                                                              |
| `litellm_settings`       | e.g. `callbacks: arbiteros_kernel.litellm_callback.proxy_handler_instance`                                                                                                                 |
| `arbiteros_config`       | e.g. `tool_agent: openclaw                                                                                                                                                                 |
| `arbiteros_skill_trust`  | `skills_root` for skill package trust (optional)                                                                                                                                           |
| `skill_scanner_llm`      | `model` / `api_base` / `api_key` for LLM-assisted scanners                                                                                                                                 |
| `session_bootstrap_scan` | Optional: scan listed `protected_paths` once per trace before the first pure-text assistant reply; append notice if any file is judged unsafe                                              |
| `taint_ablation`         | `disable_inheritance`: when `true`, policy input uses `prop_trustworthiness`/`prop_confidentiality` equal to base levels (deep copy only; does not change persisted `log/{trace_id}.json`) |


---

## 5. Receiving Agent Requests (Pre-Call)

1. **Policy confirmation detection**: If the last assistant message contains the confirmation suffix, detect Yes/No via `_detect_policy_confirmation_reply(messages)`. When detected, return `mock_response` (bypass LLM call) and skip steps 2–6 for the actual request.
2. **Resolve Trace ID**: Determine the trace_id for this request.
  - `_ensure_trace_state()` when reset requested or new device; `_resolve_trace_state_from_metadata()` when metadata contains `arbiteros_trace_id` / `arbiteros_device_key`.
3. **Response format merge**: Merge the agent’s `response_format` (if present) into the request content.
  - `_merge_agent_response_format_into_content()`.
4. **Category wrapping**: Wrap assistant history with `topic/category/content` using previously stripped categories for this Trace.
  - `_wrap_messages_with_categories()`.
5. **Topic hint**: Inject the previous turn’s topic summary into the prompt.
  - `_inject_topic_summary_hint()`.
6. **Logging**: Write to `log/precall.jsonl` and `log/api_calls.jsonl`.
  - `_save_precall_to_log()`, `_save_json()`.
7. **Inject metadata & forward**: Add `arbiteros_trace_id` and `arbiteros_device_key` to request metadata, then return data for LiteLLM to forward.
  - `_inject_trace_metadata()`.

---

## 6. Responding to Agent Requests (Post-Call)

1. **Response extraction**: Supports both Chat Completions (`.choices`) and Responses API (`output_text`).
  - Inline in `async_post_call_success_hook`; uses `_to_json()` for message dict.
2. **Response Transform**: For responses in `{topic, category, content}` format, strip the outer wrapper, keep only `content`, and record the stripped category for later pre-call wrapping.
  - `_response_transform_content_only()` (assigned to `response_transform`).
3. **Instruction Parsing**: Map instructions to `instruction_type` and `instruction_category` based on category / tool call type.
  - `builder.add_from_tool_call()` for tool calls; `builder.add_from_structured_output()` for content (inside `_response_transform_content_only` and `_add_instruction_for_non_strict`).
4. **Policy validation**:
  - In `litellm_callback.py`, build policy inputs with `apply_user_approval_preprocessing()` (copy + optional `prop`_* elevation for user-approved flows).
  - In `check_response_policy()`: if `taint_ablation.disable_inheritance` is `true`, `apply_taint_inheritance_ablation_for_policy()` deep-copies and aligns `prop`_* to base levels for that run only; then run all registered policies.
  - If any policy modifies the response, the Kernel may enter **Policy Confirmation** (Yes/No) instead of returning the protected output directly.
  - Taint ablation does **not** rewrite persisted `log/{trace_id}.json`; it only affects in-memory arguments to `Policy.check()`.
5. **Record instructions**: Write instructions to `log/{trace_id}.json`.
  - `_save_instructions_to_trace_file()`.
6. **Session bootstrap scan** (optional): On the first pure-text assistant message of a trace, if `session_bootstrap_scan.enabled` is true and `protected_paths` is non-empty, the Kernel may call an LLM to classify listed files; if any are judged unsafe, a notice is appended to that reply. Failures are fail-open (treat as safe). Configured in `litellm_config.yaml`; uses `skill_scanner_llm` for the HTTP client settings.

---

## 7. Instruction Parsing

### 7.1 InstructionBuilder

`InstructionBuilder` unifies LLM output and tool calls into an Instruction list, maintained per trace and written to `log/{trace_id}.json`.

**Main methods**:

- `**add_from_structured_output()`**: Maps `{intent, content}` to `instruction_type` and `instruction_category` (e.g. REASON, PLAN, RESPOND).
- `**add_from_tool_call()`**: Records tool name, `tool_call_id`, arguments, and optional result; retrieves predefined type and security attributes.

### 7.2 Instruction Schema

Instructions are JSON objects appended by `InstructionBuilder` and serialized under `log/{trace_id}.json`.

**Core fields** (always present for a committed instruction):


| Field                  | Description                                                                                                                                                                                                                                                                                                                                                  |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `id`                   | Unique id (UUID string).                                                                                                                                                                                                                                                                                                                                     |
| `content`              | For cognitive steps: structured or text payload from the model. For tools: object with `tool_name`, `tool_call_id`, `arguments`, and optionally `result` after the tool returns. In `arguments`, `reference_tool_id` is a **string array** of upstream `tool_call_id` values this call depends on (taint / approval); use `[]` when there are no references. |
| `runtime_step`         | Integer step index within the trace.                                                                                                                                                                                                                                                                                                                         |
| `parent_id`            | Id of the parent instruction, or `null`.                                                                                                                                                                                                                                                                                                                     |
| `source_message_id`    | Id tying this instruction to the originating assistant message, or `null`.                                                                                                                                                                                                                                                                                   |
| `security_type`        | Object; see **Security type** below.                                                                                                                                                                                                                                                                                                                         |
| `rule_types`           | Array of rule or tag hints from parsing (may be empty).                                                                                                                                                                                                                                                                                                      |
| `instruction_category` | High-level category string (e.g. `EXECUTION.Env`).                                                                                                                                                                                                                                                                                                           |
| `instruction_type`     | Atomic type (e.g. `READ`, `RESPOND`, `EXEC`).                                                                                                                                                                                                                                                                                                                |


**Security type** (`security_type` object). Base levels come from tool parsers or defaults; `prop_`* fields are filled in `_commit` using propagated taint (`compute_prop_taint_for_instruction`).


| Key                    | Description                                       |
| ---------------------- | ------------------------------------------------- |
| `confidentiality`      | `LOW` / `HIGH` / `UNKNOWN`                        |
| `trustworthiness`      | `LOW` / `HIGH` / `UNKNOWN`                        |
| `prop_confidentiality` | Propagated confidentiality for this instruction.  |
| `prop_trustworthiness` | Propagated trustworthiness for this instruction.  |
| `confidence`           | Often `UNKNOWN`.                                  |
| `reversible`           | Boolean.                                          |
| `authority`            | Authority level string (e.g. `UNKNOWN`).          |
| `risk`                 | e.g. `LOW`, `HIGH`, `UNKNOWN`.                    |
| `custom`               | Optional object (e.g. exec parse metadata, tags). |


**Optional policy-related fields** — the kernel may add these on specific instructions. They are **omitted** when not applicable; they are not set to `false` as a placeholder.


| Field                     | Description                                                                                                       |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `policy_protected`        | String: reason when this step is tied to a policy block or violation on the tool result path.                     |
| `policy_confirmation_ask` | Boolean `true` on the instruction that carries the Yes/No policy confirmation prompt.                             |
| `user_approved`           | Boolean `true` when the user chose to proceed (e.g. Yes) and the kernel marked affected instructions as approved. |


**Example** (tool call after `security_type` and `prop_`* are committed). The fenced block uses JSON with Comments (`jsonc`) so a line comment is valid; plain `json` parsers would reject the `//` line.

```jsonc
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "content": {
    "tool_name": "read",
    "tool_call_id": "call_abc123",
    "arguments": {
      "path": "/absolute/path/to/file.txt",
      "reference_tool_id": []
    }
  },
  "runtime_step": 2,
  "parent_id": "e65985a7-6b0d-4e8c-ba04-fb7b6163d9be",
  "source_message_id": "e65985a7-6b0d-4e8c-ba04-fb7b6163d9be",
  "security_type": {
    "confidentiality": "UNKNOWN",
    "trustworthiness": "HIGH",
    "prop_confidentiality": "HIGH",
    "prop_trustworthiness": "LOW",
    "confidence": "UNKNOWN",
    "reversible": true,
    "authority": "UNKNOWN",
    "risk": "LOW",
    "custom": {}
  },
  "rule_types": [],
  "instruction_category": "EXECUTION.Env",
  "instruction_type": "READ",
  // Optional: the three keys below are included only when the kernel sets them; otherwise omit them entirely (do not emit false as a placeholder).
  "policy_protected": "…reason string if this step was under a policy block…",
  "policy_confirmation_ask": true,
  "user_approved": true
}
```

`policy_protected` is a **string** (violation or block reason). `policy_confirmation_ask` and `user_approved` are **booleans** when present.

`reference_tool_id` must be a JSON **array** of strings (never a single string). Use `[]` when there is no upstream dependency; when this tool call depends on prior tool calls (e.g. for taint propagation or user-approval elevation), list their `tool_call_id` values, e.g. `["call_xyz789"]`.

### 7.3 Instruction Types and Categories


| instruction_type                 | instruction_category |
| -------------------------------- | -------------------- |
| REASON, PLAN, CRITIQUE           | COGNITIVE.Reasoning  |
| STORE, RETRIEVE, COMPRESS, PRUNE | MEMORY.Management    |
| READ, WRITE, EXEC, WAIT          | EXECUTION.Env        |
| ASK, RESPOND, USER_MESSAGE       | EXECUTION.Human      |
| HANDOFF                          | EXECUTION.Agent      |
| SUBSCRIBE, RECEIVE               | EXECUTION.Perception |


Tool parsers (e.g. OpenClaw `read` / nanobot `read_file`) assign `security_type` using path rules (`instruction_parsing/tool_parsers/linux_registry`) and optional skill-scanner trust. Workspace memory filenames (e.g. `SOUL.md`) are matched case-sensitively; registry patterns use absolute paths for reliable classification.

---

## 8. Logging

Various runtime information is recorded in the following files:


| File                       | Purpose                                                                  |
| -------------------------- | ------------------------------------------------------------------------ |
| `log/api_calls.jsonl`      | Raw post-call content of LLM responses                                   |
| `log/precall.jsonl`        | Final pre-call content sent to the LLM                                   |
| `log/langfuse_nodes.jsonl` | Langfuse node logs (for debugging)                                       |
| `log/trace_state.json`     | Persisted Trace state                                                    |
| `log/{trace_id}.json`      | Instruction information per Trace (core output with highest readability) |


