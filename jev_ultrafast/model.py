"""Jev finite decisions through OpenRouter; strings always come from the user."""

import json
import math
import re
import time
from collections import Counter
from urllib.parse import urlsplit

import httpx

from ctrl.browser_scope import host_allowed
from ctrl.credentials import get_openrouter_key

from .navigation import task_text_values
from .questions import NEXT_ACTION, TARGET

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
CLIENT = httpx.Client(http2=True, timeout=25)
MAX_HISTORY = 500
RECENT_HISTORY = 12
MAX_REQUEST_BYTES = 60_000
BLOCKED_REASONS = {
    "unavailable_control": "The next required control is absent or disabled and waiting or navigation cannot help.",
    "sign_in_required": "The task needs the user to sign in or complete an authentication challenge themselves.",
    "unsupported_content": "Required content or interaction cannot be accessed with the offered browser operations.",
    "cannot_progress": "Available operations cannot make further progress on the remaining requirements.",
}
INPUT_REASONS = {
    "missing_text": "Necessary exact text to type is missing from the user-supplied strings.",
    "missing_address": "The required website is absent from offered destinations and tabs; its exact URL is needed.",
    "ambiguous_goal": "The next requested action or outcome needs clarification, including consequential interactions.",
}
# Preserve literal contents; never unescape, rewrite, or extract page text.
QUOTED = re.compile(r'"([^"\r\n]+)"|“([^”\r\n]+)”|(?<!\w)\'([^\'\r\n]+)\'(?!\w)|‘([^’\r\n]+)’')


def supplied_values(goal, values=None):
    """An ordered, bounded tuple of exact supplied strings, including task quotes."""
    if values is None:
        values = []
    if not isinstance(values, (list, tuple)):
        raise ValueError("Supplied values must be a list of strings.")
    result = []
    quoted = [next(part for part in match.groups() if part is not None) for match in QUOTED.finditer(goal)]
    for value in [*values, *quoted, *task_text_values(goal)]:
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError("Each supplied value must contain 1–2,000 characters and cannot be blank.")
        if value not in result:
            result.append(value)
    if len(result) > 32:
        raise ValueError("Supply at most 32 distinct strings for this run.")
    return tuple(result)


def post_json(url, key, body):
    if url != ENDPOINT or body.get("model") != MODEL:
        raise ValueError("Only Jev through OpenRouter Decisions is supported.")
    if not key:
        raise ValueError("Connect your OpenRouter credential before starting a run.")
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"OpenRouter returned HTTP {response.status_code}; no action executed.")
        try:
            result = response.json()
        except ValueError:
            raise ValueError("OpenRouter returned invalid JSON; no action executed.") from None
        if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
            raise ValueError("OpenRouter returned invalid decisions; no action executed.")
        return result
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer.get("type", "choice") == "choice"
            and answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid Jev choice; no action executed.")
    return answer


def action_space(actions):
    """DOM indices and observed browser targets share operation-specific choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    identifiers = [action["id"] for action in actions]
    if any(not isinstance(identifier, str) for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("Observed actions must have distinct string IDs.")
    for action in actions:
        kind = action["kind"]
        if kind in {"open_url", "switch_tab"}:
            operation = {"open_url": "OPEN_URL", "switch_tab": "SWITCH_TAB"}[kind]
            targets.setdefault(operation, {})[action["id"]] = action
            continue
        if kind not in operations:
            if kind not in {"scroll", "wait", "back"}:
                raise ValueError("Unsupported observed operation.")
            controls["BACK" if kind == "back" else action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def task_browser_scope(state):
    """Copy only code-owned task boundaries, never infer relevance from tab text."""
    scope = state.get("task_browser_scope")
    if not isinstance(scope, dict):
        return None
    return {
        "mode": scope.get("mode"),
        "requested_urls": list(scope.get("requested_urls", [])),
        "allowed_hosts": list(scope.get("allowed_hosts", [])),
        "outside_scope": bool(scope.get("outside_scope")),
        "bootstrap": bool(state.get("bootstrap")),
    }


def in_task_scope(url, scope):
    if not scope:
        return True
    return host_allowed(url, scope["allowed_hosts"])


def scoped_actions(state, scope):
    if not scope:
        return state["actions"]
    if scope["outside_scope"] and not scope["bootstrap"]:
        raise ValueError("The browser left the requested sites. Inspect the task before continuing.")
    actions = []
    for action in state["actions"]:
        kind = action["kind"]
        if kind == "open_url":
            if action["url"] in scope["requested_urls"]:
                actions.append(action)
        elif kind == "switch_tab":
            if in_task_scope(action["url"], scope):
                actions.append(action)
        elif not scope["outside_scope"] and (kind != "back" or in_task_scope(action["url"], scope)):
            actions.append(action)
    return actions


def history_context(history, scope=None):
    """Recent detail plus deterministic counts; navigation is never completed work."""
    if len(history) > MAX_HISTORY:
        raise ValueError("Completed action history exceeds the 500-action run limit.")
    relevant = [entry for entry in history if in_task_scope(entry.get("url", ""), scope)]
    older, recent = relevant[:-RECENT_HISTORY], relevant[-RECENT_HISTORY:]
    fields = ("step", "operation", "action", "kind", "url", "title", "target", "target_id", "text", "page_changed")
    result = []
    for entry in recent:
        compact = {key: entry[key] for key in fields if entry.get(key) is not None}
        for key, limit in (("action", 160), ("title", 160), ("url", 1000), ("text", 2000)):
            value = compact.get(key)
            if isinstance(value, str) and len(value) > limit:
                compact[key] = value[:limit - 3] + "..."
        if entry.get("payload_id"):
            compact["payload_id"] = entry["payload_id"]
            compact.pop("text", None)  # Exact payload remains in the shared supplied_values registry.
        result.append(compact)
    site_counts = {}
    for entry in older:
        operation = entry.get("operation")
        if operation not in {"CLICK", "TYPE_TEXT", "SELECT", "SCROLL_UP", "SCROLL_DOWN"}:
            continue
        try:
            host = urlsplit(entry.get("url", "")).hostname
        except ValueError:
            host = None
        if host:
            site_counts.setdefault(host, Counter())[operation] += 1
    progress = {
        "earlier_action_count": len(older),
        "sites": [{"host": host, "operation_counts": dict(counts)} for host, counts in list(site_counts.items())[:16]],
        "omitted_site_count": max(0, len(site_counts) - 16),
        "ignored_outside_scope_actions": len(history) - len(relevant),
        "meaning": "Executed DOM-operation counts only. They do not prove completed requirements or items read.",
    }
    return result, progress


def target_criterion(operation, index, action):
    """Reference shared context rather than duplicating field and payload contents."""
    if operation == "TYPE_TEXT":
        return f"Element [{index.split('@')[0]}], exact supplied value {action['payload_id']}."
    if operation == "SELECT":
        return f"Observed option [{index}] on element [{index.split(':')[0]}]."
    if operation in {"OPEN_URL", "SWITCH_TAB"}:
        return f"Browser action [{index}] from task_browser_actions."
    return f"Observed element [{index}] from elements."


def decision_space(actions, values):
    """Bind each editable target to an exact payload in the same finite choice."""
    elements, targets, controls = action_space(actions)
    fields = targets.pop("TYPE_TEXT", {})
    if fields and values:
        if len(fields) * len(values) > 255:
            raise ValueError(
                "Too many field/string choices (maximum 255). Reduce Supplied strings or narrow the page's controls."
            )
        targets["TYPE_TEXT"] = {
            f"{index}@v{number}": {**action, "payload_id": f"v{number}", "text": value}
            for index, action in fields.items()
            for number, value in enumerate(values, 1)
        }
    if not values:
        for element in elements:
            element["operations"] = [op for op in element["operations"] if op != "TYPE_TEXT"]
    return elements, targets, controls


def choose(state, goal, history, values=None):
    values = supplied_values(goal, values)
    scope = task_browser_scope(state)
    actions = scoped_actions(state, scope)
    elements, targets, controls = decision_space(actions, values)
    recent_actions, prior_progress = history_context(history, scope)
    labels = {
        "CLICK": "Click an observed element, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Replace an editable field with one exact supplied string; choose a field/string pair.",
        "SELECT": "Select an observed dropdown value.",
        "OPEN_URL": "Open the next requested destination when it is not already open in a task-relevant tab.",
        "SWITCH_TAB": "Switch only to an existing tab for the next unfinished requested site, never a random open tab.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(
        DONE="Every requirement is visibly satisfied.",
        BLOCKED="No supported operation can progress for a reason other than missing input text.",
        NEEDS_INPUT="Progress requires missing exact text, a destination URL, or a more specific user instruction.",
    )
    questions = {"operation": {
        "type": "choice", "criteria": operations,
        "instructions": "Choose the next operation for state.task.goal. Follow task.rules and task_browser_scope.",
    }}
    for operation, reasons in (("BLOCKED", BLOCKED_REASONS), ("NEEDS_INPUT", INPUT_REASONS)):
        questions[operation.lower() + "_reason"] = {
            "type": "choice", "criteria": reasons,
            "instructions": (
                f"Assume the next operation is {operation}. Following state.task.rules, "
                "choose the reason supported by the current task and page."
            ),
        }
    for operation, candidates in targets.items():
        if len(candidates) > 255:
            raise ValueError(f"Too many {operation} targets (maximum 255). Narrow the available browser choices.")
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: target_criterion(operation, index, a)
                for index, a in candidates.items()
            },
            "instructions": f"Assume the next operation is {operation}. {TARGET}",
        }
    body = {
        "model": MODEL,
        "state": {
            "task": {"goal": goal, "rules": NEXT_ACTION},
            "page": (
                {"url": "", "title": "Choose a task destination", "text": ""}
                if scope and scope["outside_scope"]
                else {"url": state["url"], "title": state["title"][:240], "text": state["text"][:6000]}
            ),
            "elements": elements,
            "task_browser_actions": [
                {key: action[key] for key in ("id", "kind", "label", "url", "title", "target_id") if key in action}
                for action in actions if action["kind"] in {"open_url", "switch_tab", "back"}
            ],
            "task_browser_scope": scope,
            "active_tab": state.get("target_id"),
            "supplied_values": {f"v{number}": value for number, value in enumerate(values, 1)},
            "recent_actions": recent_actions,
            "prior_progress": prior_progress,
        },
        "questions": questions,
    }
    request_bytes = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if request_bytes > MAX_REQUEST_BYTES:
        raise ValueError(
            "The decision context is too large. Narrow the task, supplied strings, or visible page before trying again."
        )
    started = time.perf_counter()
    result = post_json(ENDPOINT, get_openrouter_key(), body)
    if "model" in result and (
        not isinstance(result["model"], str)
        or re.fullmatch(r"typesafe/jev-1\.13(?:-\d{8})?", result["model"]) is None
    ):
        raise ValueError("OpenRouter returned a different model; no action executed.")
    answers = result.get("answers", {})
    operation_answer = validate_choice(answers.get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = target_answer = payload_id = text = None
    stop_reason = None
    probabilities = {}
    if operation in targets:
        # Only the target head corresponding to the chosen operation can cause input.
        target_answer = validate_choice(answers.get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        selected = targets[operation][target]
        choice = selected["id"]
        payload_id, text = selected.get("payload_id"), selected.get("text")
        for index, action in targets[operation].items():
            probabilities[action["id"]] = probabilities.get(action["id"], 0) + target_answer["probabilities"][index]
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
        if operation in {"BLOCKED", "NEEDS_INPUT"}:
            reasons = BLOCKED_REASONS if operation == "BLOCKED" else INPUT_REASONS
            stop_reason = validate_choice(answers.get(operation.lower() + "_reason", {}), reasons)["choice"]
    return {
        "choice": choice, "operation": operation, "target": target, "payload_id": payload_id, "text": text,
        "stop_reason": stop_reason,
        "confidence": operation_answer["confidence"], "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "model": result.get("model", MODEL), "usage": result.get("usage", {}),
        "request_bytes": request_bytes, "recent_action_count": len(recent_actions),
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }
