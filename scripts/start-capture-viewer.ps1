#Requires -Version 7.2
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'The capture viewer requires Windows.' }
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $projectRoot '.runtime/viewer-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Run scripts/install-capture-viewer.ps1 -Python <CPython 3.12.13 base/python.exe> first.'
}
$diagnostics = Join-Path $projectRoot '.runtime/viewer'
[IO.Directory]::CreateDirectory($diagnostics) | Out-Null
$run = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$entry = Join-Path $PSScriptRoot 'capture-viewer.py'
# Isolated Python ignores external PYTHONPATH/user packages. The wrapper adds only this checkout.
# No extra console window; the GUI owns its helper and lives independently of the launching shell.
$process = Start-Process -FilePath $python -ArgumentList @('-I', ('"' + $entry + '"')) `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $diagnostics ($run + '.stdout.log')) `
    -RedirectStandardError (Join-Path $diagnostics ($run + '.stderr.log'))
Write-Output "CTRL started (PID $($process.Id)). Setup/troubleshooting: $projectRoot/docs/CAPTURE_VIEWER.md"
