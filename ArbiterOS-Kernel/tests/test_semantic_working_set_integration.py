"""Kernel integration guards for semantic working-set mutation."""

from __future__ import annotations

from arbiteros_kernel.precall_policy.compress_executor import compress_text
from arbiteros_kernel.precall_policy.prompt_mutator import (
    apply_context_actions_to_request,
)
from flow_cost_doctor.cost_down import OptimizationStrategy, StrategyTarget


def _compress_strategy(context_id: str, ratio: float = 0.1) -> OptimizationStrategy:
    return OptimizationStrategy(
        target=StrategyTarget(
            target_type="context_content",
            target_id=context_id,
        ),
        action="COMPRESS",
        reason="test",
        estimated_token_savings=500,
        confidence=0.8,
        risk_level="medium",
        rule_id="COMPRESS_WORKSET_SUPERSEDED",
        compress_target_ratio=ratio,
        producer_step_index=0,
    )


def test_superseded_artifact_mutation_uses_stable_pointer():
    context_id = "obs_old_parser"
    body = "def parse(value):\n" + ("    value = value.strip()\n" * 300)
    request = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "[ARBITEROS_REF id=old-read kind=TOOLRESULT]\n"
                    f"{body}"
                ),
            }
        ]
    }
    actions = {
        context_id: {
            "effective_action": "COMPRESS",
            "action": "compress",
            "semantic_fold": "superseded_artifact",
            "artifact_keys": ["src/parser.py"],
        }
    }
    kwargs = {
        "instruction_to_context": {"old-read": context_id},
        "context_actions": actions,
        "strategies_by_context": {
            context_id: _compress_strategy(context_id, ratio=0.05)
        },
        "step_index": 12,
        "stage": "coding",
        "phase": "D",
        "rule_engine": {
            "compress_backend": "rule",
            "semantic_working_set": {"enabled": True},
        },
    }
    first, modified = apply_context_actions_to_request(request, **kwargs)
    second, modified_again = apply_context_actions_to_request(request, **kwargs)
    assert modified is True
    assert modified_again is True
    assert first == second
    content = first["messages"][0]["content"]
    assert "src/parser.py" in content
    assert "later canonical read" in content
    assert len(content) < len(request["messages"][0]["content"]) / 5


def test_transient_failure_mutation_keeps_cause_but_not_bulky_stack():
    context_id = "obs_bad_selector"
    body = (
        "ERROR: not found: tests/test_parser.py::MissingClass::test_x\n"
        "(no match in any of [<Module test_parser.py>])\n"
        "no tests ran in 0.02s\n"
        + ("stack frame noise\n" * 300)
    )
    request = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "[ARBITEROS_REF id=bad-selector kind=TOOLRESULT]\n"
                    f"{body}"
                ),
            }
        ]
    }
    actions = {
        context_id: {
            "effective_action": "COMPRESS",
            "action": "compress",
            "semantic_fold": "transient_failure",
            "evidence_role": "transient_failure",
        }
    }
    mutated, modified = apply_context_actions_to_request(
        request,
        instruction_to_context={"bad-selector": context_id},
        context_actions=actions,
        strategies_by_context={
            context_id: _compress_strategy(context_id, ratio=0.05)
        },
        step_index=12,
        stage="coding",
        phase="D",
        rule_engine={
            "compress_backend": "rule",
            "semantic_working_set": {"enabled": True},
        },
    )
    assert modified is True
    content = mutated["messages"][0]["content"]
    assert "not found" in content
    assert "does not establish" in content
    assert "stack frame noise" not in content
    assert len(content) < len(request["messages"][0]["content"]) / 5


def test_rule_compress_preserves_task_focused_middle_lines_without_ratio_squaring():
    middle = (
        "def normalize_separator(candidate):\n"
        "    return CanonicalPath(candidate).as_posix()\n"
    )
    body = (
        "\n".join(f"header_{index} = {index}" for index in range(120))
        + "\n"
        + middle
        + "\n"
        + "\n".join(f"tail_{index} = {index}" for index in range(120))
    )
    compressed = compress_text(
        body,
        target_ratio=0.1,
        context_id="focused-code",
        backend="rule",
        rule_engine={"compress_backend": "rule"},
        progress_signal="code_context",
        source_type="file",
        focus_terms={"normalize", "separator", "as-posix"},
    )
    assert "normalize_separator" in compressed
    assert "as_posix" in compressed
    # A complete semantic unit may naturally be smaller than the ratio budget.
    # The regression guard is semantic completeness and absence of a second
    # forced truncation, not padding the capsule with unrelated lines.
    assert "semantic_unit_capsule" in compressed
    assert "forced_ratio_trim" not in compressed
    assert "return CanonicalPath(candidate).as_posix()" in compressed
    assert len(compressed) <= int(len(body) * 0.12) + 160


def test_kernel_does_not_retruncate_complete_semantic_unit_capsule():
    unrelated = "\n".join(
        f"{index:6}\tdef unrelated_{index}():\n"
        f"{index + 1:6}\t    value = {index}\n"
        f"{index + 2:6}\t    return value"
        for index in range(10, 190, 6)
    )
    focused = (
        "   500\tdef normalize_ignore_paths(values):\n"
        "   501\t    patterns = []\n"
        "   502\t    for value in values:\n"
        "   503\t        normalized = PlatformPath(value).as_posix()\n"
        "   504\t        patterns.append(compile_pattern(normalized))\n"
        "   505\t    return patterns\n"
    )
    body = unrelated + "\n" + focused + "\n" + unrelated
    compressed = compress_text(
        body,
        target_ratio=0.05,
        context_id="complete-semantic-unit",
        backend="rule",
        rule_engine={"compress_backend": "rule"},
        progress_signal="code_context",
        source_type="file",
        focus_terms={"normalize", "ignore-path", "as-posix"},
    )
    assert "semantic_unit_capsule" in compressed
    assert "def normalize_ignore_paths" in compressed
    assert "PlatformPath(value).as_posix()" in compressed
    assert "return patterns" in compressed
    # This coherent block intentionally exceeds the raw 5% target; the kernel
    # must honor the rule backend's bounded semantic-unit budget.
    assert len(compressed) > int(len(body) * 0.05 * 1.15)
    assert len(compressed) <= min(1800, len(body) // 2)


def test_compression_cache_key_includes_focus_terms():
    body = (
        "\n".join(f"alpha_value_{index} = {index}" for index in range(100))
        + "\ndef beta_normalize(value): return value\n"
        + "\n".join(f"gamma_value_{index} = {index}" for index in range(100))
    )
    cache: dict[str, str] = {}
    alpha = compress_text(
        body,
        target_ratio=0.1,
        context_id="same-context",
        backend="rule",
        rule_engine={"compress_backend": "rule"},
        focus_terms={"alpha"},
        cache=cache,
    )
    beta = compress_text(
        body,
        target_ratio=0.1,
        context_id="same-context",
        backend="rule",
        rule_engine={"compress_backend": "rule"},
        focus_terms={"beta", "normalize"},
        cache=cache,
    )
    assert alpha != beta
    assert "beta_normalize" in beta
    assert len(cache) == 2


def test_semantic_carriers_skip_off_frontier_dependency_prune():
    key_line = (
        "def normalize_separator(candidate):\n"
        "    return CanonicalPath(candidate).as_posix()\n"
    )
    body = (
        "\n".join(f"prefix_{index} = {index}" for index in range(120))
        + "\n"
        + key_line
        + "\n"
        + "\n".join(f"suffix_{index} = {index}" for index in range(120))
    )
    for working_set in (
        "COMPRESS_WORKSET_SUPPORT",
        "COMPRESS_WORKSET_TEST_CONTRACT",
        "COMPRESS_WORKSET_CURRENT_CODE",
    ):
        context_id = f"obs_{working_set.lower()}"
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "[ARBITEROS_REF id=semantic-read kind=TOOLRESULT]\n"
                        f"{body}"
                    ),
                }
            ]
        }
        strategy = _compress_strategy(context_id, ratio=0.25)
        strategy.rule_id = working_set
        mutated, modified = apply_context_actions_to_request(
            request,
            instruction_to_context={"semantic-read": context_id},
            context_actions={
                context_id: {
                    "effective_action": "COMPRESS",
                    "action": "compress",
                    "working_set": working_set,
                    "semantic_focus_terms": [
                        "normalize",
                        "separator",
                        "as-posix",
                    ],
                    "progress_signal": "code_context",
                    "source_type": "file",
                }
            },
            strategies_by_context={context_id: strategy},
            step_index=20,
            stage="coding",
            phase="D",
            rule_engine={
                "compress_backend": "rule",
                "dep_lifetime": {
                    "enabled": True,
                    "only_compress_pool": True,
                    "min_chars": 200,
                    "target_ratio": 0.02,
                    "min_target_chars": 80,
                    "max_target_chars": 100,
                },
            },
            live_context_ids=set(),
        )
        assert modified is True
        content = mutated["messages"][0]["content"]
        assert "normalize_separator" in content
        assert "as_posix" in content
