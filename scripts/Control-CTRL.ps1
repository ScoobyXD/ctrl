param([switch]$VerifyOnly, [string]$PreviewPath)
$ErrorActionPreference = 'Stop'
$ctrlRoot = Split-Path -Parent $PSScriptRoot
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, System.Net.Http, System.Web.Extensions
Add-Type -Path (Join-Path $PSScriptRoot 'ControlPanel.cs') -ReferencedAssemblies @(
    'System.Windows.Forms', 'System.Drawing', 'System.Net.Http', 'System.Web.Extensions'
)
if ($VerifyOnly) {
    if (-not $PreviewPath) { $PreviewPath = Join-Path $ctrlRoot '.ctrl\control-panel-preview.png' }
    [CtrlPanelApp]::Verify($ctrlRoot, $PreviewPath)
} else {
    [CtrlPanelApp]::Run($ctrlRoot)
}
