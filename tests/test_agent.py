"""Offline loop contracts: no Chrome connection, no paid API calls."""

import time
from threading import Event
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast.browser import ActionPaused, StalePage, fingerprint


def page():
    state = {
        "url": "https://example.test/", "title": "Search", "text": "Search", "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def decision(action="e1"):
    operation, target = {"e1": ("TYPE_TEXT", "1@v1"), "e3": ("CLICK", "2"), "wait": ("WAIT", None)}.get(
        action, (action, None)
    )
    return {
        "choice": action, "operation": operation, "target": target,
        "payload_id": "v1" if action == "e1" else None,
        "text": "untrusted decision string", "confidence": 1.,
        "probabilities": {action: 1.}, "latency_ms": 10, "usage": {},
    }


@pytest.fixture
def runner():
    agent = loop.Agent.__new__(loop.Agent)
    agent.screenshots = False
    agent.values = ("exact user string",)
    agent.cancel_event = Event()
    state = page()
    agent.browser = Mock(fresh=Mock(return_value=True), observe=Mock(return_value=state))
    agent.state = {
        "browser": agent.browser, "page": state, "decision": decision(), "goal": "Find a book",
        "history": [], "decisions": [], "status": "predicted", "values": list(agent.values),
        "started_at": time.perf_counter(), "record": False,
    }
    return agent


def act(runner):
    return runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})


def test_constructor_uses_existing_target_and_supplied_values(monkeypatch):
    browser = Mock(return_value=Mock(observe=Mock(return_value=page())))
    monkeypatch.setattr(loop, "Browser", browser)
    agent = loop.Agent(None, 'Find "book"', target_id="existing-tab", values=["second"])
    browser.assert_called_once_with(None, target_id="existing-tab", navigation_urls=[])
    assert agent.state["values"] == ["second", "book"]


def test_stale_decision_consumed_before_mutation(runner):
    runner.browser.fresh.return_value = False
    with pytest.raises(StalePage):
        act(runner)
    runner.browser.act.assert_not_called()
    assert runner.state["decision"] is None


def test_executor_uses_local_payload_ignoring_decision_prose(runner):
    act(runner)
    assert runner.browser.act.call_args.kwargs["text"] == "exact user string"
    assert runner.state["history"][-1]["text"] == "exact user string"
    assert runner.state["decision"] is None


@pytest.mark.parametrize("change", ["payload", "target", "operation"])
def test_invalid_pair_rejected_before_mutation(runner, change):
    key, value = {"payload": ("payload_id", "v999"), "target": ("target", "2@v1"),
                  "operation": ("operation", "CLICK")}[change]
    runner.state["decision"][key] = value
    with pytest.raises(ValueError, match="Decision does not match"):
        act(runner)
    runner.browser.act.assert_not_called()


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        act(runner)
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.browser.observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        act(runner)
    assert runner.state["history"][-1]["action"] == "Go"
    runner.browser.act.assert_called_once()
    with pytest.raises(ValueError, match="Observe and choose"):
        act(runner)
    runner.browser.act.assert_called_once()


def test_transport_failure_cannot_replay_a_mutation(runner):
    runner.state["decision"] = decision("e3")
    runner.browser.act.side_effect = RuntimeError("Transport failed")
    with pytest.raises(RuntimeError):
        act(runner)
    assert runner.state["status"] == "error"
    with pytest.raises(ValueError):
        act(runner)
    runner.browser.act.assert_called_once()


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.browser.fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready" and runner.state["decision"] is None
    runner.browser.act.assert_not_called()


def test_needs_input_is_terminal_until_supplied_and_keeps_history(runner):
    runner.state["history"] = [{"action": "prior action"}]
    runner.state["decision"] = decision("NEEDS_INPUT")
    act(runner)
    assert runner.state["status"] == "needs_input"
    assert list(runner.run()) == []
    runner.browser.act.assert_not_called()
    runner.command("supply_values", {"values": ["new value", "exact user string"]})
    assert runner.state["values"] == ["exact user string", "new value"]
    assert runner.state["status"] == "ready" and runner.state["decision"] is None
    assert runner.state["history"] == [{"action": "prior action"}]


def test_pause_before_prediction_makes_no_model_call(runner, monkeypatch):
    choose = Mock()
    monkeypatch.setattr(loop, "choose", choose)
    runner.cancel_event.set()
    runner.command("tick")
    choose.assert_not_called()
    runner.browser.act.assert_not_called()
    assert runner.state["status"] == "paused" and runner.state["decision"] is None


def test_pause_during_prediction_discards_decision_and_preserves_history(runner, monkeypatch):
    def choose(*_args):
        runner.cancel_event.set()
        return decision()

    monkeypatch.setattr(loop, "choose", choose)
    runner.state["history"] = [{"action": "prior action"}]
    runner.command("tick")
    runner.browser.act.assert_not_called()
    assert runner.state["status"] == "paused" and runner.state["decision"] is None
    assert runner.state["history"] == [{"action": "prior action"}]


def test_pause_during_freshness_check_stops_input(runner):
    def fresh(_page):
        runner.cancel_event.set()
        return True

    runner.browser.fresh.side_effect = fresh
    act(runner)
    runner.browser.act.assert_not_called()
    assert runner.state["status"] == "paused"


def test_pause_inside_browser_preflight_is_not_an_error(runner):
    runner.browser.act.side_effect = ActionPaused("Paused before input")
    result = act(runner)
    assert result["status"] == "paused" and result["decision"] is None
    assert result["history"] == []
    assert "error" not in result


def test_paused_run_can_resume_with_fresh_prediction(runner, monkeypatch):
    runner.state["status"] = "paused"
    runner.state["decision"] = None
    monkeypatch.setattr(loop, "choose", Mock(return_value=decision("e3")))
    runner.command("tick")
    runner.browser.act.assert_called_once()
    assert runner.state["status"] == "ready"


def test_done_requires_fresh_page(runner):
    runner.state["decision"] = decision("DONE")
    runner.browser.fresh.return_value = False
    with pytest.raises(StalePage):
        act(runner)
    assert runner.state["status"] != "done"


def test_zero_action_blocked_run_exposes_reason_and_consumed_decision(runner):
    runner.state["decision"] = {**decision("BLOCKED"), "stop_reason": "sign_in_required"}
    result = act(runner)
    assert result["status"] == "blocked"
    assert result["history"] == []
    assert "Sign in" in result["stop_reason"]
    assert result["last_decision"]["operation"] == "BLOCKED"
    assert result["decision"] is None
    runner.browser.act.assert_not_called()


def test_missing_address_can_be_supplied_without_losing_progress(runner):
    runner.state["decision"] = {**decision("NEEDS_INPUT"), "stop_reason": "missing_address"}
    act(runner)
    assert "web address" in runner.state["input_request"]
    runner.command("supply_values", {"values": ["https://example.org/"]})
    runner.browser.set_navigation_urls.assert_called_once_with(["https://example.org/"])
    assert runner.state["status"] == "ready"
    assert runner.state["stop_reason"] is None
    assert runner.state["last_decision"] is None


def test_action_budget_stops_without_mutation_and_explains(runner):
    runner.state["max_steps"] = 1
    runner.state["history"] = [{"action": "prior action"}]
    result = act(runner)
    runner.browser.act.assert_not_called()
    assert result["status"] == "blocked"
    assert "1-action limit" in result["stop_reason"]


def test_decision_budget_stops_tick_without_act_or_extra_request(runner, monkeypatch):
    runner.state["max_steps"] = 1
    runner.state["decisions"] = [decision(), decision()]
    choose = Mock()
    monkeypatch.setattr(loop, "choose", choose)
    result = runner.command("tick")
    choose.assert_not_called()
    runner.browser.act.assert_not_called()
    assert result["status"] == "blocked"
    assert "model-decision limit" in result["stop_reason"]


@pytest.mark.parametrize("limit", [0, 501, True, 1.5, "200"])
def test_invalid_budget_rejected_before_browser_connect(monkeypatch, limit):
    browser = Mock()
    monkeypatch.setattr(loop, "Browser", browser)
    with pytest.raises(ValueError, match="action limit"):
        loop.Agent(None, "Browse", target_id="tab", max_steps=limit)
    browser.assert_not_called()


def test_browser_target_decision_executes_observed_url_not_model_text(runner):
    action = {"id": "open_url:1", "kind": "open_url", "label": "Open example", "url": "https://example.org/"}
    runner.state["page"]["actions"].append(action)
    runner.state["decision"] = {
        **decision(action["id"]), "operation": "OPEN_URL", "target": action["id"],
        "text": "https://untrusted.invalid/",
    }
    act(runner)
    runner.browser.act.assert_called_once_with(action, runner.state["page"], text=None)
    assert runner.state["history"][-1]["operation"] == "OPEN_URL"


@pytest.mark.parametrize("destinations,next_url", [
    (["https://a.test/"], "https://a.test/"),
    (["https://a.test/", "https://b.test/", "https://a.test/"], "https://b.test/"),
    (["https://a.test/", "https://b.test/", "https://c.test/"] * 2, "https://a.test/"),
])
def test_navigation_cycle_detects_repeated_destinations(destinations, next_url):
    history = [{"kind": "switch_tab", "navigation_destination": url} for url in destinations]
    assert loop.navigation_cycle(history, {"kind": "switch_tab", "url": next_url})


def test_distinct_site_visits_and_useful_work_are_not_navigation_cycles():
    history = [{"kind": "switch_tab", "navigation_destination": "https://a.test/"}]
    assert not loop.navigation_cycle(history, {"kind": "open_url", "url": "https://b.test/"})
    history.append({"kind": "click"})
    assert not loop.navigation_cycle(history, {"kind": "switch_tab", "url": "https://a.test/"})


def test_wait_does_not_hide_navigation_cycle():
    history = [{"kind": "open_url", "navigation_destination": "https://a.test/"}, {"kind": "wait"}]
    assert loop.navigation_cycle(history, {"kind": "open_url", "url": "https://a.test/"})


def test_reproduced_alternating_tab_loop_stops_before_fourth_switch(runner):
    runner.state["history"] = [
        {"kind": "switch_tab", "navigation_destination": f"https://{host}.test/"}
        for host in ["a", "b", "a"]
    ]
    action = {
        "id": "switch_tab:b", "kind": "switch_tab", "label": "Switch to B",
        "url": "https://b.test/", "target_id": "b",
    }
    runner.state["page"]["actions"].append(action)
    runner.state["decision"] = {**decision(action["id"]), "operation": "SWITCH_TAB", "target": action["id"]}
    result = act(runner)
    runner.browser.act.assert_not_called()
    assert result["status"] == "blocked"
    assert "navigation cycle" in result["stop_reason"]
    assert len(result["history"]) == 3
    assert result["decision"] is None


def test_scope_boundary_stops_before_model_request(runner, monkeypatch):
    runner.state["page"]["scope_blocked"] = "This page is outside the task's allowed sites."
    choose = Mock()
    monkeypatch.setattr(loop, "choose", choose)
    result = runner.command("tick")
    choose.assert_not_called()
    runner.browser.act.assert_not_called()
    assert result["status"] == "blocked"
    assert result["stop_reason"] == runner.state["page"]["scope_blocked"]


def test_other_tab_metadata_is_not_progress():
    first = page()
    second = {**first, "target_id": "different-tab", "browser_context": {"tabs": ["new title"]}}
    second["actions"] = [*first["actions"], {"id": "switch_tab:x", "kind": "switch_tab", "url": "https://x.test/"}]
    assert loop.content_fingerprint(first) == loop.content_fingerprint(second)


def test_pause_while_reading_page_prevents_new_model_request(runner, monkeypatch):
    def fresh(_page):
        runner.cancel_event.set()
        return True

    runner.browser.fresh.side_effect = fresh
    choose = Mock()
    monkeypatch.setattr(loop, "choose", choose)
    result = runner.command("tick")
    choose.assert_not_called()
    runner.browser.act.assert_not_called()
    assert result["status"] == "paused"
