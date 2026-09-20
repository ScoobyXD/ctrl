#Requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Python,
    [string]$Uv = 'uv'
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
    throw 'The UIA viewer requires Windows x64.'
}
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = (Get-Command $Python -ErrorAction Stop).Source
$uvPath = (Get-Command $Uv -ErrorAction Stop).Source
$pythonIdentity = & $pythonPath -I -c 'import platform,sys; print(platform.python_version()+" "+platform.machine()+" "+str(sys.prefix == sys.base_prefix))'
if ($LASTEXITCODE -ne 0 -or $pythonIdentity -ne '3.12.13 AMD64 True') {
    throw "Pass a CPython 3.12.13 x64 base interpreter, not an existing venv: $pythonIdentity"
}
$uvIdentity = & $uvPath --version
if ($LASTEXITCODE -ne 0 -or $uvIdentity -notmatch '^uv 0\.11\.26\b') {
    throw 'Use uv 0.11.26 for this evaluated setup.'
}

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "UIA setup failed (exit $LASTEXITCODE): $Executable" }
}

# Separate environment: the capture, detector, and Jev runtimes are never modified.
$environment = Join-Path $projectRoot '.runtime/uia-venv'
$cache = Join-Path $projectRoot '.runtime/uia-uv-cache'
Invoke-Checked $pythonPath @('-I','-m','venv','--without-pip',$environment)
$environmentPython = Join-Path $environment 'Scripts/python.exe'
Invoke-Checked $uvPath @('--no-config','--cache-dir',$cache,'pip','sync',
    (Join-Path $projectRoot 'dependencies/uia-viewer-requirements.lock'),
    '--python',$environmentPython,'--require-hashes','--no-build','--default-index','https://pypi.org/simple')
Invoke-Checked $uvPath @('--no-config','--cache-dir',$cache,'pip','check','--python',$environmentPython)
$verify = @'
from pathlib import Path
import os
import sys
import comtypes.client
from PySide6 import QtCore, QtWidgets

cache = Path(comtypes.client.gen_dir).resolve()
if not cache.is_relative_to(Path(sys.prefix).resolve()):
    raise RuntimeError("UIA type library cache must remain inside this worktree's venv")
module = comtypes.client.GetModule(str(Path(os.environ["SystemRoot"]) / "System32" / "UIAutomationCore.dll"))
if not hasattr(module, "IUIAutomation"):
    raise RuntimeError("Windows UI Automation type library is unavailable")
print("UIA viewer imports OK; Qt " + QtCore.__version__ + "; comtypes " + comtypes.__version__)
'@
Invoke-Checked $environmentPython @('-I','-c',$verify)
Write-Output 'UIA viewer dependencies are ready. Run scripts/start-uia-viewer.ps1 in your interactive Windows desktop.'
