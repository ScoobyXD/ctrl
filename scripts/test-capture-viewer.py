"""Opt-in visible integration check: two clicks, real CUA/YOLO, resize, shutdown.

Writes local screenshots only for this explicitly requested verification run.
Run from the normal interactive Windows desktop, not an isolated UI sandbox.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ctrl_loop.capture_app import CaptureWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Show windows and capture the primary desktop twice")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to explicitly run desktop capture; use unittest discover for offline tests")
    run = ROOT / ".ctrl/capture-viewer" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run.mkdir(parents=True)
    report = {"passed": False, "run_directory": str(run), "python": sys.executable,
              "application_source": str(ROOT / "src/ctrl_loop/capture_app.py"), "captures": []}
    app = QApplication([])
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    fixture = QWidget()
    fixture.setWindowTitle("CTRL capture verification fixture")
    fixture.setStyleSheet("QWidget { background: #f4f6f8; color: #172230; font-size: 18px; }")
    layout = QVBoxLayout(fixture)
    marker = QLabel("PRIMARY DISPLAY · FIRST CAPTURE")
    marker.setStyleSheet("background: #ff00ff; color: black; padding: 24px;")
    layout.addWidget(marker)
    layout.addWidget(QLineEdit("A local screen used to check detection"))
    layout.addWidget(QPushButton("Example control"))
    layout.addWidget(QPushButton("Another example control"))
    fixture.resize(680, 420)
    primary = app.primaryScreen()
    geometry = primary.geometry()
    fixture.move(geometry.x() + 32, geometry.y() + 48)
    fixture.show()
    window = CaptureWindow(ROOT)
    window.move(geometry.x() + 120, geometry.y() + 110)
    window.show()
    heartbeats = []
    ticker = QTimer()
    ticker.timeout.connect(lambda: heartbeats.append(time.monotonic()))
    ticker.start(20)

    def wait_until(predicate, seconds=45):
        deadline = time.monotonic() + seconds
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out: phase={window.lifecycle.phase}, status={window.status.text()}")
            app.processEvents()
            time.sleep(.005)
        app.processEvents()

    def settle(milliseconds=200):
        until = time.monotonic() + milliseconds / 1000
        wait_until(lambda: time.monotonic() >= until, 5)

    def click_and_wait():
        before = len(heartbeats)
        started = time.monotonic()
        QTest.mouseClick(window.capture_button, Qt.MouseButton.LeftButton)
        capture_id = window.lifecycle.active_id
        assert capture_id and not window.capture_button.isEnabled() and not window.isVisible()
        wait_until(lambda: window.lifecycle.phase == "idle")
        assert window.isVisible() and window.capture_button.isEnabled()
        assert window.lifecycle.result is not None, window.status.text()
        frame = window.lifecycle.frame
        assert frame.capture_id == capture_id
        assert window.lifecycle.result.frame is frame
        assert window.viewer.frame is frame
        elapsed = time.monotonic() - started
        assert len(heartbeats) > before + 3, "UI timer did not progress during capture/inference"
        info = {"capture_id": capture_id, "width": frame.width, "height": frame.height,
                "captured_at": frame.captured_at.isoformat(), "boxes": len(window.viewer.boxes),
                "pixel_sha256": hashlib.sha256(frame.rgb.tobytes()).hexdigest(),
                "elapsed_seconds": round(elapsed, 3), "ui_ticks": len(heartbeats) - before}
        report["captures"].append(info)
        print(json.dumps(info), flush=True)
        return frame

    try:
        wait_until(lambda: window.lifecycle.phase == "idle", 60)
        window.grab().save(str(run / "ready.png"))
        first = click_and_wait()
        native = (round(geometry.width() * primary.devicePixelRatio()), round(geometry.height() * primary.devicePixelRatio()))
        report["primary_screen"] = {"qt_logical_width": geometry.width(), "qt_logical_height": geometry.height(),
                                    "device_pixel_ratio": primary.devicePixelRatio(), "expected_native_size": native}
        assert (first.width, first.height) == native, "CUA frame was not primary-screen native size"
        assert ((first.rgb[:, :, 0] > 240) & (first.rgb[:, :, 1] < 20) & (first.rgb[:, :, 2] > 240)).sum() > 5000
        assert len(window.viewer.boxes) > 0, "Real detector returned no boxes"
        Image.fromarray(first.rgb).save(run / "first-primary-screen.png")
        settle()
        window.grab().save(str(run / "first-result.png"))
        frozen_hash = hashlib.sha256(first.rgb.tobytes()).hexdigest()
        boxes = window.viewer.boxes
        for width, height in ((760, 850), (1280, 620)):
            window.resize(width, height)
            settle()
            assert window.viewer.boxes is boxes
            assert hashlib.sha256(first.rgb.tobytes()).hexdigest() == frozen_hash
            window.grab().save(str(run / f"resized-{width}x{height}.png"))
        marker.setText("PRIMARY DISPLAY · SECOND CAPTURE")
        marker.setStyleSheet("background: #00ffff; color: black; padding: 24px;")
        # The displayed image must stay unchanged even while the desktop changes.
        settle(600)
        assert window.viewer.frame is first and window.viewer.boxes is boxes
        second = click_and_wait()
        assert second is not first and second.capture_id != first.capture_id
        assert not np.array_equal(first.rgb, second.rgb)
        assert ((second.rgb[:, :, 0] < 20) & (second.rgb[:, :, 1] > 240) & (second.rgb[:, :, 2] > 240)).sum() > 5000
        assert window.lifecycle.result.frame is second
        Image.fromarray(second.rgb).save(run / "second-primary-screen.png")
        settle()
        window.grab().save(str(run / "second-result.png"))
        report["passed"] = True
        report["checks"] = ["visible launch", "two explicit Qt test clicks", "primary native dimensions",
                             "real CUA capture", "real YOLO CPU inference", "fixture visible after CTRL hides",
                             "UI heartbeat during jobs", "fixed pixels between clicks", "resize retains source geometry",
                             "second capture replaces image and boxes"]
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        window.close()
        wait_until(lambda: not window.worker.isRunning(), 40)
        settle()
        fixture.close()
        ticker.stop()
        report["worker_stopped"] = not window.worker.isRunning()
        report["max_ui_tick_gap_seconds"] = round(max((b - a for a, b in zip(heartbeats, heartbeats[1:])), default=0), 3)
        (run / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Verification: {run / 'verification.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
