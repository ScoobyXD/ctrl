#Requires -Version 7.2
[CmdletBinding()]
param([string]$Chrome = '')
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'The UIA demo requires Windows.' }
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
# Start-Process resolves WorkingDirectory as a PowerShell wildcard path.
$workingDirectory = [WildcardPattern]::Escape($projectRoot)
$python = Join-Path $projectRoot '.runtime/uia-venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Run scripts/install-uia-viewer.ps1 -Python <CPython 3.12.13 x64 base/python.exe> first.'
}
$observer = Join-Path $PSScriptRoot 'uia-observe.py'
$stateDirectory = Join-Path $projectRoot '.ctrl/uia'
[IO.Directory]::CreateDirectory($stateDirectory) | Out-Null
$nativeReady = Join-Path $stateDirectory 'demo-native-ready.json'
$browserReady = Join-Path $stateDirectory 'demo-browser-ready.json'

function Get-DemoWindows {
    # Keep window titles in memory only. Never persist or print a desktop inventory.
    $response = & $python -I $observer windows 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Could not enumerate windows for the demo. Check the UIA installation.' }
    $parsed = $response | ConvertFrom-Json
    if ($parsed.error) { throw 'Could not enumerate windows for the demo. Check the interactive desktop.' }
    return @($parsed.windows)
}

$windows = @(Get-DemoWindows)
$nativeTitle = 'CTRL UIA Native Fixture'
$browserTitle = 'CTRL UIA Browser Fixture'
$nativeWindow = $windows | Where-Object title -eq $nativeTitle | Select-Object -First 1
$browserWindow = $windows | Where-Object { $_.title -like ($browserTitle + '*') } | Select-Object -First 1
$viewerWindow = $windows | Where-Object { $_.title -like 'CTRL * UIA control options' } | Select-Object -First 1

# Resolve Chrome before launching anything, so a missing browser gives a useful error.
if (-not $browserWindow) {
    if ($Chrome) {
        $chromePath = (Get-Command $Chrome -ErrorAction Stop).Source
    } else {
        $chromePath = @(
            (Join-Path $env:ProgramFiles 'Google/Chrome/Application/chrome.exe'),
            (Join-Path ${env:ProgramFiles(x86)} 'Google/Chrome/Application/chrome.exe'),
            (Join-Path $env:LOCALAPPDATA 'Google/Chrome/Application/chrome.exe')
        ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    }
    if (-not $chromePath) { throw 'Chrome was not found. Pass -Chrome <absolute path to chrome.exe>.' }
}

if (-not $nativeWindow) {
    $nativeScript = Join-Path $PSScriptRoot 'uia-native-fixture.ps1'
    $nativeProcess = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile','-STA','-File',('"' + $nativeScript + '"'),'-ReadyFile',('"' + $nativeReady + '"')) `
        -WorkingDirectory $workingDirectory -WindowStyle Hidden -PassThru
    Write-Output "Native fixture started (PID $($nativeProcess.Id))."
} else {
    Write-Output 'The native fixture is already open.'
}

if (-not $browserWindow) {
    # This profile belongs only to this worktree. Never alter the normal Chrome profile.
    $profile = Join-Path $projectRoot '.runtime/uia-browser-profile'
    [IO.Directory]::CreateDirectory($profile) | Out-Null
    $fixturePath = Join-Path $projectRoot 'tests/fixtures/uia-controls.html'
    $fixtureUri = [Uri]::new($fixturePath).AbsoluteUri
    $null = Start-Process -FilePath $chromePath `
        -ArgumentList @(
            ('--user-data-dir="' + $profile + '"'), '--no-first-run', '--no-default-browser-check',
            '--disable-sync', '--new-window', ('"' + $fixtureUri + '"')
        ) -WorkingDirectory $workingDirectory -WindowStyle Hidden -PassThru
} else {
    Write-Output 'The browser fixture is already open.'
}

# Refresh both identities even when their fixture windows were already open.
# Chrome can transfer the launch to an existing dedicated-profile process, so use
# the resulting fixture window rather than assuming the launched PID owns it.
$poll = [Diagnostics.Stopwatch]::StartNew()
do {
    $windows = @(Get-DemoWindows)
    $nativeWindow = $windows | Where-Object {
        $_.title -eq $nativeTitle -and (-not $nativeProcess -or $_.pid -eq $nativeProcess.Id)
    } | Select-Object -First 1
    $browserWindow = $windows | Where-Object {
        $_.title -like ($browserTitle + '*')
    } | Select-Object -First 1
    if ($nativeWindow -and $browserWindow) { break }
    Start-Sleep -Milliseconds 400
} while ($poll.Elapsed.TotalSeconds -lt 12)
$poll.Stop()

foreach ($fixture in @(
    @{ Window = $nativeWindow; Path = $nativeReady; Title = $nativeTitle },
    @{ Window = $browserWindow; Path = $browserReady; Title = $browserTitle }
)) {
    if ($fixture.Window) {
        $identity = [ordered]@{
            pid = $fixture.Window.pid
            window_id = $fixture.Window.hwnd
            title = $fixture.Title
        }
        [IO.File]::WriteAllText($fixture.Path,
            ($identity | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
        Write-Output ($fixture.Title + ' is ready.')
    } else {
        # Do not leave a stale fixture identity that a later verification could reuse.
        if (Test-Path -LiteralPath $fixture.Path -PathType Leaf) {
            Remove-Item -LiteralPath $fixture.Path
        }
        Write-Warning ($fixture.Title + ' was not found within the readiness budget.')
    }
}

if (-not $viewerWindow) {
    & (Join-Path $PSScriptRoot 'start-uia-viewer.ps1')
} else {
    Write-Output 'The UIA control-options viewer is already open.'
}
Write-Output 'In the viewer, press Refresh windows, select CTRL UIA Native Fixture or CTRL UIA Browser Fixture, then Observe / Refresh.'
Write-Output 'The demo does not invoke controls. Fixture counters should remain Activations: 0. Close the demo windows when finished.'
