"""Long-lived local capture/detector service, isolated from the Qt UI thread."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import threading

from PySide6.QtCore import QThread, Signal

from .capture_state import CapturedFrame, DetectionResult


class CaptureWorker(QThread):
    ready = Signal()
    captured = Signal(object)
    detected = Signal(object)
    failed = Signal(str, str)
    fatal_error = Signal(str)

    def __init__(self, root: Path, parent=None, *, capture_factory=None, detector_factory=None):
        super().__init__(parent)
        self.root = root
        self._capture_factory = capture_factory
        self._detector_factory = detector_factory
        self._stop_requested = threading.Event()
        self._loop = None
        self._task = None
        self._queue = None

    def _post(self, callback):
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        try:
            loop.call_soon_threadsafe(callback)
            return True
        except RuntimeError:
            return False

    def capture(self, capture_id):
        if self._stop_requested.is_set():
            return False
        return self._post(lambda: self._queue.put_nowait(capture_id))

    def stop(self):
        if self._stop_requested.is_set():
            return
        self._stop_requested.set()
        # Cancels an in-flight MCP await or idle queue. A native inference already
        # executing finishes before cleanup; QThread is never forcibly terminated.
        self._post(lambda: self._task.cancel() if not self._task.done() else None)

    def run(self):
        try:
            asyncio.run(self._serve())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.fatal_error.emit(str(exc))
        finally:
            self._loop = None

    async def _serve(self):
        from .desktop_capture import DesktopCapture
        from .local_detector import LocalDetector

        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        self._queue = asyncio.Queue()
        capture_factory = self._capture_factory or DesktopCapture
        detector_factory = self._detector_factory or LocalDetector
        # Enter/capture/exit MCP in the same asyncio task, as required by AnyIO.
        with detector_factory(self.root) as detector:
            async with capture_factory(self.root) as capture:
                if self._stop_requested.is_set():
                    return
                self.ready.emit()
                while not self._stop_requested.is_set():
                    capture_id = await self._queue.get()
                    if self._stop_requested.is_set():
                        break
                    try:
                        rgb = await capture.capture()
                        if self._stop_requested.is_set():
                            break
                        frame = CapturedFrame(capture_id, datetime.now(timezone.utc), rgb)
                        self.captured.emit(frame)
                        boxes = detector.detect(frame.rgb)
                        if not self._stop_requested.is_set():
                            self.detected.emit(DetectionResult(frame, boxes))
                    except Exception as exc:
                        if not self._stop_requested.is_set():
                            self.failed.emit(capture_id, str(exc))
