from copy import deepcopy
from http.client import IncompleteRead
import hashlib
import io
import json
from pathlib import Path
import sys
import traceback
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ctrl_loop import jev

TEST_CREDENTIAL = "test-credential"
LOCAL_IDS = {"request_id": "request-1", "capture_id": "capture-1"}


def request_payload():
    return {
        "model": jev.MODEL,
        "provider": {"only": ["typesafe"], "order": ["typesafe"], "allow_fallbacks": False},
        "state": {"task": "Open Settings", "capture_id": "capture-1"},
        "questions": {
            "action": {"type": "choice", "instructions": "Which supported action advances the task?",
                       "criteria": {"capture-1:c1:click": "Click Settings", "no_target": "No target is visible"}},
            "complete": {"type": "noul", "instructions": "Is the requested result already visible?"},
        },
    }


def response_payload():
    return {
        "model": jev.RESOLVED_MODEL, "provider": jev.PROVIDER, "id": "gen-dec-123-abc",
        "answers": {
            "action": {"type": "choice", "choice": "capture-1:c1:click", "confidence": .7,
                       "probabilities": {"capture-1:c1:click": .8, "no_target": .2}},
            "complete": {"type": "noul", "noul": .02},
        },
        "usage": {"input_tokens": 45, "output_tokens": 10, "cost": .00001},
    }


class DecisionAdapterTests(unittest.TestCase):
    def call(self, envelope=None, *, raw=None, payload=None, side_effect=None):
        response = MagicMock()
        response.status = 200
        response.read.return_value = raw if raw is not None else json.dumps(
            response_payload() if envelope is None else envelope).encode()
        response.__enter__.return_value = response
        with patch.object(jev, "build_opener") as build:
            build.return_value.open.return_value = response
            build.return_value.open.side_effect = side_effect
            result = jev.decide(request_payload() if payload is None else payload, TEST_CREDENTIAL, **LOCAL_IDS)
        return result, build, response

    def test_pinned_request_and_whitelisted_output(self):
        envelope = response_payload()
        envelope["diagnostic"] = "discard this text"
        envelope["answers"]["action"]["extra"] = "discard this text"
        envelope["answers"]["complete"]["confidence"] = "discard this text"
        envelope["usage"]["diagnostic"] = "discard this text"
        envelope["local_request"] = {"request_id": "provider-fake", "capture_id": "provider-fake",
                                     "payload_sha256": "a" * 64}
        result, build, response = self.call(envelope)
        sent = build.return_value.open.call_args.args[0]
        self.assertEqual(sent.full_url, jev.ENDPOINT)
        self.assertEqual(sent.method, "POST")
        self.assertEqual(json.loads(sent.data), request_payload())
        self.assertEqual(sent.get_header("Authorization"), "Bearer test-credential")
        self.assertEqual(build.return_value.open.call_args.kwargs["timeout"], 20)
        self.assertEqual(response.read.call_args.args, (jev.MAX_RESPONSE_BYTES + 1,))
        self.assertEqual(set(result), {"answers", "model", "provider", "provider_request_id",
                                       "latency_ms", "usage", "local_request"})
        self.assertEqual(result["local_request"], jev.request_binding(request_payload(), **LOCAL_IDS))
        self.assertNotIn("local_request", json.loads(sent.data))
        self.assertEqual(result["answers"]["action"]["confidence"], .7)
        self.assertEqual(result["answers"]["action"]["probabilities"]["capture-1:c1:click"], .8)
        self.assertEqual(result["answers"]["complete"], {"type": "noul", "noul": .02})
        self.assertNotIn("discard", json.dumps(result))
        self.assertGreaterEqual(result["latency_ms"], 0)

    def test_redirect_handler_refuses_redirect(self):
        self.assertIsNone(jev._NoRedirects().redirect_request(None, None, 302, None, None, "https://example.com"))

    def test_payload_is_snapshotted_before_transport(self):
        payload = request_payload()
        original = deepcopy(payload)
        original_binding = jev.request_binding(payload, **LOCAL_IDS)
        def mutate(*args, **kwargs):
            payload["questions"]["action"]["criteria"].clear()
            return response
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(response_payload()).encode()
        response.__enter__.return_value = response
        with patch.object(jev, "build_opener") as build:
            build.return_value.open.side_effect = mutate
            result = jev.decide(payload, TEST_CREDENTIAL, **LOCAL_IDS)
        self.assertEqual(result["answers"]["action"]["choice"], "capture-1:c1:click")
        self.assertEqual(result["local_request"], original_binding)
        self.assertEqual(json.loads(build.return_value.open.call_args.args[0].data), original)

    def test_wrong_request_routes_fail_before_network(self):
        variants = []
        for field, value in (("model", "other/model"), ("model", jev.RESOLVED_MODEL),
                             ("provider", None), ("provider", {"only": ["typesafe"]})):
            payload = request_payload()
            payload[field] = value
            variants.append(payload)
        for key, value in (("allow_fallbacks", True), ("allow_fallbacks", 0),
                           ("only", ["typesafe", "other"]), ("order", ["other"])):
            payload = request_payload()
            payload["provider"][key] = value
            variants.append(payload)
        payload = request_payload()
        payload["messages"] = []
        variants.append(payload)
        for payload in variants:
            with self.subTest(payload=payload), patch.object(jev, "build_opener") as build:
                with self.assertRaises(jev.DecisionError):
                    jev.decide(payload, TEST_CREDENTIAL, **LOCAL_IDS)
                build.assert_not_called()

    def test_unknown_missing_and_mismatched_answer_ids(self):
        for mutation in (lambda e: e["answers"].pop("complete"),
                         lambda e: e["answers"].update(extra={"type": "noul", "noul": 0}),
                         lambda e: e["answers"]["action"].update(choice="capture-2:c1:click"),
                         lambda e: e["answers"]["action"].update(type="noul"),
                         lambda e: e["answers"]["complete"].update(type="choice"),
                         lambda e: e["answers"].update(action=[]),
                         lambda e: e["answers"]["action"].update(choice=["bad"]),
                         lambda e: e["answers"]["action"].update(probabilities={"capture-1:c1:click": 1}),
                         lambda e: e["answers"]["action"].update(probabilities={"capture-1:c1:click": .8, "bad": .2})):
            envelope = response_payload()
            mutation(envelope)
            with self.subTest(envelope=envelope), self.assertRaises(jev.DecisionError):
                self.call(envelope)

    def test_invalid_probability_values_confidence_and_noul(self):
        for value in (True, False, None, "0.8", [], {}, -0.1, 1.1, float("nan"), float("inf"), -float("inf")):
            for field in ("probability", "confidence", "noul"):
                envelope = response_payload()
                if field == "probability":
                    envelope["answers"]["action"]["probabilities"]["capture-1:c1:click"] = value
                elif field == "confidence":
                    envelope["answers"]["action"]["confidence"] = value
                else:
                    envelope["answers"]["complete"]["noul"] = value
                with self.subTest(field=field, value=value), self.assertRaises(jev.DecisionError):
                    self.call(envelope)

    def test_distribution_sum_maximum_and_rounding(self):
        for probabilities in ({"capture-1:c1:click": .8, "no_target": .1},
                              {"capture-1:c1:click": .2, "no_target": .8}):
            envelope = response_payload()
            envelope["answers"]["action"]["probabilities"] = probabilities
            with self.assertRaises(jev.DecisionError):
                self.call(envelope)
        envelope = response_payload()
        envelope["answers"]["action"]["probabilities"] = {"capture-1:c1:click": .5, "no_target": .5}
        self.call(envelope)  # A tied selected maximum is valid; policy belongs to the caller.
        envelope["answers"]["action"]["probabilities"] = {"capture-1:c1:click": .8, "no_target": .2000005}
        self.call(envelope)

    def test_complete_hundredth_rounded_distribution_keeps_original_values(self):
        # A live synthetic 48-option request returned a maximum of
        # .18000000000000002 and total .99. Exercise both rounding directions
        # through the HTTP adapter and the selector's revalidation boundary.
        payload = request_payload()
        payload["questions"]["action"]["criteria"] = {
            f"option_{index}": f"Synthetic option {index}" for index in range(48)}
        for tail in (.06, .08):
            values = [.18000000000000002, .17, .16, .15, .10, .09, .08, tail] + [0] * 40
            probabilities = dict(zip(payload["questions"]["action"]["criteria"], values))
            envelope = response_payload()
            envelope["answers"]["action"].update(choice="option_0", probabilities=probabilities)
            with self.subTest(total=sum(values)):
                result, _, _ = self.call(envelope, payload=payload)
                revalidated = jev.validate_decision(result, payload["questions"])
                self.assertEqual(revalidated["answers"]["action"], envelope["answers"]["action"])
                self.assertEqual(revalidated["answers"]["action"]["probabilities"], probabilities)

    def test_rounded_sum_allowance_stays_bounded_and_requires_hundredths(self):
        for values in ((.8, .18), (.8, .22), (0, 0), (1, 1),
                       (.8001, .1899), (.7999, .2101), (.8, .21000001)):
            envelope = response_payload()
            envelope["answers"]["action"]["probabilities"] = dict(zip(
                request_payload()["questions"]["action"]["criteria"], values))
            with self.subTest(values=values), self.assertRaises(jev.DecisionError) as caught:
                self.call(envelope)
            self.assertEqual(caught.exception.validation_code, "probability_sum")
        envelope = response_payload()
        envelope["answers"]["action"]["probabilities"] = {"capture-1:c1:click": .19, "no_target": .8}
        with self.assertRaises(jev.DecisionError) as caught:
            self.call(envelope)
        self.assertEqual(caught.exception.validation_code, "choice_maximum")

    def test_rounding_allowance_does_not_grow_with_option_count(self):
        payload = request_payload()
        payload["questions"]["action"]["criteria"] = {
            f"option_{index}": f"Synthetic option {index}" for index in range(255)}
        envelope = response_payload()
        envelope["answers"]["action"].update(
            choice="option_0", probabilities={f"option_{index}": .01 if index < 98 else 0
                                               for index in range(255)})
        with self.assertRaises(jev.DecisionError) as caught:
            self.call(envelope, payload=payload)
        self.assertEqual(caught.exception.validation_code, "probability_sum")
        envelope["answers"]["action"]["probabilities"]["option_98"] = .01
        self.call(envelope, payload=payload)
        del envelope["answers"]["action"]["probabilities"]["option_254"]
        with self.assertRaises(jev.DecisionError) as caught:
            self.call(envelope, payload=payload)
        self.assertEqual(caught.exception.validation_code, "probability_ids")

    def test_response_failures_identify_the_boundary_without_raw_data(self):
        cases = (
            ("model", lambda e: e.update(model="different-model")),
            ("provider", lambda e: e.update(provider="different-provider")),
            ("question_ids", lambda e: e["answers"].pop("complete")),
            ("answer_type", lambda e: e["answers"]["action"].update(type="noul")),
            ("choice_id", lambda e: e["answers"]["action"].update(choice="unknown")),
            ("probability_ids", lambda e: e["answers"]["action"]["probabilities"].pop("no_target")),
            ("probability_value", lambda e: e["answers"]["action"]["probabilities"].update(no_target=-.1)),
            ("probability_sum", lambda e: e["answers"]["action"]["probabilities"].update(no_target=.1)),
            ("choice_maximum", lambda e: e["answers"]["action"].update(choice="no_target")),
            ("confidence", lambda e: e["answers"]["action"].update(confidence=None)),
            ("noul", lambda e: e["answers"]["complete"].update(noul=2)),
            ("provider_request_id", lambda e: e.update(id="PRIVATE-ID-SENTINEL")),
            ("usage", lambda e: e.update(usage="PRIVATE-USAGE-SENTINEL")),
        )
        for expected, mutate in cases:
            envelope = response_payload()
            envelope["diagnostic"] = "PRIVATE-DIAGNOSTIC-SENTINEL"
            mutate(envelope)
            with self.subTest(expected=expected), self.assertRaises(jev.DecisionError) as caught:
                self.call(envelope)
            self.assertEqual(caught.exception.validation_code, expected)
            self.assertNotIn("PRIVATE", str(caught.exception))
        for raw, expected in ((b"not json", "json"), (b" " * (jev.MAX_RESPONSE_BYTES + 1), "response_size")):
            with self.assertRaises(jev.DecisionError) as caught:
                self.call(raw=raw)
            self.assertEqual(caught.exception.validation_code, expected)

    def test_normalized_validation_retains_exact_safe_failure_code(self):
        result, _, _ = self.call()
        result["local_request"]["request_id"] = "bad:id"
        with self.assertRaises(jev.DecisionError) as caught:
            jev.validate_decision(result, request_payload()["questions"])
        self.assertEqual(caught.exception.validation_code, "binding")
        result, _, _ = self.call()
        result["latency_ms"] = -1
        with self.assertRaises(jev.DecisionError) as caught:
            jev.validate_decision(result, request_payload()["questions"])
        self.assertEqual(caught.exception.validation_code, "latency")

    def test_validation_codes_do_not_accept_arbitrary_provider_text(self):
        for value in ("PRIVATE-CODE-SENTINEL", None, {}, [], True):
            self.assertIsNone(jev.DecisionError("response", "static", validation_code=value).validation_code)
        self.assertIsNone(jev.DecisionError("provider", "static", validation_code="model").validation_code)

    def test_returned_model_provider_and_envelope(self):
        for field, value in (("model", jev.MODEL), ("provider", "Other"), ("provider", None),
                             ("answers", []), ("usage", None), ("id", "not-a-request-id"),
                             ("id", "gen-test-credential")):
            envelope = response_payload()
            envelope[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(jev.DecisionError):
                self.call(envelope)
        for envelope in ([], None, "text", 42, True):
            with self.subTest(envelope=envelope), self.assertRaises(jev.DecisionError):
                self.call(raw=json.dumps(envelope).encode())

    def test_usage_only_accepts_valid_numbers(self):
        for key, value in (("input_tokens", True), ("output_tokens", 2.2), ("cost", -1),
                           ("cost", "free"), ("cost", float("nan"))):
            envelope = response_payload()
            envelope["usage"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(jev.DecisionError):
                self.call(envelope)

    def test_malformed_duplicate_and_oversized_json(self):
        for raw in (b"not json test-credential", b"\xff", b'{"model":"x","model":"y"}',
                    b"[" * 2000, b" " * (jev.MAX_RESPONSE_BYTES + 1)):
            with self.subTest(length=len(raw)), self.assertRaises(jev.DecisionError):
                self.call(raw=raw)

    def test_failures_do_not_expose_auth_or_provider_text(self):
        for status, category in ((401, "authentication"), (403, "authentication"), (402, "credits"),
                                 (429, "rate_limit"), (302, "provider"), (502, "provider")):
            failure = HTTPError("https://test-credential.invalid", status, "test-credential",
                                {"Authorization": "test-credential"}, io.BytesIO(b"test-credential"))
            with self.subTest(status=status):
                try:
                    self.call(side_effect=failure)
                except jev.DecisionError as error:
                    self.assertEqual(error.category, category)
                    self.assertEqual(error.status_code, status)
                    self.assertNotIn("test-credential", "".join(traceback.format_exception(error)))
                else:
                    self.fail("Expected sanitized provider error")
        for failure in (URLError(TEST_CREDENTIAL), TimeoutError(TEST_CREDENTIAL),
                        OSError(TEST_CREDENTIAL), IncompleteRead(TEST_CREDENTIAL.encode())):
            try:
                self.call(side_effect=failure)
            except jev.DecisionError as error:
                self.assertEqual(error.category, "transport")
                self.assertNotIn("test-credential", "".join(traceback.format_exception(error)))
            else:
                self.fail("Expected sanitized transport error")

    def test_error_envelope_is_sanitized_even_on_http_success(self):
        with self.assertRaises(jev.DecisionError) as caught:
            self.call({"error": {"code": 401, "message": "test-credential"}})
        self.assertEqual(caught.exception.category, "authentication")
        self.assertNotIn("test-credential", str(caught.exception))

    def test_context_limit_rejection_is_specific_bounded_and_sanitized(self):
        for status in (400, 413):
            body = io.BytesIO(b'{"error":{"message":"tokens_exceeded test-credential"}}')
            failure = HTTPError("https://test-credential.invalid", status, TEST_CREDENTIAL, {}, body)
            with self.subTest(status=status), self.assertRaises(jev.DecisionError) as caught:
                self.call(side_effect=failure)
            self.assertEqual(caught.exception.category, "context_limit")
            self.assertEqual(caught.exception.status_code, status)
            self.assertNotIn(TEST_CREDENTIAL, "".join(traceback.format_exception(caught.exception)))
            self.assertTrue(body.closed)
        # Text beyond the diagnostic bound must not influence classification.
        self.assertFalse(jev._context_limit_error(b"x" * 8192 + b"tokens_exceeded"))
        self.assertFalse(jev._context_limit_error(b"Invalid model test-credential"))

    def test_unreadable_rejection_body_retains_safe_http_status(self):
        body = MagicMock()
        body.read.side_effect = OSError(TEST_CREDENTIAL)
        failure = HTTPError("https://test-credential.invalid", 400, TEST_CREDENTIAL, {}, body)
        with self.assertRaises(jev.DecisionError) as caught:
            self.call(side_effect=failure)
        self.assertEqual(caught.exception.category, "provider")
        self.assertEqual(caught.exception.status_code, 400)
        body.read.assert_called_once_with(8192)
        body.close.assert_called_once()
        self.assertNotIn(TEST_CREDENTIAL, "".join(traceback.format_exception(caught.exception)))

    def test_invalid_input_fails_without_network(self):
        bad_requests = []
        for field, value in (("questions", {}), ("state", float("nan")), ("state", {"value": float("nan")})):
            payload = request_payload()
            payload[field] = value
            bad_requests.append(payload)
        payload = request_payload()
        payload["questions"]["action"]["criteria"]["no_target"] = ""
        bad_requests.append(payload)
        payload = request_payload()
        payload["questions"]["complete"]["criteria"] = {"true": "yes"}
        bad_requests.append(payload)
        for payload in bad_requests:
            with patch.object(jev, "build_opener") as build, self.assertRaises(jev.DecisionError):
                jev.decide(payload, TEST_CREDENTIAL, **LOCAL_IDS)
            build.assert_not_called()
        for key in ("", None, "test\ncredential", "test credential"):
            with patch.object(jev, "build_opener") as build, self.assertRaises(jev.DecisionError):
                jev.decide(request_payload(), key, **LOCAL_IDS)
            build.assert_not_called()

    def test_normalized_validation_copies_and_whitelists_without_network(self):
        result, _, _ = self.call()
        expected = deepcopy(result)
        result["diagnostic"] = TEST_CREDENTIAL
        result["answers"]["complete"]["explanation"] = TEST_CREDENTIAL
        result["usage"]["credential"] = TEST_CREDENTIAL
        with patch.object(jev, "build_opener") as build:
            validated = jev.validate_decision(result, request_payload()["questions"])
        build.assert_not_called()
        self.assertEqual(validated, expected)
        self.assertNotIn(TEST_CREDENTIAL, json.dumps(validated))
        result["answers"]["action"]["probabilities"].clear()
        result["usage"].clear()
        result["local_request"]["request_id"] = "changed"
        self.assertEqual(validated, expected)
        validated["answers"]["action"]["choice"] = "no_target"
        self.assertEqual(expected["answers"]["action"]["choice"], "capture-1:c1:click")

    def test_normalized_validation_rejects_mutated_fields(self):
        original, _, _ = self.call()
        mutations = (
            lambda r: r.update(model=jev.MODEL),
            lambda r: r.update(provider="untrusted"),
            lambda r: r.update(provider_request_id="test credential"),
            lambda r: r.update(answers=[]),
            lambda r: r.update(usage=None),
            lambda r: r.update(error={"message": TEST_CREDENTIAL}),
            lambda r: r["answers"]["action"].update(choice="capture-2:c1:click"),
            lambda r: r["answers"]["action"].update(choice="no_target"),
            lambda r: r["answers"]["action"].update(confidence=True),
            lambda r: r["answers"]["action"]["probabilities"].update(no_target=float("nan")),
            lambda r: r["answers"]["complete"].update(noul=-.1),
            lambda r: r["answers"].pop("complete"),
            lambda r: r["usage"].update(cost=TEST_CREDENTIAL),
        )
        for mutate in mutations:
            result = deepcopy(original)
            mutate(result)
            with self.subTest(mutation=mutate), self.assertRaises(jev.DecisionError) as caught:
                jev.validate_decision(result, request_payload()["questions"])
            self.assertNotIn(TEST_CREDENTIAL, str(caught.exception))
        for field in original:
            result = deepcopy(original)
            del result[field]
            with self.subTest(missing=field), self.assertRaises(jev.DecisionError):
                jev.validate_decision(result, request_payload()["questions"])

    def test_normalized_validation_rejects_invalid_latency_and_questions(self):
        original, _, _ = self.call()
        for latency in (None, True, False, "123", -1, float("nan"), float("inf"), 10**500):
            result = deepcopy(original)
            result["latency_ms"] = latency
            with self.subTest(latency=latency), self.assertRaises(jev.DecisionError):
                jev.validate_decision(result, request_payload()["questions"])
        for questions in ({}, [], None, {"action": {"type": "choice"}}):
            with self.subTest(questions=questions), self.assertRaises(jev.DecisionError):
                jev.validate_decision(original, questions)

    def test_binding_hash_is_deterministic_and_covers_the_payload(self):
        payload = request_payload()
        expected_hash = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        binding = jev.request_binding(payload, **LOCAL_IDS)
        self.assertEqual(binding, {**LOCAL_IDS, "payload_sha256": expected_hash})
        reordered = dict(reversed(list(payload.items())))
        reordered["state"] = dict(reversed(list(payload["state"].items())))
        self.assertEqual(jev.request_binding(reordered, **LOCAL_IDS), binding)
        for field, value in (("task", "Close Settings"), ("capture_id", "capture-2")):
            changed = deepcopy(payload)
            changed["state"][field] = value
            self.assertNotEqual(jev.request_binding(changed, **LOCAL_IDS)["payload_sha256"], expected_hash)
        other_ids = {"request_id": "request-2", "capture_id": "capture-2"}
        other = jev.request_binding(payload, **other_ids)
        self.assertEqual(other["payload_sha256"], expected_hash)
        self.assertNotEqual(other, binding)

    def test_local_identity_is_required_and_validated_before_dispatch(self):
        with patch.object(jev, "build_opener") as build:
            with self.assertRaises(TypeError):
                jev.decide(request_payload(), TEST_CREDENTIAL)
            build.assert_not_called()
        for field in ("request_id", "capture_id"):
            for value in (None, True, "", "bad:id", "x" * 81, "new\nline"):
                identifiers = {**LOCAL_IDS, field: value}
                with self.subTest(field=field, value=value), patch.object(jev, "build_opener") as build:
                    with self.assertRaises(jev.DecisionError):
                        jev.request_binding(request_payload(), **identifiers)
                    with self.assertRaises(jev.DecisionError):
                        jev.decide(request_payload(), TEST_CREDENTIAL, **identifiers)
                    build.assert_not_called()

    def test_normalized_binding_rejects_missing_malformed_or_extra_metadata(self):
        original, _, _ = self.call()
        bindings = (None, [], {}, {**original["local_request"], "extra": TEST_CREDENTIAL},
                    {**original["local_request"], "request_id": "bad:id"},
                    {**original["local_request"], "capture_id": "x" * 81})
        for binding in bindings:
            result = deepcopy(original)
            result["local_request"] = binding
            with self.subTest(binding=binding), self.assertRaises(jev.DecisionError) as caught:
                jev.validate_decision(result, request_payload()["questions"])
            self.assertNotIn(TEST_CREDENTIAL, str(caught.exception))
        for field in original["local_request"]:
            result = deepcopy(original)
            del result["local_request"][field]
            with self.subTest(missing=field), self.assertRaises(jev.DecisionError):
                jev.validate_decision(result, request_payload()["questions"])
        for fingerprint in (None, True, "", "f" * 63, "f" * 65, "G" * 64, "A" * 64, []):
            result = deepcopy(original)
            result["local_request"]["payload_sha256"] = fingerprint
            with self.subTest(fingerprint=fingerprint), self.assertRaises(jev.DecisionError):
                jev.validate_decision(result, request_payload()["questions"])


if __name__ == "__main__":
    unittest.main()
