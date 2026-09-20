# Shared only by this dependency's launcher and smoke check.
#Requires -Version 7.2
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'CUA Driver setup requires Windows.' }
$CuaProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$CuaPin = Get-Content -LiteralPath (Join-Path $CuaProjectRoot 'dependencies/cua-driver.lock.json') -Raw | ConvertFrom-Json
$CuaRuntime = Join-Path $CuaProjectRoot '.runtime/cua-driver'
$CuaExecutable = Join-Path $CuaRuntime ('packages/' + [IO.Path]::GetFileNameWithoutExtension($CuaPin.asset) + '/cua-driver.exe')
if (-not (Test-Path -LiteralPath $CuaExecutable)) { throw 'Run scripts/install-cua-driver.ps1 first.' }
if ((Get-FileHash -LiteralPath $CuaExecutable -Algorithm SHA256).Hash -ne $CuaPin.executableSha256) {
    throw 'Driver hash differs from the pin. Rerun installation to diagnose.'
}

function New-CuaProcessInfo {
    param([string[]]$Arguments)
    $info = [Diagnostics.ProcessStartInfo]::new($CuaExecutable)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WorkingDirectory = $CuaProjectRoot
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    # These settings apply only to the child. Do not repurpose HOME or USERPROFILE.
    $info.Environment['CUA_DRIVER_RS_TELEMETRY_ENABLED'] = 'false'
    $info.Environment['CUA_DRIVER_RS_UPDATE_CHECK'] = 'false'
    $info.Environment['CUA_DRIVER_TELEMETRY_HOME'] = Join-Path $CuaRuntime 'telemetry'
    $info.Environment['CUA_DRIVER_PERMISSION_MODE'] = 'standard'
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    return $info
}

function Invoke-CuaCommand {
    param([string[]]$Arguments, [int]$TimeoutSeconds = 30)
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = New-CuaProcessInfo -Arguments $Arguments
    try {
        $null = $process.Start()
        $process.StandardInput.Close()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill($true)
            throw "CUA command timed out: $($Arguments -join ' ')"
        }
        $result = $stdout.GetAwaiter().GetResult()
        $errorText = $stderr.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) { throw "CUA exited $($process.ExitCode): $errorText $result" }
        return $result
    } finally { $process.Dispose() }
}
