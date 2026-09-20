#Requires -Version 7.2
[CmdletBinding()]
param()
. (Join-Path $PSScriptRoot 'cua-driver-common.ps1')
$runDirectory = Join-Path $CuaRuntime ('smoke/' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))
[IO.Directory]::CreateDirectory($runDirectory) | Out-Null
$fixture = $null
$fixtureOutput = $null
$fixtureErrors = $null
$fixtureStarted = $false
$driver = $null
$driverStarted = $false
$driverErrors = $null
$closeFile = Join-Path $runDirectory 'close-fixture'
$report = [ordered]@{ passed = $false; version = $CuaPin.version; revision = $CuaPin.revision; transport = 'MCP stdio --direct'; inputActions = 0 }
$script:CuaRequestId = 0

function Invoke-SmokeRpc {
    param([string]$Method, [hashtable]$Parameters)
    $script:CuaRequestId++
    $requestId = $script:CuaRequestId
    $request = @{ jsonrpc = '2.0'; id = $requestId; method = $Method; params = $Parameters }
    $driver.StandardInput.WriteLine(($request | ConvertTo-Json -Depth 12 -Compress))
    $driver.StandardInput.Flush()
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ([DateTime]::UtcNow -lt $deadline) {
        $lineTask = $driver.StandardOutput.ReadLineAsync()
        $remaining = [Math]::Max(1, [int]($deadline - [DateTime]::UtcNow).TotalMilliseconds)
        if (-not $lineTask.Wait($remaining)) { throw "MCP timeout: $Method" }
        $line = $lineTask.GetAwaiter().GetResult()
        if ($null -eq $line) { throw "Driver closed stdout during $Method" }
        $response = $line | ConvertFrom-Json -Depth 100
        if ($response.id -eq $requestId) {
            if ($response.error) { throw ($response.error | ConvertTo-Json -Compress) }
            if ($response.result.isError) { throw ($response.result | ConvertTo-Json -Depth 20 -Compress) }
            return $response.result
        }
    }
    throw "MCP timeout: $Method"
}

try {
    $readyFile = Join-Path $runDirectory 'fixture.json'
    $fixtureHost = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
    $fixtureScript = Join-Path $PSScriptRoot 'cua-smoke-window.ps1'
    $fixtureInfo = [Diagnostics.ProcessStartInfo]::new($fixtureHost)
    $fixtureInfo.UseShellExecute = $false
    $fixtureInfo.CreateNoWindow = $true
    $fixtureInfo.RedirectStandardOutput = $true
    $fixtureInfo.RedirectStandardError = $true
    foreach ($argument in @(
        '-NoProfile', '-NonInteractive', '-STA', '-ExecutionPolicy', 'Bypass', '-File',
        $fixtureScript, '-ReadyFile', $readyFile, '-CloseFile', $closeFile
    )) { $fixtureInfo.ArgumentList.Add($argument) }
    $fixture = [Diagnostics.Process]::new()
    $fixture.StartInfo = $fixtureInfo
    $null = $fixture.Start()
    $fixtureStarted = $true
    $fixtureOutput = $fixture.StandardOutput.ReadToEndAsync()
    $fixtureErrors = $fixture.StandardError.ReadToEndAsync()
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (-not [IO.File]::Exists($readyFile)) {
        if ($fixture.HasExited -or [DateTime]::UtcNow -gt $deadline) { throw "Fixture failed to open; inspect $runDirectory" }
        Start-Sleep -Milliseconds 100
    }
    $target = Get-Content -LiteralPath $readyFile -Raw | ConvertFrom-Json
    if ($target.pid -ne $fixture.Id) { throw 'Fixture PID did not match the process we started.' }
    $report.target = $target
    $version = (Invoke-CuaCommand -Arguments @('--version')).Trim()
    if ($version -ne "cua-driver $($CuaPin.version)") { throw "Version mismatch: $version" }
    $telemetry = Invoke-CuaCommand -Arguments @('telemetry','status','--json') | ConvertFrom-Json
    if ($telemetry.enabled) { throw 'Telemetry was not disabled.' }
    $report.telemetry = $telemetry
    $doctorText = Invoke-CuaCommand -Arguments @('doctor','--json')
    $doctorText | Set-Content -LiteralPath (Join-Path $runDirectory 'doctor.json')
    $report.doctor = $doctorText | ConvertFrom-Json
    if (-not $report.doctor.ok) { throw 'Doctor reported an error; inspect doctor.json.' }

    $driver = [Diagnostics.Process]::new()
    $driver.StartInfo = New-CuaProcessInfo -Arguments @('mcp','--direct','--no-overlay')
    $null = $driver.Start()
    $driverStarted = $true
    $driverErrors = $driver.StandardError.ReadToEndAsync()
    $initialized = Invoke-SmokeRpc 'initialize' @{ protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = @{ name = 'ctrl-loop-driver-smoke'; version = '1.0' } }
    $report.server = $initialized.serverInfo
    $report.protocolVersion = $initialized.protocolVersion
    if ($initialized.serverInfo.version -ne $CuaPin.version -or $initialized.protocolVersion -ne '2025-06-18') {
        throw 'MCP server version or negotiated protocol did not match this evaluated pin.'
    }
    $driver.StandardInput.WriteLine('{"jsonrpc":"2.0","method":"notifications/initialized"}')
    $driver.StandardInput.Flush()
    $tools = Invoke-SmokeRpc 'tools/list' @{}
    $report.toolCount = @($tools.tools).Count
    foreach ($required in @('list_windows','get_window_state')) {
        if ($required -notin $tools.tools.name) { throw "Missing native tool: $required" }
    }
    $windows = Invoke-SmokeRpc 'tools/call' @{ name = 'list_windows'; arguments = @{ pid = [int]$target.pid } }
    $windows | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $runDirectory 'windows.json')
    $matchingWindows = @($windows.structuredContent.windows | Where-Object {
        $_.pid -eq $target.pid -and $_.window_id -eq $target.window_id -and $_.title -eq $target.title
    })
    if ($matchingWindows.Count -ne 1) { throw 'Driver did not enumerate the exact fixture window.' }
    $capture = Invoke-SmokeRpc 'tools/call' @{ name = 'get_window_state'; arguments = @{
        pid = [int]$target.pid; window_id = [long]$target.window_id; include_accessibility_tree = $false; include_screenshot = $true
    } }
    $metadata = $capture.structuredContent
    if ($metadata.pid -ne $target.pid -or $metadata.window_id -ne $target.window_id -or $metadata.window_title -ne $target.title) {
        throw 'Capture identity did not match the owned fixture.'
    }
    $report.captureMetadata = $metadata
    $imageBlock = @($capture.content | Where-Object type -eq 'image') | Select-Object -First 1
    if (-not $imageBlock -or $imageBlock.mimeType -ne 'image/png') { throw 'Driver returned no PNG image content.' }
    $screenshot = Join-Path $runDirectory 'fixture.png'
    [IO.File]::WriteAllBytes($screenshot, [Convert]::FromBase64String($imageBlock.data))
    $capture.content = @($capture.content | Where-Object type -ne 'image')
    $capture | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath (Join-Path $runDirectory 'capture.json')
    Add-Type -AssemblyName System.Drawing
    $bitmap = [Drawing.Bitmap]::new($screenshot)
    try {
        $magenta = 0; $cyan = 0; $black = 0; $samples = 0
        for ($y = 0; $y -lt $bitmap.Height; $y += 4) {
            for ($x = 0; $x -lt $bitmap.Width; $x += 4) {
                $color = $bitmap.GetPixel($x,$y)
                $samples++
                if ($color.R -lt 15 -and $color.G -lt 15 -and $color.B -lt 15) { $black++ }
                if ($color.R -gt 220 -and $color.G -lt 35 -and $color.B -gt 220) { $magenta++ }
                if ($color.R -lt 35 -and $color.G -gt 220 -and $color.B -gt 220) { $cyan++ }
            }
        }
        $report.image = @{ width = $bitmap.Width; height = $bitmap.Height; magentaSamples = $magenta; cyanSamples = $cyan; blackFraction = $black / $samples; sha256 = (Get-FileHash -LiteralPath $screenshot -Algorithm SHA256).Hash.ToLowerInvariant() }
        if ($bitmap.Width -ne $metadata.screenshot_width -or $bitmap.Height -ne $metadata.screenshot_height) {
            throw 'Decoded PNG dimensions do not match the capture metadata.'
        }
        if ($bitmap.Width -lt 600 -or $bitmap.Height -lt 350 -or $magenta -lt 1000 -or $cyan -lt 1000 -or $black / $samples -gt 0.03) {
            throw 'Screenshot does not contain the expected fixture geometry and colored panels.'
        }
    } finally { $bitmap.Dispose() }
    $report.passed = $true
} catch {
    $report.error = $_.Exception.Message
    throw
} finally {
    if ($driverStarted) {
        $driver.StandardInput.Close()
        $report.driverStoppedGracefully = $driver.WaitForExit(5000)
        if (-not $report.driverStoppedGracefully) { $driver.Kill($true); $driver.WaitForExit() }
        if ($driverErrors) { $driverErrors.GetAwaiter().GetResult() | Set-Content -LiteralPath (Join-Path $runDirectory 'driver.stderr.log') }
        $report.driverExitCode = $driver.ExitCode
        if (-not $report.driverStoppedGracefully -or $driver.ExitCode -ne 0) { $report.passed = $false; $report.error = 'Driver did not stop cleanly on stdin EOF.' }
    }
    if ($driver) { $driver.Dispose() }
    [IO.File]::WriteAllText($closeFile, 'close owned test fixture')
    if ($fixtureStarted) {
        $report.fixtureStoppedGracefully = $fixture.WaitForExit(5000)
        if (-not $report.fixtureStoppedGracefully) { $fixture.Kill(); $fixture.WaitForExit() }
        if ($fixtureOutput) { $fixtureOutput.GetAwaiter().GetResult() | Set-Content -LiteralPath (Join-Path $runDirectory 'fixture.stdout.log') }
        if ($fixtureErrors) { $fixtureErrors.GetAwaiter().GetResult() | Set-Content -LiteralPath (Join-Path $runDirectory 'fixture.stderr.log') }
        $report.fixtureExitCode = $fixture.ExitCode
        if (-not $report.fixtureStoppedGracefully -or $fixture.ExitCode -ne 0) { $report.passed = $false; $report.error = 'Fixture did not stop cleanly.' }
    }
    if ($fixture) { $fixture.Dispose() }
    $report | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath (Join-Path $runDirectory 'result.json')
    Write-Output "Smoke artifacts: $runDirectory"
}
if (-not $report.passed) { throw $report.error }
$report | ConvertTo-Json -Depth 12
