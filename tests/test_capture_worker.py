"""Offline real-QThread tests with injected capture/detector services."""
import asyncio
import os
from pathlib import Path
import sys
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
from PySide6.QtCore import QObject, Slot, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ctrl_loop.capture_worker import CaptureWorker

APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def eventually(predicate, timeout_ms=3000):
    deadline = time.monotonic() + timeout_ms / 1000
    while not predicate():
        APP.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Worker condition did not become true before timeout")
        time.sleep(0.005)  # Release the GIL so Python work in QThread can progress.
    APP.processEvents()


class Signals(QObject):
    def __init__(self, worker):
        super().__init__()
        self.ready_count = 0
        self.frames = []
        self.results = []
        self.failures = []
        self.fatals = []
        worker.ready.connect(self.ready)
        worker.captured.connect(self.captured)
        worker.detected.connect(self.detected)
        worker.failed.connect(self.failed)
        worker.fatal_error.connect(self.fatal)

    @Slot()
    def ready(self):
        self.ready_count += 1

    @Slot(object)
    def captured(self, value):
        self.frames.append(value)

    @Slot(object)
    def detected(self, value):
        self.results.append(value)

    @Slot(str, str)
    def failed(self, capture_id, message):
        self.failures.append((capture_id, message))

    @Slot(str)
    def fatal(self, message):
        self.fatals.append(message)


class Services:
    def __init__(self):
        self.events = []
        self.capture_count = 0
        self.detect_count = 0
        self.capture_error_once = False
        self.detect_error_once = False
        self.startup_error = False
        self.block_capture = False
        self.block_detect = False
        self.block_capture_exit = False
        self.capture_exit_started = threading.Event()
        self.release_capture_exit = threading.Event()
        self.capture_started = threading.Event()
        self.detect_started = threading.Event()
        self.release_detection = threading.Event()
        self.owner_tasks = []
        self.call_threads = []
        self.rgb = np.full((60, 80, 3), (11, 22, 33), dtype=np.uint8)

    def capture_factory(self, root):
        owner = self

        class Capture:
            async def __aenter__(self):
                owner.events.append("capture-enter")
                owner.owner_tasks.append(asyncio.current_task())
                return self

            async def capture(self):
                owner.capture_count += 1
                owner.call_threads.append(threading.get_ident())
                owner.owner_tasks.append(asyncio.current_task())
                owner.capture_started.set()
                if owner.block_capture:
                    try:
                        await asyncio.Event().wait()
                    finally:
                        owner.events.append("capture-cancelled")
                if owner.capture_error_once:
                    owner.capture_error_once = False
                    raise RuntimeError("synthetic capture fault")
                return owner.rgb

            async def __aexit__(self, *args):
                owner.owner_tasks.append(asyncio.current_task())
                if owner.block_capture_exit:
                    owner.capture_exit_started.set()
                    while not owner.release_capture_exit.is_set():
                        await asyncio.sleep(0.005)
                owner.events.append("capture-exit")

        return Capture()

    def detector_factory(self, root):
        owner = self

        class Detector:
            def __enter__(self):
                owner.events.append("detector-enter")
                if owner.startup_error:
                    raise RuntimeError("synthetic detector startup fault")
                return self

            def detect(self, rgb):
                owner.detect_count += 1
                owner.call_threads.append(threading.get_ident())
                owner.detect_started.set()
                if rgb.flags.writeable:
                    raise AssertionError("Detector received mutable original pixels")
                if owner.block_detect and not owner.release_detection.wait(5):
                    raise RuntimeError("Test did not release simulated native inference")
                if owner.detect_error_once:
                    owner.detect_error_once = False
                    raise RuntimeError("synthetic inference fault")
                return ((10, 10, 40, 30),)

            def __exit__(self, *args):
                owner.events.append("detector-exit")

        return Detector()


class CaptureWorkerTests(unittest.TestCase):
    def setUp(self):
        self.services = Services()
        self.worker = CaptureWorker(ROOT, capture_factory=self.services.capture_factory,
                                    detector_factory=self.services.detector_factory)
        self.signals = Signals(self.worker)

    def tearDown(self):
        self.services.release_detection.set()
        self.services.release_capture_exit.set()
        self.worker.stop()
        self.assertTrue(self.worker.wait(5000), "Worker did not release its runtime")
        APP.processEvents()

    def start_ready(self):
        self.worker.start()
        eventually(lambda: self.signals.ready_count == 1 or self.signals.fatals, timeout_ms=15000)
        self.assertEqual(self.signals.fatals, [])

    def assert_orderly_cleanup(self):
        self.assertEqual(self.services.events[-2:], ["capture-exit", "detector-exit"])
        self.assertEqual(len(set(self.services.owner_tasks)), 1,
                         "MCP context entry/use/exit must remain in one asyncio task")
        self.assertTrue(all(thread != threading.get_ident() for thread in self.services.call_threads))

    def test_persistent_services_one_capture_per_job_and_no_autonomous_capture(self):
        self.start_ready()
        QTest.qWait(30)
        self.assertEqual(self.services.capture_count, 0)
        for index in range(2):
            capture_id = f"explicit-{index}"
            self.assertTrue(self.worker.capture(capture_id))
            eventually(lambda: len(self.signals.results) == index + 1)
            result = self.signals.results[index]
            self.assertIs(result.frame, self.signals.frames[index])
            self.assertEqual(result.frame.capture_id, capture_id)
            self.assertEqual((result.frame.width, result.frame.height), (80, 60))
            self.assertIsNotNone(result.frame.captured_at.tzinfo)
            self.assertEqual(result.boxes, ((10, 10, 40, 30),))
        self.services.rgb[:] = 255
        self.assertEqual(self.signals.frames[0].rgb[0, 0].tolist(), [11, 22, 33])
        QTest.qWait(30)
        self.assertEqual(self.services.capture_count, 2)
        self.assertEqual(self.services.detect_count, 2)
        self.assertEqual(self.services.events.count("detector-enter"), 1)
        self.assertEqual(self.services.events.count("capture-enter"), 1)
        self.worker.stop()
        eventually(lambda: not self.worker.isRunning())
        self.assertFalse(self.worker.capture("after-close"))
        self.assert_orderly_cleanup()

    def test_capture_failure_is_not_retried_and_next_explicit_job_recovers(self):
        self.services.capture_error_once = True
        self.start_ready()
        self.worker.capture("failed")
        eventually(lambda: self.signals.failures)
        self.assertEqual(self.signals.failures, [("failed", "synthetic capture fault")])
        self.assertEqual(self.signals.frames, [])
        self.assertEqual(self.signals.results, [])
        QTest.qWait(30)
        self.assertEqual(self.services.capture_count, 1)
        self.assertEqual(self.services.detect_count, 0)
        self.worker.capture("recovery")
        eventually(lambda: self.signals.results)
        self.assertEqual(self.signals.results[0].frame.capture_id, "recovery")
        self.assertEqual(self.services.capture_count, 2)

    def test_inference_error_emits_matching_failure_after_new_frame(self):
        self.services.detect_error_once = True
        self.start_ready()
        self.worker.capture("infer-failure")
        eventually(lambda: self.signals.failures)
        self.assertEqual(len(self.signals.frames), 1)
        self.assertEqual(self.signals.frames[0].capture_id, "infer-failure")
        self.assertEqual(self.signals.results, [])
        self.assertEqual(self.signals.failures, [("infer-failure", "synthetic inference fault")])
        self.worker.capture("recovery")
        eventually(lambda: self.signals.results)
        self.assertEqual(self.signals.results[0].frame.capture_id, "recovery")

    def test_stop_cancels_pending_capture_and_exits_both_services(self):
        self.services.block_capture = True
        self.start_ready()
        self.worker.capture("cancel-capture")
        eventually(self.services.capture_started.is_set)
        self.worker.stop()
        eventually(lambda: not self.worker.isRunning())
        self.assertIn("capture-cancelled", self.services.events)
        self.assertEqual(self.signals.frames, [])
        self.assertEqual(self.signals.results, [])
        self.assertEqual(self.signals.failures, [])
        self.assertEqual(self.signals.fatals, [])
        self.assert_orderly_cleanup()

    def test_stop_during_native_inference_keeps_ui_responsive_and_discards_result(self):
        self.services.block_detect = True
        self.start_ready()
        self.worker.capture("cancel-inference")
        eventually(self.services.detect_started.is_set)
        eventually(lambda: self.signals.frames)
        self.worker.stop()
        pulse = []
        QTimer.singleShot(0, lambda: pulse.append(True))
        eventually(lambda: pulse)
        self.assertTrue(self.worker.isRunning())
        self.assertNotIn("detector-exit", self.services.events)
        self.services.release_detection.set()
        eventually(lambda: not self.worker.isRunning())
        self.assertEqual(self.signals.results, [])
        self.assertEqual(self.signals.failures, [])
        self.assertEqual(self.signals.fatals, [])
        self.assert_orderly_cleanup()

    def test_repeated_stop_does_not_interrupt_helper_cleanup(self):
        self.services.block_capture_exit = True
        self.start_ready()
        self.worker.stop()
        eventually(self.services.capture_exit_started.is_set)
        self.worker.stop()  # A second close while the helper is shutting down.
        self.services.release_capture_exit.set()
        eventually(lambda: not self.worker.isRunning())
        self.assertEqual(self.signals.fatals, [])
        self.assert_orderly_cleanup()
    def test_stop_before_start_does_not_capture_or_signal_ready(self):
        self.worker.stop()
        self.worker.start()
        eventually(lambda: not self.worker.isRunning())
        self.assertEqual(self.signals.ready_count, 0)
        self.assertEqual(self.services.capture_count, 0)
        self.assertEqual(self.signals.results, [])
        self.assert_orderly_cleanup()

    def test_detector_startup_failure_reports_fatal_without_starting_capture(self):
        self.services.startup_error = True
        self.worker.start()
        eventually(lambda: self.signals.fatals)
        eventually(lambda: not self.worker.isRunning())
        self.assertEqual(self.signals.ready_count, 0)
        self.assertEqual(self.signals.fatals, ["synthetic detector startup fault"])
        self.assertEqual(self.services.events, ["detector-enter"])
        self.assertEqual(self.services.capture_count, 0)


if __name__ == "__main__":
    unittest.main()
