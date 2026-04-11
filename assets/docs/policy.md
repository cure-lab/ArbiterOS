# Policy Rules Explanation

## 1. Overall Overview

The current Policy system is mainly divided into two layers:

- **UnaryGatePolicy**: focuses on whether a single action is allowed to execute.
- **RelationalPolicy**: focuses on whether a flow of information from a source to a target is safe.

---

## 2. UnaryGatePolicy

UnaryGatePolicy applies **rule-by-rule matching** to perform security checks on individual actions.

### UG-001: missing instruction metadata

**Definition:** The current tool call is blocked if no corresponding kernel-lowered instruction metadata can be found.

**Defensive Effect:** Prevents the policy layer from blindly allowing actions when structured security metadata is missing.

### UG-010: argument string budget

**Definition:** The action is blocked when the total length of all string content in the tool arguments exceeds 4000 characters.

**Defensive Effect:** Prevents oversized inputs from distorting rule evaluation or from using extremely long arguments to bypass simple checks.

### UG-020: exec confidence gate

**Definition:** Any action with `instruction_type = EXEC` must satisfy `confidence >= MID`.

**Defensive Effect:** Prevents the model from executing commands when its own certainty is insufficient.

### UG-021: exec trust gate

**Definition:** Any action with `instruction_type = EXEC` must satisfy `trustworthiness >= MID`.

**Defensive Effect:** Prevents execution from being driven by low-trust sources, such as external content, unverified input, or potential prompt-injection material.

### UG-030: high risk exec

**Definition:** The action is blocked when the tool is `exec` and its `risk` level is `HIGH` or `CRITICAL`.

**Defensive Effect:** Prevents obviously dangerous, destructive, or high-side-effect commands from being executed automatically.

### UG-031: approval required block

**Definition:** The action is blocked when the tool is `exec` or `write` and the metadata marks it as `approval_required = true`.

**Defensive Effect:** Ensures that actions requiring human approval cannot be auto-approved by the policy layer.

### UG-032: destructive must be reversible

**Definition:** The action is blocked when the tool is `exec` or `write`, `destructive = true`, and `reversible = false`.

**Defensive Effect:** Prevents irreversible destructive actions from executing directly, reducing the risk of unrecoverable damage.

### UG-040: blocked secret like tags

**Definition:** The action is blocked when its tag set intersects with `SECRET_LIKE` or `HIGH_RISK`.

**Defensive Effect:** Uniformly blocks sensitive or high-risk objects that have already been identified upstream, preventing further propagation.

### UG-050: respond prop confidentiality

**Definition:** The action is blocked when `prop_confidentiality > MID` for a `RESPOND` instruction.

**Defensive Effect:** Prevents highly sensitive content from appearing directly in the final response to the user.

### UG-060: protected identity or control file direct mutation

**Definition:** Direct modification of `SOUL.MD`, `AGENTS.MD`, or `IDENTITY.MD` through `write` or `edit` is prohibited.

**Defensive Effect:** Prevents direct tampering with the agent’s identity files or core control files.

### UG-061: protected identity or control file exec write target

**Definition:** Indirect writes to `SOUL.MD`, `AGENTS.MD`, or `IDENTITY.MD` through `exec` or `process` are prohibited.

**Defensive Effect:** Prevents shell commands, scripts, or redirection-based methods from bypassing direct-write protection and modifying core control files indirectly.

### UG-062: protected identity or control file mutation instruction

**Definition:** Explicit instructions to modify `SOUL.MD`, `AGENTS.MD`, or `IDENTITY.MD` may not be propagated through `message`, `sessions_send`, or `sessions_spawn`.

**Defensive Effect:** Prevents the model from delegating the task of modifying core control files to other sessions, sub-agents, or external actors.

### UG-070: suspicious gateway external redirection patch

**Definition:** The action is blocked when a `gateway` `CONFIG.PATCH` or `CONFIG.APPLY` operation contains an external URL and involves fields such as `PROXY`, `UPSTREAM`, or `BASE_URL`.

**Defensive Effect:** Prevents the gateway from being turned into an external proxy or upstream redirection point, protecting the system’s traffic path and execution chain from silent rerouting.

---

## 3. RelationalPolicy

RelationalPolicy does not define rules as numbered items. Instead, it controls actions according to **flow kinds**, meaning the category of information flow involved.

### read_external

**Definition:** Reading content from web pages, external resources, or externally referenced images.

**Defensive Effect:** Treats external input as potentially untrusted and prevents it from directly driving higher-privilege actions.

### read_sensitive

**Definition:** Reading local sensitive files, memory content, session history, gateway configuration, node data, and similar protected sources.

**Defensive Effect:** Prevents sensitive information from being read and then propagated to low-trust or inappropriate targets.

### read_state

**Definition:** Reading system state, task state, lists, snapshots, and other status-oriented information.

**Defensive Effect:** Distinguishes normal state queries from truly sensitive reads, reducing unnecessary over-blocking.

### write_local

**Definition:** Writing to local, private, and non-shared paths.

**Defensive Effect:** Allows relatively controlled local persistence while still restricting unsuitable content from being written directly into the local environment.

### write_shared

**Definition:** Writing to paths such as `/shared/`, `/public/`, `/export/`, or `/uploads/`, where content can spread more easily.

**Defensive Effect:** Prevents sensitive content from being written into locations that are more prone to leakage or wider distribution.

### delegate_sink

**Definition:** Sending tasks or content to another agent session through `sessions_send`, `sessions_spawn`, or `DELEGATE`.

**Defensive Effect:** Prevents sensitive content or low-trust instructions from being handed off to other agents for continued execution.

### comm_sink

**Definition:** Sending content to human-visible external channels, such as through `message`.

**Defensive Effect:** Prevents content that is unsuitable for external communication from entering messaging, email, chat, or other visible channels.

### voice_sink

**Definition:** Outputting content directly through tools such as `tts`.

**Defensive Effect:** Because spoken output is harder to retract once emitted, this flow kind applies stricter control to information leakage risk.

### ui_side_effect

**Definition:** Control-oriented actions in browsers, interfaces, or interactive environments.

**Defensive Effect:** Prevents the model from clicking, navigating, uploading, or otherwise manipulating the UI based on low-trust input.

### exec_side_effect

**Definition:** `exec`, `process`, or other actions that materially change the system or runtime environment.

**Defensive Effect:** Prevents low-trust content from directly driving execution-like actions and reduces the chance of high-risk side effects reaching the environment.

### persist_side_effect

**Definition:** Persistent actions such as `cron`, reminders, or long-lived tasks that continue to take effect over time.

**Defensive Effect:** Prevents medium- or high-sensitivity content from being embedded into long-term tasks and blocks low-trust sources from creating lasting side effects.

### respond_sink

**Definition:** Presenting content directly to the user as the final response.

**Defensive Effect:** Prevents content from being shown verbatim when it is not safe or appropriate for direct display.
