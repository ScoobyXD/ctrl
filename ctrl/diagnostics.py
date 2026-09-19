"""Small live compatibility check. No key or provider response is printed."""

import time

import httpx

from .credentials import get_openrouter_key

MODEL = "typesafe/jev-1.13"
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"


def check_openrouter():
    key = get_openrouter_key()
    started = time.perf_counter()
    try:
        with httpx.Client(http2=True, timeout=30) as client:
            result = client.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": MODEL,
                    "state": {"text": "The local browser agent is named [CTRL]."},
                    "questions": {
                        "name": {
                            "type": "choice",
                            "instructions": "Select the exact application name given in the text.",
                            "criteria": {"ctrl": "[CTRL]", "other": "A different name"},
                        }
                    },
                },
            )
            if result.is_error:
                raise RuntimeError(f"OpenRouter returned HTTP {result.status_code}. Check key access and credits.")
            body = result.json()
        from jev_ultrafast.model import validate_choice

        answer = validate_choice(body.get("answers", {}).get("name", {}), {"ctrl", "other"})
        if answer["choice"] != "ctrl" or not body.get("model", "").startswith("typesafe/jev-"):
            raise RuntimeError("OpenRouter's Jev compatibility check returned an unexpected answer.")
        return {
            "status": "ready",
            "message": "Jev connected through OpenRouter. Credential loaded from AWS into memory.",
            "model": body["model"],
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
    except httpx.HTTPError:
        raise RuntimeError("Could not reach OpenRouter. Check this computer's network connection.") from None


if __name__ == "__main__":
    import json

    try:
        print(json.dumps(check_openrouter()))
    except Exception as error:
        print(json.dumps({"status": "error", "message": str(error)}))
        raise SystemExit(1) from None
