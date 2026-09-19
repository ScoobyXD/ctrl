param([switch]$VerifyOnly, [string]$PreviewPath, [string]$StopGeneration)
$ErrorActionPreference = 'Stop'
$ctrlRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'Ctrl-Process.ps1')
if (-not (Test-CtrlLaunchGeneration $ctrlRoot $StopGeneration)) { return }
try {
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, System.Net.Http, System.Web.Extensions
Add-Type -Path (Join-Path $PSScriptRoot 'ControlPanel.cs') -ReferencedAssemblies @(
    'System.Windows.Forms', 'System.Drawing', 'System.Net.Http', 'System.Web.Extensions'
)
if ($VerifyOnly) {
    if (-not $PreviewPath) { $PreviewPath = Join-Path $ctrlRoot '.ctrl\control-panel-preview.png' }
    [CtrlPanelApp]::Verify($ctrlRoot, $PreviewPath)
} else {
    if (-not (Test-CtrlLaunchGeneration $ctrlRoot $StopGeneration)) { return }
    [CtrlPanelApp]::Run($ctrlRoot)
}

} catch {
    [System.IO.Directory]::CreateDirectory((Join-Path $ctrlRoot '.ctrl')) | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $ctrlRoot '.ctrl\native-launch-error.log'), ($_ | Out-String))
    throw
}
