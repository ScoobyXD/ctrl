param([switch]$VerifyOnly, [string]$PreviewPath, [string]$StopGeneration)
$ErrorActionPreference = 'Stop'
$ctrlRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'Ctrl-Process.ps1')
if (-not (Test-CtrlLaunchGeneration $ctrlRoot $StopGeneration)) { return }
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, System.Net.Http, System.Web.Extensions
Add-Type -Path (Join-Path $PSScriptRoot 'DebugConsole.cs') -ReferencedAssemblies @(
    'System.Windows.Forms', 'System.Drawing', 'System.Net.Http', 'System.Web.Extensions'
)
if ($VerifyOnly) {
    if (-not $PreviewPath) { $PreviewPath = Join-Path $ctrlRoot '.ctrl\debug-console-preview.png' }
    [CtrlDebugApp]::Verify($PreviewPath)
} else {
    if (-not (Test-CtrlLaunchGeneration $ctrlRoot $StopGeneration)) { return }
    [CtrlDebugApp]::Run($ctrlRoot)
}
