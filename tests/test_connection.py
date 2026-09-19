import asyncio
import threading

import pytest

from ctrl import browser_connection as module


def test_endpoint_only_reads_normal_chrome_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "chrome_profile", lambda: tmp_path)
    monkeypatch.setenv("BU_CDP_WS", "wss://some-remote-browser.example")
    with pytest.raises(RuntimeError, match="normal Google Chrome"):
        module.chrome_endpoint()
    (tmp_path / "DevToolsActivePort").write_text("12345\n/devtools/browser/session-id\n")
    assert module.chrome_endpoint() == "ws://127.0.0.1:12345/devtools/browser/session-id"


@pytest.mark.parametrize(
    "value", ["0\n/devtools/browser/id", "9333\n//evil.example/ws", "9333\n/devtools/browser/id?x=1"]
)
def test_invalid_endpoint_is_rejected(monkeypatch, tmp_path, value):
    monkeypatch.setattr(module, "chrome_profile", lambda: tmp_path)
    (tmp_path / "DevToolsActivePort").write_text(value)
    with pytest.raises(RuntimeError):
        module.chrome_endpoint()


def test_tab_filter_hides_browser_internals_and_ctrl_console(monkeypatch):
    monkeypatch.setenv("CTRL_PORT", "8765")
    for url in (
        "chrome://inspect",
        "about:blank",
        "file:///private",
        "http://127.0.0.1:8765/",
        "http://localhost:8765/",
        "http://localhost.:8765/",
        "http://127.0.0.2:8765/",
        "http://[::1]:8765/",
        "http://app.localhost:8765/",
    ):
        assert not module.selectable_tab({"type": "page", "url": url})
    assert not module.selectable_tab({"type": "service_worker", "url": "https://example.com"})
    assert module.selectable_tab({"type": "page", "url": "https://example.com"})
    assert module.selectable_tab({"type": "page", "url": "http://127.0.0.1:8999/fixture"})


class FakeClient:
    instances = []
    approved = threading.Event()

    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.calls = []
        self.stopped = False
        self.instances.append(self)

    async def start(self):
        while not self.approved.is_set():
            await asyncio.sleep(0.001)

    async def stop(self):
        self.stopped = True

    async def send_raw(self, method, params=None, session_id=None):
        self.calls.append((method, params, session_id))
        if method == "Browser.getVersion":
            return {"product": "Chrome/147.0.0.0"}
        if method == "Input.dispatchMouseEvent":
            await asyncio.sleep(10)
        return {}


@pytest.fixture
def connection(monkeypatch):
    FakeClient.instances = []
    FakeClient.approved.clear()
    monkeypatch.setattr(module, "_harness_client", FakeClient)
    monkeypatch.setattr(module, "chrome_endpoint", lambda: "ws://127.0.0.1:12345/devtools/browser/id")
    conn = module.ChromeConnection()
    yield conn
    conn.disconnect()
    if conn._loop is not None:
        conn._loop.call_soon_threadsafe(conn._loop.stop)
        conn._thread.join(timeout=2)
        conn._loop.close()


def test_pending_approval_reuses_same_handshake_and_never_attaches_a_tab(connection):
    assert connection.connect(wait=0.02)["state"] == "waiting_for_approval"
    assert connection.connect(wait=0.02)["state"] == "waiting_for_approval"
    assert len(FakeClient.instances) == 1
    FakeClient.approved.set()
    assert connection.connect(wait=1)["connected"]
    assert [call[0] for call in FakeClient.instances[0].calls] == ["Browser.getVersion"]


def test_timed_out_mutation_is_sent_exactly_once(connection):
    FakeClient.approved.set()
    assert connection.connect(wait=1)["connected"]
    with pytest.raises(RuntimeError, match="did not confirm"):
        connection.cdp("Input.dispatchMouseEvent", session_id="s", type="mousePressed", _response_timeout=0.01)
    assert [call[0] for call in FakeClient.instances[0].calls].count("Input.dispatchMouseEvent") == 1


def test_disconnect_stops_transport_without_closing_targets(connection):
    FakeClient.approved.set()
    connection.connect(wait=1)
    connection.disconnect()
    client = FakeClient.instances[0]
    assert client.stopped
    assert [call[0] for call in client.calls] == ["Browser.getVersion"]
