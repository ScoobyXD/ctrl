function Start-CtrlNativeWindow {
    param([string]$Root, [string]$Script, [string]$Generation, [string]$PidFile, [string]$Title)
    $ctrlNativePowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $ctrlNativeScript = Join-Path $Root ('scripts\' + $Script)
    if ($Generation -notmatch '^[a-zA-Z0-9_-]+$') { throw 'Invalid launch generation.' }
    $ctrlNativeStart = New-Object System.Diagnostics.ProcessStartInfo
    $ctrlNativeStart.FileName = $ctrlNativePowerShell
    $ctrlNativeStart.Arguments = '-NoProfile -ExecutionPolicy Bypass -STA -File "' + $ctrlNativeScript + '" -StopGeneration ' + $Generation
    $ctrlNativeStart.WorkingDirectory = $Root
    $ctrlNativeStart.UseShellExecute = $false
    $ctrlNativeStart.CreateNoWindow = $true
    # Suppress the console, not the interactive WinForms window.
    $ctrlNativeStart.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Normal
    $ctrlNativeProcess = [System.Diagnostics.Process]::Start($ctrlNativeStart)
    $ctrlNativeDeadline = [DateTime]::UtcNow.AddSeconds(15)
    $ctrlNativePidPath = Join-Path $Root ('.ctrl\' + $PidFile)
    do {
        $ctrlNativeCandidates = @($ctrlNativeProcess)
        $ctrlExistingId = 0
        if ((Test-Path -LiteralPath $ctrlNativePidPath) -and [int]::TryParse([System.IO.File]::ReadAllText($ctrlNativePidPath).Trim(), [ref]$ctrlExistingId) -and $ctrlExistingId -gt 0 -and $ctrlExistingId -ne $ctrlNativeProcess.Id) {
            $ctrlExisting = Get-Process -Id $ctrlExistingId -ErrorAction SilentlyContinue
            if ($ctrlExisting) { $ctrlNativeCandidates += $ctrlExisting }
        }
        foreach ($ctrlNativeCandidate in $ctrlNativeCandidates) {
            $ctrlNativeCandidate.Refresh()
            if (-not $ctrlNativeCandidate.HasExited -and $ctrlNativeCandidate.MainWindowHandle -ne [IntPtr]::Zero -and $ctrlNativeCandidate.MainWindowTitle -eq $Title) { return }
        }
        if ($ctrlNativeProcess.HasExited -and $ctrlNativeProcess.ExitCode -ne 0) {
            throw "The $Title window failed to start (exit $($ctrlNativeProcess.ExitCode)). Check .ctrl\native-launch-error.log."
        }
        Start-Sleep -Milliseconds 150
    } while ([DateTime]::UtcNow -lt $ctrlNativeDeadline)
    throw "The $Title window did not become visible. Close its older CTRL window/process and retry; startup was not confirmed."
}
