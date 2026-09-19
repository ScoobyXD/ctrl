"""Browser-level navigation uses only observed tabs and supplied exact URLs."""

import copy
import threading

import pytest

from jev_ultrafast import browser as module


class Chrome:
    def __init__(self):
        self.targets = {
            "source": {"targetId": "source", "type": "page", "url": "https://example.com/article", "title": "Article"},
            "other": {"targetId": "other", "type": "page", "url": "https://example.org/", "title": "Other page"},
            "ctrl": {"targetId": "ctrl", "type": "page", "url": "http://127.0.0.1:8766/", "title": "CTRL"},
            "internal": {"targetId": "internal", "type": "page", "url": "chrome://inspect/", "title": "Inspect"},
        }
        self.sessions = {}
        self.calls = []
        self.history = {
            "currentIndex": 1,
            "entries": [
                {"id": 10, "url": "https://example.com/", "title": "Home"},
                {"id": 11, "url": "https://example.com/article", "title": "Article"},
            ],
        }
        self.fail = None

    def cdp(self, method, session_id=None, **params):
        self.calls.append((method, params, session_id))
        if method == self.fail:
            raise RuntimeError("CDP reply lost")
        if method == "Target.getTargets":
            return {"targetInfos": copy.deepcopy(list(self.targets.values()))}
        if method == "Target.getTargetInfo":
            if params["targetId"] not in self.targets:
                raise RuntimeError("No such target")
            return {"targetInfo": copy.deepcopy(self.targets[params["targetId"]])}
        if method == "Target.createTarget":
            target = f"new-{len(self.targets)}"
            self.targets[target] = {"targetId": target, "type": "page", "url": params["url"], "title": "New page"}
            return {"targetId": target}
        if method == "Target.attachToTarget":
            session = f"session-{len(self.sessions)}"
            self.sessions[session] = params["targetId"]
            return {"sessionId": session}
        if method in {"Target.detachFromTarget", "Target.activateTarget"}:
            return {}
        if method == "Page.getNavigationHistory":
            return copy.deepcopy(self.history)
        if method == "Page.navigateToHistoryEntry":
            self.history["currentIndex"] = next(
                index for index, entry in enumerate(self.history["entries"]) if entry["id"] == params["entryId"]
            )
            entry = self.history["entries"][self.history["currentIndex"]]
            self.targets[self.sessions[session_id]].update(url=entry["url"], title=entry["title"])
            return {}
        if method == "Runtime.evaluate":
            info = self.targets[self.sessions[session_id]]
            marker = [info["url"], info["title"]]
            if params["expression"] == module.MARKER:
                return {"result": {"value": marker}}
            if params["expression"] == module.NAVIGATION_READY:
                return {"result": {"value": {"ready": "complete", "url": info["url"]}}}
            if params["expression"] == module.READ_STATE:
                return {
                    "result": {
                        "value": {
                            "url": info["url"],
                            "title": info["title"],
                            "text": "Page content",
                            "scroll": {"y": 0},
                            "actions": [{"id": "wait", "kind": "wait", "label": "Wait"}],
                            "marker": marker,
                            "page_key": [],
                            "guards": {},
                        }
                    }
                }
            return {"result": {"value": "complete"}}
        raise AssertionError(f"Unexpected command: {method}")


@pytest.fixture
def browser(monkeypatch):
    chrome = Chrome()
    monkeypatch.setenv("CTRL_PORT", "8766")
    monkeypatch.setattr(module, "cdp", chrome.cdp)
    monkeypatch.setattr(module, "status", lambda: {"connected": True})
    instance = module.Browser(target_id="source", navigation_urls=["https://mail.google.com/", "https://x.com/"])
    return instance, chrome


def selected(page, kind):
    return next(action for action in page["actions"] if action["kind"] == kind)


def test_observation_offers_only_observed_tabs_supplied_urls_and_real_back_history(browser):
    instance, _ = browser
    page = instance.observe(screenshot=False)
    assert page["target_id"] == "source"
    assert page["browser_context"]["tabs"][0]["target_id"] == "source"
    assert [a["target_id"] for a in page["actions"] if a["kind"] == "switch_tab"] == ["other"]
    assert [a["url"] for a in page["actions"] if a["kind"] == "open_url"] == [
        "https://mail.google.com/",
        "https://x.com/",
    ]
    assert selected(page, "back")["entry_id"] == 10
    assert instance.fresh(page)


def test_open_url_creates_foreground_tab_preserves_source_and_observes_destination(browser):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    action = selected(page, "open_url")
    instance.act(action, page)
    assert instance.target != "source"
    assert chrome.targets["source"]["url"] == "https://example.com/article"
    assert instance.observe(screenshot=False)["url"] == action["url"]
    creates = [params for method, params, _ in chrome.calls if method == "Target.createTarget"]
    assert creates == [{"url": action["url"], "background": False}]
    assert not any(method in {"Page.navigate", "Target.closeTarget"} for method, _, _ in chrome.calls)


def test_switch_tab_activates_exact_observed_target_without_navigation(browser):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    instance.act(selected(page, "switch_tab"), page)
    assert instance.target == "other"
    assert instance.observe(screenshot=False)["url"] == "https://example.org/"
    assert ("Target.activateTarget", {"targetId": "other"}, None) in chrome.calls
    assert not any(
        method in {"Page.navigate", "Target.createTarget", "Target.closeTarget"} for method, _, _ in chrome.calls
    )


@pytest.mark.parametrize("field,value", [("url", "https://changed.example/"), ("title", "Changed title")])
def test_changed_switch_target_is_stale_before_attachment(browser, field, value):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    chrome.targets["other"][field] = value
    before = len(chrome.calls)
    with pytest.raises(module.StalePage):
        instance.act(selected(page, "switch_tab"), page)
    assert not any(method == "Target.attachToTarget" for method, _, _ in chrome.calls[before:])


def test_closed_switch_target_does_not_fall_back_to_another_tab(browser):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    del chrome.targets["other"]
    with pytest.raises(module.StalePage):
        instance.act(selected(page, "switch_tab"), page)
    assert instance.target == "source"


def test_back_uses_exact_observed_history_entry(browser):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    instance.act(selected(page, "back"), page)
    assert ("Page.navigateToHistoryEntry", {"entryId": 10}, instance.session) in chrome.calls
    assert not any(a["kind"] == "back" for a in instance.observe(screenshot=False)["actions"])


def test_history_change_invalidates_back(browser):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    chrome.history["entries"][0]["id"] = 999
    with pytest.raises(module.StalePage):
        instance.act(selected(page, "back"), page)
    assert not any(method == "Page.navigateToHistoryEntry" for method, _, _ in chrome.calls)


def test_source_page_change_invalidates_browser_navigation(browser):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    chrome.targets["source"]["title"] = "Changed article"
    with pytest.raises(module.StalePage):
        instance.act(selected(page, "open_url"), page)
    assert not any(method == "Target.createTarget" for method, _, _ in chrome.calls)


@pytest.mark.parametrize("failure", ["Target.createTarget", "Target.attachToTarget", "Target.activateTarget"])
def test_unconfirmed_navigation_is_consumed_once_without_replay(browser, failure):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    kind = "switch_tab" if failure == "Target.activateTarget" else "open_url"
    action = selected(page, kind)
    chrome.fail = failure
    with pytest.raises(RuntimeError, match="not confirmed"):
        instance.act(action, page)
    count = sum(method == failure for method, _, _ in chrome.calls)
    with pytest.raises(RuntimeError, match="already used"):
        instance.act(action, page)
    assert sum(method == failure for method, _, _ in chrome.calls) == count


def test_pause_prevents_navigation_and_preserves_observation_for_resume(browser):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    instance.cancel_event = threading.Event()
    instance.cancel_event.set()
    with pytest.raises(module.ActionPaused):
        instance.act(selected(page, "open_url"), page)
    assert not any(method == "Target.createTarget" for method, _, _ in chrome.calls)
    instance.cancel_event.clear()
    instance.act(selected(page, "open_url"), page)
    assert instance.target != "source"


def test_fingerprint_distinguishes_identical_content_in_different_tabs(browser):
    instance, chrome = browser
    chrome.targets["other"].update(url=chrome.targets["source"]["url"], title=chrome.targets["source"]["title"])
    before = instance.observe(screenshot=False)
    instance.act(selected(before, "switch_tab"), before)
    after = instance.observe(screenshot=False)
    assert before["url"] == after["url"] and before["text"] == after["text"]
    assert before["fingerprint"] != after["fingerprint"]
    assert not instance.fresh(before)


def test_navigation_destinations_can_be_replaced_only_with_valid_exact_urls(browser):
    instance, _ = browser
    instance.set_navigation_urls(["https://example.net/", "https://example.net/"])
    assert instance.navigation_urls == ["https://example.net/"]
    for url in (
        "javascript:alert(1)",
        "http://127.0.0.1:8766/",
        "https://user:secret@example.com",
        "https://exa mple.com",
    ):
        with pytest.raises(ValueError):
            instance.set_navigation_urls([url])
    assert instance.navigation_urls == ["https://example.net/"]


def test_changed_browser_context_refreshes_choices_before_predict(browser):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    chrome.targets["extra"] = {"targetId": "extra", "type": "page", "url": "https://extra.example", "title": "Extra"}
    assert not instance.fresh(page)


def test_open_waits_past_transient_blank_document_without_creating_again(browser, monkeypatch):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    action = selected(page, "open_url")
    instance.act(action, page)
    evaluate = instance.evaluate
    readiness = iter(
        [
            {"ready": "complete", "url": "about:blank"},
            {"ready": "interactive", "url": action["url"]},
        ]
    )
    monkeypatch.setattr(
        instance,
        "evaluate",
        lambda expression: next(readiness) if expression == module.NAVIGATION_READY else evaluate(expression),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    assert instance.observe(screenshot=False)["url"] == action["url"]
    assert instance._pending_navigation is None
    assert sum(method == "Target.createTarget" for method, _, _ in chrome.calls) == 1


def test_navigation_readiness_timeout_cannot_replay_open(browser, monkeypatch):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    action = selected(page, "open_url")
    instance.act(action, page)
    clock = iter([0, 1, 9])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(instance, "evaluate", lambda _expression: {"ready": "loading", "url": "about:blank"})
    with pytest.raises(RuntimeError, match="still loading"):
        instance.observe(screenshot=False)
    with pytest.raises(RuntimeError, match="already used"):
        instance.act(action, page)
    assert sum(method == "Target.createTarget" for method, _, _ in chrome.calls) == 1
