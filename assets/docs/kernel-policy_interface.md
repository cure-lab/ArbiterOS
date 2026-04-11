# Kernel Policy Interface

How the ArbiterOS kernel wires **user-approval preprocessing**, **`check_response_policy`**, **policies**, and **observe-only (“warning”)** behavior. For instruction JSON shape and global kernel flow, see **`docs/kernel.md`**.

---

## 1. Overview

| Piece | Where | Role |
| ----- | ----- | ---- |
| Kernel | `arbiteros_kernel/litellm_callback.py` | After each assistant response: run **`apply_user_approval_preprocessing`**, then **`check_response_policy`**, apply the result; accumulate **`inactivate_error_type`** into pending warnings and flush them on a later **pure-text** reply. |
| User approval | `arbiteros_kernel/user_approval.py` | **`apply_user_approval_preprocessing()`** — deep-copy instructions and adjust **`prop_*`** when the user previously approved a blocked action. Runs in the **callback**, immediately **before** `check_response_policy`. |
| Registry + enforce | `arbiteros_kernel/policy_registry.json` | Each row: a policy class and **`enabled`** (enforce vs observe-only). Loaded via **`get_policy_registry()`**. |
| Policies | `arbiteros_kernel/policy/*` | Concrete **`Policy`** subclasses; implement **`check()`** below. |
| Entry point | `arbiteros_kernel/policy_check.py` | **`check_response_policy()`** — loops registry entries, **`apply_policy_enforcement_mode`** per row, returns one **`PolicyCheckResult`**. |

---

## 2. Registry `enabled`: enforce vs observe-only

Each row in **`policy_registry.json`** has **`"enabled": true`** or **`false`**. The policy’s **`check()`** always runs; what differs is whether the kernel **applies** a blocking/redaction result.

| `enabled` | Meaning | User-visible response | What gets recorded |
| --------- | ------- | ---------------------- | ------------------ |
| **`true`** | **Enforce** | If **`check()`** returns **`modified=True`**, the assistant message is **replaced** with **`result.response`** (block/redact tool calls or text). | Aggregated into **`PolicyCheckResult.error_type`** → **`policy_protected`** on instructions, normal policy block flow. |
| **`false`** | **Observe-only** (“dry run”) | Response text / tool calls sent to the agent stay **unchanged** (pre-check snapshot is restored). | The text that **would** have been the violation goes to **`inactivate_error_type`** → queued for **warning append** (Section 3 pipeline) and related metadata — **not** a hard block for that turn. |

So: **`enabled`** does not skip the policy; it switches **apply modification** vs **report only**.

---

## 3. Post-call pipeline

```
InstructionBuilder → instructions / latest_instructions
       │
       ▼
apply_user_approval_preprocessing(instructions, latest_instructions)
       │  (deep copy; prop_* elevation for user_approved / reference_tool_id)
       ▼
check_response_policy(trace_id, instructions, current_response, latest_instructions)
       │  for each PolicyEntry: instantiate policy → policy.check(...) → apply_policy_enforcement_mode(entry.enabled, …)
       ▼
PolicyCheckResult → kernel: replace response / policy_protected / Langfuse metadata
                  → if inactivate_error_type: append to pending_warning_texts
                  → later: _append_pending_warnings_to_assistant_content_if_needed (pure text only; skip policy-confirmation “ask”)
```

Same behavior as the table in Section 2; see also **`apply_policy_enforcement_mode`** in **`policy_check.py`**.

---

## 4. User approval

**Module:** `arbiteros_kernel/user_approval.py`

**Function:** `apply_user_approval_preprocessing(*, instructions, latest_instructions) -> (instructions_for_policy, latest_for_policy)`

- Deep-copies the instruction list when non-empty.
- Elevates **`security_type.prop_*`** for instructions tied to **`user_approved`** flows (and related **`tool_call_id` / `reference_tool_id`**).
- Recomputes propagated taint for the current tail when **`reference_tool_id`** is present.

Persisted **`log/{trace_id}.json`** is updated by the normal builder save path; this function only supplies the lists passed into **`check_response_policy`**.

---

## 5. Entry function: `check_response_policy`

**Module:** `arbiteros_kernel/policy_check.py`

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

| Argument | Typical source |
| -------- | -------------- |
| `trace_id` | Non-empty `arbiteros_trace_id` for the trace. |
| `instructions` | After **`apply_user_approval_preprocessing`** in production (full history). |
| `current_response` | Assistant message dict for this turn (post **`response_transform`**). |
| `latest_instructions` | Suffix of `instructions` for **this** response only. |
| `policy_classes` | **`None`** → load **`get_policy_registry()`** from **`policy_registry.json`**. Non-**`None`** → run only those classes, all treated as **enforced** (`enabled=True`). |

**Execution:** Walk each registry entry in order; for each, **`response_before = deepcopy(current_response)`**, run **`policy.check(...)`**, then **`apply_policy_enforcement_mode(entry.enabled, response_before, result)`**. Aggregate enforced errors into **`error_type`**, observe-only strings into **`inactivate_error_type`**, and record **`policy_names` / `policy_sources`** for policies that enforced a change.

---

## 6. `PolicyCheckResult`

**Module:** `arbiteros_kernel/policy_check.py`

```python
@dataclass
class PolicyCheckResult:
    modified: bool
    response: dict[str, Any]
    error_type: Optional[str] = None
    policy_names: list[str] = field(default_factory=list)
    policy_sources: dict[str, str] = field(default_factory=dict)
    inactivate_error_type: Optional[str] = None
```

| Field | Meaning |
| ----- | ------- |
| `modified` | **`True`** if any **enforced** policy changed the response after observe-only handling. |
| `response` | Message dict to return downstream. |
| `error_type` | Joined enforced violation text(s); feeds **`policy_protected`** / logging. |
| `policy_names` | Policy class names that enforced a modification. |
| `policy_sources` | Optional map **name → source location** (debugging). |
| `inactivate_error_type` | Joined observe-only “would have blocked” text; feeds **pending warnings** (Section 3 pipeline). |

---

## 7. Base class: `Policy`

**Module:** `arbiteros_kernel/policy/policy.py`

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

Implementations return **`PolicyCheckResult`**; the kernel never calls concrete policy classes except through **`check_response_policy`**.

**Inputs:** `instructions` / `latest_instructions` are the lists produced after **`apply_user_approval_preprocessing`** in normal runs.

---

## 8. Kernel when `modified=True` (enforced)

Roughly: replace the LiteLLM message with **`policy_result.response`**, rebuild instructions for the turn from the modified message, set **`policy_protected`** on new instructions with **`error_type`**, track **`_policy_protected_tool_call_ids`** so the following **tool result** instruction can also carry **`policy_protected`**, and persist **`log/{trace_id}.json`**.

---

## 9. Configuration

| What | File |
| ---- | ---- |
| Policy order and **`enabled`** (enforce vs observe-only) | `arbiteros_kernel/policy_registry.json` |

See **`docs/kernel.md`** for LiteLLM callbacks, logging, and instruction schema.
