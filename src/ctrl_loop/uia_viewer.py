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
import uuid

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QProcess, QSortFilterProxyModel, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListView, QMainWindow, QPlainTextEdit, QPushButton,
    QSpinBox, QSplitter, QTableView, QTabWidget, QVBoxLayout, QWidget,
)

from .jev import ENDPOINT, DecisionError, request_binding
from .selection import MAX_SELECTION_REQUEST_BYTES, SelectionError, SelectionSearch


MAX_OUTPUT_BYTES = 8 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[2]
TAB_GUIDANCE = (
    "Controls are the accessible elements reported by this window. Select a row to read its details below; "
    "the target is not clicked or focused. Counts show matches / total when searching.",
    "Options are supported operations, not settings or a queue. One control can offer several options or none. "
    "Select a row to inspect it below; required input is descriptive and no action runs.",
    "Enter a goal, then explicitly start Jev to suggest one option from the full snapshot, regardless of search "
    "or highlighted rows. The preview makes remote model requests and never executes the suggestion.",
)


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
                 auto_discover=True, selection_helper_path=None,
                 selection_python=None, selection_timeout_ms=300000):
        super().__init__()
        self.helper_path = Path(helper_path) if helper_path else ROOT / "scripts" / "uia-observe.py"
        self.observe_timeout = observe_timeout
        self.deadline_slack_ms = deadline_slack_ms
        self.windows_timeout_ms = windows_timeout_ms
        self.selection_helper_path = Path(selection_helper_path) if selection_helper_path else ROOT / "scripts" / "jev-select.py"
        self.selection_python = Path(selection_python) if selection_python else ROOT / ".runtime" / "jev-venv" / "Scripts" / "python.exe"
        self.selection_timeout_ms = max(1, min(300000, selection_timeout_ms))
        self.selection_process = None
        self.selection_result = None
        self.selection_requests = []
        self.request_dialog = None
        self._selection_request = None
        self._selection_buffer = bytearray()
        self._selection_bytes = 0
        self._selection_events = 0
        self._selection_abort_reason = None
        self._selection_pending_result = None
        self._selection_deadline = QTimer(self)
        self._selection_deadline.setSingleShot(True)
        self._selection_deadline.timeout.connect(lambda: self._abort_selection("Selection timed out. Already dispatched provider requests may still finish."))
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
        note = QLabel("Local observation · Selecting an option never performs it · Optional Jev preview uses remote model requests")
        note.setObjectName("readOnlyNote")
        note.setWordWrap(True)
        self.target_combo = QComboBox()
        # The native Windows theme can make the large thumb look like empty
        # track. Use a regular list with continuous scrolling and explicit
        # scrollbar colors below. Eleven rows / ten visible must not look like
        # a long list with only two reachable positions.
        target_view = QListView(self.target_combo)
        target_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        target_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.target_combo.setView(target_view)
        # Menu-style themes can disable this during setView, before our popup
        # style is applied. Always allow the list's own scrollbar when needed.
        target_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.target_combo.setMaxVisibleItems(10)
        self.target_combo.setMinimumWidth(300)
        self.target_combo.setAccessibleName("Target window")
        self.target_combo.setToolTip(
            "Visible top-level windows with titles, sorted by title. PID and HWND distinguish similar names.\n"
            "Scroll or use arrow keys to choose a target, then press Observe / Refresh. Choosing does not focus it."
        )
        self.target_combo.currentIndexChanged.connect(self._target_changed)
        self.window_count = QLabel("Window (0)")
        self.window_count.setToolTip("Number of windows in the picker, not controls inside the selected window.")
        self.window_count.setBuddy(self.target_combo)
        for signal in (self.target_combo.model().rowsInserted, self.target_combo.model().rowsRemoved,
                       self.target_combo.model().modelReset):
            signal.connect(self._window_count_changed)
        self.refresh_windows_button = QPushButton("Refresh windows")
        self.refresh_windows_button.setToolTip("Update the window list. Clears the current snapshot and Jev preview; does not observe controls.")
        self.refresh_windows_button.clicked.connect(self.refresh_windows)
        self.observe_button = QPushButton("Observe / Refresh")
        self.observe_button.setToolTip("Read a fresh local snapshot of the selected window. Results do not update automatically.")
        self.observe_button.setObjectName("observeButton")
        self.observe_button.clicked.connect(self.observe)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setToolTip("Stop the local window-list or observation helper. No current snapshot is kept.")
        self.cancel_button.clicked.connect(self.cancel)
        self.max_elements = QSpinBox()
        self.max_elements.setRange(100, 5000)
        self.max_elements.setValue(1000)
        self.max_elements.setAccessibleName("Maximum elements per observation")
        self.max_elements.setToolTip("Maximum controls to read (100–5,000). This is a limit, not the number of windows or options.")
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.window_count)
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
        self.summary.setWordWrap(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search names, roles, values, states, or supported behaviors…")
        self.search.setAccessibleName("Search observed controls and options")
        self.search.setToolTip("Filter displayed columns in both tables. Does not change the snapshot export or Jev's candidate set.")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.export_button = QPushButton("Export snapshot JSON…")
        self.export_button.setToolTip("Save the full current snapshot, including rows hidden by search. Jev results have a separate export.")
        self.export_button.clicked.connect(self.export_json)
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(self.search, 1)
        filter_bar.addWidget(self.export_button)

        self.controls_model = _Rows([
            "Name", "Role", "Current value", "Enabled", "Focused", "Offscreen",
            "Current state", "Supported behaviors", "Bounds (x, y, w, h)", "Element ID (this snapshot)",
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
        self.tabs.addTab(self._selection_panel(), "Jev selection preview")
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tab_guidance = QLabel(TAB_GUIDANCE[0])
        self.tab_guidance.setWordWrap(True)
        self.tab_guidance.setObjectName("tabGuidance")
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
        layout.addWidget(self.tab_guidance)
        layout.addWidget(splitter, 1)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #182027; color: #e7eef3; font: 13px 'Segoe UI'; }
            QLabel#pageTitle { font-size: 25px; font-weight: 650; }
            QLabel#readOnlyNote { color: #7edeb5; }
            QLabel#tabGuidance { color: #b7cbd9; }
            QLabel#observationStatus { background: #24323d; border-radius: 5px; padding: 9px; }
            QPushButton { background: #30414e; border: 1px solid #435967; border-radius: 4px; padding: 8px 10px; }
            QPushButton:hover { background: #405666; }
            QPushButton:disabled { color: #81929f; background: #25323c; }
            QPushButton#observeButton { background: #2c8c69; font-weight: 600; }
            QLineEdit, QComboBox, QSpinBox { background: #10181e; border: 1px solid #435967; padding: 7px; }
            QComboBox { combobox-popup: 0; }
            QTableView, QPlainTextEdit { background: #111a21; border: 1px solid #3a4d5b; selection-background-color: #285d75; }
            QHeaderView::section { background: #2b3c49; padding: 7px; border: 0; border-right: 1px solid #435967; }
            QTabBar::tab { background: #273743; padding: 9px 20px; }
            QTabBar::tab:selected { background: #3c5565; }
            QComboBox QAbstractItemView { background: #111a21; border: 1px solid #435967; selection-background-color: #285d75; }
            QComboBox QAbstractItemView::item { min-height: 28px; padding: 2px 8px; }
            QTableView::item:selected, QComboBox QAbstractItemView::item:selected { background: #285d75; color: #ffffff; }
            QScrollBar:vertical { background: #10181e; width: 14px; margin: 0; }
            QScrollBar:horizontal { background: #10181e; height: 14px; margin: 0; }
            QScrollBar::handle { background: #718b9c; border-radius: 5px; }
            QScrollBar::handle:vertical { min-height: 24px; margin: 2px; }
            QScrollBar::handle:horizontal { min-width: 24px; margin: 2px; }
            QScrollBar::handle:hover { background: #9bb7c8; }
            QScrollBar::handle:pressed { background: #b6d5e5; }
            QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
            QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
        """)
        self._sync_buttons()
        if auto_discover:
            QTimer.singleShot(0, self.refresh_windows)

    def _window_count_changed(self, *_):
        self.window_count.setText(f"Window ({self.target_combo.count()})")

    def _selection_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        note = QLabel("Preview sends your goal and control descriptions to OpenRouter / TypeSafe in multiple model requests. It never executes the selected action.")
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(note)
        self.selection_goal = QPlainTextEdit()
        self.selection_goal.setPlaceholderText("Describe the next action you want to select, for example: Enable notifications.")
        self.selection_goal.setAccessibleName("Goal for Jev selection preview")
        self.selection_goal.setMaximumHeight(80)
        self.selection_goal.textChanged.connect(self._selection_goal_changed)
        layout.addWidget(self.selection_goal)
        buttons = QHBoxLayout()
        self.selection_inspect_button = QPushButton("Inspect request JSON")
        self.selection_inspect_button.clicked.connect(self.inspect_selection_requests)
        self.selection_button = QPushButton("Preview selection with Jev")
        self.selection_button.clicked.connect(self.preview_selection)
        self.selection_cancel_button = QPushButton("Cancel selection")
        self.selection_cancel_button.clicked.connect(self.cancel_selection)
        self.selection_export_button = QPushButton("Export selection JSON…")
        self.selection_export_button.clicked.connect(self.export_selection_json)
        buttons.addWidget(self.selection_inspect_button)
        buttons.addWidget(self.selection_button)
        buttons.addWidget(self.selection_cancel_button)
        buttons.addStretch()
        buttons.addWidget(self.selection_export_button)
        layout.addLayout(buttons)
        self.selection_status = QLabel("Observe a window, enter a goal, then explicitly start the preview.")
        self.selection_status.setTextFormat(Qt.TextFormat.PlainText)
        self.selection_status.setWordWrap(True)
        layout.addWidget(self.selection_status)
        self.selection_choice = QLabel("No current selected action")
        self.selection_choice.setTextFormat(Qt.TextFormat.PlainText)
        self.selection_choice.setWordWrap(True)
        self.selection_choice.setStyleSheet("font-weight: 600; color: #7edeb5; padding: 6px 0;")
        layout.addWidget(self.selection_choice)
        self.selection_trace = QPlainTextEdit()
        self.selection_trace.setReadOnly(True)
        self.selection_trace.setMaximumBlockCount(2000)
        self.selection_trace.setAccessibleName("Jev selection rounds and bucket decisions")
        self.selection_trace.setPlaceholderText("Completed model requests, buckets, and narrowing rounds appear here.")
        self.selection_json = QPlainTextEdit()
        self.selection_json.setReadOnly(True)
        self.selection_json.setAccessibleName("Complete Jev selection result JSON")
        self.selection_tabs = QTabWidget()
        self.selection_tabs.addTab(self.selection_trace, "Rounds and progress")
        self.request_panel = self._request_panel()
        self.selection_tabs.addTab(self.request_panel, "Request JSON")
        self.selection_tabs.addTab(self.selection_json, "Result JSON")
        layout.addWidget(self.selection_tabs, 1)
        return panel

    def _request_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        note = QLabel("Inspect is offline. Live runs record dispatch attempts, including later rounds.")
        note.setToolTip("Bodies stay available after failure or cancellation until the goal, snapshot, or run changes. Dispatch attempted does not confirm delivery. The API key is an authorization header, never part of these bodies.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.request_list = QComboBox()
        self.request_list.setAccessibleName("Jev request bodies")
        self.request_list.currentIndexChanged.connect(self._show_request_body)
        layout.addWidget(self.request_list)
        self.request_summary = QLabel("No requests inspected or dispatched yet.")
        self.request_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.request_summary.setWordWrap(True)
        layout.addWidget(self.request_summary)
        actions = QHBoxLayout()
        self.request_expand = QPushButton("Open larger view")
        self.request_expand.clicked.connect(self.open_request_inspector)
        self.request_wire = QCheckBox("Exact wire JSON")
        self.request_wire.toggled.connect(self._show_request_body)
        self.request_copy = QPushButton("Copy body")
        self.request_copy.clicked.connect(self.copy_request_body)
        self.request_export = QPushButton("Export body JSON…")
        self.request_export.clicked.connect(self.export_request_body)
        self.requests_export = QPushButton("Export all requests…")
        self.requests_export.clicked.connect(self.export_requests_json)
        actions.addWidget(self.request_wire)
        actions.addStretch()
        actions.addWidget(self.request_expand)
        for button in (self.request_copy, self.request_export, self.requests_export):
            actions.addWidget(button)
            button.setEnabled(False)
        layout.addLayout(actions)
        self.request_json = QPlainTextEdit()
        self.request_json.setReadOnly(True)
        self.request_json.setAccessibleName("Outgoing Jev request JSON body")
        self.request_json.setPlaceholderText("Use Inspect request JSON before a run, or start a preview to see each dispatch attempt. Authentication headers are not part of the JSON body.")
        layout.addWidget(self.request_json, 1)
        return panel

    def open_request_inspector(self):
        if self.request_dialog is not None:
            self.request_dialog.show()
            self.request_dialog.raise_()
            return
        placeholder = QWidget()
        placeholder_layout = QVBoxLayout(placeholder)
        placeholder_layout.addWidget(QLabel("Request bodies are open in the inspector window."))
        reopen = QPushButton("Show request inspector")
        reopen.clicked.connect(self.open_request_inspector)
        placeholder_layout.addWidget(reopen)
        placeholder_layout.addStretch()
        self.selection_tabs.removeTab(1)
        self.selection_tabs.insertTab(1, placeholder, "Request JSON")
        self.selection_tabs.setCurrentIndex(1)
        dialog = QDialog(self)
        dialog.setWindowTitle("Jev request JSON — local inspector")
        dialog.resize(1100, 800)
        dialog.setMinimumSize(760, 500)
        dialog.setModal(False)
        layout = QVBoxLayout(dialog)
        layout.addWidget(self.request_panel)
        self.request_panel.show()
        self.request_dialog = dialog
        self.request_expand.setVisible(False)

        def restore_panel(*_):
            self.selection_tabs.removeTab(1)
            self.selection_tabs.insertTab(1, self.request_panel, "Request JSON")
            self.selection_tabs.setCurrentIndex(1)
            self.request_expand.setVisible(True)
            self.request_dialog = None
            placeholder.deleteLater()
            dialog.deleteLater()

        dialog.finished.connect(restore_panel)
        dialog.show()

    @staticmethod
    def _request_body(record):
        return json.dumps(record["payload"], allow_nan=False, separators=(",", ":")).encode("utf-8")

    def _clear_request_bodies(self):
        self.selection_requests.clear()
        self.request_list.clear()
        self._show_request_body()

    def _add_request_body(self, record, status):
        copied = copy.deepcopy(record)
        copied["status"] = status
        copied["body_bytes"] = len(self._request_body(copied))
        self.selection_requests.append(copied)
        self.request_list.addItem(f"{len(self.selection_requests)} · {status} · {copied['stage']} · round {copied['round']} · batch {copied['batch']} · {len(copied['input_ids'])} options · {copied['body_bytes']:,} bytes")
        self.requests_export.setEnabled(True)

    def _show_request_body(self, *_):
        index = self.request_list.currentIndex()
        exists = 0 <= index < len(self.selection_requests)
        self.request_copy.setEnabled(exists)
        self.request_export.setEnabled(exists)
        self.requests_export.setEnabled(bool(self.selection_requests))
        if not exists:
            self.request_json.clear()
            self.request_summary.setText("No requests inspected or dispatched yet.")
            return
        record = self.selection_requests[index]
        payload = record["payload"]
        usage = record.get("usage")
        tokens = (f"Provider reported: {usage['input_tokens']:,} input tokens, {usage['output_tokens']:,} output tokens."
                  if usage else "Token count unavailable before a provider response; bytes are not tokens.")
        self.request_summary.setText(
            f"{record['body_bytes']:,} body bytes · {len(payload['questions'])} questions · "
            f"{len(record['input_ids'])} options. {tokens}\nPOST {ENDPOINT}")
        self.request_json.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap if self.request_wire.isChecked()
                                         else QPlainTextEdit.LineWrapMode.WidgetWidth)
        body_text = (self._request_body(record).decode("utf-8") if self.request_wire.isChecked()
                     else json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False))
        # Usage updates must not reset the user's scroll position or text selection.
        if self.request_json.toPlainText() != body_text:
            self.request_json.setPlainText(body_text)

    def inspect_selection_requests(self):
        if not self.selection_inspect_button.isEnabled():
            return False
        self._clear_request_bodies()
        try:
            plan = SelectionSearch(self.snapshot, self.selection_goal.toPlainText().strip()).preview()
            for record in plan["initial_requests"]:
                self._add_request_body(record, "Planned — not sent")
        except SelectionError as error:
            self.request_summary.setText(str(error))
            self.selection_tabs.setCurrentIndex(1)
            return False
        self.selection_tabs.setCurrentIndex(1)
        self.open_request_inspector()
        return True

    def copy_request_body(self):
        index = self.request_list.currentIndex()
        if 0 <= index < len(self.selection_requests):
            QApplication.clipboard().setText(self._request_body(self.selection_requests[index]).decode("utf-8"))

    def export_request_body(self, checked=False, *, path=None):
        index = self.request_list.currentIndex()
        if not 0 <= index < len(self.selection_requests):
            return False
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export exact Jev request body", "jev-request.json", "JSON files (*.json)")
        if not path:
            return False
        try:
            Path(path).write_bytes(self._request_body(self.selection_requests[index]))
        except OSError:
            self.request_summary.setText("Could not save the request body. Choose a writable destination.")
            return False
        return True

    def export_requests_json(self, checked=False, *, path=None):
        if not self.selection_requests:
            return False
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export Jev request records", "jev-requests.json", "JSON files (*.json)")
        if not path:
            return False
        archive = {"endpoint": ENDPOINT, "method": "POST", "requests": self.selection_requests}
        try:
            Path(path).write_text(json.dumps(archive, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        except OSError:
            self.request_summary.setText("Could not save the request records. Choose a writable destination.")
            return False
        return True

    def _tab_changed(self, index):
        self.tab_guidance.setText(TAB_GUIDANCE[index])
        self.details.clear()
        self.details.setVisible(index != 2)
        if index == 0:
            self._show_selection(self.controls_table, self.controls_proxy, self.controls_model)
        elif index == 1:
            self._show_selection(self.options_table, self.options_proxy, self.options_model)

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
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
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
        selection_active = self.selection_process is not None
        goal = self.selection_goal.toPlainText().strip()
        self.selection_button.setEnabled(not active and not selection_active and not self._closing
                                         and self.snapshot is not None and bool(self.snapshot.get("options"))
                                         and bool(goal) and len(goal) <= 4000)
        self.selection_cancel_button.setEnabled(selection_active)
        self.selection_inspect_button.setEnabled(self.selection_button.isEnabled())
        self.selection_export_button.setEnabled(not selection_active and self.selection_result is not None)

    def _clear_snapshot(self):
        self._invalidate_selection("Snapshot changed. Run a new selection preview after observation.")
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
        self._invalidate_selection("New snapshot ready. Enter a goal and explicitly start a selection preview.")
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
        self._sync_buttons()

    def _selection_goal_changed(self):
        self._invalidate_selection("Goal changed. Start a new preview for this goal.")
        if len(self.selection_goal.toPlainText().strip()) > 4000:
            self.selection_status.setText("Use a goal of at most 4,000 characters.")
        self._sync_buttons()

    def _invalidate_selection(self, reason):
        self.selection_result = None
        self._selection_pending_result = None
        self.selection_choice.setText("No current selected action")
        self.selection_json.clear()
        self.selection_trace.clear()
        self._clear_request_bodies()
        if self.selection_process is not None:
            self._abort_selection(reason + " Already dispatched provider requests may still finish.")
        else:
            self.selection_status.setText(reason)

    def preview_selection(self):
        goal = self.selection_goal.toPlainText().strip()
        if (self.process is not None or self.selection_process is not None or self._closing
                or self.snapshot is None or not self.snapshot.get("options") or not goal or len(goal) > 4000):
            return
        self._invalidate_selection("Starting Jev selection preview…")
        request = {"snapshot": copy.deepcopy(self.snapshot), "goal": goal, "run_id": uuid.uuid4().hex}
        payload = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
        if len(payload) > MAX_OUTPUT_BYTES:
            self.selection_status.setText("The snapshot exceeds the 8 MiB input limit. Observe with a smaller element limit.")
            return
        self._selection_request = request
        self._selection_buffer.clear()
        self._selection_bytes = 0
        self._selection_events = 0
        self._selection_abort_reason = None
        process = QProcess(self)
        self.selection_process = process
        process.setProgram(str(self.selection_python))
        process.setWorkingDirectory(str(self.selection_helper_path.parent.parent))
        process.setArguments(["-I", str(self.selection_helper_path), "--stdin", "--live"])
        process.started.connect(lambda: self._selection_started(process, payload))
        process.readyReadStandardOutput.connect(lambda: self._read_selection_output(process))
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.finished.connect(lambda code, status: self._selection_finished(process, code, status))
        process.errorOccurred.connect(lambda error: self._selection_process_error(process, error))
        self.selection_status.setText("Reading the OpenRouter key from AWS Secrets Manager, then asking Jev to compare options. Cancel remains available.")
        self.selection_trace.appendPlainText(f"Started with {len(request['snapshot']['options'])} observed options. Selection preview only; input actions: 0.")
        self._selection_deadline.start(self.selection_timeout_ms)
        self._sync_buttons()
        process.start()

    def _selection_started(self, process, payload):
        if process is not self.selection_process or self._selection_abort_reason:
            process.kill()
            return
        # Goal and observations go only over this process's stdin, never argv or
        # temporary files. The helper controls the reduced remote model payload.
        process.write(payload)
        process.closeWriteChannel()

    def cancel_selection(self):
        self._abort_selection("Selection cancelled. Already dispatched provider requests may still finish.")

    def _abort_selection(self, reason):
        if self.selection_process is None:
            return
        self._selection_abort_reason = reason
        self._selection_pending_result = None
        self.selection_result = None
        self.selection_choice.setText("No current selected action")
        self.selection_json.clear()
        self._selection_buffer.clear()
        self._selection_deadline.stop()
        self.selection_status.setText(reason)
        self.selection_process.kill()

    def _selection_process_error(self, process, error):
        if process is self.selection_process and error == QProcess.ProcessError.FailedToStart:
            self._selection_abort_reason = "Could not start the Jev helper. Run the Jev selection installer and retry."
            self._selection_finished(process, -1, QProcess.ExitStatus.CrashExit)

    def _read_selection_output(self, process):
        chunk = bytes(process.readAllStandardOutput())
        if process is not self.selection_process or self._selection_abort_reason:
            return
        self._selection_bytes += len(chunk)
        if self._selection_bytes > MAX_OUTPUT_BYTES:
            self._abort_selection("Selection exceeded the 8 MiB response limit. No action was selected.")
            return
        self._selection_buffer.extend(chunk)
        while b"\n" in self._selection_buffer and not self._selection_abort_reason:
            line, _, rest = self._selection_buffer.partition(b"\n")
            self._selection_buffer = bytearray(rest)
            if line.strip():
                self._selection_event(line)

    def _selection_is_current(self):
        request = self._selection_request
        return bool(request and self.snapshot is not None
                    and self.snapshot.get("snapshot_id") == request["snapshot"].get("snapshot_id")
                    and self.snapshot.get("target") == request["snapshot"].get("target")
                    and self.selection_goal.toPlainText().strip() == request["goal"])

    def _selection_event(self, line):
        try:
            self._selection_events += 1
            if self._selection_events > 20000 or not self._selection_is_current():
                raise ValueError("Stale or excessive events")
            event = json.loads(line.decode("utf-8"))
            if not isinstance(event, dict):
                raise ValueError("Invalid envelope")
            kind = event.get("event")
            if kind == "error":
                message = event.get("message")
                if not isinstance(message, str):
                    raise ValueError("Invalid error")
                self._abort_selection("Selection failed: " + message[:1000])
                return
            data = event.get("data")
            if not isinstance(data, dict):
                raise ValueError("Invalid event data")
            if data.get("client_run_id", self._selection_request["run_id"]) != self._selection_request["run_id"]:
                raise ValueError("Wrong run")
            if kind == "request":
                self._validate_request_record(data)
                self._add_request_body(data, "Dispatch attempted")
            elif kind == "progress":
                for record in self.selection_requests:
                    if record["request_id"] == data.get("request_id"):
                        usage = data.get("usage")
                        if (isinstance(usage, dict) and all(type(usage.get(key)) is int and usage[key] >= 0
                                                          for key in ("input_tokens", "output_tokens"))):
                            record["usage"] = copy.deepcopy(usage)
                            self._show_request_body()
                        break
                self.selection_trace.appendPlainText(self._progress_summary(data))
                count = data.get("request_count")
                if count is not None:
                    self.selection_status.setText(f"Selection in progress · {count} model requests started · No input actions executed")
            elif kind == "result" and self._selection_pending_result is None:
                self._validate_selection_result(data)
                self._selection_pending_result = data
            else:
                raise ValueError("Invalid event sequence")
        except (DecisionError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            self._abort_selection("The Jev helper returned an invalid or stale response. No action was selected.")

    def _validate_request_record(self, data):
        request = self._selection_request
        payload = data["payload"]
        request_binding(payload, request_id=data["request_id"], capture_id=data["capture_id"])
        state = payload["state"]
        if (len(self.selection_requests) >= 64 or data["capture_id"] != request["snapshot"]["snapshot_id"]
                or not isinstance(state, dict) or state.get("snapshot_id") != data["capture_id"]
                or state.get("goal") != request["goal"]
                or data.get("stage") not in {"classify", "reduce", "final", "recovery_reduce", "recovery_final"}
                or any(type(data.get(key)) is not int or data[key] < 0 or data[key] != state.get(key)
                       for key in ("round", "batch"))
                or data["stage"] != state.get("stage")
                or not isinstance(data.get("input_ids"), list)
                or not isinstance(state.get("candidates"), list)
                or data["input_ids"] != [candidate["id"] for candidate in state["candidates"]]
                or any(record["request_id"] == data["request_id"] for record in self.selection_requests)
                or type(data.get("body_bytes")) is not int
                or data["body_bytes"] != len(self._request_body(data))
                or data["body_bytes"] > MAX_SELECTION_REQUEST_BYTES):
            raise ValueError("Invalid request record")
        observed = {option["id"] for option in request["snapshot"]["options"]}
        if not set(data["input_ids"]).issubset(observed):
            raise ValueError("Unobserved request candidate")

    @staticmethod
    def _progress_summary(data):
        fields = []
        for key, label in (("request_count", "Request"), ("stage", "Stage"), ("round", "Round"), ("batch", "Batch")):
            if data.get(key) is not None:
                fields.append(f"{label} {_text(data[key])[:100]}")
        for key, label in (("input_ids", "inputs"), ("retained_ids", "retained")):
            if isinstance(data.get(key), list):
                fields.append(f"{len(data[key])} {label}")
        if data.get("status") is not None:
            fields.append("Status: " + _text(data["status"])[:100])
        message = " · ".join(fields) or "Selection progress update"
        if data.get("answers"):
            message += "\n  Buckets / answers: " + _text(data["answers"])[:1600]
        if data.get("classification_counts"):
            message += "\n  Bucket counts: " + _text(data["classification_counts"])[:600]
        return message

    def _validate_selection_result(self, data):
        request = self._selection_request
        if (data.get("snapshot_id") != request["snapshot"]["snapshot_id"] or data.get("goal") != request["goal"]
                or data.get("preview_only") is not True or data.get("input_actions") != 0
                or data.get("status") not in {"selected", "no_match", "budget_exhausted", "cancelled"}
                or not isinstance(data.get("trace"), list)):
            raise ValueError("Invalid selection binding")
        selected = data.get("selected_option")
        if data["status"] == "selected":
            if not isinstance(selected, dict):
                raise ValueError("Missing selected option")
            observed = {option["id"]: option for option in request["snapshot"]["options"]}
            option = observed.get(selected.get("id"))
            if option is None or any(selected.get(key) != option.get(key) for key in ("element_id", "operation")):
                raise ValueError("Selected option was not observed")
            # Present the exact captured capability, never a model-invented label
            # or operation. Core metadata remains in the trace.
            data["selected_option"] = copy.deepcopy(option)
        elif selected is not None:
            raise ValueError("Unexpected selected option")

    def _selection_finished(self, process, exit_code, exit_status):
        if process is not self.selection_process:
            return
        self._read_selection_output(process)
        if self._selection_buffer.strip() and not self._selection_abort_reason:
            self._selection_event(bytes(self._selection_buffer))
        self._selection_buffer.clear()
        self._selection_deadline.stop()
        reason, result = self._selection_abort_reason, self._selection_pending_result
        self.selection_process = None
        self._selection_pending_result = None
        process.deleteLater()
        if self._closing:
            QTimer.singleShot(0, self.close)
            return
        if reason:
            self.selection_status.setText(reason)
        elif exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
            self.selection_status.setText("The Jev helper stopped unexpectedly. No action was selected.")
        elif result is None or not self._selection_is_current():
            self.selection_status.setText("No current selection result was returned. Start a new preview.")
        else:
            self.selection_result = result
            self.selection_json.setPlainText(json.dumps(result, indent=2, ensure_ascii=False))
            status = result["status"]
            self.selection_status.setText(
                f"{status.replace('_', ' ').capitalize()} · {result.get('request_count', '?')} model requests · "
                f"{result.get('considered_options', '?')} of {result.get('observed_options', '?')} options considered · Input actions: 0"
            )
            option = result["selected_option"]
            if option:
                element = self._elements.get(option["element_id"], {})
                self.selection_choice.setText(f"Selected preview: {option['operation'].replace('_', ' ')} → {_name(element)} ({element.get('role', 'role unavailable')})")
            else:
                self.selection_choice.setText("No action selected · " + str(result.get("reason") or status.replace("_", " "))[:1000])
            if not self._selection_events or self.selection_trace.blockCount() <= 1:
                for entry in result["trace"][:2000]:
                    if isinstance(entry, dict):
                        self.selection_trace.appendPlainText(self._progress_summary(entry))
        self._selection_request = None
        self._sync_buttons()

    def export_selection_json(self, checked=False, *, path=None):
        if self.selection_result is None or self.selection_process is not None:
            return False
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Export current Jev selection preview", "jev-selection.json", "JSON files (*.json)")
        if not path:
            return False
        try:
            Path(path).write_text(json.dumps(self.selection_result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError:
            self.selection_status.setText("Could not save the selection. Choose a writable destination and retry.")
            return False
        self.selection_status.setText("Selection preview exported locally. No input actions were executed.")
        return True

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
        if self.request_dialog is not None:
            self.request_dialog.close()
        if self.process is not None or self.selection_process is not None:
            event.ignore()
            self.hide()
            self._abort("Closing…")
            self._abort_selection("Closing selection preview… Already dispatched provider requests may still finish.")
            return
        self._deadline.stop()
        self._selection_deadline.stop()
        event.accept()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    window = UIAViewer()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
