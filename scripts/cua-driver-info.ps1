#Requires -Version 7.2
. (Join-Path $PSScriptRoot 'cua-driver-common.ps1')
Invoke-CuaCommand -Arguments @('--version')
Invoke-CuaCommand -Arguments @('telemetry', 'status', '--json')
Invoke-CuaCommand -Arguments @('doctor', '--json')
