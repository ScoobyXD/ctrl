"""Real helper-process boundary tests with an injected, entirely fake backend.

These exercise watchdog termination and JSON error privacy, not live UIA. They
never import the real UIA backend, enumerate windows, or inspect desktop content.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "uia-observe.py"
PRIVATE_SENTINEL = "private-provider-detail-DO-NOT-EMIT"

# The real CLI remains unmodified. Its imported backend is replaced in this
# isolated interpreter before runpy executes the production entry point.
STUB_RUNNER = r'''
import runpy
import sys
import time
from types import ModuleType

script, mode, command = sys.argv[1:4]
private_detail = "private-provider-detail-DO-NOT-EMIT"
backend = ModuleType("ctrl_loop.uia_backend")

class UIAReader:
    def __init__(self):
        if mode == "init_hang":
            time.sleep(10)
        if mode == "init_exception":
            raise RuntimeError(private_detail)

    def observe(self, hwnd, pid, max_elements, timeout, max_depth):
        if mode == "observe_hang":
            time.sleep(10)
        if mode == "observe_exception":
            raise RuntimeError(private_detail)
        from ctrl_loop.uia_contract import new_snapshot
        result = new_snapshot(hwnd, pid)
        result["target"]["title"] = "Owned fake fixture"
        result["fixture_arguments"] = [max_elements, timeout, max_depth]
        return result

def list_windows():
    if mode == "windows_exception":
        raise RuntimeError(private_detail)
    return {"windows": [{"hwnd": 123, "pid": 456, "title": "Owned fake fixture"}]}

backend.UIAReader = UIAReader
backend.list_windows = list_windows
sys.modules["ctrl_loop.uia_backend"] = backend
sys.argv = [script, command, "--hwnd", "123", "--pid", "456",
            "--max-elements", "11", "--max-depth", "3", "--timeout", "0.1"]
runpy.run_path(script, run_name="__main__")
'''


class HelperProcessContractTests(unittest.TestCase):
    def run_helper(self, mode, command="observe"):
        # Generated runners stay in this worktree's ignored runtime directory.
        # Resolve and verify the cleanup scope before TemporaryDirectory owns it.
        temporary_root = (ROOT / ".runtime" / "uia-cli-tests").resolve()
        if not temporary_root.is_relative_to(ROOT.resolve()):
            raise RuntimeError("Temporary test directory escaped the worktree")
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="runner-", dir=temporary_root) as temp:
            runner = Path(temp) / "fake_backend_runner.py"
            runner.write_text(STUB_RUNNER, encoding="utf-8")
            started = time.monotonic()
            result = subprocess.run(
                [sys.executable, str(runner), str(HELPER), mode, command],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                timeout=4.5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            elapsed = time.monotonic() - started
        self.assertEqual(result.stderr, "", "Helper must not print provider exceptions or tracebacks")
        self.assertNotIn(PRIVATE_SENTINEL, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, "Exactly one JSON response must cross the process boundary")
        payload = json.loads(lines[0])
        self.assertIsInstance(payload, dict)
        return result, payload, elapsed

    def assert_hard_deadline(self, mode):
        result, payload, elapsed = self.run_helper(mode)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(payload["status"], "error")
        self.assertTrue(payload["truncated"])
        self.assertIn("hard deadline", payload["error"])
        self.assertEqual(payload["elements"], [])
        self.assertEqual(payload["options"], [])
        self.assertEqual(payload["target"]["hwnd"], 123)
        self.assertEqual(payload["target"]["pid"], 456)
        self.assertLess(elapsed, 4.5, "Ten-second provider hang must be terminated by the watchdog")

    def test_watchdog_terminates_hung_backend_initialization(self):
        self.assert_hard_deadline("init_hang")

    def test_watchdog_terminates_hung_observation(self):
        self.assert_hard_deadline("observe_hang")

    def test_initialization_exception_is_a_single_generic_json_error(self):
        result, payload, _ = self.run_helper("init_exception")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("initialization failed", payload["error"])
        self.assertEqual(payload["elements"], [])
        self.assertEqual(payload["options"], [])

    def test_observation_exception_does_not_leak_provider_details(self):
        result, payload, _ = self.run_helper("observe_exception")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["elements"], [])
        self.assertEqual(payload["options"], [])

    def test_window_enumeration_exception_preserves_window_response_shape(self):
        result, payload, _ = self.run_helper("windows_exception", "windows")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["windows"], [])
        self.assertIn("initialization failed", payload["error"])

    def test_normal_observation_is_one_valid_snapshot_with_forwarded_budgets(self):
        result, payload, _ = self.run_helper("normal")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["source"], "windows-uia")
        self.assertTrue(payload["snapshot_id"])
        self.assertEqual(payload["target"], {"hwnd": 123, "pid": 456, "title": "Owned fake fixture"})
        self.assertEqual(payload["fixture_arguments"], [11, 0.1, 3])

    def test_normal_window_response_is_one_json_document(self):
        result, payload, _ = self.run_helper("normal", "windows")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload, {"windows": [{"hwnd": 123, "pid": 456, "title": "Owned fake fixture"}]})


if __name__ == "__main__":
    unittest.main()
