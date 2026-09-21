"""Offline loop tests: target freshness, argument bindings, and receipt semantics."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ctrl_loop.execution import (ExecutionError, ExecutionLoop, NeedsArguments, StaleTarget,
                                 bind_arguments, revalidate_selection, validate_request)
from ctrl_loop import jev
from ctrl_loop.uia_contract import build_options, new_snapshot
from ctrl_loop.window_selection import WindowSelectionSearch


def observation(name="X tab", *, pattern="SelectionItem", state=None, runtime=None, value=None):
    data = new_snapshot(101, 202)
    data["elements"] = [{"id": data["snapshot_id"] + ":e0", "parent_id": None,
                         "name": name, "role": "TabItem", "automation_id": "fixture-tab",
                         "class_name": "TestTab", "framework_id": "Test",
                         "runtime_id": runtime or [42, 12], "enabled": True, "offscreen": False,
                         "is_password": False, "patterns": [pattern], "state": state or {},
                         "bounds": [10, 20, 100, 30], "value": value}]
    data["options"] = build_options(data["elements"])
    return data


def request(**updates):
    result = {"target": {"hwnd": 101, "pid": 202}, "goal": "Open X bookmarks", "run_id": "fixture",
              "max_steps": 2, "max_seconds": 60, "arguments": {}}
    result.update(updates)
    return result


def selector(snapshot, goal, history):
    return {"status": "selected", "snapshot_id": snapshot["snapshot_id"], "goal": goal,
            "input_actions": 0, "selected_option": snapshot["options"][0]}


class Executor:
    def __init__(self, receipt=None):
        self.calls = []
        self.receipt = receipt or {"status": "delivered", "attempted": True}

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.receipt


class WindowDecider:
    """Offline Jev response using the actual question and request binding."""

    def __init__(self, choose):
        self.choose = choose
        self.calls = []

    def __call__(self, payload, *, request_id, capture_id):
        self.calls.append(deepcopy(payload))
        criteria = payload["questions"]["window"]["criteria"]
        choice = self.choose(payload)
        probabilities = {key: (.8 if key == choice else .2 / (len(criteria) - 1))
                         for key in criteria}
        return {"model": jev.RESOLVED_MODEL, "provider": jev.PROVIDER,
                "provider_request_id": "gen-offline-window", "latency_ms": 1.0,
                "usage": {"input_tokens": 20, "output_tokens": 5, "cost": 0},
                "answers": {"window": {"type": "choice", "choice": choice,
                                       "confidence": probabilities[choice],
                                       "probabilities": probabilities}},
                "local_request": jev.request_binding(payload, request_id=request_id,
                                                     capture_id=capture_id)}


class AutomaticWindowTests(unittest.TestCase):
    editor = {"hwnd": 303, "pid": 404, "title": "Commands.txt - Notepad++"}
    browser = {"hwnd": 101, "pid": 202, "title": "X - Google Chrome"}

    def setUp(self):
        network = patch.object(jev, "build_opener", side_effect=AssertionError("offline test attempted network"))
        network.start()
        self.addCleanup(network.stop)

    def run_auto(self, *, inventory=None, choose=None, alter_selection=None, cancelled=None,
                 clock=None, selector_cancelled=None, observation_target=None):
        trace = {"inventories": [], "window_selections": [], "observed_targets": [],
                 "control_selections": [], "events": []}
        decider = WindowDecider(choose or (lambda payload: "window_1"))
        executor = Executor()
        raw = {"windows": [deepcopy(self.editor), deepcopy(self.browser)]}

        def list_windows():
            trace["inventories"].append(True)
            return deepcopy(inventory(len(trace["inventories"])) if inventory else raw)

        def select_window(inventory, goal):
            trace["window_selections"].append((deepcopy(inventory), goal))
            result = WindowSelectionSearch(inventory, goal).run(decider, cancelled=selector_cancelled)
            if alter_selection:
                alter_selection(result, inventory)
            return result

        def observe(target):
            trace["observed_targets"].append(deepcopy(target))
            snapshot = observation("Notifications" if len(trace["observed_targets"]) == 3 else "X tab")
            snapshot["target"] = deepcopy(observation_target or self.browser)
            return snapshot

        def select_control(snapshot, goal, history):
            trace["control_selections"].append((deepcopy(snapshot), goal, deepcopy(history)))
            return selector(snapshot, goal, history)

        kwargs = {"window_inventory": list_windows, "window_selector": select_window,
                  "cancelled": cancelled,
                  "emit": lambda event, data: trace["events"].append((event, deepcopy(data)))}
        if clock is not None:
            kwargs["clock"] = clock
        loop = ExecutionLoop(observe, select_control, executor, **kwargs)
        req = request(max_steps=1)
        del req["target"]
        result = loop.run(req)
        trace["model_calls"] = decider.calls
        return result, executor, trace

    def assert_no_control_or_input(self, result, executor, trace):
        self.assertEqual(trace["observed_targets"], [])
        self.assertEqual(trace["control_selections"], [])
        self.assertEqual(executor.calls, [])
        self.assertEqual(result["input_actions"], 0)
        self.assertEqual(result["attempted_actions"], 0)
        self.assertFalse(result["goal_completed"])

    def test_goal_chooses_browser_over_first_editor_and_propagates_target(self):
        result, executor, trace = self.run_auto()
        self.assertEqual(result["status"], "limit_reached")
        self.assertEqual(result["input_actions"], 1)
        self.assertEqual(result["target"], self.browser)
        self.assertEqual(result["window_selection"]["selected_window"], self.browser)
        self.assertEqual(len(trace["inventories"]), 2)
        self.assertEqual(len(trace["model_calls"]), 1)
        self.assertEqual(trace["window_selections"][0][0]["windows"], [self.editor, self.browser])
        self.assertEqual(trace["model_calls"][0]["state"]["goal"], "Open X bookmarks")
        self.assertEqual(trace["observed_targets"], [self.browser] * 3)
        self.assertEqual(trace["control_selections"][0][0]["target"], self.browser)
        self.assertEqual(trace["control_selections"][0][1:], ("Open X bookmarks", []))
        self.assertEqual(executor.calls[0][0][0], self.browser)
        self.assertEqual(executor.calls[0][0][2], "select")
        events = [event for event, _ in trace["events"]]
        self.assertLess(events.index("window_inventory"), events.index("window_selection"))
        self.assertLess(events.index("window_selection"), events.index("observation"))

    def test_no_match_and_ambiguous_windows_stop_before_observation(self):
        for choice, expected in (("__none__", "no_window"), ("__ambiguous__", "ambiguous_window")):
            with self.subTest(choice=choice):
                result, executor, trace = self.run_auto(choose=lambda payload: choice)
                self.assertEqual(result["status"], expected)
                self.assertEqual(len(trace["inventories"]), 1)
                self.assertEqual(len(trace["model_calls"]), 1)
                self.assert_no_control_or_input(result, executor, trace)

    def test_selected_window_disappearance_identity_or_title_change_stops(self):
        changed_windows = [[], [self.editor],
                           [self.editor, {**self.browser, "pid": 999}],
                           [self.editor, {**self.browser, "hwnd": 999}],
                           [self.editor, {**self.browser, "title": "Other page - Google Chrome"}]]
        for windows in changed_windows:
            with self.subTest(windows=windows):
                result, executor, trace = self.run_auto(
                    inventory=lambda count: {"windows": [self.editor, self.browser] if count == 1 else windows})
                self.assertEqual(result["status"], "stale_target")
                self.assertEqual(len(trace["inventories"]), 2)
                self.assert_no_control_or_input(result, executor, trace)

    def test_inventory_reordering_preserves_selected_native_identity(self):
        result, executor, trace = self.run_auto(inventory=lambda count: {
            "windows": [self.editor, self.browser] if count == 1 else [self.browser, self.editor]})
        self.assertEqual(result["input_actions"], 1)
        self.assertEqual(trace["observed_targets"], [self.browser] * 3)
        self.assertEqual(executor.calls[0][0][0], self.browser)

    def test_initial_observation_cannot_switch_to_another_native_identity(self):
        for field, value in (("pid", 999), ("hwnd", 999), ("title", "Other page - Google Chrome")):
            with self.subTest(field=field):
                result, executor, trace = self.run_auto(observation_target={**self.browser, field: value})
                self.assertEqual(result["status"], "stale_target")
                self.assertEqual(len(trace["observed_targets"]), 1)
                self.assertEqual(trace["control_selections"], [])
                self.assertEqual(executor.calls, [])

    def test_fabricated_or_unbound_window_selection_never_reaches_controls(self):
        alterations = [
            lambda result, inventory: result.update(inventory_id="0" * 32),
            lambda result, inventory: result.update(goal="Another goal"),
            lambda result, inventory: result.update(input_actions=1),
            lambda result, inventory: result.update(request_count=2),
            lambda result, inventory: result.update(selected_window_id="window_0"),
            lambda result, inventory: result.update(selected_window=None),
            lambda result, inventory: result["selected_window"].update(hwnd=999),
            lambda result, inventory: result["selected_window"].update(pid=999),
            lambda result, inventory: result["selected_window"].update(title="Fabricated title"),
            lambda result, inventory: result["decision"]["local_request"].update(capture_id="0" * 32),
        ]
        for index, alter in enumerate(alterations):
            with self.subTest(alteration=index):
                result, executor, trace = self.run_auto(alter_selection=alter)
                self.assertEqual(result["status"], "failed")
                self.assert_no_control_or_input(result, executor, trace)

    def test_selector_cannot_mutate_inventory_to_invent_native_target(self):
        def alter(result, inventory):
            inventory["windows"][1]["hwnd"] = 999
            result["selected_window"]["hwnd"] = 999
        result, executor, trace = self.run_auto(alter_selection=alter)
        self.assertEqual(result["status"], "failed")
        self.assert_no_control_or_input(result, executor, trace)

    def test_cancellation_and_deadline_at_each_window_stage_prevent_controls(self):
        for stop_kind in ("cancel", "deadline"):
            for stage in ("before_inventory", "inventory", "model", "revalidation"):
                with self.subTest(stop_kind=stop_kind, stage=stage):
                    stopped, now = [False], [0]
                    def stop():
                        if stop_kind == "cancel":
                            stopped[0] = True
                        else:
                            now[0] = 61
                    def inventory(count):
                        if stage == "inventory" or stage == "revalidation" and count == 2:
                            stop()
                        return {"windows": [self.editor, self.browser]}
                    def choose(payload):
                        if stage == "model":
                            stop()
                        return "window_1"
                    if stage == "before_inventory":
                        if stop_kind == "cancel":
                            stopped[0] = True
                        else:
                            ticks = iter((0, 61))
                    clock = (lambda: next(ticks, 61)) if stage == "before_inventory" and stop_kind == "deadline" else (lambda: now[0])
                    result, executor, trace = self.run_auto(
                        inventory=inventory, choose=choose, cancelled=lambda: stopped[0], clock=clock)
                    self.assertEqual(result["status"], "cancelled" if stop_kind == "cancel" else "limit_reached")
                    self.assert_no_control_or_input(result, executor, trace)

    def test_window_selector_cancellation_and_request_byte_budget_stop(self):
        result, executor, trace = self.run_auto(selector_cancelled=lambda: True)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(trace["model_calls"], [])
        self.assert_no_control_or_input(result, executor, trace)
        result, executor, trace = self.run_auto(
            inventory=lambda count: {"windows": [{**self.browser, "title": "x" * 24000}]})
        self.assertEqual(result["status"], "limit_reached")
        self.assertEqual(trace["model_calls"], [])
        self.assert_no_control_or_input(result, executor, trace)

    def test_empty_and_oversized_inventory_never_defaults_to_first_window(self):
        result, executor, trace = self.run_auto(inventory=lambda count: {"windows": []})
        self.assertEqual(result["status"], "no_window")
        self.assertEqual(trace["model_calls"], [])
        self.assert_no_control_or_input(result, executor, trace)
        windows = [{"hwnd": index + 1, "pid": 202, "title": "X - Google Chrome"} for index in range(65)]
        result, executor, trace = self.run_auto(inventory=lambda count: {"windows": windows})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(trace["window_selections"], [])
        self.assert_no_control_or_input(result, executor, trace)


class FreshnessTests(unittest.TestCase):
    def test_revalidation_uses_current_bounds_and_runtime_identity(self):
        a, b = observation(), observation()
        b["elements"][0]["bounds"] = [100, 200, 120, 40]
        self.assertIs(revalidate_selection(a, b, a["options"][0]), b["elements"][0])

    def test_rejects_reused_snapshot_runtime_ambiguity_and_changed_context(self):
        a = observation()
        variations = [deepcopy(a)]
        for mutate in (
            lambda s: s["elements"][0].update(runtime_id=None),
            lambda s: s["elements"].append(deepcopy(s["elements"][0])),
            lambda s: s["elements"][0].update(name="Another tab"),
            lambda s: s["elements"][0].update(enabled=False),
            lambda s: s["elements"][0].update(offscreen=True),
            lambda s: s["elements"][0].update(state={"selected": True}),
            lambda s: s["elements"][0].update(patterns=[]),
            lambda s: s["target"].update(pid=999),
        ):
            b = observation()
            mutate(b)
            variations.append(b)
        for b in variations:
            with self.subTest(snapshot=b):
                with self.assertRaises(StaleTarget):
                    revalidate_selection(a, b, a["options"][0])

    def test_never_accepts_fabricated_option(self):
        a, b = observation(), observation()
        selected = dict(a["options"][0], operation="activate")
        with self.assertRaises(StaleTarget):
            revalidate_selection(a, b, selected)


class ArgumentTests(unittest.TestCase):
    def test_missing_or_ambiguous_text_is_not_invented(self):
        element = observation()["elements"][0]
        for args in ({}, {"bindings": [{"operation": "set_value", "name": "wrong", "value": "text"}]}):
            with self.assertRaises(NeedsArguments):
                bind_arguments("set_value", element, args)
        binding = {"operation": "set_value", "automation_id": "fixture-tab", "value": ""}
        valid = validate_request(request(arguments={"bindings": [binding]}))
        self.assertEqual(bind_arguments("set_value", element, valid["arguments"]), {"value": ""})
        with self.assertRaises(NeedsArguments):
            bind_arguments("set_value", element, {"bindings": [binding, binding]})
        with self.assertRaises(NeedsArguments):
            bind_arguments("set_value", element, {"bindings": [binding]}, {"elements": [element, element]})

    def test_number_range_and_axis_validation(self):
        element = observation(state={"range_minimum": 0, "range_maximum": 10})["elements"][0]
        binding = {"operation": "set_range_value", "name": "X tab", "value": 11}
        with self.assertRaises(NeedsArguments):
            bind_arguments("set_range_value", element, {"bindings": [binding]})
        for updates in ({"value": float("nan")}, {"value": True},
                        {"operation": "scroll_vertical", "direction": "left"}):
            with self.assertRaises(ExecutionError):
                validate_request(request(arguments={"bindings": [{**binding, **updates}]}))

    def test_limits_and_unknown_fields_fail_before_observation(self):
        for updates in ({"max_steps": 0}, {"max_steps": 21}, {"max_seconds": 99999},
                        {"goal": ""}, {"target": {"pid": True, "hwnd": 1}}, {"surprise": True}):
            with self.assertRaises(ExecutionError):
                validate_request(request(**updates))


class LoopTests(unittest.TestCase):
    def run_loop(self, snapshots, *, executor=None, select=selector, cancelled=None, req=None):
        pending = iter(snapshots)
        events = []
        executor = executor or Executor()
        loop = ExecutionLoop(lambda _: next(pending), select, executor,
                             emit=lambda event, data: events.append((event, deepcopy(data))), cancelled=cancelled)
        result = loop.run(req or request())
        return result, executor, events

    def test_two_step_navigation_observes_after_each_delivered_action(self):
        first, validation = observation(), observation()
        next_step = observation("Bookmarks", pattern="Invoke", runtime=[42, 13])
        next_validation = observation("Bookmarks", pattern="Invoke", runtime=[42, 13])
        after = observation("Saved bookmarks", pattern="Invoke", runtime=[42, 14])
        result, executor, events = self.run_loop([first, validation, next_step, next_validation, after])
        self.assertEqual(result["input_actions"], 2)
        self.assertEqual(result["status"], "limit_reached")
        self.assertFalse(result["goal_completed"])
        self.assertEqual([call[0][2] for call in executor.calls], ["select", "activate"])
        self.assertEqual([e[1]["phase"] for e in events if e[0] == "observation"],
                         ["initial", "validation", "after_action", "validation", "after_action"])

    def test_stale_selection_never_dispatches(self):
        result, executor, _ = self.run_loop([observation(), observation(runtime=[99, 99])])
        self.assertEqual(result["status"], "stale_target")
        self.assertEqual(executor.calls, [])

    def test_cancel_during_selection_never_dispatches(self):
        stopped = []
        def select(*args):
            stopped.append(True)
            return selector(*args)
        result, executor, _ = self.run_loop([observation()], select=select, cancelled=lambda: bool(stopped))
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(executor.calls, [])

    def test_no_observed_change_stops_without_claiming_success(self):
        result, executor, _ = self.run_loop([observation(), observation(), observation()])
        self.assertEqual(result["status"], "no_progress")
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(result["input_actions"], 1)
        self.assertFalse(result["goal_completed"])

    def test_runtime_id_churn_is_not_semantic_progress(self):
        result, executor, _ = self.run_loop([observation(), observation(), observation(runtime=[99, 99])])
        self.assertEqual(result["status"], "no_progress")
        self.assertEqual(len(executor.calls), 1)

    def test_unknown_outcome_not_retried_even_when_observation_changed(self):
        executor = Executor({"status": "unknown", "attempted": True})
        result, executor, _ = self.run_loop([observation(), observation(), observation("Changed")], executor=executor)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["attempted_actions"], 1)
        self.assertEqual(result["input_actions"], 0)
        self.assertEqual(len(executor.calls), 1)
        self.assertTrue(result["actions"][0]["observed_change"])

    def test_argument_pause_preserves_selection_and_sends_no_input(self):
        def text_snapshot():
            return observation("Note", pattern="Value", state={"value_read_only": False})
        result, executor, events = self.run_loop([text_snapshot(), text_snapshot()])
        self.assertEqual(result["status"], "needs_arguments")
        self.assertFalse(executor.calls)
        self.assertTrue(any(event == "selection" for event, _ in events))

    def test_no_match_does_not_mean_goal_completed(self):
        def no_match(s, goal, history):
            return {**selector(s, goal, history), "status": "no_match", "selected_option": None}
        result, executor, _ = self.run_loop([observation()], select=no_match)
        self.assertEqual(result["status"], "no_match")
        self.assertFalse(result["goal_completed"])
        self.assertFalse(executor.calls)

    def test_clock_expiry_during_selection_prevents_input(self):
        now = [0]
        def select(*args):
            now[0] = 61
            return selector(*args)
        executor = Executor()
        loop = ExecutionLoop(lambda _: observation(), select, executor, clock=lambda: now[0])
        result = loop.run(request())
        self.assertEqual(result["status"], "limit_reached")
        self.assertFalse(executor.calls)


if __name__ == "__main__":
    unittest.main()
