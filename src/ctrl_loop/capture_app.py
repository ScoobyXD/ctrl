"""CTRL's manual, primary-monitor capture-and-detection desktop application."""
from pathlib import Path
import sys

from PySide6.QtCore import QTimer, Qt, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

from .capture_state import CaptureLifecycle
from .capture_viewer import CaptureViewer
from .capture_worker import CaptureWorker


class CaptureWindow(QMainWindow):
    def __init__(self, root: Path, *, worker=None, repaint_delay_ms=350):
        super().__init__()
        self.setWindowTitle("CTRL — Capture")
        self.resize(1120, 760)
        self.setMinimumSize(520, 380)
        self.lifecycle = CaptureLifecycle()
        self._closing = False
        self._hidden_for_capture = False
        self._saved_window_state = None
        self.worker = worker if worker is not None else CaptureWorker(root, self)
        self.viewer = CaptureViewer()
        self.capture_button = QPushButton("Capture")
        self.capture_button.setObjectName("captureButton")
        self.capture_button.setAccessibleName("Capture primary screen")
        self.capture_button.setFixedSize(116, 40)
        self.capture_button.setEnabled(False)
        self.capture_button.clicked.connect(self.begin_capture)
        title = QLabel("CTRL")
        title.setStyleSheet("font-size: 22px; font-weight: 700; color: #f1f5f8;")
        subtitle = QLabel("Primary screen")
        subtitle.setStyleSheet("color: #9daab5;")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addSpacing(12)
        header.addWidget(subtitle)
        header.addStretch()
        header.addWidget(self.capture_button)
        self.status = QLabel("Loading local detector…")
        self.status.setObjectName("captureStatus")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setMinimumHeight(28)
        self.status.setStyleSheet("color: #b6c3cd; padding: 4px 0;")
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(14)
        layout.addLayout(header)
        layout.addWidget(self.viewer, 1)
        layout.addWidget(self.status)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #1b2229; color: #edf2f5; font-family: 'Segoe UI'; font-size: 13px; }
            QPushButton { background-color: #2dce7c; color: #0d2418; border: none; border-radius: 6px; font-weight: 600; }
            QPushButton:hover { background-color: #49e493; }
            QPushButton:pressed { background-color: #24b96b; }
            QPushButton:disabled { background-color: #33443c; color: #8b9a92; }
        """)
        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.setInterval(repaint_delay_ms)
        self._capture_timer.timeout.connect(self._dispatch_capture)
        self.worker.ready.connect(self._ready)
        self.worker.captured.connect(self._captured)
        self.worker.detected.connect(self._detected)
        self.worker.failed.connect(self._failed)
        self.worker.fatal_error.connect(self._fatal_error)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    @Slot()
    def _ready(self):
        if self.lifecycle.ready():
            self.capture_button.setEnabled(True)
            self.status.setText("Ready · Click Capture for one screenshot. CTRL briefly hides while capturing.")

    @Slot()
    def begin_capture(self):
        capture_id = self.lifecycle.begin()
        if capture_id is None:
            return
        self.capture_button.setEnabled(False)
        self.status.setText("Capturing primary screen…")
        self._saved_window_state = self.windowState()
        self._hidden_for_capture = True
        self.hide()
        # A single-shot delay lets the desktop repaint after Hide; no polling or
        # periodic capture. The worker makes exactly one screenshot RPC per job.
        self._capture_timer.start()

    @Slot()
    def _dispatch_capture(self):
        if self._closing or self.lifecycle.phase != "capturing":
            return
        capture_id = self.lifecycle.active_id
        if not self.worker.capture(capture_id):
            self._failed(capture_id, "Capture service is unavailable. Close and reopen CTRL.")

    def _restore(self):
        if self._hidden_for_capture:
            self._hidden_for_capture = False
            self.setWindowState(self._saved_window_state)
            self.show()
            self.raise_()
            self.activateWindow()

    @Slot(object)
    def _captured(self, frame):
        if self.lifecycle.accept_frame(frame):
            self.viewer.show_frame(frame)
            self._restore()
            self.status.setText("Analyzing…")

    @Slot(object)
    def _detected(self, result):
        if self.lifecycle.accept_result(result):
            self.viewer.show_result(result)
            frame = result.frame
            when = frame.captured_at.astimezone().strftime("%H:%M:%S")
            self.status.setText(f"Captured {when} · {frame.width} × {frame.height} · {len(result.boxes)} regions · Click Capture to refresh.")
            self.capture_button.setEnabled(True)

    @Slot(str, str)
    def _failed(self, capture_id, message):
        if self.lifecycle.fail(capture_id):
            self._restore()
            self.status.setText(f"Capture failed: {message}")
            self.capture_button.setEnabled(self.worker.isRunning())

    @Slot(str)
    def _fatal_error(self, message):
        if not self._closing:
            self.lifecycle.stop()
            self._capture_timer.stop()
            self._restore()
            self.capture_button.setEnabled(False)
            self.status.setText(f"Local runtime unavailable: {message} · Close CTRL, run the setup script, and reopen.")

    @Slot()
    def _worker_finished(self):
        if self._closing:
            self.close()
        else:
            self._capture_timer.stop()
            self._restore()
            self.capture_button.setEnabled(False)

    def closeEvent(self, event: QCloseEvent):
        self._closing = True
        self.lifecycle.stop()
        self._capture_timer.stop()
        if self.worker.isRunning():
            event.ignore()
            self._restore()
            self.capture_button.setEnabled(False)
            self.status.setText("Closing local runtime…")
            self.worker.stop()
        else:
            event.accept()


def main():
    root = Path(__file__).resolve().parents[2]
    app = QApplication(sys.argv)
    app.setApplicationName("CTRL Capture")
    app.setStyle("Fusion")
    window = CaptureWindow(root)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
