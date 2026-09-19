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
    for path, body in [('/api/state', None), ('/api/start', {'goal': 'x'})]:
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
