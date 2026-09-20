#Requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Python,
    [string]$Uv = 'uv'
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
    throw 'This evaluated dependency set requires Windows x64.'
}
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = (Get-Command $Python -ErrorAction Stop).Source
$uvPath = (Get-Command $Uv -ErrorAction Stop).Source
$pythonIdentity = & $pythonPath -c 'import platform,sys; print(platform.python_version()+" "+platform.machine()+" "+str(sys.prefix == sys.base_prefix))'
if ($LASTEXITCODE -ne 0 -or $pythonIdentity -ne '3.12.13 AMD64 True') {
    throw "Pass an unpackaged CPython 3.12.13 x64 base interpreter, not another application's venv: $pythonIdentity"
}
$uvIdentity = & $uvPath --version
if ($LASTEXITCODE -ne 0 -or $uvIdentity -notmatch '^uv 0\.11\.26\b') { throw 'Use uv 0.11.26 for this evaluated setup.' }

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Setup failed (exit $LASTEXITCODE): $Executable $($Arguments -join ' ')" }
}

# Only local package setup; no AWS session, secret lookup, or provider request.
$cache = Join-Path $projectRoot '.runtime/uv-cache'
$environment = Join-Path $projectRoot '.runtime/jev-venv'
Invoke-Checked $pythonPath @('-m','venv','--without-pip',$environment)
$environmentPython = Join-Path $environment 'Scripts/python.exe'
Invoke-Checked $uvPath @('--no-config','--cache-dir',$cache,'pip','sync',
    (Join-Path $projectRoot 'dependencies/jev-requirements.lock'),
    '--python',$environmentPython,'--require-hashes','--no-build','--default-index','https://pypi.org/simple')
Invoke-Checked $uvPath @('--no-config','--cache-dir',$cache,'pip','check','--python',$environmentPython)
Invoke-Checked $environmentPython @('-c','import boto3; print("boto3 " + boto3.__version__)')
Write-Output 'JEV dependencies are installed locally. Run scripts/jev-preview.py for an offline decision request preview.'
