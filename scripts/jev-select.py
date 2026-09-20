"""Preview a bounded Jev selection search; --live sends text, never computer input."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctrl_loop.credentials import CredentialError, get_openrouter_key
from ctrl_loop.jev import DecisionError, decide
from ctrl_loop.selection import SelectionConfig, SelectionContextError, SelectionError, SelectionSearch

MAX_INPUT_BYTES = 8 * 1024 * 1024


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON key")
        result[name] = value
    return result


def _parse(raw):
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("Input too large")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def _emit(event, *, data=None, message=None, category=None, http_status=None):
    record = {"event": event}
    if data is not None:
        record["data"] = data
    if message is not None:
        record["message"] = message
        record["input_actions"] = 0
    if category is not None:
        record["category"] = category
    if http_status is not None:
        record["http_status"] = http_status
    print(json.dumps(record, ensure_ascii=True, allow_nan=False), flush=True)


def _decision_diagnostic(error):
    """Describe the failed boundary without rendering exception/provider text."""
    messages = {
        "authentication": "OpenRouter rejected the Jev credential or provider access. Check the configured API key and model access.",
        "credits": "OpenRouter has insufficient credits for this Jev request. Check the account and API key spending limits.",
        "rate_limit": "OpenRouter or TypeSafe rate limited the Jev request. Wait briefly before trying again.",
        "request": "The Jev request failed local validation before it could be sent. Check the selection request and pinned model settings.",
        "response": "The Jev response failed validation. Its model, answer IDs, or probability data did not match the expected contract.",
        "transport": "The Jev connection failed or timed out. Check the network connection and try again.",
        "context_limit": "The Jev request exceeded the model's context limit. The control descriptions need to be split into smaller requests.",
        "provider": "OpenRouter or TypeSafe could not complete the Jev request. Try again or check provider availability.",
    }
    category = getattr(error, "category", None)
    if type(category) is not str or category not in messages:
        category = "provider"
    status = getattr(error, "status_code", None)
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    message = messages[category]
    if category == "context_limit" and status in (400, 413):
        message = f"The Jev request exceeded the model's context limit (HTTP {status}). The control descriptions need to be split into smaller requests."
    elif status in (401, 403):
        category = "authentication"
        message = f"OpenRouter rejected the Jev credential or provider access (HTTP {status}). Check the configured API key and model access."
    elif status == 402:
        category = "credits"
        message = "OpenRouter has insufficient credits for this Jev request (HTTP 402). Check the account and API key spending limits."
    elif status == 429:
        category = "rate_limit"
        message = "OpenRouter or TypeSafe rate limited the Jev request (HTTP 429). Wait briefly before trying again."
    elif status == 400:
        category = "provider"
        message = "OpenRouter rejected the Jev request format or model settings (HTTP 400). The request needs to be corrected before retrying."
    elif status == 413:
        category = "provider"
        message = "OpenRouter rejected the Jev request because it was too large (HTTP 413). Reduce the amount of control context per request."
    elif status is not None and status >= 500:
        category = "provider"
        message = f"OpenRouter or TypeSafe could not complete the Jev request (HTTP {status}). Try again or check provider availability."
    elif status is not None:
        message = f"{message} HTTP status: {status}."
    return {"message": message, "category": category, "http_status": status}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path, help="Exported UIA snapshot JSON")
    source.add_argument("--stdin", action="store_true", help="Read {snapshot, goal} JSON from stdin")
    parser.add_argument("--goal", help="Task to advance; required with --snapshot")
    parser.add_argument("--live", action="store_true", help="Make bounded remote Jev requests; never execute input")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--keep-per-group", type=int, default=4)
    parser.add_argument("--parallelism", type=int, default=4)
    parser.add_argument("--max-requests", type=int, default=64)
    parser.add_argument("--max-rounds", type=int, default=4)
    parser.add_argument("--output", type=Path, help="Explicit local export of plan or final result")
    args = parser.parse_args()
    try:
        client_run_id = None
        if args.stdin:
            data = _parse(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
            if not isinstance(data, dict) or set(data) - {"snapshot", "goal", "run_id"}:
                raise ValueError("Invalid input")
            snapshot, goal = data["snapshot"], data["goal"]
            client_run_id = data.get("run_id")
            if client_run_id is not None and (
                not isinstance(client_run_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,80}", client_run_id) is None
            ):
                raise ValueError("Invalid run ID")
        else:
            with args.snapshot.open("rb") as stream:
                snapshot = _parse(stream.read(MAX_INPUT_BYTES + 1))
            goal = args.goal
        config = SelectionConfig(batch_size=args.batch_size, keep_per_group=args.keep_per_group,
                                 parallelism=args.parallelism, max_requests=args.max_requests,
                                 max_rounds=args.max_rounds)
        search = SelectionSearch(snapshot, goal, config=config)

        def progress(data):
            _emit("progress", data={**data, "client_run_id": client_run_id})

        if args.live:
            # Key lookup happens once, only after local validation. Neither the
            # search nor subprocess messages carry the credential.
            _emit("progress", data={"stage": "credentials", "client_run_id": client_run_id,
                                    "status": "Reading the OpenRouter key from AWS Secrets Manager."})
            key = get_openrouter_key()
            try:
                _emit("progress", data={"stage": "credentials", "client_run_id": client_run_id,
                                        "status": "AWS secret loaded. Starting bounded Jev requests."})
                def remote(payload, *, request_id, capture_id):
                    return decide(payload, key, request_id=request_id, capture_id=capture_id)

                result = search.run(remote, on_progress=progress)
            finally:
                del key
        else:
            result = search.preview()
        result["client_run_id"] = client_run_id
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False,
                                              allow_nan=False) + "\n", encoding="utf-8")
        _emit("result", data=result)
        return 0
    except CredentialError as error:
        _emit("error", message=str(error))
    except DecisionError as error:
        _emit("error", **_decision_diagnostic(error))
    except SelectionContextError:
        _emit("error", category="context_limit", message="The goal and control context are too large for one Jev request. Shorten the goal or observe a smaller target.")
    except SelectionError:
        _emit("error", message="Invalid selection snapshot, goal, or search limits. Observe again and check the settings.")
    except Exception:
        # Raw SDK, provider, OS and parsing exceptions may contain screen text
        # or authentication material. They never cross the helper boundary.
        _emit("error", message="Selection preview failed. Check the snapshot and local Jev setup.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
