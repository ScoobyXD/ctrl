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


def test_natural_task_strings_are_exact_user_spans_in_stable_registry_order():
    goal = 'Search YouTube for Python generators then enter "Hello" into the field.'
    assert model.supplied_values(goal, ["first", "Python generators"]) == (
        "first", "Python generators", "Hello",
    )
    assert model.supplied_values("Search YouTube for Python generators then open AWS") == ("Python generators",)
    assert model.supplied_values("Browse YouTube and open AWS") == ()


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
        element = body["state"]["elements"][0]
        assert element["checked"] == "true" and element["selected"] is False
        assert "[1]" in target["criteria"]["1"]
        assert body["state"]["task"]["rules"] == model.NEXT_ACTION
        assert "state.task.rules" in target["instructions"]
        assert "CLICK" in target["instructions"]
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
        assert len(body["state"]["task_browser_actions"]) == 3
        tab = next(a for a in body["state"]["task_browser_actions"] if a["kind"] == "switch_tab")
        assert set(tab) >= {"title", "target_id", "url"}
        assert "switch_tab:other-tab" in questions["switch_tab_target"]["criteria"]["switch_tab:other-tab"]
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


def test_earlier_work_compacted_while_recent_detail_is_retained(monkeypatch):
    history = [
        {"step": n + 1, "operation": "CLICK", "kind": "click", "action": f"Read item {n}",
         "url": f"https://example.test/item/{n}", "title": f"Item {n}", "target": "2", "page_changed": True}
        for n in range(200)
    ]
    history[0]["text"] = "  exact earlier supplied string  "

    def post(_url, _key, body):
        assert body["state"]["recent_actions"] == history[-12:]
        assert body["state"]["prior_progress"]["earlier_action_count"] == 188
        assert body["state"]["prior_progress"]["sites"] == [
            {"host": "example.test", "operation_counts": {"CLICK": 188}}
        ]
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


def bootstrap_page():
    state = page()
    state.update(
        url="https://unrelated.example/credentials", title="Private account unrelated to task",
        text="unrelated-private-page-sentinel", bootstrap=True,
        task_browser_scope={
            "mode": "requested_sites", "requested_urls": ["https://mail.google.com/", "https://x.com/"],
            "allowed_hosts": ["mail.google.com", "x.com"], "outside_scope": True,
        },
    )
    state["actions"].extend([
        {"id": "open_url:1", "kind": "open_url", "label": "Open Gmail", "url": "https://mail.google.com/"},
        {"id": "open_url:2", "kind": "open_url", "label": "Open X", "url": "https://x.com/"},
        {"id": "switch_tab:unrelated", "kind": "switch_tab", "label": "unrelated-tab-label-sentinel",
         "target_id": "unrelated", "url": "https://unrelated.example/credentials", "title": "Private keys"},
    ])
    return state


def test_unrelated_bootstrap_content_and_targets_never_reach_model(monkeypatch):
    state = bootstrap_page()
    history = [{"operation": "SWITCH_TAB", "url": "https://unrelated.example/keys",
                "action": "unrelated-history-sentinel", "text": "private-history-payload"} for _ in range(153)]

    def post(_url, _key, body):
        rendered = json.dumps(body)
        assert "unrelated.example" not in rendered
        assert "sentinel" not in rendered
        assert "private-history-payload" not in rendered
        assert body["state"]["page"] == {"url": "", "title": "Choose a task destination", "text": ""}
        assert body["state"]["elements"] == []
        assert body["state"]["recent_actions"] == []
        assert body["state"]["prior_progress"]["ignored_outside_scope_actions"] == 153
        questions = body["questions"]
        assert "SWITCH_TAB" not in questions["operation"]["criteria"]
        assert "CLICK" not in questions["operation"]["criteria"]
        return {"answers": {
            "operation": choice(questions["operation"]["criteria"], "OPEN_URL"),
            "open_url_target": choice(questions["open_url_target"]["criteria"], "open_url:1"),
        }}

    monkeypatch.setattr(model, "post_json", post)
    result = model.choose(state, "Open Gmail, then X", history)
    assert result["choice"] == "open_url:1"
    assert result["recent_action_count"] == 0


def test_only_task_host_tabs_are_offered_even_if_title_claims_relevance(monkeypatch):
    state = bootstrap_page()
    state["actions"][-1]["title"] = "Gmail read your requested emails here"
    state["actions"].append({
        "id": "switch_tab:gmail", "kind": "switch_tab", "label": "Gmail inbox",
        "target_id": "gmail", "url": "https://mail.google.com/mail/u/0/", "title": "Inbox",
    })

    def post(_url, _key, body):
        questions = body["questions"]
        assert list(questions["switch_tab_target"]["criteria"]) == ["switch_tab:gmail"]
        return {"answers": {
            "operation": choice(questions["operation"]["criteria"], "SWITCH_TAB"),
            "switch_tab_target": choice(questions["switch_tab_target"]["criteria"], "switch_tab:gmail"),
        }}

    monkeypatch.setattr(model, "post_json", post)
    assert model.choose(state, "Open Gmail", [])["choice"] == "switch_tab:gmail"


def test_outside_scope_page_stops_before_paid_request(monkeypatch):
    state = bootstrap_page()
    state["bootstrap"] = False
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="left the requested sites"):
        model.choose(state, "Open Gmail", [])
    post.assert_not_called()


def test_repeated_switches_do_not_become_progress_or_grow_context_unbounded(monkeypatch):
    history = [
        {"step": n + 1, "operation": "SWITCH_TAB", "url": "https://example.test/",
         "action": "Switch to site", "title": "Site"}
        for n in range(500)
    ]
    captured = []

    def post(_url, _key, body):
        captured.append(body)
        assert len(body["state"]["recent_actions"]) == 12
        assert body["state"]["prior_progress"]["sites"] == []
        return {"answers": {"operation": choice(body["questions"]["operation"]["criteria"], "DONE")}}

    monkeypatch.setattr(model, "post_json", post)
    first = model.choose(page(), "Task", history[:153])
    second = model.choose(page(), "Task", history)
    assert abs(first["request_bytes"] - second["request_bytes"]) < 100
    assert second["request_bytes"] < 12_000
    assert all(model.NEXT_ACTION not in question["instructions"] for question in captured[-1]["questions"].values())


def test_payload_references_preserve_exact_shared_string_once(monkeypatch):
    exact = "  exact supplied content  "

    def post(_url, _key, body):
        assert body["state"]["supplied_values"] == {"v1": exact}
        assert "v1" in body["questions"]["type_text_target"]["criteria"]["1@v1"]
        assert exact not in body["questions"]["type_text_target"]["criteria"]["1@v1"]
        assert body["state"]["recent_actions"][0]["payload_id"] == "v1"
        assert "text" not in body["state"]["recent_actions"][0]
        return {"answers": {
            "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
            "type_text_target": choice(["1@v1"], "1@v1"),
        }}

    monkeypatch.setattr(model, "post_json", post)
    result = model.choose(page(), "Task", [{"operation": "TYPE_TEXT", "payload_id": "v1", "text": exact}], [exact])
    assert result["text"] == exact


def test_oversize_exact_payloads_fail_before_paid_call_without_truncation(monkeypatch):
    post = Mock()
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="context is too large"):
        model.choose(page(), "Task", [], [f"{n:02}" + "x" * 1998 for n in range(32)])
    post.assert_not_called()


@pytest.mark.parametrize("mode", ["requested_sites", "selected_tab"])
def test_regional_service_subdomains_allowed_but_sibling_and_lookalike_hosts_excluded(mode):
    scope = {"mode": mode, "allowed_hosts": ["console.aws.amazon.com"]}
    assert model.in_task_scope("https://us-west-1.console.aws.amazon.com/console/home", scope)
    assert model.in_task_scope("https://console.aws.amazon.com/console/home", scope)
    assert not model.in_task_scope("https://evilconsole.aws.amazon.com/", scope)
    assert not model.in_task_scope("https://signin.aws.amazon.com/", scope)
    assert not model.in_task_scope("https://console.aws.amazon.com.attacker.example/", scope)


def test_selected_tab_mode_also_filters_unrelated_switch_candidates():
    state = bootstrap_page()
    scope = {"mode": "selected_tab", "allowed_hosts": ["mail.google.com"],
             "requested_urls": [], "outside_scope": False, "bootstrap": False}
    assert all(action["kind"] != "switch_tab" for action in model.scoped_actions(state, scope))


def test_model_scope_excludes_other_local_apps_and_ctrl_port():
    scope = {"mode": "requested_sites", "allowed_hosts": ["127.0.0.1:8877"]}
    assert model.in_task_scope("http://127.0.0.1:8877/fixture", scope)
    assert not model.in_task_scope("http://127.0.0.1:8878/unrelated", scope)
    assert not model.in_task_scope("http://127.0.0.1:8766/", scope)


def audited_questions(audit):
    return {question["id"]: question for question in audit["questions"]}


def audited_options(question):
    return {option["id"]: option for option in question["options"]}


def test_audit_contains_every_offered_option_with_actual_probabilities(monkeypatch):
    offered = {}

    def post(_url, _key, body):
        offered.update(body["questions"])
        questions = body["questions"]
        operation = choice(questions["operation"]["criteria"], "CLICK")
        operation["confidence"] = .86
        operation["probabilities"].update(CLICK=.8, DONE=.2)
        click = choice(questions["click_target"]["criteria"], "2")
        click["probabilities"] = {"1": .25, "2": .75}
        return {"model": model.MODEL + "-20260917", "answers": {
            "operation": operation, "click_target": click,
            "type_text_target": choice(questions["type_text_target"]["criteria"], "1@v1"),
            "blocked_reason": choice(questions["blocked_reason"]["criteria"], "sign_in_required"),
        }}

    monkeypatch.setattr(model, "post_json", post)
    result = model.choose(page(), "Find a book", [], ["exact text"])
    audit = result["audit"]
    questions = audited_questions(audit)
    assert set(audit) == {"questions", "selected", "model", "latency_ms", "request_bytes"}
    assert list(questions) == list(offered)
    for name, question in questions.items():
        assert list(audited_options(question)) == list(offered[name]["criteria"])
        assert all(option["allowed"] is None for option in question["options"])
    assert questions["operation"]["confidence"] == .86
    assert audited_options(questions["operation"])["CLICK"]["probability"] == .8
    assert audited_options(questions["click_target"])["1"]["probability"] == .25
    assert audited_options(questions["click_target"])["2"]["label"] == "Go"
    assert questions["click_target"]["validation"] == "valid"
    assert questions["click_target"]["applies"]
    assert audited_options(questions["click_target"])["2"]["used"]
    assert not audited_options(questions["click_target"])["1"]["used"]
    for name in ("type_text_target", "blocked_reason"):
        ignored = questions[name]
        selected = next(option for option in ignored["options"] if option["selected"])
        assert selected["probability"] == 1.
        assert not selected["used"] and selected["allowed"] is None
        assert not ignored["applies"] and ignored["validation"] == "ignored"
    assert questions["needs_input_reason"]["selected_id"] is None
    assert all(option["probability"] is None for option in questions["needs_input_reason"]["options"])
    assert audit["selected"] == {"operation": "CLICK", "action_id": "e3", "target_id": "2", "target_label": "Go"}
    assert audit["model"] == result["model"] == model.MODEL + "-20260917"
    assert audit["latency_ms"] == result["latency_ms"]
    assert audit["request_bytes"] == result["request_bytes"]
    json.dumps(audit, allow_nan=False)


@pytest.mark.parametrize("bad", [None, True, False, "0.7", [], {}, float("nan"), float("inf"), -1, 2, 10**400])
def test_audit_malformed_numbers_are_null_and_reject_required_head(monkeypatch, bad):
    def post(_url, _key, body):
        questions = body["questions"]
        target = choice(questions["click_target"]["criteria"], "2")
        target["probabilities"]["2"] = bad
        target["confidence"] = bad
        return {"answers": {
            "operation": choice(questions["operation"]["criteria"], "CLICK"), "click_target": target,
        }}

    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(model.DecisionAuditError, match="Invalid Jev") as error:
        model.choose(page(), "Task", [])
    audit = error.value.audit
    target = audited_questions(audit)["click_target"]
    selected = audited_options(target)["2"]
    assert target["applies"] and target["validation"] == "invalid"
    assert target["confidence"] is None and selected["probability"] is None
    assert selected["selected"] and selected["used"] and selected["allowed"] is None
    assert audit["selected"]["action_id"] == "e3"
    assert audited_questions(audit)["operation"]["validation"] == "valid"
    json.dumps(audit, allow_nan=False)


@pytest.mark.parametrize("malformed", [None, [], "provider-private-sentinel", {"probabilities": []}])
def test_malformed_speculative_heads_are_ignored_and_never_exposed(monkeypatch, malformed):
    def post(_url, _key, body):
        questions = body["questions"]
        return {"answers": {
            "operation": choice(questions["operation"]["criteria"], "WAIT"),
            "click_target": malformed, "blocked_reason": malformed,
            "provider-private-sentinel": {"text": "provider-private-sentinel"},
        }, "reasoning": "provider-private-sentinel", "usage": {"private": "provider-private-sentinel"}}

    monkeypatch.setattr(model, "post_json", post)
    result = model.choose(page(), "Task", [])
    questions = audited_questions(result["audit"])
    assert result["choice"] == "wait"
    for name in ("click_target", "blocked_reason"):
        assert questions[name]["validation"] == "ignored"
        assert not questions[name]["applies"]
        assert all(option["probability"] is None and not option["used"] for option in questions[name]["options"])
    assert "provider-private-sentinel" not in json.dumps(result["audit"], allow_nan=False)


@pytest.mark.parametrize("head", ["operation", "click_target"])
def test_unknown_returned_ids_are_not_added_or_echoed_in_audit(monkeypatch, head):
    def post(_url, _key, body):
        questions = body["questions"]
        answers = {
            "operation": choice(questions["operation"]["criteria"], "CLICK"),
            "click_target": choice(questions["click_target"]["criteria"], "2"),
        }
        answers[head]["choice"] = "private-provider-value"
        answers[head]["probabilities"]["private-provider-value"] = .7
        return {"answers": answers}

    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(model.DecisionAuditError) as error:
        model.choose(page(), "Task", [])
    questions = audited_questions(error.value.audit)
    assert questions[head]["selected_id"] is None
    assert questions[head]["validation"] == "invalid"
    assert not any(option["selected"] or option["used"] for option in questions[head]["options"])
    if head == "operation":
        assert not questions["click_target"]["applies"]
    assert "private-provider-value" not in json.dumps(error.value.audit)
    assert "private-provider-value" not in str(error.value)


@pytest.mark.parametrize("mutation", ["missing_key", "extra_key", "sum", "non_max", "type"])
def test_audit_preserves_selected_head_distribution_validation(monkeypatch, mutation):
    def post(_url, _key, body):
        questions = body["questions"]
        target = choice(questions["click_target"]["criteria"], "2")
        if mutation == "missing_key":
            del target["probabilities"]["1"]
        elif mutation == "extra_key":
            target["probabilities"]["unknown-private-value"] = 0
        elif mutation == "sum":
            target["probabilities"]["2"] = .7
        elif mutation == "non_max":
            target["choice"] = "1"
        else:
            target["type"] = "score"
        return {"answers": {
            "operation": choice(questions["operation"]["criteria"], "CLICK"), "click_target": target,
        }}

    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(model.DecisionAuditError) as error:
        model.choose(page(), "Task", [])
    target = audited_questions(error.value.audit)["click_target"]
    assert target["validation"] == "invalid"
    assert set(audited_options(target)) == {"1", "2"}


@pytest.mark.parametrize("operation,reason", [("BLOCKED", "sign_in_required"), ("NEEDS_INPUT", "missing_text")])
def test_audit_only_uses_matching_terminal_reason(monkeypatch, operation, reason):
    def post(_url, _key, body):
        questions = body["questions"]
        return {"answers": {
            "operation": choice(questions["operation"]["criteria"], operation),
            "blocked_reason": choice(questions["blocked_reason"]["criteria"], "sign_in_required"),
            "needs_input_reason": choice(questions["needs_input_reason"]["criteria"], "missing_text"),
        }}

    monkeypatch.setattr(model, "post_json", post)
    audit = model.choose(page(), "Task", [])["audit"]
    questions = audited_questions(audit)
    assert {name for name, question in questions.items() if question["applies"]} == {
        "operation", operation.lower() + "_reason",
    }
    assert audit["selected"]["operation"] == operation
    assert audit["selected"]["action_id"] is None
    assert audit["selected"]["target_id"] == reason


def test_audit_fill_labels_preserve_exact_offered_payloads(monkeypatch):
    values = ["  first exact  ", "second"]

    def post(_url, _key, body):
        questions = body["questions"]
        return {"answers": {
            "operation": choice(questions["operation"]["criteria"], "TYPE_TEXT"),
            "type_text_target": choice(questions["type_text_target"]["criteria"], "1@v2"),
        }}

    monkeypatch.setattr(model, "post_json", post)
    audit = model.choose(page(), "Task", [], values)["audit"]
    options = audited_options(audited_questions(audit)["type_text_target"])
    assert options["1@v1"]["label"] == "Search — v1:   first exact  "
    assert options["1@v2"]["label"] == "Search — v2: second"
    assert options["1@v2"]["used"] and not options["1@v1"]["used"]
    assert audit["selected"]["action_id"] == "e1"


@pytest.mark.parametrize("response", [
    None, [], {}, {"answers": []}, {"answers": {}, "model": "private-provider-value"},
])
def test_invalid_response_has_safe_audit(monkeypatch, response):
    monkeypatch.setattr(model, "post_json", Mock(return_value=response))
    with pytest.raises(model.DecisionAuditError) as error:
        model.choose(page(), "Task", [])
    audit = error.value.audit
    assert audited_questions(audit)["operation"]["validation"] == "invalid"
    assert all(not option["used"] for question in audit["questions"] for option in question["options"])
    assert "private-provider-value" not in json.dumps(audit, allow_nan=False)


@pytest.mark.parametrize("failure,expected", [
    (ValueError("OpenRouter returned invalid JSON; no action executed."), "invalid JSON"),
    (RuntimeError("OpenRouter returned HTTP 503; no action executed."), "HTTP 503"),
    (RuntimeError("private-provider-value"), "Model response unavailable"),
])
def test_failed_response_audit_has_no_fabricated_probabilities(monkeypatch, failure, expected):
    monkeypatch.setattr(model, "post_json", Mock(side_effect=failure))
    with pytest.raises(model.DecisionAuditError, match=expected) as error:
        model.choose(page(), "Task", [])
    audit = error.value.audit
    assert audit["model"] is None
    assert all(option["probability"] is None for question in audit["questions"] for option in question["options"])
    assert "private-provider-value" not in str(error.value)
    assert "private-provider-value" not in json.dumps(audit, allow_nan=False)


def test_credential_errors_remain_outside_model_audit_wrapper(monkeypatch):
    class FakeCredentialError(RuntimeError):
        pass

    failure = FakeCredentialError("Credential connection required")
    post = Mock()
    monkeypatch.setattr(model, "get_openrouter_key", Mock(side_effect=failure))
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(FakeCredentialError) as error:
        model.choose(page(), "Task", [])
    assert error.value is failure
    post.assert_not_called()


def test_ignored_head_selection_with_invalid_distribution_does_not_gate_execution(monkeypatch):
    def post(_url, _key, body):
        questions = body["questions"]
        return {"answers": {
            "operation": choice(questions["operation"]["criteria"], "DONE"),
            "click_target": {
                "choice": "2", "confidence": float("nan"),
                "probabilities": {"1": True, "2": .12, "private-provider-value": .88},
            },
        }}

    monkeypatch.setattr(model, "post_json", post)
    result = model.choose(page(), "Task", [])
    target = audited_questions(result["audit"])["click_target"]
    options = audited_options(target)
    assert result["operation"] == "DONE"
    assert target["selected_id"] == "2" and target["confidence"] is None
    assert target["validation"] == "ignored" and not target["applies"]
    assert options["1"]["probability"] is None
    assert options["2"]["probability"] == .12 and options["2"]["selected"] and not options["2"]["used"]
    assert "private-provider-value" not in json.dumps(result["audit"], allow_nan=False)
