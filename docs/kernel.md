# ArbiterOS Kernel Architecture

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
└──────────────────────────────────────────────────────────────┘
       │
       ▼
  LLM API Call
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  Post-Call                                                   │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
  Return to Agent
```

---

## 3. Code Architecture

| Module | Responsibility |
|--------|----------------|
| `litellm_callback.py` | Hook implementation, Trace state, Langfuse emission, instruction parsing |
| `instruction_parsing/` | InstructionBuilder, Instruction schema, registry |
| `policy_check.py` | Policy orchestration entry point, PolicyCheckResult |
| `policy/` | Policy implementations |

---

## 4. Receiving Agent Requests (Pre-Call)

1. **Resolve Trace ID**: Determine the trace_id for this request.
2. **Response format merge**: Merge the agent’s `response_format` (if present) into the request content.
3. **Category wrapping**: Wrap assistant history with `topic/category/content` using previously stripped categories for this Trace.
4. **Topic hint**: `_inject_topic_summary_hint()` injects the previous turn’s topic summary into the prompt.
5. **Logging**: Write to `log/precall.jsonl` and `log/api_calls.jsonl`.
6. **Send request**: Forward the request to the LLM API endpoint.

---

## 5. Responding to Agent Requests (Post-Call)

1. **Response extraction**: Supports both Chat Completions (`.choices`) and Responses API (`output_text`).
2. **Response Transform**: For responses in `{topic, category, content}` format, strip the outer wrapper, keep only `content`, and record the stripped category for later pre-call wrapping.
3. **Instruction Parsing**: Map instructions to `instruction_type` and `instruction_category` based on category / tool call type.
4. **Policy validation**: `check_response_policy()` runs all policies on the current response. If a policy modifies the response, blocked tool_calls are replaced with error messages and the corresponding instruction is marked `policy_protected`.
5. **Record instructions**: Write the protected instructions to `log/{trace_id}.json`.

---

## 6. Instruction Parsing

`InstructionBuilder` unifies LLM output and tool calls into an Instruction list, maintained per trace and written to `log/{trace_id}.json`.

**Main methods**:

- **`add_from_structured_output()`**: Maps `{intent, content}` to `instruction_type` and `instruction_category` (e.g. REASON, PLAN, RESPOND).
- **`add_from_tool_call()`**: Records tool name, `tool_call_id`, arguments, and optional result; retrieves predefined type and security attributes.

---

## 7. Logging

| File | Purpose |
|------|---------|
| `log/api_calls.jsonl` | Raw post-call content of LLM responses |
| `log/precall.jsonl` | Final pre-call content sent to the LLM |
| `log/langfuse_nodes.jsonl` | Langfuse node logs (for debugging) |
| `log/trace_state.json` | Persisted Trace state |
| `log/{trace_id}.json` | Instruction information per Trace (core output with highest readability) |
