import importlib
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import traceback
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ctrl_loop import credentials


KEY = "sk-or-v1-SYNTHETIC_NOT_A_CREDENTIAL"


class SecretFormatTests(unittest.TestCase):
    def test_raw_and_each_supported_json_field(self):
        self.assertEqual(credentials.extract_key(" \n" + KEY + "\n"), KEY)
        for field in credentials._KEY_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(credentials.extract_key(json.dumps({field: KEY})), KEY)
        self.assertEqual(credentials.extract_key(json.dumps({"api_key": KEY, "description": "fixture"})), KEY)

    def test_malformed_ambiguous_or_unrecognized_secrets_fail_closed(self):
        values = [
            None, b"bytes", "", "   ", "sk-other-provider", "sk-or-", KEY + "\nextra",
            KEY + "\x00", KEY + '"', "[\"" + KEY + "\"]", json.dumps(KEY),
            "{", "null", "{}", '{"api_key": "unterminated',
            json.dumps({"unknown": KEY}), json.dumps({"api_key": None}),
            json.dumps({"api_key": 123}), json.dumps({"api_key": {"key": KEY}}),
            json.dumps({"api_key": KEY, "key": KEY}),
            json.dumps({"api_key": KEY, "OPENROUTER_API_KEY": None}),
            json.dumps({"api_key": KEY, "unknown": KEY}),
            '{"api_key":"' + KEY + '","api_key":"' + KEY + '"}',
        ]
        for value in values:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(credentials.CredentialError) as caught:
                    credentials.extract_key(value)
                self.assertNotIn(KEY, str(caught.exception))
                self.assertNotIn(KEY, repr(caught.exception))

    def test_exception_constructor_does_not_echo_unknown_payload(self):
        for payload in (KEY, {"key": KEY}, [KEY]):
            error = credentials.CredentialError(payload)
            self.assertNotIn(KEY, str(error))
            self.assertNotIn(KEY, repr(error))


class RuntimeCredentialTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.get_secret_value.return_value = {"SecretString": KEY}
        self.session = Mock()
        self.session.client.return_value = self.client
        self.boto3 = types.SimpleNamespace(Session=Mock(return_value=self.session))
        self.modules = patch.dict(sys.modules, {"boto3": self.boto3})
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.modules.start()
        self.environment.start()
        self.addCleanup(self.modules.stop)
        self.addCleanup(self.environment.stop)

    def test_import_has_no_boto3_or_aws_side_effect(self):
        importlib.reload(credentials)
        self.boto3.Session.assert_not_called()
        # A fresh interpreter additionally proves boto3 was not imported at all.
        script = "import sys; import ctrl_loop.credentials; assert 'boto3' not in sys.modules"
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        completed = subprocess.run([sys.executable, "-c", script], env=environment, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_defaults_and_client_cleanup_without_cache(self):
        self.assertEqual(credentials.get_openrouter_key(), KEY)
        self.boto3.Session.assert_called_once_with(profile_name="mangorts-dev", region_name="us-west-1")
        self.session.client.assert_called_once_with("secretsmanager")
        self.client.get_secret_value.assert_called_once_with(SecretId=credentials.DEFAULT_SECRET_ID)
        self.client.close.assert_called_once_with()
        self.client.get_secret_value.return_value = {"SecretString": KEY + "_ROTATED"}
        self.assertEqual(credentials.get_openrouter_key(), KEY + "_ROTATED")

    def test_overrides_are_read_at_call_time(self):
        with patch.dict(os.environ, {"CTRL_AWS_PROFILE": "fixture-profile", "CTRL_AWS_REGION": "fixture-region", "CTRL_OPENROUTER_SECRET_ID": "fixture-id"}):
            self.assertEqual(credentials.get_openrouter_key(), KEY)
        self.boto3.Session.assert_called_once_with(profile_name="fixture-profile", region_name="fixture-region")
        self.client.get_secret_value.assert_called_once_with(SecretId="fixture-id")

    def test_binary_missing_and_malformed_responses_are_rejected(self):
        for response in ({"SecretBinary": b"synthetic"}, {}, None, [], {"SecretString": "{" + KEY}):
            self.client.get_secret_value.return_value = response
            with self.assertRaises(credentials.CredentialError):
                credentials.get_openrouter_key()
        self.assertEqual(self.client.close.call_count, 5)

    def test_sdk_errors_redact_message_repr_and_standard_traceback(self):
        for error_name in ("UnauthorizedSSOTokenError", "SSOTokenLoadError", "TokenRetrievalError", "NoCredentialsError", "PartialCredentialsError", "ProfileNotFound", "ClientError"):
            error_type = type(error_name, (Exception,), {})
            self.client.get_secret_value.side_effect = error_type(KEY)
            try:
                credentials.get_openrouter_key()
            except credentials.CredentialError as error:
                self.assertNotIn(KEY, str(error))
                self.assertNotIn(KEY, repr(error))
                self.assertNotIn(KEY, "".join(traceback.format_exception(error)))
                self.assertTrue(error.__suppress_context__)
                self.assertIsNone(error.__cause__)
            else:
                self.fail("Expected a sanitized credential error")
        self.assertEqual(self.client.close.call_count, 7)

    def test_session_client_and_close_failures_are_sanitized(self):
        for method in (self.boto3.Session, self.session.client, self.client.close):
            method.side_effect = RuntimeError(KEY)
            with self.assertRaises(credentials.CredentialError) as caught:
                credentials.get_openrouter_key()
            self.assertNotIn(KEY, repr(caught.exception))
            method.side_effect = None

    def test_missing_dependency_has_static_error(self):
        with patch.dict(sys.modules, {"boto3": None}):
            with self.assertRaisesRegex(credentials.CredentialError, "pinned boto3"):
                credentials.get_openrouter_key()

    def test_sdk_logs_are_suppressed_and_previous_threshold_restored(self):
        logger = logging.getLogger("credential-test.botocore.parsers")
        previous_level, previous_propagate = logger.level, logger.propagate
        previous_disabled = logging.root.manager.disable
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        def emit_sdk_logs():
            for level in (logging.DEBUG, logging.ERROR, logging.CRITICAL):
                logger.log(level, "SDK response body: %s", KEY)

        def retrieve(**kwargs):
            emit_sdk_logs()
            return {"SecretString": KEY}

        self.client.get_secret_value.side_effect = retrieve
        self.client.close.side_effect = emit_sdk_logs
        try:
            for threshold in (logging.NOTSET, logging.WARNING, 100):
                logging.disable(threshold)
                self.assertEqual(credentials.get_openrouter_key(), KEY)
                self.assertEqual(logging.root.manager.disable, threshold)
            self.assertEqual(stream.getvalue(), "")
            logging.disable(logging.NOTSET)
            logger.error("Safe marker after lookup")
            self.assertIn("Safe marker after lookup", stream.getvalue())
            self.assertNotIn(KEY, stream.getvalue())
        finally:
            logging.disable(previous_disabled)
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate

    def test_failed_sdk_lookup_restores_logging_without_emitting_secret(self):
        logger = logging.getLogger("credential-test.botocore.failure")
        previous_level, previous_propagate = logger.level, logger.propagate
        previous_disabled = logging.root.manager.disable
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        def fail(**kwargs):
            logger.debug("Response body: %s", KEY)
            logger.error("SDK authentication diagnostic: %s", KEY)
            raise RuntimeError(KEY)

        self.client.get_secret_value.side_effect = fail
        try:
            logging.disable(logging.NOTSET)
            with self.assertRaises(credentials.CredentialError) as caught:
                credentials.get_openrouter_key()
            self.assertEqual(logging.root.manager.disable, logging.NOTSET)
            self.assertEqual(stream.getvalue(), "")
            self.assertNotIn(KEY, repr(caught.exception))
            self.client.close.assert_called_once_with()
        finally:
            logging.disable(previous_disabled)
            logger.removeHandler(handler)
            logger.setLevel(previous_level)
            logger.propagate = previous_propagate


if __name__ == "__main__":
    unittest.main()
