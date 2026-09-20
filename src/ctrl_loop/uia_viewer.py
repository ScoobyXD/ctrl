"""Manual Windows UIA observation viewer. Candidate selection never executes an action.

Every native observation runs in an expendable helper process. The GUI uses Qt's
asynchronous process signals and a hard deadline; a wedged provider cannot wedge
the GUI, cancellation, or closing. No native references enter this module.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QProcess, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QPushButton,
    QSpinBox, QSplitter, QTableView, QTabWidget, QVBoxLayout, QWidget,
)


MAX_OUTPUT_BYTES = 8 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[2]


def _text(value):
    if value is None:
        return "Unavailable"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _name(element):
    return str(element.get("name") or "(No accessible name)")


def _bounds(element):
    value = element.get("bounds")
    return ", ".join(str(v) for v in value) if isinstance(value, list) else "Unavailable"


def _state_summary(state):
    parts = []
    if state.get("toggle") is not None:
        parts.append("Toggle: " + {0: "Off", 1: "On", 2: "Indeterminate"}.get(state["toggle"], _text(state["toggle"])))
    if state.get("expand_collapse") is not None:
        parts.append({0: "Collapsed", 1: "Expanded", 2: "Partially expanded", 3: "Leaf"}.get(state["expand_collapse"], "Expansion: " + _text(state["expand_collapse"])))
    if state.get("selected") is not None:
        parts.append("Selected: " + _text(state["selected"]))
    read_only = state.get("value_read_only", state.get("read_only"))
    if read_only is not None:
        parts.append("Read-only" if read_only else "Writable")
    if state.get("range_read_only") is not None:
        parts.append("Range read-only" if state["range_read_only"] else "Range writable")
    if state.get("range_minimum") is not None or state.get("range_maximum") is not None:
        parts.append(f"Range: {_text(state.get('range_minimum'))} to {_text(state.get('range_maximum'))}")
    for direction in ("horizontal", "vertical"):
        scrollable = state.get(direction + "_scrollable")
        if scrollable is not None:
            parts.append(f"Scroll {direction}: {_text(scrollable)}")
    known = {"toggle", "expand_collapse", "selected", "value_read_only", "read_only", "range_read_only",
             "range_minimum", "range_maximum", "horizontal_scrollable", "vertical_scrollable"}
    parts.extend(f"{key.replace('_', ' ')}: {_text(value)}" for key, value in state.items() if key not in known and value is not None)
    return " · ".join(parts) or "None reported"


class _Rows(QAbstractTableModel):
    """Small read-only model, avoiding per-cell widget creation during refresh."""

    def __init__(self, headers, parent=None):
        super().__init__(parent)
        self.headers = headers
        self.rows = []
        self.records = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role == Qt.ItemDataRole.DisplayRole:
            return self.rows[index.row()][index.column()]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return super().headerData(section, orientation, role)

    def replace(self, rows, records):
        self.beginResetModel()
        self.rows, self.records = rows, records
        self.endResetModel()


class UIAViewer(QMainWindow):
    def __init__(self, helper_path=None, *, observe_timeout=10.0,
                 deadline_slack_ms=3000, windows_timeout_ms=8000,
                 auto_discover=True):
        super().__init__()
        self.helper_path = Path(helper_path) if helper_path else ROOT / "scripts" / "uia-observe.py"
        self.observe_timeout = observe_timeout
        self.deadline_slack_ms = deadline_slack_ms
        self.windows_timeout_ms = windows_timeout_ms
        self.snapshot = None
        self.process = None
        self._output = bytearray()
        self._job = None
        self._job_target = None
        self._abort_reason = None
        self._closing = False
        self._elements = {}
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._timed_out)
        self.setWindowTitle("CTRL — UIA control options")
        self.resize(1360, 880)
        self.setMinimumSize(800, 600)

        title = QLabel("UIA control options")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Observe a window, inspect its controls, and preview supported operations.")
        note = QLabel("Observation only · Selecting an option never performs it · Results stay on this computer")
        note.setObjectName("readOnlyNote")
        self.target_combo = QComboBox()
        self.target_combo.setMinimumWidth(300)
        self.target_combo.setAccessibleName("Target window")
        self.target_combo.currentIndexChanged.connect(self._target_changed)
        self.refresh_windows_button = QPushButton("Refresh windows")
        self.refresh_windows_button.clicked.connect(self.refresh_windows)
        self.observe_button = QPushButton("Observe / Refresh")
        self.observe_button.setObjectName("observeButton")
        self.observe_button.clicked.connect(self.observe)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.max_elements = QSpinBox()
        self.max_elements.setRange(100, 5000)
        self.max_elements.setValue(1000)
        self.max_elements.setAccessibleName("Maximum elements per observation")
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Window"))
        toolbar.addWidget(self.target_combo, 1)
        toolbar.addWidget(self.refresh_windows_button)
        toolbar.addWidget(QLabel("Element limit"))
        toolbar.addWidget(self.max_elements)
        toolbar.addWidget(self.observe_button)
        toolbar.addWidget(self.cancel_button)

        self.status = QLabel("Choose a target window and press Observe / Refresh.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setObjectName("observationStatus")
        self.summary = QLabel("No current snapshot")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search names, roles, values, states, or supported behaviors…")
        self.search.setAccessibleName("Search observed controls and options")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.export_button = QPushButton("Export snapshot JSON…")
        self.export_button.clicked.connect(self.export_json)
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(self.search, 1)
        filter_bar.addWidget(self.export_button)

        self.controls_model = _Rows([
            "Name", "Role", "Current value", "Enabled", "Focused", "Offscreen",
            "Current state", "Supported behaviors", "Bounds (x, y, w, h)", "Snapshot ID",
        ], self)
        self.options_model = _Rows([
            "Operation", "Target name", "Role", "Enabled", "Required input",
            "Bounds (x, y, w, h)", "Option ID",
        ], self)
        self.controls_table, self.controls_proxy = self._table(self.controls_model)
        self.options_table, self.options_proxy = self._table(self.options_model)
        for column, width in enumerate((160, 250, 110, 75, 260, 190, 200)):
            self.options_table.setColumnWidth(column, width)
        for column, width in enumerate((235, 110, 200, 75, 75, 85, 250, 230, 190, 200)):
            self.controls_table.setColumnWidth(column, width)
        self.controls_table.setAccessibleName("Observed accessible controls")
        self.options_table.setAccessibleName("Supported action candidates — preview only")
        self.tabs = QTabWidget()
        self.tabs.addTab(self.controls_table, "Controls (0)")
        self.tabs.addTab(self.options_table, "Options (0)")
        self.tabs.currentChanged.connect(lambda _: self.details.clear())
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Selected control or option details")
        self.details.setPlaceholderText(
            "Select a row for complete serializable details. IDs belong only to this snapshot.\n"
            "Names and AutomationId are not guaranteed to be unique or stable.\n"
            "Capabilities describe what the provider reports; they do not guarantee an action would succeed."
        )
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.details)
        splitter.setSizes([470, 200])
        layout = QVBoxLayout()
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(note)
        layout.addLayout(toolbar)
        layout.addWidget(self.status)
        layout.addWidget(self.summary)
        layout.addLayout(filter_bar)
        layout.addWidget(splitter, 1)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #182027; color: #e7eef3; font: 13px 'Segoe UI'; }
            QLabel#pageTitle { font-size: 25px; font-weight: 650; }
            QLabel#readOnlyNote { color: #7edeb5; }
            QLabel#observationStatus { background: #24323d; border-radius: 5px; padding: 9px; }
            QPushButton { background: #30414e; border: 1px solid #435967; border-radius: 4px; padding: 8px 10px; }
            QPushButton:hover { background: #405666; }
            QPushButton:disabled { color: #81929f; background: #25323c; }
            QPushButton#observeButton { background: #2c8c69; font-weight: 600; }
            QLineEdit, QComboBox, QSpinBox { background: #10181e; border: 1px solid #435967; padding: 7px; }
            QTableView, QPlainTextEdit { background: #111a21; border: 1px solid #3a4d5b; selection-background-color: #285d75; }
            QHeaderView::section { background: #2b3c49; padding: 7px; border: 0; border-right: 1px solid #435967; }
            QTabBar::tab { background: #273743; padding: 9px 20px; }
            QTabBar::tab:selected { background: #3c5565; }
        """)
        self._sync_buttons()
        if auto_discover:
            QTimer.singleShot(0, self.refresh_windows)

    def _table(self, model):
        proxy = QSortFilterProxyModel(self)
        proxy.setSourceModel(model)
        proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        proxy.setFilterKeyColumn(-1)
        table = QTableView()
        table.setModel(proxy)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setWordWrap(False)
        table.setSortingEnabled(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setDefaultSectionSize(145)
        table.setColumnWidth(0, 235)
        table.verticalHeader().setVisible(False)
        table.selectionModel().selectionChanged.connect(lambda *_: self._show_selection(table, proxy, model))
        return table, proxy

    def _show_selection(self, table, proxy, model):
        rows = table.selectionModel().selectedRows()
        if not rows or self.snapshot is None:
            self.details.clear()
            return
        record = model.records[proxy.mapToSource(rows[0]).row()]
        detail = {"snapshot_id": self.snapshot["snapshot_id"], "source": self.snapshot["source"]}
        if model is self.options_model:
            detail.update(option=record, target=self._elements.get(record["element_id"]), preview_only=True)
        else:
            detail["element"] = record
        self.details.setPlainText(json.dumps(detail, indent=2, ensure_ascii=False))

    def _filter(self, text):
        self.controls_proxy.setFilterFixedString(text)
        self.options_proxy.setFilterFixedString(text)
        self._counts()

    def _counts(self):
        for index, name, proxy, model in (
            (0, "Controls", self.controls_proxy, self.controls_model),
            (1, "Options", self.options_proxy, self.options_model),
        ):
            shown, total = proxy.rowCount(), model.rowCount()
            count = str(total) if shown == total else f"{shown} / {total}"
            self.tabs.setTabText(index, f"{name} ({count})")

    def _sync_buttons(self):
        active = self.process is not None
        self.target_combo.setEnabled(not active)
        self.refresh_windows_button.setEnabled(not active)
        self.observe_button.setEnabled(not active and self.target_combo.currentData() is not None)
        self.max_elements.setEnabled(not active)
        self.cancel_button.setEnabled(active)
        self.export_button.setEnabled(not active and self.snapshot is not None)

    def _clear_snapshot(self):
        self.snapshot = None
        self._elements = {}
        self.controls_model.replace([], [])
        self.options_model.replace([], [])
        self.details.clear()
        self.summary.setText("No current snapshot")
        self._counts()
        self._sync_buttons()

    def _target_changed(self, *_):
        # Even a programmatic target change invalidates an in-flight observation.
        if self.process is not None and self._job == "observe":
            self._abort("Target changed. Observe the selected window again.")
        self._clear_snapshot()
        self.status.setText("Choose a target window and press Observe / Refresh.")

    def refresh_windows(self):
        if self.process is None and not self._closing:
            self._clear_snapshot()
            self.status.setText("Reading top-level windows…")
            self._start("windows", ["windows"], self.windows_timeout_ms)

    def observe(self):
        target = self.target_combo.currentData()
        if self.process is not None or not target or self._closing:
            return
        self._clear_snapshot()
        self._job_target = dict(target)
        self.status.setText("Observing selected window through native UI Automation… Cancel remains available.")
        self._start("observe", [
            "observe", "--hwnd", str(target["hwnd"]), "--pid", str(target["pid"]),
            "--max-elements", str(self.max_elements.value()), "--timeout", str(self.observe_timeout),
        ], int(self.observe_timeout * 1000) + self.deadline_slack_ms)

    def _start(self, job, arguments, deadline_ms):
        self._job = job
        self._abort_reason = None
        self._output = bytearray()
        process = QProcess(self)
        self.process = process
        process.setWorkingDirectory(str(self.helper_path.parent.parent))
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        process.setProgram(str(executable))
        process.setArguments(["-I", str(self.helper_path), *arguments])
        process.readyReadStandardOutput.connect(lambda: self._read_output(process))
        process.started.connect(lambda: process.kill() if process is self.process and self._abort_reason else None)
        # Stderr can contain target data from third-party dependencies: discard it.
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.finished.connect(lambda code, status: self._finished(process, code, status))
        process.errorOccurred.connect(lambda error: self._process_error(process, error))
        self._sync_buttons()
        self._deadline.start(max(1, deadline_ms))
        process.start()

    def _read_output(self, process):
        chunk = bytes(process.readAllStandardOutput())
        if process is not self.process or self._abort_reason:
            return
        if len(self._output) + len(chunk) > MAX_OUTPUT_BYTES:
            self._abort("Observation exceeded the 8 MiB response limit. Reduce the element limit and retry.")
            return
        self._output.extend(chunk)

    def _timed_out(self):
        self._abort("Observation timed out. The helper was stopped; you can refresh or choose another window.")

    def cancel(self):
        self._abort("Observation cancelled. No current snapshot; press Observe / Refresh to try again.")

    def _abort(self, reason):
        if self.process is not None:
            self._abort_reason = reason
            self._output.clear()
            self._deadline.stop()
            self.status.setText(reason)
            self.process.kill()

    def _process_error(self, process, error):
        if process is self.process and error == QProcess.ProcessError.FailedToStart:
            self._abort_reason = "Could not start the observation helper. Run the UIA installer and retry."
            self._finished(process, -1, QProcess.ExitStatus.CrashExit)

    def _finished(self, process, exit_code, exit_status):
        if process is not self.process:
            return
        self._read_output(process)
        self._deadline.stop()
        job, target, reason = self._job, self._job_target, self._abort_reason
        payload = bytes(self._output)
        self._output.clear()
        self.process = None
        self._job = None
        process.deleteLater()
        if self._closing:
            QTimer.singleShot(0, self.close)
            return
        if reason:
            self.status.setText(reason)
        elif exit_status != QProcess.ExitStatus.NormalExit:
            self.status.setText("The observation helper stopped unexpectedly. Refresh or choose another window.")
        else:
            try:
                result = json.loads(payload.decode("utf-8"))
                if not isinstance(result, dict):
                    raise ValueError("Expected object")
                if job == "windows":
                    self._show_windows(result)
                else:
                    self._show_snapshot(result, target)
            except (ValueError, KeyError, TypeError, UnicodeError):
                self._clear_snapshot()
                self.status.setText("The helper returned an invalid response. Run the UIA installer and retry.")
        self._sync_buttons()

    def _show_windows(self, result):
        if result.get("error"):
            self.status.setText(f"Could not list windows: {result['error']}")
            return
        windows = result["windows"]
        if not isinstance(windows, list) or len(windows) > 10000:
            raise ValueError("Invalid windows")
        for window in windows:
            if (not isinstance(window, dict) or not isinstance(window.get("hwnd"), int)
                    or not isinstance(window.get("pid"), int) or not isinstance(window.get("title"), str)):
                raise ValueError("Invalid window")
        previous = self.target_combo.currentData()
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        selected = 0
        for index, window in enumerate(windows):
            self.target_combo.addItem(f"{window['title'] or '(Untitled window)'} — PID {window['pid']} · HWND {window['hwnd']}", window)
            if previous and (previous["hwnd"], previous["pid"]) == (window["hwnd"], window["pid"]):
                selected = index
        self.target_combo.setCurrentIndex(selected if windows else -1)
        self.target_combo.blockSignals(False)
        self.status.setText(
            f"Found {len(windows)} windows. Choose a target and press Observe / Refresh."
            if windows else "No eligible windows found. Open an application, then refresh windows."
        )

    def _show_snapshot(self, result, expected_target):
        if (result.get("schema_version") != 1 or result.get("source") != "windows-uia"
                or not isinstance(result.get("snapshot_id"), str)
                or result.get("status") not in ("complete", "partial", "error")):
            raise ValueError("Invalid snapshot")
        target = result["target"]
        if not isinstance(target, dict) or not expected_target or any(
            target.get(key) != expected_target.get(key) for key in ("hwnd", "pid")
        ):
            raise ValueError("Wrong target")
        current = self.target_combo.currentData()
        if not current or any(current.get(key) != expected_target.get(key) for key in ("hwnd", "pid")):
            raise ValueError("Stale target")
        elements, options = result["elements"], result["options"]
        if not isinstance(elements, list) or not isinstance(options, list) or len(elements) > 5000 or len(options) > 100000:
            raise ValueError("Invalid record lists")
        ids = set()
        for element in elements:
            if (not isinstance(element, dict) or not isinstance(element.get("id"), str)
                    or element["id"] in ids or not isinstance(element.get("patterns"), list)
                    or not isinstance(element.get("state"), dict)):
                raise ValueError("Invalid element")
            ids.add(element["id"])
        option_ids = set()
        for option in options:
            if (not isinstance(option, dict) or not isinstance(option.get("id"), str)
                    or option["id"] in option_ids or option.get("element_id") not in ids
                    or not isinstance(option.get("operation"), str)
                    or not isinstance(option.get("requires"), dict)):
                raise ValueError("Invalid option")
            option_ids.add(option["id"])
        if not isinstance(result.get("warnings", []), list):
            raise ValueError("Invalid warnings")
        if result["status"] == "error":
            self._clear_snapshot()
            self.status.setText(f"Observation failed: {result.get('error') or 'The provider could not produce a current snapshot.'} No current snapshot; refresh or choose another window.")
            return
        result = copy.deepcopy(result)
        # Defense in depth: password values are never shown or exported, even if
        # a buggy helper returned them. The backend is responsible for not reading them.
        for element in result["elements"]:
            if element.get("is_password") is not False:
                element["value"] = None
                element["state"] = {key: value for key, value in element["state"].items()
                                    if key.lower() not in {"value", "text", "range_value", "legacy_value"}}
        self.snapshot = result
        self._elements = {element["id"]: element for element in result["elements"]}
        rows = []
        for element in result["elements"]:
            value = _text(element.get("value"))
            if element.get("is_password") is not False:
                value = "(Password — not read)" if element.get("is_password") else "(Password status unknown — value not read)"
            rows.append([
                _name(element), _text(element.get("role")), value, _text(element.get("enabled")),
                _text(element.get("focused")), _text(element.get("offscreen")), _state_summary(element["state"]),
                ", ".join(str(pattern) for pattern in element["patterns"]) or "None reported",
                _bounds(element), element["id"],
            ])
        self.controls_model.replace(rows, result["elements"])
        rows = []
        for option in result["options"]:
            element = self._elements[option["element_id"]]
            rows.append([
                option["operation"].replace("_", " "), _name(element), _text(element.get("role")), _text(element.get("enabled")),
                " · ".join(f"{key}: {_text(value)}" for key, value in option["requires"].items()) or "None", _bounds(element), option["id"],
            ])
        self.options_model.replace(rows, result["options"])
        self._counts()
        partial = result["status"] == "partial" or result.get("truncated")
        state = "PARTIAL / TRUNCATED" if partial else result["status"].upper()
        self.summary.setText(
            f"{len(result['elements'])} controls · {len(result['options'])} options · {state} · "
            f"Source: windows-uia · {result.get('elapsed_ms', '?')} ms · Snapshot: {result['snapshot_id']}"
        )
        messages = []
        if result.get("error"):
            messages.append(str(result["error"]))
        if partial:
            messages.append("Observation is incomplete. Some controls may be missing; increase the element limit or observe a narrower window.")
        if not result["elements"]:
            messages.append("No accessible controls were returned for this target.")
        elif not result["options"]:
            messages.append("Controls were observed, but no supported action candidates were reported.")
        else:
            messages.append("Snapshot ready. Select an option to preview it; no target action is performed.")
        messages.extend(str(warning) for warning in result.get("warnings", [])[:8])
        messages.append("UIA coverage depends on the target application's accessibility provider.")
        self.status.setText(" ".join(messages))

    def export_json(self, checked=False, *, path=None):
        if self.snapshot is None or self.process is not None:
            return False
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export current UIA snapshot — may contain target text", "uia-snapshot.json", "JSON files (*.json)"
            )
        if not path:
            return False
        try:
            Path(path).write_text(json.dumps(self.snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError:
            self.status.setText("Could not save the snapshot. Choose a writable destination and retry.")
            return False
        self.status.setText("Snapshot exported locally. Observe / Refresh to collect a new snapshot.")
        return True

    def closeEvent(self, event):
        self._closing = True
        if self.process is not None:
            event.ignore()
            self.hide()
            self._abort("Closing…")
            return
        self._deadline.stop()
        event.accept()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = UIAViewer()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
