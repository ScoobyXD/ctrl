"""Jev finite decisions through OpenRouter; strings always come from the user."""

import json
import math
import re
import time

import httpx

from ctrl.credentials import get_openrouter_key

from .questions import NEXT_ACTION, TARGET

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
CLIENT = httpx.Client(http2=True, timeout=25)
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
    for value in [*values, *quoted]:
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
    """One index per observed element; each operation has its own valid targets."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            if kind not in {"scroll", "wait"}:
                raise ValueError("Unsupported observed operation.")
            controls[action["id"].upper()] = action
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
    elements, targets, controls = decision_space(state["actions"], values)
    labels = {
        "CLICK": "Click an observed element, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Replace an editable field with one exact supplied string; choose a field/string pair.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(
        DONE="Every requirement is visibly satisfied.",
        BLOCKED="No supported operation can progress for a reason other than missing input text.",
        NEEDS_INPUT="Progress requires typing text absent from supplied_values. Ask the user for it.",
    )
    questions = {"operation": {
        "type": "choice", "criteria": operations,
        "instructions": json.dumps({"goal": goal, "rules": NEXT_ACTION}, ensure_ascii=False),
    }}
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: json.dumps({
                    "element": a["label"],
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded", "payload_id", "text") if k in a},
                }, ensure_ascii=False)
                for index, a in candidates.items()
            },
            "instructions": json.dumps(
                {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]}, ensure_ascii=False
            ),
        }
    body = {
        "model": MODEL,
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "supplied_values": {f"v{number}": value for number, value in enumerate(values, 1)},
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
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
    return {
        "choice": choice, "operation": operation, "target": target, "payload_id": payload_id, "text": text,
        "confidence": operation_answer["confidence"], "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "model": result.get("model", MODEL), "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }
