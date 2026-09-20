"""Offline selector correctness tests; no provider, credentials, or UI actions.

The fake decider uses the real pinned response envelope and request binding. Its
judgments are scripted ground truth, not evidence of live model accuracy.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ctrl_loop import jev
from ctrl_loop import selection
from ctrl_loop.selection import (MAX_SELECTION_REQUEST_BYTES, SelectionConfig,
                                 SelectionContextError, SelectionError, SelectionSearch)


def snapshot(count, *, snapshot_id="selection_fixture"):
    elements = []
    options = []
    for index in range(count):
        element_id = f"{snapshot_id}:e{index}"
        elements.append({
            "id": element_id, "parent_id": None, "name": "Opaque control",
            "role": "Button", "automation_id": "duplicate-automation-id",
            "framework_id": "Fixture", "class_name": "FixtureButton",
            "enabled": True, "focused": False, "offscreen": False,
            "is_password": False, "bounds": [10, 20 + index, 100, 30],
            "source": "windows-uia", "patterns": ["Invoke"], "state": {},
            "value": None, "errors": [],
        })
        options.append({
            "id": element_id + ":activate", "element_id": element_id,
            "operation": "activate", "label": "Opaque control", "requires": {},
        })
    return {
        "schema_version": 1, "snapshot_id": snapshot_id, "source": "windows-uia",
        "target": {"hwnd": 101, "pid": 202, "title": "Owned selector fixture"},
        "started_at": "2026-09-20T00:00:00+00:00", "elapsed_ms": 1,
        "status": "complete", "truncated": False, "warnings": [], "error": None,
        "elements": elements, "options": options,
    }


def choice_answer(criteria, choice, probabilities=None):
    if probabilities is None:
        other_probability = .2 / (len(criteria) - 1)
        probabilities = {key: (.8 if key == choice else other_probability) for key in criteria}
    return {"type": "choice", "choice": choice, "confidence": probabilities[choice],
            "probabilities": probabilities}


def request_body(payload):
    return json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")


def browser_snapshot(count):
    observed = snapshot(count, snapshot_id="e0fb0105a5a1480d96043d7dfefc5263")
    parent = None
    for index in range(4):
        ancestor = deepcopy(observed["elements"][0])
        ancestor.update(id=f"{observed['snapshot_id']}:ancestor{index}", parent_id=parent,
                        name="Synthetic browser page and document context " * 4,
                        role="Pane", patterns=[], value="Synthetic document value " * 4)
        observed["elements"].append(ancestor)
        parent = ancestor["id"]
    for element in observed["elements"][:count]:
        element["parent_id"] = parent
    return observed


class FakeDecider:
    """Thread-safe, normalized Jev fake whose policies see the actual payload."""

    def __init__(self, *, classify=None, choose=None, delay=0):
        self.classify = classify or (lambda candidate, payload: "promising")
        self.choose = choose or (lambda criteria, payload: next(k for k in criteria if k != "__none__"))
        self.delay = delay
        self.calls = []
        self.lock = threading.Lock()
        self.active = 0
        self.maximum_active = 0

    def __call__(self, payload, *, request_id, capture_id):
        with self.lock:
            self.calls.append({"payload": deepcopy(payload), "request_id": request_id,
                               "capture_id": capture_id})
            sequence = len(self.calls)
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            answers = {}
            for question_id, question in payload["questions"].items():
                criteria = question["criteria"]
                if question_id.startswith("classify_"):
                    index = int(question_id.removeprefix("classify_"))
                    selected = self.classify(payload["state"]["candidates"][index], payload)
                else:
                    selected = self.choose(criteria, payload)
                if isinstance(selected, tuple):
                    selected, probabilities = selected
                else:
                    probabilities = None
                answers[question_id] = choice_answer(criteria, selected, probabilities)
            result = {
                "model": jev.RESOLVED_MODEL, "provider": jev.PROVIDER,
                "provider_request_id": f"gen-offline-{sequence}", "latency_ms": 1.0,
                "usage": {"input_tokens": 20, "output_tokens": 5, "cost": 0},
                "answers": answers,
                "local_request": jev.request_binding(payload, request_id=request_id,
                                                       capture_id=capture_id),
            }
            # Keep the fake honest: malformed fake distributions fail at their source.
            return jev.validate_decision(result, payload["questions"])
        finally:
            with self.lock:
                self.active -= 1

    def classified_ids(self):
        return [candidate["id"] for call in self.calls
                if any(key.startswith("classify_") for key in call["payload"]["questions"])
                for candidate in call["payload"]["state"]["candidates"]]

    def action_calls(self):
        return [call for call in self.calls if "action" in call["payload"]["questions"]]


class SelectionTests(unittest.TestCase):
    def setUp(self):
        # Even an accidental use of the real adapter cannot make a network call.
        self.network = patch.object(jev, "build_opener", side_effect=AssertionError("offline test attempted network"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_preview_covers_every_candidate_without_lexical_filter_or_io(self):
        observed = snapshot(1100)
        preview = SelectionSearch(observed, "Resolve the blue bird's account").preview()
        requests = preview["initial_requests"]
        candidate_ids = [candidate_id for request in requests for candidate_id in request["input_ids"]]
        self.assertEqual(candidate_ids, [option["id"] for option in observed["options"]])
        self.assertEqual(len(set(candidate_ids)), 1100)
        for request in requests:
            self.assertEqual(request["stage"], "classify")
            self.assertEqual(request["capture_id"], observed["snapshot_id"])
            self.assertLessEqual(len(request["input_ids"]), 64)
            self.assertLessEqual(len(request_body(request["payload"])), MAX_SELECTION_REQUEST_BYTES)
            binding = jev.request_binding(request["payload"], request_id=request["request_id"],
                                          capture_id=request["capture_id"])
            self.assertEqual(binding["capture_id"], observed["snapshot_id"])
            for question in request["payload"]["questions"].values():
                self.assertEqual(set(question["criteria"]), {"promising", "uncertain", "unlikely"})
        json.dumps(preview, allow_nan=False)

    def test_late_nonkeyword_target_survives_more_than_a_thousand_candidates(self):
        observed = snapshot(1100)
        target = observed["options"][-1]["id"]
        decider = FakeDecider(choose=lambda criteria, payload: target if target in criteria else next(
            key for key in criteria if key != "__none__"))
        result = SelectionSearch(observed, "Resolve the blue bird's account",
                                 config=SelectionConfig(max_requests=256)).run(decider)
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["selected_option"]["id"], target)
        self.assertCountEqual(decider.classified_ids(), [option["id"] for option in observed["options"]])
        self.assertTrue(any(call["payload"]["state"]["stage"] == "reduce" for call in decider.action_calls()))
        final = decider.action_calls()[-1]["payload"]["questions"]["action"]["criteria"]
        self.assertIn(target, final)
        self.assertIn("__none__", final)
        self.assertLessEqual(len(final), 255)
        json.dumps(result, allow_nan=False)

    def test_uncertain_candidate_remains_available_to_final_choice(self):
        observed = snapshot(90)
        target = observed["options"][-1]["id"]
        decider = FakeDecider(
            classify=lambda candidate, payload: "uncertain" if candidate["id"] == target else "promising",
            choose=lambda criteria, payload: target if target in criteria else "__none__")
        result = SelectionSearch(observed, "A task unrelated to control names").run(decider)
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["selected_option"]["id"], target)
        self.assertIn(target, decider.action_calls()[-1]["payload"]["questions"]["action"]["criteria"])

    def test_probabilities_are_compared_only_within_their_own_group(self):
        observed = snapshot(320)
        search = SelectionSearch(observed, "Select by model judgment", config=SelectionConfig(
            batch_size=64, keep_per_group=1))
        groups = search._make_requests(search._ids, "reduce", 1)
        self.assertGreater(len(groups), 1)
        first_group_winner = observed["options"][0]["id"]
        later_group_winner = groups[1]["input_ids"][0]
        def choose(criteria, payload):
            ids = [key for key in criteria if key != "__none__"]
            if payload["state"]["stage"] == "reduce":
                chosen = ids[0]
                # First group's winner has lower probability than many options in
                # another group. It still gets that group's retained slot.
                confidence = max(.04, 1 / len(criteria) + .01) if chosen == first_group_winner else .90
                remainder = (1 - confidence) / (len(criteria) - 1)
                return chosen, {key: confidence if key == chosen else remainder for key in criteria}
            self.assertIn(first_group_winner, criteria)
            self.assertIn(later_group_winner, criteria)
            return first_group_winner
        decider = FakeDecider(choose=choose)
        result = search.run(decider)
        self.assertEqual(result["selected_option"]["id"], first_group_winner)

    def test_final_none_recovers_a_candidate_classified_unlikely(self):
        observed = snapshot(80)
        target = observed["options"][-1]["id"]
        decider = FakeDecider(
            classify=lambda candidate, payload: "unlikely" if candidate["id"] == target else "promising",
            choose=lambda criteria, payload: target if target in criteria else (
                "__none__" if payload["state"]["stage"].endswith("final") else
                next(key for key in criteria if key != "__none__")))
        result = SelectionSearch(observed, "Recover the omitted correct target").run(decider)
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["selected_option"]["id"], target)
        stages = [call["payload"]["state"]["stage"] for call in decider.action_calls()]
        self.assertIn("final", stages)
        self.assertIn("recovery_final", stages)

    def test_exhausted_none_never_invents_an_action(self):
        decider = FakeDecider(choose=lambda criteria, payload: "__none__")
        result = SelectionSearch(snapshot(12), "Nothing here advances the task").run(decider)
        self.assertEqual(result["status"], "no_match")
        self.assertIsNone(result["selected_option"])

    def test_final_none_recovers_a_target_omitted_by_group_narrowing(self):
        observed = snapshot(320)
        target = observed["options"][-1]["id"]
        def choose(criteria, payload):
            stage = payload["state"]["stage"]
            if stage == "reduce":
                return next(key for key in criteria if key != "__none__")
            if stage == "final":
                self.assertNotIn(target, criteria)
                return "__none__"
            return target if target in criteria else next(key for key in criteria if key != "__none__")
        decider = FakeDecider(choose=choose)
        result = SelectionSearch(observed, "Recover a narrowing false negative", config=SelectionConfig(
            keep_per_group=1)).run(decider)
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["selected_option"]["id"], target)
        self.assertIn("recovery_final", [call["payload"]["state"]["stage"]
                                          for call in decider.action_calls()])

    def test_request_budget_reports_unresolved_without_promoting_partial_winner(self):
        observed = snapshot(300)
        decider = FakeDecider()
        result = SelectionSearch(observed, "Choose safely", config=SelectionConfig(
            batch_size=64, max_requests=2)).run(decider)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertIsNone(result["selected_option"])
        self.assertLessEqual(len(decider.calls), 2)

    def test_round_budget_does_not_select_a_group_winner_without_shared_final(self):
        decider = FakeDecider()
        result = SelectionSearch(snapshot(320), "Need one comparable final decision", config=SelectionConfig(
            batch_size=64, keep_per_group=20, max_rounds=1)).run(decider)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertIn("round budget", result["reason"])
        self.assertIsNone(result["selected_option"])
        self.assertNotIn("final", [call["payload"]["state"]["stage"]
                                   for call in decider.action_calls()])

    def test_browser_context_is_bounded_through_real_adapter_and_shared_final(self):
        observed = browser_snapshot(132)
        target = observed["options"][-1]["id"]
        dispatched = []

        def open_request(request, *, timeout):
            self.assertLessEqual(len(request.data), MAX_SELECTION_REQUEST_BYTES)
            payload = json.loads(request.data)
            self.assertEqual(request.data, request_body(payload))
            dispatched.append(payload)
            answers = {}
            for question_id, question in payload["questions"].items():
                criteria = question["criteria"]
                winner = ("promising" if question_id.startswith("classify_") else
                          target if target in criteria else next(key for key in criteria if key != "__none__"))
                answers[question_id] = choice_answer(criteria, winner)
            response = MagicMock()
            response.status = 200
            response.read.return_value = json.dumps({
                "model": jev.RESOLVED_MODEL, "provider": jev.PROVIDER,
                "id": "gen-synthetic-browser", "answers": answers,
                "usage": {"input_tokens": 0, "output_tokens": 0, "cost": 0},
            }).encode()
            response.__enter__.return_value = response
            return response

        def remote(payload, *, request_id, capture_id):
            return jev.decide(payload, "synthetic-test-key", request_id=request_id, capture_id=capture_id)

        with patch.object(jev, "build_opener") as build, \
             patch("socket.socket", side_effect=AssertionError("Unexpected network")):
            build.return_value.open.side_effect = open_request
            result = SelectionSearch(observed, "Open a new YouTube video").run(remote)
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["selected_option"]["id"], target)
        classified = [candidate for payload in dispatched if payload["state"]["stage"] == "classify"
                      for candidate in payload["state"]["candidates"]]
        self.assertCountEqual([candidate["id"] for candidate in classified],
                              [option["id"] for option in observed["options"]])
        self.assertTrue(all(len(candidate["ancestors"]) == 4 for candidate in classified))
        self.assertTrue(any(payload["state"]["stage"] == "reduce" for payload in dispatched))
        finals = [payload for payload in dispatched if payload["state"]["stage"] == "final"]
        self.assertEqual(len(finals), 1)
        self.assertIn(target, finals[0]["questions"]["action"]["criteria"])
        self.assertLessEqual(result["request_count"], SelectionConfig().max_requests)

    def test_unicode_context_uses_escaped_wire_bytes_and_preserves_every_option(self):
        observed = snapshot(40)
        for element, option in zip(observed["elements"], observed["options"]):
            element["name"] = "\U0001f4fa" * 180
            option["label"] = element["name"]
        preview = SelectionSearch(observed, "\u6253\u5f00\u65b0\u89c6\u9891", history=["\u4e0a\u4e00\u6b65" * 40]).preview()
        requests = preview["initial_requests"]
        self.assertEqual([key for request in requests for key in request["input_ids"]],
                         [option["id"] for option in observed["options"]])
        for request in requests:
            body = request_body(request["payload"])
            self.assertLessEqual(len(body), MAX_SELECTION_REQUEST_BYTES)
            self.assertIn(b"\\ud83d\\udcfa", body)
            self.assertGreater(len(body), len(json.dumps(request["payload"], ensure_ascii=False).encode("utf-8")))

    def test_oversized_single_candidate_or_shared_goal_fails_before_dispatch(self):
        observed = browser_snapshot(1)
        for element in observed["elements"]:
            element["name"] = "\U0001f4fa" * 512
        cases = ((observed, "Choose safely", None),
                 (snapshot(1), "\U0001f4fa" * 4000, None),
                 (snapshot(1), "Choose safely", ["\U0001f4fa" * 512] * 8))
        for candidate_snapshot, goal, history in cases:
            with self.subTest(goal_length=len(goal), history=history is not None), \
                 self.assertRaises(SelectionContextError) as caught:
                SelectionSearch(candidate_snapshot, goal, history=history)
            self.assertNotIn("\U0001f4fa", str(caught.exception))

    def test_action_context_is_preflighted_when_classification_alone_would_fit(self):
        observed = snapshot(1)
        observed["options"][0]["label"] = "\U0001f4fa" * 512
        goal = "g" * 4000
        history = [{"action": "a" * 512, "result": "r" * 512}] * 8
        with patch.object(selection, "MAX_SELECTION_REQUEST_BYTES", 100_000):
            search = SelectionSearch(observed, goal, history=history)
            classify = search.preview()["initial_requests"][0]["payload"]
            final = search._make_requests(search._ids, "final", 0)[0]["payload"]
        self.assertLessEqual(len(request_body(classify)), MAX_SELECTION_REQUEST_BYTES)
        self.assertGreater(len(request_body(final)), MAX_SELECTION_REQUEST_BYTES)
        with self.assertRaises(SelectionContextError):
            SelectionSearch(observed, goal, history=history)

    def test_small_context_groups_shrink_under_default_retention(self):
        for label_size in (400, 512):
            with self.subTest(label_size=label_size):
                observed = browser_snapshot(20)
                for ancestor in observed["elements"][20:]:
                    ancestor.update(name="n" * label_size, value="v" * label_size)
                target = observed["options"][-1]["id"]
                decider = FakeDecider(choose=lambda criteria, payload: target if target in criteria else
                                      next(key for key in criteria if key != "__none__"))
                search = SelectionSearch(observed, "Select the known synthetic target")
                groups = search._make_requests(search._ids, "reduce", 1)
                self.assertTrue(any(1 < len(group["input_ids"]) <= search.config.keep_per_group + 1
                                    for group in groups))
                if label_size == 512:
                    self.assertTrue(any(len(group["input_ids"]) <= search.config.keep_per_group
                                        for group in groups))
                result = search.run(decider)
                self.assertEqual(result["status"], "selected")
                self.assertEqual(result["selected_option"]["id"], target)
                finals = [call for call in decider.action_calls()
                          if call["payload"]["state"]["stage"] == "final"]
                self.assertEqual(len(finals), 1)
                self.assertIn(target, finals[0]["payload"]["questions"]["action"]["criteria"])
                for call in decider.calls:
                    self.assertLessEqual(len(request_body(call["payload"])), MAX_SELECTION_REQUEST_BYTES)

    def test_singleton_only_context_groups_do_not_loop_or_select(self):
        observed = browser_snapshot(3)
        for ancestor in observed["elements"][3:]:
            ancestor.update(name="\u751f" * 250, value="\u751f" * 250)
        search = SelectionSearch(observed, "Choose safely")
        groups = search._make_requests(search._ids, "reduce", 1)
        self.assertEqual([len(group["input_ids"]) for group in groups], [1, 1, 1])
        decider = FakeDecider()
        result = search.run(decider)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertIsNone(result["selected_option"])
        self.assertIn("context-size limit", result["reason"])
        self.assertEqual({call["payload"]["state"]["round"] for call in decider.action_calls()}, {1})
        self.assertFalse(any(call["payload"]["state"]["stage"].endswith("final")
                             for call in decider.action_calls()))

    def test_context_packing_obeys_request_budget_without_partial_winner(self):
        observed = browser_snapshot(132)
        needed = SelectionSearch(observed, "Choose safely").preview()["initial_request_count"]
        decider = FakeDecider()
        result = SelectionSearch(observed, "Choose safely", config=SelectionConfig(
            max_requests=needed + 1)).run(decider)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertIsNone(result["selected_option"])
        self.assertEqual(len(decider.calls), needed)
        self.assertEqual(len(decider.classified_ids()), 132)
        self.assertEqual(decider.action_calls(), [])

    def test_invalid_or_foreign_responses_never_select(self):
        mutations = {
            "wrong request": lambda response: response["local_request"].update(request_id="another_request"),
            "wrong snapshot": lambda response: response["local_request"].update(capture_id="another_snapshot"),
            "wrong hash": lambda response: response["local_request"].update(payload_sha256="f" * 64),
            "wrong provider": lambda response: response.update(provider="Other"),
            "wrong model": lambda response: response.update(model=jev.MODEL),
            "foreign choice": lambda response: next(iter(response["answers"].values())).update(choice="foreign"),
            "invalid probability": lambda response: next(iter(response["answers"].values()))["probabilities"].update(promising=float("nan")),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                honest = FakeDecider()
                def corrupt(payload, *, request_id, capture_id):
                    response = honest(payload, request_id=request_id, capture_id=capture_id)
                    mutate(response)
                    return response
                with self.assertRaises(jev.DecisionError):
                    SelectionSearch(snapshot(4), "Choose safely").run(corrupt)

    def test_password_and_unknown_password_values_never_enter_requests(self):
        observed = snapshot(3)
        for element, protected, value in zip(observed["elements"], (True, None, False),
                                              ("PASSWORD_SENTINEL", "UNKNOWN_SENTINEL", "public fixture value")):
            element["is_password"] = protected
            element["value"] = value
        decider = FakeDecider()
        SelectionSearch(observed, "Inspect safe context").run(decider)
        emitted = json.dumps(decider.calls, allow_nan=False)
        self.assertNotIn("PASSWORD_SENTINEL", emitted)
        self.assertNotIn("UNKNOWN_SENTINEL", emitted)
        self.assertIn("public fixture value", emitted)

    def test_ancestor_and_unrecognized_metadata_cannot_bypass_value_redaction(self):
        observed = snapshot(2)
        observed["elements"][1]["parent_id"] = observed["elements"][0]["id"]
        observed["elements"][0].update(is_password=True, value="ANCESTOR_PASSWORD_SENTINEL")
        observed["elements"][0]["state"]["raw_value"] = "STATE_SECRET_SENTINEL"
        observed["elements"][1]["provider_debug"] = "PROVIDER_DEBUG_SENTINEL"
        observed["options"][1]["requires"]["value"] = "REQUIRES_SECRET_SENTINEL"
        observed["options"] = [observed["options"][1]]
        decider = FakeDecider()
        SelectionSearch(observed, "Read only the safe context").run(decider)
        emitted = json.dumps(decider.calls, allow_nan=False)
        for sentinel in ("ANCESTOR_PASSWORD_SENTINEL", "STATE_SECRET_SENTINEL",
                         "PROVIDER_DEBUG_SENTINEL", "REQUIRES_SECRET_SENTINEL"):
            self.assertNotIn(sentinel, emitted)
        ancestors = decider.calls[0]["payload"]["state"]["candidates"][0]["ancestors"]
        self.assertEqual(ancestors[0]["id"], observed["elements"][0]["id"])

    def test_input_and_preview_mutation_cannot_change_frozen_search_snapshot(self):
        observed = snapshot(6)
        original = deepcopy(observed)
        search = SelectionSearch(observed, "Select an observed option")
        preview = search.preview()
        preview["initial_requests"][0]["payload"]["state"]["candidates"].clear()
        observed["options"].clear()
        observed["elements"][0]["name"] = "MUTATED_AFTER_CONSTRUCTION"
        decider = FakeDecider()
        result = search.run(decider)
        self.assertEqual(result["status"], "selected")
        self.assertCountEqual(decider.classified_ids(), [option["id"] for option in original["options"]])
        self.assertNotIn("MUTATED_AFTER_CONSTRUCTION", json.dumps(decider.calls))
        self.assertEqual(original["elements"][0]["name"], "Opaque control")

    def test_run_does_not_mutate_callers_snapshot(self):
        observed = snapshot(100)
        before = deepcopy(observed)
        SelectionSearch(observed, "Choose a control").run(FakeDecider())
        self.assertEqual(observed, before)

    def test_precancelled_search_dispatches_nothing(self):
        decider = FakeDecider()
        result = SelectionSearch(snapshot(100), "Cancelled goal").run(decider, cancelled=lambda: True)
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(result["selected_option"])
        self.assertEqual(decider.calls, [])

    def test_cancellation_during_classification_does_not_reach_final_choice(self):
        cancel = threading.Event()
        honest = FakeDecider()
        def cancel_after_response(payload, *, request_id, capture_id):
            result = honest(payload, request_id=request_id, capture_id=capture_id)
            cancel.set()
            return result
        result = SelectionSearch(snapshot(300), "Cancelled midway", config=SelectionConfig(
            parallelism=1)).run(cancel_after_response, cancelled=cancel.is_set)
        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(result["selected_option"])
        self.assertEqual(honest.action_calls(), [])

    def test_cancellation_returns_before_a_blocked_provider_finishes(self):
        started, release, cancel, finished = (threading.Event() for _ in range(4))
        honest = FakeDecider()
        returned = {}
        def blocked(payload, *, request_id, capture_id):
            started.set()
            release.wait(3)
            return honest(payload, request_id=request_id, capture_id=capture_id)
        def run():
            try:
                returned["result"] = SelectionSearch(snapshot(100), "Cancel in-flight request",
                    config=SelectionConfig(parallelism=1)).run(blocked, cancelled=cancel.is_set)
            except Exception as error:
                returned["error"] = error
            finally:
                finished.set()
        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(started.wait(2), "The fake provider did not receive a request")
            cancel.set()
            self.assertTrue(finished.wait(1), "Cancellation waited for the blocked provider")
            self.assertNotIn("error", returned)
            self.assertEqual(returned["result"]["status"], "cancelled")
            self.assertIsNone(returned["result"]["selected_option"])
            self.assertEqual(honest.calls, [])
        finally:
            release.set()
            worker.join(3)

    def test_parallel_requests_never_exceed_configured_bound(self):
        decider = FakeDecider(delay=.015)
        result = SelectionSearch(snapshot(640), "Bound concurrency", config=SelectionConfig(
            batch_size=32, parallelism=3)).run(decider)
        self.assertEqual(result["status"], "selected")
        self.assertGreater(decider.maximum_active, 1)
        self.assertLessEqual(decider.maximum_active, 3)
        self.assertEqual(decider.active, 0)
        self.assertEqual(len({call["request_id"] for call in decider.calls}), len(decider.calls))

    def test_config_is_frozen_and_rejects_invalid_budgets(self):
        config = SelectionConfig()
        with self.assertRaises(FrozenInstanceError):
            config.batch_size = 10
        for changes in ({"batch_size": 1}, {"batch_size": 255}, {"keep_per_group": 64},
                        {"parallelism": 0}, {"max_requests": 0}, {"max_rounds": 0}):
            with self.subTest(changes=changes), self.assertRaises((SelectionError, ValueError)):
                SelectionSearch(snapshot(2), "Goal", config=SelectionConfig(**changes))

    def test_foreign_duplicate_or_dangling_options_fail_before_decider(self):
        mutations = (
            lambda observed: observed["options"][0].update(id="foreign:e0:activate"),
            lambda observed: observed["options"][0].update(element_id="selection_fixture:missing"),
            lambda observed: observed["options"].append(deepcopy(observed["options"][0])),
            lambda observed: observed["options"][0].update(operation="execute_arbitrary_code"),
        )
        for mutate in mutations:
            observed = snapshot(2)
            mutate(observed)
            with self.subTest(mutation=mutate), self.assertRaises(SelectionError):
                SelectionSearch(observed, "Goal")


if __name__ == "__main__":
    unittest.main()
