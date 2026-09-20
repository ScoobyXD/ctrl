# Windows PowerShell 5.1 / built-in WinForms. This owned fixture sends no input.
param(
    [string]$ReadyFile = '',
    [string]$CloseFile = '',
    [ValidateRange(0, 86400)][int]$LifetimeSeconds = 0
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing -TypeDefinition @'
using System.Windows.Forms;
public class UiaFixtureForm : Form {
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    public static extern bool SetProcessDpiAwarenessContext(System.IntPtr context);
    protected override bool ShowWithoutActivation { get { return true; } }
}
'@
$null = [UiaFixtureForm]::SetProcessDpiAwarenessContext([IntPtr]::new(-4))
[Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object UiaFixtureForm
$form.Name = 'uia_native_fixture'
$form.Text = 'CTRL UIA Native Fixture'
$form.AccessibleName = 'CTRL UIA Native Fixture'
$form.ClientSize = New-Object Drawing.Size(680,490)
$form.StartPosition = 'Manual'
$form.Location = New-Object Drawing.Point(100,100)
$form.Font = New-Object Drawing.Font('Segoe UI',11)
$form.BackColor = [Drawing.Color]::FromArgb(248,249,252)
$form.AutoScaleMode = 'None'
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false

function Add-FixtureControl {
    param([string]$Type, [string]$Id, [string]$Name, [string]$Text,
        [int]$X, [int]$Y, [int]$Width, [int]$Height)
    $control = New-Object ('System.Windows.Forms.' + $Type)
    $control.Name = $Id
    $control.AccessibleName = $Name
    $control.Text = $Text
    $control.Location = New-Object Drawing.Point($X,$Y)
    $control.Size = New-Object Drawing.Size($Width,$Height)
    $form.Controls.Add($control)
    return $control
}

$heading = Add-FixtureControl 'Label' 'heading' 'UIA observation fixture' 'UIA observation fixture' 24 18 620 38
$heading.Font = New-Object Drawing.Font('Segoe UI',18,[Drawing.FontStyle]::Bold)
$null = Add-FixtureControl 'Label' 'instructions' 'Observation leaves this data unchanged' 'Observation leaves this data unchanged. All data here is synthetic.' 24 62 632 34
$null = Add-FixtureControl 'Label' 'draft_label' 'Draft label' 'Draft' 24 120 146 28
$draft = Add-FixtureControl 'TextBox' 'draft' 'Draft text' 'Example draft' 186 116 460 32
$null = Add-FixtureControl 'Label' 'theme_label' 'Theme label' 'Theme' 24 172 146 28
$theme = Add-FixtureControl 'ComboBox' 'theme' 'Theme choice' '' 186 168 460 32
$theme.DropDownStyle = 'DropDownList'
$null = $theme.Items.AddRange([object[]]@('Light','Dark','System'))
$theme.SelectedIndex = 0
$notifications = Add-FixtureControl 'CheckBox' 'notifications' 'Enable notifications' 'Enable notifications' 186 222 460 32
$notifications.Checked = $true
$null = Add-FixtureControl 'Label' 'password_label' 'Password label' 'Synthetic password' 24 279 156 28
$password = Add-FixtureControl 'TextBox' 'password' 'Fixture password' '' 186 275 460 32
$password.UseSystemPasswordChar = $true
# A reader must redact this value even though it is synthetic test data.
$password.Text = 'native-secret-sentinel'
$increment = Add-FixtureControl 'Button' 'increment' 'Increment counter' 'Increment counter' 186 333 216 42
$disabled = Add-FixtureControl 'Button' 'disabled' 'Disabled action' 'Disabled action' 426 333 220 42
$disabled.Enabled = $false
$script:activationCount = 0
$status = Add-FixtureControl 'TextBox' 'status' 'Fixture action count' 'Activations: 0' 186 397 460 34
$status.ReadOnly = $true
$increment.Add_Click({
    $script:activationCount += 1
    $status.Text = 'Activations: ' + $script:activationCount
})
$footer = Add-FixtureControl 'Label' 'footer' 'Fixture footer' 'Close this window when finished. No automation actions are required.' 24 446 632 26

$form.Add_Shown({
    if ($ReadyFile) {
        # Identity only: do not put control text or password values in this file.
        $identity = [ordered]@{
            pid = $PID
            window_id = $form.Handle.ToInt64()
            title = $form.Text
            ready_at_utc = [DateTime]::UtcNow.ToString('o')
        }
        [IO.File]::WriteAllText($ReadyFile, ($identity | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    }
})
$started = [Diagnostics.Stopwatch]::StartNew()
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 250
$timer.Add_Tick({
    if (($CloseFile -and [IO.File]::Exists($CloseFile)) -or
        ($LifetimeSeconds -gt 0 -and $started.Elapsed.TotalSeconds -ge $LifetimeSeconds)) {
        $form.Close()
    }
})
$timer.Start()
try { [Windows.Forms.Application]::Run($form) }
finally { $timer.Dispose(); $form.Dispose(); $started.Stop() }
