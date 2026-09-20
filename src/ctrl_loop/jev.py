"""Pinned, decision-only OpenRouter adapter. Credentials are supplied in memory."""
from copy import deepcopy
from http.client import HTTPException
import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
RESOLVED_MODEL = "typesafe/jev-1.13-20260917"
PROVIDER = "TypeSafe"
TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1_048_576
# Permit serialization rounding, not a materially incomplete distribution.
PROBABILITY_SUM_TOLERANCE = 1e-6
_ROUTE = {"only": ["typesafe"], "order": ["typesafe"], "allow_fallbacks": False}


class DecisionError(RuntimeError):
    """A safe error whose message never includes provider or credential content."""

    def __init__(self, category, message, *, status_code=None):
        super().__init__(message)
        self.category = category
        self.status_code = status_code


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request_error():
    return DecisionError("request", "Invalid JEV decision request or pinned route.")


def _response_error():
    return DecisionError("response", "JEV returned an invalid or unexpected decision response.")


def _http_error(status):
    if type(status) is not int or not 100 <= status <= 599:
        return DecisionError("provider", "JEV provider request failed.")
    if status in (401, 403):
        category, message = "authentication", "JEV authentication or access was rejected."
    elif status == 402:
        category, message = "credits", "OpenRouter credits are insufficient for this request."
    elif status == 429:
        category, message = "rate_limit", "JEV request was rate limited."
    else:
        category, message = "provider", f"JEV provider request failed (HTTP {status})."
    return DecisionError(category, message, status_code=status)


def _probability(value):
    if type(value) not in (int, float):
        raise _response_error()
    try:
        valid = math.isfinite(value) and 0 <= value <= 1
    except OverflowError:
        valid = False
    if not valid:
        raise _response_error()
    return value


def _validate_request(payload):
    if (type(payload) is not dict
            or set(payload) != {"model", "state", "questions", "provider"}
            or payload["model"] != MODEL
            or payload["provider"] != _ROUTE
            or type(payload["provider"].get("allow_fallbacks")) is not bool
            or type(payload["state"]) not in (str, dict, list)):
        raise _request_error()
    _validate_questions(payload["questions"])


def _validate_questions(questions):
    if type(questions) is not dict or not questions:
        raise _request_error()
    for question_id, question in questions.items():
        if (type(question_id) is not str or not question_id
                or type(question) is not dict
                or set(question) - {"type", "instructions", "criteria"}
                or type(question.get("instructions")) is not str
                or not question["instructions"].strip()):
            raise _request_error()
        criteria = question.get("criteria")
        if question.get("type") == "choice":
            if (type(criteria) is not dict or not 2 <= len(criteria) <= 255
                    or any(type(key) is not str or not key for key in criteria)
                    or any(type(value) is not str or not value.strip()
                           for value in criteria.values())):
                raise _request_error()
        elif question.get("type") == "noul":
            if "criteria" in question and (
                    type(criteria) is not dict or set(criteria) != {"true", "false"}
                    or any(type(value) is not str or not value.strip()
                           for value in criteria.values())):
                raise _request_error()
        else:
            raise _request_error()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON number")


def _validate_response(envelope, questions, api_key=None):
    if type(envelope) is not dict:
        raise _response_error()
    if "error" in envelope:
        error = envelope["error"]
        raise _http_error(error.get("code") if type(error) is dict else None)
    if envelope.get("model") != RESOLVED_MODEL or envelope.get("provider") != PROVIDER:
        raise _response_error()
    answers = envelope.get("answers")
    if type(answers) is not dict or set(answers) != set(questions):
        raise _response_error()
    validated = {}
    for question_id, question in questions.items():
        answer = answers[question_id]
        if type(answer) is not dict or answer.get("type") != question["type"]:
            raise _response_error()
        if question["type"] == "choice":
            choice = answer.get("choice")
            options = question["criteria"]
            probabilities = answer.get("probabilities")
            if (type(choice) is not str or choice not in options
                    or type(probabilities) is not dict or set(probabilities) != set(options)):
                raise _response_error()
            probabilities = {key: _probability(probabilities[key]) for key in options}
            if (not math.isclose(math.fsum(probabilities.values()), 1.0, rel_tol=0,
                                 abs_tol=PROBABILITY_SUM_TOLERANCE)
                    or probabilities[choice] != max(probabilities.values())):
                raise _response_error()
            validated[question_id] = {
                "type": "choice", "choice": choice, "probabilities": probabilities,
                "confidence": _probability(answer.get("confidence")),
            }
        else:
            validated[question_id] = {"type": "noul", "noul": _probability(answer.get("noul"))}
    provider_request_id = envelope.get("id")
    if provider_request_id is not None and (
            type(provider_request_id) is not str
            or re.fullmatch(r"gen-[A-Za-z0-9_-]{1,128}", provider_request_id) is None
            or (api_key is not None and api_key in provider_request_id)):
        raise _response_error()
    usage = envelope.get("usage")
    if type(usage) is not dict:
        raise _response_error()
    safe_usage = {}
    for key in ("input_tokens", "output_tokens", "cost"):
        if key not in usage:
            continue
        value = usage[key]
        try:
            valid = (type(value) in (int, float) and math.isfinite(value) and value >= 0
                     and (key == "cost" or type(value) is int))
        except OverflowError:
            valid = False
        if not valid:
            raise _response_error()
        safe_usage[key] = value
    return {"answers": validated, "model": RESOLVED_MODEL, "provider": PROVIDER,
            "provider_request_id": provider_request_id, "usage": safe_usage}


def validate_decision(result: dict, questions: dict) -> dict:
    """Revalidate and copy normalized output before a local registry accepts it.

    This boundary needs no credential and performs no I/O. Unknown diagnostic
    fields are discarded. Revalidation proves shape and allowed IDs, not that a
    caller has preserved the original model judgment or its capture freshness.
    """
    try:
        snapshot = deepcopy(result)
        question_snapshot = deepcopy(questions)
        _validate_questions(question_snapshot)
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise _response_error() from None
    required = {"answers", "model", "provider", "provider_request_id", "usage", "latency_ms"}
    if type(snapshot) is not dict or not required <= set(snapshot) or "error" in snapshot:
        raise _response_error()
    latency_ms = snapshot["latency_ms"]
    try:
        valid_latency = (type(latency_ms) in (int, float)
                         and math.isfinite(latency_ms) and latency_ms >= 0)
    except OverflowError:
        valid_latency = False
    if not valid_latency:
        raise _response_error()
    validated = _validate_response({
        "answers": snapshot["answers"], "model": snapshot["model"],
        "provider": snapshot["provider"], "id": snapshot["provider_request_id"],
        "usage": snapshot["usage"],
    }, question_snapshot)
    validated["latency_ms"] = latency_ms
    return validated


def decide(payload: dict, api_key: str) -> dict:
    """Return validated decisions, never actions or raw provider diagnostics.

    The caller supplies the key directly from its credential lookup. This adapter
    does not read environment variables, retrieve secrets, retry, or follow
    redirects. Probability sums allow an absolute rounding tolerance of 1e-6.
    """
    if (type(api_key) is not str or not 1 <= len(api_key) <= 4096
            or any(not 33 <= ord(character) <= 126 for character in api_key)):
        raise DecisionError("authentication", "A valid in-memory OpenRouter credential is required.")
    try:
        snapshot = deepcopy(payload)
        _validate_request(snapshot)
        body = json.dumps(snapshot, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise _request_error() from None
    started = time.perf_counter()
    try:
        request = Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
        })
        with build_opener(_NoRedirects()).open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise _http_error(response.status)
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        status = error.code
        error.close()
        raise _http_error(status) from None
    except (URLError, OSError, HTTPException, ValueError):
        raise DecisionError("transport", "JEV transport failed or timed out.") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _response_error()
    try:
        envelope = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise _response_error() from None
    result = _validate_response(envelope, snapshot["questions"], api_key)
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result
