#Requires -Version 7.2
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'cua-driver-common.ps1')
$runDirectory = Join-Path $CuaProjectRoot ('.ctrl/perception/' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
[IO.Directory]::CreateDirectory($runDirectory) | Out-Null
$observationPath = Join-Path $runDirectory 'observation.json'
$closeFile = Join-Path $runDirectory 'close-fixture'
$totalTimer = [Diagnostics.Stopwatch]::StartNew()
$fixture = $null; $fixtureOutput = $null; $fixtureErrors = $null; $fixtureStarted = $false
$driver = $null; $driverErrors = $null; $driverStarted = $false
$script:DetectorRequestId = 0
$rpcLog = [Collections.Generic.List[object]]::new()
$observation = [ordered]@{
    schema_version = 1
    capture_id = [Guid]::NewGuid().ToString()
    passed = $false
    observation_path = $observationPath
    capture_started_at_utc = $null
    acquired_at_utc = $null
    driver = [ordered]@{ version = $CuaPin.version; revision = $CuaPin.revision; transport = 'MCP stdio --direct --no-overlay' }
    input_actions = 0
    accessibility_requested = $false
    timings_ms = [ordered]@{}
}

function Invoke-DetectorCaptureRpc {
    param([string]$Method, [hashtable]$Parameters)
    $script:DetectorRequestId++
    $requestId = $script:DetectorRequestId
    $request = @{ jsonrpc = '2.0'; id = $requestId; method = $Method; params = $Parameters }
    $rpcTimer = [Diagnostics.Stopwatch]::StartNew()
    $rpcEntry = [ordered]@{ request = $request; started_at_utc = [DateTime]::UtcNow.ToString('o'); completed = $false }
    $rpcLog.Add($rpcEntry)
    try {
        $driver.StandardInput.WriteLine(($request | ConvertTo-Json -Depth 12 -Compress))
        $driver.StandardInput.Flush()
        while ($rpcTimer.Elapsed.TotalSeconds -lt 30) {
            $lineTask = $driver.StandardOutput.ReadLineAsync()
            $remaining = [Math]::Max(1, 30000 - [int]$rpcTimer.Elapsed.TotalMilliseconds)
            if (-not $lineTask.Wait($remaining)) { throw "MCP timeout: $Method" }
            $line = $lineTask.GetAwaiter().GetResult()
            if ($null -eq $line) { throw "Driver closed stdout during $Method" }
            $response = $line | ConvertFrom-Json -Depth 100
            if ($response.id -eq $requestId) {
                if ($response.error) { throw ($response.error | ConvertTo-Json -Compress) }
                if ($response.result.isError) { throw ($response.result | ConvertTo-Json -Depth 20 -Compress) }
                $rpcEntry.completed = $true
                return $response.result
            }
        }
        throw "MCP timeout: $Method"
    } finally { $rpcEntry.elapsed_ms = [Math]::Round($rpcTimer.Elapsed.TotalMilliseconds, 3); $rpcTimer.Stop() }
}

try {
    $readyFile = Join-Path $runDirectory 'fixture.json'
    $fixtureHost = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
    $fixtureInfo = [Diagnostics.ProcessStartInfo]::new($fixtureHost)
    $fixtureInfo.UseShellExecute = $false
    $fixtureInfo.CreateNoWindow = $true
    $fixtureInfo.RedirectStandardOutput = $true
    $fixtureInfo.RedirectStandardError = $true
    foreach ($argument in @('-NoProfile','-NonInteractive','-STA','-ExecutionPolicy','Bypass','-File',
        (Join-Path $PSScriptRoot 'detector-smoke-window.ps1'), '-ReadyFile',$readyFile,'-CloseFile',$closeFile)) {
        $fixtureInfo.ArgumentList.Add($argument)
    }
    $fixtureTimer = [Diagnostics.Stopwatch]::StartNew()
    $fixture = [Diagnostics.Process]::new()
    $fixture.StartInfo = $fixtureInfo
    $null = $fixture.Start()
    $fixtureStarted = $true
    $fixtureOutput = $fixture.StandardOutput.ReadToEndAsync()
    $fixtureErrors = $fixture.StandardError.ReadToEndAsync()
    while (-not [IO.File]::Exists($readyFile)) {
        if ($fixture.HasExited -or $fixtureTimer.Elapsed.TotalSeconds -gt 15) { throw "Fixture failed to open; inspect $runDirectory" }
        Start-Sleep -Milliseconds 100
    }
    $observation.timings_ms.fixture_ready = [Math]::Round($fixtureTimer.Elapsed.TotalMilliseconds, 3)
    $fixtureTimer.Stop()
    $target = Get-Content -LiteralPath $readyFile -Raw | ConvertFrom-Json
    if ($target.pid -ne $fixture.Id -or $target.window_id -le 0) { throw 'Fixture identity did not match the process we started.' }
    $observation.target = [ordered]@{ pid = $target.pid; window_id = $target.window_id; title = $target.title }
    $observation.fixture_ground_truth = $target

    $driverTimer = [Diagnostics.Stopwatch]::StartNew()
    $driver = [Diagnostics.Process]::new()
    $driver.StartInfo = New-CuaProcessInfo -Arguments @('mcp','--direct','--no-overlay')
    $null = $driver.Start()
    $driverStarted = $true
    $driverErrors = $driver.StandardError.ReadToEndAsync()
    $initialized = Invoke-DetectorCaptureRpc 'initialize' @{
        protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = @{ name = 'ctrl-loop-detector-capture'; version = '1.0' }
    }
    $observation.driver.server = $initialized.serverInfo
    $observation.driver.protocol_version = $initialized.protocolVersion
    if ($initialized.serverInfo.version -ne $CuaPin.version -or $initialized.protocolVersion -ne '2025-06-18') {
        throw 'MCP version or negotiated protocol did not match the evaluated pin.'
    }
    $driver.StandardInput.WriteLine('{"jsonrpc":"2.0","method":"notifications/initialized"}')
    $driver.StandardInput.Flush()
    $tools = Invoke-DetectorCaptureRpc 'tools/list' @{}
    foreach ($required in @('list_windows','get_window_state')) {
        if ($required -notin $tools.tools.name) { throw "Missing native tool: $required" }
    }
    $observation.timings_ms.driver_initialize_and_tools = [Math]::Round($driverTimer.Elapsed.TotalMilliseconds, 3)
    $driverTimer.Stop()
    $windows = Invoke-DetectorCaptureRpc 'tools/call' @{ name = 'list_windows'; arguments = @{ pid = [int]$target.pid } }
    $windows | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath (Join-Path $runDirectory 'windows.json')
    $matchingWindows = @($windows.structuredContent.windows | Where-Object {
        $_.pid -eq $target.pid -and $_.window_id -eq $target.window_id -and $_.title -eq $target.title
    })
    if ($matchingWindows.Count -ne 1) { throw 'Driver did not enumerate the exact owned fixture.' }
    $observation.window_enumeration_metadata = $matchingWindows[0]

    $captureTimer = [Diagnostics.Stopwatch]::StartNew()
    $observation.capture_started_at_utc = [DateTime]::UtcNow.ToString('o')
    $capture = Invoke-DetectorCaptureRpc 'tools/call' @{ name = 'get_window_state'; arguments = @{
        pid = [int]$target.pid; window_id = [long]$target.window_id; include_accessibility_tree = $false; include_screenshot = $true
    } }
    $observation.acquired_at_utc = [DateTime]::UtcNow.ToString('o')
    $observation.acquisition_timestamp_meaning = 'Host UTC when the capture RPC returned; capture_started_at_utc bounds the acquisition interval.'
    $observation.timings_ms.capture_rpc = [Math]::Round($captureTimer.Elapsed.TotalMilliseconds, 3)
    $captureTimer.Stop()
    $metadata = $capture.structuredContent
    if ($metadata.pid -ne $target.pid -or $metadata.window_id -ne $target.window_id -or $metadata.window_title -ne $target.title) {
        throw 'Capture identity did not match the owned fixture.'
    }
    $observation.driver_capture_metadata = $metadata
    $images = @($capture.content | Where-Object type -eq 'image')
    if ($images.Count -ne 1 -or $images[0].mimeType -ne 'image/png') { throw 'Driver did not return exactly one PNG image.' }
    $screenshot = Join-Path $runDirectory 'fixture.png'
    [IO.File]::WriteAllBytes($screenshot, [Convert]::FromBase64String($images[0].data))
    $capture.content = @($capture.content | Where-Object type -ne 'image')
    $capture | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath (Join-Path $runDirectory 'capture.json')
    Add-Type -AssemblyName System.Drawing
    $bitmap = [Drawing.Bitmap]::new($screenshot)
    try {
        $magenta = 0; $cyan = 0; $black = 0; $samples = 0
        for ($y = 0; $y -lt $bitmap.Height; $y += 2) {
            for ($x = 0; $x -lt $bitmap.Width; $x += 2) {
                $color = $bitmap.GetPixel($x,$y)
                $samples++
                if ($color.R -lt 15 -and $color.G -lt 15 -and $color.B -lt 15) { $black++ }
                if ($color.R -gt 220 -and $color.G -lt 35 -and $color.B -gt 220) { $magenta++ }
                if ($color.R -lt 35 -and $color.G -gt 220 -and $color.B -gt 220) { $cyan++ }
            }
        }
        $observation.image = [ordered]@{
            path = $screenshot; mime_type = 'image/png'; width = $bitmap.Width; height = $bitmap.Height
            sha256 = (Get-FileHash -LiteralPath $screenshot -Algorithm SHA256).Hash.ToLowerInvariant()
            coordinate_space = 'original_screenshot_pixels'; origin = 'top_left'; x_direction = 'right'; y_direction = 'down'
            post_capture_resize = $false
        }
        $observation.capture_pixel_checks = [ordered]@{ magenta_samples = $magenta; cyan_samples = $cyan; black_fraction = $black / $samples }
        if ($bitmap.Width -ne $metadata.screenshot_width -or $bitmap.Height -ne $metadata.screenshot_height) {
            throw 'PNG dimensions differ from the driver screenshot metadata.'
        }
        if ($bitmap.Width -lt 900 -or $bitmap.Height -lt 560 -or $magenta -lt 80 -or $cyan -lt 80 -or $black / $samples -gt 0.10) {
            throw 'Screenshot is missing expected rendered fixture geometry or fiducials.'
        }
    } finally { $bitmap.Dispose() }
    $observation.image_to_window_transform = [ordered]@{
        status = 'unverified'; matrix = $null
        reason = 'Driver frame bounds, native frame/client geometry, and PNG size are retained independently. Their offsets/scales have not been calibrated; detector boxes stay in original screenshot pixels.'
    }
    $observation.passed = $true
} catch {
    $observation.error = $_.Exception.Message
    throw
} finally {
    # Keep cleanup independent: a broken driver pipe or log must not skip fixture cleanup.
    $cleanupErrors = [Collections.Generic.List[string]]::new()
    try {
        if ($driverStarted) {
            $observation.driver_stopped_gracefully = $false
            $driver.StandardInput.Close()
            $observation.driver_stopped_gracefully = $driver.WaitForExit(5000)
            if (-not $observation.driver_stopped_gracefully) {
                $driver.Kill($true)
                if (-not $driver.WaitForExit(5000)) { throw 'Driver did not exit after forced termination.' }
            }
            if ($driverErrors) {
                if (-not $driverErrors.Wait(5000)) { throw 'Driver stderr did not close after shutdown.' }
                $driverErrors.GetAwaiter().GetResult() | Set-Content -LiteralPath (Join-Path $runDirectory 'driver.stderr.log')
            }
            $observation.driver_exit_code = $driver.ExitCode
            if (-not $observation.driver_stopped_gracefully -or $driver.ExitCode -ne 0) {
                $cleanupErrors.Add('Driver did not stop cleanly on stdin EOF.')
            }
        }
    } catch {
        $cleanupErrors.Add('Driver cleanup: ' + $_.Exception.Message)
    } finally {
        if ($driver) {
            try {
                if ($driverStarted -and -not $driver.HasExited) {
                    $driver.Kill($true)
                    if (-not $driver.WaitForExit(5000)) { throw 'Driver is still running after cleanup retry.' }
                }
            } catch { $cleanupErrors.Add('Driver termination: ' + $_.Exception.Message) }
            $driver.Dispose()
        }
    }
    try {
        [IO.File]::WriteAllText($closeFile, 'close owned detector fixture')
        if ($fixtureStarted) {
            $observation.fixture_stopped_gracefully = $false
            $observation.fixture_stopped_gracefully = $fixture.WaitForExit(5000)
            if (-not $observation.fixture_stopped_gracefully) {
                $fixture.Kill()
                if (-not $fixture.WaitForExit(5000)) { throw 'Fixture did not exit after forced termination.' }
            }
            if ($fixtureOutput) {
                if (-not $fixtureOutput.Wait(5000)) { throw 'Fixture stdout did not close after shutdown.' }
                $fixtureOutput.GetAwaiter().GetResult() | Set-Content -LiteralPath (Join-Path $runDirectory 'fixture.stdout.log')
            }
            if ($fixtureErrors) {
                if (-not $fixtureErrors.Wait(5000)) { throw 'Fixture stderr did not close after shutdown.' }
                $fixtureErrors.GetAwaiter().GetResult() | Set-Content -LiteralPath (Join-Path $runDirectory 'fixture.stderr.log')
            }
            $observation.fixture_exit_code = $fixture.ExitCode
            if (-not $observation.fixture_stopped_gracefully -or $fixture.ExitCode -ne 0) {
                $cleanupErrors.Add('Fixture did not stop cleanly.')
            }
        }
    } catch {
        $cleanupErrors.Add('Fixture cleanup: ' + $_.Exception.Message)
    } finally {
        if ($fixture) {
            try {
                if ($fixtureStarted -and -not $fixture.HasExited) {
                    $fixture.Kill()
                    if (-not $fixture.WaitForExit(5000)) { throw 'Fixture is still running after cleanup retry.' }
                }
            } catch { $cleanupErrors.Add('Fixture termination: ' + $_.Exception.Message) }
            $fixture.Dispose()
        }
    }
    if ($cleanupErrors.Count -gt 0) {
        $observation.passed = $false
        $observation.cleanup_errors = $cleanupErrors.ToArray()
        if (-not $observation.error) { $observation.error = 'Process cleanup failed; see cleanup_errors.' }
    }
    $observation.timings_ms.capture_workflow_total = [Math]::Round($totalTimer.Elapsed.TotalMilliseconds, 3)
    $totalTimer.Stop()
    $rpcLog | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath (Join-Path $runDirectory 'requests.json')
    $observation | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath $observationPath
}
if (-not $observation.passed) { throw $observation.error }
$observation | ConvertTo-Json -Depth 50
