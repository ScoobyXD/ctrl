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
from ctrl_loop import credentials, cua_driver, execution_runtime, selection, jev
from ctrl_loop.uia_contract import new_snapshot

browser = {"hwnd": 321, "pid": 654, "title": "Home / X - Google Chrome"}
editor = {"hwnd": 111, "pid": 222, "title": "Commands.txt - Notepad++"}

def windows(**kwargs):
    if MODE == "empty": return {"windows": []}
    return {"windows": [editor, browser]}

def observe(target, **kwargs):
    if MODE.startswith("auto"):
        assert target == browser
    snapshot = new_snapshot(target["hwnd"], target["pid"])
    snapshot["target"].update(target)
    return snapshot

class Executor:
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass

def key():
    if MODE == "empty": raise AssertionError("Empty inventory must not retrieve credentials")
    if MODE == "blocked_output":
        sys.stdout.write("x" * 1000000)
        sys.stdout.flush()
    if MODE in ("credentials_hang", "blocked_output"):
        time.sleep(60)
    return "offline-fixture-key"

def run(self, *args, **kwargs):
    if MODE in ("credentials_hang", "blocked_output"):
        args[0]({}, request_id="blocked", capture_id="blocked")
    return {"status": "no_match", "snapshot_id": self.snapshot_id, "goal": self.goal,
            "input_actions": 0, "selected_option": None}

def decide(payload, key, **ids):
    if MODE == "auto_model_hang": time.sleep(60)
    question_id, question = next(iter(payload["questions"].items()))
    # Inventory order is deliberate: the unrelated editor comes first.
    choices = [name for name in question["criteria"] if not name.startswith("__")]
    choice = "__none__" if MODE == "auto_no_match" else choices[1]
    if MODE == "auto_ambiguous": choice = "__ambiguous__"
    return {"model": jev.RESOLVED_MODEL, "provider": jev.PROVIDER,
            "provider_request_id": "gen-offline", "latency_ms": 0, "usage": {},
            "local_request": jev.request_binding(payload, **ids),
            "answers": {question_id: {"type": "choice", "choice": choice,
                "probabilities": {name: float(name == choice) for name in question["criteria"]},
                "confidence": 1}}}

credentials.get_openrouter_key = key
cua_driver.NativeCuaExecutor = Executor
execution_runtime.observe_target = observe
execution_runtime.observe_windows = windows
selection.SelectionSearch.run = run
jev.decide = decide
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
        self.process = subprocess.Popen([sys.executable, "-B", "-I", str(wrapper)], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        request = {"target": {"hwnd": 1, "pid": 2}, "goal": "Synthetic test", "run_id": "pipe-test",
                   "max_steps": 1, "max_seconds": seconds}
        request.update(updates)
        if mode.startswith("auto") or mode == "empty": request.pop("target")
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

    def test_automatic_helper_chooses_browser_before_control_selection(self):
        process = self.start_helper("auto")
        output, errors = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, errors)
        events = [json.loads(line) for line in output.splitlines()]
        choice = next(e for e in events if e["event"] == "window_selection")
        self.assertEqual(choice["data"]["selection"]["selected_window"]["hwnd"], 321)
        observations = [e for e in events if e["event"] == "observation"]
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["data"]["snapshot"]["target"]["hwnd"], 321)
        self.assertEqual(events[-1]["data"]["target"]["hwnd"], 321)
        self.assertEqual(events[-1]["data"]["input_actions"], 0)

    def test_automatic_empty_inventory_needs_no_credentials_or_observation(self):
        process = self.start_helper("empty")
        output, errors = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, errors)
        events = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(events[-1]["data"]["status"], "no_window")
        self.assertFalse(any(e["event"] == "observation" for e in events))
        self.assertFalse(any(e["data"].get("stage") == "credentials" for e in events))

    def test_automatic_no_match_never_inspects_controls(self):
        process = self.start_helper("auto_no_match")
        output, errors = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, errors)
        events = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(events[-1]["data"]["status"], "no_window")
        self.assertFalse(any(e["event"] == "observation" for e in events))

    def test_automatic_ambiguity_never_inspects_controls(self):
        process = self.start_helper("auto_ambiguous")
        output, errors = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, errors)
        events = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(events[-1]["data"]["status"], "ambiguous_window")
        self.assertFalse(any(e["event"] == "observation" for e in events))

    def test_cancel_while_window_model_is_blocked_exits_without_observation(self):
        process = self.start_helper("auto_model_hang")
        for _ in range(8):
            event = json.loads(process.stdout.readline())
            if event["event"] == "progress" and event["data"].get("stage") == "credentials":
                break
        else:
            self.fail("Window decision boundary was not reached")
        process.stdin.write(b'{"cancel":true,"run_id":"pipe-test"}\n')
        process.stdin.flush()
        output, _ = process.communicate(timeout=4)
        events = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(events[-1]["data"]["status"], "cancelled")
        self.assertFalse(any(e["event"] == "observation" for e in events))


if __name__ == "__main__":
    unittest.main()
