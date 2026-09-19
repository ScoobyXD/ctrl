"""Exercise a copied Stop script against fake process records and a disposable app.

Neither the installed app scripts nor real process-management commands are run.
"""

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows Stop script regression")


def run_stop(
    tmp_path, *, pid="733", owned=False, stop_app=True, kill_fails=False, reuse_child=False, foreign_app=False
):
    root = tmp_path / "CTRL [synthetic]"
    (root / "scripts").mkdir(parents=True)
    (root / ".ctrl").mkdir()
    if pid is not None:
        (root / ".ctrl" / "server.pid").write_text(pid)
    marker = root / "shutdown-received"
    requests = []
    experiment = "hybrid" if "$ctrlExperiment = 'hybrid'" in (ROOT / "Stop-CTRL.ps1").read_text() else ""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            requests.append(self.path)
            if self.path == "/health":
                body = json.dumps({"app": "other" if foreign_app else "[CTRL]", "experiment": experiment}).encode()
            else:
                body = b'<meta name="ctrl-token" content="synthetic-token">'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            requests.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            authorized = self.headers.get("X-CTRL-Token") == "synthetic-token"
            if authorized:
                marker.write_text("received")
            self.send_response(200 if authorized else 403)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            if authorized:
                if stop_app:
                    threading.Thread(target=close_app, daemon=True).start()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    close_lock = threading.Lock()

    def close_app():
        with close_lock:
            server.shutdown()
            server.server_close()

    server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    server_thread.start()
    # Only the temporary copy points to the disposable fixture's random port.
    script = (ROOT / "Stop-CTRL.ps1").read_text()
    script = script.replace("$ctrlPort = 8766", f"$ctrlPort = {server.server_port}")
    script = script.replace("$ctrlPort = 8767", f"$ctrlPort = {server.server_port}")
    (root / "Stop-CTRL.ps1").write_text(script)
    shutil.copyfile(ROOT / "scripts" / "Ctrl-Process.ps1", root / "scripts" / "Ctrl-Process.ps1")
    records = [
        {
            "ProcessId": 733,
            "Name": "python.exe",
            "ExecutablePath": "C:\\Other\\python.exe",
            "CommandLine": "python.exe -m ctrl.server",
            "CreationDate": "foreign",
        },
    ]
    if owned:
        records += [
            {
                "ProcessId": 731,
                "Name": "python.exe",
                "ExecutablePath": str(root / ".venv" / "Scripts" / "python.exe"),
                "CommandLine": "python.exe -m ctrl.server",
                "CreationDate": "parent",
            },
            {
                "ProcessId": 732,
                "ParentProcessId": 731,
                "Name": "python.exe",
                "ExecutablePath": "C:\\Python\\python.exe",
                "CommandLine": "python.exe -m ctrl.server",
                "CreationDate": "child",
            },
        ]
    (root / "processes.json").write_text(json.dumps(records))
    wrapper = root / "test-wrapper.ps1"
    wrapper.write_text(
        r"""
param([string]$Root, [bool]$KillFails, [bool]$ReuseChild)
$global:ctrlTestRecords = @((Get-Content -LiteralPath (Join-Path $Root 'processes.json') -Raw | ConvertFrom-Json))
$global:ctrlTestKilled = @()
function Get-CimInstance {
    param($ClassName, $Filter, $ErrorAction)
    if ($Filter -eq "Name = 'python.exe'") { return $global:ctrlTestRecords }
    if ($Filter -match '^ParentProcessId = (\d+)$') {
        return @($global:ctrlTestRecords | Where-Object { $_.ParentProcessId -eq [int]$Matches[1] })
    }
    if ($Filter -match '^ProcessId = (\d+)$') {
        $record = @($global:ctrlTestRecords | Where-Object { $_.ProcessId -eq [int]$Matches[1] })
        if ($ReuseChild -and [int]$Matches[1] -eq 732 -and $record.Count) {
            return [pscustomobject]@{
                ProcessId=732; ParentProcessId=731; Name='python.exe'; CommandLine='unrelated'; CreationDate='reused'
            }
        }
        return $record
    }
    throw "Unexpected process query: $Filter"
}
function Stop-Process {
    param($Id, [switch]$Force, $ErrorAction)
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'shutdown-received'))) {
        throw 'Kill attempted before graceful shutdown.'
    }
    if ($Id -eq 733) { throw 'Unrelated saved PID was targeted.' }
    $global:ctrlTestKilled += $Id
    if (-not $KillFails) {
        $global:ctrlTestRecords = @($global:ctrlTestRecords | Where-Object { $_.ProcessId -ne $Id })
    }
}
function Wait-Process { param($Id, $Timeout, $ErrorAction) }
$failure = $null
try { & (Join-Path $Root 'Stop-CTRL.ps1') -Force } catch { $failure = $_.Exception.Message }
@{ error=$failure; killed=$global:ctrlTestKilled } | ConvertTo-Json -Compress
"""
    )
    # Booleans are literals in a command, not loosely coerced -File string values.
    command = (
        f"& '{wrapper}' -Root '{root}' -KillFails ${str(kill_fails).lower()} -ReuseChild ${str(reuse_child).lower()}"
    )
    try:
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=15
        )
        assert result.returncode == 0, result.stderr
        outcome = json.loads(result.stdout.strip().splitlines()[-1])
        return outcome, result.stdout, requests, root
    finally:
        close_app()
        server_thread.join(timeout=1)


@pytest.mark.parametrize("pid", [None, "not-a-pid", "733"])
def test_stale_or_missing_pid_never_blocks_authenticated_shutdown(tmp_path, pid):
    result, output, requests, root = run_stop(tmp_path, pid=pid)
    assert result == {"error": None, "killed": []}
    assert requests == ["/health", "/", "/api/shutdown"]
    assert "is off." in output
    assert not (root / ".ctrl" / "server.pid").exists()
    assert (root / ".ctrl" / "stop-generation").read_text()


def test_fallback_finds_exact_owned_launcher_despite_foreign_saved_pid(tmp_path):
    result, output, requests, _ = run_stop(tmp_path, owned=True)
    assert result == {"error": None, "killed": [732, 731]}
    assert requests[-1] == "/api/shutdown"
    assert "is off." in output


def test_reused_child_pid_is_not_killed(tmp_path):
    result, output, _, _ = run_stop(tmp_path, owned=True, reuse_child=True)
    assert result == {"error": None, "killed": [731]}
    assert "is off." in output


@pytest.mark.parametrize("owned,stop_app,kill_fails", [(False, False, False), (True, True, True)])
def test_live_listener_or_owned_process_prevents_false_off_confirmation(tmp_path, owned, stop_app, kill_fails):
    result, output, requests, root = run_stop(tmp_path, owned=owned, stop_app=stop_app, kill_fails=kill_fails)
    assert "could not be confirmed" in result["error"]
    assert "/api/shutdown" in requests
    assert "is off." not in output
    assert (root / ".ctrl" / "server.pid").exists()


def test_different_app_on_port_never_receives_a_shutdown_token(tmp_path):
    result, output, requests, _ = run_stop(tmp_path, foreign_app=True)
    assert "could not be confirmed" in result["error"]
    assert result["killed"] == []
    assert requests == ["/health"]
    assert "is off." not in output
