# Windows PowerShell 5.1 / built-in .NET WinForms fixture. No input is synthesized.
param([Parameter(Mandatory)][string]$ReadyFile, [Parameter(Mandatory)][string]$CloseFile)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing -TypeDefinition @'
using System.Windows.Forms;
public class CuaSmokeForm : Form {
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    public static extern bool SetProcessDpiAwarenessContext(System.IntPtr context);
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams {
        get { var p = base.CreateParams; p.ExStyle |= 0x08000000; return p; }
    }
}
'@
$dpiAware = [CuaSmokeForm]::SetProcessDpiAwarenessContext([IntPtr]::new(-4))
if (-not $dpiAware) { throw 'Could not enable PerMonitorV2 DPI awareness for the fixture.' }
$form = New-Object CuaSmokeForm
$form.Text = 'CTRL Loop - CUA read-only smoke fixture'
$form.ClientSize = New-Object Drawing.Size(640,360)
$form.StartPosition = 'Manual'
$form.Location = New-Object Drawing.Point(80,80)
$form.BackColor = [Drawing.Color]::White
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false
$label = New-Object Windows.Forms.Label
$label.Text = 'CUA Driver 0.28.2 - read-only capture'
$label.Location = New-Object Drawing.Point(24,24)
$label.Size = New-Object Drawing.Size(592,48)
$label.Font = New-Object Drawing.Font('Segoe UI',12)
$form.Controls.Add($label)
foreach ($spec in @(@(24,100,'Magenta'), @(344,100,'Cyan'))) {
    $panel = New-Object Windows.Forms.Panel
    $panel.Location = New-Object Drawing.Point($spec[0],$spec[1])
    $panel.Size = New-Object Drawing.Size(272,210)
    $panel.BackColor = [Drawing.Color]::FromName($spec[2])
    $form.Controls.Add($panel)
}
$form.Add_Shown({
    @{ pid = $PID; window_id = $form.Handle.ToInt64(); title = $form.Text; dpiAwareness = 'PerMonitorV2' } |
        ConvertTo-Json | Set-Content -LiteralPath $ReadyFile -Encoding UTF8
})
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 250
$expires = [DateTime]::UtcNow.AddSeconds(90)
$timer.Add_Tick({
    if ([IO.File]::Exists($CloseFile) -or [DateTime]::UtcNow -gt $expires) { $form.Close() }
})
$timer.Start()
try { [Windows.Forms.Application]::Run($form) }
finally { $timer.Dispose(); $form.Dispose() }
