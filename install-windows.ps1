Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ArbiterOSRepoUrl = if ($env:ARBITEROS_REPO_URL) { $env:ARBITEROS_REPO_URL } else { "https://github.com/cure-lab/ArbiterOS.git" }
$ArbiterOSBranch = if ($env:ARBITEROS_BRANCH) { $env:ARBITEROS_BRANCH } else { "main" }
$InstallRoot = if ($env:INSTALL_ROOT) { $env:INSTALL_ROOT } else { $env:USERPROFILE }
$InstallDir = if ($env:INSTALL_DIR) { $env:INSTALL_DIR } else { Join-Path $InstallRoot "ArbiterOS" }
$KernelSubdir = if ($env:KERNEL_SUBDIR) { $env:KERNEL_SUBDIR } else { "ArbiterOS-Kernel" }
$KernelDir = if ($env:KERNEL_DIR) { $env:KERNEL_DIR } else { Join-Path $InstallDir $KernelSubdir }
$OpenClawConfigPath = if ($env:OPENCLAW_CONFIG_PATH) { $env:OPENCLAW_CONFIG_PATH } else { Join-Path $env:USERPROFILE ".openclaw\openclaw.json" }
$ConfiguredModelName = ""

function Log([string]$Message) {
    Write-Host "[INFO] $Message"
}

function Warn([string]$Message) {
    Write-Warning $Message
}

function Fail([string]$Message) {
    throw $Message
}

function Invoke-ExternalCommand([ScriptBlock]$Command, [switch]$SilenceOutput) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($SilenceOutput) {
            & $Command *> $null
        } else {
            # Do not let native stdout/stderr become function pipeline output; callers expect a single exit code.
            & $Command 2>&1 | Out-Host
        }
        if ($null -eq $LASTEXITCODE) {
            return 0
        }
        return $LASTEXITCODE
    } catch {
        return 1
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Get-OpenClawGatewayPort {
    if ($env:OPENCLAW_GATEWAY_PORT -match '^\d+$') {
        return [int]$env:OPENCLAW_GATEWAY_PORT
    }
    return 18789
}

function Test-OpenClawGatewayPortOpen {
    param(
        [int]$Port = 0,
        [int]$TimeoutMs = 2500
    )
    if ($Port -le 0) {
        $Port = Get-OpenClawGatewayPort
    }
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $signaled = $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if (-not $signaled) {
            return $false
        }
        try {
            $client.EndConnect($iar)
        } catch {
            return $false
        }
        return $client.Connected
    } catch {
        return $false
    } finally {
        if ($null -ne $client) {
            try { $client.Close() } catch { }
        }
    }
}

function Wait-OpenClawGatewayPortOpen {
    param(
        [int]$MaxWaitSeconds = 20,
        [int]$IntervalSeconds = 2
    )
    $port = Get-OpenClawGatewayPort
    $deadline = [datetime]::UtcNow.AddSeconds($MaxWaitSeconds)
    while ([datetime]::UtcNow -lt $deadline) {
        if (Test-OpenClawGatewayPortOpen -Port $port) {
            return $true
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
    return (Test-OpenClawGatewayPortOpen -Port $port)
}

function Wait-OpenClawGatewayPortClosed {
    param(
        [int]$MaxWaitSeconds = 45,
        [int]$IntervalSeconds = 1
    )
    $port = Get-OpenClawGatewayPort
    $deadline = [datetime]::UtcNow.AddSeconds($MaxWaitSeconds)
    while ([datetime]::UtcNow -lt $deadline) {
        if (-not (Test-OpenClawGatewayPortOpen -Port $port -TimeoutMs 800)) {
            return $true
        }
        Start-Sleep -Seconds $IntervalSeconds
    }
    return -not (Test-OpenClawGatewayPortOpen -Port $port -TimeoutMs 800)
}

function Ensure-Command([string]$CommandName) {
    $cmd = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($cmd) {
        return
    }

    switch ($CommandName) {
        "git" {
            Fail "Missing required command: git. Please install Git for Windows first."
        }
        "python" {
            Fail "Missing required command: python. Please install Python 3.12+ first."
        }
        "uv" {
            Log "Installing uv for current user..."
            powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
            $env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:Path"
            if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
                Fail "uv install script finished, but uv is still not found in PATH. Open a new PowerShell and rerun."
            }
        }
        default {
            Fail "Cannot auto-install command: $CommandName"
        }
    }
}

function Test-CurrentDirectoryIsArbiterOSRoot {
    $root = (Get-Location).Path
    $cwdKernelDir = Join-Path $root $KernelSubdir
    $cwdReadme = Join-Path $root "README.md"
    return ((Test-Path $cwdKernelDir) -and (Test-Path $cwdReadme))
}

function Ensure-Python312 {
    $versionOk = $false
    try {
        & python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
        if ($LASTEXITCODE -eq 0) {
            $versionOk = $true
        }
    } catch {
        $versionOk = $false
    }

    if ($versionOk) {
        return
    }

    Log "Python 3.12+ not detected. Installing with uv..."
    & uv python install 3.12
}

function Clone-Or-Use-Repo {
    if (Test-CurrentDirectoryIsArbiterOSRoot) {
        $script:InstallDir = (Get-Location).Path
        $script:KernelDir = Join-Path $script:InstallDir $KernelSubdir
        Log "Using current directory: $script:InstallDir"
        return
    }

    $gitDir = Join-Path $InstallDir ".git"
    if (Test-Path $gitDir) {
        Log "Updating existing ArbiterOS repo at $InstallDir"
        & git -C $InstallDir fetch origin $ArbiterOSBranch
        & git -C $InstallDir checkout $ArbiterOSBranch
        & git -C $InstallDir pull --ff-only origin $ArbiterOSBranch
    } else {
        Log "Cloning ArbiterOS into $InstallDir"
        & git clone -b $ArbiterOSBranch $ArbiterOSRepoUrl $InstallDir
    }

    if (-not (Test-Path $KernelDir)) {
        Fail "Kernel directory not found: $KernelDir. Please set KERNEL_SUBDIR/KERNEL_DIR."
    }
}

function Setup-Kernel {
    Push-Location $KernelDir
    try {
        & uv sync --group dev
        $envExample = Join-Path $KernelDir ".env.example"
        $envFile = Join-Path $KernelDir ".env"
        if ((Test-Path $envExample) -and (-not (Test-Path $envFile))) {
            Copy-Item $envExample $envFile
            Log "Created $envFile from .env.example"
        } elseif (-not (Test-Path $envFile)) {
            New-Item -Path $envFile -ItemType File | Out-Null
        }
    } finally {
        Pop-Location
    }
}

function Read-With-Default([string]$Prompt, [string]$DefaultValue) {
    if ($DefaultValue) {
        $val = Read-Host "$Prompt [$DefaultValue]"
        if ([string]::IsNullOrWhiteSpace($val)) { return $DefaultValue }
        return $val
    }
    return (Read-Host "$Prompt")
}

function Configure-LiteLLMYaml {
    $cfg = Join-Path $KernelDir "litellm_config.yaml"
    if (-not (Test-Path $cfg)) {
        Fail "Missing file: $cfg"
    }

    $defaultModelName = "gpt-4o-mini"
    $defaultModel = "openai/gpt-4o-mini"
    $defaultApiBase = "https://api.openai.com/v1"
    $defaultScannerModel = "openai/gpt-4.1-mini"

    Log "Configure first model entry in $cfg"
    $modelName = Read-With-Default "model_name" $defaultModelName
    $model = Read-With-Default "litellm_params.model" $defaultModel
    $apiKey = Read-With-Default "litellm_params.api_key" ""
    $apiBase = Read-With-Default "litellm_params.api_base" $defaultApiBase

    Log "Configure skill trust / skill scanner in $cfg"
    $skillsRoot = Read-With-Default "arbiteros_skill_trust.skills_root" ""
    $scannerModel = Read-With-Default "skill_scanner_llm.model" $defaultScannerModel
    $scannerBase = Read-With-Default "skill_scanner_llm.api_base" $defaultApiBase
    $scannerKey = Read-With-Default "skill_scanner_llm.api_key" ""

    $py = @'
from pathlib import Path
import os
import sys

cfg = Path(sys.argv[1])
model_name = os.environ.get("ARBITEROS_MODEL_NAME", "")
model = os.environ.get("ARBITEROS_MODEL", "")
api_key = os.environ.get("ARBITEROS_API_KEY", "")
api_base = os.environ.get("ARBITEROS_API_BASE", "")
skills_root = os.environ.get("ARBITEROS_SKILLS_ROOT", "")
scanner_model = os.environ.get("ARBITEROS_SCANNER_MODEL", "")
scanner_base = os.environ.get("ARBITEROS_SCANNER_BASE", "")
scanner_key = os.environ.get("ARBITEROS_SCANNER_KEY", "")

raw = cfg.read_text(encoding="utf-8")
# If YAML was saved as one physical line with literal \n (two chars) — e.g. pasted from JSON —
# splitlines() yields a single row and no startswith() rules match; the file would be written back
# unchanged and stay broken. Decode those escapes when we see almost no real newlines.
if raw.count("\n") <= 1 and "\\n" in raw:
    raw = raw.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
lines = raw.splitlines()
out = []
in_first_model = False
in_params = False
first_model_done = False
in_trust = False
in_scanner = False

for line in lines:
    if line.startswith("  - model_name:") and not first_model_done:
        in_first_model = True
        first_model_done = True
        out.append(f"  - model_name: {model_name}")
        continue
    if in_first_model and line.startswith("  - model_name:"):
        in_first_model = False
        in_params = False
        out.append(line)
        continue
    if in_first_model and line.startswith("    litellm_params:"):
        in_params = True
        out.append(line)
        continue
    if in_first_model and in_params and line.startswith("      model:"):
        out.append(f"      model: {model}")
        continue
    if in_first_model and in_params and line.startswith("      api_key:"):
        out.append(f"      api_key: {api_key}")
        continue
    if in_first_model and in_params and line.startswith("      api_base:"):
        out.append(f"      api_base: {api_base}")
        continue

    if line.startswith("arbiteros_skill_trust:"):
        in_trust = True
        in_scanner = False
        out.append(line)
        continue
    if line.startswith("skill_scanner_llm:"):
        in_scanner = True
        in_trust = False
        out.append(line)
        continue
    if line.startswith("litellm_settings:"):
        in_trust = False
        in_scanner = False
        out.append(line)
        continue
    if in_trust and line.startswith("  skills_root:"):
        out.append(f"  skills_root: {skills_root}")
        continue
    if in_scanner and line.startswith("  model:"):
        out.append(f"  model: {scanner_model}")
        continue
    if in_scanner and line.startswith("  api_base:"):
        out.append(f"  api_base: {scanner_base}")
        continue
    if in_scanner and line.startswith("  api_key:"):
        out.append(f"  api_key: {scanner_key}")
        continue
    out.append(line)

nl = chr(10)
cfg.write_text(nl.join(out) + nl, encoding="utf-8")
'@

    $tmpPy = [System.IO.Path]::ChangeExtension([System.IO.Path]::GetTempFileName(), ".py")
    try {
        [System.IO.File]::WriteAllText($tmpPy, $py, [System.Text.UTF8Encoding]::new($false))
        $prevModelName = $env:ARBITEROS_MODEL_NAME
        $prevModel = $env:ARBITEROS_MODEL
        $prevApiKey = $env:ARBITEROS_API_KEY
        $prevApiBase = $env:ARBITEROS_API_BASE
        $prevSkillsRoot = $env:ARBITEROS_SKILLS_ROOT
        $prevScannerModel = $env:ARBITEROS_SCANNER_MODEL
        $prevScannerBase = $env:ARBITEROS_SCANNER_BASE
        $prevScannerKey = $env:ARBITEROS_SCANNER_KEY
        $env:ARBITEROS_MODEL_NAME = $modelName
        $env:ARBITEROS_MODEL = $model
        $env:ARBITEROS_API_KEY = $apiKey
        $env:ARBITEROS_API_BASE = $apiBase
        $env:ARBITEROS_SKILLS_ROOT = $skillsRoot
        $env:ARBITEROS_SCANNER_MODEL = $scannerModel
        $env:ARBITEROS_SCANNER_BASE = $scannerBase
        $env:ARBITEROS_SCANNER_KEY = $scannerKey
        & python $tmpPy $cfg
        if ($LASTEXITCODE -ne 0) {
            Fail "Failed to update litellm_config.yaml via Python helper (exit code: $LASTEXITCODE)."
        }
        $script:ConfiguredModelName = $modelName
    } finally {
        $env:ARBITEROS_MODEL_NAME = $prevModelName
        $env:ARBITEROS_MODEL = $prevModel
        $env:ARBITEROS_API_KEY = $prevApiKey
        $env:ARBITEROS_API_BASE = $prevApiBase
        $env:ARBITEROS_SKILLS_ROOT = $prevSkillsRoot
        $env:ARBITEROS_SCANNER_MODEL = $prevScannerModel
        $env:ARBITEROS_SCANNER_BASE = $prevScannerBase
        $env:ARBITEROS_SCANNER_KEY = $prevScannerKey
        if (Test-Path $tmpPy) {
            Remove-Item -Path $tmpPy -Force -ErrorAction SilentlyContinue
        }
    }
}

function Configure-OpenClawJson {
    $litellmCfg = Join-Path $KernelDir "litellm_config.yaml"
    if (-not (Test-Path $litellmCfg)) {
        Warn "Missing $litellmCfg. Skipping OpenClaw config."
        return
    }

    $modelName = ""
    foreach ($line in Get-Content $litellmCfg) {
        if ($line -match "^\s*-\s*model_name:\s*(.+)$") {
            $modelName = $Matches[1].Trim()
            break
        }
    }
    if (-not $modelName -and $script:ConfiguredModelName) {
        $modelName = $script:ConfiguredModelName
        Log "Using configured model_name from installer input: $modelName"
    }
    if (-not $modelName) {
        Warn "Cannot read model_name from $litellmCfg. Skipping OpenClaw config."
        return
    }

    $cfgDir = Split-Path -Parent $OpenClawConfigPath
    if (-not (Test-Path $cfgDir)) {
        New-Item -Path $cfgDir -ItemType Directory -Force | Out-Null
    }
    if (-not (Test-Path $OpenClawConfigPath)) {
        [System.IO.File]::WriteAllText($OpenClawConfigPath, "{}" + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    }

    # Single-quoted here-string: no PowerShell $ expansion. Use chr(10) + json.dump so the temp .py
    # never relies on backslash escapes (avoids JSON files ending with literal \n or other parse errors).
    $py = @'
import json
import sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
model_name = sys.argv[2].strip()
model_key = f"arbiteros/{model_name}"

raw = cfg_path.read_text(encoding="utf-8-sig")
try:
    data = json.loads(raw)
except Exception as e:
    print(f"ERROR: existing OpenClaw config is not valid JSON: {e}", file=sys.stderr)
    sys.exit(2)

data.setdefault("models", {})
data["models"].setdefault("providers", {})
data["models"]["providers"]["arbiteros"] = {
    "baseUrl": "http://127.0.0.1:4000/v1",
    "apiKey": "n/a",
    "api": "openai-completions",
    "authHeader": False,
    "models": [
        {
            "id": model_name,
            "name": model_name,
            "reasoning": False,
            "input": ["text"],
            "cost": {
                "input": 0,
                "output": 0,
                "cacheRead": 0,
                "cacheWrite": 0,
            },
            "contextWindow": 200000,
            "maxTokens": 8192,
            "compat": {"supportsStore": False},
        }
    ],
}

data.setdefault("agents", {})
data["agents"].setdefault("defaults", {})
data["agents"]["defaults"].setdefault("model", {})
data["agents"]["defaults"]["model"]["primary"] = model_key
data["agents"]["defaults"].setdefault("models", {})
data["agents"]["defaults"]["models"].setdefault(model_key, {})

data.setdefault("auth", {})
data["auth"].setdefault("profiles", {})
data["auth"]["profiles"].setdefault("openai:default", {})
data["auth"]["profiles"]["openai:default"]["provider"] = "arbiteros"
data["auth"]["profiles"]["openai:default"].setdefault("mode", "api_key")

data.setdefault("gateway", {})
data["gateway"].setdefault("mode", "local")

with cfg_path.open("w", encoding="utf-8", newline=chr(10)) as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write(chr(10))

# Defensive cleanup: if a previous/broken writer left a literal "\n" suffix,
# remove it so OpenClaw JSON5 parser won't fail on trailing backslash.
raw_after = cfg_path.read_text(encoding="utf-8")
if raw_after.endswith("\\n"):
    cfg_path.write_text(raw_after[:-2] + chr(10), encoding="utf-8")
'@

    $tmpPy = [System.IO.Path]::ChangeExtension([System.IO.Path]::GetTempFileName(), ".py")
    try {
        [System.IO.File]::WriteAllText($tmpPy, $py, [System.Text.UTF8Encoding]::new($false))
        & python $tmpPy $OpenClawConfigPath $modelName
        if ($LASTEXITCODE -ne 0) {
            Warn "Skipped updating OpenClaw config because existing file is not valid JSON (exit code: $LASTEXITCODE)."
            Warn "Run 'openclaw doctor --fix' (or onboard) and rerun installer."
            return
        }
    } finally {
        if (Test-Path $tmpPy) {
            Remove-Item -Path $tmpPy -Force -ErrorAction SilentlyContinue
        }
    }
    Log "Updated OpenClaw config: $OpenClawConfigPath"
    Log "Set provider=arbiteros, primary=arbiteros/$modelName"
}

function Get-OpenClawDashboardStartInfo {
    param(
        [System.Management.Automation.CommandInfo]$OpenClawCommand
    )
    $path = if ($OpenClawCommand.Source) { $OpenClawCommand.Source } elseif ($OpenClawCommand.Path) { $OpenClawCommand.Path } else { $null }
    if (-not $path) {
        return @{ FilePath = "openclaw"; ArgumentList = @("dashboard") }
    }
    # Start-Process on a .ps1 uses the file association (often Notepad), not PowerShell. Prefer npm's .cmd shim.
    if ($path -like "*.ps1") {
        $cmdShim = [System.IO.Path]::ChangeExtension($path, ".cmd")
        if (Test-Path -LiteralPath $cmdShim) {
            return @{ FilePath = $cmdShim; ArgumentList = @("dashboard") }
        }
        $psHost = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        if (-not (Test-Path -LiteralPath $psHost)) {
            $psHost = "powershell.exe"
        }
        return @{ FilePath = $psHost; ArgumentList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $path, "dashboard") }
    }
    return @{ FilePath = $path; ArgumentList = @("dashboard") }
}

function Restart-OpenClawGateway-And-Dashboard {
    $openclaw = Get-Command openclaw -ErrorAction SilentlyContinue
    if (-not $openclaw) {
        Warn "openclaw command not found. Skipping gateway start and dashboard."
        return
    }

    $versionExit = Invoke-ExternalCommand -Command { openclaw --version } -SilenceOutput
    if ($versionExit -ne 0) {
        Warn "openclaw exists in PATH but is broken. Reinstall OpenClaw CLI, then rerun this script."
        Warn "Suggested fix: npm uninstall -g openclaw; npm install -g @openclaw/cli"
        return
    }

    $gwPort = Get-OpenClawGatewayPort

    # Windows: `openclaw gateway restart` tends to hit scheduled-task + health-check timeouts while the
    # port is still held. Stop first, wait for the port to drop, then a plain `gateway start` reloads config
    # without the flaky restart path.
    if (Test-OpenClawGatewayPortOpen -Port $gwPort) {
        Log "Gateway already listening on 127.0.0.1:${gwPort}; stopping so the next start picks up updated config."
        $null = Invoke-ExternalCommand -Command { openclaw gateway stop } -SilenceOutput
        if (Wait-OpenClawGatewayPortClosed -MaxWaitSeconds 45) {
            Start-Sleep -Seconds 2
        } else {
            Warn "Port ${gwPort} did not free within 45s after openclaw gateway stop; start may fail or hit a stale listener."
        }
    }

    Log "Starting OpenClaw gateway..."
    $startExit = Invoke-ExternalCommand -Command { openclaw gateway start }
    $gatewayUp = $false
    if ($startExit -eq 0) {
        $gatewayUp = $true
    } else {
        # Windows often launches the gateway in a separate console; the CLI may time out on health
        # checks (e.g. 60s) even though the HTTP server is already listening.
        Start-Sleep -Seconds 2
        if (Wait-OpenClawGatewayPortOpen -MaxWaitSeconds 20) {
            Log "Gateway is listening on 127.0.0.1:${gwPort} (openclaw start exited $startExit; treating as success)."
            $gatewayUp = $true
        }
    }

    if (-not $gatewayUp) {
        Warn "Failed to start OpenClaw gateway (port $gwPort not listening). Try in a separate window: openclaw gateway"
        return
    }

    Log "Opening OpenClaw dashboard..."
    try {
        # In-script `openclaw dashboard` often returns a non-zero exit code even when it works
        # (no interactive TTY / browser launch path). Launch a separate process like a normal shell.
        $dash = Get-OpenClawDashboardStartInfo -OpenClawCommand $openclaw
        Start-Process -FilePath $dash.FilePath -ArgumentList $dash.ArgumentList -WorkingDirectory (Get-Location).Path -ErrorAction Stop
        Log "Launched dashboard helper. If the browser did not open, run in any terminal: openclaw dashboard"
    } catch {
        Warn "Could not start openclaw dashboard from installer: $($_.Exception.Message)"
        Log "Run manually: openclaw dashboard"
    }
}

function Write-RunScript {
    $runScript = Join-Path $InstallDir "run-kernel.ps1"
    $content = @"
Set-StrictMode -Version Latest
`$ErrorActionPreference = "Stop"

Set-Location "$KernelDir"
`$env:Path = "`$env:USERPROFILE\.local\bin;`$env:USERPROFILE\.cargo\bin;`$env:Path"
`$env:PYTHONUTF8 = "1"
`$env:PYTHONIOENCODING = "utf-8"
uv run poe litellm
"@
    $content | Out-File -FilePath $runScript -Encoding utf8
    Log "Created run script: $runScript"
}

function Test-IsWindows {
    $isWindowsVar = Get-Variable -Name IsWindows -Scope Global -ErrorAction SilentlyContinue
    if ($isWindowsVar) {
        return [bool]$isWindowsVar.Value
    }

    if ($env:OS -eq "Windows_NT") {
        return $true
    }

    return ([System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT)
}

function Main {
    if (-not (Test-IsWindows)) {
        Fail "This script is for Windows only. For Linux/macOS, use install.sh."
    }

    if (-not (Test-CurrentDirectoryIsArbiterOSRoot)) {
        Ensure-Command "git"
    } else {
        Log "Current folder has $KernelSubdir and README.md (ZIP or full checkout); git is not required."
    }
    Ensure-Command "python"
    Ensure-Command "uv"
    Ensure-Python312
    Clone-Or-Use-Repo
    Setup-Kernel
    Configure-LiteLLMYaml
    Configure-OpenClawJson
    Restart-OpenClawGateway-And-Dashboard
    Write-RunScript

    Log "Done. Kernel path: $KernelDir"
    Log "Start manually: powershell -ExecutionPolicy Bypass -File `"$InstallDir\run-kernel.ps1`""
    Warn "Windows has no systemd user service. If you need auto-start, we can add a Scheduled Task script."
}

Main
