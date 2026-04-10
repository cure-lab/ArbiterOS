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
            & $Command
        }
        return $LASTEXITCODE
    } catch {
        return 1
    } finally {
        $ErrorActionPreference = $prevEap
    }
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
    $cwdKernelDir = Join-Path (Get-Location).Path $KernelSubdir
    $cwdReadme = Join-Path (Get-Location).Path "README.md"
    if ((Test-Path $cwdKernelDir) -and (Test-Path $cwdReadme)) {
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

    $py = @"
from pathlib import Path
import sys

cfg = Path(sys.argv[1])
model_name, model, api_key, api_base = sys.argv[2:6]
skills_root, scanner_model, scanner_base, scanner_key = sys.argv[6:10]

lines = cfg.read_text(encoding="utf-8").splitlines()
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

cfg.write_text("\\n".join(out) + "\\n", encoding="utf-8")
"@

    $tmpPy = [System.IO.Path]::ChangeExtension([System.IO.Path]::GetTempFileName(), ".py")
    try {
        [System.IO.File]::WriteAllText($tmpPy, $py, [System.Text.UTF8Encoding]::new($false))
        & python $tmpPy $cfg $modelName $model $apiKey $apiBase $skillsRoot $scannerModel $scannerBase $scannerKey
        if ($LASTEXITCODE -ne 0) {
            Fail "Failed to update litellm_config.yaml via Python helper (exit code: $LASTEXITCODE)."
        }
        $script:ConfiguredModelName = $modelName
    } finally {
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
        "{}" | Out-File -FilePath $OpenClawConfigPath -Encoding utf8
    }

    $py = @"
import json
import sys
from pathlib import Path

cfg_path = Path(sys.argv[1])
model_name = sys.argv[2]
model_key = f"arbiteros/{model_name}"

try:
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
except Exception:
    data = {}

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

cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
"@

    $tmpPy = [System.IO.Path]::ChangeExtension([System.IO.Path]::GetTempFileName(), ".py")
    try {
        [System.IO.File]::WriteAllText($tmpPy, $py, [System.Text.UTF8Encoding]::new($false))
        & python $tmpPy $OpenClawConfigPath $modelName
        if ($LASTEXITCODE -ne 0) {
            Fail "Failed to update OpenClaw config via Python helper (exit code: $LASTEXITCODE)."
        }
    } finally {
        if (Test-Path $tmpPy) {
            Remove-Item -Path $tmpPy -Force -ErrorAction SilentlyContinue
        }
    }
    Log "Updated OpenClaw config: $OpenClawConfigPath"
    Log "Set provider=arbiteros, primary=arbiteros/$modelName"
}

function Restart-OpenClawGateway-And-Dashboard {
    $openclaw = Get-Command openclaw -ErrorAction SilentlyContinue
    if (-not $openclaw) {
        Warn "openclaw command not found. Skipping gateway restart/dashboard."
        return
    }

    $versionExit = Invoke-ExternalCommand -Command { openclaw --version } -SilenceOutput
    if ($versionExit -ne 0) {
        Warn "openclaw exists in PATH but is broken. Reinstall OpenClaw CLI, then rerun this script."
        Warn "Suggested fix: npm uninstall -g openclaw; npm install -g @openclaw/cli"
        return
    }

    Log "Restarting OpenClaw gateway..."
    $restartExit = Invoke-ExternalCommand -Command { openclaw gateway restart }
    if ($restartExit -ne 0) {
        Warn "openclaw gateway restart failed; trying openclaw gateway start..."
        $startExit = Invoke-ExternalCommand -Command { openclaw gateway start }
        if ($startExit -ne 0) {
            Warn "Failed to start OpenClaw gateway."
            return
        }
    }

    Log "Opening OpenClaw dashboard..."
    $dashExit = Invoke-ExternalCommand -Command { openclaw dashboard }
    if ($dashExit -ne 0) {
        Warn "Failed to open OpenClaw dashboard."
    }
}

function Write-RunScript {
    $runScript = Join-Path $InstallDir "run-kernel.ps1"
    $content = @"
Set-StrictMode -Version Latest
`$ErrorActionPreference = "Stop"

Set-Location "$KernelDir"
`$env:Path = "`$env:USERPROFILE\.local\bin;`$env:USERPROFILE\.cargo\bin;`$env:Path"
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

    Ensure-Command "git"
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
