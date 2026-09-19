"""Offline OpenRouter Decisions and supplied-value contracts. No paid calls."""

import json
from unittest.mock import Mock

import httpx
import pytest

from jev_ultrafast import model


def page():
    return {
        "url": "https://example.test/", "title": "Search", "text": "Search",
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }


def choice(ids, selected):
    return {"type": "choice", "choice": selected, "confidence": 1.,
            "probabilities": {i: float(i == selected) for i in ids}}


@pytest.fixture(autouse=True)
def no_live_requests(monkeypatch):
    monkeypatch.setattr(model, "get_openrouter_key", lambda: "offline-test-key")
    monkeypatch.setattr(model.CLIENT, "post", Mock(side_effect=AssertionError("Unexpected live request")))


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence", "bool", "type"])
def test_invalid_choice_is_rejected(mutation):
    answer = choice(["a", "b"], "a")
    if mutation == "unknown":
        answer["choice"] = "invented"
    elif mutation == "nan":
        answer["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del answer["probabilities"]["b"]
    elif mutation == "negative":
        answer["probabilities"]["b"] = -1
    elif mutation == "non_max":
        answer["choice"] = "b"
    elif mutation == "bool":
        answer["confidence"] = True
    elif mutation == "type":
        answer["type"] = "score"
    else:
        answer["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid Jev"):
        model.validate_choice(answer, {"a", "b"})


@pytest.mark.parametrize("answer", [None, [], {}, {"probabilities": []}])
def test_malformed_answer_rejected(answer):
    with pytest.raises(ValueError, match="Invalid Jev"):
        model.validate_choice(answer, {"a"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_exact_supplied_values_are_deduped_not_rewritten():
    goal = 'Find "New York" then “London” and \'Paris\'. Don\'t infer a city.'
    assert model.supplied_values(goal, ["  exact  ", "New York"]) == ("  exact  ", "New York", "London", "Paris")
    assert model.supplied_values("Find cats; do not type quotes", []) == ()
    assert model.supplied_values('Search "C:\\new"') == ("C:\\new",)


@pytest.mark.parametrize("values", ["string", [None], [2], [" "], ["x" * 2001], [str(i) for i in range(33)]])
def test_invalid_supplied_values_rejected(values):
    with pytest.raises(ValueError):
        model.supplied_values("Task", values)


def test_one_openrouter_request_selects_field_and_exact_payload(monkeypatch):
    calls = []

    def post(url, key, body):
        calls.append(body)
        assert url == "https://openrouter.ai/api/alpha/decisions" and key == "offline-test-key"
        assert body["model"] == "typesafe/jev-1.13"
        for question in body["questions"].values():
            assert question["type"] == "choice"
            assert isinstance(question["instructions"], str)
            assert all(isinstance(value, str) for value in question["criteria"].values())
        return {"model": model.MODEL, "answers": {
            "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
            "type_text_target": choice(["1@v1", "1@v2"], "1@v2"),
            "click_target": {"choice": "invented", "text": "untrusted text"},
        }}

    monkeypatch.setattr(model, "post_json", post)
    result = model.choose(page(), "Find a book", [], ["first", "exact supplied text"])
    assert len(calls) == 1
    assert result["choice"] == "e1" and result["target"] == "1@v2"
    assert result["payload_id"] == "v2" and result["text"] == "exact supplied text"
    assert result["probabilities"] == {"e1": 1.0}
    assert "request" not in result and "raw_answers" not in result


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {"answers": {
            "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
            "type_text_target": choice(["1@v1"], "1@v1"),
            "click_target": choice(["1", "2", "999"], "999"),
        }}

    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid Jev"):
        model.choose(page(), 'Find "book"', [])


def test_missing_supplied_strings_offers_needs_input_without_typing(monkeypatch):
    def post(_url, _key, body):
        questions = body["questions"]
        assert "TYPE_TEXT" not in questions["operation"]["criteria"]
        assert "type_text_target" not in questions
        assert body["state"]["supplied_values"] == {}
        return {"answers": {
            "operation": choice(questions["operation"]["criteria"], "NEEDS_INPUT"),
            "needs_input_reason": choice(questions["needs_input_reason"]["criteria"], "missing_text"),
        }}

    monkeypatch.setattr(model, "post_json", post)
    assert model.choose(page(), "Find a book", [])["choice"] == "NEEDS_INPUT"


def test_target_receives_current_control_state_and_rules(monkeypatch):
    state = page()
    state["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        criteria = json.loads(target["criteria"]["1"])
        assert criteria["checked"] == "true" and criteria["selected"] is False
        rules = json.loads(questions["operation"]["instructions"])["rules"]
        assert rules in json.loads(target["instructions"])["rules"]
        return {"answers": {
            "operation": choice(questions["operation"]["criteria"], "CLICK"),
            "click_target": choice(target["criteria"], "3"),
        }}

    monkeypatch.setattr(model, "post_json", post)
    assert model.choose(state, "Search with free cancellation", [])["choice"] == "e3"


def test_transport_error_is_redacted_and_not_retried(monkeypatch):
    post = Mock(side_effect=httpx.ConnectError("secret-key-must-not-leak"))
    monkeypatch.setattr(model.CLIENT, "post", post)
    with pytest.raises(RuntimeError, match="Model connection failed") as error:
        model.post_json(model.ENDPOINT, "secret-key-must-not-leak", {"model": model.MODEL})
    assert "secret-key-must-not-leak" not in str(error.value)
    assert post.call_count == 1


def test_paid_endpoint_and_model_cannot_be_overridden():
    with pytest.raises(ValueError, match="Only Jev"):
        model.post_json("https://example.test", "test", {"model": model.MODEL})
    with pytest.raises(ValueError, match="Only Jev"):
        model.post_json(model.ENDPOINT, "test", {"model": "other-model"})


def test_provider_error_body_is_not_exposed(monkeypatch):
    response = httpx.Response(401, json={"error": {"message": "secret-key"}})
    monkeypatch.setattr(model.CLIENT, "post", Mock(return_value=response))
    with pytest.raises(RuntimeError, match="HTTP 401") as error:
        model.post_json(model.ENDPOINT, "test", {"model": model.MODEL})
    assert "secret-key" not in str(error.value)


def test_excess_pairs_rejected_before_model_request(monkeypatch):
    state = page()
    state["actions"] = [
        {"id": f"e{n}", "kind": "fill", "label": f"Field {n}", "node": n, "role": "textbox"}
        for n in range(9)
    ]
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="maximum 255"):
        model.choose(state, "Fill fields", [], [str(n) for n in range(32)])
    post.assert_not_called()


@pytest.mark.parametrize("returned_model", ["other/model", "typesafe/jev-1.13-unverified", None])
def test_other_model_response_cannot_execute(monkeypatch, returned_model):
    monkeypatch.setattr(model, "post_json", Mock(return_value={"model": returned_model, "answers": {}}))
    with pytest.raises(ValueError, match="different model"):
        model.choose(page(), "Task", [])


def browser_page():
    state = page()
    state["target_id"] = "current-tab"
    state["actions"].extend([
        {"id": "open_url:0", "kind": "open_url", "label": "Open requested site", "url": "https://example.org/"},
        {"id": "switch_tab:other-tab", "kind": "switch_tab", "label": "Switch to existing tab",
         "target_id": "other-tab", "url": "https://example.net/", "title": "Another site"},
        {"id": "back", "kind": "back", "label": "Go back", "entry_id": 7,
         "url": "https://example.test/prior", "title": "Prior page"},
    ])
    return state


def test_browser_targets_do_not_require_nodes_or_change_dom_indices():
    state = browser_page()
    elements, targets, controls = model.action_space(state["actions"])
    assert len(elements) == 2
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["OPEN_URL"]["open_url:0"]["url"] == "https://example.org/"
    assert targets["SWITCH_TAB"]["switch_tab:other-tab"]["target_id"] == "other-tab"
    assert controls["BACK"]["id"] == "back"
    assert all("node" not in action for action in targets["OPEN_URL"].values())


@pytest.mark.parametrize("operation,selected", [
    ("OPEN_URL", "open_url:0"), ("SWITCH_TAB", "switch_tab:other-tab"), ("BACK", "back"),
])
def test_one_request_uses_only_selected_browser_head(monkeypatch, operation, selected):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        questions = body["questions"]
        assert body["state"]["active_tab"] == "current-tab"
        assert len(body["state"]["browser_actions"]) == 3
        assert set(json.loads(questions["switch_tab_target"]["criteria"]["switch_tab:other-tab"])) >= {
            "title", "target_id", "url",
        }
        answers = {"operation": choice(questions["operation"]["criteria"], operation),
                   "blocked_reason": {"choice": "invented"}, "needs_input_reason": {"choice": "invented"},
                   "click_target": {"choice": "invented"}}
        if operation != "BACK":
            key = operation.lower() + "_target"
            answers[key] = choice(questions[key]["criteria"], selected)
        return {"answers": answers}

    monkeypatch.setattr(model, "post_json", post)
    result = model.choose(browser_page(), "Open the next requested site", [])
    assert len(calls) == 1
    assert result["operation"] == operation and result["choice"] == selected
    assert result["stop_reason"] is None
    assert result["target"] == (None if operation == "BACK" else selected)


def test_navigation_cannot_consume_a_different_operation_target(monkeypatch):
    def post(_url, _key, body):
        questions = body["questions"]
        return {"answers": {
            "operation": choice(questions["operation"]["criteria"], "OPEN_URL"),
            "open_url_target": choice(["switch_tab:other-tab"], "switch_tab:other-tab"),
            "switch_tab_target": choice(questions["switch_tab_target"]["criteria"], "switch_tab:other-tab"),
        }}

    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid Jev"):
        model.choose(browser_page(), "Go to another site", [])


@pytest.mark.parametrize("operation,reason", [
    ("BLOCKED", "sign_in_required"), ("BLOCKED", "unsupported_content"),
    ("NEEDS_INPUT", "missing_address"), ("NEEDS_INPUT", "ambiguous_goal"),
])
def test_only_matching_terminal_reason_head_is_consumed(monkeypatch, operation, reason):
    def post(_url, _key, body):
        questions = body["questions"]
        key = operation.lower() + "_reason"
        answers = {
            "operation": choice(questions["operation"]["criteria"], operation),
            "blocked_reason": {"choice": "invented"}, "needs_input_reason": {"choice": "invented"},
            "click_target": {"choice": "invented"},
        }
        answers[key] = choice(questions[key]["criteria"], reason)
        return {"answers": answers}

    monkeypatch.setattr(model, "post_json", post)
    result = model.choose(page(), "Task", [])
    assert result["operation"] == operation and result["stop_reason"] == reason


def test_incompatible_stop_reason_is_rejected(monkeypatch):
    def post(_url, _key, body):
        questions = body["questions"]
        return {"answers": {
            "operation": choice(questions["operation"]["criteria"], "BLOCKED"),
            "blocked_reason": choice(["missing_text"], "missing_text"),
        }}

    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid Jev"):
        model.choose(page(), "Task", [])


def test_all_completed_history_retained_across_site_changes(monkeypatch):
    history = [
        {"step": n + 1, "operation": "CLICK", "kind": "click", "action": f"Read item {n}",
         "url": f"https://example.test/item/{n}", "title": f"Item {n}", "target": "2", "page_changed": True}
        for n in range(200)
    ]
    history[0]["text"] = "  exact earlier supplied string  "

    def post(_url, _key, body):
        assert body["state"]["recent_actions"] == history
        return {"answers": {"operation": choice(body["questions"]["operation"]["criteria"], "DONE")}}

    monkeypatch.setattr(model, "post_json", post)
    assert model.choose(page(), "Finish all sites", history)["operation"] == "DONE"


def test_history_bound_fails_before_paid_request(monkeypatch):
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="500-action"):
        model.choose(page(), "Task", [{} for _ in range(501)])
    post.assert_not_called()


def test_duplicate_action_ids_cannot_confuse_browser_and_dom_targets():
    state = browser_page()
    state["actions"][-1]["id"] = "e1"
    with pytest.raises(ValueError, match="distinct string IDs"):
        model.action_space(state["actions"])


def test_excess_browser_choices_are_not_silently_discarded(monkeypatch):
    state = page()
    state["actions"].extend([
        {"id": f"open_url:{n}", "kind": "open_url", "label": "Open", "url": f"https://example.org/{n}"}
        for n in range(256)
    ])
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="OPEN_URL targets"):
        model.choose(state, "Open site", [])
    post.assert_not_called()
