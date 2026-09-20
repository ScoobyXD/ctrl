"""Offline helper contracts: explicit remote opt-in and sanitized NDJSON."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ctrl_loop import jev
from ctrl_loop.uia_contract import build_options, new_snapshot

spec = importlib.util.spec_from_file_location("jev_select_cli", ROOT / "scripts/jev-select.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def fixture():
    snapshot = new_snapshot(111, 222)
    snapshot["snapshot_id"] = "cli-snapshot"
    snapshot["target"]["title"] = "Owned fixture"
    snapshot["elements"] = [
        {"id": "cli-snapshot:e1", "parent_id": None, "name": "Save draft", "role": "Button",
         "is_password": False, "value": None, "patterns": ["Invoke"], "state": {},
         "enabled": True, "offscreen": False, "focused": False},
    ]
    snapshot["options"] = build_options(snapshot["elements"])
    return {"snapshot": snapshot, "goal": "Save the draft", "run_id": "test-client"}


def response(payload, key, *, request_id, capture_id):
    answers = {}
    for qid, question in payload["questions"].items():
        options = question["criteria"]
        winner = "promising" if "promising" in options else next(k for k in options if k != "__none__")
        answers[qid] = {"type": "choice", "choice": winner, "confidence": 1,
                        "probabilities": {k: int(k == winner) for k in options}}
    return {"answers": answers, "model": jev.RESOLVED_MODEL, "provider": jev.PROVIDER,
            "provider_request_id": "gen-mocked", "usage": {"cost": 0.001}, "latency_ms": 1,
            "local_request": jev.request_binding(payload, request_id=request_id, capture_id=capture_id)}


class SelectionCliTests(unittest.TestCase):
    def invoke(self, data=None, *args):
        body = json.dumps(fixture() if data is None else data).encode()
        output = io.StringIO()
        with patch.object(sys, "argv", ["jev-select.py", "--stdin", *args]), \
             patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(body), encoding="utf-8")), \
             redirect_stdout(output):
            status = cli.main()
        return status, output.getvalue(), [json.loads(line) for line in output.getvalue().splitlines()]

    def test_offline_default_never_loads_credentials_or_sends(self):
        with patch.object(cli, "get_openrouter_key") as key, patch.object(cli, "decide") as remote, \
             patch("socket.socket", side_effect=AssertionError("Unexpected network")):
            code, _, records = self.invoke()
        self.assertEqual(code, 0)
        key.assert_not_called()
        remote.assert_not_called()
        self.assertEqual([r["event"] for r in records], ["result"])
        self.assertEqual(records[0]["data"]["client_run_id"], "test-client")
        self.assertTrue(records[0]["data"]["initial_requests"])

    def test_live_stream_contains_progress_and_one_bound_preview_result(self):
        secret = "sk-or-SYNTHETIC-TEST-ONLY"
        with patch.object(cli, "get_openrouter_key", return_value=secret) as key, \
             patch.object(cli, "decide", side_effect=response) as remote, \
             patch("socket.socket", side_effect=AssertionError("Unexpected network")):
            code, output, records = self.invoke(None, "--live")
        self.assertEqual(code, 0)
        key.assert_called_once_with()
        self.assertGreaterEqual(remote.call_count, 2)
        self.assertNotIn(secret, output)
        self.assertTrue(any(r["event"] == "progress" for r in records))
        requests = [record["data"] for record in records if record["event"] == "request"]
        self.assertEqual(len(requests), remote.call_count)
        self.assertEqual([request["stage"] for request in requests], ["classify", "final"])
        for request, call in zip(requests, remote.call_args_list):
            self.assertEqual(request["client_run_id"], "test-client")
            self.assertEqual(request["payload"], call.args[0])
            self.assertEqual(request["request_id"], call.kwargs["request_id"])
            self.assertEqual(request["capture_id"], call.kwargs["capture_id"])
            self.assertEqual(request["body_bytes"], len(json.dumps(
                request["payload"], allow_nan=False, separators=(",", ":")).encode("utf-8")))
            self.assertNotIn("headers", request)
        result = records[-1]["data"]
        self.assertEqual(result["status"], "selected")
        self.assertTrue(result["preview_only"])
        self.assertEqual(result["input_actions"], 0)
        self.assertEqual(result["client_run_id"], "test-client")
        self.assertEqual(result["selected_option"]["id"], "cli-snapshot:e1:activate")

    def test_aws_lookup_failure_has_no_request_events(self):
        with patch.object(cli, "get_openrouter_key", side_effect=cli.CredentialError()), \
             patch.object(cli, "decide") as remote:
            code, _, records = self.invoke(None, "--live")
        self.assertEqual(code, 1)
        self.assertFalse(any(record["event"] == "request" for record in records))
        remote.assert_not_called()

    def test_failed_provider_request_is_available_before_error(self):
        with patch.object(cli, "get_openrouter_key", return_value="sk-or-KEY-SENTINEL"), \
             patch.object(cli, "decide", side_effect=jev.DecisionError("transport", "private")) as remote:
            code, output, records = self.invoke(None, "--live")
        self.assertEqual(code, 1)
        request_records = [record for record in records if record["event"] == "request"]
        self.assertEqual(len(request_records), 1)
        self.assertEqual(request_records[0]["data"]["stage"], "classify")
        self.assertEqual(request_records[0]["data"]["payload"], remote.call_args.args[0])
        self.assertLess(records.index(request_records[0]), len(records) - 1)
        self.assertEqual(records[-1]["event"], "error")
        self.assertNotIn("SENTINEL", output)

    def test_invalid_input_fails_before_credential_lookup(self):
        with patch.object(cli, "get_openrouter_key") as key:
            code, _, records = self.invoke({"snapshot": {}, "goal": "Save"}, "--live")
        self.assertEqual(code, 1)
        key.assert_not_called()
        self.assertEqual(records[-1]["event"], "error")

    def test_provider_exception_content_is_not_rendered(self):
        secret = "sk-or-SYNTHETIC-TEST-ONLY"
        with patch.object(cli, "get_openrouter_key", return_value=secret), \
             patch.object(cli, "decide", side_effect=RuntimeError(secret)):
            code, output, records = self.invoke(None, "--live")
        self.assertEqual(code, 1)
        self.assertNotIn(secret, output)
        self.assertNotIn("Traceback", output)
        self.assertEqual(records[-1]["event"], "error")

    def test_provider_statuses_are_distinguished_without_provider_text(self):
        cases = (
            (401, "authentication", "credential"),
            (403, "authentication", "access"),
            (402, "credits", "credits"),
            (429, "rate_limit", "rate limited"),
            (400, "provider", "request format"),
            (413, "provider", "too large"),
            (502, "provider", "could not complete"),
        )
        for status, category, text in cases:
            error = jev._http_error(status)
            error.args = ("sk-or-PROVIDER-MESSAGE-SENTINEL",)
            with self.subTest(status=status), \
                 patch.object(cli, "get_openrouter_key", return_value="sk-or-KEY-SENTINEL"), \
                 patch.object(cli, "decide", side_effect=error), \
                 patch("socket.socket", side_effect=AssertionError("Unexpected network")):
                code, output, records = self.invoke(None, "--live")
            self.assertEqual(code, 1)
            record = records[-1]
            self.assertEqual(record["event"], "error")
            self.assertEqual(record["input_actions"], 0)
            self.assertEqual(record["category"], category)
            self.assertEqual(record["http_status"], status)
            self.assertIn(f"HTTP {status}", record["message"])
            self.assertIn(text, record["message"])
            self.assertNotIn("SENTINEL", output)
            self.assertNotIn("Traceback", output)

    def test_local_response_and_transport_errors_explain_the_failed_boundary(self):
        cases = (("request", "local validation"), ("response", "response failed validation"),
                 ("transport", "connection failed or timed out"))
        for category, text in cases:
            error = jev.DecisionError(category, "sk-or-ERROR-SENTINEL")
            with self.subTest(category=category), \
                 patch.object(cli, "get_openrouter_key", return_value="sk-or-KEY-SENTINEL"), \
                 patch.object(cli, "decide", side_effect=error):
                code, output, records = self.invoke(None, "--live")
            self.assertEqual(code, 1)
            self.assertEqual(records[-1]["category"], category)
            self.assertIn(text, records[-1]["message"])
            self.assertNotIn("http_status", records[-1])
            self.assertNotIn("SENTINEL", output)

    def test_request_failure_before_credentials_does_not_claim_aws_succeeded(self):
        with patch.object(cli, "SelectionSearch", side_effect=jev.DecisionError("request", "private")), \
             patch.object(cli, "get_openrouter_key") as key:
            code, _, records = self.invoke(None, "--live")
        self.assertEqual(code, 1)
        key.assert_not_called()
        self.assertEqual(records[-1]["category"], "request")
        self.assertIn("before it could be sent", records[-1]["message"])
        self.assertNotIn("AWS", records[-1]["message"])

    def test_response_validation_codes_have_specific_safe_diagnostics(self):
        expected = {
            "envelope": "decision object", "model": "pinned version",
            "provider": "pinned TypeSafe", "question_ids": "question IDs",
            "answer_type": "question type", "choice_id": "not offered",
            "probability_ids": "offered option IDs", "probability_value": "finite number",
            "probability_sum": "rounding allowance", "choice_maximum": "highest returned probability",
            "confidence": "valid confidence", "noul": "yes-probability",
            "provider_request_id": "provider request ID", "usage": "usage data",
            "json": "decision JSON", "response_size": "response size limit",
            "binding": "observation identifiers", "latency": "timing data",
        }
        self.assertEqual(set(cli._RESPONSE_VALIDATION_MESSAGES), set(expected))
        for validation_code, text in expected.items():
            error = jev.DecisionError("response", "sk-or-ERROR-SENTINEL")
            error.validation_code = validation_code
            with self.subTest(validation_code=validation_code), \
                 patch.object(cli, "get_openrouter_key", return_value="sk-or-KEY-SENTINEL"), \
                 patch.object(cli, "decide", side_effect=error):
                code, output, records = self.invoke(None, "--live")
            self.assertEqual(code, 1)
            self.assertEqual(records[-1]["category"], "response")
            self.assertEqual(records[-1]["input_actions"], 0)
            self.assertIn(text, records[-1]["message"])
            self.assertTrue(records[-1]["message"].endswith(f"[{validation_code}]"))
            self.assertNotIn("http_status", records[-1])
            self.assertNotIn("SENTINEL", output)

    def test_untrusted_validation_codes_use_generic_response_diagnostic(self):
        class HostileCode(str):
            def __hash__(self):
                raise AssertionError("Untrusted validation code was hashed")

            def __str__(self):
                raise AssertionError("Untrusted validation code was formatted")

        for validation_code in (None, "sk-or-VALIDATION-SENTINEL", "probability_sum\nSENTINEL",
                                {"code": "SENTINEL"}, ["SENTINEL"], True, 1,
                                HostileCode("probability_sum")):
            error = jev.DecisionError("response", "sk-or-ERROR-SENTINEL")
            error.validation_code = validation_code
            with self.subTest(code_type=type(validation_code)), \
                 patch.object(cli, "get_openrouter_key", return_value="sk-or-KEY-SENTINEL"), \
                 patch.object(cli, "decide", side_effect=error):
                code, output, records = self.invoke(None, "--live")
            self.assertEqual(code, 1)
            self.assertEqual(records[-1]["category"], "response")
            self.assertIn("response failed validation", records[-1]["message"])
            self.assertNotIn("[", records[-1]["message"])
            self.assertNotIn("SENTINEL", output)
            self.assertNotIn("Traceback", output)

    def test_response_validation_code_does_not_override_http_failure(self):
        error = jev.DecisionError("response", "SENTINEL", status_code=429)
        error.validation_code = "probability_sum"
        result = cli._decision_diagnostic(error)
        self.assertEqual(result["category"], "rate_limit")
        self.assertIn("HTTP 429", result["message"])
        self.assertNotIn("probability_sum", result["message"])
        self.assertNotIn("SENTINEL", result["message"])

    def test_provider_context_limit_has_a_specific_safe_diagnostic(self):
        error = jev._http_error(400, context_limit=True)
        error.args = ("sk-or-ERROR-SENTINEL",)
        with patch.object(cli, "get_openrouter_key", return_value="sk-or-KEY-SENTINEL"), \
             patch.object(cli, "decide", side_effect=error):
            code, output, records = self.invoke(None, "--live")
        self.assertEqual(code, 1)
        self.assertEqual(records[-1]["category"], "context_limit")
        self.assertEqual(records[-1]["http_status"], 400)
        self.assertIn("context limit", records[-1]["message"])
        self.assertNotIn("SENTINEL", output)

    def test_oversized_shared_context_is_rejected_before_aws_lookup(self):
        request = fixture()
        request["goal"] = "\U0001f600" * 4000
        with patch.object(cli, "get_openrouter_key") as key, patch.object(cli, "decide") as remote:
            code, _, records = self.invoke(request, "--live")
        self.assertEqual(code, 1)
        self.assertEqual(records[-1]["category"], "context_limit")
        self.assertIn("Shorten the goal", records[-1]["message"])
        key.assert_not_called()
        remote.assert_not_called()

    def test_untrusted_error_fields_are_not_formatted_or_emitted(self):
        class HostileText(str):
            def __hash__(self):
                raise AssertionError("Untrusted category was hashed")

            def __str__(self):
                raise AssertionError("Untrusted text was formatted")

        class HostileStatus(int):
            def __str__(self):
                raise AssertionError("Untrusted status was formatted")

        fields = (
            ("sk-or-CATEGORY-SENTINEL", "sk-or-STATUS-SENTINEL"),
            ({"key": "sk-or-CATEGORY-SENTINEL"}, ["sk-or-STATUS-SENTINEL"]),
            (HostileText("provider"), HostileStatus(500)),
            (None, True), ([], 10**100), (False, -1),
        )
        for category, status in fields:
            error = jev.DecisionError(category, "sk-or-MESSAGE-SENTINEL", status_code=status)
            with self.subTest(category_type=type(category), status_type=type(status)), \
                 patch.object(cli, "get_openrouter_key", return_value="sk-or-KEY-SENTINEL"), \
                 patch.object(cli, "decide", side_effect=error):
                code, output, records = self.invoke(None, "--live")
            self.assertEqual(code, 1)
            self.assertEqual(records[-1]["category"], "provider")
            self.assertNotIn("http_status", records[-1])
            self.assertNotIn("SENTINEL", output)
            self.assertNotIn("Traceback", output)


if __name__ == "__main__":
    unittest.main()
