"""Activation must confirm rendering before any page input is dispatched."""

import json
import shutil
import subprocess
import threading
from unittest.mock import Mock

import pytest
from test_browser import action_browser

from jev_ultrafast import browser as module

READY = {"visible": True, "focused": True, "frames": 2}


def activation_browser(monkeypatch, states=None, window_state="normal", cancel_on=None):
    browser, page, action = action_browser(monkeypatch)
    browser.cancel_event = threading.Event()
    browser.fresh = Mock(return_value=True)
    states = iter(states or [READY])
    last = READY
    calls = []

    def cdp(method, **params):
        nonlocal last
        calls.append((method, params))
        if method == cancel_on:
            browser.cancel_event.set()
        if method == "Browser.getWindowForTarget":
            return {"windowId": 42, "bounds": {"windowState": window_state, "width": 1400, "height": 900}}
        if method == "Runtime.evaluate":
            if "__ctrlActivationReadiness" in params["expression"]:
                last = next(states, last)
                return {"result": {"value": last}}
            return {"result": {"value": {"x": 30, "y": 40}}}
        return {}

    monkeypatch.setattr(module, "cdp", cdp)
    return browser, page, action, calls


def fast_clock(monkeypatch):
    ticks = iter(index / 4 for index in range(100))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))


@pytest.mark.parametrize("window_state", ["normal", "maximized", "fullscreen", "minimized"])
def test_restore_only_exact_selected_minimized_window_without_geometry(monkeypatch, window_state):
    browser, page, action, calls = activation_browser(monkeypatch, window_state=window_state)
    browser.act(action, page)
    assert calls[0] == (
        "Browser.getWindowForTarget",
        {"targetId": "chosen-tab", "_response_timeout": 1.0},
    )
    restores = [params for method, params in calls if method == "Browser.setWindowBounds"]
    assert restores == (
        [
            {
                "windowId": 42,
                "bounds": {"windowState": "normal"},
                "_response_timeout": 1.0,
            }
        ]
        if window_state == "minimized"
        else []
    )
    assert sum(method == "Page.bringToFront" for method, _ in calls) == 1
    assert browser.fresh.call_count == 2


def test_delayed_visibility_focus_and_two_frames_precede_input(monkeypatch):
    states = [
        {"visible": False, "focused": True, "frames": 0},
        {"visible": True, "focused": False, "frames": 0},
        {"visible": True, "focused": True, "frames": 1},
        READY,
    ]
    browser, page, action, calls = activation_browser(monkeypatch, states)
    browser.act(action, page)
    reads = [
        index
        for index, (method, params) in enumerate(calls)
        if method == "Runtime.evaluate" and "__ctrlActivationReadiness" in params["expression"]
    ]
    inputs = [index for index, (method, _) in enumerate(calls) if method.startswith("Input.")]
    assert len(reads) == 4
    assert min(inputs) > max(reads)
    assert browser.fresh.call_count == 2


@pytest.mark.parametrize(
    "state",
    [
        {"visible": False, "focused": True, "frames": 2},
        {"visible": True, "focused": False, "frames": 2},
        {"visible": True, "focused": True, "frames": 0},
    ],
)
def test_hidden_unfocused_or_frozen_rendering_times_out_without_input(monkeypatch, state):
    browser, page, action, calls = activation_browser(monkeypatch, [state])
    fast_clock(monkeypatch)
    with pytest.raises(RuntimeError, match="Restore or uncover"):
        browser.act(action, page)
    assert not any(method.startswith("Input.") for method, _ in calls)
    assert page["observation_id"] not in browser._consumed


@pytest.mark.parametrize(
    "method",
    [
        "Browser.getWindowForTarget",
        "Browser.setWindowBounds",
        "Page.bringToFront",
        "Runtime.evaluate",
    ],
)
def test_cancel_after_each_activation_operation_prevents_further_input(monkeypatch, method):
    browser, page, action, calls = activation_browser(monkeypatch, window_state="minimized", cancel_on=method)
    with pytest.raises(module.ActionPaused):
        browser.act(action, page)
    assert calls[-1][0] == method
    assert not any(name.startswith("Input.") for name, _ in calls)
    assert page["observation_id"] not in browser._consumed


def test_stale_page_after_real_readiness_prevents_input(monkeypatch):
    browser, page, action, calls = activation_browser(monkeypatch)
    browser.fresh.side_effect = [True, False]
    with pytest.raises(module.StalePage, match="changed when activated"):
        browser.act(action, page)
    assert not any(name.startswith("Input.") for name, _ in calls)


def test_probe_requires_real_frames_and_resets_when_hidden():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to execute the browser readiness probe")
    script = """
      const vm=require('vm'),fs=require('fs'),expression=JSON.parse(fs.readFileSync(0,'utf8'));
      const queue=[],context={window:{},document:{visibilityState:'hidden',hasFocus:()=>true},
        requestAnimationFrame:callback=>queue.push(callback)};
      const read=()=>vm.runInNewContext(expression,context), states=[];
      states.push(read()); if(queue.length) throw Error('Scheduled a frame while hidden');
      context.document.visibilityState='visible';states.push(read());
      queue.shift()();states.push(read());queue.shift()();states.push(read());
      context.document.visibilityState='hidden';states.push(read());
      process.stdout.write(JSON.stringify(states));
    """
    result = subprocess.run(
        [node, "-e", script],
        input=json.dumps(module.activation_readiness_expression("test")),
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    states = json.loads(result.stdout)
    assert [state["frames"] for state in states] == [0, 0, 1, 2, 0]
    assert states[0]["visible"] is False and states[3]["visible"] is True


def test_renderer_probe_timeout_has_actionable_error_without_input(monkeypatch):
    browser, page, action, calls = activation_browser(monkeypatch)
    browser.evaluate = Mock(side_effect=RuntimeError("CDP response timed out"))
    with pytest.raises(RuntimeError, match="No page input was sent") as error:
        browser.act(action, page)
    assert '"probe":"unavailable"' in str(error.value)
    assert not any(name.startswith("Input.") for name, _ in calls)
    assert page["observation_id"] not in browser._consumed


def test_readiness_diagnostics_exclude_untrusted_values(monkeypatch):
    browser, page, action, _ = activation_browser(monkeypatch)
    browser.evaluate = Mock(return_value={
        "visible": "private-sentinel", "focused": False, "frames": "private-sentinel", "extra": "private-sentinel",
    })
    fast_clock(monkeypatch)
    with pytest.raises(RuntimeError) as error:
        browser.act(action, page)
    assert "private-sentinel" not in str(error.value)
    assert '"visible":null,"focused":false,"frames":null' in str(error.value)
    assert '"probe":"responding"' in str(error.value)
