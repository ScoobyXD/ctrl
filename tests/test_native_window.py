"""Launch actual disposable WinForms windows through the shipped native helper."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows native launch regression")


def test_native_helper_shows_window_and_reuses_existing_window(tmp_path):
    root = tmp_path / "CTRL [native probe]"
    (root / "scripts").mkdir(parents=True)
    (root / ".ctrl").mkdir()
    shutil.copyfile(ROOT / "scripts/Start-CtrlNativeWindow.ps1", root / "scripts/Start-CtrlNativeWindow.ps1")
    (root / "scripts/Debug-CTRL.ps1").write_text(r'''param([string]$StopGeneration)
$ErrorActionPreference = 'Stop'
$probeRoot = Split-Path -Parent $PSScriptRoot
$probePidFile = Join-Path $probeRoot '.ctrl/debugger.pid'
if (Test-Path -LiteralPath $probePidFile) {
    $probeExisting = Get-Process -Id ([int][IO.File]::ReadAllText($probePidFile)) -ErrorAction SilentlyContinue
    if ($probeExisting -and $probeExisting.MainWindowTitle -eq 'CTRL disposable native probe') { exit 0 }
}
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = 'CTRL disposable native probe'
$form.StartPosition = 'Manual'
$form.Location = New-Object System.Drawing.Point(-32000,-32000)
[IO.File]::WriteAllText($probePidFile,[string]$PID)
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 12000
$timer.Add_Tick({ $form.Close() })
$form.Add_Shown({ $timer.Start() })
try { [System.Windows.Forms.Application]::Run($form) } finally { $timer.Dispose(); $form.Dispose() }
''', encoding="utf-8")
    wrapper = root / "verify.ps1"
    wrapper.write_text(r'''param([string]$Root)
$ErrorActionPreference = 'Stop'
. (Join-Path $Root 'scripts/Start-CtrlNativeWindow.ps1')
$probeLaunch = @{
    Root=$Root; Script='Debug-CTRL.ps1'; Generation='initial'
    PidFile='debugger.pid'; Title='CTRL disposable native probe'
}
$probe = $null
try {
    Start-CtrlNativeWindow @probeLaunch
    $first = [int][IO.File]::ReadAllText((Join-Path $Root '.ctrl/debugger.pid'))
    $probe = Get-Process -Id $first
    $handle = $probe.MainWindowHandle.ToInt64()
    Start-CtrlNativeWindow @probeLaunch
    $second = [int][IO.File]::ReadAllText((Join-Path $Root '.ctrl/debugger.pid'))
    @{ first=$first; second=$second; handle=$handle; responding=$probe.Responding } | ConvertTo-Json -Compress
} finally { if ($probe -and -not $probe.HasExited) { $probe.Kill() } }
''', encoding="utf-8")
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper), str(root)],
        capture_output=True, text=True, timeout=35,
    )
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["first"] == evidence["second"]
    assert evidence["handle"] != 0 and evidence["responding"]


def test_native_helper_surfaces_real_child_startup_failure(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/Start-CtrlNativeWindow.ps1", tmp_path / "scripts/Start-CtrlNativeWindow.ps1")
    (tmp_path / "scripts/Debug-CTRL.ps1").write_text("param([string]$StopGeneration)\nexit 7\n", encoding="utf-8")
    wrapper = tmp_path / "verify.ps1"
    wrapper.write_text(r'''param([string]$Root)
$ErrorActionPreference = 'Stop'
. (Join-Path $Root 'scripts/Start-CtrlNativeWindow.ps1')
$probeLaunch = @{
    Root=$Root; Script='Debug-CTRL.ps1'; Generation='initial'
    PidFile='debugger.pid'; Title='CTRL disposable native probe'
}
Start-CtrlNativeWindow @probeLaunch
''', encoding="utf-8")
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper), str(tmp_path)],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode != 0
    assert "failed to start (exit 7)" in result.stderr
