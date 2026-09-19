param(
    [switch]$VerifyOnly,
    [string]$PreviewPath,
    [string]$HybridRoot = 'C:\Users\Jonat\ctrl-experiments\tiptour-windows\windows-browser'
)
$ErrorActionPreference = 'Stop'
$ctrlChooserRoot = Split-Path -Parent $PSScriptRoot
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'ExperimentChooser.cs') -Raw) -ReferencedAssemblies System.Windows.Forms,System.Drawing
if ($VerifyOnly) {
    if (-not $PreviewPath) { $PreviewPath = Join-Path $ctrlChooserRoot '.ctrl\experiment-chooser-preview.png' }
    [CtrlExperimentChooserApp]::Verify($PreviewPath)
    Write-Output "Preview: $PreviewPath"
    exit 0
}
[CtrlExperimentChooserApp]::Run($ctrlChooserRoot, $HybridRoot)
