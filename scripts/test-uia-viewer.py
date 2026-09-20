"""Live, read-only GUI verification against the two owned UIA fixtures.

This drives only our viewer's Qt widgets, never controls in the target apps.
Run from an interactive desktop after starting the native and browser fixtures.
Generated exports/screenshots contain only the explicitly selected fixtures.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ctrl_loop.uia_viewer import UIAViewer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-ready", type=Path, default=ROOT / ".ctrl/uia/desktop-ready.json")
    parser.add_argument("--browser-ready", type=Path, default=ROOT / ".ctrl/uia/browser-ready.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".ctrl/uia")
    parser.add_argument("--keep-open", action="store_true",
                        help="Leave the verified viewer open until its window is closed")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    targets = []
    for kind, path in (("native", args.native_ready), ("browser", args.browser_ready)):
        identity = json.loads(path.read_text(encoding="utf-8-sig"))
        assert "CTRL UIA" in identity.get("title", ""), "Only an owned CTRL UIA fixture is accepted"
        targets.append((kind, {"hwnd": identity.get("hwnd", identity.get("window_id")),
                               "pid": identity["pid"], "title": identity["title"]}))
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = UIAViewer(auto_discover=False)
    window.show()
    ticks = []
    timer = QTimer(window)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(10)
    for _, target in targets:
        window.target_combo.addItem(target["title"], target)

    def eventually(predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() >= deadline:
                raise AssertionError("Viewer did not complete within the live-test deadline")
            QTest.qWait(5)
        app.processEvents()

    def observe():
        start_ticks = len(ticks)
        QTest.mouseClick(window.observe_button, Qt.MouseButton.LeftButton)
        assert window.process is not None, "Observe did not start its helper"
        eventually(lambda: window.process is None)
        assert len(ticks) > start_ticks, "GUI heartbeat did not run during native observation"
        data = window.snapshot
        assert data is not None and data["status"] == "complete", "Expected a complete live fixture snapshot"
        assert window.controls_model.rowCount() == len(data["elements"])
        assert window.options_model.rowCount() == len(data["options"])
        return data

    summary = {"live_uia": True, "viewer_process_protocol": True, "targets": []}
    try:
        for index, (kind, target) in enumerate(targets):
            window.search.clear()
            window.target_combo.setCurrentIndex(index)
            first = observe()
            first_id = first["snapshot_id"]
            data = observe()
            assert data["snapshot_id"] != first_id, "Repeated refresh reused a snapshot ID"
            assert data["target"]["pid"] == target["pid"] and data["target"]["hwnd"] == target["hwnd"]
            actual_title = data["target"].get("title", "")
            if kind == "native":
                assert actual_title == "CTRL UIA Native Fixture", "The observed target is not the owned native fixture"
            else:
                assert actual_title.startswith("CTRL UIA Browser Fixture"), "The observed target is not the owned browser fixture"
            elements = data["elements"]

            def find(name, role=None):
                found = [e for e in elements if e.get("name") == name and (role is None or e.get("role") == role)]
                assert found, f"Missing owned fixture control: {name}"
                return found[0]

            draft = find("Draft text", "Edit")
            assert draft["value"] == "Example draft", "Observation altered or failed to read the fixture draft"
            assert find("Enable notifications", "CheckBox")["state"]["toggle"] == 1
            password = find("Fixture password", "Edit")
            assert password["is_password"] is True and password["value"] is None
            assert find("Disabled action", "Button")["enabled"] is False
            assert any(e.get("value") == "Activations: 0" or e.get("name") == "Activations: 0" for e in elements), "Fixture counter changed"
            assert any(o["element_id"] == draft["id"] and o["operation"] == "set_value" for o in data["options"])
            increment = find("Increment counter", "Button")
            assert any(o["element_id"] == increment["id"] and o["operation"] == "activate" for o in data["options"])
            if kind == "browser":
                assert find("Additional options", "Button")["state"]["expand_collapse"] == 0

            window.search.setText("Draft text")
            assert window.controls_proxy.rowCount() >= 1 and window.options_proxy.rowCount() >= 1
            window.tabs.setCurrentIndex(1)
            window.options_table.selectRow(0)
            detail = json.loads(window.details.toPlainText())
            assert detail["preview_only"] is True and detail["option"]["operation"] == "set_value"
            assert window.process is None, "Selecting an option started an unexpected process"
            export_path = args.output_dir / f"{kind}-viewer-export.json"
            assert window.export_json(path=export_path)
            exported = export_path.read_text(encoding="utf-8")
            assert "native-secret-sentinel" not in exported and "browser-secret-sentinel" not in exported
            assert json.loads(exported)["snapshot_id"] == data["snapshot_id"]
            window.search.clear()
            window.options_table.clearSelection()
            window.details.clear()
            app.processEvents()
            QTest.qWait(100)
            assert window.grab().save(str(args.output_dir / f"{kind}-viewer.png"))
            # The generic screenshot names the native fixture for a compact, legible list.
            if kind == "native":
                assert window.grab().save(str(args.output_dir / "viewer.png"))
            summary["targets"].append({
                "kind": kind, "controls": len(elements), "options": len(data["options"]),
                "elapsed_ms": data["elapsed_ms"], "status": data["status"],
                "refresh_created_new_snapshot": True, "search_and_preview_passed": True,
                "fixture_state_unchanged": True, "password_redacted": True,
            })
        summary["gui_heartbeat_ticks"] = len(ticks)
        timer.stop()
        (args.output_dir / "viewer-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary), flush=True)
        if args.keep_open:
            window.status.setText("Live native and browser fixture checks passed. Current browser options are ready to inspect. Selecting an option only previews it.")
            app.setQuitOnLastWindowClosed(True)
            return app.exec()
        return 0
    finally:
        timer.stop()
        window.close()
        eventually(lambda: window.process is None, timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())
