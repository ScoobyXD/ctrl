$ErrorActionPreference = 'Stop'
$ctrlRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'Ctrl-Process.ps1')
foreach ($ctrlScript in @('Start-CTRL.ps1', 'Stop-CTRL.ps1', 'scripts\Control-CTRL.ps1', 'scripts\Ctrl-Process.ps1')) {
    $ctrlTokens = $null; $ctrlErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $ctrlRoot $ctrlScript), [ref]$ctrlTokens, [ref]$ctrlErrors) | Out-Null
    if ($ctrlErrors.Count) { throw ($ctrlErrors | Out-String) }
}
$ctrlParent = [pscustomobject]@{ ProcessId=731; ExecutablePath=(Join-Path $ctrlRoot '.venv\Scripts\python.exe'); CommandLine='python.exe -m ctrl.server --port 8766' }
if (-not (Test-CtrlServerProcess $ctrlParent $ctrlRoot)) { throw 'The owned server was not recognized.' }
$ctrlForeign = [pscustomobject]@{ ProcessId=731; ExecutablePath='C:\Other\python.exe'; CommandLine='python.exe -m ctrl.server' }
if (Test-CtrlServerProcess $ctrlForeign $ctrlRoot) { throw 'A different Python installation was accepted.' }
$ctrlWrongModule = [pscustomobject]@{ ExecutablePath=$ctrlParent.ExecutablePath; CommandLine='python.exe -m ctrl.server_unrelated' }
if (Test-CtrlServerProcess $ctrlWrongModule $ctrlRoot) { throw 'An unrelated Python module was accepted.' }
$ctrlChild = [pscustomobject]@{ ProcessId=732; ParentProcessId=731; Name='python.exe'; CommandLine='python.exe -m ctrl.server' }
if (-not (Test-CtrlServerChild $ctrlChild $ctrlParent $ctrlRoot)) { throw 'The owned server child was not recognized.' }
$ctrlChild.ParentProcessId = 999
if (Test-CtrlServerChild $ctrlChild $ctrlParent $ctrlRoot) { throw 'An unrelated process child was accepted.' }
& (Join-Path $PSScriptRoot 'Control-CTRL.ps1') -VerifyOnly
Write-Output '[CTRL] launcher and process-ownership checks passed. No process was stopped or started.'
