"""Tests for bash parser + darwin registry (macOS-specific command classification).

Uses the bash shell parser wired to the darwin_data YAML registries, covering
macOS-specific commands that do not exist in the linux registry:
osascript, open, launchctl, diskutil, hdiutil, security, ditto, defaults,
PlistBuddy, xattr, sw_vers, system_profiler, mdfind, mdls, brew, port, etc.

General bash parsing mechanics (splitting, path extraction, cd context) are
already covered by test_shell.py and are not duplicated here.
"""

import functools

import pytest

from arbiteros_kernel.instruction_parsing.registries.darwin import (
    classify_exe as _darwin_classify_exe,
    classify_exe_risk as _darwin_classify_exe_risk,
)
from arbiteros_kernel.instruction_parsing.shell_parsers.bash import (
    CommandAnalysis,
    analyze_command as _bash_analyze_command,
)

analyze_command = functools.partial(
    _bash_analyze_command,
    classify_exe=_darwin_classify_exe,
    classify_exe_risk=_darwin_classify_exe_risk,
)


# ---------------------------------------------------------------------------
# Instruction type — macOS-specific EXEC commands
# ---------------------------------------------------------------------------


class TestMacOSExecCommands:
    def test_osascript_is_exec(self):
        assert analyze_command("osascript -e 'tell app \"Finder\" to quit'").itype == "EXEC"

    def test_open_is_exec(self):
        assert analyze_command("open ~/Downloads/app.dmg").itype == "EXEC"

    def test_launchctl_is_exec(self):
        assert analyze_command("launchctl load ~/Library/LaunchAgents/com.example.plist").itype == "EXEC"

    def test_diskutil_is_exec(self):
        assert analyze_command("diskutil eraseVolume APFS MyVol /dev/disk2").itype == "EXEC"

    def test_hdiutil_is_exec(self):
        assert analyze_command("hdiutil attach ~/image.dmg").itype == "EXEC"

    def test_security_is_exec(self):
        assert analyze_command("security find-generic-password -s MyService").itype == "EXEC"

    def test_softwareupdate_is_exec(self):
        assert analyze_command("softwareupdate --install --all").itype == "EXEC"

    def test_networksetup_is_exec(self):
        assert analyze_command("networksetup -setdnsservers Wi-Fi 8.8.8.8").itype == "EXEC"

    def test_tmutil_is_exec(self):
        assert analyze_command("tmutil startbackup").itype == "EXEC"

    def test_dd_is_exec(self):
        assert analyze_command("dd if=/dev/zero of=/dev/disk2 bs=1m").itype == "EXEC"


# ---------------------------------------------------------------------------
# Instruction type — macOS-specific WRITE commands
# ---------------------------------------------------------------------------


class TestMacOSWriteCommands:
    def test_ditto_is_write(self):
        assert analyze_command("ditto /src /dst").itype == "WRITE"

    def test_defaults_write_is_write(self):
        assert analyze_command("defaults write com.apple.dock autohide -bool true").itype == "WRITE"

    def test_plistbuddy_is_write(self):
        assert analyze_command("PlistBuddy -c 'Set :Key Value' ~/Library/Preferences/com.example.plist").itype == "WRITE"

    def test_xattr_is_write(self):
        assert analyze_command("xattr -d com.apple.quarantine ~/Downloads/app.dmg").itype == "WRITE"


# ---------------------------------------------------------------------------
# Instruction type — macOS-specific READ commands
# ---------------------------------------------------------------------------


class TestMacOSReadCommands:
    def test_mdfind_is_read(self):
        assert analyze_command("mdfind -name 'report.pdf'").itype == "READ"

    def test_mdls_is_read(self):
        assert analyze_command("mdls ~/Documents/file.pdf").itype == "READ"

    def test_sw_vers_is_read(self):
        assert analyze_command("sw_vers").itype == "READ"

    def test_system_profiler_is_read(self):
        assert analyze_command("system_profiler SPHardwareDataType").itype == "READ"

    def test_vm_stat_is_read(self):
        assert analyze_command("vm_stat").itype == "READ"

    def test_scutil_is_read(self):
        assert analyze_command("scutil --get HostName").itype == "READ"

    def test_ioreg_is_read(self):
        assert analyze_command("ioreg -l").itype == "READ"


# ---------------------------------------------------------------------------
# Instruction type — Homebrew
# ---------------------------------------------------------------------------


class TestBrew:
    def test_brew_install_is_exec(self):
        assert analyze_command("brew install ripgrep").itype == "EXEC"

    def test_brew_uninstall_is_exec(self):
        assert analyze_command("brew uninstall ripgrep").itype == "EXEC"

    def test_brew_upgrade_is_exec(self):
        assert analyze_command("brew upgrade").itype == "EXEC"

    def test_brew_update_is_exec(self):
        assert analyze_command("brew update").itype == "EXEC"

    def test_brew_tap_is_exec(self):
        assert analyze_command("brew tap homebrew/cask").itype == "EXEC"

    def test_brew_bundle_is_exec(self):
        assert analyze_command("brew bundle install").itype == "EXEC"

    def test_brew_list_is_read(self):
        assert analyze_command("brew list").itype == "READ"

    def test_brew_info_is_read(self):
        assert analyze_command("brew info ripgrep").itype == "READ"

    def test_brew_outdated_is_read(self):
        assert analyze_command("brew outdated").itype == "READ"

    def test_brew_search_is_read(self):
        assert analyze_command("brew search wget").itype == "READ"

    def test_brew_deps_is_read(self):
        assert analyze_command("brew deps ripgrep").itype == "READ"


# ---------------------------------------------------------------------------
# Instruction type — MacPorts
# ---------------------------------------------------------------------------


class TestMacPorts:
    def test_port_install_is_exec(self):
        assert analyze_command("port install wget").itype == "EXEC"

    def test_port_uninstall_is_exec(self):
        assert analyze_command("port uninstall wget").itype == "EXEC"

    def test_port_upgrade_is_exec(self):
        assert analyze_command("port upgrade outdated").itype == "EXEC"

    def test_port_list_is_read(self):
        assert analyze_command("port list installed").itype == "READ"

    def test_port_info_is_read(self):
        assert analyze_command("port info wget").itype == "READ"

    def test_port_search_is_read(self):
        assert analyze_command("port search wget").itype == "READ"


# ---------------------------------------------------------------------------
# Instruction type — launchctl read-only subcommands
# ---------------------------------------------------------------------------


class TestLaunchctl:
    def test_launchctl_list_is_exec(self):
        # launchctl is EXEC in exe_registry; read-only subcommands are only
        # distinguished in exe_risk (LOW), not promoted to READ in itype.
        assert analyze_command("launchctl list").itype == "EXEC"

    def test_launchctl_print_is_exec(self):
        assert analyze_command("launchctl print system").itype == "EXEC"

    def test_launchctl_load_is_exec(self):
        assert analyze_command("launchctl load /Library/LaunchDaemons/com.example.plist").itype == "EXEC"


# ---------------------------------------------------------------------------
# Instruction type — commands shared with linux (regression guard)
# ---------------------------------------------------------------------------


class TestSharedCommands:
    def test_cat_is_read(self):
        assert analyze_command("cat /etc/hosts").itype == "READ"

    def test_ls_is_read(self):
        assert analyze_command("ls ~/Desktop").itype == "READ"

    def test_cp_is_write(self):
        assert analyze_command("cp /src /dst").itype == "WRITE"

    def test_mv_is_write(self):
        assert analyze_command("mv /old /new").itype == "WRITE"

    def test_rm_is_exec(self):
        assert analyze_command("rm -rf /tmp/junk").itype == "EXEC"

    def test_python_is_exec(self):
        assert analyze_command("python3 script.py").itype == "EXEC"

    def test_git_push_is_exec(self):
        assert analyze_command("git push origin main").itype == "EXEC"

    def test_git_log_is_read(self):
        assert analyze_command("git log --oneline").itype == "READ"

    def test_git_commit_is_write(self):
        assert analyze_command("git commit -m 'msg'").itype == "WRITE"


# ---------------------------------------------------------------------------
# Risk — macOS-specific HIGH risk commands
# ---------------------------------------------------------------------------


class TestMacOSHighRisk:
    def test_rm_is_high_risk(self):
        assert analyze_command("rm -rf /tmp/junk").risk == "HIGH"

    def test_osascript_is_high_risk(self):
        assert analyze_command("osascript -e 'do shell script \"rm -rf /\"'").risk == "HIGH"

    def test_diskutil_is_high_risk(self):
        assert analyze_command("diskutil eraseDisk APFS MyDisk /dev/disk2").risk == "HIGH"

    def test_hdiutil_is_high_risk(self):
        assert analyze_command("hdiutil create -size 10g -fs APFS ~/disk.dmg").risk == "HIGH"

    def test_dd_is_high_risk(self):
        assert analyze_command("dd if=/dev/urandom of=/dev/disk2").risk == "HIGH"

    def test_security_is_high_risk(self):
        assert analyze_command("security delete-generic-password -s MyService").risk == "HIGH"

    def test_kill_is_high_risk(self):
        assert analyze_command("kill -9 1234").risk == "HIGH"

    def test_pkill_is_high_risk(self):
        assert analyze_command("pkill Finder").risk == "HIGH"

    def test_killall_is_high_risk(self):
        assert analyze_command("killall Dock").risk == "HIGH"

    def test_shutdown_is_high_risk(self):
        assert analyze_command("shutdown -h now").risk == "HIGH"

    def test_git_clean_is_high_risk(self):
        assert analyze_command("git clean -fdx").risk == "HIGH"

    def test_git_reset_is_high_risk(self):
        assert analyze_command("git reset --hard HEAD").risk == "HIGH"

    def test_sudo_rm_is_high_risk(self):
        assert analyze_command("sudo rm -rf /System/important").risk == "HIGH"

    def test_truncate_is_high_risk(self):
        assert analyze_command("truncate -s 0 /var/log/system.log").risk == "HIGH"


# ---------------------------------------------------------------------------
# Risk — macOS-specific LOW risk commands
# ---------------------------------------------------------------------------


class TestMacOSLowRisk:
    def test_sw_vers_is_low_risk(self):
        assert analyze_command("sw_vers").risk == "LOW"

    def test_mdfind_is_low_risk(self):
        assert analyze_command("mdfind -name 'report.pdf'").risk == "LOW"

    def test_mdls_is_low_risk(self):
        assert analyze_command("mdls ~/Documents/file.pdf").risk == "LOW"

    def test_vm_stat_is_low_risk(self):
        assert analyze_command("vm_stat").risk == "LOW"

    def test_launchctl_list_is_low_risk(self):
        assert analyze_command("launchctl list").risk == "LOW"

    def test_launchctl_print_is_low_risk(self):
        assert analyze_command("launchctl print system").risk == "LOW"

    def test_brew_list_is_low_risk(self):
        assert analyze_command("brew list").risk == "LOW"

    def test_brew_info_is_low_risk(self):
        assert analyze_command("brew info wget").risk == "LOW"

    def test_ls_is_low_risk(self):
        assert analyze_command("ls ~/Desktop").risk == "LOW"

    def test_cat_is_low_risk(self):
        assert analyze_command("cat /etc/hosts").risk == "LOW"


# ---------------------------------------------------------------------------
# Risk folding in pipelines
# ---------------------------------------------------------------------------


class TestRiskFolding:
    def test_high_wins_over_low_in_pipeline(self):
        r = analyze_command("ls ~/Desktop | rm -rf /tmp/junk")
        assert r.risk == "HIGH"

    def test_osascript_high_wins(self):
        r = analyze_command("cat /etc/hosts | osascript -e 'display dialog \"hi\"'")
        assert r.risk == "HIGH"

    def test_all_low_stays_low(self):
        r = analyze_command("sw_vers && ls ~/Desktop && cat /etc/hosts")
        assert r.risk == "LOW"

    def test_unknown_taints_low(self):
        # brew install is EXEC but UNKNOWN risk → prevents LOW result
        r = analyze_command("ls /tmp && brew install wget")
        assert r.risk == "UNKNOWN"


# ---------------------------------------------------------------------------
# Instruction type folding in pipelines
# ---------------------------------------------------------------------------


class TestItypeFolding:
    def test_exec_beats_read(self):
        r = analyze_command("cat /etc/hosts | osascript -e 'tell application \"Finder\" to quit'")
        assert r.itype == "EXEC"

    def test_exec_beats_write(self):
        r = analyze_command("ditto /src /dst && osascript -e 'say \"done\"'")
        assert r.itype == "EXEC"

    def test_write_beats_read(self):
        r = analyze_command("cat /etc/hosts && ditto /src /dst")
        assert r.itype == "WRITE"

    def test_all_read_stays_read(self):
        r = analyze_command("cat /etc/hosts | grep search | wc -l")
        assert r.itype == "READ"
