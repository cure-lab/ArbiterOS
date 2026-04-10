# Agent Instructions

This document defines a standardized set of **Agent Instructions** to categorize the various actions an agent can perform. These instructions serve as a common language for describing agent behavior, enabling better interpretability, safety monitoring, and fine-grained policy control.

## System Roles & Definitions

We define two primary roles within the interaction loop:

- **Agent**: An LLM entity equipped with tools and external memory, capable of performing various cognitive and functional actions.
- **Environment**: The external context that the agent interacts with. It encompasses any entity outside the agent's internal state:
    - **Digital/Physical World**: The execution boundaries (e.g., file system, web, API, shell, physical sensors and actuators).
    - **User**: The principal issuing instructions and evaluating results. For a top-level agent this is a human operator; for a sub-agent this is the orchestrating parent agent.
    - **Peer Agents / Sub-Agents**: Other agents in the system, available for collaboration or delegation.

## Types of Instructions

This taxonomy applies to any agent paradigm and deployment context — from a coding assistant operating on a file system to an embodied agent interacting with the physical world.

### 1. COGNITIVE

Cognitive instructions are entirely internal to the agent — they involve no interaction with the environment. They represent the agent's reasoning, planning, and memory operations that can occur at any point during execution.

* **Reasoning & Decision**
    * `REASON`: **Internal Reasoning.** Generating Chain-of-Thought (CoT), logical deduction, and hypothesis generation.
    * `PLAN`: **Task Decomposition.** Breaking down high-level goals into executable sub-tasks and ordering them.
    * `CRITIQUE`: **Self-Correction.** Reviewing past instructions or errors to adjust future strategies (e.g., analyzing a stack trace).

* **Memory Management**
    * `STORE`: **Persist Experience.** Writing important information or outcomes to long-term memory for future retrieval.
    * `RETRIEVE`: **Recall Context.** Fetching relevant history or knowledge (RAG) from long-term memory based on the current query.
    * `COMPRESS`: **Summarization.** Replacing the entire context window with a condensed summary that retains essential facts while discarding noise.
    * `PRUNE`: **Context Pruning.** Selectively removing specific parts of the context that are no longer relevant to the current task (e.g., sliding window, dropping earlier sub-task history) to free up space.

### 2. ACTUATION

Actuation instructions represent the agent acting as an initiator — proactively reading from, writing to, or executing operations against the environment, as well as communicating with the user and delegating to other agents.

* **Environment Interaction**
    * `READ`: **Pull Information.** Actively pulling data from the environment (e.g., reading a file or web page for a coding agent; sensor observation for an embodied agent).
    * `WRITE`: **Change State.** Persisting data to the environment with no behavioral side effects beyond storage (e.g., saving to a file, updating a database record). Use `EXEC` when the operation triggers external behavior. Written files are automatically tracked in the [Registry](registry_usage.md#automatic-taint-tracking) to propagate taint to future reads.
    * `EXEC`: **Execute Command.** Triggering operations that cause behavioral side effects beyond storage (e.g., `run build`, `send email`, `deploy`, actuating a motor).
    * `WAIT`: **Suspend and Wait.** Suspending execution to wait for an ongoing operation to complete before proceeding (e.g., waiting for a script to finish running before processing its output).
* **User Interaction**
    * `ASK`: **User-in-the-Loop.** Requesting user confirmation or input before proceeding with a critical instruction (e.g., `Do you agree with the current plan?`).
    * `RESPOND`: **Final Output.** Delivering the final answer or result to the user (e.g., `Here is the solution to your problem.`).
* **Agent Collaboration**
    * `DELEGATE`: **Inter-Agent Delegation.** Handing off a task to another specialized agent.

### 3. PERCEPTION

Perception instructions represent the agent as a receiver — reacting to events or data pushed from the environment rather than actively initiating them.

* **Event Handling**
    * `SUBSCRIBE`: **Register Listener.** Registering with the Agent Harness to receive pushed notifications for specific environmental changes (e.g., file modifications, incoming messages, sensor signals).
    * `RECEIVE`: **Handle Push Event.** Processing an event delivered by the Agent Harness (e.g., `onFileChange`, `onSensorReading`). Typically implemented as an incoming message.
* **User Interaction**
    * `USER_MESSAGE`: **User Prompt.** New input from the user. Since the User is modeled as part of the Environment (see [System Roles & Definitions](#system-roles--definitions)), user messages are treated as a special category of environmental push event — they interrupt the agent's current process and require immediate attention.

---

## Instruction Metadata

In addition to the instruction type, each instruction carries a structured metadata schema capturing both its extrinsic and intrinsic attributes.

### 1. Message Metadata

This layer captures the **extrinsic** attributes of the message flow — immutable facts generated by the ArbiterOS Kernel, infrastructure, or the model provider.

* **Identity**
  * `id`: Unique identifier for the instruction.
  * `trace_id`: Global ID linking the entire interaction chain.

* **Timing**
  * `timestamp`: Precise time of generation.
  * `latency_ms`: Time taken to generate this specific token stream.

* **Infrastructure Context**
  * `model_id`: The specific model version used (e.g., `gpt-4-turbo-2024-04-09`).
  * `provider`: The inference provider (e.g., `openai`, `azure`, `anthropic`).
  * `token_usage`:
    * `input_tokens`: Length of context.
    * `output_tokens`: Length of generated output.
  * `finish_reason`: Why the stream stopped (e.g., `stop`, `length`, `content_filter`).

### 2. Safety & Trust Metadata

This layer captures the **intrinsic** characteristics of the instruction using a taint-tracking model. The five fields — `TRUSTWORTHINESS`, `CONFIDENCE`, `REVERSIBLE`, `RISK`, and `CONFIDENTIALITY` — each represent a dimension of potential risk or uncertainty (taint). In taint tracking, "taint" marks information or actions that are potentially unsafe: originating from unreliable sources, involving dangerous operations, or touching sensitive data. Taint propagates through the system and can only be cleared by an explicit sanitization step. `AUTHORITY` serves as the sole sanitization node, recording whether a human or policy engine has explicitly approved the instruction.

---

#### 2.1 Taint Sources

These fields assess the reliability of the information that led to this instruction. Both follow a worst-case-wins rule: `LOW` represents the heaviest taint.

##### `TRUSTWORTHINESS` — Source Reliability

Rating whether the **external source** of information can be trusted. The primary defence against prompt-injection attacks: untrusted content must never influence privileged instructions without explicit approval. Values are resolved automatically by the [Registry](registry_usage.md#file_trustworthinessyaml--file-trustworthiness-classification).

| Value     | Meaning                                                                                                                                                                                 |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `HIGH`    | Source is system-controlled or package-manager-verified (e.g. local filesystem under `/usr/`, `/etc/`, agent's own memory files). Content can be used directly.                         |
| `UNKNOWN` | Source provenance is unverified or unavailable. Neither confirmed trusted nor confirmed untrusted; treat with caution.                                                                  |
| `LOW`     | Source is external and unverified (e.g. web pages, downloaded files, camera/screen captures from third-party nodes, external URLs). Content must be treated as potentially adversarial. |

##### `CONFIDENCE` — Agent Certainty

Rating the **agent's own certainty** in the reasoning or decision that produced this instruction. Distinct from trustworthiness: an agent can be highly confident in information from an untrusted source (and still be wrong or manipulated).

| Value     | Meaning                                                                                                                             |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `HIGH`    | Agent has strong evidence or direct recall supporting this instruction.                                                             |
| `UNKNOWN` | Confidence has not been evaluated, or the agent has partial evidence with some inference required. Default before evaluation. |
| `LOW`     | Agent is uncertain; the instruction is speculative or based on weak signals.                                                        |

---

#### 2.2 Taint Impact

These fields characterize the consequences of executing this instruction on the environment. Unlike taint sources, here `HIGH` (or `false` for reversibility) represents the most constrained state — i.e., the heaviest taint.

##### `REVERSIBLE` — Undo Capability

Boolean flag indicating whether the effects of this instruction can be undone after execution.

| Value   | Meaning                                                                                                                                                                                                      |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `true`  | Instruction is reversible: state can be restored (e.g. file edits via `git revert`, cron entry removal, read-only observations).                                                                             |
| `false` | Instruction is **irreversible**: effects persist permanently (e.g. sent messages, shell side-effects, spawned sub-agents, audio playback). Irreversible instructions should receive stricter policy scrutiny. |

##### `RISK` — Execution Danger

Rating the inherent danger of **executing** this instruction, independent of what data it touches. Values are resolved automatically by the [Registry](registry_usage.md#exe_riskyaml--execution-risk-classification).

| Value     | Meaning                                                                                                                                                                    |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `HIGH`    | Instruction is known to cause irreversible damage or destructive side-effects (e.g. `rm`, `dd`, `shutdown`, `kill`, `git clean`). Requires explicit approval before execution. |
| `UNKNOWN` | Instruction is not explicitly classified as safe or dangerous (e.g. `cat`, `python`, `git commit`). Apply default policy scrutiny.                                            |
| `LOW`     | Instruction is known to be safe and read-only with no side effects (e.g. `ls`, `echo`, `cd`, `pwd`, `whoami`). Can be executed with minimal scrutiny.                         |

##### `CONFIDENTIALITY` — Data Sensitivity

Rating the sensitivity of the data **produced or accessed** by this instruction. Used to decide whether output may be logged, stored, or forwarded to other agents. Values are resolved automatically by the [Registry](registry_usage.md#file_confidentialityyaml--file-confidentiality-classification).

| Value     | Meaning                                                                                                                                                                                   |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `HIGH`    | Data is highly sensitive and must not be stored or transmitted without explicit approval (e.g. private keys, credentials, `/etc/shadow`, conversation history, camera/location captures). |
| `UNKNOWN` | Data sensitivity has not been classified; treat conservatively.                                                                                                                           |
| `LOW`     | Data carries no significant sensitivity; safe to log and forward (e.g. public documentation, system binaries, `/tmp` files, source code).                                                |

---

#### 2.3 Sanitization

##### `AUTHORITY` — Approval State

The sole sanitization node in the taint model. When taint has accumulated — from low trustworthiness, low confidence, high risk, or sensitive data — explicit approval by a human or policy engine clears it and permits execution. The default value is `UNKNOWN` until the policy engine evaluates the instruction.

| Value             | Meaning                                                                                               |
| ----------------- | ----------------------------------------------------------------------------------------------------- |
| `HUMAN_APPROVED`  | A human operator has explicitly authorised this instruction (e.g. via an `ASK` confirmation loop).   |
| `POLICY_APPROVED` | The instruction passed all automated policy checks and is approved without requiring human review.    |
| `HUMAN_BLOCKED`   | A human operator has explicitly rejected this instruction.                                            |
| `POLICY_BLOCKED`  | The instruction was blocked by automated policy (e.g. rate limit, path-protection, schema violation). |
| `UNKNOWN`         | Authority has not yet been evaluated (default at parse time, before the policy engine runs).          |
