"""Offline Qt checks for capture identity, painted geometry, and window lifecycle."""
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ctrl_loop.capture_app import CaptureWindow
from ctrl_loop.capture_state import CapturedFrame, CaptureLifecycle, DetectionResult, fit_image
from ctrl_loop.capture_viewer import CaptureViewer

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def frame(capture_id="test", width=200, height=100, color=(34, 51, 68)):
    rgb = np.full((height, width, 3), color, dtype=np.uint8)
    return CapturedFrame(capture_id, datetime.now(timezone.utc), rgb)


def eventually(predicate, timeout_ms=1500):
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate():
        APP.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Qt condition did not become true before timeout")
        QTest.qWait(5)
    APP.processEvents()


class FrameAndLifecycleTests(unittest.TestCase):
    def test_capture_owns_readonly_pixels_separate_from_source(self):
        source = np.zeros((3, 5, 3), dtype=np.uint8)
        captured = CapturedFrame("owned", datetime.now(timezone.utc), source)
        source[:] = 255
        self.assertTrue(np.all(captured.rgb == 0))
        self.assertFalse(np.shares_memory(source, captured.rgb))
        with self.assertRaises(ValueError):
            captured.rgb[0, 0, 0] = 1
        self.assertEqual((captured.width, captured.height), (5, 3))

    def test_invalid_frames_and_rectangles_are_rejected(self):
        for pixels in (np.zeros((0, 2, 3), np.uint8), np.zeros((2, 2, 4), np.uint8),
                       np.zeros((2, 2, 3), np.float32)):
            with self.subTest(shape=pixels.shape, dtype=pixels.dtype), self.assertRaises(ValueError):
                CapturedFrame("bad", datetime.now(timezone.utc), pixels)
        captured = frame()
        for box in ((-1, 2, 10, 20), (1, 2, 201, 20), (1, 2, 10, 101),
                    (1, 2, 1, 20), (1, 20, 10, 2), (1, 2, float("nan"), 20), (1, 2, 3)):
            with self.subTest(box=box), self.assertRaises(ValueError):
                DetectionResult(captured, (box,))

    def test_result_requires_exact_frame_and_active_job_once(self):
        state = CaptureLifecycle()
        self.assertIsNone(state.begin())
        self.assertTrue(state.ready())
        first_id = state.begin()
        self.assertIsNone(state.begin())
        self.assertFalse(state.accept_frame(frame("wrong")))
        captured = frame(first_id)
        self.assertTrue(state.accept_frame(captured))
        self.assertFalse(state.accept_frame(captured))
        self.assertFalse(state.accept_result(DetectionResult(frame(first_id), ())))
        result = DetectionResult(captured, ((10, 10, 30, 30),))
        self.assertTrue(state.accept_result(result))
        self.assertFalse(state.accept_result(result))
        second_id = state.begin()
        self.assertNotEqual(first_id, second_id)
        self.assertFalse(state.fail(first_id))
        self.assertFalse(state.accept_result(result))
        replacement = frame(second_id)
        self.assertTrue(state.accept_frame(replacement))
        self.assertIsNone(state.result)
        self.assertIs(state.frame, replacement)
        self.assertTrue(state.fail(second_id))
        self.assertFalse(state.fail(second_id))

    def test_close_rejects_all_late_work(self):
        state = CaptureLifecycle()
        state.ready()
        capture_id = state.begin()
        captured = frame(capture_id)
        state.accept_frame(captured)
        state.stop()
        self.assertFalse(state.ready())
        self.assertIsNone(state.begin())
        self.assertFalse(state.accept_frame(captured))
        self.assertFalse(state.accept_result(DetectionResult(captured, ())))
        self.assertFalse(state.fail(capture_id))
        self.assertEqual(state.phase, "closing")

    def test_fit_geometry_handles_letterboxing_and_empty_viewports(self):
        self.assertEqual(fit_image(200, 100, 600, 200), (2, 100, 0))
        self.assertEqual(fit_image(200, 100, 400, 400), (2, 0, 100))
        self.assertEqual(fit_image(200, 100, 100, 200), (0.5, 0, 75))
        self.assertEqual(fit_image(200, 100, 0, 200), (0, 0, 0))


class PaintedViewerTests(unittest.TestCase):
    def test_green_box_and_image_use_same_transform_at_several_aspect_ratios(self):
        viewer = CaptureViewer()
        self.addCleanup(viewer.close)
        captured = frame()
        original = captured.rgb.copy()
        viewer.show_frame(captured)
        viewer.show_result(DetectionResult(captured, ((30, 20, 150, 70),)))
        for width, height, scale, left, top in ((600, 200, 2, 100, 0),
                                               (400, 400, 2, 0, 100),
                                               (320, 200, 1.6, 0, 20)):
            with self.subTest(viewport=(width, height)):
                viewer.resize(width, height)
                viewer.show()
                APP.processEvents()
                painted = viewer.grab().toImage()
                self.assertEqual((painted.width(), painted.height()), (width, height))
                # Sample actual rendered pixels, using independently known fixture geometry.
                self.assertEqual(painted.pixelColor(round(left + 30 * scale), round(top + 45 * scale)).name(), "#00ff66")
                self.assertEqual(painted.pixelColor(round(left + 90 * scale), round(top + 20 * scale)).name(), "#00ff66")
                self.assertEqual(painted.pixelColor(round(left + 90 * scale), round(top + 45 * scale)).name(), "#223344")
                if left:
                    self.assertEqual(painted.pixelColor(20, height // 2).name(), "#101418")
                if top:
                    self.assertEqual(painted.pixelColor(width // 2, 5).name(), "#101418")
        np.testing.assert_array_equal(captured.rgb, original)

    def test_new_image_immediately_clears_boxes_and_rejects_old_result(self):
        viewer = CaptureViewer()
        self.addCleanup(viewer.close)
        first = frame("first")
        first_result = DetectionResult(first, ((30, 20, 150, 70),))
        viewer.show_frame(first)
        self.assertTrue(viewer.show_result(first_result))
        second = frame("second", color=(77, 88, 99))
        viewer.show_frame(second)
        self.assertEqual(viewer.boxes, ())
        self.assertFalse(viewer.show_result(first_result))
        self.assertFalse(viewer.show_result(DetectionResult(frame("second"), ())))
        self.assertIs(viewer.frame, second)
        viewer.resize(400, 200)
        viewer.show()
        APP.processEvents()
        self.assertEqual(viewer.grab().toImage().pixelColor(60, 90).name(), "#4d5863")


class FakeWorker(QObject):
    ready = Signal()
    captured = Signal(object)
    detected = Signal(object)
    failed = Signal(str, str)
    fatal_error = Signal(str)
    finished = Signal()

    def __init__(self, ready_on_start=True):
        super().__init__()
        self.ready_on_start = ready_on_start
        self.running = False
        self.jobs = []
        self.stop_count = 0
        self.accept_job = True

    def start(self):
        self.running = True
        if self.ready_on_start:
            QTimer.singleShot(0, self.ready.emit)

    def capture(self, capture_id):
        self.jobs.append(capture_id)
        return self.accept_job

    def stop(self):
        self.stop_count += 1

    def finish(self):
        self.running = False
        self.finished.emit()

    def isRunning(self):
        return self.running


class WindowLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.worker = FakeWorker()
        self.window = CaptureWindow(ROOT, worker=self.worker, repaint_delay_ms=10)
        self.window.show()
        eventually(lambda: self.window.capture_button.isEnabled())

    def tearDown(self):
        self.worker.finish()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def click_capture(self):
        QTest.mouseClick(self.window.capture_button, Qt.MouseButton.LeftButton)
        return self.window.lifecycle.active_id

    def test_only_deliberate_clicks_capture_and_second_result_replaces_first(self):
        QTest.qWait(35)
        self.assertEqual(self.worker.jobs, [])
        first_id = self.click_capture()
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.window.capture_button.isEnabled())
        self.window.begin_capture()  # Even a duplicate invocation must not queue work.
        eventually(lambda: len(self.worker.jobs) == 1)
        self.assertEqual(self.worker.jobs, [first_id])
        first = frame(first_id)
        self.worker.captured.emit(first)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.status.text(), "Analyzing…")
        self.assertIs(self.window.viewer.frame, first)
        self.assertFalse(self.window.capture_button.isEnabled())
        result = DetectionResult(first, ((10, 10, 30, 30),))
        self.worker.detected.emit(result)
        self.assertTrue(self.window.capture_button.isEnabled())
        QTest.qWait(35)
        self.assertEqual(self.worker.jobs, [first_id])
        self.assertIs(self.window.viewer.frame, first)
        self.assertEqual(self.window.viewer.boxes, result.boxes)
        second_id = self.click_capture()
        eventually(lambda: len(self.worker.jobs) == 2)
        second = frame(second_id, color=(1, 2, 3))
        self.worker.captured.emit(second)
        self.assertEqual(self.window.viewer.boxes, ())
        self.worker.detected.emit(result)
        self.assertFalse(self.window.capture_button.isEnabled())
        self.assertIs(self.window.viewer.frame, second)
        second_result = DetectionResult(second, ((40, 20, 80, 60),))
        self.worker.detected.emit(second_result)
        self.assertEqual(self.window.viewer.boxes, second_result.boxes)
        self.assertEqual(len(self.worker.jobs), 2)

    def test_capture_error_restores_window_and_allows_explicit_retry(self):
        capture_id = self.click_capture()
        eventually(lambda: len(self.worker.jobs) == 1)
        self.worker.failed.emit("old-job", "stale")
        self.assertFalse(self.window.isVisible())
        self.worker.failed.emit(capture_id, "synthetic capture error")
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.capture_button.isEnabled())
        self.assertIn("synthetic capture error", self.window.status.text())
        QTest.qWait(35)
        self.assertEqual(len(self.worker.jobs), 1)
        self.click_capture()
        eventually(lambda: len(self.worker.jobs) == 2)

    def test_unavailable_worker_restores_hidden_window(self):
        self.worker.accept_job = False
        self.click_capture()
        eventually(self.window.isVisible)
        self.assertIn("Capture service is unavailable", self.window.status.text())
        self.assertEqual(len(self.worker.jobs), 1)

    def test_close_during_repaint_delay_cancels_dispatch_and_late_signals(self):
        self.window._capture_timer.setInterval(100)
        capture_id = self.click_capture()
        self.window.close()
        self.assertEqual(self.worker.stop_count, 1)
        self.assertTrue(self.window.isVisible())
        QTest.qWait(120)
        self.assertEqual(self.worker.jobs, [])
        self.worker.captured.emit(frame(capture_id))
        self.assertIsNone(self.window.viewer.frame)
        self.worker.finish()
        self.assertFalse(self.window.isVisible())

    def test_close_during_analysis_is_responsive_and_discards_late_result(self):
        capture_id = self.click_capture()
        eventually(lambda: len(self.worker.jobs) == 1)
        captured = frame(capture_id)
        self.worker.captured.emit(captured)
        self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertIn("Closing", self.window.status.text())
        self.assertFalse(self.window.capture_button.isEnabled())
        pulse = []
        QTimer.singleShot(0, lambda: pulse.append(True))
        eventually(lambda: pulse)
        self.worker.detected.emit(DetectionResult(captured, ((10, 10, 30, 30),)))
        self.worker.failed.emit(capture_id, "late failure")
        self.assertEqual(self.window.viewer.boxes, ())
        self.assertIn("Closing", self.window.status.text())
        self.worker.finish()
        self.assertFalse(self.window.isVisible())

    def test_maximized_window_state_is_restored_after_capture_and_failure(self):
        self.window.showMaximized()
        APP.processEvents()
        self.assertTrue(self.window.isMaximized())
        capture_id = self.click_capture()
        eventually(lambda: len(self.worker.jobs) == 1)
        captured = frame(capture_id)
        self.worker.captured.emit(captured)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isMaximized())
        self.worker.detected.emit(DetectionResult(captured, ()))
        second_id = self.click_capture()
        eventually(lambda: len(self.worker.jobs) == 2)
        self.worker.failed.emit(second_id, "synthetic error")
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isMaximized())

    def test_inference_failure_keeps_new_image_without_old_boxes(self):
        first_id = self.click_capture()
        eventually(lambda: len(self.worker.jobs) == 1)
        first = frame(first_id)
        self.worker.captured.emit(first)
        self.worker.detected.emit(DetectionResult(first, ((10, 10, 30, 30),)))
        second_id = self.click_capture()
        eventually(lambda: len(self.worker.jobs) == 2)
        second = frame(second_id, color=(1, 2, 3))
        self.worker.captured.emit(second)
        self.worker.failed.emit(second_id, "synthetic inference error")
        self.assertTrue(self.window.isVisible())
        self.assertIs(self.window.viewer.frame, second)
        self.assertEqual(self.window.viewer.boxes, ())
        self.assertTrue(self.window.capture_button.isEnabled())
        self.assertIn("synthetic inference error", self.window.status.text())

    def test_fatal_during_analysis_ignores_late_result_and_can_close(self):
        capture_id = self.click_capture()
        eventually(lambda: len(self.worker.jobs) == 1)
        captured = frame(capture_id)
        self.worker.captured.emit(captured)
        self.worker.fatal_error.emit("synthetic fatal runtime error")
        self.worker.ready.emit()
        self.worker.detected.emit(DetectionResult(captured, ((10, 10, 30, 30),)))
        self.assertFalse(self.window.capture_button.isEnabled())
        self.assertEqual(self.window.viewer.boxes, ())
        self.assertIn("synthetic fatal runtime error", self.window.status.text())
        self.worker.finish()
        self.window.close()
        self.assertFalse(self.window.isVisible())

    def test_close_while_loading_ignores_late_ready(self):
        worker = FakeWorker(ready_on_start=False)
        window = CaptureWindow(ROOT, worker=worker, repaint_delay_ms=10)
        try:
            window.show()
            self.assertFalse(window.capture_button.isEnabled())
            window.close()
            self.assertEqual(worker.stop_count, 1)
            worker.ready.emit()
            self.assertFalse(window.capture_button.isEnabled())
            self.assertIn("Closing", window.status.text())
            self.assertEqual(worker.jobs, [])
            worker.finish()
            self.assertFalse(window.isVisible())
        finally:
            worker.finish()
            window.close()
            window.deleteLater()
    def test_fatal_error_restores_window_and_disables_capture(self):
        self.click_capture()
        self.worker.fatal_error.emit("synthetic startup fault")
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.capture_button.isEnabled())
        self.assertIn("synthetic startup fault", self.window.status.text())
        QTest.qWait(30)
        self.assertEqual(self.worker.jobs, [])


if __name__ == "__main__":
    unittest.main()
