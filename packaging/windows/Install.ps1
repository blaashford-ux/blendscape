# Blendscape: one-time setup on Windows. Creates a private Python environment next to this file and installs
# Blendscape into it. Needs Python 3.10-3.13 (https://www.python.org/downloads/ or: winget install Python.Python.3.12).
# SPDX-License-Identifier: GPL-3.0-or-later
param([switch]$NoPause)
$ErrorActionPreference = "Continue"  # native tools (pip) write warnings to stderr; we check exit codes instead
Set-Location -LiteralPath $PSScriptRoot

function Finish([int]$code) {
    if (-not $NoPause) { Read-Host "Press Enter to close" | Out-Null }
    exit $code
}

# find a usable Python: the py launcher first (it can pick a version), then plain python
$python = $null
$pyArgs = @()
foreach ($cand in @("py -3.12", "py -3.13", "py -3.11", "py -3.10", "python")) {
    $parts = @($cand -split " ")
    $exe = $parts[0]
    $rest = @($parts | Select-Object -Skip 1)
    try {
        $v = & $exe @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -match '^3\.(\d+)$' -and [int]$Matches[1] -ge 10 -and [int]$Matches[1] -le 13) {
            $python = $exe
            $pyArgs = $rest
            break
        }
    } catch { }
}
if (-not $python) {
    Write-Host ""
    Write-Host "Python 3.10 to 3.13 was not found." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), or run:"
    Write-Host "    winget install Python.Python.3.12"
    Write-Host "then run this setup again."
    Finish 1
}
Write-Host "Using Python $v"

$wheel = Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot "wheels") -Filter "blendscape-*.whl" | Select-Object -First 1
if (-not $wheel) { Write-Host "No blendscape wheel found in the wheels folder." -ForegroundColor Red; Finish 1 }

Write-Host "Creating the environment (.venv) ..."
& $python @pyArgs -m venv .venv
if ($LASTEXITCODE -ne 0) { Write-Host "Could not create the environment." -ForegroundColor Red; Finish 1 }

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
Write-Host "Installing Blendscape and its libraries (a few minutes the first time) ..."
& $venvPython -m pip install --disable-pip-version-check --upgrade pip
& $venvPython -m pip install --disable-pip-version-check "$($wheel.FullName)[api]"
if ($LASTEXITCODE -ne 0) { Write-Host "The install failed: see the messages above." -ForegroundColor Red; Finish 1 }

$rb = Join-Path $PSScriptRoot "tools\rubberband.exe"
if (Test-Path $rb) { $env:RUBBERBAND = $rb }
Write-Host ""
& (Join-Path $PSScriptRoot ".venv\Scripts\blendscape.exe") doctor
Write-Host ""
Write-Host "Done. Start Blendscape with 'Start Blendscape.bat'." -ForegroundColor Green
Finish 0
