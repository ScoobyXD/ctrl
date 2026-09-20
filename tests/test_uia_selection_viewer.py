"""Offline Jev-preview GUI contracts using expendable local fake helpers.

No API requests or input actions against target applications occur here.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import sys
import time
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ctrl_loop.uia_viewer import UIAViewer
from ctrl_loop.selection import SelectionSearch


APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def eventually(predicate, timeout_ms=5000):
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate():
        APP.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Qt selection condition timed out")
        QTest.qWait(5)
    APP.processEvents()


def fixture_snapshot():
    return {
        "schema_version": 1, "snapshot_id": "owned-selection-snapshot", "source": "windows-uia",
        "target": {"hwnd": 111, "pid": 222, "title": "Owned selection fixture"},
        "status": "complete", "truncated": False, "warnings": [], "error": None, "elapsed_ms": 20,
        "elements": [{"id": "element-save", "name": "Save <draft> café", "role": "Button",
                      "value": None, "is_password": False, "enabled": True, "focused": False,
                      "offscreen": False, "state": {}, "patterns": ["Invoke"], "bounds": [0, 0, 100, 25]}],
        "options": [{"id": "option-save", "element_id": "element-save", "operation": "activate",
                     "label": "Save <draft> café", "requires": {}}],
    }


class SelectionViewerTests(unittest.TestCase):
    def setUp(self):
        self.folder = ROOT / ".runtime" / "selection-viewer-tests" / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.helper = self.folder / "fake-selection.py"
        self.write_helper()
        self.window = UIAViewer(auto_discover=False, selection_helper_path=self.helper,
                                selection_python=sys.executable, selection_timeout_ms=3000)
        self.window.show()
        self.data = fixture_snapshot()
        self.window.target_combo.addItem("Owned selection fixture", self.data["target"])
        self.window._show_snapshot(self.data, self.data["target"])
        self.window.tabs.setCurrentIndex(2)
        self.window.selection_goal.setPlainText("Save the café draft <once>")

    def tearDown(self):
        self.window.close()
        eventually(lambda: self.window.selection_process is None and self.window.process is None)
        self.window.deleteLater()
        APP.processEvents()
        root = (ROOT / ".runtime" / "selection-viewer-tests").resolve()
        resolved = self.folder.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise AssertionError("Unsafe test cleanup path")
        shutil.rmtree(resolved)

    def write_helper(self, mode="success"):
        body = r'''
import json, sys, time
request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
assert sys.argv[1:] == ["--stdin", "--live"]
def emit(event, data=None, message=None):
    value = {"event": event}
    if data is not None: value["data"] = data
    if message is not None: value["message"] = message
    sys.stdout.buffer.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()
progress = {"stage": "classify", "round": 1, "batch": 1, "request_count": 1,
            "input_ids": ["option-save"], "retained_ids": ["option-save"],
            "answers": {"option-save": "high relevance"}, "client_run_id": request["run_id"]}
payload = {"model": "typesafe/jev-1.13",
    "provider": {"only": ["typesafe"], "order": ["typesafe"], "allow_fallbacks": False},
    "state": {"goal": request["goal"], "snapshot_id": request["snapshot"]["snapshot_id"],
              "stage": "classify", "round": 1, "batch": 1, "candidates": [{"id": "option-save"}]},
    "questions": {"classify_0": {"type": "choice", "criteria": {"promising": "Relevant", "unlikely": "Unrelated"}, "instructions": "Classify"}}}
wire = {"request_id": "fixture-request", "capture_id": request["snapshot"]["snapshot_id"],
        "stage": "classify", "round": 1, "batch": 1, "input_ids": ["option-save"],
        "payload": payload, "body_bytes": len(json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()),
        "request_count": 1, "client_run_id": request["run_id"]}
if mode == "wrong_request_size": wire["body_bytes"] += 1
if mode == "wrong_request_goal": payload["state"]["goal"] = "different goal"
emit("request", wire)
progress.update(request_id="fixture-request", usage={"input_tokens": 10, "output_tokens": 3, "cost": 0.0})
emit("progress", progress)
if mode == "hang": time.sleep(60)
if mode == "error": emit("error", message="Fixture provider rejected the request."); sys.exit(1)
if mode == "crash": sys.exit(2)
if mode == "invalid": sys.stdout.write("invalid JSON\n"); sys.stdout.flush(); sys.exit(0)
if mode == "flood": sys.stdout.buffer.write(b'x' * (8 * 1024 * 1024 + 1)); sys.stdout.buffer.flush(); sys.exit(0)
result = {"snapshot_id": request["snapshot"]["snapshot_id"], "goal": request["goal"],
          "run_id": "core-generated-run", "client_run_id": request["run_id"], "status": "selected",
          "selected_option": request["snapshot"]["options"][0], "trace": [progress],
          "request_count": 2, "observed_options": 1, "considered_options": 1,
          "usage": {"input_tokens": 10, "output_tokens": 3}, "preview_only": True, "input_actions": 0}
if mode == "wrong_snapshot": result["snapshot_id"] = "stale-snapshot"
if mode == "wrong_goal": result["goal"] = "old goal"
if mode == "wrong_run": result["client_run_id"] = "old-run"
if mode == "invented_option": result["selected_option"] = {"id": "invented", "element_id": "element-save", "operation": "activate"}
if mode == "unsafe_action": result["input_actions"] = 1
if mode == "no_match": result.update(status="no_match", selected_option=None, reason="No matching observed control")
if mode == "budget": result.update(status="budget_exhausted", selected_option=None, reason="Request budget reached")
emit("result", result)
if mode == "result_then_hang": time.sleep(60)
'''
        self.helper.write_text("mode = " + repr(mode) + "\n" + body, encoding="utf-8")

    def start(self):
        QTest.mouseClick(self.window.selection_button, Qt.MouseButton.LeftButton)
        self.assertIsNotNone(self.window.selection_process)

    def finish(self):
        self.start()
        eventually(lambda: self.window.selection_process is None)

    def test_explicit_start_only_and_utf8_stdin_success(self):
        self.assertIsNone(self.window.selection_process)
        self.assertIsNone(self.window.selection_result)
        self.assertTrue(self.window.selection_button.isEnabled())
        self.finish()
        self.assertEqual(self.window.selection_result["goal"], "Save the café draft <once>")
        self.assertEqual(self.window.selection_result["selected_option"]["id"], "option-save")
        self.assertIn("Save <draft> café", self.window.selection_choice.text())
        self.assertEqual(self.window.selection_choice.textFormat(), Qt.TextFormat.PlainText)
        self.assertIn("Round 1", self.window.selection_trace.toPlainText())
        self.assertIn("Buckets", self.window.selection_trace.toPlainText())
        self.assertEqual(self.window.selection_result["input_actions"], 0)
        self.assertIsNone(self.window.process)
        self.assertEqual(list(self.folder.iterdir()), [self.helper])
        output = self.folder / "selection.json"
        self.assertTrue(self.window.export_selection_json(path=output))
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), self.window.selection_result)

    def test_offline_inspection_uses_actual_builder_without_aws_or_subprocess(self):
        # The real selector requires snapshot-scoped IDs; old helper fixtures
        # above deliberately exercise only the viewer boundary.
        observed = copy.deepcopy(self.data)
        element_id = observed["snapshot_id"] + ":e0"
        option_id = element_id + ":activate"
        observed["elements"][0]["id"] = element_id
        observed["options"][0].update(id=option_id, element_id=element_id)
        self.window._show_snapshot(observed, observed["target"])
        with patch("ctrl_loop.credentials.get_openrouter_key", side_effect=AssertionError("AWS forbidden")):
            QTest.mouseClick(self.window.selection_inspect_button, Qt.MouseButton.LeftButton)
        self.assertIsNone(self.window.selection_process)
        self.assertEqual(self.window.selection_tabs.currentIndex(), 1)
        record = self.window.selection_requests[0]
        expected = SelectionSearch(observed, self.window.selection_goal.toPlainText()).preview()["initial_requests"][0]["payload"]
        self.assertEqual(record["payload"], expected)
        self.assertEqual(json.loads(self.window.request_json.toPlainText()), expected)
        self.assertEqual(record["status"], "Planned — not sent")
        self.assertIn("bytes are not tokens", self.window.request_summary.text())
        self.window.request_wire.setChecked(True)
        wire = json.dumps(expected, allow_nan=False, separators=(",", ":")).encode()
        self.assertEqual(self.window.request_json.toPlainText().encode(), wire)
        output = self.folder / "request.json"
        self.assertTrue(self.window.export_request_body(path=output))
        self.assertEqual(output.read_bytes(), wire)
        self.window.copy_request_body()
        self.assertEqual(APP.clipboard().text().encode(), wire)
        self.assertIsNotNone(self.window.request_dialog)
        self.window.request_dialog.close()
        self.assertIsNone(self.window.request_dialog)
        self.assertIs(self.window.selection_tabs.widget(1), self.window.request_panel)
        self.window.open_request_inspector()
        self.assertIsNotNone(self.window.request_dialog)
        self.assertEqual(self.window.request_json.toPlainText().encode(), wire)
        self.window.selection_goal.setPlainText("Another goal")
        self.assertEqual(self.window.selection_requests, [])
        self.assertEqual(self.window.request_json.toPlainText(), "")

    def test_request_records_survive_failure_and_cancellation_and_export_all(self):
        for mode in ("error", "hang"):
            with self.subTest(mode=mode):
                self.write_helper(mode)
                self.start()
                eventually(lambda: bool(self.window.selection_requests))
                if mode == "hang":
                    self.window.cancel_selection()
                eventually(lambda: self.window.selection_process is None)
                self.assertIsNone(self.window.selection_result)
                self.assertEqual(len(self.window.selection_requests), 1)
                record = self.window.selection_requests[0]
                self.assertEqual(record["status"], "Dispatch attempted")
                self.assertEqual(record["payload"]["state"]["goal"], self.window.selection_goal.toPlainText())
                self.assertIn("10 input tokens", self.window.request_summary.text())
                output = self.folder / "requests.json"
                self.assertTrue(self.window.export_requests_json(path=output))
                archive = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(archive["requests"], self.window.selection_requests)
                self.assertNotIn("Authorization", output.read_text(encoding="utf-8"))

    def test_invalid_request_records_are_rejected_before_display(self):
        for mode in ("wrong_request_size", "wrong_request_goal"):
            self.write_helper(mode)
            self.finish()
            self.assertEqual(self.window.selection_requests, [])
            self.assertEqual(self.window.request_json.toPlainText(), "")
            self.assertIn("invalid or stale", self.window.selection_status.text())

    def test_request_usage_refresh_preserves_text_selection(self):
        self.write_helper("hang")
        self.start()
        eventually(lambda: bool(self.window.selection_requests))
        editor = self.window.request_json
        cursor = editor.textCursor()
        cursor.setPosition(5)
        cursor.setPosition(11, QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        selected = cursor.selectedText()
        event = {"event": "progress", "data": {"request_id": "fixture-request",
                 "client_run_id": self.window._selection_request["run_id"],
                 "usage": {"input_tokens": 20, "output_tokens": 4, "cost": 0.0}}}
        self.window._selection_event(json.dumps(event).encode())
        self.assertEqual(editor.textCursor().selectedText(), selected)
        self.assertEqual(editor.textCursor().position(), 11)
        self.assertIn("20 input tokens", self.window.request_summary.text())
        self.window.cancel_selection()
        eventually(lambda: self.window.selection_process is None)

    def test_progress_responsive_and_cancel_releases_process(self):
        self.write_helper("hang")
        ticks = []
        timer = QTimer(self.window)
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)
        self.start()
        eventually(lambda: "Round 1" in self.window.selection_trace.toPlainText() and len(ticks) >= 3)
        self.assertFalse(self.window.selection_button.isEnabled())
        self.window.cancel_selection()
        eventually(lambda: self.window.selection_process is None)
        timer.stop()
        self.assertIsNone(self.window.selection_result)
        self.assertIn("provider requests may still finish", self.window.selection_status.text())
        self.assertTrue(self.window.selection_button.isEnabled())

    def test_immediate_cancel_during_start(self):
        self.write_helper("hang")
        self.start()
        self.window.cancel_selection()
        eventually(lambda: self.window.selection_process is None)
        self.assertIn("cancelled", self.window.selection_status.text())

    def test_goal_change_cancels_pending_result_and_prevents_late_choice(self):
        self.write_helper("result_then_hang")
        self.start()
        eventually(lambda: self.window._selection_pending_result is not None)
        self.assertIsNone(self.window.selection_result)
        self.window.selection_goal.setPlainText("A different goal")
        eventually(lambda: self.window.selection_process is None)
        self.assertIsNone(self.window.selection_result)
        self.assertIn("No current selected action", self.window.selection_choice.text())
        self.assertEqual(self.window.selection_json.toPlainText(), "")

    def test_target_change_cancels_and_clears_choice(self):
        self.write_helper("hang")
        self.start()
        self.window.target_combo.addItem("Other target", {"hwnd": 333, "pid": 444, "title": "Other"})
        self.window.target_combo.setCurrentIndex(1)
        eventually(lambda: self.window.selection_process is None)
        self.assertIsNone(self.window.selection_result)
        self.assertFalse(self.window.selection_button.isEnabled())

    def test_snapshot_replacement_invalidates_completed_and_pending_selection(self):
        self.finish()
        self.assertIsNotNone(self.window.selection_result)
        changed = copy.deepcopy(self.data)
        changed["snapshot_id"] = "new-snapshot"
        self.window._show_snapshot(changed, changed["target"])
        self.assertIsNone(self.window.selection_result)
        self.assertFalse(self.window.selection_export_button.isEnabled())
        self.write_helper("hang")
        self.start()
        self.window._clear_snapshot()
        eventually(lambda: self.window.selection_process is None)
        self.assertIsNone(self.window.selection_result)

    def test_result_binding_and_action_integrity_reject_bad_responses(self):
        for mode in ("wrong_snapshot", "wrong_goal", "wrong_run", "invented_option", "unsafe_action"):
            with self.subTest(mode=mode):
                self.write_helper(mode)
                self.finish()
                self.assertIsNone(self.window.selection_result)
                self.assertIn("invalid or stale", self.window.selection_status.text())
                self.assertEqual(self.window.selection_json.toPlainText(), "")

    def test_error_malformed_and_crash_are_recoverable(self):
        for mode in ("error", "invalid", "crash"):
            with self.subTest(mode=mode):
                self.write_helper(mode)
                self.finish()
                self.assertIsNone(self.window.selection_result)
                self.assertTrue(self.window.selection_button.isEnabled())
        self.write_helper()
        self.finish()
        self.assertIsNotNone(self.window.selection_result)

    def test_no_match_and_budget_exhaustion_are_honest_terminal_results(self):
        for mode, status in (("no_match", "no_match"), ("budget", "budget_exhausted")):
            self.write_helper(mode)
            self.finish()
            self.assertEqual(self.window.selection_result["status"], status)
            self.assertIsNone(self.window.selection_result["selected_option"])
            self.assertIn("No action selected", self.window.selection_choice.text())

    def test_watchdog_and_output_budget_stop_helper(self):
        self.write_helper("hang")
        self.window.selection_timeout_ms = 150
        self.finish()
        self.assertIn("timed out", self.window.selection_status.text())
        self.window.selection_timeout_ms = 3000
        self.write_helper("flood")
        self.finish()
        self.assertIn("8 MiB", self.window.selection_status.text())
        self.assertIsNone(self.window.selection_result)

    def test_close_stops_selection_without_blocking(self):
        self.write_helper("hang")
        self.start()
        before = time.monotonic()
        self.window.close()
        self.assertLess(time.monotonic() - before, 0.25)
        eventually(lambda: self.window.selection_process is None)
        self.assertFalse(self.window.isVisible())

    def test_blank_or_oversized_goal_and_empty_options_disable_requests(self):
        self.window.selection_goal.setPlainText(" ")
        self.assertFalse(self.window.selection_button.isEnabled())
        self.window.preview_selection()
        self.assertIsNone(self.window.selection_process)
        self.window.selection_goal.setPlainText("a" * 4001)
        self.assertFalse(self.window.selection_button.isEnabled())
        self.window.selection_goal.setPlainText("Save")
        empty = copy.deepcopy(self.data)
        empty["options"] = []
        self.window._show_snapshot(empty, empty["target"])
        self.assertFalse(self.window.selection_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
