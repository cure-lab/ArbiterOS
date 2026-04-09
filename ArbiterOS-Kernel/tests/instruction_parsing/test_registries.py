"""Comprehensive unit tests for the three platform registries and shared base.

Coverage:
  • registries/_base.py   — _is_classifiable, _path_matches, RegistrySet behaviour
  • registries/linux.py   — Linux-specific exe / risk / confidentiality / trust
  • registries/darwin.py  — macOS-specific exe / risk / confidentiality / trust
  • registries/windows.py — Windows-specific exe / risk / confidentiality / trust

Isolation strategy
──────────────────
The global ``isolated_user_registry`` fixture in conftest.py already resets
the Linux RegistrySet singleton before every test.  This file adds two
module-level autouse fixtures that apply the same isolation to the Darwin
and Windows singletons so all three registries start from a clean state in
every test.
"""

import os

import pytest

import arbiteros_kernel.instruction_parsing.registries.darwin as _dr
import arbiteros_kernel.instruction_parsing.registries.windows as _wr
from arbiteros_kernel.instruction_parsing.registries._base import (
    _is_classifiable,
    _path_matches,
    RegistrySet,
)
from arbiteros_kernel.instruction_parsing.registries.linux import (
    classify_exe as linux_exe,
    classify_exe_risk as linux_risk,
    classify_confidentiality as linux_conf,
    classify_trustworthiness as linux_trust,
    register_file_taint as linux_taint,
    get_user_registered_paths as linux_registered,
)
from arbiteros_kernel.instruction_parsing.registries.darwin import (
    classify_exe as darwin_exe,
    classify_exe_risk as darwin_risk,
    classify_confidentiality as darwin_conf,
    classify_trustworthiness as darwin_trust,
)
from arbiteros_kernel.instruction_parsing.registries.windows import (
    classify_exe as windows_exe,
    classify_exe_risk as windows_risk,
    classify_confidentiality as windows_conf,
    classify_trustworthiness as windows_trust,
)


# ---------------------------------------------------------------------------
# Per-test isolation for Darwin and Windows singletons
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_darwin_registry(tmp_path, monkeypatch):
    user_dir = str(tmp_path / "darwin_registry")
    monkeypatch.setattr(_dr._DARWIN, "_user_dir", user_dir)
    monkeypatch.setattr(_dr._DARWIN, "_exe_user", None)
    monkeypatch.setattr(_dr._DARWIN, "_file_conf_user", None)
    monkeypatch.setattr(_dr._DARWIN, "_file_trust_user", None)
    monkeypatch.setattr(_dr._DARWIN, "_exe_risk_user", None)
    monkeypatch.setattr(_dr._DARWIN, "_exe_dirty", False)
    monkeypatch.setattr(_dr._DARWIN, "_file_conf_dirty", False)
    monkeypatch.setattr(_dr._DARWIN, "_file_trust_dirty", False)
    monkeypatch.setattr(_dr._DARWIN, "_exe_risk_dirty", False)
    yield


@pytest.fixture(autouse=True)
def _isolated_windows_registry(tmp_path, monkeypatch):
    user_dir = str(tmp_path / "windows_registry")
    monkeypatch.setattr(_wr._WINDOWS, "_user_dir", user_dir)
    monkeypatch.setattr(_wr._WINDOWS, "_exe_user", None)
    monkeypatch.setattr(_wr._WINDOWS, "_file_conf_user", None)
    monkeypatch.setattr(_wr._WINDOWS, "_file_trust_user", None)
    monkeypatch.setattr(_wr._WINDOWS, "_exe_risk_user", None)
    monkeypatch.setattr(_wr._WINDOWS, "_exe_dirty", False)
    monkeypatch.setattr(_wr._WINDOWS, "_file_conf_dirty", False)
    monkeypatch.setattr(_wr._WINDOWS, "_file_trust_dirty", False)
    monkeypatch.setattr(_wr._WINDOWS, "_exe_risk_dirty", False)
    yield


# ===========================================================================
# _base.py helpers
# ===========================================================================


class TestIsClassifiable:
    """_is_classifiable accepts paths that can be matched against registry rules."""

    @pytest.mark.parametrize("path", [
        "/etc/shadow",
        "/usr/bin/python3",
        "/home/user/file.txt",
        "/private/etc/ssh/sshd_config",   # macOS real path
        "/System/Library/Frameworks/Foundation.framework",
    ])
    def test_absolute_posix_paths_accepted(self, path):
        assert _is_classifiable(path) is True

    @pytest.mark.parametrize("path", [
        "~/Documents/file.txt",
        "~/.ssh/id_rsa",
        "~/Library/Keychains/login.keychain-db",
    ])
    def test_tilde_paths_accepted(self, path):
        assert _is_classifiable(path) is True

    @pytest.mark.parametrize("path", [
        "C:/Users/john/file.txt",
        "C:\\Users\\john\\file.txt",
        "D:/data/archive.zip",
    ])
    def test_windows_drive_letter_paths_accepted(self, path):
        assert _is_classifiable(path) is True

    @pytest.mark.parametrize("url", [
        "http://evil.com/payload",
        "https://example.com/data.json",
        "ftp://files.example.com/archive.tar.gz",
    ])
    def test_urls_accepted(self, url):
        assert _is_classifiable(url) is True

    @pytest.mark.parametrize("path", [
        "./relative.txt",
        "../parent/file.txt",
        "just_a_filename.txt",
        "some/relative/path",
        "",
    ])
    def test_relative_paths_rejected(self, path):
        assert _is_classifiable(path) is False


class TestPathMatches:
    """_path_matches covers fnmatch, PurePosixPath.match, basename fallback."""

    def test_exact_match(self):
        assert _path_matches("/etc/shadow", "/etc/shadow")

    def test_single_star_glob(self):
        assert _path_matches("/etc/sudoers.d/admin", "/etc/sudoers.d/*")

    def test_double_star_glob(self):
        assert _path_matches("/etc/pki/tls/certs/ca.crt", "/etc/pki/**")

    def test_extension_basename_pattern(self):
        assert _path_matches("/home/user/server.pem", "*.pem")

    def test_url_glob(self):
        assert _path_matches("https://evil.com/payload", "https://*")

    def test_no_match(self):
        assert not _path_matches("/var/log/syslog", "/etc/*")
        assert not _path_matches("README.md", "*.pem")

    def test_backslash_normalised_to_forward_slash(self):
        assert _path_matches("C:\\Users\\john\\file.txt", "C:/Users/*/*.txt")

    def test_tilde_pattern_expands_both_sides(self):
        # Both path and pattern go through expanduser, so they match correctly.
        expanded = os.path.expanduser("~/.ssh/id_rsa")
        assert _path_matches(expanded, "~/.ssh/*")
        assert _path_matches("~/.ssh/id_rsa", expanded)

    def test_windows_drive_path_matches(self):
        assert _path_matches("C:/Users/john/.ssh/id_rsa", "C:/Users/*/.ssh/*")

    def test_deep_nested_double_star(self):
        assert _path_matches(
            "C:/Users/john/AppData/Roaming/Microsoft/Protect/key",
            "C:/Users/*/AppData/**",
        )


class TestRegistrySetBehavior:
    """RegistrySet two-layer mechanics: source read-only, user read-write."""

    def test_register_file_taint_stores_path(self):
        linux_taint("/tmp/output.txt", "HIGH", "HIGH")
        assert "/tmp/output.txt" in linux_registered()

    def test_classify_confidentiality_uses_user_layer(self):
        # Register a normally-LOW /tmp path with HIGH confidentiality.
        linux_taint("/tmp/sensitive.txt", "HIGH", "HIGH")
        result = linux_conf(["/tmp/sensitive.txt"])
        assert result == "HIGH"

    def test_non_absolute_path_not_registered(self):
        linux_taint("relative/path.txt", "HIGH", "HIGH")
        assert "relative/path.txt" not in linux_registered()

    def test_empty_paths_returns_unknown(self):
        assert linux_conf([]) == "UNKNOWN"
        assert linux_trust([]) == "UNKNOWN"

    def test_unclassifiable_paths_return_unknown(self):
        # Relative paths are not classifiable.
        assert linux_conf(["./local.txt"]) == "UNKNOWN"
        assert linux_trust(["./local.txt"]) == "UNKNOWN"

    def test_source_layer_read_only_exe(self):
        # Source layer classifies cat as READ.
        assert linux_exe("cat", None) == "READ"

    def test_unknown_command_defaults_to_exec(self):
        assert linux_exe("no_such_command_xyz_abc", None) == "EXEC"

    def test_subcommand_takes_priority_over_bare_exe(self):
        # "git push" is EXEC, "git log" is READ — subcommand changes the result.
        assert linux_exe("git", "push") == "EXEC"
        assert linux_exe("git", "log") == "READ"


# ===========================================================================
# Linux registry
# ===========================================================================


class TestLinuxExeClassification:
    @pytest.mark.parametrize("exe,sub,expected", [
        ("bash", None, "EXEC"),
        ("zsh", None, "EXEC"),
        ("python3", None, "EXEC"),
        ("rm", None, "EXEC"),
        ("ssh", None, "EXEC"),
        ("sudo", None, "EXEC"),
        ("docker", None, "EXEC"),
        ("git", "push", "EXEC"),
        ("git", "fetch", "EXEC"),
        ("cat", None, "READ"),
        ("grep", None, "READ"),
        ("ls", None, "READ"),
        ("git", "log", "READ"),
        ("git", "status", "READ"),
        ("git", "diff", "READ"),
        ("cp", None, "WRITE"),
        ("mv", None, "WRITE"),
        ("tar", None, "WRITE"),
        ("git", "commit", "WRITE"),
        ("git", "add", "WRITE"),
        # Package managers: install ops are EXEC, info queries are READ
        ("pip", "install", "EXEC"),
        ("npm", "install", "EXEC"),
        ("brew", "install", "EXEC"),
        ("apt", "install", "EXEC"),
        ("pip", "list", "READ"),
        ("npm", "list", "READ"),
        ("brew", "list", "READ"),
        ("apt", "list", "READ"),
        ("apt-cache", None, "READ"),
    ])
    def test_classify_exe(self, exe, sub, expected):
        assert linux_exe(exe, sub) == expected


class TestLinuxRisk:
    @pytest.mark.parametrize("exe,sub,expected", [
        ("rm", None, "HIGH"),
        ("dd", None, "HIGH"),
        ("mkfs", None, "HIGH"),
        ("shutdown", None, "HIGH"),
        ("kill", None, "HIGH"),
        ("git", "clean", "HIGH"),
        ("git", "reset", "HIGH"),
        ("ls", None, "LOW"),
        ("echo", None, "LOW"),
        ("cat", None, "LOW"),
        ("grep", None, "LOW"),
        ("pwd", None, "LOW"),
        ("git", "log", "LOW"),
        ("git", "status", "LOW"),
        ("python3", None, "UNKNOWN"),
        ("docker", None, "UNKNOWN"),
    ])
    def test_classify_risk(self, exe, sub, expected):
        assert linux_risk(exe, sub) == expected


class TestLinuxConfidentiality:
    @pytest.mark.parametrize("paths,expected", [
        (["/etc/shadow"], "HIGH"),
        (["/etc/sudoers"], "HIGH"),
        (["~/.ssh/id_rsa"], "HIGH"),
        (["~/.aws/credentials"], "HIGH"),
        (["/home/user/key.pem"], "HIGH"),     # extension pattern
        (["/var/log/auth.log"], "HIGH"),
        (["/etc/nginx/nginx.conf"], "HIGH"),  # matches /etc/**
        (["/usr/bin/python3"], "LOW"),
        (["/tmp/tmpfile.txt"], "LOW"),
        (["/usr/lib/libc.so.6"], "LOW"),
        (["./relative.txt"], "UNKNOWN"),      # not classifiable
    ])
    def test_classify_confidentiality(self, paths, expected):
        assert linux_conf(paths) == expected

    def test_highest_wins_across_list(self):
        # One HIGH path in a list of LOW paths → HIGH
        result = linux_conf(["/usr/bin/python3", "/etc/shadow"])
        assert result == "HIGH"


class TestLinuxTrustworthiness:
    @pytest.mark.parametrize("paths,expected", [
        (["/home/user/Downloads/evil.bin"], "LOW"),
        (["~/Downloads/malware.sh"], "LOW"),
        (["http://evil.com/payload"], "LOW"),
        (["https://attacker.com/shell"], "LOW"),
        (["/tmp/tmpfile"], "LOW"),
        (["/var/tmp/upload"], "LOW"),
        (["/usr/bin/python3"], "HIGH"),
        (["/etc/nginx/nginx.conf"], "HIGH"),
        (["/usr/lib/libc.so.6"], "HIGH"),
        (["/opt/myapp/bin/server"], "HIGH"),
    ])
    def test_classify_trustworthiness(self, paths, expected):
        assert linux_trust(paths) == expected

    def test_lowest_wins_across_list(self):
        # One LOW path among HIGH paths → LOW (worst-case wins)
        result = linux_trust(["/usr/bin/python3", "/tmp/untrusted.sh"])
        assert result == "LOW"


# ===========================================================================
# Darwin registry
# ===========================================================================


class TestDarwinExeClassification:
    @pytest.mark.parametrize("exe,sub,expected", [
        # macOS-specific EXEC
        ("osascript", None, "EXEC"),
        ("open", None, "EXEC"),
        ("diskutil", None, "EXEC"),
        ("security", None, "EXEC"),
        ("hdiutil", None, "EXEC"),
        ("softwareupdate", None, "EXEC"),
        ("launchctl", None, "EXEC"),
        # macOS-specific READ
        ("mdfind", None, "READ"),
        ("mdls", None, "READ"),
        ("sw_vers", None, "READ"),
        ("system_profiler", None, "READ"),
        ("vm_stat", None, "READ"),
        # Subcommands that flip EXEC → READ: NOTE — classify_exe checks
        # categories in EXEC > WRITE > READ priority order.  The bare
        # "diskutil" and "launchctl" live in EXEC, so any subcommand of
        # those commands also resolves to EXEC regardless of READ entries.
        ("diskutil", "list", "EXEC"),
        ("diskutil", "info", "EXEC"),
        ("launchctl", "list", "EXEC"),
        ("launchctl", "print", "EXEC"),
        # Package managers: install ops EXEC, info queries READ, bare defaults EXEC
        ("pip", "install", "EXEC"),
        ("npm", "install", "EXEC"),
        ("brew", "install", "EXEC"),
        ("pip", "list", "READ"),
        ("npm", "list", "READ"),
        ("brew", "list", "READ"),
        # WRITE
        ("defaults", None, "WRITE"),
        ("ditto", None, "WRITE"),
        # POSIX universals still present
        ("cat", None, "READ"),
        ("cp", None, "WRITE"),
        ("python3", None, "EXEC"),
        ("git", "push", "EXEC"),
        ("git", "log", "READ"),
    ])
    def test_classify_exe(self, exe, sub, expected):
        assert darwin_exe(exe, sub) == expected


class TestDarwinRisk:
    @pytest.mark.parametrize("exe,sub,expected", [
        # macOS-specific HIGH risk
        ("diskutil", None, "HIGH"),
        ("osascript", None, "HIGH"),
        ("security", None, "HIGH"),
        ("hdiutil", None, "HIGH"),
        # POSIX HIGH risk still present
        ("rm", None, "HIGH"),
        ("kill", None, "HIGH"),
        ("shutdown", None, "HIGH"),
        # macOS-specific LOW risk
        ("mdfind", None, "LOW"),
        ("mdls", None, "LOW"),
        ("sw_vers", None, "LOW"),
        ("brew", "list", "LOW"),
        ("brew", "info", "LOW"),
        # POSIX LOW risk still present
        ("ls", None, "LOW"),
        ("cat", None, "LOW"),
        ("echo", None, "LOW"),
        ("git", "log", "LOW"),
        # launchctl read-only subcommands
        ("launchctl", "list", "LOW"),
        ("launchctl", "print", "LOW"),
    ])
    def test_classify_risk(self, exe, sub, expected):
        assert darwin_risk(exe, sub) == expected


class TestDarwinConfidentiality:
    @pytest.mark.parametrize("paths,expected", [
        # macOS-specific HIGH: Keychain
        (["~/Library/Keychains/login.keychain-db"], "HIGH"),
        (["~/Library/Keychains/System.keychain"], "HIGH"),
        # macOS-specific HIGH: user Library
        (["~/Library/Application Support/1Password/data.db"], "HIGH"),
        (["~/Library/Preferences/com.apple.security.plist"], "HIGH"),
        # POSIX HIGH: ssh keys, AWS creds
        (["~/.ssh/id_rsa"], "HIGH"),
        (["~/.aws/credentials"], "HIGH"),
        # macOS /private/etc mirrors /etc
        (["/private/etc/ssh/sshd_config"], "HIGH"),
        (["/private/var/log/system.log"], "HIGH"),
        # Generic HIGH: extension patterns
        (["/home/user/cert.pem"], "HIGH"),
        # Darwin LOW: system dirs (public content)
        (["/System/Library/Frameworks/Foundation.framework/Foundation"], "LOW"),
        (["/Applications/Safari.app/Contents/MacOS/Safari"], "LOW"),
        (["/opt/homebrew/bin/brew"], "LOW"),
        (["/private/tmp/tmpfile.txt"], "LOW"),
        (["/usr/bin/python3"], "LOW"),
        # Not classifiable
        (["./relative.txt"], "UNKNOWN"),
    ])
    def test_classify_confidentiality(self, paths, expected):
        assert darwin_conf(paths) == expected


class TestDarwinTrustworthiness:
    @pytest.mark.parametrize("paths,expected", [
        # LOW: untrusted sources
        (["~/Downloads/app.dmg"], "LOW"),
        (["/private/tmp/tmpfile"], "LOW"),
        (["https://evil.com/payload"], "LOW"),
        (["/tmp/uploaded.sh"], "LOW"),
        # HIGH: Apple-managed system
        (["/System/Library/Frameworks/Foundation.framework/Foundation"], "HIGH"),
        (["/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder"], "HIGH"),
        # HIGH: Homebrew
        (["/opt/homebrew/bin/python3"], "HIGH"),
        (["/usr/local/bin/brew"], "HIGH"),
        # HIGH: installed apps
        (["/Applications/Xcode.app/Contents/MacOS/Xcode"], "HIGH"),
        (["/Library/Frameworks/Python.framework/Versions/3.11/Python"], "HIGH"),
        # HIGH: system config
        (["/etc/hosts"], "HIGH"),
        (["/usr/bin/python3"], "HIGH"),
    ])
    def test_classify_trustworthiness(self, paths, expected):
        assert darwin_trust(paths) == expected


# ===========================================================================
# Windows registry
# ===========================================================================


class TestWindowsExeClassification:
    """All lookups use lowercase cmdlet names (as powershell.py normalises them)."""

    @pytest.mark.parametrize("exe,sub,expected", [
        # EXEC
        ("invoke-expression", None, "EXEC"),
        ("iex", None, "EXEC"),
        ("remove-item", None, "EXEC"),
        ("rm", None, "EXEC"),
        ("del", None, "EXEC"),
        ("start-process", None, "EXEC"),
        ("stop-process", None, "EXEC"),
        ("python", None, "EXEC"),
        ("git", "push", "EXEC"),
        # READ
        ("get-content", None, "READ"),
        ("gc", None, "READ"),
        ("cat", None, "READ"),
        ("get-childitem", None, "READ"),
        ("ls", None, "READ"),
        ("dir", None, "READ"),
        ("write-output", None, "READ"),
        ("echo", None, "READ"),
        ("get-location", None, "READ"),
        ("pwd", None, "READ"),
        ("git", "log", "READ"),
        ("git", "status", "READ"),
        # WRITE
        ("set-content", None, "WRITE"),
        ("new-item", None, "WRITE"),
        ("copy-item", None, "WRITE"),
        ("move-item", None, "WRITE"),
        ("rename-item", None, "WRITE"),
        ("compress-archive", None, "WRITE"),
        ("git", "commit", "WRITE"),
        ("set-acl", None, "WRITE"),
        ("expand-archive", None, "WRITE"),
        ("add-content", None, "WRITE"),
    ])
    def test_classify_exe(self, exe, sub, expected):
        assert windows_exe(exe, sub) == expected


class TestWindowsRisk:
    @pytest.mark.parametrize("exe,sub,expected", [
        # HIGH
        ("remove-item", None, "HIGH"),
        ("rm", None, "HIGH"),
        ("del", None, "HIGH"),
        ("invoke-expression", None, "HIGH"),
        ("iex", None, "HIGH"),
        ("stop-computer", None, "HIGH"),
        ("restart-computer", None, "HIGH"),
        ("stop-process", None, "HIGH"),
        ("kill", None, "HIGH"),
        ("git", "clean", "HIGH"),
        ("git", "reset", "HIGH"),
        # LOW
        ("get-content", None, "LOW"),
        ("gc", None, "LOW"),
        ("get-childitem", None, "LOW"),
        ("ls", None, "LOW"),
        ("dir", None, "LOW"),
        ("write-output", None, "LOW"),
        ("echo", None, "LOW"),
        ("get-location", None, "LOW"),
        ("pwd", None, "LOW"),
        ("test-path", None, "LOW"),
        ("git", "log", "LOW"),
        ("git", "status", "LOW"),
    ])
    def test_classify_risk(self, exe, sub, expected):
        assert windows_risk(exe, sub) == expected


class TestWindowsConfidentiality:
    @pytest.mark.parametrize("paths,expected", [
        # HIGH: Windows credential paths
        (["C:/Users/john/.ssh/id_rsa"], "HIGH"),
        (["C:/Users/john/.aws/credentials"], "HIGH"),
        (["C:/Windows/System32/config/SAM"], "HIGH"),
        (["C:/Windows/System32/config/SYSTEM"], "HIGH"),
        (["C:/Users/john/AppData/Roaming/Microsoft/Protect/key"], "HIGH"),
        (["C:/Users/john/AppData/Roaming/Microsoft/Credentials/abc"], "HIGH"),
        # HIGH: extension patterns (cross-platform)
        (["/home/user/cert.pem"], "HIGH"),
        (["/tmp/server.key"], "HIGH"),
        # LOW: temp and system dirs
        (["C:/Temp/tmpfile.txt"], "LOW"),
        (["C:/Windows/Temp/session.tmp"], "LOW"),
        (["C:/Windows/System32/cmd.exe"], "LOW"),
        (["C:/Program Files/Python/python.exe"], "LOW"),
        (["C:/Users/Public/Documents/readme.txt"], "LOW"),
        # UNKNOWN: Linux path → no Windows rule matches
        (["./relative.txt"], "UNKNOWN"),
    ])
    def test_classify_confidentiality(self, paths, expected):
        assert windows_conf(paths) == expected


class TestWindowsTrustworthiness:
    @pytest.mark.parametrize("paths,expected", [
        # LOW: untrusted sources
        (["C:/Users/john/Downloads/malware.exe"], "LOW"),
        (["C:/Users/john/Downloads/setup.zip"], "LOW"),
        (["C:/Temp/tmp.txt"], "LOW"),
        (["http://evil.com/payload"], "LOW"),
        (["https://attacker.com/shell.ps1"], "LOW"),
        (["D:/external/data.csv"], "LOW"),
        (["E:/removable/file.bin"], "LOW"),
        # HIGH: OS and installed software
        (["C:/Windows/System32/cmd.exe"], "HIGH"),
        (["C:/Windows/SysWOW64/notepad.exe"], "HIGH"),
        (["C:/Windows/explorer.exe"], "HIGH"),
        (["C:/Program Files/Python/python.exe"], "HIGH"),
        (["C:/Program Files (x86)/Git/bin/git.exe"], "HIGH"),
    ])
    def test_classify_trustworthiness(self, paths, expected):
        assert windows_trust(paths) == expected

    def test_low_wins_across_list(self):
        result = windows_trust([
            "C:/Windows/System32/cmd.exe",
            "C:/Users/john/Downloads/malware.exe",
        ])
        assert result == "LOW"

    def test_all_high_stays_high(self):
        result = windows_trust([
            "C:/Windows/System32/cmd.exe",
            "C:/Program Files/Python/python.exe",
        ])
        assert result == "HIGH"
