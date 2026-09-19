"""Audits distinguish a model choice, a discarded preview, and a dispatched action."""

from copy import deepcopy
from threading import Event
from unittest.mock import Mock

import pytest
from test_agent import decision, page

from ctrl.server import Controller
from jev_ultrafast.agent import Agent
from jev_ultrafast.browser import StalePage
from jev_ultrafast.trace import TraceStore


def audited_decision():
    result = decision("e3")
    result["audit"] = {
        "questions": [{"id": "operation", "options": [
            {"id": "CLICK", "probability": .8, "selected": True, "used": True, "allowed": None},
            {"id": "WAIT", "probability": .2, "selected": False, "used": False, "allowed": None},
        ]}],
        "selected": {"operation": "CLICK", "action_id": "e3", "target_id": "2", "target_label": "Go"},
        "model": "typesafe/jev-1.13", "latency_ms": 10, "request_bytes": 1234,
    }
    return result


@pytest.fixture
def agent(monkeypatch):
    from jev_ultrafast import agent as module

    fake = Mock(observe=Mock(return_value=page()), fresh=Mock(return_value=True))
    monkeypatch.setattr(module, "Browser", Mock(return_value=fake))
    monkeypatch.setattr(module, "choose", Mock(side_effect=lambda *_args: deepcopy(audited_decision())))
    return Agent(None, "Find a book", target_id="synthetic", cancel_event=Event())


def latest(agent):
    return agent.trace_store().get(agent.trace_state()["latest_trace_id"])


def test_preview_keeps_probabilities_but_no_execution_or_permission_claim(agent):
    result = agent.command("predict")
    trace = latest(agent)
    assert result["history"] == []
    assert "audit" not in result["decision"]
    assert trace["observation"]["text"] == "Search"
    assert trace["execution"]["status"] == "pending"
    assert all(option["allowed"] is None for option in trace["questions"][0]["options"])
    assert "questions" not in result["trace_summaries"][0]


def test_actual_action_marks_only_used_branch_and_survives_later_cancel(agent):
    agent.command("tick")
    trace = latest(agent)
    assert trace["execution"]["status"] == "executed"
    assert trace["questions"][0]["options"][0]["allowed"] is True
    assert trace["questions"][0]["options"][1]["allowed"] is None
    agent.cancel_run()
    assert latest(agent) == trace
    assert len(agent.state["history"]) == 1


def test_stale_preflight_rejects_without_dispatch_or_replay(agent):
    agent.command("predict")
    agent.browser.act.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})
    trace = latest(agent)
    assert trace["validation"]["status"] == "stale"
    assert trace["execution"]["status"] == "rejected"
    assert trace["questions"][0]["options"][0]["allowed"] is False
    assert agent.state["decision"] is None and agent.state["history"] == []


def test_post_dispatch_observation_failure_does_not_relabel_executed_action(agent):
    agent.browser.observe.side_effect = StalePage("changed after click")
    with pytest.raises(StalePage):
        agent.command("tick")
    assert latest(agent)["execution"]["status"] == "executed"
    assert len(agent.state["history"]) == 1


def test_invalid_model_response_preserves_sanitized_audit(agent, monkeypatch):
    class InvalidDecision(ValueError):
        audit = audited_decision()["audit"]

    from jev_ultrafast import agent as module

    monkeypatch.setattr(module, "choose", Mock(side_effect=InvalidDecision("Invalid Jev choice")))
    with pytest.raises(InvalidDecision):
        agent.command("predict")
    assert latest(agent)["selected"]["operation"] == "CLICK"
    assert latest(agent)["execution"]["status"] == "rejected"
    agent.browser.act.assert_not_called()


def test_cancel_discards_preview_and_cannot_resume_through_any_command(agent):
    agent.command("predict")
    control = Controller()
    control.agent = agent
    control.capture()
    result = control.cancel_task()
    assert result["status"] == "cancelled"
    assert latest(agent)["execution"]["status"] == "cancelled"
    assert agent.state["decision"] is None
    for command in ("run", "step", "predict", "act", "supply_values"):
        with pytest.raises(ValueError, match="cancelled"):
            control.submit(command, {})
    agent.browser.act.assert_not_called()


def test_cancel_during_model_call_discards_returned_choice_before_browser_input(agent, monkeypatch):
    from jev_ultrafast import agent as module

    control = Controller()
    control.agent = agent
    agent.cancel_event = control.cancel
    agent.browser.cancel_event = control.cancel
    control.capture()
    control.busy = True
    control.running = True

    def choose(*_args):
        assert control.cancel_task()["status"] == "cancelling"
        return audited_decision()

    monkeypatch.setattr(module, "choose", choose)
    control.work("run", {})
    assert control.state(discover_tabs=False)["status"] == "cancelled"
    assert not control.busy and not control.running
    assert latest(agent)["execution"]["status"] == "cancelled"
    agent.browser.act.assert_not_called()


def test_trace_copies_cannot_mutate_authoritative_evidence():
    traces = TraceStore()
    observation = page()
    identifier = traces.begin(observation, [])
    audit = audited_decision()["audit"]
    traces.audit(identifier, audit)
    observation["text"] = "changed"
    audit["questions"].clear()
    copy = traces.get(identifier)
    copy["questions"].clear()
    assert traces.get(identifier)["questions"]
    assert traces.get(identifier)["observation"]["text"] == "Search"
    assert traces.get("not-found") is None


def test_late_pause_after_predict_cancels_pending_execution(agent):
    control = Controller()
    control.agent = agent
    agent.cancel_event = control.cancel
    command = agent.command

    def pause_after_command(*args):
        result = command(*args)
        control.cancel.set()
        return result

    agent.command = pause_after_command
    control.work("predict", {})
    assert latest(agent)["execution"]["status"] == "paused"
    assert agent.state["decision"] is None


def test_early_prediction_stop_disposes_previous_preview(agent):
    agent.command("predict")
    agent.state["page"]["scope_blocked"] = "Outside allowed sites"
    agent.command("predict")
    assert latest(agent)["execution"]["status"] == "cancelled"
    assert agent.state["status"] == "blocked"


def test_last_decision_keeps_source_fingerprint_after_navigation(agent):
    source = agent.state["page"]["fingerprint"]
    destination = {**page(), "url": "https://example.test/result", "fingerprint": "destination"}
    agent.browser.observe.return_value = destination
    agent.command("tick")
    control = Controller()
    control.agent = agent
    control.capture()
    assert control.view["last_decision"]["fingerprint"] == source
    assert control.view["page"]["fingerprint"] == "destination"
