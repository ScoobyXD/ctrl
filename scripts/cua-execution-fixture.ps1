# Owned, synthetic WPF target for the CUA execution smoke check.
param(
    [Parameter(Mandatory)][string]$ReadyFile,
    [Parameter(Mandatory)][string]$StateFile,
    [Parameter(Mandatory)][string]$CloseFile,
    [ValidateRange(1, 600)][int]$LifetimeSeconds = 180
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
Add-Type -TypeDefinition @'
public class CuaFixtureDpi {
    [System.Runtime.InteropServices.DllImport("user32.dll")]
    public static extern bool SetProcessDpiAwarenessContext(System.IntPtr context);
}
'@
$null = [CuaFixtureDpi]::SetProcessDpiAwarenessContext([IntPtr]::new(-4))
$window = New-Object Windows.Window
$window.Name = 'cua_execution_fixture'
$window.Title = 'CTRL CUA Execution Fixture'
[Windows.Automation.AutomationProperties]::SetName($window, $window.Title)
[Windows.Automation.AutomationProperties]::SetAutomationId($window, $window.Name)
$window.Width = 730
$window.Height = 440
$window.Left = 120
$window.Top = 120
$window.FontFamily = 'Segoe UI'
$window.FontSize = 15
$window.ResizeMode = 'NoResize'
$window.ShowActivated = $false
$layout = New-Object Windows.Controls.Canvas
$window.Content = $layout

function Set-FixtureIdentity($Control, [string]$Id, [string]$Label) {
    $Control.Name = $Id
    [Windows.Automation.AutomationProperties]::SetName($Control, $Label)
    [Windows.Automation.AutomationProperties]::SetAutomationId($Control, $Id)
}
function New-FixtureControl {
    param([string]$Type, [string]$Id, [string]$Label,
        [int]$X, [int]$Y, [int]$Width, [int]$Height)
    $control = New-Object ('System.Windows.Controls.' + $Type)
    Set-FixtureIdentity $control $Id $Label
    if ($Type -eq 'TextBlock' -or $Type -eq 'TextBox') { $control.Text = $Label }
    elseif ($Type -eq 'Button') { $control.Content = $Label }
    $control.Width = $Width
    $control.Height = $Height
    [Windows.Controls.Canvas]::SetLeft($control, $X)
    [Windows.Controls.Canvas]::SetTop($control, $Y)
    return $control
}

$heading = New-FixtureControl 'TextBlock' 'fixture_heading' 'Local execution check: synthetic data only' 20 16 680 32
$null = $layout.Children.Add($heading)
$tabs = New-FixtureControl 'TabControl' 'fixture_tabs' 'Fixture tabs' 20 56 680 290
$homeTab = New-Object Windows.Controls.TabItem
Set-FixtureIdentity $homeTab 'fixture_home_tab' 'Home tab'
$homeTab.Header = 'Home tab'
$homePanel = New-Object Windows.Controls.Canvas
$homeTab.Content = $homePanel
$xTab = New-Object Windows.Controls.TabItem
Set-FixtureIdentity $xTab 'fixture_x_tab' 'X tab'
$xTab.Header = 'X tab'
$xPanel = New-Object Windows.Controls.Canvas
$xTab.Content = $xPanel
$null = $tabs.Items.Add($homeTab)
$null = $tabs.Items.Add($xTab)
$tabs.SelectedIndex = 0
$null = $layout.Children.Add($tabs)
$instructions = New-FixtureControl 'TextBlock' 'home_message' 'Select X tab, then open Bookmarks.' 24 28 560 32
$null = $homePanel.Children.Add($instructions)
$bookmarks = New-FixtureControl 'Button' 'fixture_bookmarks' 'Open Bookmarks' 24 22 220 42
$null = $xPanel.Children.Add($bookmarks)
$marker = New-FixtureControl 'TextBlock' 'fixture_bookmarks_state' 'Bookmarks opened: saved fixture page' 24 84 590 32
$marker.Visibility = 'Collapsed'
$null = $xPanel.Children.Add($marker)
$noteLabel = New-FixtureControl 'TextBlock' 'fixture_note_label' 'Synthetic note' 24 138 180 30
$null = $xPanel.Children.Add($noteLabel)
$note = New-FixtureControl 'TextBox' 'fixture_note' 'Fixture note' 210 132 400 34
$note.Text = 'Original note'
$null = $xPanel.Children.Add($note)
$footer = New-FixtureControl 'TextBlock' 'fixture_footer' 'No account, external site, clipboard, or persistent document.' 20 365 680 30
$null = $layout.Children.Add($footer)

$script:events = [Collections.Generic.List[object]]::new()
$script:tabSelections = 0
$script:bookmarkClicks = 0
$script:textChanges = 0
function Save-FixtureState {
    $state = [ordered]@{
        pid = $PID
        window_id = ([Windows.Interop.WindowInteropHelper]::new($window)).Handle.ToInt64()
        selected_tab = $tabs.SelectedItem.Header
        tab_selections = $script:tabSelections
        bookmark_clicks = $script:bookmarkClicks
        bookmarks_open = [bool]$marker.IsVisible
        note_text = $note.Text
        text_changes = $script:textChanges
        events = @($script:events.ToArray())
    }
    # Readers tolerate the brief write window; all contents are fixture-owned.
    [IO.File]::WriteAllText($StateFile, ($state | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
}
function Add-FixtureEvent([string]$Name) {
    $script:events.Add([ordered]@{ name = $Name; at_utc = [DateTime]::UtcNow.ToString('o') })
    Save-FixtureState
}
$tabs.Add_SelectionChanged({
    if ($_.OriginalSource -eq $tabs) {
        $script:tabSelections += 1
        Add-FixtureEvent ('tab:' + $tabs.SelectedItem.Header)
    }
})
$bookmarks.Add_Click({
    $script:bookmarkClicks += 1
    $marker.Visibility = 'Visible'
    Add-FixtureEvent 'bookmarks:click'
})
$note.Add_TextChanged({
    $script:textChanges += 1
    Add-FixtureEvent 'note:changed'
})
$window.Add_ContentRendered({
    Save-FixtureState
    $identity = [ordered]@{
        pid = $PID
        window_id = ([Windows.Interop.WindowInteropHelper]::new($window)).Handle.ToInt64()
        title = $window.Title
        ready_at_utc = [DateTime]::UtcNow.ToString('o')
    }
    [IO.File]::WriteAllText($ReadyFile, ($identity | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
})
$started = [Diagnostics.Stopwatch]::StartNew()
$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(100)
$timer.Add_Tick({
    if ([IO.File]::Exists($CloseFile) -or $started.Elapsed.TotalSeconds -ge $LifetimeSeconds) {
        $window.Close()
    }
})
$timer.Start()
$app = New-Object Windows.Application
try { $null = $app.Run($window) }
finally { $timer.Stop(); $started.Stop() }
