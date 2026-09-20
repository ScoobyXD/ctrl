"""Render the UIA viewer with synthetic data and an offline Jev helper.

No window enumeration, native UIA observation, credentials, or model calls.
Uses the real Qt platform by default; set QT_QPA_PLATFORM=offscreen for CI.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider

from ctrl_loop.uia_contract import build_options, new_snapshot
from ctrl_loop.uia_viewer import UIAViewer


def fixture(target):
    data = new_snapshot(target["hwnd"], target["pid"])
    data["target"] = target
    definitions = [
        ("Save draft", "Button", ["Invoke"], {}),
        ("Draft text", "Edit", ["Value"], {"value_read_only": False}),
        ("Enable notifications", "CheckBox", ["Toggle"], {"toggle": 1}),
        ("Light theme", "ListItem", ["SelectionItem"], {"selected": True}),
        ("Results", "Pane", ["Scroll"], {"horizontal_scrollable": True, "vertical_scrollable": True}),
        ("Additional options", "Button", ["ExpandCollapse"], {"expand_collapse": 0}),
        ("Disabled action", "Button", ["Invoke"], {}),
        ("Activations: 0", "Text", [], {}),
    ]
    for index, (name, role, patterns, state) in enumerate(definitions):
        data["elements"].append({
            "id": f"{data['snapshot_id']}:e{index}", "parent_id": None,
            "name": name, "role": role, "patterns": patterns, "state": state,
            "value": "Example draft" if role == "Edit" else None,
            "enabled": name != "Disabled action", "offscreen": False,
            "focused": False, "is_password": False, "bounds": [20, 30 + 40 * index, 200, 30],
        })
    data["options"] = build_options(data["elements"])
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".ctrl" / "uia-picker-check")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # A real helper boundary, but its answer is entirely synthetic.
    helper = args.output_dir.resolve() / "synthetic-selection.py"
    helper.write_text('''import json, sys
request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
snapshot = request["snapshot"]
selected = next(o for o in snapshot["options"] if o["operation"] == "toggle")
result = {"snapshot_id": snapshot["snapshot_id"], "goal": request["goal"],
          "client_run_id": request["run_id"], "preview_only": True,
          "input_actions": 0, "status": "selected", "selected_option": selected,
          "trace": [], "request_count": 0,
          "considered_options": len(snapshot["options"]), "observed_options": len(snapshot["options"])}
print(json.dumps({"event": "result", "data": result}), flush=True)
''', encoding="utf-8")
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = UIAViewer(auto_discover=False, selection_helper_path=helper, selection_python=sys.executable)
    window.show()
    assert QTest.qWaitForWindowExposed(window)
    if app.platformName() == "windows":
        window.activateWindow()
        assert QTest.qWaitForWindowActive(window)

    def settle():
        app.processEvents()
        QTest.qWait(80)

    def capture(widget, name):
        settle()
        assert widget.grab().save(str(args.output_dir / (name + ".png")))

    checks = {"platform": app.platformName(), "style": app.style().objectName(),
              "synthetic_only": True, "model_requests": 0, "input_actions": 0, "pickers": []}
    try:
        for count in (11, 80):
            windows = [{"title": f"Synthetic window {i + 1:02}", "hwnd": 1000 + i, "pid": 2000 + i}
                       for i in range(count)]
            window._show_windows({"windows": windows})
            window.target_combo.setCurrentIndex(0)
            settle()
            window.target_combo.showPopup()
            view = window.target_combo.view()
            # Windows may animate the first popup before revealing its list.
            popup_deadline = time.monotonic() + 2
            while not view.isVisible() and time.monotonic() < popup_deadline:
                settle()
            settle()
            assert view.isVisible(), "Window picker did not become visible"
            bar = view.verticalScrollBar()
            assert bar.isVisible(), "Overflow must expose the scrollbar"
            assert bar.maximum() > 1
            bar.setValue(0)
            capture(view.window(), f"picker-{count}-top")
            style_option = QStyleOptionSlider()
            bar.initStyleOption(style_option)
            handle = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, style_option,
                                                QStyle.SubControl.SC_ScrollBarSlider, bar)
            groove = bar.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar, style_option,
                                                QStyle.SubControl.SC_ScrollBarGroove, bar)
            start = handle.center()
            destination = start + QPoint(0, (groove.height() - handle.height()) // 2)
            QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(bar, destination, delay=50)
            QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=destination)
            settle()
            assert 0 < bar.value() < bar.maximum(), "Thumb must reach an intermediate position"
            capture(view.window(), f"picker-{count}-middle")
            middle_value = bar.value()
            middle_row = view.indexAt(view.viewport().rect().center()).row()
            if count == 80:
                assert 20 < middle_row < 60, "Long-list middle must be reachable"
            bar.setValue(bar.maximum())
            settle()
            assert view.visualRect(view.model().index(count - 1, 0)).intersects(view.viewport().rect())
            capture(view.window(), f"picker-{count}-bottom")
            checks["pickers"].append({"windows": count, "maximum": bar.maximum(),
                "page_step": bar.pageStep(), "handle_height": handle.height(), "groove_height": groove.height(),
                "drag_value": middle_value, "middle_row": middle_row, "last_row_visible": True})
            window.target_combo.hidePopup()

        data = fixture(window.target_combo.currentData())
        window._show_snapshot(data, data["target"])
        window.controls_table.selectRow(0)
        capture(window, "controls")
        window.tabs.setCurrentIndex(1)
        window.options_table.selectRow(0)
        assert json.loads(window.details.toPlainText())["preview_only"] is True
        capture(window, "options")
        window.tabs.setCurrentIndex(2)
        window.selection_goal.setPlainText("Enable notifications (synthetic offline check)")
        QTest.mouseClick(window.selection_button, Qt.MouseButton.LeftButton)
        deadline = time.monotonic() + 5
        while window.selection_process is not None:
            settle()
            assert time.monotonic() < deadline, "Synthetic helper timed out"
        assert window.selection_result is not None, window.selection_status.text()
        assert window.selection_result["selected_option"]["operation"] == "toggle"
        capture(window, "jev-preview")
        checks.update(controls=len(data["elements"]), options=len(data["options"]), offline_preview="selected")
        (args.output_dir / "summary.json").write_text(json.dumps(checks, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(checks))
        return 0
    finally:
        window.target_combo.hidePopup()
        window.close()
        deadline = time.monotonic() + 3
        while window.selection_process is not None and time.monotonic() < deadline:
            settle()


if __name__ == "__main__":
    raise SystemExit(main())
