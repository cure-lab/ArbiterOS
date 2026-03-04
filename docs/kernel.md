# ArbiterOS Kernel Architecture

## Supported Agents and Models

ArbiterOS Kernel supports agents that can **customize the LLM URL and API key** when making LLM calls. (Openclaw, Nanobot, ...)

**Currently known supported models**:

| Provider | Models |
| -------- | ------ |
| GPT | gpt-5.2, gpt-5.2-chat-latest, gpt-5.1, gpt-5.1-chat-latest, gpt-5, gpt-5-mini, gpt-5-nano, gpt-5-chat-latest, gpt-4.1, gpt-4.1-mini, gpt-4.1-nano |
| O-series | o4-mini, o3, o3-mini, o1 |
| GLM | GLM series |

---

## 1. Overview

ArbiterOS Kernel is an Agent protection and monitoring layer that runs on top of the LiteLLM proxy. It instructionizes model responses across the full Agent LLM request/response lifecycle, enforces policy protection, monitors execution, and visualizes various runtime information:

- **Request preprocessing**: Message truncation, category+topic wrapping, Trace management
- **Response postprocessing**: Category+topic stripping, instruction parsing, policy protection
- **Observability**: Langfuse tracing, log persistence

---

## 2. Summary: Overall Flow

```
LiteLLM Request
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  Pre-Call                                                    │
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
│  • Policy validation                                         │
│  • Record instructions                                       │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
  Return to Agent
```

---

## 3. Code Architecture


| Module                 | Responsibility                                                           |
| ---------------------- | ------------------------------------------------------------------------ |
| `litellm_callback.py`  | Hook implementation, Trace state, Langfuse emission, instruction parsing |
| `instruction_parsing/` | InstructionBuilder, Instruction schema, registry                         |
| `policy_check.py`      | Policy orchestration entry point, PolicyCheckResult                      |
| `policy/`              | Policy implementations                                                   |


---

## 4. Receiving Agent Requests (Pre-Call)

1. **Resolve Trace ID**: Determine the trace_id for this request.
  - `_ensure_trace_state()` when reset requested or new device; `_resolve_trace_state_from_metadata()` when metadata contains `arbiteros_trace_id` / `arbiteros_device_key`.
2. **Response format merge**: Merge the agent’s `response_format` (if present) into the request content.
  - `_merge_agent_response_format_into_content()`.
3. **Category wrapping**: Wrap assistant history with `topic/category/content` using previously stripped categories for this Trace.
  - `_wrap_messages_with_categories()`.
4. **Topic hint**: Inject the previous turn’s topic summary into the prompt.
  - `_inject_topic_summary_hint()`.
5. **Logging**: Write to `log/precall.jsonl` and `log/api_calls.jsonl`.
  - `_save_precall_to_log()`, `_save_json()`.
6. **Inject metadata & forward**: Add `arbiteros_trace_id` and `arbiteros_device_key` to request metadata, then return data for LiteLLM to forward.
  - `_inject_trace_metadata()`.

---

## 5. Responding to Agent Requests (Post-Call)

1. **Response extraction**: Supports both Chat Completions (`.choices`) and Responses API (`output_text`).
  - Inline in `async_post_call_success_hook`; uses `_to_json()` for message dict.
2. **Response Transform**: For responses in `{topic, category, content}` format, strip the outer wrapper, keep only `content`, and record the stripped category for later pre-call wrapping.
  - `_response_transform_content_only()` (assigned to `response_transform`).
3. **Instruction Parsing**: Map instructions to `instruction_type` and `instruction_category` based on category / tool call type.
  - `builder.add_from_tool_call()` for tool calls; `builder.add_from_structured_output()` for content (inside `_response_transform_content_only` and `_add_instruction_for_non_strict`).
4. **Policy validation**: Run all policies on the current response. If a policy modifies the response, blocked tool_calls are replaced with error messages and the corresponding instruction is marked `policy_protected`.
  - `check_response_policy()`; `_replace_instructions_from_modified_response()` when policy modifies response.
5. **Record instructions**: Write the protected instructions to `log/{trace_id}.json`.
  - `_save_instructions_to_trace_file()`.

---

## 6. Instruction Parsing

### 6.1 InstructionBuilder

`InstructionBuilder` unifies LLM output and tool calls into an Instruction list, maintained per trace and written to `log/{trace_id}.json`.

**Main methods**:

- `**add_from_structured_output()`**: Maps `{intent, content}` to `instruction_type` and `instruction_category` (e.g. REASON, PLAN, RESPOND).
- `**add_from_tool_call()**`: Records tool name, `tool_call_id`, arguments, and optional result; retrieves predefined type and security attributes.

### 6.2 Instruction Schema

```json
{
  "id": "uuid",
  "content": "...",
  "runtime_step": 1,
  "parent_id": null,
  "source_message_id": "...",
  "security_type": "...",
  "rule_types": [],
  "instruction_category": "EXECUTION.Human",
  "instruction_type": "RESPOND"
}
```

### 6.3 Instruction Types and Categories


| instruction_type                 | instruction_category |
| -------------------------------- | -------------------- |
| REASON, PLAN, CRITIQUE           | COGNITIVE.Reasoning  |
| STORE, RETRIEVE, COMPRESS, PRUNE | MEMORY.Management    |
| READ, WRITE, EXEC, WAIT          | EXECUTION.Env        |
| ASK, RESPOND, USER_MESSAGE       | EXECUTION.Human      |
| HANDOFF                          | EXECUTION.Agent      |
| SUBSCRIBE, RECEIVE               | EXECUTION.Perception |


---

## 7. Logging

Various runtime information is recorded in the following files:


| File                       | Purpose                                                                  |
| -------------------------- | ------------------------------------------------------------------------ |
| `log/api_calls.jsonl`      | Raw post-call content of LLM responses                                   |
| `log/precall.jsonl`        | Final pre-call content sent to the LLM                                   |
| `log/langfuse_nodes.jsonl` | Langfuse node logs (for debugging)                                       |
| `log/trace_state.json`     | Persisted Trace state                                                    |
| `log/{trace_id}.json`      | Instruction information per Trace (core output with highest readability) |


