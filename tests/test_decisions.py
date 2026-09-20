"""Offline capture-binding and local/outbound data-boundary checks."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ctrl_loop.decisions import PreviewRegistry, RegistryError
from ctrl_loop.jev import PROVIDER, RESOLVED_MODEL


def fixture():
    return json.loads((ROOT / "examples/jev-fixture.json").read_text(encoding="utf-8-sig"))


def selected(action_id):
    data = fixture()
    capture = data["observation"]["capture_id"]
    options = [item["id"] + ":click" for item in data["candidates"]] + [capture + ":reobserve", capture + ":no_target"]
    return {
        "model": RESOLVED_MODEL, "provider": PROVIDER,
        "provider_request_id": "gen-offline-registry-test", "latency_ms": 12.5,
        "usage": {"input_tokens": 10, "output_tokens": 20, "cost": 0.0001},
        "answers": {
            "next_action": {"type": "choice", "choice": action_id,
                            "probabilities": {option: int(option == action_id) for option in options},
                            "confidence": 0.99},
            "already_satisfied": {"type": "noul", "noul": 0.01},
            "no_viable_target": {"type": "noul", "noul": 0.02},
        },
    }


class CaptureBindingTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.registry = PreviewRegistry(self.data)
        self.action = self.data["candidates"][0]["id"] + ":click"

    def test_valid_response_resolves_local_geometry_without_input(self):
        pending = self.registry.begin()
        result = self.registry.accept(pending, selected(self.action))
        self.assertEqual(result["request_id"], pending.request_id)
        self.assertEqual(result["capture_id"], self.data["observation"]["capture_id"])
        self.assertEqual(result["status"], "preview_only")
        self.assertEqual(result["input_actions"], 0)
        self.assertEqual(tuple(result["local_target"]["box_xyxy"]), tuple(self.data["candidates"][0]["box_xyxy"]))
        self.assertEqual(result["local_target"]["label_source"], "manual_fixture_annotation")
        self.assertFalse(result["observation"]["action_transform_verified"])

    def test_invalidated_capture_rejects_outstanding_response_and_new_request(self):
        pending = self.registry.begin()
        self.registry.invalidate()
        with self.assertRaises(RegistryError):
            self.registry.accept(pending, selected(self.action))
        with self.assertRaises(RegistryError):
            self.registry.begin()

    def test_other_registry_with_same_capture_cannot_supply_request(self):
        pending = self.registry.begin()
        other = PreviewRegistry(self.data)
        foreign_pending = other.begin()
        self.assertEqual(pending.capture_id, foreign_pending.capture_id)
        self.assertNotEqual(pending.request_id, foreign_pending.request_id)
        with self.assertRaises(RegistryError):
            self.registry.accept(foreign_pending, selected(self.action))
        # A foreign response must not consume the true outstanding request.
        self.assertEqual(self.registry.accept(pending, selected(self.action))["input_actions"], 0)

    def test_copied_request_and_replay_are_rejected(self):
        pending = self.registry.begin()
        with self.assertRaises(RegistryError):
            self.registry.accept(replace(pending), selected(self.action))
        self.registry.accept(pending, selected(self.action))
        with self.assertRaises(RegistryError):
            self.registry.accept(pending, selected(self.action))
        replacement = self.registry.begin()
        with self.assertRaises(RegistryError):
            self.registry.accept(pending, selected(self.action))
        self.registry.accept(replacement, selected(self.action))

    def test_only_one_request_can_be_outstanding(self):
        pending = self.registry.begin()
        with self.assertRaises(RegistryError):
            self.registry.begin()
        self.registry.accept(pending, selected(self.action))

    def test_unknown_or_malformed_action_consumes_response_without_resolving(self):
        bad_decisions = [None, {}, {"answers": []}, selected("other-capture:c1:click"),
                         selected("made-up"), selected(None), selected([])]
        for decision in bad_decisions:
            with self.subTest(decision_type=type(decision).__name__):
                registry = PreviewRegistry(self.data)
                pending = registry.begin()
                with self.assertRaises(RegistryError):
                    registry.accept(pending, decision)
                with self.assertRaises(RegistryError):
                    registry.accept(pending, selected(self.action))

    def test_explicit_noninput_options_do_not_resolve_a_target(self):
        for suffix in (":reobserve", ":no_target"):
            pending = self.registry.begin()
            result = self.registry.accept(pending, selected(pending.capture_id + suffix))
            self.assertIsNone(result["local_target"])
            self.assertEqual(result["input_actions"], 0)

    def test_malformed_normalized_results_are_rejected_and_consumed(self):
        def change(path, value):
            result = selected(self.action)
            target = result
            for field in path[:-1]:
                target = target[field]
            target[path[-1]] = value
            return result

        malformed = [
            {"answers": {"next_action": {"choice": self.action}}},
            change(["answers", "next_action", "confidence"], True),
            change(["answers", "next_action", "probabilities"], {self.action: 1.0}),
            change(["answers", "next_action", "probabilities", self.action], float("nan")),
            change(["answers", "already_satisfied", "noul"], 1.5),
            change(["answers", "no_viable_target", "noul"], "0.1"),
            change(["answers", "no_viable_target", "type"], "choice"),
            change(["model"], "unexpected/model"), change(["provider"], "UnexpectedProvider"),
            change(["latency_ms"], float("inf")), change(["latency_ms"], -1),
            change(["latency_ms"], True), change(["usage", "cost"], -1),
            change(["provider_request_id"], "unexpected-id"),
        ]
        for decision in malformed:
            registry = PreviewRegistry(self.data)
            pending = registry.begin()
            with self.assertRaises(RegistryError):
                registry.accept(pending, decision)
            with self.assertRaises(RegistryError):
                registry.accept(pending, selected(self.action))

    def test_accepted_decision_is_a_normalized_defensive_copy(self):
        pending = self.registry.begin()
        decision = selected(self.action)
        decision["ignored_diagnostic"] = "Untrusted provider diagnostic"
        result = self.registry.accept(pending, decision)
        self.assertNotIn("ignored_diagnostic", result["decision"])
        decision["answers"]["next_action"]["probabilities"][self.action] = 0.0
        decision["answers"]["already_satisfied"]["noul"] = 1.0
        decision["usage"]["cost"] = 999.0
        self.assertEqual(result["decision"]["answers"]["next_action"]["probabilities"][self.action], 1)
        self.assertEqual(result["decision"]["answers"]["already_satisfied"]["noul"], 0.01)
        self.assertEqual(result["decision"]["usage"]["cost"], 0.0001)

    def test_fixture_and_payload_mutation_cannot_change_registered_request(self):
        expected = copy.deepcopy(self.data)
        self.data["task"] = "Changed after registration"
        self.data["observation"]["target"]["pid"] = 99999
        self.data["candidates"][0]["box_xyxy"][0] = 0
        self.data["candidates"][0]["description"] = "Changed candidate"
        pending = self.registry.begin()
        payload = pending.payload()
        payload["state"]["task"] = "Changed transport copy"
        payload["state"]["candidates"][0]["label"] = "Changed label"
        payload["questions"]["next_action"]["criteria"].clear()
        fresh = pending.payload()
        self.assertEqual(fresh["state"]["task"], expected["task"])
        self.assertEqual(fresh["state"]["candidates"][0]["label"], expected["candidates"][0]["label"])
        self.assertIn(self.action, fresh["questions"]["next_action"]["criteria"])
        result = self.registry.accept(pending, selected(self.action))
        self.assertEqual(result["observation"], expected["observation"])
        self.assertEqual(tuple(result["local_target"]["box_xyxy"]), tuple(expected["candidates"][0]["box_xyxy"]))

    def test_outbound_payload_excludes_local_coordinates_identity_and_confidence(self):
        payload = self.registry.begin().payload()
        forbidden = {"box_xyxy", "detector_confidence", "detector_model", "target", "pid", "window_id",
                     "image_width", "image_height", "image_sha256", "coordinate_space", "action_transform_verified"}

        def visit(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden.intersection(value))
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(payload)
        encoded = json.dumps(payload)
        for candidate in self.data["candidates"]:
            self.assertNotIn(str(candidate["detector_confidence"]), encoded)
            self.assertNotIn(candidate["detector_model"], encoded)
            self.assertIn(candidate["label"], encoded)
            self.assertIn(candidate["label_source"], encoded)
        self.assertNotIn(str(self.data["observation"]["target"]["window_id"]), encoded)


class FixtureValidationTests(unittest.TestCase):
    def assertRejected(self, data):
        with self.assertRaises(RegistryError) as caught:
            PreviewRegistry(data)
        self.assertEqual(str(caught.exception), "Invalid decision preview fixture.")

    def test_duplicate_foreign_and_malformed_candidate_ids(self):
        data = fixture()
        data["candidates"].append(copy.deepcopy(data["candidates"][0]))
        self.assertRejected(data)
        for candidate_id in ("other-capture:c1", "plain-id", 1, None,
                             data["observation"]["capture_id"] + ":c1:extra"):
            data = fixture()
            data["candidates"][0]["id"] = candidate_id
            self.assertRejected(data)

    def test_nonfinite_wrong_type_out_of_range_or_degenerate_boxes(self):
        boxes = [[-1, 1, 10, 10], [1, -1, 10, 10], [10, 1, 10, 10], [1, 10, 10, 10],
                 [11, 1, 10, 10], [1, 1, 10000, 10], [1, 1, 10, 10000],
                 [float("nan"), 1, 10, 10], [1, 1, float("inf"), 10],
                 [True, 1, 10, 10], ["1", 1, 10, 10], [1, 2, 3], None]
        for box in boxes:
            data = fixture()
            data["candidates"][0]["box_xyxy"] = box
            self.assertRejected(data)

    def test_bad_confidences_and_unsupported_label_or_operation(self):
        for confidence in (-0.1, 1.1, float("nan"), float("inf"), True, "0.5", None):
            data = fixture()
            data["candidates"][0]["detector_confidence"] = confidence
            self.assertRejected(data)
        for field, value in (("label_source", "yolo_icon_class"), ("label_source", "ocr"),
                             ("label", "  "), ("description", ""), ("operation", "type")):
            data = fixture()
            data["candidates"][0][field] = value
            self.assertRejected(data)

    def test_invalid_observation_identity_dimensions_timestamp_and_transform(self):
        cases = [("image_width", 0), ("image_height", True), ("capture_id", "capture with spaces"),
                 ("image_sha256", "not-a-hash"), ("coordinate_space", "desktop_pixels"),
                 ("action_transform_verified", True), ("action_transform_verified", 0),
                 ("acquired_at_utc", "2026-09-20T00:59:24")]
        for field, value in cases:
            data = fixture()
            data["observation"][field] = value
            self.assertRejected(data)
        for field, value in (("pid", True), ("pid", -1), ("window_id", 0)):
            data = fixture()
            data["observation"]["target"][field] = value
            self.assertRejected(data)

    def test_schema_version_requires_exact_integer_and_candidate_limit(self):
        for version in (True, 1.0, "1", 2, None):
            data = fixture()
            data["schema_version"] = version
            self.assertRejected(data)
        data = fixture()
        data["candidates"] = [copy.deepcopy(data["candidates"][0]) for _ in range(65)]
        self.assertRejected(data)

    def test_no_candidates_retains_only_explicit_noninput_options(self):
        data = fixture()
        data["candidates"] = []
        pending = PreviewRegistry(data).begin()
        self.assertEqual(set(pending.payload()["questions"]["next_action"]["criteria"]),
                         {pending.capture_id + ":no_target", pending.capture_id + ":reobserve"})


if __name__ == "__main__":
    unittest.main()
