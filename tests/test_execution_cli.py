"""Real execution-helper pipe/deadline tests with offline boundary substitutes."""
import json
from pathlib import Path
import subprocess
import sys
import shutil
import time
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]


BOOTSTRAP = r'''
import os, runpy, sys, time
sys.path.insert(0, str(ROOT / "src"))
from ctrl_loop import credentials, cua_driver, execution_runtime, selection
from ctrl_loop.uia_contract import new_snapshot

def observe(target, **kwargs):
    return new_snapshot(target["hwnd"], target["pid"])

class Executor:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass

def key():
    if MODE == "blocked_output":
        sys.stdout.write("x" * 1000000)
        sys.stdout.flush()
    if MODE in ("credentials_hang", "blocked_output"):
        time.sleep(60)
    return "offline-fixture-key"

def run(self, *args, **kwargs):
    return {"status": "no_match", "snapshot_id": self.snapshot_id, "goal": self.goal,
            "input_actions": 0, "selected_option": None}

credentials.get_openrouter_key = key
cua_driver.NativeCuaExecutor = Executor
execution_runtime.observe_target = observe
selection.SelectionSearch.run = run
sys.argv = [str(ROOT / "scripts/cua-run.py"), "--stdin"]
runpy.run_path(sys.argv[0], run_name="__main__")
'''


class ExecutionHelperTests(unittest.TestCase):
    def start_helper(self, mode="done", seconds=20, **updates):
        folder = ROOT / ".runtime" / "execution-cli-tests"
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder / uuid.uuid4().hex
        self.folder.mkdir()
        wrapper = self.folder / "bootstrap.py"
        wrapper.write_text("from pathlib import Path\nROOT = Path(" + repr(str(ROOT)) + ")\nMODE = " + repr(mode) + "\n" + BOOTSTRAP)
        self.process = subprocess.Popen([sys.executable, "-I", str(wrapper)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        request = {"target": {"hwnd": 1, "pid": 2}, "goal": "Synthetic test", "run_id": "pipe-test",
                   "max_steps": 1, "max_seconds": seconds}
        request.update(updates)
        self.process.stdin.write((json.dumps(request) + "\n").encode())
        self.process.stdin.flush()
        return self.process

    def tearDown(self):
        if getattr(self, "process", None):
            if self.process.poll() is None:
                self.process.kill()
            self.process.communicate()
        if getattr(self, "folder", None):
            root = (ROOT / ".runtime/execution-cli-tests").resolve()
            folder = self.folder.resolve()
            if folder == root or not folder.is_relative_to(root):
                raise AssertionError("Unsafe test cleanup path")
            shutil.rmtree(folder)

    def test_helper_exits_with_stdin_open_and_one_terminal_result(self):
        process = self.start_helper()
        self.assertEqual(process.wait(timeout=5), 0)
        output, errors = process.communicate()
        self.assertNotIn(b"Fatal Python error", errors)
        events = [json.loads(line) for line in output.splitlines()]
        terminals = [e for e in events if e["event"] == "result"]
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0]["data"]["status"], "no_match")
        self.assertTrue(all(e["run_id"] == "pipe-test" for e in events))

    def test_cancel_stops_blocked_credentials_without_waiting_for_network(self):
        process = self.start_helper("credentials_hang")
        # Wait for the helper's actual credential boundary before cancelling.
        for _ in range(8):
            event = json.loads(process.stdout.readline())
            if event["event"] == "progress" and event["data"].get("stage") == "credentials":
                break
        else:
            self.fail("Credential boundary was not reached")
        start = time.monotonic()
        process.stdin.write(b'{"cancel":true,"run_id":"pipe-test"}\n')
        process.stdin.flush()
        self.assertEqual(process.wait(timeout=4), 2)
        self.assertLess(time.monotonic() - start, 3.5)
        output, _ = process.communicate()
        self.assertIn(b'"status": "cancelled"', output)

    def test_argument_validation_error_retains_the_callers_run_id(self):
        process = self.start_helper(arguments={"bindings": [{"operation": "set_value", "name": "Note"}]})
        output, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 1)
        event = json.loads(output)
        self.assertEqual(event["run_id"], "pipe-test")
        self.assertEqual(event["event"], "error")
        self.assertIn("explicit value", event["data"]["message"])

    def test_hard_deadline_survives_a_blocked_output_pipe(self):
        process = self.start_helper("blocked_output", seconds=5)
        # Deliberately leave stdout undrained: terminal emission cannot be a
        # prerequisite for killing a helper that exceeded the execution limit.
        self.assertEqual(process.wait(timeout=9), 2)


if __name__ == "__main__":
    unittest.main()
