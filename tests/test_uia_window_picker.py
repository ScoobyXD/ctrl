"""Synthetic Qt picker regressions; no UIA observation or remote requests.

Defaults to offscreen for automated runs. Set QT_QPA_PLATFORM=windows to exercise
the same geometry, painting and input checks using the native Windows Qt style.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider

from ctrl_loop.uia_viewer import UIAViewer


APP = QApplication.instance() or QApplication([])
APP.setQuitOnLastWindowClosed(False)


def windows(count):
    return [{"hwnd": 1000 + index, "pid": 2000 + index,
             "title": f"Synthetic window {index:02d}"} for index in range(count)]


def scrollbar_rects(bar):
    option = QStyleOptionSlider()
    bar.initStyleOption(option)
    style = bar.style()
    return tuple(style.subControlRect(QStyle.ComplexControl.CC_ScrollBar,
                                     option, control, bar) for control in (
        QStyle.SubControl.SC_ScrollBarSlider, QStyle.SubControl.SC_ScrollBarGroove))


class WindowPickerTests(unittest.TestCase):
    def setUp(self):
        self.window = UIAViewer(auto_discover=False)
        self.window.show()
        self.assertTrue(QTest.qWaitForWindowExposed(self.window))
        if APP.platformName() == "windows":
            self.window.activateWindow()
            self.assertTrue(QTest.qWaitForWindowActive(self.window))
        APP.processEvents()
        self.combo = self.window.target_combo

    def tearDown(self):
        self.combo.hidePopup()
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()

    def open_picker(self, count):
        records = windows(count)
        self.window._show_windows({"windows": records})
        QTest.mouseClick(self.combo, Qt.MouseButton.LeftButton)
        view = self.combo.view()
        # Windows animates the popup using a temporary widget; the real view is
        # hidden until that finishes and cannot receive a row click yet.
        deadline = time.monotonic() + 1.5
        while not view.isVisible() and time.monotonic() < deadline:
            QTest.qWait(10)
        APP.processEvents()
        self.assertTrue(view.isVisible())
        self.assertTrue(view.verticalScrollBar().isVisible())
        return records, view, view.verticalScrollBar()

    def test_eleven_windows_have_a_large_bright_thumb_and_fractional_scroll(self):
        _, view, bar = self.open_picker(11)
        row_height = view.visualRect(self.combo.model().index(0, 0)).height()
        self.assertGreater(bar.maximum(), 1)
        self.assertAlmostEqual(bar.maximum(), row_height, delta=2)
        handle, groove = scrollbar_rects(bar)
        expected_fraction = bar.pageStep() / (bar.maximum() + bar.pageStep())
        self.assertGreater(handle.height() / groove.height(), 0.85)
        self.assertAlmostEqual(handle.height() / groove.height(), expected_fraction, delta=0.02)

        # Native Windows styling previously painted the large thumb almost the
        # same color as the popup, making the small bright track look like it.
        rendered = bar.grab().toImage()
        scale = rendered.devicePixelRatio()
        handle_point = handle.center()
        track_point = QPoint(groove.center().x(), (handle.bottom() + groove.bottom()) // 2)
        handle_color = rendered.pixelColor(round(handle_point.x() * scale), round(handle_point.y() * scale))
        track_color = rendered.pixelColor(round(track_point.x() * scale), round(track_point.y() * scale))
        self.assertGreater(handle_color.lightness(), track_color.lightness() + 25)

        bar.setValue(bar.maximum() // 2)
        APP.processEvents()
        first = view.visualRect(self.combo.model().index(0, 0))
        last = view.visualRect(self.combo.model().index(10, 0))
        self.assertLess(first.top(), 0)
        self.assertGreater(first.bottom(), 0)
        self.assertLess(last.top(), view.viewport().height())
        self.assertGreater(last.bottom(), view.viewport().height())
        self.assertEqual(first.top(), -bar.value())

    def test_dragging_thumb_reaches_an_intermediate_pixel_offset(self):
        _, view, bar = self.open_picker(11)
        handle, groove = scrollbar_rects(bar)
        start = handle.center()
        end = start + QPoint(0, (groove.height() - handle.height()) // 2)
        QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(bar, end, delay=20)
        QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=end)
        APP.processEvents()
        self.assertGreater(bar.value(), bar.minimum())
        self.assertLess(bar.value(), bar.maximum())
        self.assertAlmostEqual(bar.value(), bar.maximum() / 2, delta=2)
        self.assertEqual(view.visualRect(self.combo.model().index(0, 0)).top(), -bar.value())

    def test_long_list_reaches_middle_and_end_and_selects_the_correct_identity(self):
        records, view, bar = self.open_picker(80)
        bar.setValue(bar.maximum())
        APP.processEvents()
        self.assertTrue(view.viewport().rect().contains(view.visualRect(self.combo.model().index(79, 0)).center()))

        bar.setValue(bar.maximum() // 2)
        APP.processEvents()
        middle = view.indexAt(view.viewport().rect().center())
        self.assertGreater(middle.row(), 20)
        self.assertLess(middle.row(), 60)
        chosen = records[middle.row()]
        # Native QComboBox briefly suppresses releases after opening to prevent
        # the click that opens a popup from also choosing an item.
        QTest.mouseMove(view.viewport(), view.visualRect(middle).center(), delay=20)
        QTest.qWait(200)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton,
                         pos=view.visualRect(middle).center())
        APP.processEvents()
        self.assertEqual(self.combo.currentData(), chosen)

        QTest.mouseClick(self.combo, Qt.MouseButton.LeftButton)
        APP.processEvents()
        QTest.keyClick(view, Qt.Key.Key_End)
        QTest.keyClick(view, Qt.Key.Key_Return)
        APP.processEvents()
        self.assertEqual(self.combo.currentData(), records[-1])
        QTest.keyClick(self.combo, Qt.Key.Key_Home)
        self.assertEqual(self.combo.currentData(), records[0])
        self.assertIsNone(self.window.process)
        self.assertIsNone(self.window.snapshot)

    def test_window_count_tracks_empty_single_and_many_discovery_results(self):
        self.assertEqual(self.window.window_count.text(), "Window (0)")
        for count in (1, 11, 80, 0):
            with self.subTest(count=count):
                self.window._show_windows({"windows": windows(count)})
                self.assertEqual(self.combo.count(), count)
                self.assertEqual(self.window.window_count.text(), f"Window ({count})")
        self.assertEqual(self.combo.currentIndex(), -1)

    def test_rediscovery_preserves_hwnd_and_pid_after_reorder_and_title_change(self):
        original = windows(11)
        self.window._show_windows({"windows": original})
        self.combo.setCurrentIndex(7)
        chosen = dict(original[7], title="Renamed synthetic window")
        # A reused HWND belonging to a different PID must not win the match.
        replacement = [dict(chosen, pid=9999), chosen, original[2]]
        self.window._show_windows({"windows": replacement})
        self.assertEqual(self.combo.currentIndex(), 1)
        self.assertEqual(self.combo.currentData(), chosen)
        self.assertIn(chosen["title"], self.combo.currentText())
        self.assertEqual(self.window.window_count.text(), "Window (3)")

    def test_returning_to_a_table_restores_its_highlighted_row_details(self):
        target = windows(1)[0]
        self.window._show_windows({"windows": [target]})
        snapshot = {
            "schema_version": 1, "snapshot_id": "synthetic-picker-test", "source": "windows-uia",
            "target": target, "status": "complete", "warnings": [], "truncated": False,
            "elements": [{"id": "element-1", "name": "Synthetic button", "role": "Button",
                          "patterns": ["Invoke"], "state": {}, "is_password": False}],
            "options": [{"id": "option-1", "element_id": "element-1", "operation": "activate",
                         "label": "Synthetic button", "requires": {}}],
        }
        self.window._show_snapshot(snapshot, target)
        self.window.controls_table.selectRow(0)
        control_details = json.loads(self.window.details.toPlainText())
        self.window.tabs.setCurrentIndex(1)
        self.window.options_table.selectRow(0)
        option_details = json.loads(self.window.details.toPlainText())
        self.assertTrue(option_details["preview_only"])
        self.window.tabs.setCurrentIndex(2)
        self.assertTrue(self.window.details.isHidden())
        for tab, expected in ((0, control_details), (1, option_details)):
            self.window.tabs.setCurrentIndex(tab)
            self.assertFalse(self.window.details.isHidden())
            self.assertEqual(json.loads(self.window.details.toPlainText()), expected)
        self.assertIsNone(self.window.process)
        self.assertIsNone(self.window.selection_process)


if __name__ == "__main__":
    unittest.main()
