$ErrorActionPreference = 'Stop'
$ctrlPidFile = Join-Path $PSScriptRoot '.ctrl\server.pid'
if (-not (Test-Path -LiteralPath $ctrlPidFile)) { Write-Host '[CTRL] is not running from this launcher.'; exit }
$ctrlProcessId = [int](Get-Content -LiteralPath $ctrlPidFile -Raw)
$ctrlProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $ctrlProcessId" -ErrorAction SilentlyContinue
$ctrlExpected = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($ctrlProcess -and $ctrlProcess.ExecutablePath -eq $ctrlExpected -and $ctrlProcess.CommandLine -match '-m ctrl\.server') {
    try {
        $ctrlHtml = (Invoke-WebRequest -Uri 'http://127.0.0.1:8766/' -TimeoutSec 2).Content
        $ctrlToken = [regex]::Match($ctrlHtml, 'name="ctrl-token" content="([^"]+)"').Groups[1].Value
        Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/shutdown' -Method Post `
            -Headers @{'X-CTRL-Token'=$ctrlToken} -ContentType 'application/json' -Body '{}' -TimeoutSec 3 | Out-Null
        Wait-Process -Id $ctrlProcessId -Timeout 8 -ErrorAction SilentlyContinue
    } catch {}
    # Windows venv Python uses a redirector process. Stop only its verified server child if needed.
    $ctrlChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ctrlProcessId" |
        Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match '-m ctrl\.server' }
    foreach ($ctrlChild in $ctrlChildren) { Stop-Process -Id $ctrlChild.ProcessId -ErrorAction SilentlyContinue }
    Stop-Process -Id $ctrlProcessId -ErrorAction SilentlyContinue
    Write-Host '[CTRL] stopped. Chrome tabs remain open.'
} elseif ($ctrlProcess) { throw 'The stored process ID belongs to another program; it was not stopped.' }
Remove-Item -LiteralPath $ctrlPidFile
