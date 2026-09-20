#Requires -Version 7.2
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'The UIA viewer requires Windows.' }
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $projectRoot '.runtime/uia-venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Run scripts/install-uia-viewer.ps1 -Python <CPython 3.12.13 x64 base/python.exe> first.'
}
$entry = Join-Path $PSScriptRoot 'uia-viewer.py'
# Isolated Python loads only this checkout's src. No console or target-content logs.
# Start-Process resolves WorkingDirectory as a PowerShell wildcard path.
$workingDirectory = [WildcardPattern]::Escape($projectRoot)
$process = Start-Process -FilePath $python -ArgumentList @('-I', ('"' + $entry + '"')) `
    -WorkingDirectory $workingDirectory -WindowStyle Hidden -PassThru
Write-Output "UIA control-options viewer started (PID $($process.Id)). Select a target window, then Observe."
