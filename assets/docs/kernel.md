# ArbiterOS Kernel Architecture

## Supported Agents and Models

ArbiterOS Kernel supports agents that can **customize the LLM URL and API key** when making LLM calls. The agent can override these per request, enabling flexible routing and provider switching.

**Currently known supported models**:


| Provider | Models                                                                                                                                            |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| GPT      | gpt-5.2, gpt-5.2-chat-latest, gpt-5.1, gpt-5.1-chat-latest, gpt-5, gpt-5-mini, gpt-5-nano, gpt-5-chat-latest, gpt-4.1, gpt-4.1-mini, gpt-4.1-nano |
| O-series | o4-mini, o3, o3-mini, o1                                                                                                                          |
| GLM      | GLM series                                                                                                                                        |


Note: some older models do not reliably support `response_format` (`json_schema`) and `tool_call` in the same turn. If an agent keeps chatting and never calls tools, remove the `response_format` block from `ArbiterOS-Kernel/litellm_config.yaml` to restore tool-calling behavior.

---

## 1. Overview

ArbiterOS Kernel is an Agent protection and monitoring layer that runs on top of the LiteLLM proxy. It instructionizes model responses across the full Agent LLM request/response lifecycle, enforces policy protection, monitors execution, and visualizes various runtime information:

- **Request preprocessing**: Message truncation, category+topic wrapping, Trace management, `[ARBITEROS_REF]` watermark injection, `depends_on` schema hints
- **Response postprocessing**: Category+topic stripping, instruction parsing, causal `depends_on` resolution, policy protection (with optional Yes/No confirmation)
- **Observability**: Langfuse tracing, log persistence (`log/instruction/{trace_id}.json`)

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
│  • Response format merge + depends_on schema injection       │
│  • Category wrapping                                         │
│  • Sync context instructions (system/user)                     │
│  • Inject [ARBITEROS_REF] watermarks into messages           │
│  • Emit new TOOLRESULT instructions (deduped)                │
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
│  • Instruction Parsing + depends_on resolution               │
│  • Policy validation & Confirmation                          │
│  • Record instructions                                       │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
  Return to Agent
```

---

## 3. Code Architecture


| Module                      | Responsibility                                                                               |
| --------------------------- | -------------------------------------------------------------------------------------------- |
| `litellm_callback.py`       | Hook implementation, Trace state, Langfuse emission, instruction parsing, ref injection      |
| `instruction_depends_on.py` | `[ARBITEROS_REF]` markers, `depends_on` normalization/resolution, tool-result dedupe helpers |
| `depends_on_sidecar.py`     | Optional sidecar LLM pass to fill `depends_on` for plain-text `RESPOND` steps                |
| `instruction_parsing/`      | InstructionBuilder, Instruction schema, registry, path-based taint labels                    |
| `policy_check.py`           | Policy orchestration entry point, optional taint ablation before policies                    |
| `user_approval.py`          | Pre-policy copy: elevate trust for user-approved blocked tool calls                          |
| `taint_ablation.py`         | Pre-policy copy: optional disable of propagated `prop_*` for ablation study                  |
| `policy/`                   | Policy implementations                                                                       |


---

## 4. Configuration (`litellm_config.yaml`)

The Kernel reads the LiteLLM config file (typically `ArbiterOS-Kernel/litellm_config.yaml`, or path from `ARBITEROS_LITELLM_CONFIG`). Common blocks:


| Block                          | Purpose                                                                                                                                                                                                |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `model_list`                   | LiteLLM model aliases and upstream routing (`litellm_params`)                                                                                                                                          |
| `litellm_settings`             | e.g. `callbacks: arbiteros_kernel.litellm_callback.proxy_handler_instance`                                                                                                                             |
| `model_list[].response_format` | Kernel `json_schema` includes `depends_on` (array of `{instruction_id, confidence, counterfactual}`); allowed ids are injected per request                                                             |
| `arbiteros_config`             | e.g. `tool_agent: openclaw`; `depends_on_sidecar.enabled` for optional RESPOND lineage pass; `upstream_compat` for Codex/Claude Code                                                                   |
| `arbiteros_skill_trust`        | `skills_root` for skill package trust (optional)                                                                                                                                                       |
| `skill_scanner_llm`            | `model` / `api_base` / `api_key` for LLM-assisted scanners                                                                                                                                             |
| `session_bootstrap_scan`       | Optional: scan listed `protected_paths` once per trace before the first pure-text assistant reply; append notice if any file is judged unsafe                                                          |
| `taint_ablation`               | `disable_inheritance`: when `true`, policy input uses `prop_trustworthiness`/`prop_confidentiality` equal to base levels (deep copy only; does not change persisted `log/instruction/{trace_id}.json`) |
| `precall_log_enabled`          | When `true`, append each final pre-call payload to `log/precall/{trace_id}.json`                                                                                                                       |


---

## 5. Receiving Agent Requests (Pre-Call)

1. **Policy confirmation detection**: If the last assistant message contains the confirmation suffix, detect Yes/No via `_detect_policy_confirmation_reply(messages)`. When detected, return `mock_response` (bypass LLM call) and skip the remaining pre-call steps for the actual request.
2. **Resolve Trace ID**: Determine the trace_id for this request.
  - `_ensure_trace_state()` when reset requested or new device; `_resolve_trace_state_from_metadata()` when metadata contains `arbiteros_trace_id` / `arbiteros_device_key`.
3. **Response format merge**: Merge the agent’s `response_format` (if present) into the request content.
  - `_merge_agent_response_format_into_content()`.
4. `**depends_on` schema injection**: Extend the structured-output schema with allowed prior instruction ids for this trace.
  - `_inject_depends_on_schema_into_response_format()`.
5. **Category wrapping**: Wrap assistant history with `topic/category/content/depends_on` using previously stripped categories for this Trace.
  - `_wrap_messages_with_categories()`, `_wrap_request_with_categories()`.
6. **Context instruction sync**: Register system and user messages as first-class instructions (once per `context_key`).
  - `_sync_context_instructions_for_trace()` via `_inject_ref_markers_into_messages()`.
7. `**[ARBITEROS_REF]` watermark injection**: Prefix conversation messages with `[ARBITEROS_REF id=<uuid> kind=…]` so the model can cite prior steps in `depends_on`.
  - `_inject_ref_markers_into_messages()`, `_inject_ref_markers_into_responses_input()`.
8. **Tool result instruction emit (deduped)**: Scan request history for `role: "tool"` (or Responses `function_call_output`) and append **new** `TOOLRESULT` instructions only.
  - `_emit_tool_result_nodes_if_needed()`; skips when `builder_has_tool_result_for_call_id()` or `_should_emit_tool_result_once()` already recorded the `tool_call_id`.
9. **Topic hint**: Inject the previous turn’s topic summary into the prompt.
  - `_inject_topic_summary_hint()`.
10. **Logging**: Write to `log/precall.jsonl`, optional `log/precall/{trace_id}.json`, and `log/api_calls.jsonl`.
  - `_save_precall_to_log()`, `_save_json()`.
11. **Inject metadata & forward**: Add `arbiteros_trace_id` and `arbiteros_device_key` to request metadata, then return data for LiteLLM to forward.
  - `_inject_trace_metadata()`.

---

## 6. Responding to Agent Requests (Post-Call)

1. **Response extraction**: Supports both Chat Completions (`.choices`) and Responses API (`output_text`).
  - Inline in `async_post_call_success_hook`; uses `_to_json()` for message dict.
2. **Response Transform**: For responses in `{topic, category, content}` format, strip the outer wrapper, keep only `content`, and record the stripped category for later pre-call wrapping.
  - `_response_transform_content_only()` (assigned to `response_transform`).
3. **Instruction Parsing**: Map instructions to `instruction_type` and `instruction_category` based on category / tool call type; resolve model-declared `depends_on` refs to instruction-bound metadata.
  - `builder.add_from_tool_call()` for tool calls; `builder.add_from_structured_output()` for content; `_set_instruction_depends_on()` / `_apply_respond_text_depends_on()` for lineage.
  - Optional: `depends_on_sidecar` (`arbiteros_config.depends_on_sidecar.enabled: true`) runs a follow-up LLM pass for plain-text `RESPOND` steps when the main model omitted `depends_on`.
4. **Policy validation**:
  - In `litellm_callback.py`, build policy inputs with `apply_user_approval_preprocessing()` (copy + optional `prop`_* elevation for user-approved flows).
  - In `check_response_policy()`: if `taint_ablation.disable_inheritance` is `true`, `apply_taint_inheritance_ablation_for_policy()` deep-copies and aligns `prop`_* to base levels for that run only; then run all registered policies.
  - If any policy modifies the response, the Kernel may enter **Policy Confirmation** (Yes/No) instead of returning the protected output directly.
  - Taint ablation does **not** rewrite persisted `log/instruction/{trace_id}.json`; it only affects in-memory arguments to `Policy.check()`.
5. **Record instructions**: Write instructions to `log/instruction/{trace_id}.json`.
  - `_save_instructions_to_trace_file()`.
6. **Session bootstrap scan** (optional): On the first pure-text assistant message of a trace, if `session_bootstrap_scan.enabled` is true and `protected_paths` is non-empty, the Kernel may call an LLM to classify listed files; if any are judged unsafe, a notice is appended to that reply. Failures are fail-open (treat as safe). Configured in `litellm_config.yaml`; uses `skill_scanner_llm` for the HTTP client settings.
7. **Pending observe-only policy warnings (written into the assistant reply)**: Policies with `**enabled: false`** in `policy_registry.json` do not replace the model output; their “would have blocked” text is aggregated into `**PolicyCheckResult.inactivate_error_type**` and, in `litellm_callback.py`, **queued** per trace (`pending_warning_texts`). When a later assistant message is **non-empty plain text** and has **no** `tool_calls` / `function_call`, and the turn is **not** a policy-confirmation “ask”, the kernel **appends** a block to that message’s `**content`**: a fixed Chinese preamble (`_PENDING_WARNINGS_APPEND_PREAMBLE`) plus lines `**warning1；…**`, `**warning2；…**`, … for the queued strings, then **clears** the queue. Tool-only turns do not flush the queue (warnings keep accumulating until a suitable text reply). This mutates only the **copy returned to the agent** (Langfuse can still reflect pre-append content). See `**docs/kernel-policy_interface.md`** (registry `enabled`, post-call pipeline) for how this ties to `check_response_policy`.

---

## 7. Instruction Parsing

### 7.1 InstructionBuilder

`InstructionBuilder` unifies LLM output, tool calls, and context messages (system/user) into an Instruction list, maintained per trace and written to `log/instruction/{trace_id}.json`.

**Main methods**:

- `**add_from_structured_output()`**: Maps `{intent, content}` to `instruction_type` and `instruction_category` (e.g. REASON, PLAN, RESPOND).
- `**add_from_tool_call()**`: Records tool name, `tool_call_id`, arguments, and optional result; retrieves predefined type and security attributes.
- `**add_from_context_message()**`: Registers system prompt or user input as instructions with stable `context_key` (`system:0`, `user:0`, …).

### 7.2 Instruction Schema

Instructions are JSON objects appended by `InstructionBuilder` and serialized under `log/instruction/{trace_id}.json`.

**Core fields** (always present for a committed instruction):


| Field                  | Description                                                                                                                                                                      |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                   | Unique id (UUID string). Used in `[ARBITEROS_REF id=…]` watermarks and `depends_on.instruction_id`.                                                                              |
| `content`              | For cognitive steps: plain text or structured payload. For tools: object with `tool_name`, `tool_call_id`, `arguments` (agent-facing params only), and optionally `result` after the tool returns. **`depends_on` is not stored inside `arguments`** — see below. |
| `runtime_step`         | Integer step index within the trace.                                                                                                                                             |
| `parent_id`            | Id of the parent instruction, or `null`.                                                                                                                                         |
| `source_message_id`    | Id tying this instruction to the originating assistant message, or `null`.                                                                                                       |
| `security_type`        | Object; see **Security type** below.                                                                                                                                             |
| `rule_types`           | Array of rule or tag hints from parsing (may be empty).                                                                                                                          |
| `instruction_category` | High-level category string (e.g. `EXECUTION.Env`).                                                                                                                               |
| `instruction_type`     | Atomic type (e.g. `READ`, `RESPOND`, `EXEC`, `SYSTEMPROMPT`, `USERINPUT`).                                                                                                       |
| `arbiteros_ref_kind`   | Ref marker kind: `SYSTEMPROMPT`, `USERINPUT`, `TOOLCALL`, `TOOLRESULT`, or `LLMOUTPUT`.                                                                                          |
| `context_key`          | Stable key for context messages (`system:0`, `user:1`, …); omitted for model/tool steps.                                                                                         |


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


**Optional lineage field** — causal dependencies on prior instructions:


| Field        | Description                                                                                                                                                                                                                                                                                                                                     |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `depends_on` | Array of resolved dependency edges on the **instruction object** (not inside `content.arguments`). Each entry includes `instruction_id`, `ref`, `ref_type` (`instruction_id`), `source` (`model` / `kernel` / `sidecar`), `confidence` (0–1), and `counterfactual`. Use `[]` when the step has no direct causal predecessors. Omitted on context-only steps (`SYSTEMPROMPT`, `USERINPUT`) unless explicitly set. Kernel auto-fills tool-result → tool-call edges with `source: "kernel"`. |


**Optional policy-related fields** — the kernel may add these on specific instructions. They are **omitted** when not applicable; they are not set to `false` as a placeholder.


| Field                     | Description                                                                                                       |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `policy_protected`        | String: reason when this step is tied to a policy block or violation on the tool result path.                     |
| `policy_confirmation_ask` | Boolean `true` on the instruction that carries the Yes/No policy confirmation prompt.                             |
| `user_approved`           | Boolean `true` when the user chose to proceed (e.g. Yes) and the kernel marked affected instructions as approved. |


**Example** (tool call as persisted in `log/instruction/{trace_id}.json`). The model may return `depends_on` inside tool arguments on the wire; the kernel strips it before save and stores the resolved edges on the instruction root. The fenced block uses JSON with Comments (`jsonc`).

```jsonc
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "content": {
    "tool_name": "read",
    "tool_call_id": "call_abc123",
    "arguments": {
      "path": "/absolute/path/to/file.txt"
    }
  },
  "runtime_step": 11,
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
  "arbiteros_ref_kind": "TOOLCALL",
  "depends_on": [{
    "instruction_id": "c8834406-a5bc-436a-8f50-e940a4d30cf7",
    "ref": "c8834406-a5bc-436a-8f50-e940a4d30cf7",
    "ref_type": "instruction_id",
    "source": "model",
    "confidence": 0.9,
    "counterfactual": "Without the user's request to read this file, I would not perform this read."
  }],
  "token_usage": {
    "llm_call_seq": 6,
    "model": "gpt-5.2",
    "turn_index": 10,
    "prompt_tokens": 937,
    "completion_tokens": 219,
    "total_tokens": 1156
  }
  // Optional policy keys below — omit entirely when not set (do not emit false as a placeholder):
  // "policy_protected": "…",
  // "policy_confirmation_ask": true,
  // "user_approved": true
}
```

`policy_protected` is a **string** (violation or block reason). `policy_confirmation_ask` and `user_approved` are **booleans** when present.


### 7.3 `[ARBITEROS_REF]` watermarks and `depends_on` flow

1. **Record**: Each committed instruction gets a UUID (`id`) and an `arbiteros_ref_kind`.
2. **Inject (pre-call)**: Before the LLM call, the kernel prefixes the matching conversation slot with
  `[ARBITEROS_REF id=<uuid> kind=SYSTEMPROMPT|USERINPUT|TOOLCALL|TOOLRESULT|LLMOUTPUT]`.
3. **Constrain (pre-call)**: `_inject_depends_on_schema_into_response_format()` lists allowed prior `instruction_id` values in the structured-output schema so the model cannot cite unknown ids.
4. **Declare (model)**: Structured assistant output or tool `arguments` include `depends_on: [{ instruction_id, confidence, counterfactual }, …]`.
5. **Strip (post-call)**: `_strip_and_record_tool_depends_on_in_arguments()` removes `depends_on` from tool arguments returned to the agent and from the copy stored in `content.arguments`.
6. **Resolve (post-call)**: `_set_instruction_depends_on()` normalizes declarations into the persisted `depends_on` array on each new instruction.
7. **Sidecar (optional)**: When `depends_on_sidecar.enabled` is true, plain-text `RESPOND` steps without model-declared deps may receive a second internal LLM pass (`source: "sidecar"`).

**TOOLRESULT deduplication**: Agents that replay full chat history (e.g. OpenHands) resend all prior `role: "tool"` messages on every turn. `_emit_tool_result_nodes_if_needed()` scans that history but emits at most **one** `TOOLRESULT` instruction per `tool_call_id` per trace (`builder_has_tool_result_for_call_id` + `_should_emit_tool_result_once`).

### 7.4 Instruction Types and Categories


| instruction_type                 | instruction_category |
| -------------------------------- | -------------------- |
| SYSTEMPROMPT                     | EXECUTION.Env        |
| USERINPUT                        | EXECUTION.Human      |
| REASON, PLAN, CRITIQUE           | COGNITIVE.Reasoning  |
| STORE, RETRIEVE, COMPRESS, PRUNE | MEMORY.Management    |
| READ, WRITE, EXEC, WAIT          | EXECUTION.Env        |
| ASK, RESPOND, USER_MESSAGE       | EXECUTION.Human      |
| HANDOFF                          | EXECUTION.Agent      |
| SUBSCRIBE, RECEIVE               | EXECUTION.Perception |


Tool parsers (e.g. OpenClaw `read` / nanobot `read_file`) assign `security_type` using path rules (`instruction_parsing/tool_parsers/linux_registry`) and optional skill-scanner trust. Workspace memory filenames (e.g. `SOUL.md`) are matched case-sensitively; registry patterns use absolute paths for reliable classification.

---

## 8. Traces, sessions, and parallel runs

### How traces are chosen

Each request is assigned to a trace via `**device_key`** (`channel:user_id`), persisted in `log/trace_state.json`. The kernel does **not** allocate one trace per HTTP request.

Typical session signals (when present on the request):


| Source                | Signal                                                                        | Effect                                                                        |
| --------------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Codex / Responses API | `prompt_cache_key`                                                            | One Codex exec session → one trace                                            |
| Claude Code           | Session id in request metadata / headers                                      | One CLI session → one trace                                                   |
| OpenClaw / others     | Channel + conversation labels in messages, or `metadata.arbiteros_device_key` | Per-agent session when the client provides identity                           |
| Bare API              | None of the above                                                             | Falls back to a shared **anonymous** identity → requests merge into one trace |


### Parallel workloads

- **Supported pattern:** run **multiple full agent sessions** in parallel (e.g. several Codex or Claude Code processes). Each session carries its own identity → separate `trace_id` and `log/instruction/{trace_id}.json`.
- **Unsupported / ambiguous pattern:** many parallel **bare** proxy calls (scripts, `curl`, load tests) with only `model` + `input`. The kernel cannot tell whether those are independent jobs or one client; **default behavior is to merge into one anonymous trace**. Alternatives (one trace per request) would explode trace file count and are not the default.

### Explicit metadata (advanced)

Custom clients may set LiteLLM request `metadata`:

- `arbiteros_device_key` — stable session identity (`channel:user_id` form).
- `arbiteros_trace_id` — continue an existing trace when valid for that device.

Full agents normally do not need this; bare API integrators use it only when they must partition traces themselves.

---

## 9. Logging

Various runtime information is recorded in the following files:


| File                              | Purpose                                                               |
| --------------------------------- | --------------------------------------------------------------------- |
| `log/api_calls.jsonl`             | Raw post-call content of LLM responses                                |
| `log/precall.jsonl`               | Global append-only log of final pre-call payloads (when enabled)      |
| `log/precall/{trace_id}.json`     | Per-trace pre-call payload history (`precall_log_enabled: true`)      |
| `log/langfuse_nodes.jsonl`        | Langfuse node logs (for debugging)                                    |
| `log/trace_state.json`            | Persisted Trace state                                                 |
| `log/instruction/{trace_id}.json` | Instruction trajectory per trace (core output; includes `depends_on`) |


