from __future__ import annotations

import json

from arbiteros_kernel.cost.down import (
    apply_agent_scaffold_compaction_to_request,
    apply_phase3_runtime_policy_to_request,
)
from arbiteros_kernel.litellm_callback import (
    _lower_apply_patch_command,
    _lower_apply_patch_tool_calls_in_message,
)
from arbiteros_kernel.policy_runtime import policy_runtime_override


def _policy() -> dict:
    return {
        "schema_version": "cost_down_policy.v1",
        "context_policies": {
            "tool_ctx": {
                "action": "compress",
                "rule_id": "COMPRESS_LONG_MEDIUM_VALUE",
                "risk_level": "medium",
                "confidence": 0.8,
                "compress_target_ratio": 0.2,
                "source_type": "tool",
                "progress_signal": "stack_trace",
            }
        },
        "execution": {
            "schema_version": "phase3_runtime_execution.v1",
            "rule_compression": {"default_target_ratio": 0.30},
        },
    }


def _long_output(prefix: str = "same informational line") -> str:
    return "\n".join(f"{prefix} {index}" for index in range(160))


def test_scaffold_compaction_preserves_task_and_submission(monkeypatch) -> None:
    monkeypatch.setenv("ARBITEROS_COST_DOWN_AGENT_SCAFFOLD_COMPACTION", "1")
    request = {
        "messages": [
            {
                "role": "user",
                "content": """Task: fix pkg/demo.py and run tests/test_demo.py.

## Recommended Workflow
1. Analyze every directory.
2. Create a reproduction script.
3. Read every configuration file before editing.
4. Inspect the full repository history.
5. Explain every command before running it.
6. Repeat the complete test suite after each edit.
7. Re-open every modified file before submission.

## Command Execution Rules
You issue commands in a subshell.
Example of a CORRECT response: inspect everything first.
Always provide a detailed narrative before and after each command.
Never combine related inspections into one command.
Print the current directory before every operation.
Describe all expected output before invoking a tool.

## Environment Details
You have a full Linux shell environment.
The repository is already checked out and dependencies may be installed.
Standard command-line tools are available in the environment.
You may inspect files, run tests, and edit the working tree.

## Submission
Run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt`.
""",
            }
        ]
    }

    updated, stats = apply_agent_scaffold_compaction_to_request(
        request, trace_id="stable_scaffold"
    )

    content = updated["messages"][0]["content"]
    assert stats["changed"] is True
    assert "pkg/demo.py" in content
    assert "tests/test_demo.py" in content
    assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in content
    assert "Example of a CORRECT response" not in content
    assert len(content) < len(request["messages"][0]["content"])


def test_dependency_pruning_keeps_referenced_evidence() -> None:
    referenced = _long_output("FAILED tests/test_demo.py::test_demo")
    unused = "\n".join("same informational line" for _ in range(160))
    request = {
        "messages": [
            {"role": "user", "content": "Fix the failing demo test."},
            {"role": "tool", "tool_call_id": "call_unused", "content": unused},
            {"role": "tool", "tool_call_id": "call_needed", "content": referenced},
            {"role": "tool", "tool_call_id": "call_recent", "content": "recent"},
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_next",
                        "function": {
                            "name": "bash",
                            "arguments": '{"reference_tool_id":"call_needed"}',
                        },
                    }
                ],
            },
        ]
    }
    cfg = {
        "cost_down": {
            "phase3_runtime": {
                "enabled": True,
                "enable_rule_compression": True,
                "enable_llm_compression": False,
                "policy": _policy(),
                "allow_message_fallback": True,
                "message_fallback_min_input_tokens": 999999,
                "stale_tool_keep_recent": 1,
                "dependency_evidence_pruning": True,
                "dependency_prune_min_tool_results": 3,
                "dependency_prune_min_chars": 100,
                "dependency_prune_target_ratio": 0.08,
                "dependency_prune_max_target_chars": 360,
                "dependency_protected_keep_recent": 0,
                "dependency_protect_high_signal_only": True,
                "min_target_chars": 140,
                "max_target_chars": 1200,
            }
        }
    }

    with policy_runtime_override(cfg):
        updated, stats = apply_phase3_runtime_policy_to_request(
            request, trace_id="stable_dependency"
        )

    assert "pruned unreferenced tool evidence" in updated["messages"][1]["content"]
    assert updated["messages"][2]["content"] == referenced
    assert stats["dependency_evidence_graph"]["prunable_tool_call_ids"] == [
        "call_unused"
    ]
    assert stats["actions"]["phase3_dependency_evidence_prune"]["messages"] == 1


def test_duplicate_tool_output_elision_preserves_latest_copy() -> None:
    duplicate = (
        "<returncode>0</returncode>\n"
        "<output>\n"
        + "\n".join(
            f"diff --git a/pkg/demo.py b/pkg/demo.py line {index}"
            for index in range(160)
        )
        + "\n</output>"
    )
    request = {
        "messages": [
            {"role": "tool", "tool_call_id": "call_old", "content": duplicate},
            {"role": "tool", "tool_call_id": "call_mid", "content": "middle"},
            {"role": "tool", "tool_call_id": "call_new", "content": duplicate},
        ]
    }
    cfg = {
        "cost_down": {
            "phase3_runtime": {
                "enabled": True,
                "enable_rule_compression": True,
                "enable_llm_compression": False,
                "policy": _policy(),
                "allow_message_fallback": True,
                "stale_tool_keep_recent": 1,
                "duplicate_tool_elision": True,
                "duplicate_tool_min_chars": 200,
                "min_target_chars": 140,
                "max_target_chars": 420,
            }
        }
    }

    with policy_runtime_override(cfg):
        updated, stats = apply_phase3_runtime_policy_to_request(
            request, trace_id="stable_duplicate"
        )

    assert "duplicate tool output omitted" in updated["messages"][0]["content"]
    assert updated["messages"][2]["content"] == duplicate
    assert stats["actions"]["phase3_duplicate_tool_elide"]["messages"] == 1


def test_apply_patch_lowering_is_opt_in(monkeypatch) -> None:
    command = (
        "cd /testbed && apply_patch <<'PATCH'\n"
        "*** Begin Patch\n*** Update File: pkg/demo.py\n@@\n-old\n+new\n"
        "*** End Patch\nPATCH"
    )
    lowered = _lower_apply_patch_command(command)
    assert lowered is not None
    assert "python - <<'PY'" in lowered

    message = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "bash",
                    "arguments": json.dumps({"command": command}),
                },
            }
        ],
    }
    assert _lower_apply_patch_tool_calls_in_message(message) == 0
    monkeypatch.setenv("ARBITEROS_COST_DOWN_LOWER_APPLY_PATCH_TOOL_CALLS", "1")
    assert _lower_apply_patch_tool_calls_in_message(message) == 1
    arguments = json.loads(message["tool_calls"][0]["function"]["arguments"])
    assert "apply_patch <<'PATCH'" not in arguments["command"]
