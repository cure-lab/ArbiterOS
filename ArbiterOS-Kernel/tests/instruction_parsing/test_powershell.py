"""Unit tests for arbiteros_kernel.instruction_parsing.shell_parsers.powershell.

Mirrors the structure and coverage of test_shell.py (bash) so the two parsers
are held to the same contract.  All assertions are based on the PowerShell
grammar and the windows_data YAML registries.
"""

import os

import pytest

from arbiteros_kernel.instruction_parsing.shell_parsers._base import (
    CommandAnalysis,
    ShellAnalyzer,
)
from arbiteros_kernel.instruction_parsing.shell_parsers.powershell import (
    analyze_command,
    _is_path_like,
    _classify_segment,
    _classify_segment_risk,
    _parse_cd_dir,
)


# ---------------------------------------------------------------------------
# Protocol / type contract
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_analyze_command_satisfies_shell_analyzer_protocol(self):
        assert isinstance(analyze_command, ShellAnalyzer)

    def test_returns_command_analysis_instance(self):
        r = analyze_command("Get-Content ./file.txt")
        assert isinstance(r, CommandAnalysis)


# ---------------------------------------------------------------------------
# Empty / whitespace
# ---------------------------------------------------------------------------


class TestEmptyCommand:
    def test_empty_string(self):
        r = analyze_command("")
        assert r.segments == []
        assert r.operators == []
        assert r.itypes == []
        assert r.itype == "EXEC"
        assert r.risks == []
        assert r.risk == "UNKNOWN"
        assert r.path_tokens == []
        assert r.write_targets == []

    def test_whitespace_only(self):
        r = analyze_command("   ")
        assert r.segments == []
        assert r.itype == "EXEC"
        assert r.risk == "UNKNOWN"

    def test_command_stored_verbatim(self):
        cmd = "Get-Content ./file.txt"
        assert analyze_command(cmd).command == cmd


# ---------------------------------------------------------------------------
# Instruction type — single segment
# ---------------------------------------------------------------------------


class TestInstructionType:
    def test_get_content_is_read(self):
        assert analyze_command("Get-Content ./file.txt").itype == "READ"

    def test_get_childitem_is_read(self):
        assert analyze_command("Get-ChildItem C:/temp").itype == "READ"

    def test_set_content_is_write(self):
        assert analyze_command("Set-Content ./out.txt 'data'").itype == "WRITE"

    def test_new_item_is_write(self):
        assert analyze_command("New-Item -ItemType File ./new.txt").itype == "WRITE"

    def test_copy_item_is_write(self):
        assert analyze_command("Copy-Item ./src.txt ./dst.txt").itype == "WRITE"

    def test_invoke_expression_is_exec(self):
        assert analyze_command("Invoke-Expression 'dir'").itype == "EXEC"

    def test_remove_item_is_exec(self):
        assert analyze_command("Remove-Item ./file.txt").itype == "EXEC"

    def test_start_process_is_exec(self):
        assert analyze_command("Start-Process notepad.exe").itype == "EXEC"

    # Case insensitivity
    def test_case_insensitive_lower(self):
        assert analyze_command("get-content ./file.txt").itype == "READ"

    def test_case_insensitive_upper(self):
        assert analyze_command("GET-CONTENT ./file.txt").itype == "READ"

    # Aliases
    def test_gc_alias_is_read(self):
        assert analyze_command("gc ./file.txt").itype == "READ"

    def test_ls_alias_is_read(self):
        assert analyze_command("ls C:/temp").itype == "READ"

    def test_iex_alias_is_exec(self):
        assert analyze_command("iex 'dir'").itype == "EXEC"

    # Folding: EXEC > WRITE > READ
    def test_exec_beats_read_in_pipeline(self):
        r = analyze_command("Get-Content file.txt | Invoke-Expression")
        assert r.itype == "EXEC"

    def test_exec_beats_write_in_chain(self):
        r = analyze_command("Copy-Item src dst && Invoke-Expression 'dir'")
        assert r.itype == "EXEC"

    def test_write_beats_read_in_chain(self):
        r = analyze_command("Get-Content a.txt && Set-Content b.txt 'x'")
        assert r.itype == "WRITE"

    def test_all_read_stays_read(self):
        r = analyze_command("Get-Content a.txt | Select-Object -First 5 | Write-Output")
        assert r.itype == "READ"

    def test_per_segment_itypes_populated(self):
        r = analyze_command("Get-Content file.txt | Invoke-Expression")
        assert len(r.itypes) == 2
        assert r.itypes[0] == "READ"
        assert r.itypes[1] == "EXEC"


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------


class TestRisk:
    def test_remove_item_is_high_risk(self):
        assert analyze_command("Remove-Item -Recurse C:/temp").risk == "HIGH"

    def test_invoke_expression_is_high_risk(self):
        assert analyze_command("Invoke-Expression 'dir'").risk == "HIGH"

    def test_iex_alias_is_high_risk(self):
        assert analyze_command("iex 'Get-Process'").risk == "HIGH"

    def test_get_content_is_low_risk(self):
        assert analyze_command("Get-Content ./file.txt").risk == "LOW"

    def test_get_childitem_is_low_risk(self):
        assert analyze_command("Get-ChildItem C:/temp").risk == "LOW"

    def test_ls_alias_is_low_risk(self):
        assert analyze_command("ls C:/temp").risk == "LOW"

    def test_high_wins_over_low_in_pipeline(self):
        r = analyze_command("Get-Content file.txt | Remove-Item")
        assert r.risk == "HIGH"

    def test_per_segment_risks_populated(self):
        r = analyze_command("Get-Content file.txt | Remove-Item ./old.txt")
        assert len(r.risks) == 2


# ---------------------------------------------------------------------------
# Pipeline splitting and operators
# ---------------------------------------------------------------------------


class TestSplitting:
    def test_pipe_produces_two_segments(self):
        r = analyze_command("Get-Content file.txt | Select-Object -First 5")
        assert len(r.segments) == 2
        assert r.operators == ["|"]

    def test_and_and_produces_two_segments(self):
        r = analyze_command("Get-Content a.txt && Set-Content b.txt 'x'")
        assert len(r.segments) == 2
        assert r.operators == ["&&"]

    def test_or_or_produces_two_segments(self):
        r = analyze_command("Get-Content a.txt || Remove-Item b.txt")
        assert len(r.segments) == 2
        assert r.operators == ["||"]

    def test_semicolon_produces_two_segments(self):
        r = analyze_command("Get-Content a.txt; Remove-Item b.txt")
        assert len(r.segments) == 2
        assert r.operators == [";"]

    def test_three_piped_segments(self):
        r = analyze_command("Get-Content f.txt | Select-Object | Write-Output")
        assert len(r.segments) == 3
        assert r.operators == ["|", "|"]

    def test_mixed_operators(self):
        r = analyze_command("Get-Content a.txt; Get-Content b.txt && Remove-Item c.txt")
        assert len(r.segments) == 3
        assert r.operators == [";", "&&"]

    def test_segments_operators_count_relationship(self):
        r = analyze_command("Get-Content a.txt | Get-Content b.txt | Get-Content c.txt")
        assert len(r.operators) == len(r.segments) - 1


# ---------------------------------------------------------------------------
# _is_path_like heuristic
# ---------------------------------------------------------------------------


class TestIsPathLike:
    @pytest.mark.parametrize("token", [
        "/etc/passwd",
        "~/Documents/file.txt",
        "./relative.txt",
        "../parent/file.txt",
        "C:/Users/john/file.txt",
        "C:\\Users\\john\\file.txt",
        "D:/data/file.txt",
        "//server/share/file.txt",
        "http://example.com/file",
        "https://example.com/file",
        "some/relative/path",
    ])
    def test_path_like_tokens(self, token):
        assert _is_path_like(token) is True

    @pytest.mark.parametrize("token", [
        "-Force",
        "-Recurse",
        "-Path",
        "notepad",
        "Write-Output",
        "True",
    ])
    def test_non_path_tokens(self, token):
        assert _is_path_like(token) is False


# ---------------------------------------------------------------------------
# Path token extraction
# ---------------------------------------------------------------------------


class TestPathTokens:
    def test_absolute_windows_path_collected(self):
        r = analyze_command("Get-Content C:/Users/john/file.txt")
        assert "C:/Users/john/file.txt" in r.path_tokens

    def test_tilde_path_collected(self):
        r = analyze_command("Get-Content ~/Documents/file.txt")
        assert any("Documents/file.txt" in t for t in r.path_tokens)

    def test_dotslash_path_collected(self):
        r = analyze_command("Get-Content ./notes.txt")
        assert any("notes.txt" in t for t in r.path_tokens)

    def test_relative_dotdot_path_collected(self):
        r = analyze_command("Get-Content ../parent/config.txt")
        assert any("config.txt" in t for t in r.path_tokens)

    def test_flag_not_collected_as_path(self):
        r = analyze_command("Get-ChildItem -Recurse C:/temp")
        assert "-Recurse" not in r.path_tokens

    def test_quoted_path_stripped_and_collected(self):
        r = analyze_command('Get-Content "C:/Users/john/file.txt"')
        assert "C:/Users/john/file.txt" in r.path_tokens

    def test_multiple_path_args_collected(self):
        r = analyze_command("Copy-Item ./src.txt ./dst.txt")
        assert any("src.txt" in t for t in r.path_tokens)
        assert any("dst.txt" in t for t in r.path_tokens)

    def test_redirect_target_collected(self):
        r = analyze_command("Get-Content ./file.txt > ./output.txt")
        assert any("output.txt" in t for t in r.path_tokens)

    def test_paths_across_semicolon_segments(self):
        r = analyze_command("Get-Content C:/a.txt; Get-Content C:/b.txt")
        assert "C:/a.txt" in r.path_tokens
        assert "C:/b.txt" in r.path_tokens

    def test_no_args_no_path_tokens(self):
        r = analyze_command("Get-Location")
        assert r.path_tokens == []


# ---------------------------------------------------------------------------
# Write target extraction
# ---------------------------------------------------------------------------


class TestWriteTargets:
    def test_redirect_gt_is_write_target(self):
        r = analyze_command("Get-Content ./file.txt > ./output.txt")
        assert any("output.txt" in t for t in r.write_targets)

    def test_redirect_append_is_write_target(self):
        r = analyze_command("Get-Content ./file.txt >> ./output.txt")
        assert any("output.txt" in t for t in r.write_targets)

    def test_write_command_arg_is_write_target(self):
        r = analyze_command("Set-Content ./out.txt 'data'")
        assert any("out.txt" in t for t in r.write_targets)

    def test_copy_item_dest_is_write_target(self):
        r = analyze_command("Copy-Item ./src.txt ./dst.txt")
        assert any("dst.txt" in t for t in r.write_targets)

    def test_read_command_args_not_write_targets(self):
        r = analyze_command("Get-Content C:/Users/john/file.txt")
        assert r.write_targets == []

    def test_redirect_stdin_not_write_target(self):
        # PowerShell doesn't use < for stdin the same way, but test the
        # principle: no false write targets for read operations
        r = analyze_command("Get-Content ./input.txt")
        assert r.write_targets == []


# ---------------------------------------------------------------------------
# Set-Location (cd) context tracking
# ---------------------------------------------------------------------------


class TestCdContext:
    def test_absolute_cd_resolves_subsequent_path(self):
        r = analyze_command("Set-Location C:/temp; Get-Content file.txt")
        assert any("C:/temp" in t and "file.txt" in t for t in r.path_tokens)

    def test_cd_alias_resolves_subsequent_path(self):
        r = analyze_command("cd C:/Users/john; Get-Content config.txt")
        assert any("C:/Users/john" in t and "config.txt" in t for t in r.path_tokens)

    def test_sl_alias_resolves_subsequent_path(self):
        r = analyze_command("sl C:/temp; Get-Content report.txt")
        assert any("C:/temp" in t and "report.txt" in t for t in r.path_tokens)

    def test_cd_and_and_propagates(self):
        r = analyze_command("Set-Location C:/logs && Get-Content app.log")
        assert any("C:/logs" in t and "app.log" in t for t in r.path_tokens)

    def test_cd_with_pipe_does_not_propagate(self):
        r = analyze_command("Set-Location C:/temp | Get-Content file.txt")
        assert not any("C:/temp" in t and "file.txt" in t for t in r.path_tokens)

    def test_cd_with_or_does_not_propagate(self):
        r = analyze_command("Set-Location C:/temp || Get-Content file.txt")
        assert not any("C:/temp" in t and "file.txt" in t for t in r.path_tokens)

    def test_relative_cd_not_tracked(self):
        r = analyze_command("cd subdir; Get-Content file.txt")
        assert isinstance(r, CommandAnalysis)  # must not crash

    def test_tilde_cd_resolves(self):
        r = analyze_command("cd ~; Get-Content config.txt")
        home = os.path.expanduser("~").replace("\\", "/")
        assert any(home in t.replace("\\", "/") for t in r.path_tokens)

    def test_cd_segment_itself_produces_no_path_tokens(self):
        r = analyze_command("Set-Location C:/temp; Get-ChildItem")
        # The cd target should NOT itself appear as a path token
        assert "C:/temp" not in r.path_tokens


# ---------------------------------------------------------------------------
# _classify_segment helper
# ---------------------------------------------------------------------------


class TestClassifySegment:
    def test_get_content_read(self):
        assert _classify_segment("Get-Content ./file.txt") == "READ"

    def test_set_content_write(self):
        assert _classify_segment("Set-Content ./out.txt 'x'") == "WRITE"

    def test_invoke_expression_exec(self):
        assert _classify_segment("Invoke-Expression 'dir'") == "EXEC"

    def test_case_insensitive(self):
        assert _classify_segment("GET-CONTENT ./file.txt") == "READ"

    def test_empty_segment_defaults_read(self):
        assert _classify_segment("") == "READ"

    def test_unknown_cmdlet_defaults_exec(self):
        assert _classify_segment("Unknown-Cmdlet-Xyz ./arg") == "EXEC"


# ---------------------------------------------------------------------------
# _classify_segment_risk helper
# ---------------------------------------------------------------------------


class TestClassifySegmentRisk:
    def test_remove_item_high(self):
        assert _classify_segment_risk("Remove-Item ./file.txt") == "HIGH"

    def test_get_content_low(self):
        assert _classify_segment_risk("Get-Content ./file.txt") == "LOW"

    def test_invoke_expression_high(self):
        assert _classify_segment_risk("Invoke-Expression 'ls'") == "HIGH"

    def test_empty_segment_unknown(self):
        assert _classify_segment_risk("") == "UNKNOWN"


# ---------------------------------------------------------------------------
# _parse_cd_dir helper
# ---------------------------------------------------------------------------


class TestParseCdDir:
    def test_set_location_returns_path(self):
        result = _parse_cd_dir("Set-Location C:/Users/john")
        assert result is not None
        assert "C:/Users/john" in result.replace("\\", "/")

    def test_cd_alias_returns_path(self):
        result = _parse_cd_dir("cd C:/temp")
        assert result is not None
        assert "C:/temp" in result.replace("\\", "/")

    def test_relative_path_returns_none(self):
        assert _parse_cd_dir("cd subdir") is None

    def test_non_cd_command_returns_none(self):
        assert _parse_cd_dir("Get-Content ./file.txt") is None

    def test_tilde_expanded(self):
        result = _parse_cd_dir("cd ~")
        assert result is not None
        home = os.path.expanduser("~").replace("\\", "/")
        assert result.replace("\\", "/") == home


# ---------------------------------------------------------------------------
# CommandAnalysis structure invariants
# ---------------------------------------------------------------------------


class TestCommandAnalysisStructure:
    def test_all_fields_present(self):
        r = analyze_command("Get-Content file.txt | Invoke-Expression")
        for field in ("command", "segments", "operators", "itypes", "itype",
                      "risks", "risk", "path_tokens", "write_targets"):
            assert hasattr(r, field)

    def test_segment_count_matches_itype_count(self):
        r = analyze_command("Get-Content a.txt | Select-Object | Write-Output")
        assert len(r.segments) == len(r.itypes)

    def test_segment_count_matches_risk_count(self):
        r = analyze_command("Get-Content a.txt | Select-Object | Write-Output")
        assert len(r.segments) == len(r.risks)

    def test_operators_count_is_segments_minus_one(self):
        r = analyze_command("Get-Content a.txt | Select-Object | Write-Output")
        assert len(r.operators) == len(r.segments) - 1

    def test_single_segment_no_operators(self):
        r = analyze_command("Get-Content ./file.txt")
        assert len(r.segments) == 1
        assert len(r.operators) == 0
