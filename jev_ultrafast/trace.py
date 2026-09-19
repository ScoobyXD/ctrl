"""Thread-safe, in-memory evidence for the current task's finite decisions."""

from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4


class TraceStore:
    def __init__(self):
        self.lock = RLock()
        self.items = {}

    def begin(self, page, elements):
        with self.lock:
            identifier = uuid4().hex
            self.items[identifier] = {
                "id": identifier, "sequence": len(self.items) + 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "observation": deepcopy({
                    **{key: page[key] for key in (
                        "url", "title", "text", "actions", "fingerprint", "task_browser_scope",
                        "bootstrap", "scope_blocked", "scroll",
                    ) if key in page},
                    "elements": elements,
                }),
                "questions": [], "selected": {}, "model": None,
                "latency_ms": None, "request_bytes": None,
                "validation": {"status": "pending", "message": "Waiting for Jev's decision"},
                "execution": {"status": "pending", "message": "No action executed"},
            }
            return identifier

    def audit(self, identifier, audit):
        with self.lock:
            if identifier not in self.items:
                return
            item = self.items[identifier]
            for key in ("questions", "selected", "model", "latency_ms", "request_bytes"):
                if key in audit:
                    item[key] = deepcopy(audit[key])
            item["validation"]["message"] = "Browser validation pending"
            item["execution"]["message"] = "Decision preview; no action executed"

    def finish(self, identifier, execution, message, validation=None):
        with self.lock:
            item = self.items.get(identifier)
            # An observation failure or later cancellation cannot erase an executed action.
            if item is None or item["execution"]["status"] not in {"pending", "paused"}:
                return
            item["execution"] = {"status": execution, "message": message}
            if validation:
                item["validation"] = {"status": validation, "message": message}
                allowed = True if validation == "passed" else False if validation in {"rejected", "stale"} else None
                for question in item["questions"]:
                    for option in question.get("options", []):
                        if option.get("used"):
                            option["allowed"] = allowed
                            option["validation_note"] = message

    def get(self, identifier):
        with self.lock:
            return deepcopy(self.items.get(identifier))

    def summaries(self):
        with self.lock:
            return [{
                "id": item["id"], "sequence": item["sequence"], "timestamp": item["timestamp"],
                "status": item["execution"]["status"],
                "operation": item["selected"].get("operation"),
                "target_label": item["selected"].get("target_label"), "latency_ms": item["latency_ms"],
            } for item in self.items.values()]
