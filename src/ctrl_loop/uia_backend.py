"""Direct Windows UI Automation reader, intended only for a disposable MTA process.

No pattern action interfaces are acquired. No focus/input/realization APIs are used.
All COM references die with the observation process; only JSON-safe data escapes.
"""
import ctypes
from ctypes import wintypes
import math
import sys
import time

from .uia_contract import new_snapshot, build_options

TEXT_LIMIT = 512
PATTERNS = ("Invoke", "Value", "RangeValue", "Toggle", "SelectionItem", "Selection",
            "ExpandCollapse", "Scroll", "ScrollItem", "Text", "TextEdit", "Grid",
            "GridItem", "Table", "TableItem", "Window", "Transform", "Dock",
            "VirtualizedItem", "ItemContainer", "LegacyIAccessible")
META = {
    "name": "Name", "role_id": "ControlType", "automation_id": "AutomationId",
    "class_name": "ClassName", "framework_id": "FrameworkId", "enabled": "IsEnabled",
    "focused": "HasKeyboardFocus", "offscreen": "IsOffscreen", "is_password": "IsPassword",
    "bounds": "BoundingRectangle",
    # Snapshot IDs are display identities only. RuntimeId lets execution match
    # the same native control in an independently collected current snapshot.
    "runtime_id": "RuntimeId",
}
STATE = {
    "value_read_only": "ValueIsReadOnly", "range_read_only": "RangeValueIsReadOnly",
    "range_minimum": "RangeValueMinimum", "range_maximum": "RangeValueMaximum",
    "toggle": "ToggleToggleState", "selected": "SelectionItemIsSelected",
    "expand_collapse": "ExpandCollapseExpandCollapseState",
    "horizontal_scrollable": "ScrollHorizontallyScrollable",
    "vertical_scrollable": "ScrollVerticallyScrollable",
}


def user32():
    dll = ctypes.WinDLL("user32", use_last_error=True)
    dll.IsWindow.argtypes = [wintypes.HWND]
    dll.IsWindowVisible.argtypes = [wintypes.HWND]
    dll.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    dll.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    dll.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    return dll


def window_identity(hwnd, dll=None):
    dll = dll or user32()
    if not dll.IsWindow(hwnd):
        return None
    pid = wintypes.DWORD()
    dll.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    # GetWindowText reads another process's top-level caption, not its edit controls.
    title = ctypes.create_unicode_buffer(513)
    dll.GetWindowTextW(hwnd, title, 513)
    return dict(hwnd=int(hwnd), pid=pid.value, title=title.value)


def list_windows():
    dll = user32()
    result = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    @callback_type
    def visit(hwnd, _):
        if dll.IsWindowVisible(hwnd):
            identity = window_identity(hwnd, dll)
            if identity and identity["title"]:
                result.append(identity)
        return True
    dll.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    dll.EnumWindows(visit, 0)
    return {"windows": sorted(result, key=lambda x: (x["title"].casefold(), x["hwnd"]))}


def primitive(value):
    """Reject UIA's unsupported sentinel/COM pointers instead of fabricating defaults."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [primitive(x) for x in value]
    return None


class UIAReader:
    def __init__(self):
        # Match CUA's physical desktop rectangles, including mixed-DPI desktops.
        # This helper has no windows; set awareness before COM initializes UIA.
        if sys.platform == "win32":
            dpi = ctypes.WinDLL("user32", use_last_error=True)
            dpi.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            dpi.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
            dpi.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        # comtypes initializes COM at import time. This process never owns Qt windows.
        sys.coinit_flags = 0
        import comtypes.client
        self.u = comtypes.client.GetModule("UIAutomationCore.dll")
        try:
            self.automation = comtypes.client.CreateObject(self.u.CUIAutomation8,
                                                         interface=self.u.IUIAutomation2)
            self.automation.ConnectionTimeout = 700
            self.automation.TransactionTimeout = 1200
        except (OSError, AttributeError, comtypes.COMError):
            self.automation = comtypes.client.CreateObject(self.u.CUIAutomation,
                                                          interface=self.u.IUIAutomation)
        self.cache = self.automation.CreateCacheRequest()
        self.cache.TreeScope = self.u.TreeScope_Element
        # Full references are local and needed to walk to the next node, never serialized.
        self.cache.AutomationElementMode = self.u.AutomationElementMode_Full
        self.walker = self.automation.ControlViewWalker
        names = list(META.values()) + list(STATE.values())
        names += ["Is" + name + "PatternAvailable" for name in PATTERNS]
        for name in names:
            self.cache.AddProperty(self.property_id(name))
        self.roles = {getattr(self.u, name): name[4:-13] for name in dir(self.u)
                      if name.startswith("UIA_") and name.endswith("ControlTypeId")}

    def property_id(self, name):
        return getattr(self.u, "UIA_" + name + "PropertyId")

    def read(self, node, snapshot_id, index, parent_id):
        errors = []
        def cached(name):
            try:
                value = primitive(node.GetCachedPropertyValueEx(self.property_id(name), True))
                if isinstance(value, str) and len(value) > TEXT_LIMIT:
                    errors.append(name + " text truncated at 512 characters")
                    return value[:TEXT_LIMIT]
                return value
            except Exception:
                errors.append(name + " unavailable (provider or stale element)")
                return None
        data = {key: cached(name) for key, name in META.items()}
        if not isinstance(data["runtime_id"], list) or not data["runtime_id"]:
            # Some WinForms providers omit the cached RuntimeId property while
            # exposing the read-only IUIAutomationElement.GetRuntimeId method.
            try:
                data["runtime_id"] = primitive(node.GetRuntimeId())
            except Exception:
                data["runtime_id"] = None
        role_id = data.pop("role_id")
        data["role"] = self.roles.get(role_id, "Unknown")
        data["state"] = {key: cached(name) for key, name in STATE.items()}
        data["patterns"] = [name for name in PATTERNS if cached("Is" + name + "PatternAvailable") is True]
        data.update(id=f"{snapshot_id}:e{index}", parent_id=parent_id,
                    source="windows-uia", value=None, errors=errors)
        # Crucially no value properties are in the metadata cache above. Unknown password
        # state is treated conservatively. Recheck current password status before value read.
        if data["is_password"] is False and any(p in data["patterns"] for p in ("Value", "RangeValue")):
            data["is_password"] = None
            try:
                is_password = primitive(node.GetCurrentPropertyValueEx(self.property_id("IsPassword"), True))
                data["is_password"] = is_password
                if is_password is False:
                    name = "ValueValue" if "Value" in data["patterns"] else "RangeValueValue"
                    value = primitive(node.GetCurrentPropertyValueEx(self.property_id(name), True))
                    if isinstance(value, str) and len(value) > TEXT_LIMIT:
                        errors.append("Value text truncated at 512 characters")
                        value = value[:TEXT_LIMIT]
                    data["value"] = value
                else:
                    data["is_password"] = is_password
            except Exception:
                errors.append("Value unavailable (provider or stale element)")
        return data

    def observe(self, hwnd, pid, max_elements=1000, timeout=8, max_depth=40):
        start = time.monotonic()
        deadline = start + timeout
        result = new_snapshot(hwnd, pid)
        target = window_identity(hwnd)
        if not target or target["pid"] != pid:
            result.update(status="error", error="Target closed or changed. Refresh the window list.")
            return result
        result["target"] = target
        limited = set()
        try:
            node = self.automation.ElementFromHandleBuildCache(hwnd, self.cache)
            # Frames represent an element plus the next traversal stage. No subtree-wide
            # cache request or FindAll is made before applying the element budget.
            stack = [(node, None, 0, False)]
            while stack:
                if len(result["elements"]) >= max_elements:
                    limited.add("Element budget reached; remaining tree was not read.")
                    break
                if time.monotonic() >= deadline:
                    limited.add("Time budget reached; remaining tree was not read.")
                    break
                node, parent_id, depth, siblings = stack.pop()
                if not node:
                    continue
                item = self.read(node, result["snapshot_id"], len(result["elements"]), parent_id)
                item["depth"] = depth
                result["elements"].append(item)
                # Each navigation call fetches only one node's bounded metadata.
                if len(result["elements"]) >= max_elements:
                    limited.add("Element budget reached; traversal stopped conservatively.")
                    break
                if time.monotonic() >= deadline:
                    limited.add("Time budget reached; traversal stopped conservatively.")
                    break
                if siblings:
                    try:
                        sibling = self.walker.GetNextSiblingElementBuildCache(node, self.cache)
                        if sibling:
                            stack.append((sibling, parent_id, depth, True))
                    except Exception:
                        limited.add("A sibling became unavailable; coverage is incomplete.")
                if depth >= max_depth:
                    limited.add("Depth budget reached; deeper controls were not read.")
                elif time.monotonic() < deadline:
                    try:
                        child = self.walker.GetFirstChildElementBuildCache(node, self.cache)
                        if child:
                            stack.append((child, item["id"], depth + 1, True))
                    except Exception:
                        limited.add("A child became unavailable; coverage is incomplete.")
                else:
                    limited.add("Time budget reached; child controls were not read.")
            latest = window_identity(hwnd)
            if not latest or latest["pid"] != pid:
                result.update(status="error", error="Target disappeared during observation; refresh required.")
                result["options"] = []
            else:
                result["options"] = build_options(result["elements"])
        except Exception:
            result.update(status="error", error="Windows UIA could not read this target (closed, inaccessible or provider failure).")
        result["truncated"] = bool(limited)
        result["warnings"] = sorted(limited)
        if any(e["errors"] for e in result["elements"]):
            result["warnings"].append("Some properties were unavailable or text was truncated; inspect control details.")
        if result["status"] != "error" and result["warnings"]:
            result["status"] = "partial"
        result["elapsed_ms"] = round((time.monotonic() - start) * 1000)
        result["coverage"] = "Exposed control-view tree only; completeness of application content is not guaranteed."
        return result
