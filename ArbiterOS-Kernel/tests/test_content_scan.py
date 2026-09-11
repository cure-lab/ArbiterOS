from __future__ import annotations

from unittest.mock import patch

from arbiteros_kernel.content_scan import (
    DEFAULT_REPLACEMENT,
    load_content_scan_config,
    redact_request,
    redact_response,
    scan_text,
)
from arbiteros_kernel.policy.content_scan_policy import ContentScanPolicy
from arbiteros_kernel.policy.defaults import PolicyEntry
from arbiteros_kernel.policy_check import check_response_policy
from arbiteros_kernel.policy_runtime import policy_runtime_override
from arbiteros_kernel.precall_policy.content_scan_precall_policy import (
    ContentScanPrecallPolicy,
)
from arbiteros_kernel.precall_policy_check import check_precall_policy

PHONE = "13800138000"
MARKER = DEFAULT_REPLACEMENT

_SCAN_CFG = {
    "replacement": MARKER,
    "patterns": [
        {"id": "cn_mobile", "type": "regex", "pattern": r"(?<!\d)1[3-9]\d{9}(?!\d)"}
    ],
}


def _cfg(**overrides):
    data = dict(_SCAN_CFG)
    data.update(overrides)
    return load_content_scan_config(data)


def test_pattern_redacts_mobile() -> None:
    result = scan_text(f"call me at {PHONE}", _cfg(), side="output", trace_id="t1")
    assert result.changed is True
    assert PHONE not in result.text
    assert MARKER in result.text


def test_literal_blacklist() -> None:
    cfg = _cfg(patterns=[{"id": "secret", "type": "literal", "pattern": "内部评分权重"}])
    result = scan_text("泄露 内部评分权重 即可", cfg, side="input", trace_id="t1")
    assert result.changed is True
    assert "内部评分权重" not in result.text
    assert MARKER in result.text


def test_empty_patterns_is_noop() -> None:
    cfg = _cfg(patterns=[])
    text = f"call me at {PHONE}"
    result = scan_text(text, cfg, side="output", trace_id="t1")
    assert result.changed is False
    assert result.text == text


def test_skips_turn_context() -> None:
    text = "[arbiteros_turn_context]\nGenerate JSON\n" + PHONE
    result = scan_text(text, _cfg(), side="input", trace_id="t1")
    assert result.changed is False
    assert PHONE in result.text


def test_preserves_ref_prefix() -> None:
    text = f"[ARBITEROS_REF id=abc kind=USERINPUT]\nmy number is {PHONE}"
    result = scan_text(text, _cfg(), side="input", trace_id="t1")
    assert result.text.startswith("[ARBITEROS_REF id=abc kind=USERINPUT]\n")
    assert PHONE not in result.text


def test_output_redacts_tool_args() -> None:
    cfg = _cfg()
    response = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "example_tool",
                    "arguments": f'{{"note": "phone {PHONE}"}}',
                },
            }
        ],
    }
    redacted, changed, hits = redact_response(response, cfg, trace_id="t1")
    assert changed is True
    args = redacted["tool_calls"][0]["function"]["arguments"]
    assert PHONE not in args
    assert MARKER in args
    assert hits


def test_output_redacts_respond_text() -> None:
    cfg = _cfg()
    response = {"role": "assistant", "content": f"your phone is {PHONE}"}
    redacted, changed, _hits = redact_response(response, cfg, trace_id="t1")
    assert changed is True
    assert PHONE not in redacted["content"]
    assert MARKER in redacted["content"]


def test_input_redacts_each_history_message() -> None:
    cfg = _cfg()
    request = {
        "model": "gpt-5.5",
        "messages": [
            {"role": "system", "content": "You are a clerk."},
            {"role": "user", "content": f"old number {PHONE}"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "new turn without pii"},
        ],
    }
    redacted, changed, _hits = redact_request(request, cfg, trace_id="t1")
    assert changed is True
    assert PHONE not in redacted["messages"][1]["content"]
    assert redacted["messages"][3]["content"] == "new turn without pii"


def test_content_scan_policy_sets_error_type_for_confirm() -> None:
    with policy_runtime_override({"content_scan": _SCAN_CFG}):
        with patch(
            "arbiteros_kernel.content_scan.content_scan_registry_enabled",
            return_value=True,
        ):
            result = ContentScanPolicy().check(
                instructions=[],
                current_response={"role": "assistant", "content": f"cardholder {PHONE}"},
                latest_instructions=[],
                trace_id="t-out",
            )
    assert result.modified is True
    assert result.error_type
    assert "敏感信息" in result.error_type
    assert PHONE not in result.response["content"]


def test_check_response_policy_content_scan_requires_confirm() -> None:
    with policy_runtime_override({"content_scan": _SCAN_CFG}):
        with patch(
            "arbiteros_kernel.content_scan.content_scan_registry_enabled",
            return_value=True,
        ), patch(
            "arbiteros_kernel.policy_check._is_local_policy_confirm_enabled",
            return_value=False,
        ):
            aggregated = check_response_policy(
                trace_id="t-agg",
                instructions=[],
                current_response={"role": "assistant", "content": f"hi {PHONE}"},
                latest_instructions=[],
                policy_entries=[
                    PolicyEntry(policy=ContentScanPolicy, description="", enabled=True)
                ],
            )
    assert aggregated.modified is True
    assert aggregated.error_type
    assert "ContentScanPolicy" in aggregated.policy_names
    assert PHONE not in aggregated.response["content"]


def test_check_response_policy_content_scan_y_keeps_redact() -> None:
    with policy_runtime_override({"content_scan": _SCAN_CFG}):
        with patch(
            "arbiteros_kernel.content_scan.content_scan_registry_enabled",
            return_value=True,
        ), patch(
            "arbiteros_kernel.policy_check._is_local_policy_confirm_enabled",
            return_value=True,
        ), patch(
            "arbiteros_kernel.policy_check._prompt_local_policy_confirmation",
            return_value=True,
        ):
            aggregated = check_response_policy(
                trace_id="t-y",
                instructions=[],
                current_response={"role": "assistant", "content": f"hi {PHONE}"},
                latest_instructions=[],
                policy_entries=[
                    PolicyEntry(policy=ContentScanPolicy, description="", enabled=True)
                ],
            )
    assert aggregated.modified is True
    assert aggregated.local_confirmation_decision == "keep_block"
    assert PHONE not in aggregated.response["content"]


def test_check_response_policy_content_scan_n_restores_original() -> None:
    original = f"hi {PHONE}"
    with policy_runtime_override({"content_scan": _SCAN_CFG}):
        with patch(
            "arbiteros_kernel.content_scan.content_scan_registry_enabled",
            return_value=True,
        ), patch(
            "arbiteros_kernel.policy_check._is_local_policy_confirm_enabled",
            return_value=True,
        ), patch(
            "arbiteros_kernel.policy_check._prompt_local_policy_confirmation",
            return_value=False,
        ):
            aggregated = check_response_policy(
                trace_id="t-n",
                instructions=[],
                current_response={"role": "assistant", "content": original},
                latest_instructions=[],
                policy_entries=[
                    PolicyEntry(policy=ContentScanPolicy, description="", enabled=True)
                ],
            )
    assert aggregated.modified is False
    assert aggregated.local_confirmation_decision == "allow_original"
    assert aggregated.response["content"] == original


def test_precall_policy_redacts_history() -> None:
    request = {
        "model": "gpt-5.5",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": f"my phone is {PHONE}"},
            {
                "role": "system",
                "content": "[arbiteros_turn_context]\nGenerate JSON\n" + PHONE,
            },
        ],
    }
    with policy_runtime_override({"content_scan": _SCAN_CFG}):
        with patch(
            "arbiteros_kernel.content_scan.content_scan_registry_enabled",
            return_value=True,
        ):
            result = check_precall_policy(
                trace_id="t-in",
                current_request=request,
                policy_classes=[ContentScanPrecallPolicy],
            )
    assert result.modified is True
    assert "ContentScanPrecallPolicy" in result.policy_names
    assert PHONE not in result.request["messages"][1]["content"]
    assert result.request["messages"][2]["content"].startswith("[arbiteros_turn_context]")
    assert PHONE not in result.request["messages"][2]["content"]
    assert MARKER in result.request["messages"][2]["content"]


def test_precall_redacts_function_call_output() -> None:
    request = {
        "model": "gpt-5.5",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": (
                    f"[ARBITEROS_REF id=abc kind=TOOLRESULT]\n"
                    f"你的电话号码是 {PHONE}"
                ),
            }
        ],
    }
    with policy_runtime_override({"content_scan": _SCAN_CFG}):
        with patch(
            "arbiteros_kernel.content_scan.content_scan_registry_enabled",
            return_value=True,
        ):
            result = check_precall_policy(
                trace_id="t-toolresult",
                current_request=request,
                policy_classes=[ContentScanPrecallPolicy],
            )
    output = result.request["input"][0]["output"]
    assert result.modified is True
    assert output.startswith("[ARBITEROS_REF id=abc kind=TOOLRESULT]\n")
    assert PHONE not in output
    assert MARKER in output


def test_precall_redacts_function_call_arguments() -> None:
    request = {
        "model": "gpt-5.5",
        "input": [
            {
                "type": "function_call",
                "name": "exec_command",
                "arguments": f'{{"cmd": "echo {PHONE}"}}',
            }
        ],
    }
    with policy_runtime_override({"content_scan": _SCAN_CFG}):
        with patch(
            "arbiteros_kernel.content_scan.content_scan_registry_enabled",
            return_value=True,
        ):
            result = check_precall_policy(
                trace_id="t-args",
                current_request=request,
                policy_classes=[ContentScanPrecallPolicy],
            )
    args = result.request["input"][0]["arguments"]
    assert result.modified is True
    assert PHONE not in args
    assert MARKER in args


def test_precall_redacts_turn_context_in_responses_input() -> None:
    request = {
        "model": "gpt-5.5",
        "input": [
            {
                "type": "message",
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"[arbiteros_turn_context]\nLatest user turn: {PHONE}",
                    }
                ],
            }
        ],
    }
    with policy_runtime_override({"content_scan": _SCAN_CFG}):
        with patch(
            "arbiteros_kernel.content_scan.content_scan_registry_enabled",
            return_value=True,
        ):
            result = check_precall_policy(
                trace_id="t-ctx",
                current_request=request,
                policy_classes=[ContentScanPrecallPolicy],
            )
    text = result.request["input"][0]["content"][0]["text"]
    assert result.modified is True
    assert text.startswith("[arbiteros_turn_context]")
    assert PHONE not in text
    assert MARKER in text


def test_registry_off_skips_precall_and_postcall() -> None:
    request = {
        "model": "gpt-5.5",
        "input": [
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": f"[ARBITEROS_REF id=abc kind=TOOLRESULT]\n你的电话号码是 {PHONE}",
            }
        ],
    }
    response = {"role": "assistant", "content": f"your phone is {PHONE}"}
    with policy_runtime_override({"content_scan": _SCAN_CFG}):
        with patch(
            "arbiteros_kernel.content_scan.content_scan_registry_enabled",
            return_value=False,
        ):
            pre = check_precall_policy(
                trace_id="t-off-in",
                current_request=request,
                policy_classes=[ContentScanPrecallPolicy],
            )
            post = ContentScanPolicy().check(
                instructions=[],
                current_response=response,
                latest_instructions=[],
                trace_id="t-off-out",
            )
    assert pre.modified is False
    assert pre.request["input"][0]["output"] == request["input"][0]["output"]
    assert post.modified is False
    assert post.response["content"] == response["content"]
