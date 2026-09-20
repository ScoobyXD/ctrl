"""Print the exact outgoing text-only request; --live runs one decision, never input."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctrl_loop.credentials import CredentialError, get_openrouter_key
from ctrl_loop.decisions import PreviewRegistry, RegistryError
from ctrl_loop.jev import DecisionError, ENDPOINT, decide


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=ROOT / "examples/jev-fixture.json")
    parser.add_argument("--live", action="store_true", help="Load the existing AWS secret and make one JEV request")
    parser.add_argument("--output", type=Path, help="Optional preview/result JSON; contains supplied screen text")
    args = parser.parse_args()
    try:
        fixture = json.loads(args.fixture.read_text(encoding="utf-8-sig"))
        registry = PreviewRegistry(fixture)
        pending = registry.begin()
        preview = {"endpoint": ENDPOINT, "request_id": pending.request_id, "capture_id": pending.capture_id,
                   "payload": pending.payload(), "input_actions": 0}
        print(json.dumps({"outgoing_preview": preview}, indent=2, allow_nan=False), flush=True)
        if args.live:
            key = get_openrouter_key()
            try:
                decision = decide(pending.payload(), key,
                                  request_id=pending.request_id, capture_id=pending.capture_id)
            finally:
                del key
            result = registry.accept(pending, decision)
            result["verified_at_utc"] = datetime.now(timezone.utc).isoformat()
            result["requested_model"] = pending.payload()["model"]
            result["requested_provider"] = pending.payload()["provider"]
            print(json.dumps(result, indent=2, allow_nan=False))
        else:
            result = {"status": "offline_preview", **preview}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return 0
    except (CredentialError, DecisionError, RegistryError) as error:
        print(json.dumps({"status": "error", "message": str(error), "input_actions": 0}), file=sys.stderr)
    except Exception:
        # Never render raw exceptions: third-party transport/SDK errors may carry secrets.
        print(json.dumps({"status": "error", "message": "Decision preview failed; check setup and fixture format.",
                          "input_actions": 0}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
