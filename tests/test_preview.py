"""CLI defaults, mocked decision integration, and diagnostic redaction."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ctrl_loop import jev

spec = importlib.util.spec_from_file_location("jev_preview", ROOT / "scripts/jev-preview.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)

KEY = "sk-or-v1-SYNTHETIC_NOT_A_CREDENTIAL"


class FixtureResponse:
    status = 200

    def __init__(self, payload):
        criteria = payload["questions"]["next_action"]["criteria"]
        winner = next(iter(criteria))
        self.body = json.dumps({
            "model": jev.RESOLVED_MODEL,
            "provider": jev.PROVIDER,
            "id": "gen-offline-test",
            "answers": {
                "next_action": {"type": "choice", "choice": winner,
                                "probabilities": {key: int(key == winner) for key in criteria},
                                "confidence": 0.99},
                "already_satisfied": {"type": "noul", "noul": 0.01},
                "no_viable_target": {"type": "noul", "noul": 0.02},
            },
            "usage": {"input_tokens": 10, "output_tokens": 20, "cost": 0.0001},
            "ignored_diagnostic": KEY,
        }).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.body[:limit]


class PreviewCliTests(unittest.TestCase):
    def run_main(self, args=()):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["jev-preview.py", *args]), redirect_stdout(stdout), redirect_stderr(stderr):
            code = preview.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_default_outputs_preview_without_credentials_or_network(self):
        with patch.object(preview, "get_openrouter_key") as credential, \
             patch.object(preview, "decide") as decision, \
             patch("socket.socket", side_effect=AssertionError("Offline CLI attempted network")):
            code, stdout, stderr = self.run_main()
        self.assertEqual(code, 0, stderr)
        credential.assert_not_called()
        decision.assert_not_called()
        outgoing = json.loads(stdout)["outgoing_preview"]
        self.assertEqual(outgoing["input_actions"], 0)
        self.assertEqual(outgoing["endpoint"], jev.ENDPOINT)
        self.assertEqual(outgoing["payload"]["model"], jev.MODEL)
        self.assertEqual(outgoing["payload"]["provider"]["only"], ["typesafe"])
        self.assertFalse(outgoing["payload"]["provider"]["allow_fallbacks"])
        self.assertEqual(stderr, "")

    def test_mocked_live_request_validates_and_redacts_saved_result(self):
        sent = []

        def respond(request, timeout):
            self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
            self.assertEqual(request.full_url, jev.ENDPOINT)
            self.assertEqual(request.method, "POST")
            self.assertEqual(timeout, jev.TIMEOUT_SECONDS)
            payload = json.loads(request.data)
            self.assertNotIn(KEY, json.dumps(payload))
            sent.append(payload)
            return FixtureResponse(payload)

        opener = Mock()
        opener.open.side_effect = respond
        output = ROOT / ".ctrl" / "mocked-test-result.json"
        with patch.object(preview, "get_openrouter_key", return_value=KEY) as credential, \
             patch.object(jev, "build_opener", return_value=opener), \
             patch("socket.socket", side_effect=AssertionError("Mocked CLI attempted network")), \
             patch.object(Path, "mkdir"), patch.object(Path, "write_text", autospec=True) as write:
            code, stdout, stderr = self.run_main(["--live", "--output", str(output)])
        self.assertEqual(code, 0, stderr)
        write.assert_called_once()
        self.assertEqual(write.call_args.args[0], output)
        saved = write.call_args.args[1]
        credential.assert_called_once_with()
        opener.open.assert_called_once()
        self.assertEqual(len(sent), 1)
        for contents in (stdout, stderr, saved):
            self.assertNotIn(KEY, contents)
        result = json.loads(saved)
        self.assertEqual(result["input_actions"], 0)
        self.assertEqual(result["status"], "preview_only")
        self.assertEqual(result["decision"]["provider"], jev.PROVIDER)
        self.assertEqual(result["decision"]["model"], jev.RESOLVED_MODEL)
        self.assertEqual(result["requested_model"], jev.MODEL)
        self.assertEqual(result["decision"]["answers"]["already_satisfied"]["noul"], 0.01)
        self.assertNotIn("ignored_diagnostic", result["decision"])

    def test_generic_secret_bearing_failures_are_redacted_and_write_no_result(self):
        for failing_stage in ("get_openrouter_key", "decide"):
            with self.subTest(failing_stage=failing_stage):
                output = ROOT / ".ctrl" / "mocked-test-result.json"
                with patch.object(preview, "get_openrouter_key", return_value=KEY) as credential, \
                     patch.object(preview, "decide") as decision, \
                     patch.object(Path, "write_text") as write:
                    (credential if failing_stage == "get_openrouter_key" else decision).side_effect = RuntimeError(KEY)
                    code, stdout, stderr = self.run_main(["--live", "--output", str(output)])
                self.assertEqual(code, 1)
                self.assertNotIn(KEY, stdout + stderr)
                write.assert_not_called()
                self.assertEqual(json.loads(stderr)["input_actions"], 0)
                self.assertNotIn("Traceback", stderr)

    def test_malformed_provider_response_never_becomes_accepted_preview(self):
        def respond(request, timeout):
            response = FixtureResponse(json.loads(request.data))
            envelope = json.loads(response.body)
            envelope["answers"]["already_satisfied"]["noul"] = KEY
            response.body = json.dumps(envelope).encode("utf-8")
            return response

        opener = Mock()
        opener.open.side_effect = respond
        with patch.object(preview, "get_openrouter_key", return_value=KEY), \
             patch.object(jev, "build_opener", return_value=opener), \
             patch.object(preview.PreviewRegistry, "accept") as accept:
            code, stdout, stderr = self.run_main(["--live"])
        self.assertEqual(code, 1)
        accept.assert_not_called()
        self.assertNotIn(KEY, stdout + stderr)
        self.assertEqual(json.loads(stderr)["status"], "error")

    def test_missing_fixture_reports_static_error_before_credentials(self):
        with patch.object(Path, "read_text", side_effect=FileNotFoundError), \
             patch.object(preview, "get_openrouter_key") as credential:
            code, stdout, stderr = self.run_main(["--fixture", "missing.json", "--live"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        credential.assert_not_called()
        self.assertEqual(json.loads(stderr)["message"], "Decision preview failed; check setup and fixture format.")


if __name__ == "__main__":
    unittest.main()
