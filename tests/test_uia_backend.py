"""Deterministic contract tests using fake UIA providers, not live UIA evidence.

The fakes expose only read methods. Calls outside the metadata/value-read surface
fail immediately, making password and target traversal regressions observable.
"""
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ctrl_loop import uia_backend as backend
from ctrl_loop.uia_contract import build_options, new_snapshot


UNSUPPORTED = object()


def element(**changes):
    result = dict(id="snapshot:e1", name="Save", enabled=True, offscreen=False,
                  is_password=False, patterns=[], state={})
    result.update(changes)
    return result


def operations(item):
    return {option["operation"] for option in build_options([item])}


class FakeNode:
    def __init__(self, name="Fixture", *, password=False, patterns=("Invoke",),
                 cached=None, current=None):
        self.name = name
        self.cached = dict(Name=name, ControlType=50000, AutomationId="repeated-id",
                           IsEnabled=True, IsOffscreen=False, IsPassword=password,
                           HasKeyboardFocus=False, BoundingRectangle=(10, 20, 80, 30),
                           ValueIsReadOnly=False, RangeValueIsReadOnly=False)
        self.cached.update({"Is" + p + "PatternAvailable": True for p in patterns})
        self.cached.update(cached or {})
        self.current = {"IsPassword": password, "ValueValue": "fixture value",
                        "RangeValueValue": 42.0}
        self.current.update(current or {})
        self.calls = []

    def GetCachedPropertyValueEx(self, prop, ignore_default):
        self.calls.append(("cached", prop, ignore_default))
        if prop in ("ValueValue", "RangeValueValue", "LegacyIAccessibleValue"):
            raise AssertionError("Sensitive values must never be in the metadata cache")
        result = self.cached.get(prop, UNSUPPORTED)
        if isinstance(result, Exception):
            raise result
        return result

    def GetCurrentPropertyValueEx(self, prop, ignore_default):
        self.calls.append(("current", prop, ignore_default))
        result = self.current.get(prop, UNSUPPORTED)
        if isinstance(result, Exception):
            raise result
        return result


def reader_for(root=None, children=None, siblings=None):
    reader = backend.UIAReader.__new__(backend.UIAReader)
    reader.roles = {50000: "Button"}
    reader.property_id = lambda name: name
    reader.cache = object()
    reader.navigation = []
    children = children or {}
    siblings = siblings or {}

    def navigate(kind, node, cache):
        reader.navigation.append((kind, node.name))
        result = (children if kind == "child" else siblings).get(node)
        if isinstance(result, Exception):
            raise result
        return result

    reader.walker = SimpleNamespace(
        GetFirstChildElementBuildCache=lambda node, cache: navigate("child", node, cache),
        GetNextSiblingElementBuildCache=lambda node, cache: navigate("sibling", node, cache))
    reader.automation = SimpleNamespace(ElementFromHandleBuildCache=lambda hwnd, cache: root)
    return reader


class CapabilityContractTests(unittest.TestCase):
    def test_roles_labels_and_identifiers_never_invent_capabilities(self):
        for role in ("Button", "MenuItem", "Edit", "CheckBox"):
            with self.subTest(role=role):
                self.assertEqual(build_options([element(role=role, automation_id="click_me")]), [])

    def test_operations_require_known_enabled_onscreen_state(self):
        for enabled in (False, None, 1, "true"):
            self.assertEqual(operations(element(enabled=enabled, patterns=["Invoke"])), set())
        for offscreen in (True, None, 0, "false"):
            self.assertEqual(operations(element(offscreen=offscreen, patterns=["Invoke"])), set())
        self.assertEqual(operations(element(patterns=["Invoke", "Toggle", "SelectionItem"])),
                         {"activate", "toggle", "select"})

    def test_setters_require_explicit_nonpassword_and_writable_state(self):
        patterns = ["Value", "RangeValue"]
        writable = {"value_read_only": False, "range_read_only": False}
        for password in (True, None, 0):
            self.assertEqual(operations(element(patterns=patterns, state=writable,
                                                is_password=password)), set())
        for read_only in (True, None, 0):
            self.assertEqual(operations(element(patterns=patterns, state={
                "value_read_only": read_only, "range_read_only": read_only})), set())
        self.assertEqual(operations(element(patterns=patterns, state=writable)),
                         {"set_value", "set_range_value"})

    def test_expand_and_scroll_reflect_provider_state(self):
        expected = {0: {"expand"}, 1: {"collapse"}, 2: {"expand", "collapse"},
                    3: set(), None: set()}
        for state, allowed in expected.items():
            self.assertEqual(operations(element(patterns=["ExpandCollapse"],
                                                state={"expand_collapse": state})), allowed)
        self.assertEqual(operations(element(patterns=["Scroll"], state={
            "horizontal_scrollable": True, "vertical_scrollable": None})), {"scroll_horizontal"})
        self.assertEqual(operations(element(patterns=["Text", "ScrollItem", "VirtualizedItem"])), set())

    def test_ids_remain_snapshot_scoped_even_with_duplicate_names_and_automation_ids(self):
        reader = reader_for()
        node = FakeNode()
        first, second = new_snapshot(123, 456), new_snapshot(123, 456)
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        a = reader.read(node, first["snapshot_id"], 0, None)
        b = reader.read(node, first["snapshot_id"], 1, None)
        c = reader.read(node, second["snapshot_id"], 0, None)
        self.assertEqual(len({a["id"], b["id"], c["id"]}), 3)
        options = build_options([a, b, c])
        self.assertEqual(len({item["id"] for item in options}), 3)
        self.assertEqual({item["element_id"] for item in options}, {a["id"], b["id"], c["id"]})
        json.dumps([a, b, c, options], allow_nan=False)

    def test_unlabeled_supported_control_has_honest_fallback(self):
        self.assertEqual(build_options([element(name="", patterns=["Invoke"])])[0]["label"],
                         "(unlabeled)")


class PropertySafetyTests(unittest.TestCase):
    def test_runtime_identity_uses_read_only_method_when_cache_is_unavailable(self):
        node = FakeNode()
        node.GetRuntimeId = lambda: (42, 10, 20)
        data = reader_for().read(node, "snapshot", 0, None)
        self.assertEqual(data["runtime_id"], [42, 10, 20])
        node.cached["RuntimeId"] = (42, 11, 21)
        node.GetRuntimeId = lambda: (_ for _ in ()).throw(AssertionError("Cache already supplied identity"))
        self.assertEqual(reader_for().read(node, "snapshot", 0, None)["runtime_id"], [42, 11, 21])

    def test_initial_bulk_cache_never_prefetches_password_values(self):
        requested = []
        cache = SimpleNamespace(AddProperty=requested.append)
        automation = SimpleNamespace(CreateCacheRequest=lambda: cache, ControlViewWalker=object())
        properties = list(backend.META.values()) + list(backend.STATE.values())
        properties += ["Is" + name + "PatternAvailable" for name in backend.PATTERNS]
        module = SimpleNamespace(CUIAutomation8=object(), IUIAutomation2=object(),
                                 TreeScope_Element=1, AutomationElementMode_Full=0,
                                 **{"UIA_" + name + "PropertyId": name for name in properties})
        comtypes = ModuleType("comtypes")
        client = ModuleType("comtypes.client")
        client.GetModule = lambda path: module
        client.CreateObject = lambda cls, interface: automation
        comtypes.client = client
        with patch.dict(sys.modules, {"comtypes": comtypes, "comtypes.client": client}):
            reader = backend.UIAReader()
        self.assertNotIn("ValueValue", requested)
        self.assertNotIn("RangeValueValue", requested)
        self.assertNotIn("LegacyIAccessibleValue", requested)
        self.assertIn("IsPassword", requested)
        self.assertIn("IsInvokePatternAvailable", requested)
        self.assertIn("ValueIsReadOnly", requested)
        self.assertEqual(reader.cache.TreeScope, 1)

    def test_unknown_native_objects_and_nonfinite_numbers_are_not_serialized(self):
        self.assertIsNone(backend.primitive(UNSUPPORTED))
        for value in (float("nan"), float("inf"), float("-inf")):
            self.assertIsNone(backend.primitive(value))
        self.assertEqual(backend.primitive((True, 2, 3.5, "text", UNSUPPORTED)),
                         [True, 2, 3.5, "text", None])

    def test_password_or_unknown_status_never_queries_current_values(self):
        for password in (True, None, UNSUPPORTED):
            with self.subTest(password=password):
                node = FakeNode(password=password, patterns=("Value", "RangeValue"))
                result = reader_for().read(node, "snapshot", 0, None)
                self.assertIsNone(result["value"])
                self.assertFalse(any(kind == "current" for kind, _, _ in node.calls))
                self.assertFalse(operations(result))

    def test_cached_nonpassword_is_rechecked_before_value_read(self):
        for current_password in (True, None, UNSUPPORTED):
            with self.subTest(current_password=current_password):
                node = FakeNode(patterns=("Value",), current={"IsPassword": current_password})
                result = reader_for().read(node, "snapshot", 0, None)
                self.assertIsNone(result["value"])
                self.assertEqual([prop for kind, prop, _ in node.calls if kind == "current"],
                                 ["IsPassword"])
                self.assertFalse(operations(result))

    def test_failed_password_recheck_does_not_reuse_cached_false_for_setters(self):
        node = FakeNode(patterns=("Value",), current={"IsPassword": RuntimeError("stale")})
        result = reader_for().read(node, "snapshot", 0, None)
        self.assertIsNone(result["value"])
        self.assertFalse(operations(result))
        self.assertTrue(result["errors"])

    def test_value_read_occurs_only_after_fresh_explicit_false(self):
        for pattern, property_name, expected in (("Value", "ValueValue", "fixture value"),
                                                 ("RangeValue", "RangeValueValue", 42.0)):
            with self.subTest(pattern=pattern):
                node = FakeNode(patterns=(pattern,))
                result = reader_for().read(node, "snapshot", 0, None)
                self.assertEqual(result["value"], expected)
                self.assertEqual([prop for kind, prop, _ in node.calls if kind == "current"],
                                 ["IsPassword", property_name])
                self.assertTrue(all(ignore_default is True for _, _, ignore_default in node.calls))

    def test_text_is_bounded_and_property_failures_are_explicit(self):
        node = FakeNode(name="n" * 600, patterns=("Value",),
                        cached={"HasKeyboardFocus": RuntimeError("stale")},
                        current={"ValueValue": "v" * 600})
        result = reader_for().read(node, "snapshot", 0, None)
        self.assertEqual(len(result["name"]), backend.TEXT_LIMIT)
        self.assertEqual(len(result["value"]), backend.TEXT_LIMIT)
        self.assertIsNone(result["focused"])
        self.assertEqual(len(result["errors"]), 3)


class TargetTraversalTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(hwnd=123, pid=456, title="Owned fixture")

    def observe(self, reader, **kwargs):
        with patch.object(backend, "window_identity", return_value=self.identity):
            return reader.observe(123, 456, **kwargs)

    def test_depth_first_parentage_never_walks_target_window_siblings(self):
        root, a, b, grandchild, foreign = [FakeNode(name) for name in ("root", "a", "b", "grandchild", "foreign")]
        reader = reader_for(root, {root: a, a: grandchild}, {root: foreign, a: b})
        result = self.observe(reader)
        self.assertEqual([e["name"] for e in result["elements"]], ["root", "a", "grandchild", "b"])
        self.assertNotIn(("sibling", "root"), reader.navigation)
        self.assertFalse(foreign.calls)
        items = {e["name"]: e for e in result["elements"]}
        self.assertEqual(items["grandchild"]["parent_id"], items["a"]["id"])
        self.assertEqual(items["b"]["parent_id"], items["root"]["id"])
        self.assertEqual(result["status"], "complete")

    def test_element_budget_stops_before_fetching_another_node(self):
        root, child = FakeNode("root"), FakeNode("child")
        reader = reader_for(root, {root: child})
        result = self.observe(reader, max_elements=1)
        self.assertEqual(len(result["elements"]), 1)
        self.assertFalse(reader.navigation)
        self.assertFalse(child.calls)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["status"], "partial")

    def test_depth_budget_does_not_fetch_deeper_children(self):
        root, child, hidden = FakeNode("root"), FakeNode("child"), FakeNode("hidden")
        reader = reader_for(root, {root: child, child: hidden})
        result = self.observe(reader, max_depth=1)
        self.assertEqual([e["name"] for e in result["elements"]], ["root", "child"])
        self.assertNotIn(("child", "child"), reader.navigation)
        self.assertFalse(hidden.calls)
        self.assertTrue(any("Depth budget" in warning for warning in result["warnings"]))

    def test_elapsed_budget_returns_partial_without_reading_nodes(self):
        root = FakeNode()
        reader = reader_for(root)
        with patch.object(backend.time, "monotonic", side_effect=[0, 1, 1]):
            result = self.observe(reader, timeout=0.1)
        self.assertEqual(result["elements"], [])
        self.assertFalse(root.calls)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["truncated"])

    def test_budget_expiring_during_property_read_prevents_more_navigation(self):
        root, child = FakeNode("root"), FakeNode("child")
        reader = reader_for(root, {root: child})
        with patch.object(backend.time, "monotonic", side_effect=[0, 0, 1, 1]):
            result = self.observe(reader, timeout=0.1)
        self.assertEqual(len(result["elements"]), 1)
        self.assertFalse(reader.navigation)
        self.assertFalse(child.calls)
        self.assertEqual(result["status"], "partial")

    def test_target_disappearance_discards_candidates(self):
        reader = reader_for(FakeNode())
        with patch.object(backend, "window_identity", side_effect=[self.identity, None]):
            result = reader.observe(123, 456)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["options"], [])
        self.assertTrue(result["elements"])

    def test_closed_or_reused_handle_is_rejected_before_uia(self):
        for identity in (None, dict(hwnd=123, pid=999, title="Different process")):
            reader = reader_for(FakeNode())
            with patch.object(backend, "window_identity", return_value=identity):
                result = reader.observe(123, 456)
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["elements"], [])
            self.assertEqual(result["options"], [])

    def test_provider_navigation_failure_marks_partial_coverage(self):
        root = FakeNode()
        result = self.observe(reader_for(root, {root: RuntimeError("stale child")}))
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["truncated"])
        self.assertTrue(any("child became unavailable" in text for text in result["warnings"]))

    def test_initial_provider_failure_is_error_without_candidates(self):
        reader = reader_for()
        def fail(hwnd, cache):
            raise RuntimeError("provider failed")
        reader.automation.ElementFromHandleBuildCache = fail
        result = self.observe(reader)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["options"], [])


if __name__ == "__main__":
    unittest.main()
