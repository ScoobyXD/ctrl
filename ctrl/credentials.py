"""Retrieve the OpenRouter credential into process memory; never persist or expose it."""

import json
import os
import threading

PROFILE = os.environ.get("CTRL_AWS_PROFILE", "mangorts-dev")
REGION = os.environ.get("CTRL_AWS_REGION", "us-west-1")
SECRET_ID = os.environ.get(
    "CTRL_OPENROUTER_SECRET_ID",
    "arn:aws:secretsmanager:us-west-1:686458136219:secret:mangorts/dev/openrouterjev-LitpZ1",
)
_key = None
_lock = threading.Lock()


class CredentialError(RuntimeError):
    """An actionable error that contains no credentials or SDK response bodies."""


def extract_key(secret):
    if not isinstance(secret, str) or not secret.strip():
        raise CredentialError("The AWS secret does not contain a usable OpenRouter key.")
    text = secret.strip()
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            raise CredentialError("The OpenRouter secret contains invalid JSON.") from None
        names = ("OPENROUTER_API_KEY", "openrouter_api_key", "openrouter", "api_key", "apiKey", "key", "value")
        values = [data[name] for name in names if isinstance(data.get(name), str) and data[name].strip()]
        # Only accept a clearly identifiable OpenRouter value; do not guess another provider's key.
        preferred = [v.strip() for v in data.values() if isinstance(v, str) and v.strip().startswith("sk-or-")]
        if len(preferred) == 1:
            text = preferred[0]
        elif len(values) == 1:
            text = values[0].strip()
        else:
            raise CredentialError("Store one OpenRouter key under OPENROUTER_API_KEY in the configured AWS secret.")
    if not text.startswith("sk-or-") or any(c.isspace() for c in text):
        raise CredentialError("The configured AWS secret is not an OpenRouter API key.")
    return text


def get_openrouter_key():
    global _key
    with _lock:
        if _key is not None:
            return _key
        try:
            import boto3

            session = boto3.Session(profile_name=PROFILE, region_name=REGION)
            client = session.client("secretsmanager")
            try:
                response = client.get_secret_value(SecretId=SECRET_ID)
            finally:
                client.close()
            _key = extract_key(response.get("SecretString"))
            return _key
        except CredentialError:
            raise
        except Exception as exc:
            name = type(exc).__name__
            if name in {"UnauthorizedSSOTokenError", "SSOTokenLoadError", "TokenRetrievalError", "NoCredentialsError"}:
                raise CredentialError(f"AWS sign-in expired. Run: aws sso login --profile {PROFILE}") from None
            if name == "ProfileNotFound":
                raise CredentialError(f"AWS SSO profile '{PROFILE}' is not configured on this computer.") from None
            raise CredentialError(
                "Could not read the OpenRouter secret. "
                "Check AWS SSO, network access, and secretsmanager:GetSecretValue."
            ) from None


def clear_cached_key():
    global _key
    with _lock:
        _key = None
