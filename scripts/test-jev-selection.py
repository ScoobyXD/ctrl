"""Synthetic 132-option Jev check; --live explicitly enables AWS/model calls.

Default: report the offline request plan. Live: exercise the viewer's actual
selection helper with four browser-like ancestors, without reading a real app
or executing input. Writes only synthetic evidence under .ctrl/jev-selection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctrl_loop.selection import SelectionSearch
from ctrl_loop.uia_contract import build_options, new_snapshot


def fixture():
    data = new_snapshot(101, 202)
    data["target"]["title"] = "Synthetic browser — Jev selection verification"
    parent = None
    for index in range(4):
        identity = f"{data['snapshot_id']}:parent{index}"
        data["elements"].append({"id": identity, "parent_id": parent,
            "name": f"Synthetic browser pane {index}", "role": "Pane", "patterns": [],
            "state": {}, "value": None, "enabled": True, "focused": False,
            "offscreen": False, "is_password": False})
        parent = identity
    for index in range(132):
        data["elements"].append({"id": f"{data['snapshot_id']}:e{index}", "parent_id": parent,
            "name": "Open another YouTube video" if index == 131 else f"Account setting {index}",
            "role": "Button", "patterns": ["Invoke"], "state": {}, "value": None,
            "enabled": True, "focused": False, "offscreen": False, "is_password": False})
    data["options"] = build_options(data["elements"])
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use AWS and make bounded paid Jev requests with synthetic data")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".ctrl/jev-selection")
    args = parser.parse_args()
    data, goal = fixture(), "Open a new YouTube video."
    plan = SelectionSearch(data, goal).preview()
    summary = {"synthetic_only": True, "observed_options": len(data["options"]),
        "initial_requests": plan["initial_request_count"], "max_request_bytes": plan["max_request_bytes"],
        "largest_initial_request_bytes": max(len(json.dumps(r["payload"], allow_nan=False,
            separators=(",", ":")).encode()) for r in plan["initial_requests"]), "input_actions": 0}
    if not args.live:
        print(json.dumps(dict(summary, mode="offline", model_requests=0)))
        return 0

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from ctrl_loop.uia_viewer import UIAViewer

    args.output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = UIAViewer(auto_discover=False)
    window.show()
    try:
        window.target_combo.addItem(data["target"]["title"], data["target"])
        window._show_snapshot(data, data["target"])
        window.tabs.setCurrentIndex(2)
        window.selection_goal.setPlainText(goal)
        assert QTest.qWaitForWindowExposed(window)
        QTest.mouseClick(window.selection_button, Qt.MouseButton.LeftButton)
        deadline = time.monotonic() + 310
        while window.selection_process is not None and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(20)
        app.processEvents()
        result = window.selection_result
        if result is None:
            # The helper's messages are sanitized; this fixture contains no real UI data.
            print(json.dumps(dict(summary, status="failed", message=window.selection_status.text())), flush=True)
            return 1
        selected = result.get("selected_option") or {}
        summary.update(status=result["status"], model_requests=result["request_count"],
            considered_options=result["considered_options"], usage=result["usage"],
            expected_target_selected=selected.get("id") == data["options"][-1]["id"],
            input_actions=result["input_actions"], platform=app.platformName())
        (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        assert window.grab().save(str(args.output_dir / "live-viewer.png"))
        print(json.dumps(summary), flush=True)
        return 0 if (summary["status"] == "selected" and summary["input_actions"] == 0
                     and summary["expected_target_selected"]) else 1
    finally:
        window.close()
        deadline = time.monotonic() + 3
        while window.selection_process is not None and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(20)


if __name__ == "__main__":
    raise SystemExit(main())
