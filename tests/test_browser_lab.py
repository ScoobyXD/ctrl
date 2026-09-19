"""Fixture validation only; these tests never connect to Chrome or Jev."""

import re
import threading
import urllib.error
import urllib.request

import pytest

from scripts import browser_lab as lab


@pytest.fixture
def local_lab():
    server = lab.make_lab_server(port=0)
    server.lab_state.reset(message_count=12, page_size=5)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = lab.LocalHttp(f"http://127.0.0.1:{server.server_port}")
    yield server, client
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def render_token(content):
    return re.search("data-view-token='([^']+)'", content)[1]


def test_server_binds_only_loopback_and_rejects_foreign_host_origin(local_lab):
    server, client = local_lab
    assert server.server_address[0] == "127.0.0.1"
    assert client.request("/health")["status"] == "ok"
    for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}):
        request = urllib.request.Request(client.base_url + "/health", headers=headers)
        with pytest.raises(urllib.error.HTTPError) as error:
            client.opener.open(request, timeout=2)
        assert error.value.code == 403


@pytest.mark.parametrize("url", ["https://example.com", "http://0.0.0.0:8877", "http://user:pass@127.0.0.1:8877"])
def test_driver_client_cannot_contact_non_loopback_or_credentialed_endpoints(url):
    with pytest.raises(ValueError, match="127.0.0.1"):
        lab.LocalHttp(url)


def test_catalog_requires_filters_and_rendered_target_detail(local_lab):
    _, client = local_lab
    initial = client.request("/catalog")
    assert "<select name='department'>" in initial
    assert not lab.validate_result("catalog", client.request("/validation"))
    results = client.request("/catalog?searched=1&q=trail&department=outdoors&stock=1")
    assert "<h2>1 products</h2>" in results
    assert "Alpine Trail Mug" in results and "Trail Map Case" not in results
    details = client.request("/catalog/item/alpine-trail-mug")
    assert not lab.validate_result("catalog", client.request("/validation"))
    client.request("/api/view", {"nonce": render_token(details)})
    evidence = client.request("/validation")
    assert lab.validate_result("catalog", evidence)
    assert evidence["catalog"]["submissions"] == [{"query": "trail", "department": "outdoors", "stock_only": True}]


def test_user_query_is_html_escaped(local_lab):
    _, client = local_lab
    content = client.request("/catalog?q=%27%3E%3Cscript%3Ebad%3C%2Fscript%3E")
    assert "<script>bad</script>" not in content
    assert "&lt;script&gt;bad&lt;/script&gt;" in content


def test_pagination_and_message_body_counts_are_independent_of_page_fetches(local_lab):
    _, client = local_lab
    for page_number, rows in [(1, 5), (2, 5), (3, 2)]:
        content = client.request(f"/inbox?page={page_number}")
        assert len(re.findall(r"href='/inbox/message/", content)) == rows
    first = client.request("/inbox/message/1")
    assert "Evidence code: M001-7919" in first
    evidence = client.request("/validation")["inbox"]
    assert evidence["opened_unique"] == 1 and evidence["body_view_unique"] == 0
    client.request("/api/view", {"nonce": render_token(first)})
    again = client.request("/inbox/message/1")
    client.request("/api/view", {"nonce": render_token(again)})
    evidence = client.request("/validation")
    assert evidence["inbox"]["list_pages"] == [1, 2, 3]
    assert evidence["inbox"]["body_view_unique"] == 1
    assert not lab.validate_result("inbox-small", evidence)
    assert "1 read" in client.request("/inbox")


def test_all_messages_and_all_list_pages_are_required_for_inbox_validation():
    state = lab.LabState()
    state.reset(message_count=100, page_size=10)
    state.message_opened.update(range(1, 101))
    state.message_viewed.update(range(1, 100))
    state.inbox_pages.update(range(1, 11))
    assert not lab.validate_result("inbox-100", state.validation())
    state.message_viewed.add(100)
    assert lab.validate_result("inbox-100", state.validation())
    state.inbox_pages.remove(10)
    assert not lab.validate_result("inbox-100", state.validation())


def test_reset_invalidates_old_view_nonces_and_clears_evidence(local_lab):
    _, client = local_lab
    token = render_token(client.request("/inbox/message/1"))
    session = client.request("/validation")["session"]
    client.request("/api/reset", {"message_count": 100, "page_size": 10})
    with pytest.raises(urllib.error.HTTPError) as error:
        client.request("/api/view", {"nonce": token})
    assert error.value.code == 400
    evidence = client.request("/validation")
    assert evidence["session"] != session
    assert evidence["inbox"]["opened_unique"] == evidence["inbox"]["body_view_unique"] == 0


def test_smoke_cli_requires_explicit_run_flag(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "argv", ["browser_lab.py", "smoke"])
    monkeypatch.setattr(lab, "run_smoke", lambda *_: pytest.fail("Agent must not start without --run"))
    with pytest.raises(SystemExit) as error:
        lab.main()
    assert error.value.code == 2


def test_smoke_refuses_to_interrupt_existing_operation(monkeypatch):
    calls = []

    class BusyApp:
        def __init__(self, *_):
            pass

        def authenticate_app(self):
            pass

        def request(self, path, body=None):
            calls.append((path, body))
            assert path == "/api/state"
            return {"busy": True}

    monkeypatch.setattr(lab, "LocalHttp", BusyApp)
    with pytest.raises(RuntimeError, match="already busy"):
        lab.run_smoke("catalog")
    assert calls == [("/api/state", None)]


@pytest.mark.parametrize("poll_error", [False, True])
def test_driver_uses_plain_goal_and_independent_evidence_and_pauses_on_failure(monkeypatch, poll_error):
    calls = []
    counts = {"state": 0}
    evidence = lab.LabState().validation()

    class FakeClient:
        def __init__(self, url):
            self.url = url

        def authenticate_app(self):
            pass

        def request(self, path, body=None):
            calls.append((self.url, path, body))
            if path == "/api/state":
                counts["state"] += 1
                if counts["state"] == 1:
                    return {"busy": False, "browser": {"connected": True}}
                if poll_error:
                    raise ConnectionError("Connection lost while observing the run")
                return {"busy": False, "status": "done", "history": [{"action": "mock"}]}
            if path == "/api/tabs":
                return {"tabs": [{"target_id": "ordinary-tab", "url": "https://example.com"}]}
            if path == "/validation":
                return evidence
            assert path in {"/api/reset", "/api/start", "/api/pause"}
            return {}

    monkeypatch.setattr(lab, "LocalHttp", FakeClient)
    monkeypatch.setattr(lab.time, "sleep", lambda *_: None)
    if poll_error:
        with pytest.raises(ConnectionError, match="Connection lost"):
            lab.run_smoke("catalog")
        assert calls[-1] == (lab.APP_URL, "/api/pause", {})
    else:
        result = lab.run_smoke("catalog")
        assert result["agent_status"] == "done"
        assert result["validated"] is False
        assert result["actions"] == 1
        assert not any(path == "/api/pause" for _, path, _ in calls)
    starts = [body for _, path, body in calls if path == "/api/start"]
    assert len(starts) == 1
    assert starts[0]["goal"].startswith(f"Open {lab.LAB_URL}/catalog.")
    assert starts[0]["target_id"] is None
    assert set(starts[0]) == {"goal", "target_id", "values", "run", "max_steps"}


def test_local_driver_never_follows_redirects():
    handler = lab.NoRedirect()
    with pytest.raises(RuntimeError, match="does not follow HTTP redirects"):
        handler.redirect_request(None, None, 302, "Found", {}, "https://example.com")


def test_rejected_start_does_not_pause_another_run(monkeypatch):
    calls = []

    class RejectStart:
        def __init__(self, *_):
            pass

        def authenticate_app(self):
            pass

        def request(self, path, body=None):
            calls.append(path)
            if path == "/api/state":
                return {"busy": False, "browser": {"connected": True}}
            if path == "/api/start":
                raise RuntimeError("A different run started first")
            assert path == "/api/reset"
            return {}

    monkeypatch.setattr(lab, "LocalHttp", RejectStart)
    with pytest.raises(RuntimeError, match="different run"):
        lab.run_smoke("catalog")
    assert "/api/pause" not in calls
