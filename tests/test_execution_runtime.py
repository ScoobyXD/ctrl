"""Offline process-boundary tests; no native windows are enumerated."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ctrl_loop.execution import ExecutionError
from ctrl_loop.execution_runtime import observe_target, observe_windows


class ExecutionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.process = Mock(returncode=0)
        self.process.poll.return_value = 0
        self.process.communicate.return_value = (b'{"windows": []}', b'')
        self.spawn = patch("ctrl_loop.execution_runtime.subprocess.Popen", return_value=self.process)
        self.job_patch = patch("ctrl_loop.cua_driver.KillOnCloseJob")
        self.popen = self.spawn.start()
        self.job = self.job_patch.start().return_value
        self.addCleanup(self.spawn.stop)
        self.addCleanup(self.job_patch.stop)

    def test_inventory_uses_disposable_helper_without_target_or_tree(self):
        result = observe_windows(python=sys.executable)
        self.assertEqual(result, {"windows": []})
        command = self.popen.call_args.args[0]
        self.assertEqual(command[-1], "windows")
        self.assertNotIn("observe", command)
        self.assertNotIn("--hwnd", command)
        self.job.assign.assert_called_once_with(self.process)
        self.job.close.assert_called_once()

    def test_control_observation_still_forwards_identity_and_limits(self):
        observe_target({"hwnd": 101, "pid": 202}, python=sys.executable,
                       max_elements=42, timeout=3)
        self.assertEqual(self.popen.call_args.args[0][-9:],
                         ["observe", "--hwnd", "101", "--pid", "202",
                          "--max-elements", "42", "--timeout", "3"])

    def test_cancellation_before_spawn(self):
        with self.assertRaises(InterruptedError):
            observe_windows(python=sys.executable, cancelled=lambda: True)
        self.popen.assert_not_called()

    def test_cancellation_kills_owned_inventory_child(self):
        checks = iter([False, False, True])
        self.process.poll.return_value = None
        self.process.communicate.side_effect = [subprocess.TimeoutExpired("fake", .1), (b"", b"")]
        with self.assertRaises(InterruptedError):
            observe_windows(python=sys.executable, cancelled=lambda: next(checks))
        self.process.kill.assert_called_once()
        self.job.close.assert_called_once()

    def test_inventory_deadline_kills_child_and_closes_job(self):
        self.process.poll.return_value = None
        with patch("ctrl_loop.execution_runtime.time.monotonic", side_effect=[0, 10]):
            with self.assertRaisesRegex(ExecutionError, "deadline"):
                observe_windows(python=sys.executable)
        self.process.kill.assert_called_once()
        self.job.close.assert_called_once()

    def test_failed_invalid_and_oversized_output_are_not_inventory(self):
        for code, output in ((2, b'{}'), (0, b'private invalid response'),
                             (0, b' ' * (8 * 1024 * 1024 + 1))):
            with self.subTest(returncode=code, size=len(output)):
                self.process.returncode = code
                self.process.communicate.return_value = (output, b'private provider stderr')
                with self.assertRaises(ExecutionError) as error:
                    observe_windows(python=sys.executable)
                self.assertNotIn("private", str(error.exception))


if __name__ == "__main__":
    unittest.main()
