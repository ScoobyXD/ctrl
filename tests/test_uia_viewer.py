"""Offline Qt/process contract tests; these do not claim native UIA coverage."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time
import unittest
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ctrl_loop.uia_viewer import UIAViewer


APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def eventually(predicate, timeout_ms=5000):
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate():
        APP.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Qt condition did not become true before timeout")
        QTest.qWait(5)
    APP.processEvents()


def snapshot():
    return {
        "schema_version": 1, "snapshot_id": "test-snapshot", "source": "windows-uia",
        "target": {"hwnd": 111, "pid": 222, "title": "Owned fixture"},
        "started_at": "2026-09-20T00:00:00Z", "elapsed_ms": 20,
        "status": "complete", "truncated": False, "warnings": [], "error": None,
        "elements": [
            {"id": "e1", "parent_id": None, "name": "Save draft", "role": "Button",
             "automation_id": "save", "class_name": "Button", "framework_id": "Win32",
             "value": None, "is_password": False, "enabled": True, "focused": False,
             "offscreen": False, "bounds": [10, 20, 100, 30], "patterns": ["Invoke"],
             "state": {}, "errors": []},
            {"id": "e2", "parent_id": "e1", "name": "", "role": "Edit",
             "automation_id": "", "class_name": "Edit", "framework_id": "Win32",
             "value": "Fixture text", "is_password": False, "enabled": None, "focused": True,
             "offscreen": None, "bounds": None, "patterns": ["Value"],
             "state": {"read_only": False}, "errors": []},
        ],
        "options": [
            {"id": "o1", "element_id": "e1", "operation": "activate", "label": "Save draft", "requires": {}},
            {"id": "o2", "element_id": "e2", "operation": "set_value", "label": "", "requires": {"value": "string"}},
        ],
    }


class ViewerProcessTests(unittest.TestCase):
    def setUp(self):
        # Keep generated helpers inside the isolated worktree. Python's private
        # Windows TEMP ACLs can be inaccessible to a sandboxed child process.
        self.folder = ROOT / ".runtime" / "viewer-tests" / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.helper = self.folder / "fake-helper.py"
        self.result = snapshot()
        self.write_helper()
        self.window = UIAViewer(self.helper, auto_discover=False, observe_timeout=1.0)
        self.window.show()
        self.window.target_combo.addItem("Owned fixture", {"hwnd": 111, "pid": 222, "title": "Owned fixture"})

    def tearDown(self):
        self.window.close()
        eventually(lambda: self.window.process is None)
        APP.processEvents()
        self.window.deleteLater()
        APP.processEvents()
        test_root = (ROOT / ".runtime" / "viewer-tests").resolve()
        resolved = self.folder.resolve()
        if not resolved.is_relative_to(test_root) or resolved == test_root:
            raise AssertionError("Refusing cleanup outside the generated test directory")
        shutil.rmtree(resolved)

    def write_helper(self, *, mode="success", result=None):
        if result is None:
            result = self.result
        # Fake helper is a real child process, allowing watchdog, crash, output
        # limits, and UI responsiveness to be exercised without target apps.
        code = (
            "import json, sys, time\n"
            f"mode = {mode!r}\n"
            f"result = json.loads({json.dumps(result)!r})\n"
            "if mode == 'hang': time.sleep(60)\n"
            "if mode == 'crash': sys.exit(2)\n"
            "if mode == 'invalid': print('not json'); sys.exit(0)\n"
            "if mode == 'flood': sys.stdout.write('x' * (8 * 1024 * 1024 + 1)); sys.exit(0)\n"
            "if sys.argv[1] == 'windows':\n"
            "    print(json.dumps({'windows': [{'hwnd': 111, 'pid': 222, 'title': 'Owned fixture'}]}))\n"
            "else:\n"
            "    print(json.dumps(result))\n"
        )
        self.helper.write_text(code, encoding="utf-8")

    def observe(self):
        self.window.observe()
        eventually(lambda: self.window.process is None)

    def test_windows_discovery_is_explicitly_separate_from_observation(self):
        self.window.refresh_windows()
        eventually(lambda: self.window.process is None)
        self.assertEqual(self.window.target_combo.count(), 1)
        self.assertEqual(self.window.target_combo.currentData()["hwnd"], 111)
        self.assertIsNone(self.window.snapshot)
        self.assertTrue(self.window.observe_button.isEnabled())

    def test_successful_options_are_readable_filterable_and_preview_only(self):
        self.observe()
        self.assertEqual(self.window.controls_model.rowCount(), 2)
        self.assertEqual(self.window.options_model.rowCount(), 2)
        self.assertIn("windows-uia", self.window.summary.text())
        self.assertIn("(No accessible name)", self.window.controls_model.rows[1])
        self.assertIn("Unavailable", self.window.controls_model.rows[1])
        self.window.search.setText("save")
        self.assertEqual(self.window.controls_proxy.rowCount(), 1)
        self.assertEqual(self.window.options_proxy.rowCount(), 1)
        self.window.tabs.setCurrentIndex(1)
        self.window.options_table.selectRow(0)
        detail = json.loads(self.window.details.toPlainText())
        self.assertEqual(detail["option"]["operation"], "activate")
        self.assertTrue(detail["preview_only"])
        self.assertIsNone(self.window.process)
        self.window.search.setText("no matching label")
        self.assertEqual(self.window.controls_proxy.rowCount(), 0)
        self.assertIn("0 / 2", self.window.tabs.tabText(0))

    def test_export_matches_snapshot_and_is_only_written_on_request(self):
        self.observe()
        output = self.folder / "saved.json"
        self.assertFalse(output.exists())
        self.assertTrue(self.window.export_json(path=output))
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), self.window.snapshot)

    def test_password_value_defense_in_depth_for_display_and_export(self):
        self.result["elements"][1].update(is_password=True, value="must-not-display", state={"value": "must-not-display", "read_only": False})
        self.write_helper()
        self.observe()
        self.assertIsNone(self.window.snapshot["elements"][1]["value"])
        self.assertNotIn("must-not-display", json.dumps(self.window.snapshot))
        output = self.folder / "saved.json"
        self.window.export_json(path=output)
        self.assertNotIn("must-not-display", output.read_text(encoding="utf-8"))

    def test_unknown_password_state_suppresses_values_and_state_is_readable(self):
        self.result["elements"][0]["state"] = {"toggle": 1, "selected": None, "expand_collapse": 0}
        self.result["elements"][1].update(is_password=None, value="must-not-display")
        self.write_helper()
        self.observe()
        self.assertIsNone(self.window.snapshot["elements"][1]["value"])
        self.assertIn("Toggle: On", self.window.controls_model.rows[0][6])
        self.assertIn("Collapsed", self.window.controls_model.rows[0][6])
        self.assertNotIn("null", self.window.controls_model.rows[0][6])
        self.assertEqual(self.window.snapshot["elements"][0]["state"]["toggle"], 1)

    def test_cancel_keeps_gui_responsive_and_releases_process(self):
        self.write_helper(mode="hang")
        ticks = []
        timer = QTimer(self.window)
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start(10)
        self.window.observe()
        eventually(lambda: len(ticks) >= 4)
        self.assertFalse(self.window.observe_button.isEnabled())
        self.window.cancel()
        eventually(lambda: self.window.process is None)
        self.assertIn("cancelled", self.window.status.text())
        self.assertTrue(self.window.observe_button.isEnabled())
        self.assertIsNone(self.window.snapshot)
        timer.stop()

    def test_immediate_cancel_during_process_start(self):
        self.write_helper(mode="hang")
        self.window.observe()
        self.window.cancel()
        eventually(lambda: self.window.process is None)
        self.assertIn("cancelled", self.window.status.text())

    def test_hung_helper_is_killed_by_deadline(self):
        self.write_helper(mode="hang")
        self.window.observe_timeout = 0.08
        self.window.deadline_slack_ms = 30
        self.observe()
        self.assertIn("timed out", self.window.status.text())
        self.assertTrue(self.window.observe_button.isEnabled())
        self.assertIsNone(self.window.snapshot)

    def test_windows_discovery_also_has_hard_deadline(self):
        self.write_helper(mode="hang")
        self.window.windows_timeout_ms = 100
        self.window.refresh_windows()
        eventually(lambda: self.window.process is None)
        self.assertIn("timed out", self.window.status.text())

    def test_closing_stops_helper_without_blocking(self):
        self.write_helper(mode="hang")
        self.window.observe()
        before = time.monotonic()
        self.window.close()
        self.assertLess(time.monotonic() - before, 0.25)
        eventually(lambda: self.window.process is None)
        self.assertFalse(self.window.isVisible())

    def test_repeated_refresh_and_target_change_invalidate_snapshot(self):
        self.observe()
        self.assertIsNotNone(self.window.snapshot)
        self.window.observe()
        self.assertIsNone(self.window.snapshot)
        self.assertEqual(self.window.options_model.rowCount(), 0)
        self.assertFalse(self.window.export_button.isEnabled())
        eventually(lambda: self.window.process is None)
        self.assertIsNotNone(self.window.snapshot)
        self.window.target_combo.addItem("Other target", {"hwnd": 333, "pid": 444, "title": "Other target"})
        self.window.target_combo.setCurrentIndex(1)
        self.assertIsNone(self.window.snapshot)
        self.assertEqual(self.window.controls_model.rowCount(), 0)

    def test_programmatic_target_change_cancels_inflight_observation(self):
        self.write_helper(mode="hang")
        self.window.observe()
        self.window.target_combo.addItem("Other", {"hwnd": 333, "pid": 444, "title": "Other"})
        self.window.target_combo.setCurrentIndex(1)
        eventually(lambda: self.window.process is None)
        self.assertIsNone(self.window.snapshot)
        self.assertIn("Target changed", self.window.status.text())

    def test_wrong_target_response_rejected(self):
        self.result["target"]["pid"] = 999
        self.write_helper()
        self.observe()
        self.assertIsNone(self.window.snapshot)
        self.assertIn("invalid response", self.window.status.text())

    def test_invalid_response_and_error_clear_previous_result(self):
        self.observe()
        self.write_helper(mode="invalid")
        self.observe()
        self.assertIsNone(self.window.snapshot)
        self.assertIn("invalid response", self.window.status.text())
        self.result.update(status="error", error="Target window is no longer available.", elements=[], options=[])
        self.write_helper()
        self.observe()
        self.assertIn("no longer available", self.window.status.text())
        self.assertEqual(self.window.options_model.rowCount(), 0)
        self.assertIsNone(self.window.snapshot)
        self.assertNotIn("Snapshot ready", self.window.status.text())

    def test_error_with_records_never_displays_candidates_as_current(self):
        self.result.update(status="error", error="Target closed during observation.")
        self.write_helper()
        self.observe()
        self.assertIsNone(self.window.snapshot)
        self.assertEqual(self.window.options_model.rowCount(), 0)
        self.assertIn("Observation failed", self.window.status.text())

    def test_partial_and_empty_results_are_explicit(self):
        self.result.update(status="partial", truncated=True, warnings=["Element budget reached."])
        self.write_helper()
        self.observe()
        self.assertIn("PARTIAL", self.window.summary.text())
        self.assertIn("incomplete", self.window.status.text())
        self.assertIn("Element budget reached", self.window.status.text())
        self.result.update(status="complete", truncated=False, warnings=[], elements=[], options=[])
        self.write_helper()
        self.observe()
        self.assertIn("No accessible controls", self.window.status.text())

    def test_crash_and_output_limit_recover(self):
        self.write_helper(mode="crash")
        self.observe()
        self.assertIsNone(self.window.snapshot)
        self.assertTrue(self.window.observe_button.isEnabled())
        self.write_helper(mode="flood")
        self.observe()
        self.assertIn("8 MiB", self.window.status.text())
        self.assertIsNone(self.window.snapshot)
        self.write_helper()
        self.observe()
        self.assertIsNotNone(self.window.snapshot)


if __name__ == "__main__":
    unittest.main()
