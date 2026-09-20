#Requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Python,
    [string]$Uv = 'uv',
    # Explicit opt-in: read verified artifacts from another checkout, never its venv/code.
    [string]$ReuseRuntimeFrom
)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or [Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
    throw 'The capture viewer requires Windows x64.'
}
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = (Get-Command $Python -ErrorAction Stop).Source
$uvPath = (Get-Command $Uv -ErrorAction Stop).Source
$pythonIdentity = & $pythonPath -I -c 'import platform,sys; print(platform.python_version()+" "+platform.machine()+" "+str(sys.prefix == sys.base_prefix))'
if ($LASTEXITCODE -ne 0 -or $pythonIdentity -ne '3.12.13 AMD64 True') {
    throw "Pass a CPython 3.12.13 x64 base interpreter, not another application's venv: $pythonIdentity"
}
$uvIdentity = & $uvPath --version
if ($LASTEXITCODE -ne 0 -or $uvIdentity -notmatch '^uv 0\.11\.26\b') {
    throw 'Use uv 0.11.26 for this evaluated setup.'
}
function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Setup failed (exit $LASTEXITCODE): $Executable $($Arguments -join ' ')" }
}
function Assert-Hash {
    param([string]$Path, [string]$Expected)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf) -or
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Expected) {
        throw "Missing file or SHA256 mismatch: $Path"
    }
}
function Copy-Verified {
    param([string]$Source, [string]$Destination, [string]$Expected)
    Assert-Hash $Source $Expected
    if (Test-Path -LiteralPath $Destination) {
        Assert-Hash $Destination $Expected
    } else {
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Destination)) | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Destination
        Assert-Hash $Destination $Expected
    }
}
$detectorLockPath = Join-Path $projectRoot 'dependencies/ui-detector.lock.json'
$detectorPin = Get-Content -LiteralPath $detectorLockPath -Raw | ConvertFrom-Json
$detectorLockHash = (Get-FileHash -LiteralPath $detectorLockPath -Algorithm SHA256).Hash
$licensePin = $detectorPin.artifacts | Where-Object role -eq 'license'
$checkpointPin = $detectorPin.artifacts | Where-Object role -eq 'checkpoint'
function Read-VerifiedModel {
    param([string]$Directory)
    $manifest = Get-Content -LiteralPath (Join-Path $Directory 'export-manifest.json') -Raw | ConvertFrom-Json
    if ($manifest.lockfileSha256 -ne $detectorLockHash -or
        $manifest.modelId -ne $detectorPin.id -or
        $manifest.checkpointSha256 -ne $checkpointPin.sha256 -or
        $manifest.source.revision -ne $detectorPin.source.revision) {
        throw 'Detector export provenance does not match this checkout; rerun the pinned exporter.'
    }
    $model = Join-Path $Directory 'model.onnx'
    Assert-Hash $model $manifest.onnx.sha256
    if ((Get-Item -LiteralPath $model).Length -ne $manifest.onnx.sizeBytes) {
        throw 'Detector ONNX size differs from its export manifest.'
    }
    Assert-Hash (Join-Path $Directory 'LICENSE') $licensePin.sha256
    return $manifest
}
$runtime = Join-Path $projectRoot '.runtime'
$modelDirectory = Join-Path $runtime 'ui-detector'
if ($ReuseRuntimeFrom) {
    $sourceRoot = (Resolve-Path -LiteralPath $ReuseRuntimeFrom).Path
    $sourceRuntime = Join-Path $sourceRoot '.runtime'
    $sourceModel = Join-Path $sourceRuntime 'ui-detector'
    $manifest = Read-VerifiedModel $sourceModel
    $cuaPin = Get-Content -LiteralPath (Join-Path $projectRoot 'dependencies/cua-driver.lock.json') -Raw | ConvertFrom-Json
    $archiveRelative = 'cua-driver/downloads/' + $cuaPin.asset
    Copy-Verified (Join-Path $sourceRuntime $archiveRelative) (Join-Path $runtime $archiveRelative) $cuaPin.sha256
    Copy-Verified (Join-Path $sourceModel 'model.onnx') (Join-Path $modelDirectory 'model.onnx') $manifest.onnx.sha256
    Copy-Verified (Join-Path $sourceModel 'LICENSE') (Join-Path $modelDirectory 'LICENSE') $licensePin.sha256
    $sourceManifest = Join-Path $sourceModel 'export-manifest.json'
    Copy-Verified $sourceManifest (Join-Path $modelDirectory 'export-manifest.json') (Get-FileHash -LiteralPath $sourceManifest -Algorithm SHA256).Hash
    Write-Output "Reused verified archive/model/license only from: $sourceRoot"
    Write-Output 'The export manifest retains the original export path as provenance; inference uses this checkout.'
}
if (-not (Test-Path -LiteralPath (Join-Path $modelDirectory 'export-manifest.json'))) {
    # The existing pinned acquisition/export route also provisions its own tooling.
    # Neither exporter nor reference PyTorch inference runs during normal app use.
    Invoke-Checked 'pwsh' @('-NoProfile','-File',(Join-Path $PSScriptRoot 'install-local-perception.ps1'),'-Python',$pythonPath,'-Uv',$uvPath)
} else {
    Invoke-Checked 'pwsh' @('-NoProfile','-File',(Join-Path $PSScriptRoot 'install-cua-driver.ps1'))
}
$null = Read-VerifiedModel $modelDirectory
$environment = Join-Path $runtime 'viewer-venv'
Invoke-Checked $pythonPath @('-I','-m','venv','--without-pip',$environment)
$environmentPython = Join-Path $environment 'Scripts/python.exe'
$cache = Join-Path $runtime 'uv-cache'
Invoke-Checked $uvPath @('--no-config','--cache-dir',$cache,'pip','sync',
    (Join-Path $projectRoot 'dependencies/capture-viewer-requirements.lock'),
    '--python',$environmentPython,'--require-hashes','--no-build','--default-index','https://pypi.org/simple')
Invoke-Checked $uvPath @('--no-config','--cache-dir',$cache,'pip','check','--python',$environmentPython)
Invoke-Checked $environmentPython @('-I','-c','from PySide6 import QtCore,QtWidgets; from mcp import ClientSession,StdioServerParameters; from mcp.client.stdio import stdio_client; import cv2,numpy,PIL; print("Viewer imports OK; Qt",QtCore.__version__)')
Invoke-Checked $environmentPython @('-I',(Join-Path $PSScriptRoot 'windowsml_probe.py'),
    '--model',(Join-Path $modelDirectory 'model.onnx'),
    '--output',(Join-Path $runtime 'diagnostics/viewer-windowsml.json'))
Write-Output 'Capture viewer dependencies are ready. Run scripts/start-capture-viewer.ps1 in your interactive Windows desktop.'
