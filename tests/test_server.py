import json
import re
import threading
import urllib.error
import urllib.request

import pytest

from ctrl.server import Controller, make_server


@pytest.fixture
def server(monkeypatch, tmp_path):
    import ctrl.server as app

    (tmp_path / 'index.html').write_text('<meta name="ctrl-token" content="__TOKEN__">')
    monkeypatch.setattr(app, 'STATIC', tmp_path)
    monkeypatch.setattr(app.browser_connection, 'status', lambda: {'status': 'disconnected'})
    live = make_server(0)
    thread = threading.Thread(target=live.serve_forever, daemon=True)
    thread.start()
    yield live
    live.shutdown()
    live.server_close()


def request(server, path, token=None, body=None, headers=None):
    hdrs = dict(headers or {})
    if token:
        hdrs['X-CTRL-Token'] = token
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}', data=payload, headers=hdrs)
    # Unit tests must never use the account/network's configured HTTP proxy.
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=2)


def test_task_data_and_mutations_require_token(server):
    for path, body in [('/api/state', None), ('/api/control', None), ('/api/start', {'goal': 'x'}),
                       ('/api/traces/example', None), ('/api/cancel', {})]:
        with pytest.raises(urllib.error.HTTPError) as error:
            request(server, path, body=body)
        assert error.value.code == 403


def test_same_origin_and_host_enforced(server):
    html = request(server, '/').read().decode()
    token = re.search('content="([^"]+)"', html)[1]
    with request(server, '/api/state', token) as response:
        assert json.load(response)['status'] == 'idle'
    for headers in [{'Origin': 'https://evil.example'}, {'Host': 'evil.example'}]:
        with pytest.raises(urllib.error.HTTPError) as error:
            request(server, '/api/state', token, headers=headers)
        assert error.value.code == 403


def test_path_escape_rejected(server):
    with pytest.raises(urllib.error.HTTPError) as error:
        request(server, '/../pyproject.toml')
    assert error.value.code == 404


def test_pause_does_not_wait_for_worker():
    control = Controller()
    control.busy = True
    control.running = True
    control.view['status'] = 'ready'
    state = control.pause()
    assert control.cancel.is_set()
    assert state['running'] is False
    assert state['status'] == 'pausing'


def test_second_command_cannot_double_execute():
    control = Controller()
    control.busy = True
    with pytest.raises(RuntimeError, match='already in progress'):
        control.submit('connect', {})


def test_start_validates_user_values():
    with pytest.raises(ValueError):
        Controller.validate_start({'goal': 'search', 'target_id': 'tab', 'values': ['']})


@pytest.mark.parametrize("limit", [0, 501, True, None, "200"])
def test_start_rejects_invalid_action_budget(limit):
    with pytest.raises(ValueError, match="action limit"):
        Controller.validate_start({"goal": "browse", "target_id": "tab", "max_steps": limit})


def test_start_accepts_longer_explicit_budget():
    Controller.validate_start({"goal": "browse", "target_id": "tab", "max_steps": 500})


def test_control_panel_endpoint_has_no_task_or_page_contents(server):
    server.control.view.update(
        goal="private task", page={"url": "https://example.org/private?q=secret", "text": "private body"},
    )
    token = re.search('content="([^\"]+)"', request(server, '/').read().decode())[1]
    result = json.load(request(server, '/api/control', token))
    assert set(result) == {"status", "running", "busy", "action_count", "current_host"}
    assert result["current_host"] == "example.org"
    assert "private" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


def test_emergency_stop_never_waits_for_tab_discovery_and_cannot_be_resumed(monkeypatch):
    from unittest.mock import Mock

    from ctrl import server as module

    monkeypatch.setattr(module.browser_connection, "status", lambda: {"connected": True})
    discover = Mock(side_effect=AssertionError("Stop must never discover tabs"))
    monkeypatch.setattr(module.browser_connection, "list_tabs", discover)
    control = Controller()
    control.running = True
    control.busy = True
    result = control.stop()
    assert result["status"] == "stopping"
    assert not result["running"]
    assert control.cancel.is_set() and control.shutdown.is_set()
    discover.assert_not_called()
    control.busy = False
    with pytest.raises(RuntimeError, match="agent is off"):
        control.submit("credentials", {})


def test_trace_endpoint_not_found_and_cancel_keeps_service_alive(server):
    token = re.search('content="([^"]+)"', request(server, '/').read().decode())[1]
    with pytest.raises(urllib.error.HTTPError) as error:
        request(server, '/api/traces/missing', token)
    assert error.value.code == 404
    assert json.load(request(server, '/api/cancel', token, {}))["status"] == "cancelled"
    assert json.load(request(server, '/health'))["status"] == "ok"


def test_targetless_start_requires_an_explicit_destination_or_known_site():
    for task in ("Open YouTube and search for NASA", "Open https://example.org/"):
        Controller.validate_start({"goal": task})
    with pytest.raises(ValueError, match="destination website"):
        Controller.validate_start({"goal": "Click the search button"})
    with pytest.raises(ValueError, match="supplied value"):
        Controller.validate_start({"goal": "Open YouTube", "values": [None]})
    with pytest.raises(ValueError):
        Controller.validate_start({"goal": "Open http://127.0.0.1:8766/"})
