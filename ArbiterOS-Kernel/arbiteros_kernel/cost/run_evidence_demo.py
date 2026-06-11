from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

from arbiteros_kernel.cost import doctor as cost_doctor
from arbiteros_kernel.cost import telemetry as cost_telemetry
from arbiteros_kernel.instruction_parsing.builder import InstructionBuilder
from arbiteros_kernel.instruction_parsing.tool_parsers import parse_tool_instruction
from arbiteros_kernel.policy.unary_gate_policy import UnaryGatePolicy


_KERNEL_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LITELLM_CONFIG = _KERNEL_ROOT / "litellm_config.yaml"
_DEFAULT_ARIS_REPO = Path(
    "/mnt/d/Experiments/Policy/test/Auto-claude-code-research-in-sleep"
)
_DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "demo_outputs"
_MCP_SERVERS = (
    "claude-review",
    "gemini-review",
    "manual-review",
    "llm-chat",
    "minimax-chat",
    "codex-image2",
)


class Recorder:
    def __init__(self, *, interactive: bool) -> None:
        self.interactive = interactive
        self.lines: list[str] = []

    def line(self, text: str = "") -> None:
        print(text)
        self.lines.append(text)

    def section(self, title: str) -> None:
        self.line()
        self.line("=" * 78)
        self.line(title)
        self.line("=" * 78)

    def pause(self, message: str) -> None:
        if self.interactive:
            self.line()
            input(f"{message} Press Enter to continue...")

    def markdown(self) -> str:
        return "\n".join(self.lines).rstrip() + "\n"


def _encode_jsonrpc(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


def _read_jsonrpc_message(proc: subprocess.Popen[bytes]) -> dict[str, Any]:
    assert proc.stdout is not None
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = proc.stdout.read(1)
        if not chunk:
            raise RuntimeError("MCP server closed stdout before sending a header")
        header += chunk
    content_length: Optional[int] = None
    for raw_line in header.decode("utf-8", errors="replace").split("\r\n"):
        if raw_line.lower().startswith("content-length:"):
            content_length = int(raw_line.split(":", 1)[1].strip())
    if content_length is None:
        raise RuntimeError("MCP response did not include Content-Length")
    return json.loads(proc.stdout.read(content_length).decode("utf-8"))


def _call_tools_list(server_py: Path, repo: Path) -> list[str]:
    proc = subprocess.Popen(
        [sys.executable, str(server_py)],
        cwd=str(repo),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        assert proc.stdin is not None
        proc.stdin.write(
            _encode_jsonrpc(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            )
        )
        proc.stdin.flush()
        _read_jsonrpc_message(proc)
        proc.stdin.write(
            _encode_jsonrpc(
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
            )
        )
        proc.stdin.flush()
        response = _read_jsonrpc_message(proc)
        tools = response.get("result", {}).get("tools", [])
        if not isinstance(tools, list):
            return []
        return [
            str(tool.get("name"))
            for tool in tools
            if isinstance(tool, dict) and tool.get("name")
        ]
    finally:
        proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()


def _flow_metadata(tool_name: str) -> tuple[str, str, bool, bool]:
    parsed = parse_tool_instruction(tool_name, {"prompt": "demo", "jobId": "job-1"})
    custom = parsed.security_type.get("custom", {})
    metadata = custom.get("policy_metadata", {}) if isinstance(custom, dict) else {}
    return (
        parsed.instruction_type,
        str(metadata.get("mcp_flow_kind") or ""),
        bool(metadata.get("unknown_mcp_tool", False)),
        bool(custom.get("review_required", False)) if isinstance(custom, dict) else False,
    )


def _tool_call(tool_name: str, tool_call_id: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": tool_call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


def run_mcp_parser_demo(
    rec: Recorder, aris_repo: Path
) -> tuple[bool, list[dict[str, Any]]]:
    rec.section("Demo 1: real ARIS MCP tools -> ArbiterOS parser")
    rec.line(f"ARIS repo: {aris_repo}")
    rec.line("This step starts local MCP servers and calls JSON-RPC tools/list.")
    rec.pause("About to start local MCP servers.")

    rows: list[dict[str, Any]] = []
    for service in _MCP_SERVERS:
        server_py = aris_repo / "mcp-servers" / service / "server.py"
        if not server_py.exists():
            rows.append({"service": service, "error": "server.py not found"})
            continue
        try:
            tool_names = _call_tools_list(server_py, aris_repo)
        except Exception as exc:
            rows.append({"service": service, "error": str(exc)})
            continue
        for tool in tool_names:
            full_name = f"mcp__{service}__{tool}"
            instruction_type, flow, unknown, review_required = _flow_metadata(full_name)
            rows.append(
                {
                    "service": service,
                    "tool": tool,
                    "full_name": full_name,
                    "instruction_type": instruction_type,
                    "flow": flow,
                    "unknown": unknown,
                    "review_required": review_required,
                }
            )

    rec.line()
    rec.line("| service | tool | ArbiterOS type | flow | unknown |")
    rec.line("| --- | --- | --- | --- | --- |")
    for row in rows:
        if row.get("error"):
            rec.line(f"| {row['service']} | ERROR: {row['error']} | - | - | - |")
            continue
        rec.line(
            "| {service} | {tool} | {instruction_type} | {flow} | {unknown} |".format(
                **row
            )
        )

    known_rows = [row for row in rows if not row.get("error")]
    unknown_rows = [row for row in known_rows if row.get("unknown")]
    errors = [row for row in rows if row.get("error")]
    passed = len(known_rows) == 16 and not unknown_rows and not errors
    rec.line()
    rec.line(
        "MCP parser check: "
        f"{len(known_rows)} tools discovered, {len(unknown_rows)} unknown, "
        f"{len(errors)} server errors."
    )
    rec.line("Expected: 16 discovered tools, 0 unknown, 0 errors.")
    rec.line(f"Result: {'PASS' if passed else 'FAIL'}")
    return passed, rows


def _synthetic_cost_entries() -> list[dict[str, Any]]:
    return [
        {
            "ts": "2026-06-11T00:00:00",
            "trace_id": "demo-cost-trace",
            "request_model": "gpt-5.1",
            "response_model": "gpt-5.1",
            "pricing_model": "gpt-5.1",
            "usage": {
                "prompt_tokens": 900,
                "completion_tokens": 80,
                "total_tokens": 980,
                "cached_tokens": 100,
            },
            "estimated_cost": {
                "input_cost_usd": 0.001,
                "output_cost_usd": 0.001,
                "total_cost_usd": 0.002,
            },
        },
        {
            "ts": "2026-06-11T00:00:01",
            "trace_id": "demo-cost-trace",
            "request_model": "gpt-5.1",
            "response_model": "gpt-5.1",
            "pricing_model": "gpt-5.1",
            "usage": {
                "prompt_tokens": 5200,
                "completion_tokens": 70,
                "total_tokens": 5270,
                "cached_tokens": 1000,
            },
            "estimated_cost": {
                "input_cost_usd": 0.006,
                "output_cost_usd": 0.001,
                "total_cost_usd": 0.007,
            },
        },
    ]


def run_cost_doctor_demo(rec: Recorder) -> tuple[bool, dict[str, Any]]:
    rec.section("Demo 2: cost telemetry log -> Cost Doctor attribution")
    rec.line("This step writes a temporary cost_telemetry.jsonl and analyzes it.")
    rec.line("The synthetic trace intentionally contains a high-input, low-output step.")
    rec.pause("About to run Cost Doctor on a temporary trace.")

    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".jsonl", delete=False
    ) as f:
        log_path = Path(f.name)
        for entry in _synthetic_cost_entries():
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        summary = cost_doctor.analyze_cost_log(
            log_path,
            trace_id="demo-cost-trace",
            config={
                "min_input_heavy_tokens": 1000,
                "low_output_prompt_tokens": 800,
                "low_output_completion_tokens": 128,
                "cost_spike_ratio": 0.5,
            },
        )
    finally:
        try:
            log_path.unlink()
        except OSError:
            pass

    rec.line()
    rec.line(cost_doctor.build_markdown_report(summary).rstrip())
    rule_ids = {item.get("rule_id") for item in summary.get("diagnoses", [])}
    expected = {"CD-INPUT-HEAVY", "CD-LOW-OUTPUT", "CD-COST-SPIKE"}
    passed = expected.issubset(rule_ids)
    rec.line()
    rec.line(f"Cost Doctor rules observed: {', '.join(sorted(str(x) for x in rule_ids))}")
    rec.line(f"Expected at least: {', '.join(sorted(expected))}")
    rec.line(f"Result: {'PASS' if passed else 'FAIL'}")
    return passed, summary


def _unknown_tool_gate_result(tool_name: str) -> tuple[bool, str, dict[str, Any]]:
    builder = InstructionBuilder(trace_id="demo-unknown-mcp")
    instruction = builder.add_from_tool_call(
        tool_name=tool_name,
        tool_call_id="unknown1",
        arguments={"target": "customer", "opaque": True},
    )
    result = UnaryGatePolicy().check(
        instructions=builder.instructions,
        current_response={
            "role": "assistant",
            "tool_calls": [
                _tool_call(
                    tool_name,
                    "unknown1",
                    {"target": "customer", "opaque": True},
                )
            ],
        },
        latest_instructions=[instruction],
        trace_id="demo-unknown-mcp",
    )
    response = result.response if isinstance(result.response, dict) else {}
    blocked = bool(result.error_type) or response.get("tool_calls") is None
    return blocked, str(result.error_type or ""), instruction


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(stripped[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _run_unknown_guidance_api_check(
    *,
    config_path: Path,
    preferred_model: Optional[str],
    block_message: str,
    tool_name: str,
) -> tuple[bool, dict[str, Any]]:
    model, api_base, api_key = _read_litellm_api_config(config_path, preferred_model)
    request_data = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are checking whether a safety prompt is understandable. "
                    "Return JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Check this ArbiterOS unknown-MCP block message. Return JSON with "
                    "booleans has_tool_name, has_parser_guidance, has_persistent_allowlist_guidance, "
                    "has_single_turn_allow_guidance, and a short summary.\n\n"
                    f"Tool name: {tool_name}\n\nMessage:\n{block_message}"
                ),
            },
        ],
        "max_completion_tokens": 180,
    }
    try:
        response = _post_json(_chat_completions_url(api_base), request_data, api_key)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if "max_completion_tokens" not in body:
            raise RuntimeError(f"HTTP {exc.code}: {body[:500]}") from exc
        request_data = dict(request_data)
        request_data.pop("max_completion_tokens", None)
        request_data["max_tokens"] = 180
        response = _post_json(_chat_completions_url(api_base), request_data, api_key)

    usage = cost_telemetry.extract_token_usage_from_response_obj(response)
    text = _extract_text(response)
    parsed = _extract_json_object(text)
    expected_keys = (
        "has_tool_name",
        "has_parser_guidance",
        "has_persistent_allowlist_guidance",
        "has_single_turn_allow_guidance",
    )
    passed = all(bool(parsed.get(key)) for key in expected_keys)
    return passed, {
        "model": model,
        "api_base": api_base,
        "usage": usage,
        "raw_response": text,
        "parsed": parsed,
    }


def run_unknown_tool_demo(
    rec: Recorder,
    *,
    config_path: Path,
    preferred_model: Optional[str],
    live_api: bool,
) -> tuple[bool, dict[str, Any]]:
    rec.section("Demo 3: unsupported MCP tools -> block, explain, allowlist")
    tool_name = "mcp__unknown-service__frobnicate"
    instruction_type, flow, unknown, review_required = _flow_metadata(tool_name)
    batch_names = [
        f"mcp__dtap-suite-{i:03d}__opaque_action_{i:03d}"
        for i in range(1, 121)
    ]
    batch_results = [_flow_metadata(name) for name in batch_names]
    batch_unknown_count = sum(1 for _, item_flow, item_unknown, _ in batch_results if item_flow == "unknown" and item_unknown)

    blocked, block_message, instruction = _unknown_tool_gate_result(tool_name)
    metadata = (
        instruction.get("security_type", {})
        .get("custom", {})
        .get("policy_metadata", {})
    )
    guidance_checks = {
        "mentions_tool_name": tool_name in block_message,
        "mentions_parser_flow_lowering": "parser/flow lowering" in block_message,
        "mentions_persistent_allowlist": "长期可信" in block_message and "加入" in block_message,
        "mentions_single_turn_allow": "本轮确认" in block_message and "放行原始调用" in block_message,
    }

    rec.line(f"Single unknown tool: {tool_name}")
    rec.line(f"Tool: {tool_name}")
    rec.line(f"ArbiterOS type: {instruction_type}")
    rec.line(f"Flow: {flow}")
    rec.line(f"unknown_mcp_tool: {unknown}")
    rec.line(f"review_required: {review_required}")
    rec.line(f"Gate blocked tool call: {blocked}")
    rec.line(f"Allowlist file hint: {metadata.get('unknown_mcp_allowlist_file')}")
    rec.line()
    rec.line("Block message excerpt:")
    rec.line("```text")
    rec.line(block_message[:1200])
    rec.line("```")
    rec.line()
    rec.line("Deterministic guidance checks:")
    for key, value in guidance_checks.items():
        rec.line(f"- {key}: {value}")
    rec.line()
    rec.line(f"Batch unknown MCP tools tested: {len(batch_names)}")
    rec.line(f"Batch unknown+review_required count: {batch_unknown_count}")

    old_env_allowlist = os.environ.get("ARBITEROS_UNKNOWN_MCP_ALLOWLIST")
    old_env_file = os.environ.get("ARBITEROS_UNKNOWN_MCP_ALLOWLIST_FILE")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as f:
        allowlist_path = Path(f.name)
        json.dump({"tools": [tool_name]}, f)
    try:
        os.environ.pop("ARBITEROS_UNKNOWN_MCP_ALLOWLIST", None)
        os.environ["ARBITEROS_UNKNOWN_MCP_ALLOWLIST_FILE"] = str(allowlist_path)
        allowlisted_type, allowlisted_flow, allowlisted_unknown, allowlisted_review = _flow_metadata(tool_name)
        allowlisted_blocked, _, _ = _unknown_tool_gate_result(tool_name)
    finally:
        try:
            allowlist_path.unlink()
        except OSError:
            pass
        if old_env_allowlist is None:
            os.environ.pop("ARBITEROS_UNKNOWN_MCP_ALLOWLIST", None)
        else:
            os.environ["ARBITEROS_UNKNOWN_MCP_ALLOWLIST"] = old_env_allowlist
        if old_env_file is None:
            os.environ.pop("ARBITEROS_UNKNOWN_MCP_ALLOWLIST_FILE", None)
        else:
            os.environ["ARBITEROS_UNKNOWN_MCP_ALLOWLIST_FILE"] = old_env_file

    rec.line()
    rec.line("Temporary allowlist check, simulating always allow:")
    rec.line(f"- allowlisted type: {allowlisted_type}")
    rec.line(f"- allowlisted flow: {allowlisted_flow}")
    rec.line(f"- allowlisted unknown_mcp_tool: {allowlisted_unknown}")
    rec.line(f"- allowlisted review_required: {allowlisted_review}")
    rec.line(f"- gate blocked after allowlist: {allowlisted_blocked}")

    api_result: dict[str, Any] = {"skipped": True}
    api_pass = True
    if live_api:
        rec.line()
        rec.line("Live API readability check:")
        try:
            api_pass, api_result = _run_unknown_guidance_api_check(
                config_path=config_path,
                preferred_model=preferred_model,
                block_message=block_message,
                tool_name=tool_name,
            )
            rec.line(f"- model: {api_result.get('model')}")
            rec.line(f"- usage: {json.dumps(api_result.get('usage'), ensure_ascii=False)}")
            rec.line(f"- parsed: {json.dumps(api_result.get('parsed'), ensure_ascii=False)}")
        except Exception as exc:
            api_pass = False
            api_result = {"error": str(exc)}
            rec.line(f"- API check error: {exc}")

    deterministic_pass = (
        instruction_type == "EXEC"
        and flow == "unknown"
        and unknown
        and review_required
        and blocked
        and batch_unknown_count == len(batch_names)
        and all(guidance_checks.values())
        and allowlisted_flow == "unknown_allowed"
        and not allowlisted_unknown
        and not allowlisted_review
        and not allowlisted_blocked
    )
    passed = deterministic_pass and api_pass
    rec.line(f"Result: {'PASS' if passed else 'FAIL'}")
    return passed, {
        "tool_name": tool_name,
        "blocked": blocked,
        "batch_tested": len(batch_names),
        "batch_unknown_count": batch_unknown_count,
        "guidance_checks": guidance_checks,
        "allowlisted": {
            "instruction_type": allowlisted_type,
            "flow": allowlisted_flow,
            "unknown_mcp_tool": allowlisted_unknown,
            "review_required": allowlisted_review,
            "blocked": allowlisted_blocked,
        },
        "api_check": api_result,
    }


def _strip_openai_prefix(model: str) -> str:
    value = model.strip()
    if value.lower().startswith("openai/"):
        return value.split("/", 1)[1]
    return value


def _chat_completions_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _read_litellm_api_config(
    config_path: Path, preferred_model: Optional[str]
) -> tuple[str, str, str]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RuntimeError("litellm_config.yaml is not a mapping")
    models = raw.get("model_list")
    if not isinstance(models, list):
        raise RuntimeError("litellm_config.yaml has no model_list")

    selected: Optional[dict[str, Any]] = None
    for item in models:
        if not isinstance(item, dict):
            continue
        if preferred_model and item.get("model_name") != preferred_model:
            continue
        selected = item
        break
    if selected is None:
        selected = next((item for item in models if isinstance(item, dict)), None)
    if selected is None:
        raise RuntimeError("No model entry found in litellm_config.yaml")

    params = selected.get("litellm_params")
    params = params if isinstance(params, dict) else {}
    model = str(selected.get("model_name") or params.get("model") or "").strip()
    api_base = str(params.get("api_base") or params.get("base_url") or "").strip()
    api_key = str(params.get("api_key") or "").strip()
    if not model or not api_base or not api_key:
        raise RuntimeError("Selected model is missing model/api_base/api_key")
    return _strip_openai_prefix(model), api_base, api_key


def _post_json(url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _extract_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"].strip()
    return ""


def run_live_api_demo(
    rec: Recorder,
    *,
    config_path: Path,
    preferred_model: Optional[str],
) -> tuple[bool, dict[str, Any]]:
    rec.section("Demo 4: live API call -> real token usage")
    rec.line(f"Config: {config_path}")
    rec.line("API key is read from litellm_config.yaml and is never printed.")
    rec.pause("About to spend a very small API call.")

    try:
        model, api_base, api_key = _read_litellm_api_config(config_path, preferred_model)
        url = _chat_completions_url(api_base)
        request_data = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Reply with one short sentence for an engineering test.",
                },
                {
                    "role": "user",
                    "content": "Say READY and mention ArbiterOS cost telemetry.",
                },
            ],
            "max_completion_tokens": 32,
        }
        try:
            response = _post_json(url, request_data, api_key)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if "max_completion_tokens" not in body:
                raise RuntimeError(f"HTTP {exc.code}: {body[:500]}") from exc
            request_data = dict(request_data)
            request_data.pop("max_completion_tokens", None)
            request_data["max_tokens"] = 32
            response = _post_json(url, request_data, api_key)

        usage = cost_telemetry.extract_token_usage_from_response_obj(response)
        cost = cost_telemetry.estimate_llm_cost_usd(request_data, response, usage)
        entry = {
            "ts": datetime.now().isoformat(),
            "trace_id": "demo-live-api-trace",
            "request_model": cost.get("request_model"),
            "response_model": cost.get("response_model"),
            "pricing_model": cost.get("pricing_model"),
            "usage": usage,
            "estimated_cost": {
                "priced": cost.get("priced", False),
                "currency": cost.get("currency", "USD"),
                "input_cost_usd": cost.get("input_cost_usd", 0.0),
                "output_cost_usd": cost.get("output_cost_usd", 0.0),
                "total_cost_usd": cost.get("total_cost_usd", 0.0),
            },
        }
        summary = cost_doctor.summarize_trace([entry], trace_id="demo-live-api-trace")
        text = _extract_text(response)
        rec.line()
        rec.line(f"Base URL: {api_base}")
        rec.line(f"Model: {model}")
        rec.line(f"Assistant response: {text}")
        rec.line(f"Usage: {json.dumps(usage, ensure_ascii=False)}")
        rec.line(
            "Estimated cost: "
            f"${float(cost.get('total_cost_usd', 0.0) or 0.0):.8f} "
            f"(priced={bool(cost.get('priced', False))})"
        )
        rec.line()
        rec.line(cost_doctor.build_markdown_report(summary).rstrip())
        passed = bool(text) and int(usage.get("total_tokens", 0) or 0) > 0
        rec.line()
        rec.line("Expected: non-empty model response and total_tokens > 0.")
        rec.line(f"Result: {'PASS' if passed else 'FAIL'}")
        return passed, summary
    except Exception as exc:
        rec.line()
        rec.line(f"Live API error: {exc}")
        rec.line("Result: FAIL")
        return False, {"error": str(exc)}


def _write_outputs(
    *,
    output_dir: Path,
    transcript: str,
    mcp_rows: list[dict[str, Any]],
    cost_summary: dict[str, Any],
    unknown_summary: dict[str, Any],
    live_api_summary: dict[str, Any],
    overall_pass: bool,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = output_dir / f"{stamp}_cost_mcp_evidence.md"
    json_path = output_dir / f"{stamp}_cost_mcp_evidence.json"
    md_path.write_text(transcript, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "overall_pass": overall_pass,
                "mcp_rows": mcp_rows,
                "cost_summary": cost_summary,
                "unknown_summary": unknown_summary,
                "live_api_summary": live_api_summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return md_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate reproducible evidence for ArbiterOS cost/MCP handling."
    )
    parser.add_argument("--aris-repo", type=Path, default=_DEFAULT_ARIS_REPO)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--no-live-api", action="store_true")
    parser.add_argument("--litellm-config", type=Path, default=_DEFAULT_LITELLM_CONFIG)
    parser.add_argument("--model", default=None, help="Optional model_name in litellm_config.yaml")
    args = parser.parse_args()

    rec = Recorder(interactive=args.interactive)
    rec.section("ArbiterOS Cost + MCP Evidence Demo")
    rec.line(f"Kernel root: {_KERNEL_ROOT}")
    rec.line(f"Python: {sys.executable}")
    rec.line(f"Interactive: {args.interactive}")
    rec.line(f"Live API: {not args.no_live_api}")
    rec.line()
    rec.line("What this proves:")
    rec.line("1. ARIS MCP servers can be queried locally through tools/list.")
    rec.line("2. Their real MCP tool names are lowered into ArbiterOS flow metadata.")
    rec.line("3. Unsupported MCP tools still fail closed as unknown/review_required.")
    rec.line("4. Cost telemetry can be attributed and diagnosed into concrete rules.")
    rec.line("5. The default run spends a tiny live API call and captures real usage.")

    if not args.aris_repo.exists():
        rec.line()
        rec.line(f"ERROR: ARIS repo not found: {args.aris_repo}")
        md_path, json_path = _write_outputs(
            output_dir=args.output_dir,
            transcript=rec.markdown(),
            mcp_rows=[],
            cost_summary={},
            unknown_summary={},
            live_api_summary={},
            overall_pass=False,
        )
        rec.line(f"Transcript written to: {md_path}")
        rec.line(f"JSON summary written to: {json_path}")
        return 1

    mcp_pass, mcp_rows = run_mcp_parser_demo(rec, args.aris_repo)
    cost_pass, cost_summary = run_cost_doctor_demo(rec)
    live_pass = True
    live_summary: dict[str, Any] = {"skipped": True}
    unknown_pass, unknown_summary = run_unknown_tool_demo(
        rec,
        config_path=args.litellm_config,
        preferred_model=args.model,
        live_api=not args.no_live_api,
    )
    if not args.no_live_api:
        live_pass, live_summary = run_live_api_demo(
            rec,
            config_path=args.litellm_config,
            preferred_model=args.model,
        )

    overall_pass = mcp_pass and cost_pass and unknown_pass and live_pass
    rec.section("Overall")
    rec.line(f"MCP parser demo: {'PASS' if mcp_pass else 'FAIL'}")
    rec.line(f"Cost Doctor demo: {'PASS' if cost_pass else 'FAIL'}")
    rec.line(f"Unknown MCP fail-closed demo: {'PASS' if unknown_pass else 'FAIL'}")
    rec.line(
        "Live API usage demo: "
        f"{'PASS' if live_pass else 'FAIL'}"
        f"{' (skipped)' if args.no_live_api else ''}"
    )
    rec.line(f"OVERALL: {'PASS' if overall_pass else 'FAIL'}")

    transcript = rec.markdown()
    md_path, json_path = _write_outputs(
        output_dir=args.output_dir,
        transcript=transcript,
        mcp_rows=mcp_rows,
        cost_summary=cost_summary,
        unknown_summary=unknown_summary,
        live_api_summary=live_summary,
        overall_pass=overall_pass,
    )
    rec.line(f"Transcript written to: {md_path}")
    rec.line(f"JSON summary written to: {json_path}")
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
