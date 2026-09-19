param([switch]$Force)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\Ctrl-Process.ps1')
$ctrlPort = 8766
$ctrlExperiment = ''
$ctrlDisplayName = '[CTRL]'
# Invalidate startup even when the PID file is absent, invalid, or reused.
$ctrlStateDirectory = Join-Path $PSScriptRoot '.ctrl'
[System.IO.Directory]::CreateDirectory($ctrlStateDirectory) | Out-Null
[System.IO.File]::WriteAllText((Join-Path $ctrlStateDirectory 'stop-generation'), [guid]::NewGuid().ToString('N'))
$ctrlPidFile = Join-Path $ctrlStateDirectory 'server.pid'
$ctrlSavedPid = if (Test-Path -LiteralPath $ctrlPidFile) { [System.IO.File]::ReadAllText($ctrlPidFile) } else { $null }

# Graceful shutdown is independent of the advisory PID file. Check the endpoint's
# app identity before sending its in-memory token; never follow a redirect.
Add-Type -AssemblyName System.Net.Http
$ctrlHandler = New-Object System.Net.Http.HttpClientHandler
$ctrlHandler.UseProxy = $false
$ctrlHandler.AllowAutoRedirect = $false
$ctrlHttp = New-Object System.Net.Http.HttpClient($ctrlHandler)
$ctrlHttp.Timeout = [TimeSpan]::FromMilliseconds(500)
$ctrlBaseUrl = "http://127.0.0.1:$ctrlPort"
try {
    $ctrlHealth = $ctrlHttp.GetStringAsync("$ctrlBaseUrl/health").GetAwaiter().GetResult() | ConvertFrom-Json
    if ($ctrlHealth.app -eq '[CTRL]' -and [string]$ctrlHealth.experiment -eq $ctrlExperiment) {
        $ctrlHtml = $ctrlHttp.GetStringAsync("$ctrlBaseUrl/").GetAwaiter().GetResult()
        $ctrlToken = [regex]::Match($ctrlHtml, 'name="ctrl-token" content="([^"]+)"').Groups[1].Value
        if ($ctrlToken) {
            $ctrlHttp.DefaultRequestHeaders.Add('X-CTRL-Token', $ctrlToken)
            $ctrlBody = New-Object System.Net.Http.StringContent('{}', [System.Text.Encoding]::UTF8, 'application/json')
            try { $ctrlReply = $ctrlHttp.PostAsync("$ctrlBaseUrl/api/shutdown", $ctrlBody).GetAwaiter().GetResult(); $ctrlReply.Dispose() }
            finally { $ctrlBody.Dispose() }
        }
    }
} catch { # An unresponsive endpoint cannot prevent the strictly owned-process fallback.
} finally { $ctrlToken = $null; $ctrlHtml = $null; $ctrlHttp.Dispose(); $ctrlHandler.Dispose() }

# Discover only this installation's exact Python launcher and ctrl.server module.
# A stale saved PID never authorizes killing the unrelated process that reused it.
$ctrlParents = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { Test-CtrlServerProcess -Process $_ -Root $PSScriptRoot })
$ctrlChildren = @()
foreach ($ctrlParent in $ctrlParents) {
    $ctrlOwnedChildren = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($ctrlParent.ProcessId)" |
        Where-Object { Test-CtrlServerChild -Process $_ -Parent $ctrlParent -Root $PSScriptRoot })
    foreach ($ctrlChild in $ctrlOwnedChildren) {
        $ctrlChildren += [pscustomobject]@{ Process=$ctrlChild; Parent=$ctrlParent }
    }
    if (-not $Force) { Wait-Process -Id $ctrlParent.ProcessId -Timeout 1 -ErrorAction SilentlyContinue }
    foreach ($ctrlChild in $ctrlOwnedChildren) {
        $ctrlCurrent = Get-CimInstance Win32_Process -Filter "ProcessId = $($ctrlChild.ProcessId)" -ErrorAction SilentlyContinue
        if ($ctrlCurrent -and $ctrlCurrent.CreationDate -eq $ctrlChild.CreationDate -and
            (Test-CtrlServerChild -Process $ctrlCurrent -Parent $ctrlParent -Root $PSScriptRoot)) {
            Stop-Process -Id $ctrlChild.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    $ctrlCurrent = Get-CimInstance Win32_Process -Filter "ProcessId = $($ctrlParent.ProcessId)" -ErrorAction SilentlyContinue
    if ($ctrlCurrent -and $ctrlCurrent.CreationDate -eq $ctrlParent.CreationDate -and
        (Test-CtrlServerProcess -Process $ctrlCurrent -Root $PSScriptRoot)) {
        Stop-Process -Id $ctrlParent.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Test-CtrlStopPortClosed {
    param([int]$Port)
    try {
        $ctrlNetwork = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties()
        return -not @($ctrlNetwork.GetActiveTcpListeners() | Where-Object { $_.Port -eq $Port }).Count
    } catch { return $false } # Unknown listener state cannot confirm shutdown.
}

$ctrlVerified = $false
$ctrlDeadline = [DateTime]::UtcNow.AddSeconds(2)
do {
    $ctrlRemaining = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { Test-CtrlServerProcess -Process $_ -Root $PSScriptRoot })
    foreach ($ctrlRecord in $ctrlChildren) {
        $ctrlCurrent = Get-CimInstance Win32_Process -Filter "ProcessId = $($ctrlRecord.Process.ProcessId)" -ErrorAction SilentlyContinue
        if ($ctrlCurrent -and $ctrlCurrent.CreationDate -eq $ctrlRecord.Process.CreationDate -and
            (Test-CtrlServerChild -Process $ctrlCurrent -Parent $ctrlRecord.Parent -Root $PSScriptRoot)) {
            $ctrlRemaining += $ctrlCurrent
        }
    }
    if (-not $ctrlRemaining.Count -and (Test-CtrlStopPortClosed -Port $ctrlPort)) { $ctrlVerified = $true; break }
    Start-Sleep -Milliseconds 100
} while ([DateTime]::UtcNow -lt $ctrlDeadline)
if (-not $ctrlVerified) {
    throw 'Stop was requested, but shutdown could not be confirmed. An owned process or local listener remains; unrelated processes were not stopped.'
}
if ((Test-Path -LiteralPath $ctrlPidFile) -and [System.IO.File]::ReadAllText($ctrlPidFile) -eq $ctrlSavedPid) {
    Remove-Item -LiteralPath $ctrlPidFile -Force -ErrorAction SilentlyContinue
}
Write-Host "$ctrlDisplayName is off. Chrome and its tabs remain open."
