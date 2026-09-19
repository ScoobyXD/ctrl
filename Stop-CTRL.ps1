param([switch]$Force)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\Ctrl-Process.ps1')
# Invalidate any startup that began before this Stop, even before a PID exists.
$ctrlStateDirectory = Join-Path $PSScriptRoot '.ctrl'
[System.IO.Directory]::CreateDirectory($ctrlStateDirectory) | Out-Null
[System.IO.File]::WriteAllText((Join-Path $ctrlStateDirectory 'stop-generation'), [guid]::NewGuid().ToString('N'))
$ctrlPidFile = Join-Path $PSScriptRoot '.ctrl\server.pid'
if (-not (Test-Path -LiteralPath $ctrlPidFile)) { Write-Host '[CTRL] is already off.'; return }
$ctrlProcessId = 0
if (-not [int]::TryParse((Get-Content -LiteralPath $ctrlPidFile -Raw).Trim(), [ref]$ctrlProcessId) -or $ctrlProcessId -le 0) {
    throw 'The saved CTRL process ID is invalid. No process was stopped.'
}
$ctrlProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $ctrlProcessId" -ErrorAction SilentlyContinue
if ($ctrlProcess -and -not (Test-CtrlServerProcess -Process $ctrlProcess -Root $PSScriptRoot)) {
    throw 'The saved process ID belongs to another program. It was not stopped.'
}
if ($ctrlProcess) {
    $ctrlChildren = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ctrlProcessId" |
        Where-Object { Test-CtrlServerChild -Process $_ -Parent $ctrlProcess -Root $PSScriptRoot })
    Add-Type -AssemblyName System.Net.Http
    $ctrlHandler = New-Object System.Net.Http.HttpClientHandler
    $ctrlHandler.UseProxy = $false
    $ctrlHttp = New-Object System.Net.Http.HttpClient($ctrlHandler)
    $ctrlHttp.Timeout = [TimeSpan]::FromMilliseconds(500)
    try {
        # The local token exists only in memory. Neither HTML nor headers are logged.
        $ctrlHtml = $ctrlHttp.GetStringAsync('http://127.0.0.1:8766/').GetAwaiter().GetResult()
        $ctrlToken = [regex]::Match($ctrlHtml, 'name="ctrl-token" content="([^"]+)"').Groups[1].Value
        if ($ctrlToken) {
            $ctrlHttp.DefaultRequestHeaders.Add('X-CTRL-Token', $ctrlToken)
            $ctrlBody = New-Object System.Net.Http.StringContent('{}', [System.Text.Encoding]::UTF8, 'application/json')
            try { $ctrlReply = $ctrlHttp.PostAsync('http://127.0.0.1:8766/api/shutdown', $ctrlBody).GetAwaiter().GetResult(); $ctrlReply.Dispose() }
            finally { $ctrlBody.Dispose() }
        }
        if (-not $Force) { Wait-Process -Id $ctrlProcessId -Timeout 1 -ErrorAction SilentlyContinue }
    } catch { # An unresponsive server cannot prevent the process fallback.
    } finally { $ctrlToken = $null; $ctrlHtml = $null; $ctrlHttp.Dispose(); $ctrlHandler.Dispose() }
    foreach ($ctrlChild in $ctrlChildren) {
        $ctrlCurrentChild = Get-CimInstance Win32_Process -Filter "ProcessId = $($ctrlChild.ProcessId)" -ErrorAction SilentlyContinue
        if ($ctrlCurrentChild -and $ctrlCurrentChild.CreationDate -eq $ctrlChild.CreationDate -and
            (Test-CtrlServerChild -Process $ctrlCurrentChild -Parent $ctrlProcess -Root $PSScriptRoot)) {
            Stop-Process -Id $ctrlChild.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    $ctrlCurrentParent = Get-CimInstance Win32_Process -Filter "ProcessId = $ctrlProcessId" -ErrorAction SilentlyContinue
    if ($ctrlCurrentParent -and $ctrlCurrentParent.CreationDate -eq $ctrlProcess.CreationDate -and
        (Test-CtrlServerProcess -Process $ctrlCurrentParent -Root $PSScriptRoot)) {
        Stop-Process -Id $ctrlProcessId -Force -ErrorAction SilentlyContinue
    }
}
Remove-Item -LiteralPath $ctrlPidFile -Force -ErrorAction SilentlyContinue
Write-Host '[CTRL] is off. Chrome and its tabs remain open.'
