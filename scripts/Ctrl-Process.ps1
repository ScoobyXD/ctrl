function Test-CtrlLaunchGeneration {
    param([string]$Root, [string]$Generation)
    if (-not $Generation) { return $true }
    $ctrlGenerationFile = Join-Path $Root '.ctrl\stop-generation'
    $ctrlGenerationNow = if (Test-Path -LiteralPath $ctrlGenerationFile) { [System.IO.File]::ReadAllText($ctrlGenerationFile).Trim() } else { 'initial' }
    return $Generation -eq $ctrlGenerationNow
}

function Test-CtrlServerProcess {
    param($Process, [string]$Root)
    if (-not $Process -or -not $Process.ExecutablePath) { return $false }
    $expected = [System.IO.Path]::GetFullPath((Join-Path $Root '.venv\Scripts\python.exe'))
    return [string]::Equals($Process.ExecutablePath, $expected, [System.StringComparison]::OrdinalIgnoreCase) -and
        $Process.CommandLine -match '(?:^|\s)-m\s+ctrl\.server(?:\s|$)'
}

function Test-CtrlServerChild {
    param($Process, $Parent, [string]$Root)
    return (Test-CtrlServerProcess -Process $Parent -Root $Root) -and $Process -and
        $Process.ParentProcessId -eq $Parent.ProcessId -and $Process.Name -eq 'python.exe' -and
        $Process.CommandLine -match '(?:^|\s)-m\s+ctrl\.server(?:\s|$)'
}
