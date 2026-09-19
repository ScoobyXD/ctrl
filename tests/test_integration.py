"""Controller lifecycle regressions; no real browser, account, or network access."""

import threading
from types import SimpleNamespace

import pytest

from ctrl.credentials import CredentialError
from ctrl.server import Controller


class StubAgent:
    def __init__(self, status="predicted"):
        self.browser = SimpleNamespace(target="chosen-tab")
        self.state = {
            "status": status,
            "goal": "Existing task",
            "page": {"fingerprint": "page-1", "actions": []},
            "history": [],
            "decision": {"fingerprint": "page-1", "operation": "CLICK", "choice": "e1"},
        }
        self.closed = False

    def snapshot(self):
        return dict(self.state)

    def close(self):
        self.closed = True


@pytest.fixture
def control(monkeypatch):
    from ctrl import server

    monkeypatch.setattr(server.browser_connection, "status", lambda: {"connected": False, "status": "disconnected"})
    return Controller()


def test_idle_pause_discards_previously_chosen_action(control):
    control.agent = StubAgent()
    control.capture()
    snapshot = control.pause()
    assert snapshot["status"] == "paused"
    assert snapshot["decision"] is None
    assert control.agent.state["status"] == "paused"
    assert control.agent.state["decision"] is None


def test_credential_failure_publishes_agent_error_instead_of_old_prediction(control):
    control.agent = StubAgent()
    control.capture()

    def command(*_args):
        control.agent.state.update(status="error", decision=None)
        raise CredentialError("AWS sign-in expired. Run: aws sso login --profile test")

    control.agent.command = command
    control.work("step", {})
    snapshot = control.state()
    assert snapshot["status"] == "error"
    assert snapshot["decision"] is None
    assert snapshot["credentials"]["status"] == "error"
    assert snapshot["error"].startswith("AWS sign-in expired")


def test_failed_attach_does_not_leave_previous_executable_decision(control, monkeypatch):
    from jev_ultrafast import agent

    previous = StubAgent()
    control.agent = previous
    control.capture()

    def missing_tab(*_args, **_kwargs):
        raise RuntimeError("The chosen Chrome tab is no longer available.")

    monkeypatch.setattr(agent, "Agent", missing_tab)
    control.work("start", {"goal": "A new task", "target_id": "closed-tab", "values": []})
    snapshot = control.state()
    assert previous.closed
    assert control.agent is None
    assert snapshot["status"] == "error"
    assert snapshot["decision"] is None
    assert snapshot["error"] == "The chosen Chrome tab is no longer available."


def test_credential_worker_blocks_second_operation_but_pause_is_immediate(control, monkeypatch):
    from ctrl import server

    entered = threading.Event()
    release = threading.Event()

    def diagnostics():
        entered.set()
        assert release.wait(timeout=2)
        return {"status": "ready", "message": "Jev connected"}

    monkeypatch.setattr(server, "check_openrouter", diagnostics)
    control.submit("credentials", {})
    try:
        assert entered.wait(timeout=1)
        with pytest.raises(RuntimeError, match="already in progress"):
            control.submit("connect", {})
        result = control.pause()
        assert control.cancel.is_set()
        assert result["running"] is False
        assert result["busy"] is True
    finally:
        release.set()
        control.worker.join(timeout=2)
    assert control.credentials["status"] == "ready"
    assert control.busy is False


def test_unexpected_sdk_error_does_not_reveal_response_or_credentials(control, monkeypatch):
    from ctrl import server

    class ProviderError(Exception):
        pass

    def broken_check():
        raise ProviderError("Authorization: Bearer sk-or-SECRET-do-not-expose")

    monkeypatch.setattr(server, "check_openrouter", broken_check)
    control.work("credentials", {})
    snapshot = control.state()
    assert "sk-or-SECRET" not in str(snapshot)
    assert "No browser action was automatically retried" in snapshot["error"]
    assert snapshot["credentials"]["status"] == "error"
    assert snapshot["busy"] is False
