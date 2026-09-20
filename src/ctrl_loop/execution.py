"""Bounded, explicitly started observe/select/revalidate/deliver/observe loop.

Selections are recommendations. A driver receipt establishes only delivery, and
a fresh UIA observation establishes only the state it exposes, never goal success.
This module imports neither a credential provider nor a native driver at import.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import time

from .uia_contract import build_options


class ExecutionError(ValueError):
    """Static, safe diagnostic from a local execution boundary."""


class StaleTarget(ExecutionError):
    pass


class NeedsArguments(ExecutionError):
    pass


def validate_request(request):
    if type(request) is not dict or set(request) - {
        "target", "goal", "run_id", "max_steps", "max_seconds", "max_elements", "arguments"
    }:
        raise ExecutionError("Invalid execution request.")
    target = request.get("target")
    if (type(target) is not dict or any(type(target.get(k)) is not int or target[k] <= 0
                                       for k in ("hwnd", "pid"))):
        raise ExecutionError("Select a current window before starting execution.")
    goal = request.get("goal")
    if type(goal) is not str or not goal.strip() or len(goal) > 4000:
        raise ExecutionError("Supply a goal of 1–4000 characters.")
    if type(request.get("run_id")) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request["run_id"]):
        raise ExecutionError("Invalid execution run identity.")
    result = deepcopy(request)
    for key, default, low, high in (("max_steps", 5, 1, 20), ("max_seconds", 180, 5, 1200),
                                    ("max_elements", 1000, 1, 5000)):
        value = result.setdefault(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ExecutionError("Execution limits are outside the supported bounds.")
    arguments = result.setdefault("arguments", {})
    if type(arguments) is not dict or set(arguments) - {"bindings"}:
        raise ExecutionError("Arguments must contain a bindings list.")
    bindings = arguments.get("bindings", [])
    if type(bindings) is not list or len(bindings) > 50:
        raise ExecutionError("Provide at most 50 explicit argument bindings.")
    for binding in bindings:
        if (type(binding) is not dict or set(binding) - {
            "operation", "name", "automation_id", "role", "value", "direction"
        } or binding.get("operation") not in {
            "set_value", "set_range_value", "scroll_vertical", "scroll_horizontal"
        } or not any(type(binding.get(k)) is str and binding[k] for k in ("name", "automation_id"))
                or any(k in binding and (type(binding[k]) is not str or not binding[k]
                                         or len(binding[k]) > 512)
                       for k in ("name", "automation_id", "role"))):
            raise ExecutionError("Each argument binding needs an operation and a control name or AutomationId.")
        operation = binding["operation"]
        if operation == "set_value":
            if type(binding.get("value")) is not str or len(binding["value"]) > 4000 or "direction" in binding:
                raise ExecutionError("Text bindings require an explicit value of at most 4000 characters.")
        elif operation == "set_range_value":
            value = binding.get("value")
            if type(value) not in (int, float) or not math.isfinite(value) or "direction" in binding:
                raise ExecutionError("Range bindings require a finite number.")
        elif (binding.get("direction") not in (
                ("up", "down") if operation == "scroll_vertical" else ("left", "right"))
              or "value" in binding):
            raise ExecutionError("Scroll bindings require an explicit direction on the selected axis.")
    return result


def _runtime(element):
    value = element.get("runtime_id")
    if type(value) is not list or not value or any(type(i) is not int for i in value):
        raise StaleTarget("The control has no usable UIA runtime identity. Observe another target.")
    return tuple(value)


def _identity(element):
    return tuple(element.get(k) for k in ("name", "role", "automation_id", "class_name", "framework_id"))


def _ancestors(snapshot, element):
    elements = {e["id"]: e for e in snapshot["elements"]}
    result, visited = [], set()
    parent = element.get("parent_id")
    while parent is not None:
        if parent in visited or parent not in elements or len(result) >= 64:
            raise StaleTarget("The control ancestry cannot be validated.")
        visited.add(parent)
        item = elements[parent]
        result.append((_runtime(item), _identity(item)))
        parent = item.get("parent_id")
    return result


def validate_observation(snapshot, target):
    if (type(snapshot) is not dict or snapshot.get("schema_version") != 1
            or snapshot.get("status") not in ("complete", "partial", "truncated")
            or type(snapshot.get("snapshot_id")) is not str
            or type(snapshot.get("elements")) is not list
            or type(snapshot.get("options")) is not list
            or any(snapshot.get("target", {}).get(k) != target[k] for k in ("hwnd", "pid"))):
        raise StaleTarget("The selected window could not be observed with its original PID and HWND.")
    return snapshot


def revalidate_selection(previous, current, selected):
    """Match an actual selected candidate across fresh snapshots, never by index.

    Native runtime identity, metadata, ancestry, supported operation and decision
    state must agree. Geometry may move: the executor receives current geometry.
    """
    validate_observation(current, previous["target"])
    if current["snapshot_id"] == previous["snapshot_id"]:
        raise StaleTarget("Execution requires a new observation after selection.")
    if selected not in previous["options"]:
        raise StaleTarget("The selection is not an option from this observation.")
    old = next((e for e in previous["elements"] if e["id"] == selected["element_id"]), None)
    if old is None:
        raise StaleTarget("The selected control is unavailable.")
    runtime = _runtime(old)
    candidates = [e for e in current["elements"] if e.get("runtime_id") == list(runtime)]
    if len(candidates) != 1:
        raise StaleTarget("The selected control disappeared or its runtime identity became ambiguous.")
    fresh = candidates[0]
    if _identity(old) != _identity(fresh) or _ancestors(previous, old) != _ancestors(current, fresh):
        raise StaleTarget("The selected control identity or ancestry changed during selection.")
    if (old.get("state") != fresh.get("state") or old.get("value") != fresh.get("value")
            or old.get("is_password") != fresh.get("is_password")):
        raise StaleTarget("The selected control state changed during selection. Start again from fresh evidence.")
    supported = [o for o in build_options([fresh]) if o["operation"] == selected["operation"]]
    if len(supported) != 1:
        raise StaleTarget("The selected operation is no longer enabled and available onscreen.")
    return fresh


def bind_arguments(operation, element, arguments, snapshot=None):
    if operation not in ("set_value", "set_range_value", "scroll_vertical", "scroll_horizontal"):
        return {}
    matching = [b for b in arguments.get("bindings", []) if b["operation"] == operation
                and all(element.get(k) == b[k] for k in ("name", "automation_id", "role") if k in b)]
    if len(matching) != 1:
        raise NeedsArguments("The selected operation needs exactly one explicit argument binding for this control.")
    binding = matching[0]
    if snapshot is not None:
        targets = [e for e in snapshot["elements"]
                   if all(e.get(k) == binding[k] for k in ("name", "automation_id", "role") if k in binding)]
        if len(targets) != 1:
            raise NeedsArguments("The argument binding matches multiple controls. Add a unique AutomationId, name or role.")
    if operation == "set_range_value":
        state, value = element.get("state", {}), binding["value"]
        low, high = state.get("range_minimum"), state.get("range_maximum")
        if (type(low) not in (int, float) or type(high) not in (int, float)
                or not math.isfinite(low) or not math.isfinite(high) or not low <= value <= high):
            raise NeedsArguments("The explicit number is outside the current provider range, or the range is unavailable.")
    return {k: binding[k] for k in ("value", "direction") if k in binding}


def observation_fingerprint(snapshot):
    """Visible semantic state; ignore observation IDs, timing, focus and movement."""
    # Some unrelated system menu providers generate a new RuntimeId per reader.
    # That changes identity bookkeeping, not the visible result of an action.
    data = [{k: e.get(k) for k in ("name", "role", "automation_id", "enabled",
                                   "offscreen", "patterns", "state", "value", "is_password")}
            for e in snapshot["elements"]]
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


class ExecutionLoop:
    def __init__(self, observer, selector, executor, *, emit=None, cancelled=None, clock=time.monotonic):
        self.observer, self.selector, self.executor = observer, selector, executor
        self.emit = emit or (lambda event, data: None)
        self.cancelled = cancelled or (lambda: False)
        self.clock = clock
        self.deadline = float("inf")
        self.actions = []
        self.attempted_actions = 0
        self.input_actions = 0

    def should_stop(self):
        return self.cancelled() or self.clock() >= self.deadline

    def _check(self):
        if self.should_stop():
            raise InterruptedError

    def run(self, request):
        request = validate_request(request)
        self.deadline = self.clock() + request["max_seconds"]
        target, goal = request["target"], request["goal"]
        history, seen = [], set()
        step = 0
        self.actions, self.attempted_actions, self.input_actions = [], 0, 0

        def finish(status, reason):
            result = dict(status=status, reason=reason, run_id=request["run_id"],
                          input_actions=self.input_actions, attempted_actions=self.attempted_actions,
                          steps=len(self.actions), actions=deepcopy(self.actions), goal_completed=False)
            self.emit("result", result)
            return result

        def observe(phase):
            self._check()
            snapshot = validate_observation(self.observer(target), target)
            self._check()
            self.emit("observation", {"snapshot": snapshot, "step": step, "phase": phase})
            return snapshot

        try:
            previous = observe("initial")
            for step in range(1, request["max_steps"] + 1):
                self._check()
                self.emit("progress", {"stage": "select", "step": step, "status": "Asking Jev to select the next supported action."})
                result = self.selector(deepcopy(previous), goal, deepcopy(history))
                self._check()
                if (type(result) is not dict or result.get("snapshot_id") != previous["snapshot_id"]
                        or result.get("goal") != goal or result.get("input_actions") != 0):
                    raise ExecutionError("Selection result is not bound to the current observation and goal.")
                self.emit("selection", {"selection": result, "step": step})
                if result.get("status") != "selected":
                    return finish("no_match" if result.get("status") == "no_match" else "stopped",
                                  "Selection stopped without a next action; goal completion is unverified.")
                selected = result.get("selected_option")
                if type(selected) is not dict:
                    raise ExecutionError("Selection returned no valid option.")
                current = observe("validation")
                fresh = revalidate_selection(previous, current, selected)
                arguments = bind_arguments(selected["operation"], fresh, request["arguments"], current)
                fingerprint = observation_fingerprint(current)
                key = (_runtime(fresh), selected["operation"], fingerprint)
                if key in seen:
                    return finish("no_progress", "The same action on the same observed state would repeat. Execution stopped.")
                seen.add(key)
                self._check()
                self.emit("progress", {"stage": "execute", "step": step, "status": "Validating the native driver target and delivering input."})
                # Count dispatches separately: a timeout after dispatch is not a
                # reliable failure, and no such action is ever automatically retried.
                self.attempted_actions += 1
                try:
                    receipt = self.executor.execute(target, fresh, selected["operation"],
                                                    arguments, cancelled=self.should_stop)
                except Exception:
                    receipt = {"status": "unknown", "attempted": True,
                               "error": "The executor stopped without a delivery receipt. Do not retry automatically."}
                if type(receipt) is not dict or receipt.get("status") not in {"delivered", "failed", "unknown", "cancelled"}:
                    receipt = {"status": "unknown", "attempted": True, "error": "The executor returned an invalid delivery receipt."}
                if receipt.get("attempted") is False:
                    self.attempted_actions -= 1
                if receipt["status"] == "delivered":
                    self.input_actions += 1
                action = {"step": step, "operation": selected["operation"], "name": fresh.get("name"),
                          "automation_id": fresh.get("automation_id"), "status": receipt["status"],
                          "selection_snapshot_id": previous["snapshot_id"],
                          "validated_snapshot_id": current["snapshot_id"], "receipt": receipt,
                          "goal_completed": False}
                self.actions.append(action)
                self.emit("action", deepcopy(action))
                # Even a failed/unknown driver result can follow partial input.
                # Refresh when possible, retain uncertainty, then stop the loop.
                after = observe("after_action")
                changed = fingerprint != observation_fingerprint(after)
                action["observed_change"] = changed
                action["after_snapshot_id"] = after["snapshot_id"]
                self.emit("action", deepcopy(action))
                if receipt["status"] != "delivered":
                    return finish("failed", "Input delivery failed or is uncertain. Fresh observation is available; no automatic retry.")
                if not changed:
                    return finish("no_progress", "Driver reported delivery, but no UIA state change was observed. Goal completion is unverified.")
                history.append({"action": selected["operation"] + " on " + str(fresh.get("name") or "unnamed control"),
                                "result": "Driver reported delivery; a fresh UIA observation changed. Goal completion is unverified."})
                previous = after
            return finish("limit_reached", "The action limit was reached. Review the fresh observation before another run.")
        except InterruptedError:
            return finish("cancelled" if self.cancelled() else "limit_reached",
                          "Execution stopped. Any in-flight delivery may have occurred; inspect the latest outcome.")
        except NeedsArguments as error:
            return finish("needs_arguments", str(error))
        except StaleTarget as error:
            return finish("stale_target", str(error))
        except ExecutionError as error:
            return finish("failed", str(error))
        except Exception:
            return finish("failed", "Execution stopped at an unexpected local boundary. Review the recorded outcomes before restarting.")
