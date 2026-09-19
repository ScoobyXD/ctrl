"""HTTP trace contracts using only a disposable loopback server and synthetic data."""

import json
import re
import urllib.error
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_server import request
from test_server import server as server

from jev_ultrafast.trace import TraceStore

PRIVATE = "private-synthetic-sentinel-never-publish"


def synthetic_page():
    return {
        "url": "https://example.test/catalog", "title": "Synthetic catalog", "text": "Search\nCatalog",
        "fingerprint": "synthetic-fingerprint", "target_id": "synthetic-tab",
        "task_browser_scope": {"mode": "selected_tab", "allowed_hosts": ["example.test"]},
        "actions": [
            {"id": "e1", "kind": "click", "node": 1, "label": "Search", "role": "button"},
            {"id": "e2", "kind": "fill", "node": 2, "label": "Query", "role": "textbox", "value": ""},
        ],
        "marker": [PRIVATE], "page_key": [[PRIVATE]], "guards": {"1": [PRIVATE]},
        "unrelated_private_data": {"tab": PRIVATE}, "raw_request": PRIVATE,
    }


def option(identifier, label, probability, selected=False, used=False):
    return {
        "id": identifier, "label": label, "probability": probability,
        "selected": selected, "used": used, "allowed": None,
        "validation_note": "Browser validation pending" if used else "Not evaluated",
    }


def synthetic_audit():
    return {
        "questions": [
            {"id": "operation", "label": "Next operation", "applies": True,
             "selected_id": "CLICK", "confidence": .9, "validation": "valid", "options": [
                 option("CLICK", "Click an observed control", .8, True, True),
                 option("WAIT", "Wait for the page", .2),
             ]},
            {"id": "click_target", "label": "Click target", "applies": True,
             "selected_id": "1", "confidence": .95, "validation": "valid", "options": [
                 option("1", "Search", 1., True, True),
             ]},
            {"id": "type_text_target", "label": "Text target", "applies": False,
             "selected_id": "2@v1", "confidence": .7, "validation": "ignored", "options": [
                 option("2@v1", "Query: exact synthetic value", 1., True),
             ]},
        ],
        "selected": {"operation": "CLICK", "action_id": "e1", "target_id": "1", "target_label": "Search"},
        "model": "typesafe/jev-1.13", "latency_ms": 17, "request_bytes": 1234,
        "raw_request": PRIVATE, "raw_answers": PRIVATE, "credential": PRIVATE,
    }


class SyntheticAgent:
    """The HTTP controller remains real; browser/model/credential work is absent."""

    def __init__(self, _url=None, goal="Inspect the synthetic catalog", *, target_id="synthetic-tab",
                 values=None, max_steps=20, cancel_event=None):
        self.browser = SimpleNamespace(target=target_id)
        self.closed = False
        self.traces = TraceStore()
        self.current_trace = None
        self.state = {
            "status": "ready", "goal": goal, "page": synthetic_page(), "history": [],
            "decision": None, "last_decision": None, "values": values or [], "max_steps": max_steps,
        }

    def seed_trace(self):
        self.current_trace = self.traces.begin(self.state["page"], [
            {"index": "1", "label": "Search", "operations": ["CLICK"]},
            {"index": "2", "label": "Query", "operations": ["TYPE_TEXT"]},
        ])
        self.traces.audit(self.current_trace, synthetic_audit())
        self.state["status"] = "predicted"
        self.state["decision"] = {
            "operation": "CLICK", "choice": "e1", "target": "1", "confidence": .9,
            "request": PRIVATE, "raw_answers": PRIVATE, "audit": {"private": PRIVATE},
        }
        self.state["decisions"] = [{"request": PRIVATE}]
        return self.current_trace

    def snapshot(self):
        return {**deepcopy(self.state), **self.trace_state()}

    def trace_store(self):
        return self.traces

    def trace_state(self):
        summaries = self.traces.summaries()
        return {"trace_summaries": summaries, "latest_trace_id": summaries[-1]["id"] if summaries else None}

    def finish_trace(self, execution, message, validation=None):
        self.traces.finish(self.current_trace, execution, message, validation)

    def cancel_run(self):
        self.finish_trace("cancelled", "Task cancelled; preview discarded")
        self.state.update(status="cancelled", decision=None)

    def close(self):
        self.closed = True

    def command(self, *_args):
        raise AssertionError("This test must not dispatch an agent command")


@pytest.fixture
def trace_api(server, monkeypatch):
    from ctrl import server as server_module
    from jev_ultrafast import agent as agent_module

    def forbidden(*_args, **_kwargs):
        raise AssertionError("This test must not use a browser, credentials, or a model")

    monkeypatch.setattr(agent_module, "Agent", SyntheticAgent)
    monkeypatch.setattr(server_module, "check_openrouter", forbidden)
    monkeypatch.setattr(server_module.browser_connection, "connect", forbidden)
    monkeypatch.setattr(server_module.browser_connection, "list_tabs", forbidden)
    agent = SyntheticAgent()
    server.control.agent = agent
    server.control.capture()
    with request(server, "/") as response:
        token = re.search('name="ctrl-token" content="([^"]+)"', response.read().decode())[1]
    yield SimpleNamespace(server=server, agent=agent, token=token)
    if server.control.worker:
        server.control.worker.join(timeout=2)
        assert not server.control.worker.is_alive()


def get_json(api, path):
    with request(api.server, path, api.token) as response:
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        return json.load(response)


def post_json(api, path, body=None):
    with request(api.server, path, api.token, {} if body is None else body) as response:
        assert response.status == 202
        return json.load(response)


def test_authenticated_trace_and_state_omit_private_worker_data(trace_api):
    api = trace_api
    identifier = api.agent.seed_trace()
    api.server.control.capture()
    detail = get_json(api, f"/api/traces/{identifier}")
    state = get_json(api, "/api/state")
    assert detail["id"] == identifier
    assert detail["observation"]["text"] == "Search\nCatalog"
    assert detail["observation"]["actions"][0]["id"] == "e1"
    assert detail["observation"]["elements"][0]["label"] == "Search"
    assert state["latest_trace_id"] == identifier
    assert state["trace_summaries"][0]["target_label"] == "Search"
    assert "questions" not in state["trace_summaries"][0]
    assert state["decision"]["fingerprint"] == "synthetic-fingerprint"
    for payload in (detail, state):
        rendered = json.dumps(payload, allow_nan=False)
        assert PRIVATE not in rendered
        assert all(key not in payload.get("observation", payload.get("page", {})) for key in (
            "marker", "page_key", "guards", "unrelated_private_data", "raw_request",
        ))
    # Publishing a safe view must not delete the authoritative freshness evidence.
    assert api.agent.state["page"]["guards"] == {"1": [PRIVATE]}


@pytest.mark.parametrize("phase,validation,allowed", [
    ("pending", None, None), ("executed", "passed", True), ("rejected", "stale", False),
])
def test_http_trace_preserves_answered_used_and_allowed_semantics(trace_api, phase, validation, allowed):
    api = trace_api
    identifier = api.agent.seed_trace()
    if validation:
        api.agent.finish_trace(phase, "Synthetic validation result", validation)
    detail = get_json(api, f"/api/traces/{identifier}")
    questions = {question["id"]: question for question in detail["questions"]}
    operation = questions["operation"]["options"]
    selected_target = questions["click_target"]["options"][0]
    ignored_target = questions["type_text_target"]["options"][0]
    assert detail["execution"]["status"] == phase
    assert operation[0]["probability"] == .8 and operation[1]["probability"] == .2
    for selected in (operation[0], selected_target):
        assert selected["selected"] and selected["used"]
        assert selected["allowed"] is allowed
    assert not operation[1]["selected"] and not operation[1]["used"] and operation[1]["allowed"] is None
    assert ignored_target["probability"] == 1. and ignored_target["selected"]
    assert not ignored_target["used"] and ignored_target["allowed"] is None
    assert not questions["type_text_target"]["applies"]
    assert questions["type_text_target"]["validation"] == "ignored"


def test_trace_detail_requires_authentication_and_unknown_id_is_404(trace_api):
    api = trace_api
    identifier = api.agent.seed_trace()
    for token in (None, "incorrect-token"):
        with pytest.raises(urllib.error.HTTPError) as error:
            request(api.server, f"/api/traces/{identifier}", token)
        assert error.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as error:
        request(api.server, "/api/traces/unknown-trace", api.token)
    assert error.value.code == 404


@pytest.mark.parametrize("executed", [False, True])
def test_http_cancel_preserves_trace_and_rejects_resume(trace_api, executed):
    api = trace_api
    identifier = api.agent.seed_trace()
    if executed:
        api.agent.finish_trace("executed", "Synthetic action dispatched", "passed")
    api.server.control.capture()
    before = get_json(api, f"/api/traces/{identifier}")
    assert post_json(api, "/api/cancel")["status"] == "cancelled"
    after = get_json(api, f"/api/traces/{identifier}")
    assert after["questions"] == before["questions"]
    assert after["observation"] == before["observation"]
    assert after["execution"]["status"] == ("executed" if executed else "cancelled")
    assert get_json(api, "/api/state")["latest_trace_id"] == identifier
    with pytest.raises(urllib.error.HTTPError) as error:
        request(api.server, "/api/run", api.token, {})
    assert error.value.code == 400
    assert "cancelled" in json.load(error.value)["error"]
    assert get_json(api, "/health")["status"] == "ok"


@pytest.mark.parametrize("command", ["reset", "start"])
def test_http_reset_or_new_start_replaces_previous_task_traces(trace_api, command):
    api = trace_api
    identifier = api.agent.seed_trace()
    api.server.control.capture()
    body = {"goal": "Inspect a new synthetic catalog", "target_id": "synthetic-tab", "run": False}
    post_json(api, f"/api/{command}", body if command == "start" else {})
    api.server.control.worker.join(timeout=2)
    assert not api.server.control.worker.is_alive()
    state = get_json(api, "/api/state")
    assert not state["busy"] and state["error"] is None
    assert state["trace_summaries"] == [] and state["latest_trace_id"] is None
    assert api.agent.closed
    with pytest.raises(urllib.error.HTTPError) as error:
        request(api.server, f"/api/traces/{identifier}", api.token)
    assert error.value.code == 404
    if command == "start":
        assert api.server.control.agent is not api.agent
        new_identifier = api.server.control.agent.seed_trace()
        assert new_identifier != identifier
        assert get_json(api, f"/api/traces/{new_identifier}")["id"] == new_identifier
    else:
        assert api.server.control.agent is None and state["status"] == "idle"
