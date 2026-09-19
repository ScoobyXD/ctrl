"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import time
from pathlib import Path

from .browser import ActionPaused, Browser, StalePage
from .model import action_space, choose, decision_space, supplied_values
from .questions import MAX_STEPS


class Agent:
    def __init__(
        self, url, goals, *, target_id=None, values=None, cancel_event=None, record_dir=None, screenshots=False
    ):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.values = supplied_values(task, values)
        self.cancel_event = cancel_event
        self.browser = Browser(url, target_id=target_id)
        self.browser.cancel_event = cancel_event
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            decisions=[],
            values=list(self.values),
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def _pause_requested(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.state["decision"] = None
            self.state["status"] = "paused"
            self._elapsed()
            return True
        return False

    def _elapsed(self):
        if self.state["started_at"] is not None:
            self.state["elapsed_ms"] = round((time.perf_counter() - self.state["started_at"]) * 1000)

    def command(self, name, body=None):
        try:
            return self._command(name, body)
        except ActionPaused:
            self.state["decision"] = None
            self.state["status"] = "paused"
            self._elapsed()
            return self.snapshot()
        except StalePage:
            raise
        except Exception as error:
            self.state["decision"] = None
            if self.state["status"] not in {"done", "blocked", "needs_input", "paused"}:
                self.state["status"] = "error"
            self.state["error"] = str(error)
            self._elapsed()
            raise

    def _command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                if state["status"] == "paused":
                    return self.snapshot()
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a run first")
            if state["status"] in {"done", "blocked", "needs_input", "error"}:
                raise ValueError("This run has stopped. Supply missing values or start a fresh run.")
            if self._pause_requested():
                return self.snapshot()
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if len(state["decisions"]) >= MAX_STEPS * 2:
                state["status"] = "blocked"
                raise ValueError("Reached the run's model-call budget")
            state["decision"] = choose(state["page"], state["goal"], state["history"], self.values)
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
            self._pause_requested()
        elif name == "act":
            if self._pause_requested():
                return self.snapshot()
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED", "NEEDS_INPUT"}:
                if decision["operation"] != selected:
                    raise ValueError("Decision operation does not match its selected action.")
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["status"] = {"DONE": "done", "BLOCKED": "blocked", "NEEDS_INPUT": "needs_input"}[selected]
                if selected == "NEEDS_INPUT":
                    state["input_request"] = "Add the exact text required by the task to Supplied strings."
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            _, targets, controls = decision_space(page["actions"], self.values)
            operation = decision["operation"]
            candidate = targets.get(operation, {}).get(decision.get("target")) or controls.get(operation)
            if candidate is None or candidate["id"] != selected:
                raise ValueError("Decision does not match an offered operation and target.")
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action run budget")
            text = None
            if action["kind"] == "fill":
                if candidate.get("payload_id") != decision.get("payload_id"):
                    raise ValueError("Decision does not match an offered supplied string.")
                text = candidate["text"]
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before typing. Choose again.")
            # Recheck cancellation after all preparation and before the browser can mutate.
            if self._pause_requested():
                return self.snapshot()
            # Browser.act checks freshness and target geometry immediately before input.
            state["browser"].act(action, page, text=text)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision.get("target_probabilities", {}).get(
                        decision.get("target"), decision["probabilities"][selected]
                    ),
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "payload_id": decision.get("payload_id"),
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            state["status"] = (
                "blocked"
                if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
                else "ready"
            )
            self._pause_requested()
        elif name == "supply_values":
            if state["status"] in {"done", "blocked", "error"}:
                raise ValueError("Start a fresh run to supply values after a completed run.")
            additions = body.get("values", [])
            if not isinstance(additions, (list, tuple)):
                raise ValueError("Supplied values must be a list of strings.")
            self.values = supplied_values(state["goal"], [*self.values, *additions])
            state["values"] = list(self.values)
            state["decision"] = None
            state.pop("input_request", None)
            state["status"] = "ready"
            self._pause_requested()
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked", "needs_input", "error", "paused"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
