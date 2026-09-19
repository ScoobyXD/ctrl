param([switch]$NoBrowser, [switch]$NoPanel, [switch]$NoDebugger, [switch]$WebDashboard, [string]$StopGeneration)
$ErrorActionPreference = 'Stop'
$ctrlRoot = $PSScriptRoot
Set-Location -LiteralPath $ctrlRoot
$ctrlPython = Join-Path $ctrlRoot '.venv\Scripts\python.exe'
$ctrlState = Join-Path $ctrlRoot '.ctrl'
$ctrlGenerationPath = Join-Path $ctrlState 'stop-generation'
$ctrlCurrentGeneration = if (Test-Path -LiteralPath $ctrlGenerationPath) { [System.IO.File]::ReadAllText($ctrlGenerationPath).Trim() } else { 'initial' }
if (-not $StopGeneration) { $StopGeneration = $ctrlCurrentGeneration }
if ($StopGeneration -ne $ctrlCurrentGeneration) { throw 'Startup cancelled by Stop.' }
if (-not (Test-Path -LiteralPath $ctrlPython)) {
    Write-Host '[CTRL] Setting up the local Python environment...'
    & uv sync --locked --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed. Check uv and network access.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $ctrlRoot 'ctrl\static\index.html'))) {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Interface dependency setup failed.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Interface build failed.' }
}
$ctrlReady = $false
try {
    $ctrlHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/health' -TimeoutSec 2
    $ctrlReady = $ctrlHealth.app -eq '[CTRL]'
} catch {}
if (-not $ctrlReady) {
    New-Item -ItemType Directory -Path $ctrlState -Force | Out-Null
    $ctrlProcessId = & $ctrlPython -m ctrl.launch --stop-generation $StopGeneration
    if ($LASTEXITCODE -ne 0) { throw 'Could not launch the Python server.' }
    $ctrlProcess = Get-Process -Id ([int]$ctrlProcessId)
    for ($ctrlAttempt = 0; $ctrlAttempt -lt 30; $ctrlAttempt++) {
        Start-Sleep -Milliseconds 300
        try {
            $ctrlHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/health' -TimeoutSec 1
            if ($ctrlHealth.app -eq '[CTRL]') { $ctrlReady = $true; break }
        } catch {}
        if ($ctrlProcess.HasExited) { break }
    }
    if (-not $ctrlReady) { throw 'Could not start [CTRL]. Inspect .ctrl/server-error.log.' }
}
if ((Test-Path -LiteralPath $ctrlGenerationPath) -and [System.IO.File]::ReadAllText($ctrlGenerationPath).Trim() -ne $StopGeneration) {
    throw 'Startup cancelled by Stop.'
}
if (-not $NoPanel) {
    $ctrlPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $ctrlPanelScript = Join-Path $ctrlRoot 'scripts\Control-CTRL.ps1'
    Start-Process -FilePath $ctrlPowerShell -WindowStyle Hidden -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-File', ('"' + $ctrlPanelScript + '"'), '-StopGeneration', $StopGeneration
    ) | Out-Null
}
if (-not $NoDebugger) {
    $ctrlPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $ctrlDebugScript = Join-Path $ctrlRoot 'scripts\Debug-CTRL.ps1'
    Start-Process -FilePath $ctrlPowerShell -WindowStyle Hidden -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-File', ('"' + $ctrlDebugScript + '"'), '-StopGeneration', $StopGeneration
    ) | Out-Null
}
if ($WebDashboard -and -not $NoBrowser) {
    $ctrlChrome = Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'
    if (-not (Test-Path -LiteralPath $ctrlChrome)) {
        $ctrlChrome = Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'
    }
    if (-not (Test-Path -LiteralPath $ctrlChrome)) { throw 'Install Google Chrome to open [CTRL].' }
    # A normal tab in the existing Chrome profile, with no automation profile or browser flags.
    Start-Process -FilePath $ctrlChrome -ArgumentList 'http://127.0.0.1:8766'
}
Write-Host '[CTRL] native debugger is ready. Its backend runs at http://127.0.0.1:8766'
