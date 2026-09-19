"""Compile launch configuration without opening a form or starting another process."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which("powershell.exe"), reason="Windows native launcher")
def test_control_panel_gui_child_is_visible_without_console_and_helpers_stay_hidden(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "inspect-launch.ps1"
    runner.write_text(
        r"""
param([string]$Root)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing, System.Net.Http, System.Web.Extensions
Add-Type -Path (Join-Path $Root 'scripts\ControlPanel.cs') -ReferencedAssemblies @(
    'System.Windows.Forms', 'System.Drawing', 'System.Net.Http', 'System.Web.Extensions'
)
$result = @()
foreach ($script in @('scripts\Debug-CTRL.ps1', 'Start-CTRL.ps1', 'Stop-CTRL.ps1')) {
    $launch = [CtrlPanelApp]::LaunchInfo($Root, $script, @())
    $result += @{
        script=$script; window_style=$launch.WindowStyle.ToString(); no_console=$launch.CreateNoWindow
        shell=$launch.UseShellExecute; directory=$launch.WorkingDirectory; arguments=$launch.Arguments
    }
}
$result | ConvertTo-Json -Compress
"""
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-File", str(runner), "-Root", str(root)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    configs = json.loads(result.stdout)
    assert [config["window_style"] for config in configs] == ["Normal", "Hidden", "Hidden"]
    for config in configs:
        assert config["no_console"] and not config["shell"]
        assert Path(config["directory"]) == root
        assert f'-File "{root / config["script"]}"' in config["arguments"]
        assert "-STA" in config["arguments"]
