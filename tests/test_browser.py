import threading
from unittest.mock import Mock

import pytest

from jev_ultrafast import browser as module


def attached(monkeypatch, *, url=None):
    calls = []

    def cdp(method, **kwargs):
        calls.append((method, kwargs))
        if method == "Runtime.evaluate" and "__ctrlActivationReadiness" in kwargs.get("expression", ""):
            return {"result": {"value": {"visible": True, "focused": True, "frames": 2}}}
        return {
            "Browser.getWindowForTarget": {"windowId": 42, "bounds": {"windowState": "normal"}},
            "Target.getTargetInfo": {"targetInfo": {"type": "page", "url": "https://example.com"}},
            "Target.createTarget": {"targetId": "new-tab"},
            "Target.attachToTarget": {"sessionId": "session"},
            "Runtime.evaluate": {"result": {"value": "complete"}},
        }.get(method, {})

    monkeypatch.setattr(module, "cdp", cdp)
    monkeypatch.setattr(module, "status", lambda: {"connected": True})
    browser = module.Browser(url=url) if url else module.Browser(target_id="chosen-tab")
    return browser, calls


def test_existing_tab_attachment_does_not_navigate_resize_focus_or_close(monkeypatch):
    browser, calls = attached(monkeypatch)
    browser.close()
    browser.close()
    assert calls == [
        ("Target.getTargetInfo", {"targetId": "chosen-tab"}),
        ("Target.attachToTarget", {"targetId": "chosen-tab", "flatten": True}),
        ("Target.detachFromTarget", {"sessionId": "session"}),
    ]


def test_explicit_url_creates_only_one_tab_and_leaves_it_open(monkeypatch):
    browser, calls = attached(monkeypatch, url="https://example.org/new")
    browser.close()
    assert calls[0] == ("Target.createTarget", {"url": "https://example.org/new", "background": True})
    assert not any(method in {"Target.closeTarget", "Page.navigate", "Browser.close"} for method, _ in calls)


def test_target_is_required_and_cannot_be_combined_with_url():
    with pytest.raises(ValueError):
        module.Browser()
    with pytest.raises(ValueError):
        module.Browser("https://example.com", target_id="tab")


def action_browser(monkeypatch):
    browser, _ = attached(monkeypatch)
    action = {"id": "e1", "kind": "click", "node": 1, "label": "Save"}
    page = {"actions": [action], "observation_id": "one-observation"}
    monkeypatch.setattr(browser, "fresh", lambda *_: True)
    return browser, page, action


def test_unconfirmed_action_cannot_reuse_observation(monkeypatch):
    browser, page, action = action_browser(monkeypatch)
    execute = Mock(side_effect=RuntimeError("mouse release response lost"))
    monkeypatch.setattr(module, "browser_operation", execute)
    with pytest.raises(RuntimeError, match="response lost"):
        browser.act(action, page)
    with pytest.raises(RuntimeError, match="already used"):
        browser.act(action, page)
    assert execute.call_count == 1


def test_stale_decision_never_sends_input(monkeypatch):
    browser, page, action = action_browser(monkeypatch)
    monkeypatch.setattr(browser, "fresh", lambda *_: False)
    execute = Mock()
    monkeypatch.setattr(module, "browser_operation", execute)
    with pytest.raises(module.StalePage):
        browser.act(action, page)
    execute.assert_not_called()


def test_unobserved_action_is_rejected(monkeypatch):
    browser, page, action = action_browser(monkeypatch)
    with pytest.raises(ValueError, match="observed"):
        browser.act({**action, "node": 99}, page)


def test_activation_revalidates_observed_target_before_input(monkeypatch):
    browser, page, action = action_browser(monkeypatch)
    browser.fresh = Mock(side_effect=[True, False])
    activate = Mock()
    browser.activate_for_input = activate
    execute = Mock()
    monkeypatch.setattr(module, "browser_operation", execute)
    with pytest.raises(module.StalePage, match="changed when activated"):
        browser.act(action, page)
    activate.assert_called_once_with()
    assert browser.fresh.call_count == 2
    assert page["observation_id"] not in browser._consumed
    execute.assert_not_called()


def test_pause_during_tab_activation_prevents_page_input(monkeypatch):
    browser, page, action = action_browser(monkeypatch)
    browser.cancel_event = threading.Event()
    browser.activate_for_input = Mock(side_effect=browser.cancel_event.set)
    execute = Mock()
    monkeypatch.setattr(module, "browser_operation", execute)
    with pytest.raises(module.ActionPaused, match="after tab activation"):
        browser.act(action, page)
    execute.assert_not_called()
    assert page["observation_id"] not in browser._consumed


def test_interrupted_select_is_not_reported_as_safe_to_retry(monkeypatch):
    monkeypatch.setattr(module, "cdp", lambda *_args, **_kwargs: {"exceptionDetails": {"text": "navigated"}})
    with pytest.raises(RuntimeError, match="interrupted"):
        module.browser_operation(
            {
                "operation": "act",
                "session": "s",
                "action": {
                    "id": "e1",
                    "kind": "select",
                    "node": 2,
                    "value": "v",
                },
            }
        )


def test_fill_does_not_type_if_click_focused_something_else(monkeypatch):
    calls = []
    evaluations = iter([{"x": 20, "y": 30}, False])

    def cdp(method, **kwargs):
        calls.append(method)
        return {"result": {"value": next(evaluations)}} if method == "Runtime.evaluate" else {}

    monkeypatch.setattr(module, "cdp", cdp)
    with pytest.raises(RuntimeError, match="did not receive focus"):
        module.browser_operation(
            {
                "operation": "act",
                "session": "s",
                "text": "Supplied text",
                "action": {
                    "id": "e1",
                    "kind": "fill",
                    "node": 2,
                },
            }
        )
    assert "Input.insertText" not in calls
    assert "Input.dispatchKeyEvent" not in calls


def test_scroll_uses_current_viewport(monkeypatch):
    calls = []

    def cdp(method, **kwargs):
        calls.append((method, kwargs))
        return {"result": {"value": {"w": 400, "h": 300}}} if method == "Runtime.evaluate" else {}

    monkeypatch.setattr(module, "cdp", cdp)
    module.browser_operation(
        {
            "operation": "act",
            "session": "s",
            "action": {
                "id": "scroll_down",
                "kind": "scroll",
                "delta": 560,
            },
        }
    )
    assert calls[-1][1]["x"] == 200
    assert calls[-1][1]["y"] == 150


def test_pause_during_freshness_check_prevents_mutation(monkeypatch):
    browser, page, action = action_browser(monkeypatch)
    browser.cancel_event = threading.Event()

    def fresh(*_):
        browser.cancel_event.set()
        return True

    monkeypatch.setattr(browser, "fresh", fresh)
    execute = Mock()
    monkeypatch.setattr(module, "browser_operation", execute)
    with pytest.raises(module.ActionPaused):
        browser.act(action, page)
    execute.assert_not_called()


def test_pause_during_hit_test_prevents_first_mouse_event(monkeypatch):
    cancel = threading.Event()
    calls = []

    def cdp(method, **_kwargs):
        calls.append(method)
        if method == "Runtime.evaluate":
            cancel.set()
        return {"result": {"value": {"x": 20, "y": 30}}}

    monkeypatch.setattr(module, "cdp", cdp)
    with pytest.raises(module.ActionPaused):
        module.browser_operation(
            {
                "operation": "act",
                "session": "s",
                "cancel_event": cancel,
                "action": {
                    "id": "e1",
                    "kind": "click",
                    "node": 2,
                },
            }
        )
    assert calls == ["Runtime.evaluate"]


def test_pause_after_mouse_pressed_does_not_leave_button_down(monkeypatch):
    cancel = threading.Event()
    calls = []

    def cdp(method, **kwargs):
        calls.append((method, kwargs.get("type")))
        if method == "Input.dispatchMouseEvent":
            cancel.set()
        return {"result": {"value": {"x": 20, "y": 30}}}

    monkeypatch.setattr(module, "cdp", cdp)
    module.browser_operation(
        {
            "operation": "act",
            "session": "s",
            "cancel_event": cancel,
            "action": {
                "id": "e1",
                "kind": "click",
                "node": 2,
            },
        }
    )
    assert calls[-2:] == [("Input.dispatchMouseEvent", "mousePressed"), ("Input.dispatchMouseEvent", "mouseReleased")]


def test_paused_before_input_can_resume_from_unchanged_observation(monkeypatch):
    browser, page, action = action_browser(monkeypatch)
    browser.activate_for_input = Mock()
    cancel = browser.cancel_event = threading.Event()
    inputs = []
    pause_first_read = True

    def cdp(method, **kwargs):
        nonlocal pause_first_read
        if method == "Runtime.evaluate":
            if pause_first_read:
                pause_first_read = False
                cancel.set()
            return {"result": {"value": {"x": 20, "y": 30}}}
        if method == "Input.dispatchMouseEvent":
            inputs.append(kwargs["type"])
        return {}

    monkeypatch.setattr(module, "cdp", cdp)
    with pytest.raises(module.ActionPaused):
        browser.act(action, page)
    assert inputs == []
    cancel.clear()
    assert browser.act(action, page) == {"executed": "e1"}
    assert inputs == ["mousePressed", "mouseReleased"]
