"""Serializable UIA candidates scoped to observations; execution lives separately."""
from datetime import datetime, timezone
import uuid


def new_snapshot(hwnd=0, pid=0):
    return dict(schema_version=1, snapshot_id=uuid.uuid4().hex,
                source="windows-uia", target=dict(hwnd=hwnd, pid=pid, title=""),
                started_at=datetime.now(timezone.utc).isoformat(), elapsed_ms=0,
                status="complete", truncated=False, warnings=[], error=None,
                elements=[], options=[])


def build_options(elements):
    """Only known-enabled, onscreen targets with provider-backed capabilities.

    This describes supported operations, not permission, readiness or success.
    IDs only identify an element in its originating snapshot.
    """
    options = []
    for e in elements:
        if e.get("enabled") is not True or e.get("offscreen") is not False:
            continue
        patterns = e.get("patterns", [])
        state = e.get("state", {})
        operations = []
        if "Invoke" in patterns:
            operations.append(("activate", {}))
        if "Toggle" in patterns:
            operations.append(("toggle", {}))
        if "SelectionItem" in patterns:
            operations.append(("select", {}))
        if "ExpandCollapse" in patterns:
            expanded = state.get("expand_collapse")
            if expanded in (0, 2):
                operations.append(("expand", {}))
            if expanded in (1, 2):
                operations.append(("collapse", {}))
        if e.get("is_password") is False:
            if "Value" in patterns and state.get("value_read_only") is False:
                operations.append(("set_value", {"value": "explicit user-supplied text"}))
            if "RangeValue" in patterns and state.get("range_read_only") is False:
                operations.append(("set_range_value", {"value": "explicit user-supplied number",
                    "minimum": state.get("range_minimum"), "maximum": state.get("range_maximum")}))
        if "Scroll" in patterns:
            if state.get("horizontal_scrollable") is True:
                operations.append(("scroll_horizontal", {"direction": "left or right"}))
            if state.get("vertical_scrollable") is True:
                operations.append(("scroll_vertical", {"direction": "up or down"}))
        for operation, requires in operations:
            options.append(dict(id=e["id"] + ":" + operation, element_id=e["id"],
                operation=operation, label=e.get("name") or "(unlabeled)", requires=requires))
    return options
