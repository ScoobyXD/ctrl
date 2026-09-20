"""Offline loop tests: target freshness, argument bindings, and receipt semantics."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ctrl_loop.execution import (ExecutionError, ExecutionLoop, NeedsArguments, StaleTarget,
                                 bind_arguments, revalidate_selection, validate_request)
from ctrl_loop.uia_contract import build_options, new_snapshot


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
