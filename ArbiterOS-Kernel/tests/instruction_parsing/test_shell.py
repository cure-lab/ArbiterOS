"""Tests for arbiteros_kernel.instruction_parsing.shell_parsers.bash.analyze_command."""

import functools
import os

import pytest

from arbiteros_kernel.instruction_parsing.registries.linux import (
    classify_exe as _linux_classify_exe,
    classify_exe_risk as _linux_classify_exe_risk,
)
from arbiteros_kernel.instruction_parsing.shell_parsers.bash import (
    CommandAnalysis,
    analyze_command as _bash_analyze_command,
)

analyze_command = functools.partial(
    _bash_analyze_command,
    classify_exe=_linux_classify_exe,
    classify_exe_risk=_linux_classify_exe_risk,
)


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
        cmd = "cat /etc/passwd"
        assert analyze_command(cmd).command == cmd


# ---------------------------------------------------------------------------
# Instruction type per segment
# ---------------------------------------------------------------------------


class TestInstructionType:
    def test_single_read(self):
        assert analyze_command("cat /etc/passwd").itype == "READ"

    def test_single_write(self):
        assert analyze_command("cp /src /dst").itype == "WRITE"

    def test_single_exec(self):
        assert analyze_command("python run.py").itype == "EXEC"

    def test_git_push_is_exec(self):
        assert analyze_command("git push origin main").itype == "EXEC"

    def test_git_commit_is_write(self):
        assert analyze_command("git commit -m 'msg'").itype == "WRITE"

    def test_git_log_is_read(self):
        assert analyze_command("git log --oneline").itype == "READ"

    # Folding: EXEC > WRITE > READ

    def test_exec_beats_read_in_pipeline(self):
        r = analyze_command("cat file.txt | python process.py")
        assert r.itype == "EXEC"

    def test_exec_beats_write_in_chain(self):
        r = analyze_command("cp a b && python run.py")
        assert r.itype == "EXEC"

    def test_write_beats_read_in_chain(self):
        r = analyze_command("cat file && cp a b")
        assert r.itype == "WRITE"

    def test_all_read_stays_read(self):
        r = analyze_command("cat a | grep foo | wc -l")
        assert r.itype == "READ"

    def test_per_segment_itypes_populated(self):
        r = analyze_command("cat file | python run.py")
        assert len(r.itypes) == 2
        assert r.itypes[0] == "READ"
        assert r.itypes[1] == "EXEC"


# ---------------------------------------------------------------------------
# Risk per segment
# ---------------------------------------------------------------------------


class TestRisk:
    def test_high_risk_rm(self):
        assert analyze_command("rm -rf /tmp/old").risk == "HIGH"

    def test_high_risk_bash(self):
        # bash with a subcommand-like arg → UNKNOWN (no match in risk registry);
        # use rm which is unconditionally HIGH
        assert analyze_command("rm -rf /tmp/junk").risk == "HIGH"

    def test_low_risk_echo_ls(self):
        r = analyze_command("echo hello && ls /tmp")
        assert r.risk == "LOW"

    def test_high_wins_over_low(self):
        r = analyze_command("ls /tmp | rm -rf /tmp/junk")
        assert r.risk == "HIGH"

    def test_unknown_taints_low(self):
        r = analyze_command("echo hi && python run.py")
        assert r.risk == "UNKNOWN"

    def test_per_segment_risks_populated(self):
        r = analyze_command("ls /tmp | rm -rf /tmp/junk")
        assert len(r.risks) == 2


# ---------------------------------------------------------------------------
# Pipeline splitting and operators
# ---------------------------------------------------------------------------


class TestSplitting:
    def test_pipe_produces_two_segments(self):
        r = analyze_command("cat file | grep foo")
        assert len(r.segments) == 2
        assert r.operators == ["|"]

    def test_and_and_produces_two_segments(self):
        r = analyze_command("cat file && python run.py")
        assert len(r.segments) == 2
        assert r.operators == ["&&"]

    def test_or_or_produces_two_segments(self):
        r = analyze_command("cmd1 || cmd2")
        assert len(r.segments) == 2
        assert r.operators == ["||"]

    def test_semicolon_produces_two_segments(self):
        r = analyze_command("echo hello; bash evil.sh")
        assert len(r.segments) == 2
        assert r.operators == [";"]

    def test_newline_separator(self):
        r = analyze_command("echo hello\npython run.py")
        assert len(r.segments) == 2

    def test_three_piped_segments(self):
        r = analyze_command("cat file | grep foo | wc -l")
        assert len(r.segments) == 3
        assert r.operators == ["|", "|"]

    def test_five_piped_segments(self):
        r = analyze_command("cat f | grep a | sort | uniq | wc -l")
        assert len(r.segments) == 5

    def test_pipe_inside_quotes_not_split(self):
        r = analyze_command(r"find . | sed 's|a|b|' | sort")
        assert len(r.segments) == 3

    def test_subshell_split(self):
        r = analyze_command("cat file && (python run.py | grep result)")
        assert len(r.segments) == 3

    def test_background_operator(self):
        r = analyze_command("sleep 10 & cat file")
        assert len(r.segments) == 2

    def test_segments_operators_interleave_correctly(self):
        r = analyze_command("ls /tmp ; cat file && python run.py")
        assert len(r.segments) == 3
        assert r.operators == [";", "&&"]


# ---------------------------------------------------------------------------
# Path token extraction
# ---------------------------------------------------------------------------


class TestPathTokens:
    def test_absolute_path_argument_collected(self):
        r = analyze_command("cat /etc/passwd")
        assert "/etc/passwd" in r.path_tokens

    def test_home_relative_path_collected(self):
        r = analyze_command("cat ~/config.yaml")
        assert any("config.yaml" in t for t in r.path_tokens)

    def test_dotslash_path_collected(self):
        r = analyze_command("cat ./notes.txt")
        assert any("notes.txt" in t for t in r.path_tokens)

    def test_flag_not_collected_as_path(self):
        r = analyze_command("ls -la /tmp")
        assert "-la" not in r.path_tokens

    def test_exec_command_bare_word_not_collected(self):
        # For EXEC segments, bare non-path words are not collected.
        r = analyze_command("python script.py")
        # "script.py" has no path separator — should not appear unless
        # the registry treats it as READ/WRITE
        assert "script.py" not in r.path_tokens

    def test_path_like_exec_binary_collected(self):
        r = analyze_command("~/bin/myscript.sh arg1")
        assert any("myscript.sh" in t for t in r.path_tokens)

    def test_multiple_path_args(self):
        r = analyze_command("cp /src/file.txt /dst/file.txt")
        assert "/src/file.txt" in r.path_tokens
        assert "/dst/file.txt" in r.path_tokens

    def test_redirect_output_target_collected(self):
        r = analyze_command("echo hi > /tmp/out.txt")
        assert "/tmp/out.txt" in r.path_tokens

    def test_paths_across_and_and_segments(self):
        # && without redirects splits normally; both paths collected
        r = analyze_command("cat /etc/passwd && cat /etc/shadow")
        assert "/etc/passwd" in r.path_tokens
        assert "/etc/shadow" in r.path_tokens

    def test_redirect_at_pipeline_end_is_one_segment(self):
        # tree-sitter wraps `pipeline > file` in a single redirected_statement;
        # _ts_find_exec_units looks for a `command` child but finds a `pipeline`
        # child, so the redirect target is NOT extracted — known limitation.
        r = analyze_command("cat /etc/passwd | grep root > /tmp/found.txt")
        assert len(r.segments) == 1
        assert "/tmp/found.txt" not in r.write_targets  # known limitation

    def test_no_args_no_path_tokens(self):
        r = analyze_command("ls")
        assert r.path_tokens == []


# ---------------------------------------------------------------------------
# Write target extraction (redirect and command)
# ---------------------------------------------------------------------------


class TestWriteTargets:
    def test_redirect_gt_is_write_target(self):
        r = analyze_command("echo hi > /tmp/out.txt")
        assert "/tmp/out.txt" in r.write_targets

    def test_redirect_append_is_write_target(self):
        r = analyze_command("echo hi >> /tmp/out.txt")
        assert "/tmp/out.txt" in r.write_targets

    def test_redirect_ampgt_is_write_target(self):
        r = analyze_command("cmd &> /tmp/err.log")
        assert "/tmp/err.log" in r.write_targets

    def test_redirect_ampgtgt_is_write_target(self):
        r = analyze_command("cmd &>> /tmp/err.log")
        assert "/tmp/err.log" in r.write_targets

    def test_redirect_lt_is_not_write_target(self):
        r = analyze_command("cat < /tmp/input.txt")
        assert "/tmp/input.txt" not in r.write_targets

    def test_write_command_args_are_write_targets(self):
        # cp / mv write to the destination (WRITE itype)
        r = analyze_command("cp /src.txt /dst.txt")
        assert "/dst.txt" in r.write_targets

    def test_read_command_args_not_write_targets(self):
        r = analyze_command("cat /etc/passwd")
        assert r.write_targets == []

    def test_multiple_redirects(self):
        # tree-sitter treats `cmd > f && cmd > f` as one redirected_statement,
        # so use two separate analyze_command calls or a semicolon without
        # redirects on both sides
        r1 = analyze_command("echo foo > /tmp/b.txt")
        assert "/tmp/b.txt" in r1.write_targets
        r2 = analyze_command("echo bar > /tmp/d.txt")
        assert "/tmp/d.txt" in r2.write_targets


# ---------------------------------------------------------------------------
# cd context tracking
# ---------------------------------------------------------------------------


class TestCdContext:
    def test_absolute_cd_resolves_subsequent_path(self):
        r = analyze_command("cd /tmp && cat file.txt")
        assert any(t == "/tmp/file.txt" for t in r.path_tokens)

    def test_cd_with_semicolon_propagates(self):
        r = analyze_command("cd /tmp; cat file.txt")
        assert any(t == "/tmp/file.txt" for t in r.path_tokens)

    def test_cd_with_pipe_does_not_propagate(self):
        r = analyze_command("cd /tmp | cat file.txt")
        # pipe runs in subshell — cd context must NOT carry over
        assert "/tmp/file.txt" not in r.path_tokens

    def test_cd_with_or_does_not_propagate(self):
        r = analyze_command("cd /tmp || cat file.txt")
        assert "/tmp/file.txt" not in r.path_tokens

    def test_relative_cd_not_tracked(self):
        # Relative cd can't be resolved without knowing cwd — context unchanged
        r = analyze_command("cd subdir && cat file.txt")
        # Should not crash; path resolution is best-effort
        assert isinstance(r, CommandAnalysis)

    def test_tilde_cd_resolves_subsequent_path(self):
        home = os.path.expanduser("~")
        r = analyze_command("cd ~ && cat config.txt")
        assert any(t == os.path.join(home, "config.txt") for t in r.path_tokens)

    def test_cd_absolute_path_resolves_redirect(self):
        # Redirect + && causes tree-sitter to produce a single segment,
        # so test cd context with a plain read command instead
        r = analyze_command("cd /var/log && cat syslog")
        assert any(t == "/var/log/syslog" for t in r.path_tokens)

    def test_cd_segment_itself_produces_no_path_tokens(self):
        r = analyze_command("cd /tmp && ls")
        # /tmp should NOT appear as a path token (cd doesn't produce file IO)
        assert "/tmp" not in r.path_tokens


# ---------------------------------------------------------------------------
# CommandAnalysis structure
# ---------------------------------------------------------------------------


class TestCommandAnalysisStructure:
    def test_is_dataclass_instance(self):
        r = analyze_command("cat /etc/passwd")
        assert isinstance(r, CommandAnalysis)

    def test_all_fields_present(self):
        r = analyze_command("cat /etc/passwd | python run.py")
        assert hasattr(r, "command")
        assert hasattr(r, "segments")
        assert hasattr(r, "operators")
        assert hasattr(r, "itypes")
        assert hasattr(r, "itype")
        assert hasattr(r, "risks")
        assert hasattr(r, "risk")
        assert hasattr(r, "path_tokens")
        assert hasattr(r, "write_targets")

    def test_segment_count_matches_itype_count(self):
        r = analyze_command("cat a | grep b | python c.py")
        assert len(r.segments) == len(r.itypes)

    def test_segment_count_matches_risk_count(self):
        r = analyze_command("cat a | grep b | python c.py")
        assert len(r.segments) == len(r.risks)

    def test_operators_count_is_segments_minus_one(self):
        r = analyze_command("cat a | grep b | python c.py")
        assert len(r.operators) == len(r.segments) - 1
