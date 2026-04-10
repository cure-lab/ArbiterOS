# Kernel Policy Interface

This document describes how the ArbiterOS kernel integrates with the policy layer for checking and optionally modifying LLM assistant responses.

---

## 1. Overview


| Component             | Location                               | Role                                                                                                                                                                                                 |
| --------------------- | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Kernel**            | `arbiteros_kernel/litellm_callback.py` | Intercepts LLM responses in `post_call_success` (and the streaming equivalent) and runs policy checks before returning the response to the upper-layer Agent.                                        |
| **Policy layer**      | `arbiteros_kernel/policy/`             | Contains one or more policy classes that inspect and optionally **modify** assistant responses (e.g., block or redact tool calls, schema validation, path budgets, allow/deny rules, rate limiting). |
| **Check entry point** | `arbiteros_kernel/policy_check.py`     | Exposes `check_response_policy()` and `PolicyCheckResult`. The kernel calls only this entry point and never invokes individual policy classes directly.                                              |


### Data flow

```
LLM response → response_transform → check_response_policy → PolicyCheckResult → Send to Agent
```

- **modified=True**: Update message, rebuild instructions, set `policy_protected`, then send to Agent.
- **modified=False**: Skip update; send original response to Agent.

---

## 2. Kernel integration

**Module:** `arbiteros_kernel/litellm_callback.py`

This section describes when and how the kernel invokes the policy layer.

### When policies run

Policies run in **post_call_success** (and the streaming post_call equivalent), after:

- The assistant message dict has been built (`final_msg_dict` / `msg_dict`).
- Optional `response_transform` has been applied (e.g., stripping internal fields).

The kernel exposes to policies the **final message structure** that would otherwise be returned to the Agent.

### Arguments passed by the kernel


| Argument              | Source                                                                                                                                    |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `trace_id`            | `metadata.get("arbiteros_trace_id")`; must be a non-empty string.                                                                         |
| `instructions`        | `list(builder.instructions)` for the trace.                                                                                               |
| `current_response`    | The message dict at this stage (`final_msg_dict` or `msg_dict`).                                                                          |
| `latest_instructions` | The instruction slice for this response (from `_policy_instruction_count_before` / `_policy_instruction_count_before_stream` to the end). |


The kernel does **not** pass `policy_classes`; it always uses the default list from `policy.defaults`.

### When `policy_result.modified` is True

1. **Replace response:** The kernel uses `policy_result.response` as the new message and writes it back to the LiteLLM response (e.g., `response.choices[0].message = Message(**final_msg_dict)`).
2. **Sync instructions:** Calls `_replace_instructions_from_modified_response(builder, modified_response, instruction_start_index)` to regenerate and replace the instructions added in this turn from the modified response (tool_calls before content).
3. **Mark instructions:** Each new instruction in this turn gets `instr["policy_protected"] = error_type_str`, where `error_type_str` comes from the aggregated `policy_result.error_type`.
4. **Tool-call-level protection:** If the original response had `tool_calls`, the kernel stores `trace_id -> { tool_call_id: error_type_str }` in `_policy_protected_tool_call_ids`. When the **tool result** instruction for that `tool_call_id` is recorded later, it sets `builder.instructions[-1]["policy_protected"] = error_type` and removes the entry. Thus both the assistant's tool-call instruction and the subsequent tool-result instruction can carry the `policy_protected` marker.
5. **Persist:** The updated instructions are written back to the trace file.

---

## 3. Entry Function:  `check_response_policy`

**Module:** `arbiteros_kernel/policy_check.py`

The kernel does not instantiate or call policy classes directly. It uses this single entry point:

```python
def check_response_policy(
    *,
    trace_id: str,
    instructions: list[dict[str, Any]],
    current_response: dict[str, Any],
    latest_instructions: list[dict[str, Any]] | None = None,
    policy_classes: Optional[list[type[Policy]]] = None,
) -> PolicyCheckResult:
```

### Arguments

- Same as `Policy.check()` for the first four parameters. `latest_instructions` defaults to `[]` when omitted.
- `policy_classes`: List of policy **classes** to run in order. If `None`, uses `arbiteros_kernel.policy.defaults.DEFAULT_POLICY_CLASSES`.

### Execution flow

1. **Initialize:** `response = current_response`.
2. **Iterate:** For each policy class in `policy_classes`, instantiate it and call `policy.check(...)`. The `current_response` passed to each policy is the `response` produced by the previous one.
3. **On modification:** If a policy returns `modified=True`, set `response = result.response` and append `result.error_type` (if present) to an error list.
4. **Return:** A single aggregated `PolicyCheckResult`:
  - `modified = (len(errors) > 0)`
  - `response` = the final response after all policies have run
  - `error_type` = `"\n".join(errors)` when errors exist, otherwise `None`

Policies run **in sequence**; each one sees the output of the previous. The kernel receives one combined `PolicyCheckResult`.

---

## 4. `PolicyCheckResult`

**Module:** `arbiteros_kernel/policy_check.py`

This is the return type of `check_response_policy` (and of each `Policy.check()`):

```python
@dataclass
class PolicyCheckResult:
    modified: bool          # True if the response was modified by the policy
    response: dict[str, Any]  # The response to pass onward (original or modified)
    error_type: Optional[str] = None  # When modified=True, describes the reason for modification
```


| Field        | Description                                                                                                                                                   |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `modified`   | Whether the policy changed the response (e.g., blocked tool calls, redacted content).                                                                         |
| `response`   | The response object used by the kernel downstream. If `modified=True`, this must be the modified version.                                                     |
| `error_type` | Optional string describing the reason (e.g., `"POLICY_BLOCK tool=read reason=..."`). Appears in metadata and in the `policy_protected` field of instructions. |


---

## 5. Base class: `Policy`

**Module:** `arbiteros_kernel/policy/policy.py`

All policies inherit from `Policy` and implement the unified `check` method:

```python
class Policy(ABC):
    @abstractmethod
    def check(
        self,
        instructions: list[dict[str, Any]],
        current_response: dict[str, Any],
        latest_instructions: list[dict[str, Any]],
        trace_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> PolicyCheckResult:
        ...
```

### Parameters


| Parameter             | Description                                                                                                                                  |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `instructions`        | Full instruction history for the current trace (from the instruction builder). Includes `latest_instructions`.                               |
| `current_response`    | The assistant message dict to be checked. Passed after any `response_transform`; may contain `content`, `tool_calls`, `function_call`, etc.  |
| `latest_instructions` | Instructions derived from **this** response only (e.g., new tool_calls + content from this turn). Usually a suffix subset of `instructions`. |
| `trace_id`            | Trace ID for the current request (used for auditing, logging, etc.).                                                                         |


