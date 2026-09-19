param([switch]$VerifyOnly, [string]$PreviewPath)
$ErrorActionPreference = 'Stop'
$ctrlRoot = Split-Path -Parent $PSScriptRoot
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, System.Net.Http, System.Web.Extensions
Add-Type -Path (Join-Path $PSScriptRoot 'DebugConsole.cs') -ReferencedAssemblies @(
    'System.Windows.Forms', 'System.Drawing', 'System.Net.Http', 'System.Web.Extensions'
)
if ($VerifyOnly) {
    if (-not $PreviewPath) { $PreviewPath = Join-Path $ctrlRoot '.ctrl\debug-console-preview.png' }
    [CtrlDebugApp]::Verify($PreviewPath)
} else {
    [CtrlDebugApp]::Run($ctrlRoot)
}
