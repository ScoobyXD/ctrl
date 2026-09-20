"""Read the existing OpenRouter credential at runtime without persisting it.

Importing this module does not import boto3, contact AWS, or load a credential.
The returned key belongs only in process memory; callers must never log it.
"""

import json
import logging
import os
import re
import threading


DEFAULT_PROFILE = "mangorts-dev"
DEFAULT_REGION = "us-west-1"
DEFAULT_SECRET_ID = (
    "arn:aws:secretsmanager:us-west-1:686458136219:"
    "secret:mangorts/dev/openrouterjev-LitpZ1"
)

_KEY_FIELDS = frozenset(
    {"OPENROUTER_API_KEY", "openrouter_api_key", "openrouter", "api_key", "apiKey", "key", "value"}
)
_KEY_PATTERN = re.compile(r"sk-or-[A-Za-z0-9_-]+", re.ASCII)
_LOOKUP_LOCK = threading.RLock()
_ERRORS = {
    "dependency": "AWS credential support requires the pinned boto3 dependency.",
    "profile": "The configured AWS profile is unavailable. Check CTRL_AWS_PROFILE and the existing AWS configuration.",
    "signin": "AWS sign-in is unavailable or expired. Sign in with the existing AWS SSO profile and retry.",
    "lookup": "Could not read the OpenRouter secret. Check AWS sign-in, network access, region, and secretsmanager:GetSecretValue permission.",
    "format": "The configured AWS secret must contain one OpenRouter API key, as a raw key or in one supported JSON field.",
}


class CredentialError(RuntimeError):
    """A static diagnostic that cannot echo supplied credentials or SDK errors."""

    def __init__(self, code="lookup"):
        # Treat even an accidentally supplied payload as an unknown error code.
        message = _ERRORS.get(code, _ERRORS["lookup"]) if isinstance(code, str) else _ERRORS["lookup"]
        super().__init__(message)


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise CredentialError("format")
        result[name] = value
    return result


def extract_key(secret):
    """Accept a raw OpenRouter key or one explicitly named JSON key field.

    Supported fields are OPENROUTER_API_KEY, openrouter_api_key, openrouter,
    api_key, apiKey, key, and value. Duplicate or multiple supported fields
    are rejected; values under unknown fields are never guessed to be keys.
    """
    if not isinstance(secret, str) or not secret.strip():
        raise CredentialError("format") from None
    value = secret.strip()
    if value.startswith("{"):
        try:
            data = json.loads(value, object_pairs_hook=_unique_object)
        except (ValueError, TypeError, CredentialError):
            raise CredentialError("format") from None
        fields = _KEY_FIELDS.intersection(data)
        if len(fields) != 1:
            raise CredentialError("format") from None
        field = next(iter(fields))
        value = data[field]
        if any(
            name != field and isinstance(other, str) and other.strip().startswith("sk-or-")
            for name, other in data.items()
        ):
            raise CredentialError("format") from None
    if not isinstance(value, str) or _KEY_PATTERN.fullmatch(value.strip()) is None:
        raise CredentialError("format") from None
    return value.strip()


def get_openrouter_key():
    """Retrieve the configured SecretString with the existing AWS auth flow.

    Nonsecret location overrides: CTRL_AWS_PROFILE, CTRL_AWS_REGION, and
    CTRL_OPENROUTER_SECRET_ID. No key is cached, written, or logged here.
    SecretBinary is intentionally unsupported. During the lookup all process
    logging at CRITICAL or lower is temporarily disabled, because boto3/botocore
    debug logs may include secret response bodies. A lock serializes this
    module's lookups; the prior logging threshold is restored even on failure.
    This is intended for the single-process preview CLI: unrelated threads'
    log records are also suppressed during this short scope.
    """
    with _LOOKUP_LOCK:
        previous = logging.root.manager.disable
        logging.disable(max(previous, logging.CRITICAL))
        try:
            return _lookup_openrouter_key()
        finally:
            logging.disable(previous)


def _lookup_openrouter_key():
    try:
        import boto3
    except ImportError:
        raise CredentialError("dependency") from None

    try:
        session = boto3.Session(
            profile_name=os.environ.get("CTRL_AWS_PROFILE", DEFAULT_PROFILE),
            region_name=os.environ.get("CTRL_AWS_REGION", DEFAULT_REGION),
        )
        client = session.client("secretsmanager")
        try:
            response = client.get_secret_value(
                SecretId=os.environ.get("CTRL_OPENROUTER_SECRET_ID", DEFAULT_SECRET_ID)
            )
        finally:
            client.close()
        if not isinstance(response, dict):
            raise CredentialError("format")
        return extract_key(response.get("SecretString"))
    except CredentialError:
        raise
    except Exception as error:
        name = type(error).__name__
        if name == "ProfileNotFound":
            raise CredentialError("profile") from None
        if name in {
            "UnauthorizedSSOTokenError", "SSOTokenLoadError", "TokenRetrievalError",
            "NoCredentialsError", "PartialCredentialsError",
        }:
            raise CredentialError("signin") from None
        raise CredentialError("lookup") from None
