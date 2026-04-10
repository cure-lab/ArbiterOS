# ArbiterOS Docs Index

This folder contains the core technical documentation for ArbiterOS Kernel design, policy integration, registry behavior, agent extension, and Langfuse-based visualization.

## Documents in This Folder

| File | What it covers |
| --- | --- |
| `kernel.md` | End-to-end Kernel architecture: pre-call/post-call flow, instruction parsing, policy checks, and runtime logs. |
| `kernel-policy_interface.md` | Contract between Kernel and policy layer (`check_response_policy`, `PolicyCheckResult`, and modification flow). |
| `registry_usage.md` | Registry YAML model (`exe_registry`, `exe_risk`, `file_trustworthiness`, `file_confidentiality`), lookup order, and automatic taint tracking. |
| `agent_insturctions_design.md` | Canonical instruction taxonomy (COGNITIVE/ACTUATION/PERCEPTION) and safety metadata model (trust, risk, confidentiality, authority). |
| `add_new_agent.md` | Practical guide for adding a new agent tool parser/registry and wiring runtime selection + policy aliases. |
| `visualization.md` | Langfuse governance UI guide: Home/Tracing/Analysis/Summary/Policy/Settings pages and policy refinement workflows. |

## Suggested Reading Paths

### 1) Understand the Kernel Pipeline
1. `kernel.md`
2. `kernel-policy_interface.md`
3. `registry_usage.md`

### 2) Understand the Governance Data Model
1. `agent_insturctions_design.md`
2. `registry_usage.md`
3. `kernel.md`

### 3) Extend ArbiterOS for a New Agent
1. `add_new_agent.md`
2. `agent_insturctions_design.md`
3. `kernel-policy_interface.md`

### 4) Operate and Inspect Governance in UI
1. `visualization.md`
2. `kernel.md`