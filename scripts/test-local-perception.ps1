#Requires -Version 7.2
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $projectRoot '.runtime/winml-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run install-local-perception.ps1 first.' }
& $python -m unittest discover -s (Join-Path $projectRoot 'tests') -v
if ($LASTEXITCODE -ne 0) { throw 'Detector geometry checks failed.' }
$observationText = & pwsh -NoProfile -File (Join-Path $PSScriptRoot 'capture-detector-fixture.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Read-only fixture capture failed.' }
$observation = ($observationText -join "`n") | ConvertFrom-Json -Depth 60
if (-not $observation.passed) { throw 'Capture did not pass.' }
& $python (Join-Path $PSScriptRoot 'test-ui-detector.py') --observation $observation.observation_path
if ($LASTEXITCODE -ne 0) { throw 'Windows ML/PyTorch detector comparison failed.' }
Write-Output "Capture identity: $($observation.capture_id)"
Write-Output "Evidence directory: $([IO.Path]::GetDirectoryName($observation.observation_path))"
