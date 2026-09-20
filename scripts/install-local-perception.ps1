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

# All environments and downloads are local; no PATH or machine configuration edits.
Invoke-Checked 'pwsh' @('-NoProfile','-File',(Join-Path $PSScriptRoot 'install-cua-driver.ps1'))
$cache = Join-Path $projectRoot '.runtime/uv-cache'
foreach ($name in @('winml','exporter')) {
    $environment = Join-Path $projectRoot ".runtime/$name-venv"
    Invoke-Checked $pythonPath @('-m','venv','--without-pip',$environment)
    $environmentPython = Join-Path $environment 'Scripts/python.exe'
    $requirementsName = if ($name -eq 'winml') { 'windowsml' } else { 'exporter' }
    $syncArguments = @('--no-config','--cache-dir',$cache,'pip','sync',
        (Join-Path $projectRoot "dependencies/$requirementsName-requirements.lock"),
        '--python',$environmentPython,'--require-hashes','--no-build','--default-index','https://pypi.org/simple')
    if ($name -eq 'exporter') {
        # CPU build suffixes are exact pins; every downloaded artifact must match the lock hash.
        $syncArguments += @('--index','https://download.pytorch.org/whl/cpu','--index-strategy','unsafe-best-match')
    }
    Invoke-Checked $uvPath $syncArguments
    Invoke-Checked $uvPath @('--no-config','--cache-dir',$cache,'pip','check','--python',$environmentPython)
}
$winmlPython = Join-Path $projectRoot '.runtime/winml-venv/Scripts/python.exe'
$exportPython = Join-Path $projectRoot '.runtime/exporter-venv/Scripts/python.exe'
Invoke-Checked $winmlPython @((Join-Path $PSScriptRoot 'windowsml_probe.py'),'--diagnostics-only',
    '--output',(Join-Path $projectRoot '.runtime/diagnostics/windowsml-bootstrap.json'))
Invoke-Checked $exportPython @((Join-Path $PSScriptRoot 'export-ui-detector.py'),'acquire')
Invoke-Checked $exportPython @((Join-Path $PSScriptRoot 'export-ui-detector.py'),'export')
Invoke-Checked $winmlPython @((Join-Path $PSScriptRoot 'windowsml_probe.py'),'--model',
    (Join-Path $projectRoot '.runtime/ui-detector/model.onnx'),
    '--output',(Join-Path $projectRoot '.runtime/diagnostics/windowsml-cpu.json'))
Write-Output 'Local perception dependencies are installed. Run scripts/test-local-perception.ps1 for the CUA-to-boxes comparison.'
