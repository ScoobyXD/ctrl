"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import time
from pathlib import Path

from .browser import ActionPaused, Browser, StalePage, fingerprint
from .model import action_space, choose, decision_space, supplied_values
from .navigation import navigation_urls
from .questions import MAX_STEPS
from .trace import TraceStore

STOP_REASONS = {
    "unavailable_control": "Jev could not find a supported control to continue from this page.",
    "sign_in_required": "Sign in to this site in Chrome, then start the task again.",
    "unsupported_content": "The required content is not accessible to the current page reader.",
    "cannot_progress": "Jev found no supported action that advances the remaining task.",
    "missing_text": "Add the exact text the task needs under Text to enter, then resume.",
    "missing_address": "Add the site's full web address under Text to enter, then resume.",
    "ambiguous_goal": "Specify the remaining action more precisely, then start the updated task.",
}


NAVIGATION_KINDS = {"switch_tab", "open_url", "back"}


def navigation_cycle(history, proposed):
    """Stop repeated navigation before input, independently of model confidence."""
    if proposed.get("kind") not in NAVIGATION_KINDS:
        return False
    destinations = []
    for entry in reversed(history):
        if entry.get("kind") == "wait":
            continue
        if entry.get("kind") not in NAVIGATION_KINDS:
            break
        destinations.append(entry.get("navigation_destination", entry.get("url", "")))
        if len(destinations) == 7:
            break
    destinations.reverse()
    destinations.append(proposed.get("url", ""))
    # AA, ABAB, ABCABC, or ABCDABCD. Ordinary work between visits resets this guard.
    return any(
        len(destinations) >= size * 2
        and all(destinations[-size:])
        and destinations[-size:] == destinations[-2 * size:-size]
        for size in range(1, 5)
    )


def content_fingerprint(page):
    """Unrelated tab titles and browser metadata never count as page progress."""
    return fingerprint({
        "url": page["url"], "text": page.get("text", ""), "scroll": page.get("scroll", {}),
        "actions": [action for action in page["actions"] if action["kind"] not in NAVIGATION_KINDS],
    })


class Agent:
    def __init__(
        self, url, goals, *, target_id=None, values=None, max_steps=MAX_STEPS, cancel_event=None,
        record_dir=None, screenshots=False
    ):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        if type(max_steps) is not int or not 1 <= max_steps <= 500:
            raise ValueError("Set an action limit from 1 to 500.")
        plan = [task]
        self.values = supplied_values(task, values)
        self.cancel_event = cancel_event
        self._traces = TraceStore()
        self._trace_id = None
        self.browser = Browser(url, target_id=target_id, navigation_urls=navigation_urls(task, self.values))
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
            stop_reason=None,
            last_decision=None,
            max_steps=max_steps,
            plan=plan,
            plan_index=0,
            decisions=[],
            values=list(self.values),
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir and page.get("screenshot"):
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
            **self.trace_state(),
        }

    def trace_store(self):
        if not hasattr(self, "_traces"):
            self._traces = TraceStore()
            self._trace_id = None
        return self._traces

    def trace_state(self):
        summaries = self.trace_store().summaries()
        return {"trace_summaries": summaries, "latest_trace_id": summaries[-1]["id"] if summaries else None}

    def finish_trace(self, execution, message, validation=None):
        self.trace_store().finish(self._trace_id, execution, message, validation)

    def cancel_run(self):
        self.state["decision"] = None
        self.state["status"] = "cancelled"
        self.state["stop_reason"] = "Task cancelled. Start a new task to run again."
        self.finish_trace("cancelled", "Task cancelled; this preview will not execute")
        self._elapsed()

    def _pause_requested(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.finish_trace("paused", "Paused before another browser action")
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
            self.finish_trace("paused", "Paused before browser input")
            self.state["decision"] = None
            self.state["status"] = "paused"
            self._elapsed()
            return self.snapshot()
        except StalePage:
            self.finish_trace("rejected", "Page changed; decision discarded without replay", "stale")
            raise
        except Exception as error:
            if hasattr(error, "audit"):
                self.finish_trace("rejected", "Invalid or unavailable Jev response; no action executed", "rejected")
            else:
                self.finish_trace("error", "Decision or action failed; no automatic retry", "error")
            self.state["decision"] = None
            if self.state["status"] not in {"done", "blocked", "needs_input", "paused", "cancelled"}:
                self.state["status"] = "error"
            self.state["error"] = str(error) if isinstance(error, (ValueError, RuntimeError, TimeoutError)) else (
                "Operation failed; no browser action was automatically retried."
            )
            self._elapsed()
            raise

    def _command(self, name, body=None):
        body = body or {}
        state = self.state
        if state["status"] == "cancelled":
            raise ValueError("This task was cancelled. Start a new task to run again.")
        if name == "tick":
            try:
                self.command("predict", {})
                if state["status"] in {"done", "blocked", "needs_input", "paused", "error"}:
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
            self.finish_trace("cancelled", "Preview replaced by a new decision")
            state["decision"] = None
            if state["page"].get("scope_blocked"):
                state["status"] = "blocked"
                state["stop_reason"] = str(state["page"]["scope_blocked"])
                self._elapsed()
                return self.snapshot()
            if len(state["decisions"]) >= state.get("max_steps", MAX_STEPS) * 2:
                state["status"] = "blocked"
                state["stop_reason"] = "Reached the model-decision limit before completing the task."
                self._elapsed()
                return self.snapshot()
            if self._pause_requested():
                return self.snapshot()
            self._trace_id = self.trace_store().begin(state["page"], action_space(state["page"]["actions"])[0])
            try:
                selected_decision = choose(state["page"], state["goal"], state["history"], self.values)
            except Exception as error:
                self.trace_store().audit(self._trace_id, getattr(error, "audit", {}))
                raise
            self.trace_store().audit(self._trace_id, selected_decision.get("audit", {}))
            state["decision"] = {key: value for key, value in selected_decision.items() if key != "audit"}
            state["decision"]["fingerprint"] = state["page"]["fingerprint"]
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
            state["last_decision"] = decision
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED", "NEEDS_INPUT"}:
                if decision["operation"] != selected:
                    raise ValueError("Decision operation does not match its selected action.")
                if selected == "DONE" and page.get("bootstrap"):
                    raise ValueError("Cannot complete the task before observing a requested destination.")
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["status"] = {"DONE": "done", "BLOCKED": "blocked", "NEEDS_INPUT": "needs_input"}[selected]
                reason = decision.get("stop_reason")
                state["stop_reason"] = (
                    "Jev marked the task complete. Check the final page and action history."
                    if selected == "DONE" else STOP_REASONS.get(reason, STOP_REASONS[
                        "missing_text" if selected == "NEEDS_INPUT" else "cannot_progress"
                    ])
                )
                if selected == "NEEDS_INPUT":
                    state["input_request"] = state["stop_reason"]
                state["plan_index"] = int(selected == "DONE")
                self.finish_trace("terminal", state["stop_reason"], "passed")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            _, targets, controls = decision_space(page["actions"], self.values)
            operation = decision["operation"]
            candidate = targets.get(operation, {}).get(decision.get("target")) or controls.get(operation)
            if candidate is None or candidate["id"] != selected:
                raise ValueError("Decision does not match an offered operation and target.")
            if len(state["history"]) >= state.get("max_steps", MAX_STEPS):
                state["status"] = "blocked"
                state["stop_reason"] = (
                    f"Reached the {state.get('max_steps', MAX_STEPS)}-action limit before completing the task."
                )
                self.finish_trace("rejected", state["stop_reason"], "rejected")
                self._elapsed()
                return self.snapshot()
            if navigation_cycle(state["history"], action):
                state["status"] = "blocked"
                state["stop_reason"] = (
                    "Stopped a repeated navigation cycle before changing another tab. "
                    "No further browser actions will run. Revise the task before restarting."
                )
                self.finish_trace("rejected", state["stop_reason"], "rejected")
                self._elapsed()
                return self.snapshot()
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
            self.finish_trace("executed", "Selected action passed browser checks and was dispatched", "passed")
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "navigation_destination": action.get("url") if action["kind"] in NAVIGATION_KINDS else None,
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
                    "title": page.get("title", ""),
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"][-1].update(
                page_changed=content_fingerprint(state["page"]) != content_fingerprint(page),
                url=state["page"]["url"],
                title=state["page"].get("title", ""),
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"] and state["page"].get("screenshot"):
                self.record_dir.mkdir(parents=True, exist_ok=True)
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            state["status"] = (
                "blocked"
                if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
                else "ready"
            )
            if state["status"] == "blocked":
                state["stop_reason"] = (
                    "Three consecutive actions produced no visible change. Inspect the page before restarting."
                )
            self._pause_requested()
        elif name == "supply_values":
            if state["status"] in {"done", "blocked", "error"}:
                raise ValueError("Start a fresh run to supply values after a completed run.")
            additions = body.get("values", [])
            if not isinstance(additions, (list, tuple)):
                raise ValueError("Supplied values must be a list of strings.")
            self.values = supplied_values(state["goal"], [*self.values, *additions])
            self.browser.set_navigation_urls(navigation_urls(state["goal"], self.values))
            state["values"] = list(self.values)
            state["decision"] = None
            self.finish_trace("cancelled", "Supplied values changed; preview discarded")
            state.pop("input_request", None)
            state["stop_reason"] = None
            state["last_decision"] = None
            state["status"] = "ready"
            self._pause_requested()
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked", "needs_input", "error", "paused", "cancelled"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
