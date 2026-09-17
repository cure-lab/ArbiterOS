from __future__ import annotations

from pathlib import Path

from arbiteros_kernel.role_habit_log import (
    accumulate_role_habit,
    extract_tool_categories,
    pattern_ngrams,
    snapshot_trace,
)


def _tool(step: int, category: str, call_id: str, *, kind: str = "TOOLCALL") -> dict:
    return {
        "id": f"instr-{call_id}-{kind}",
        "runtime_step": step,
        "instruction_type": category,
        "arbiteros_ref_kind": kind,
        "content": {"tool_name": "x", "tool_call_id": call_id, "arguments": {}},
    }


def test_extract_skips_non_tool_and_dedupes_call_id() -> None:
    sequence = extract_tool_categories(
        [
            {"instruction_type": "USERINPUT", "arbiteros_ref_kind": "USERINPUT"},
            _tool(1, "READ", "c1"),
            _tool(2, "READ", "c1", kind="TOOLRESULT"),
            _tool(3, "EXEC", "c2"),
            {"instruction_type": "RESPOND", "arbiteros_ref_kind": "LLMOUTPUT"},
            _tool(4, "WRITE", "c3"),
        ]
    )
    assert sequence == ["READ", "EXEC", "WRITE"]


def test_pattern_ngrams_length_2_and_3() -> None:
    grams = pattern_ngrams(["READ", "EXEC", "EXEC", "WRITE"])
    assert grams["READ→EXEC"] == 1
    assert grams["EXEC→EXEC"] == 1
    assert grams["EXEC→WRITE"] == 1
    assert grams["READ→EXEC→EXEC"] == 1
    assert grams["EXEC→EXEC→WRITE"] == 1


def test_accumulate_replaces_same_trace_and_adds_new_role_file(tmp_path: Path) -> None:
    first_instr = [_tool(1, "READ", "a"), _tool(2, "EXEC", "b")]
    second_instr = first_instr + [_tool(3, "WRITE", "c")]
    first = accumulate_role_habit(
        "bank_demo",
        trace_id="t1",
        instructions=first_instr,
        tokens=100,
        elapsed_seconds=10.0,
        usd=0.2,
        log_dir=tmp_path,
        enabled=True,
    )
    assert first is not None
    assert first["trace_count"] == 1
    assert first["counts"] == {"READ": 1, "WRITE": 0, "EXEC": 1}
    assert first["total_tool_calls"] == 2
    assert first["mix"]["READ"] == 0.5
    assert first["cost"]["tokens"] == 100

    replaced = accumulate_role_habit(
        "bank_demo",
        trace_id="t1",
        instructions=second_instr,
        tokens=150,
        elapsed_seconds=20.0,
        usd=0.3,
        log_dir=tmp_path,
        enabled=True,
    )
    assert replaced is not None
    assert replaced["trace_count"] == 1
    assert replaced["counts"] == {"READ": 1, "WRITE": 1, "EXEC": 1}
    assert replaced["total_tool_calls"] == 3
    assert replaced["cost"]["tokens"] == 150
    assert replaced["patterns"]["READ→EXEC"] == 1
    assert replaced["patterns"]["EXEC→WRITE"] == 1

    other = accumulate_role_habit(
        "bank_demo",
        trace_id="t2",
        instructions=[_tool(1, "EXEC", "z")],
        tokens=50,
        elapsed_seconds=10.0,
        usd=0.1,
        log_dir=tmp_path,
        enabled=True,
    )
    assert other is not None
    assert other["trace_count"] == 2
    assert other["counts"]["EXEC"] == 2
    assert other["total_tool_calls"] == 4
    assert other["frequency"]["avg_calls_per_trace"] == 2.0
    assert other["cost"]["tokens"] == 200
    assert (tmp_path / "bank_demo.json").is_file()


def test_default_role_filename_and_disabled(tmp_path: Path) -> None:
    snap = snapshot_trace([_tool(1, "READ", "a")])
    assert snap["tool_calls"] == 1
    none = accumulate_role_habit(
        None,
        trace_id="t1",
        instructions=[_tool(1, "READ", "a")],
        log_dir=tmp_path,
        enabled=False,
    )
    assert none is None
    record = accumulate_role_habit(
        "default",
        trace_id="t1",
        instructions=[_tool(1, "READ", "a")],
        log_dir=tmp_path,
        enabled=True,
    )
    assert record is not None
    assert record["role"] == "default"
    assert (tmp_path / "default.json").is_file()
