"""Offline execution UI protocol tests; fake helpers never deliver desktop input."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest.mock import patch

from test_uia_selection_viewer import APP, ROOT, eventually, fixture_snapshot
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from ctrl_loop.uia_viewer import UIAViewer


class ExecutionViewerTests(unittest.TestCase):
    def setUp(self):
        self.folder = ROOT / ".runtime" / "execution-viewer-tests" / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.helper = self.folder / "fake-execution.py"
        self.write_helper()
        self.window = UIAViewer(auto_discover=False, execution_helper_path=self.helper,
                                execution_python=sys.executable, selection_python=sys.executable)
        self.window.target_combo.addItem("Owned fixture", fixture_snapshot()["target"])
        self.window.execution_goal.setPlainText("Save the café draft <once>")
        self.window.show()
        self.window.tabs.setCurrentIndex(3)

    def tearDown(self):
        self.window.close()
        eventually(lambda: self.window.execution_process is None)
        self.window.deleteLater()
        APP.processEvents()
        root = (ROOT / ".runtime" / "execution-viewer-tests").resolve()
        folder = self.folder.resolve()
        if folder == root or not folder.is_relative_to(root):
            raise AssertionError("Unsafe cleanup path")
        shutil.rmtree(folder)

    def write_helper(self, mode="success"):
        body = r'''
import json, sys, time
assert sys.argv[1:] == ["--stdin"]
request = json.loads(sys.stdin.buffer.readline().decode("utf-8"))
assert "snapshot" not in request
assert request["target"] == {"hwnd": 111, "pid": 222}
def emit(kind, data):
    event = {"event": kind, "run_id": request["run_id"], "data": data}
    if mode == "wrong_run": event["run_id"] = "old-run"
    sys.stdout.buffer.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
def observe(number):
    snapshot["snapshot_id"] = "fresh-" + str(number)
    emit("observation", {"snapshot": snapshot, "step": number})
def result(status="limit_reached", count=2):
    emit("result", {"status": status, "reason": "Fixture stopped at its bound.",
                    "input_actions": count, "attempted_actions": count, "goal_completed": False})
emit("progress", {"step": 0, "stage": "observe"})
if mode == "password":
    snapshot["elements"][0].update(is_password=True, value="PASSWORD_SENTINEL", state={"value": "PASSWORD_SENTINEL"})
if mode == "hang": time.sleep(60)
if mode == "invalid": print("not json", flush=True); time.sleep(60)
if mode == "polite_cancel":
    cancel = json.loads(sys.stdin.buffer.readline().decode("utf-8"))
    assert cancel == {"cancel": True, "run_id": request["run_id"]}
    result("cancelled", 0); sys.exit(0)
observe(0)
for step in range(1, 3):
    emit("selection", {"step": step, "selection": {
        "snapshot_id": snapshot["snapshot_id"], "goal": request["goal"], "status": "selected",
        "input_actions": 0, "selected_option": snapshot["options"][0]}})
    if mode == "selection_only": sys.exit(2)
    action = {"step": step, "operation": "activate", "status": "delivered", "goal_completed": False}
    if mode == "not_attempted_then_crash": action.update(status="failed", receipt={"attempted": False})
    if mode == "unknown_then_crash": action.update(status="unknown", receipt={"attempted": True})
    emit("action", action)
    observe(step)
    action.update(observed_change=True, after_snapshot_id=snapshot["snapshot_id"])
    emit("action", action)
    if mode in {"receipt_then_crash", "not_attempted_then_crash", "unknown_then_crash"}: sys.exit(2)
if mode == "error": emit("error", {"message": "Fixture error"}); sys.exit(1)
result()
if mode == "result_nonzero": sys.exit(1)
if mode == "result_then_hang": time.sleep(60)
'''
        self.helper.write_text("mode = " + repr(mode) + "\nsnapshot = " + repr(fixture_snapshot()) + "\n" + body,
                               encoding="utf-8")

    def start(self):
        QTest.mouseClick(self.window.execution_button, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(self.window.execution_process)

    def finish(self):
        self.start()
        eventually(lambda: self.window.execution_process is None)

    def test_explicit_start_new_observations_and_receipts(self):
        self.assertIsNone(self.window.execution_process)
        self.assertIsNone(self.window.snapshot)
        self.assertTrue(self.window.execution_button.isEnabled())
        self.window.execution_arguments.setPlainText('{"bindings":[{"operation":"set_value","name":"Draft","value":"café"}]}')
        self.finish()
        self.assertEqual(self.window.execution_result["input_actions"], 2)
        self.assertFalse(self.window.execution_result["goal_completed"])
        self.assertEqual(self.window.snapshot["snapshot_id"], "fresh-2")
        self.assertIn("prediction", self.window.execution_trace.toPlainText())
        self.assertIn("Input outcome: delivered", self.window.execution_trace.toPlainText())
        self.assertIn("Goal completion is not established", self.window.execution_status.text())
        self.assertIsNone(self.window.selection_result)
        output = self.folder / "execution.json"
        self.assertTrue(self.window.export_execution_json(path=output))
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved["request"]["goal"], "Save the café draft <once>")
        self.assertEqual(saved["request"]["arguments"]["bindings"][0]["value"], "café")
        self.assertEqual(saved["events"][-1]["event"], "result")

    def test_invalid_arguments_do_not_start(self):
        for text in ("[1]", "{bad}", '{"value":NaN}', "x" * 65537):
            self.window.execution_arguments.setPlainText(text)
            self.window.start_execution()
            self.assertIsNone(self.window.execution_process)
            self.assertIn("No execution started", self.window.execution_status.text())

    def test_selection_is_not_delivery(self):
        self.write_helper("selection_only")
        self.finish()
        self.assertEqual(self.window.execution_result["input_actions"], 0)
        self.assertTrue(self.window.execution_result["unreported_outcome_possible"])
        self.assertIn("prediction", self.window.execution_trace.toPlainText())

    def test_invalid_wire_arguments_never_create_process_and_valid_retry_runs(self):
        snapshot = fixture_snapshot()
        self.window._show_snapshot(snapshot, snapshot["target"])
        for text in (
            '{"bindings":[{"operation":"set_range_value","name":"Volume","value":1e309}]}',
            '{"bindings":[{"operation":"set_value","name":"Draft","value":"\\ud800"}]}',
            '{"bindings":[{"operation":"set_value","value":"Missing target"}]}',
        ):
            with self.subTest(arguments=text):
                self.window.execution_arguments.setPlainText(text)
                with patch("ctrl_loop.uia_viewer.QProcess", side_effect=AssertionError("No process before validation")):
                    self.window.start_execution()
                self.assertIsNone(self.window.execution_process)
                self.assertIsNone(self.window._execution_request)
                self.assertEqual(self.window.snapshot["snapshot_id"], snapshot["snapshot_id"])
                self.assertTrue(self.window.execution_button.isEnabled())
                self.assertFalse(self.window.execution_stop_button.isEnabled())
                self.assertIn("No execution started", self.window.execution_status.text())
        self.window.execution_arguments.setPlainText("{}")
        self.finish()
        self.assertEqual(self.window.execution_result["status"], "limit_reached")

    def test_valid_final_result_survives_nonzero_exit(self):
        self.write_helper("result_nonzero")
        self.finish()
        self.assertEqual(self.window.execution_result["status"], "limit_reached")
        self.assertEqual(self.window.execution_result["input_actions"], 2)
        self.assertNotIn("source", self.window.execution_result)

    def test_execution_observation_export_preserves_password_defense(self):
        self.write_helper("password")
        self.finish()
        output = self.folder / "redacted.json"
        self.assertTrue(self.window.export_execution_json(path=output))
        self.assertNotIn("PASSWORD_SENTINEL", output.read_text(encoding="utf-8"))
        self.assertIsNone(self.window.snapshot["elements"][0]["value"])

    def test_crash_preserves_single_receipt_despite_observation_update(self):
        self.write_helper("receipt_then_crash")
        self.finish()
        self.assertEqual(self.window.execution_result["input_actions"], 1)
        self.assertEqual(self.window.execution_result["attempted_actions"], 1)
        self.assertIn("unknown outcome", self.window.execution_status.text())

    def test_crash_attempt_count_excludes_explicit_non_attempts(self):
        for mode, attempted in (("not_attempted_then_crash", 0), ("unknown_then_crash", 1)):
            with self.subTest(mode=mode):
                self.write_helper(mode)
                self.finish()
                self.assertEqual(self.window.execution_result["attempted_actions"], attempted)
                self.assertEqual(self.window.execution_result["input_actions"], 0)
                self.assertTrue(self.window.execution_result["unreported_outcome_possible"])

    def test_graceful_cancel_protocol(self):
        self.write_helper("polite_cancel")
        self.start()
        eventually(lambda: len(self.window.execution_events) > 0)
        self.assertFalse(self.window.observe_button.isEnabled())
        self.assertFalse(self.window.selection_button.isEnabled())
        self.assertFalse(self.window.execution_arguments.isEnabled())
        QTest.mouseClick(self.window.execution_stop_button, Qt.MouseButton.LeftButton)
        eventually(lambda: self.window.execution_process is None)
        self.assertEqual(self.window.execution_result["status"], "cancelled")
        self.assertEqual(self.window.execution_result["input_actions"], 0)
        self.assertTrue(self.window.execution_button.isEnabled())

    def test_goal_change_cancels_and_shares_goal(self):
        self.write_helper("polite_cancel")
        self.start()
        eventually(lambda: len(self.window.execution_events) > 0)
        self.window.selection_goal.setPlainText("Different goal")
        eventually(lambda: self.window.execution_process is None)
        self.assertEqual(self.window.execution_goal.toPlainText(), "Different goal")
        self.assertIn("Goal changed", self.window.execution_status.text())

    def test_target_change_cancels_without_reusing_previous_observation(self):
        self.write_helper("polite_cancel")
        self.start()
        eventually(lambda: len(self.window.execution_events) > 0)
        self.window.target_combo.addItem("Other target", {"hwnd": 333, "pid": 444, "title": "Other"})
        self.window.target_combo.setCurrentIndex(1)
        eventually(lambda: self.window.execution_process is None)
        self.assertIsNone(self.window.snapshot)
        self.assertIn("Target changed", self.window.execution_status.text())

    def test_wrong_run_and_invalid_events_stop(self):
        for mode in ("wrong_run", "invalid"):
            self.write_helper(mode)
            self.finish()
            self.assertIn("invalid or stale", self.window.execution_status.text())
            self.assertEqual(self.window.execution_result["input_actions"], 0)

    def test_helper_deadline_and_late_result_not_final_until_exit(self):
        self.write_helper("result_then_hang")
        self.start()
        eventually(lambda: self.window._execution_pending_result is not None)
        self.assertIsNone(self.window.execution_result)
        self.window._execution_deadline.start(20)
        eventually(lambda: self.window.execution_process is None)
        self.assertIn("timed out", self.window.execution_status.text())

    def test_immediate_stop_and_close_are_responsive(self):
        self.write_helper("hang")
        self.start()
        self.window.stop_execution()
        eventually(lambda: self.window.execution_process is None)
        self.start()
        self.window.close()
        eventually(lambda: self.window.execution_process is None)
        self.assertFalse(self.window.isVisible())

    def test_page_scroll_and_no_summary_regression(self):
        self.assertIs(self.window.centralWidget(), self.window.page_scroll)
        self.assertNotIn("Summary", [self.window.selection_tabs.tabText(i)
                                     for i in range(self.window.selection_tabs.count())])
        APP.processEvents()
        self.assertGreater(self.window.page_scroll.verticalScrollBar().maximum(), 0)


if __name__ == "__main__":
    unittest.main()
