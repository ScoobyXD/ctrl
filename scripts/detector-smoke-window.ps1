# Windows PowerShell 5.1 / built-in .NET WinForms. The fixture accepts no commands or input.
param([Parameter(Mandatory)][string]$ReadyFile, [Parameter(Mandatory)][string]$CloseFile)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;
public class DetectorSmokeForm : Form {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")]
    public static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
    [DllImport("user32.dll")]
    public static extern uint GetDpiForWindow(IntPtr hwnd);
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams {
        get { var p = base.CreateParams; p.ExStyle |= 0x08000000; return p; }
    }
}
'@
if (-not [DetectorSmokeForm]::SetProcessDpiAwarenessContext([IntPtr]::new(-4))) {
    throw 'Could not enable PerMonitorV2 DPI awareness for the fixture.'
}
[Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object DetectorSmokeForm
$form.Name = 'detector_fixture'
$form.Text = 'CTRL Loop - Local UI detector fixture'
$form.AutoScaleMode = 'None'
$form.ClientSize = New-Object Drawing.Size(920,580)
$form.StartPosition = 'Manual'
$form.Location = New-Object Drawing.Point(80,80)
$form.BackColor = [Drawing.Color]::FromArgb(248,249,252)
$form.Font = New-Object Drawing.Font('Segoe UI',11)
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$script:fixtureControls = New-Object 'System.Collections.Generic.List[System.Windows.Forms.Control]'

function Add-FixtureControl {
    param([string]$Type, [string]$Name, [string]$Text, [int]$X, [int]$Y, [int]$Width, [int]$Height)
    $control = New-Object ('System.Windows.Forms.' + $Type)
    $control.Name = $Name
    $control.Text = $Text
    $control.Location = New-Object Drawing.Point($X,$Y)
    $control.Size = New-Object Drawing.Size($Width,$Height)
    $control.TabStop = $false
    $form.Controls.Add($control)
    $script:fixtureControls.Add($control)
    return $control
}

$heading = Add-FixtureControl 'Label' 'heading' 'Project settings' 28 24 760 40
$heading.Font = New-Object Drawing.Font('Segoe UI',20,[Drawing.FontStyle]::Bold)
$null = Add-FixtureControl 'Label' 'subtitle' 'Local capture and detection test - no actions are performed.' 30 70 840 30
$null = Add-FixtureControl 'Label' 'name_label' 'Project name' 32 124 140 28
$name = Add-FixtureControl 'TextBox' 'project_name' 'Example project' 194 120 444 34
$browse = Add-FixtureControl 'Button' 'browse' 'Browse...' 666 117 204 40
$null = Add-FixtureControl 'Label' 'theme_label' 'Color theme' 32 184 140 28
$theme = Add-FixtureControl 'ComboBox' 'theme' '' 194 180 444 36
$theme.DropDownStyle = 'DropDownList'
$null = $theme.Items.AddRange([object[]]@('Light','Dark','System'))
$theme.SelectedIndex = 0
$preview = Add-FixtureControl 'Button' 'preview' 'Preview' 666 177 204 40
$autosave = Add-FixtureControl 'CheckBox' 'autosave' 'Save changes automatically' 194 242 444 34
$autosave.Checked = $true
$notifications = Add-FixtureControl 'CheckBox' 'notifications' 'Show desktop notifications' 194 287 444 34
$null = Add-FixtureControl 'Label' 'mode_label' 'Workspace' 32 351 154 28
$personal = Add-FixtureControl 'RadioButton' 'personal_mode' 'Personal' 194 346 190 34
$personal.Checked = $true
$shared = Add-FixtureControl 'RadioButton' 'shared_mode' 'Shared' 412 346 190 34
$null = Add-FixtureControl 'Label' 'status_label' 'Status: Ready for read-only observation' 32 425 828 35
$save = Add-FixtureControl 'Button' 'save' 'Save settings' 194 493 204 46
$reset = Add-FixtureControl 'Button' 'reset' 'Reset defaults' 424 493 204 46
$cancel = Add-FixtureControl 'Button' 'cancel' 'Cancel' 666 493 204 46
# Pixel fiducials check that the screenshot really contains this rendered fixture.
foreach ($spec in @(@('fiducial_magenta',822,'Magenta'), @('fiducial_cyan',864,'Cyan'))) {
    $marker = Add-FixtureControl 'Panel' $spec[0] '' $spec[1] 28 24 24
    $marker.BackColor = [Drawing.Color]::FromName($spec[2])
}

function Convert-Rectangle {
    param([Drawing.Rectangle]$Rectangle)
    return [ordered]@{ x = $Rectangle.X; y = $Rectangle.Y; width = $Rectangle.Width; height = $Rectangle.Height }
}

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 100
$script:fixtureShown = $false
$script:fixtureReady = $false
$started = [Diagnostics.Stopwatch]::StartNew()
$form.Add_Shown({ $script:fixtureShown = $true })
$timer.Add_Tick({
    if ([IO.File]::Exists($CloseFile) -or $started.Elapsed.TotalSeconds -gt 90) {
        $form.Close()
        return
    }
    if ($script:fixtureShown -and -not $script:fixtureReady -and $started.Elapsed.TotalMilliseconds -ge 500) {
        $form.Refresh()
        $nativeFrame = New-Object 'DetectorSmokeForm+RECT'
        if (-not [DetectorSmokeForm]::GetWindowRect($form.Handle, [ref]$nativeFrame)) { throw 'GetWindowRect failed.' }
        $controls = @($script:fixtureControls | ForEach-Object {
            [ordered]@{
                id = $_.Name
                native_type = $_.GetType().Name
                fixture_text = $_.Text
                enabled = $_.Enabled
                visible = $_.Visible
                bounds_client_pixels = Convert-Rectangle $_.Bounds
                bounds_screen_pixels = Convert-Rectangle ($form.RectangleToScreen($_.Bounds))
                client_area_bounds_screen_pixels = Convert-Rectangle ($_.RectangleToScreen($_.ClientRectangle))
                geometry_note = 'Bounds include the control frame; client_area_bounds_screen_pixels excludes its frame. No native bounds are in screenshot coordinates.'
            }
        })
        $identity = [ordered]@{
            pid = $PID
            window_id = $form.Handle.ToInt64()
            title = $form.Text
            dpi_awareness = 'PerMonitorV2'
            dpi = [DetectorSmokeForm]::GetDpiForWindow($form.Handle)
            autoscale_mode = 'None'
            native_frame_bounds_screen_pixels = [ordered]@{
                x = $nativeFrame.Left; y = $nativeFrame.Top
                width = $nativeFrame.Right - $nativeFrame.Left; height = $nativeFrame.Bottom - $nativeFrame.Top
            }
            client_bounds_screen_pixels = Convert-Rectangle ($form.RectangleToScreen($form.ClientRectangle))
            client_size_pixels = [ordered]@{ width = $form.ClientSize.Width; height = $form.ClientSize.Height }
            controls = $controls
            ground_truth_note = 'These native fixture bounds are layout evidence, not labels inferred by the detector and not validated action coordinates.'
            ready_at_utc = [DateTime]::UtcNow.ToString('o')
        }
        $temporaryReady = $ReadyFile + '.tmp'
        [IO.File]::WriteAllText($temporaryReady, ($identity | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
        [IO.File]::Move($temporaryReady, $ReadyFile)
        $script:fixtureReady = $true
    }
})
$timer.Start()
try { [Windows.Forms.Application]::Run($form) }
finally { $timer.Dispose(); $form.Dispose(); $started.Stop() }
