"""Browser-level navigation uses only observed tabs and supplied exact URLs."""

import copy
import json
import shutil
import subprocess
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
        if method in {"Target.detachFromTarget", "Target.activateTarget", "Page.bringToFront"}:
            return {}
        if method == "Browser.getWindowForTarget":
            return {"windowId": 1, "bounds": {"windowState": "normal"}}
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
            if "__ctrlActivationReadiness" in params["expression"]:
                return {"result": {"value": {"visible": True, "focused": True, "frames": 2}}}
            if module.MARKER in params["expression"]:
                return {"result": {"value": marker}}
            if module.NAVIGATION_READY in params["expression"]:
                return {"result": {"value": {"ready": "complete", "url": info["url"]}}}
            if module.READ_STATE in params["expression"]:
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
    instance = module.Browser(
        target_id="source",
        navigation_urls=[
            "https://mail.google.com/",
            "https://x.com/",
            "https://example.com/",
            "https://example.org/",
        ],
    )
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
        "https://example.com/",
        "https://example.org/",
    ]
    assert selected(page, "back")["entry_id"] == 10
    assert instance.fresh(page)


def test_open_url_creates_preserved_tab_then_activates_only_destination(browser):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    action = selected(page, "open_url")
    instance.act(action, page)
    assert instance.target != "source"
    assert chrome.targets["source"]["url"] == "https://example.com/article"
    assert instance.observe(screenshot=False)["url"] == action["url"]
    creates = [params for method, params, _ in chrome.calls if method == "Target.createTarget"]
    assert creates == [{"url": action["url"], "background": True}]
    activated = [session for method, _, session in chrome.calls if method == "Page.bringToFront"]
    assert activated == [instance.session]
    assert chrome.sessions[activated[0]] == instance.target
    assert not any(method in {"Page.navigate", "Target.closeTarget"} for method, _, _ in chrome.calls)


def test_switch_tab_activates_exact_observed_target_without_navigation(browser):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    instance.act(selected(page, "switch_tab"), page)
    assert instance.target == "other"
    assert instance.observe(screenshot=False)["url"] == "https://example.org/"
    assert not any(
        method in {"Page.navigate", "Target.createTarget", "Target.closeTarget", "Target.activateTarget"}
        for method, _, _ in chrome.calls
    )
    activated = [chrome.sessions[session] for method, _, session in chrome.calls if method == "Page.bringToFront"]
    assert activated == ["other"]


def test_passive_attachment_and_observation_do_not_activate_tabs(browser):
    instance, chrome = browser
    instance.observe(screenshot=False)
    assert not any(method in {"Page.bringToFront", "Target.activateTarget"} for method, _, _ in chrome.calls)


@pytest.mark.parametrize("kind", ["open_url", "switch_tab"])
def test_navigation_activation_failure_keeps_dispatch_and_cannot_replay(browser, kind):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    action = selected(page, kind)
    assert instance.act(action, page) == {"executed": action["id"]}
    count = len(chrome.targets)
    chrome.fail = "Page.bringToFront"
    with pytest.raises(RuntimeError):
        instance.observe(screenshot=False)
    with pytest.raises(RuntimeError, match="already used"):
        instance.act(action, page)
    assert len(chrome.targets) == count
    assert instance._pending_navigation is not None


def test_pause_after_navigation_dispatch_does_not_activate_or_recreate(browser):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    instance.act(selected(page, "open_url"), page)
    instance.cancel_event = threading.Event()
    instance.cancel_event.set()
    with pytest.raises(module.ActionPaused):
        instance.observe(screenshot=False)
    assert not any(method == "Page.bringToFront" for method, _, _ in chrome.calls)
    instance.cancel_event.clear()
    instance.observe(screenshot=False)
    assert sum(method == "Target.createTarget" for method, _, _ in chrome.calls) == 1
    assert sum(method == "Page.bringToFront" for method, _, _ in chrome.calls) == 1


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


@pytest.mark.parametrize("failure", ["Target.createTarget", "Target.attachToTarget", "Page.navigateToHistoryEntry"])
def test_unconfirmed_navigation_is_consumed_once_without_replay(browser, failure):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    kind = "back" if failure == "Page.navigateToHistoryEntry" else "open_url"
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
    chrome.targets["extra"] = {
        "targetId": "extra",
        "type": "page",
        "url": "https://example.org/extra",
        "title": "Extra",
    }
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
        lambda expression, **kwargs: next(readiness)
        if expression == module.NAVIGATION_READY else evaluate(expression, **kwargs),
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


def test_unrelated_start_page_is_neutral_and_never_read(browser):
    instance, chrome = browser
    instance.set_navigation_urls(["https://mail.google.com/"])
    chrome.targets["source"].update(url="https://secrets.example/keys", title="Private secret settings")
    chrome.targets["mail"] = {
        "targetId": "mail",
        "type": "page",
        "url": "https://mail.google.com/mail/",
        "title": "Mail",
    }
    chrome.calls.clear()
    page = instance.observe(screenshot=True)
    assert page["bootstrap"] is True
    assert page["url"] == "" and page["title"] == "Choose a task destination"
    assert "secrets.example" not in json.dumps(page)
    assert "Private secret settings" not in json.dumps(page)
    assert {action["kind"] for action in page["actions"]} == {"open_url", "switch_tab"}
    assert [tab["target_id"] for tab in page["browser_context"]["tabs"]] == ["mail"]
    assert page["task_browser_scope"] == {
        "requested_urls": ["https://mail.google.com/"],
        "allowed_hosts": ["mail.google.com"],
        "mode": "requested_sites",
        "outside_scope": True,
    }
    assert instance.fresh(page)
    assert not any(
        method in {"Runtime.evaluate", "Page.getNavigationHistory", "Page.captureScreenshot"}
        for method, _, _ in chrome.calls
    )


def test_pause_after_open_keeps_consumption_and_can_observe_destination_on_resume(browser):
    instance, chrome = browser
    page = instance.observe(screenshot=False)
    action = selected(page, "open_url")
    instance.act(action, page)
    instance.cancel_event = threading.Event()
    instance.cancel_event.set()
    before = len(chrome.calls)
    with pytest.raises(module.ActionPaused):
        instance.observe(screenshot=False)
    assert len(chrome.calls) == before
    with pytest.raises(RuntimeError, match="already used"):
        instance.act(action, page)
    instance.cancel_event.clear()
    assert instance.observe(screenshot=False)["url"] == action["url"]
    assert sum(method == "Target.createTarget" for method, _, _ in chrome.calls) == 1


def test_bootstrap_can_open_only_a_requested_destination_without_reading_source(browser):
    instance, chrome = browser
    instance.set_navigation_urls(["https://mail.google.com/"])
    page = instance.observe(screenshot=False)
    chrome.calls.clear()
    instance.act(selected(page, "open_url"), page)
    assert not any(method == "Runtime.evaluate" for method, _, _ in chrome.calls)
    destination = instance.observe(screenshot=False)
    assert destination["url"] == "https://mail.google.com/"
    assert destination["task_browser_scope"]["outside_scope"] is False
    assert "bootstrap" not in destination


def test_initial_selected_tab_mode_does_not_offer_other_hosts(browser):
    instance, chrome = browser
    instance.set_navigation_urls([])
    chrome.targets["same"] = {
        "targetId": "same",
        "type": "page",
        "url": "https://www.example.com/help",
        "title": "Help",
    }
    page = instance.observe(screenshot=False)
    assert page["text"] == "Page content"
    assert page["task_browser_scope"]["mode"] == "selected_tab"
    assert page["task_browser_scope"]["allowed_hosts"] == ["example.com"]
    assert [action["target_id"] for action in page["actions"] if action["kind"] == "switch_tab"] == ["same"]
    assert "https://example.org/" not in json.dumps(page)


def test_requested_host_matching_allows_children_but_not_siblings_or_suffix_attacks(browser):
    instance, chrome = browser
    instance.set_navigation_urls(["https://console.aws.amazon.com/"])
    for target, url in {
        "allowed": "https://www.console.aws.amazon.com/home",
        "region": "https://us-west-1.console.aws.amazon.com/console/home",
        "sibling": "https://signin.aws.amazon.com/",
        "suffix": "https://console.aws.amazon.com.attacker.example/",
        "prefix": "https://notconsole.aws.amazon.com/",
    }.items():
        chrome.targets[target] = {"targetId": target, "type": "page", "url": url, "title": target}
    page = instance.observe(screenshot=False)
    assert {tab["target_id"] for tab in page["browser_context"]["tabs"]} == {"allowed", "region"}
    assert {action["target_id"] for action in page["actions"] if action["kind"] == "switch_tab"} == {
        "allowed",
        "region",
    }


def test_post_action_redirect_outside_requested_sites_stops_without_reading_redirect(browser):
    instance, chrome = browser
    instance.set_navigation_urls(["https://mail.google.com/"])
    page = instance.observe(screenshot=False)
    instance.act(selected(page, "open_url"), page)
    chrome.targets[instance.target].update(url="https://signin.example/private", title="Secret login")
    chrome.calls.clear()
    boundary = instance.observe(screenshot=True)
    assert boundary["scope_blocked"]
    assert boundary["bootstrap"] is False
    assert boundary["actions"] == []
    assert boundary["task_browser_scope"]["outside_scope"] is True
    assert "signin.example" not in json.dumps(boundary) and "Secret login" not in json.dumps(boundary)
    assert not any(
        method in {"Runtime.evaluate", "Page.getNavigationHistory", "Page.captureScreenshot"}
        for method, _, _ in chrome.calls
    )


def test_unrelated_history_entries_and_tabs_are_omitted(browser):
    instance, chrome = browser
    chrome.history["entries"][0].update(url="https://private.example/account", title="Private account")
    chrome.targets["private"] = {
        "targetId": "private",
        "type": "page",
        "url": "https://private.example/account",
        "title": "Private account",
    }
    page = instance.observe(screenshot=False)
    assert page["browser_context"]["history"]["previous"] is None
    assert not any(action["kind"] == "back" for action in page["actions"])
    assert "private.example" not in json.dumps(page)
    chrome.targets["private"]["title"] = "Unrelated update"
    assert instance.fresh(page)


def test_document_redirect_between_metadata_and_reader_is_guarded(browser, monkeypatch):
    instance, chrome = browser
    original = chrome.cdp

    def redirect(method, **params):
        if method == "Runtime.evaluate" and module.READ_STATE in params["expression"]:
            expression = params["expression"]
            assert expression.index(".some(base=>") < expression.index(module.READ_STATE)
            chrome.targets["source"].update(url="https://private.example/", title="Private page")
            return {"result": {"value": module.OUTSIDE_SCOPE}}
        return original(method, **params)

    monkeypatch.setattr(module, "cdp", redirect)
    page = instance.observe(screenshot=False)
    assert page["task_browser_scope"]["outside_scope"]
    assert "Page content" not in page["text"]
    assert "private.example" not in json.dumps(page)


@pytest.mark.parametrize(
    "host,port,base,reads",
    [
        ("private.example", "", "example.com", 0),
        ("example.com", "", "example.com", 1),
        ("www.example.com", "", "example.com", 1),
        ("regional.example.com", "", "example.com", 1),
        ("example.com.attacker.test", "", "example.com", 0),
        ("notexample.com", "", "example.com", 0),
        ("localhost", "8766", "localhost:8766", 0),
        ("localhost.", "8766", "localhost:8766", 0),
        ("localhost", "8877", "localhost:8877", 1),
        ("localhost", "8878", "localhost:8877", 0),
        ("localhost", "", "localhost:80", 1),
        ("localhost", "8877", "localhost", 0),
        ("other.localhost", "8877", "localhost:8877", 0),
        ("127.0.0.1", "8766", "127.0.0.1:8766", 0),
        ("127.0.0.1", "8767", "127.0.0.1:8767", 0),
        ("127.0.0.2", "8766", "127.0.0.2:8766", 0),
        ("127.0.0.1", "8877", "127.0.0.1:8877", 1),
        ("127.0.0.1", "8878", "127.0.0.1:8877", 0),
        ("[::1]", "8877", "[::1]:8877", 1),
        ("[::1]", "8878", "[::1]:8877", 0),
        ("[::1]", "8766", "[::1]:8766", 0),
        ("[::ffff:7f00:1]", "8877", "[::ffff:7f00:1]:8877", 1),
        ("[::ffff:7f00:1]", "8878", "[::ffff:7f00:1]:8877", 0),
        ("[::ffff:7f00:1]", "8766", "[::ffff:7f00:1]:8766", 0),
    ],
)
def test_injected_scope_guard_runs_before_any_reader_code(host, port, base, reads, monkeypatch):
    monkeypatch.setenv("CTRL_PORT", "8766")
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required to execute the browser JavaScript guard")
    expression = module.scoped_expression("(() => { reads++; return {text:'authorized page'}; })()", [base])
    script = (
        "const fs=require('fs'),vm=require('vm'); const input=JSON.parse(fs.readFileSync(0,'utf8'));"
        "const context={location:{hostname:input.host,port:input.port,protocol:'http:'},reads:0};"
        "const value=vm.runInNewContext(input.expression,context);"
        "process.stdout.write(JSON.stringify({reads:context.reads,value}));"
    )
    result = subprocess.run(
        [node, "-e", script],
        input=json.dumps({"expression": expression, "host": host, "port": port}),
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    actual = json.loads(result.stdout)
    assert actual["reads"] == reads
    assert actual["value"] == ({"text": "authorized page"} if reads else module.OUTSIDE_SCOPE)


def test_local_browser_scope_excludes_other_ports_from_tabs_and_back(browser):
    instance, chrome = browser
    instance.set_navigation_urls(["http://127.0.0.1:8877/"])
    for target, port in (("lab", 8877), ("other-local-app", 8878)):
        chrome.targets[target] = {
            "targetId": target,
            "type": "page",
            "url": f"http://127.0.0.1:{port}/",
            "title": target,
        }
    page = instance.observe(screenshot=False)
    assert instance.allowed_hosts == ["127.0.0.1:8877"]
    assert [tab["target_id"] for tab in page["browser_context"]["tabs"]] == ["lab"]
    assert "other-local-app" not in json.dumps(page)
    assert not instance.in_scope("http://127.0.0.1:8766/")


def test_selected_local_tab_keeps_its_own_port(monkeypatch):
    chrome = Chrome()
    chrome.targets["source"]["url"] = "http://localhost:8877/"
    chrome.targets["other"]["url"] = "http://localhost:8878/"
    monkeypatch.setattr(module, "cdp", chrome.cdp)
    monkeypatch.setattr(module, "status", lambda: {"connected": True})
    instance = module.Browser(target_id="source")
    assert instance.allowed_hosts == ["localhost:8877"]
    assert not instance.in_scope("http://localhost:8878/")


def bootstrap_browser(monkeypatch):
    chrome = Chrome()
    monkeypatch.setattr(module, "cdp", chrome.cdp)
    monkeypatch.setattr(module, "status", lambda: {"connected": True})
    return module.Browser(navigation_urls=["https://example.org/"]), chrome


def test_targetless_bootstrap_reads_no_tab_and_attaches_only_after_selected_open(monkeypatch):
    instance, chrome = bootstrap_browser(monkeypatch)
    assert chrome.calls == []
    page = instance.observe(screenshot=True)
    assert page["bootstrap"] and page["target_id"] is None
    assert instance.fresh(page)
    assert {method for method, _, _ in chrome.calls} == {"Target.getTargets"}
    action = selected(page, "open_url")
    instance.act(action, page)
    assert sum(method == "Target.createTarget" for method, _, _ in chrome.calls) == 1
    assert not any(method == "Target.detachFromTarget" for method, _, _ in chrome.calls)
    assert instance.observe(screenshot=False)["url"] == "https://example.org/"
    with pytest.raises(RuntimeError, match="already used"):
        instance.act(action, page)


def test_targetless_switch_attaches_without_null_detach_or_create(monkeypatch):
    instance, chrome = bootstrap_browser(monkeypatch)
    page = instance.observe(screenshot=False)
    instance.act(selected(page, "switch_tab"), page)
    assert instance.target == "other"
    assert not any(method in {"Target.createTarget", "Target.detachFromTarget"} for method, _, _ in chrome.calls)


def test_closed_targetless_browser_cannot_open_a_tab(monkeypatch):
    instance, chrome = bootstrap_browser(monkeypatch)
    page = instance.observe(screenshot=False)
    instance.close()
    chrome.calls.clear()
    with pytest.raises(RuntimeError, match="closed"):
        instance.act(selected(page, "open_url"), page)
    with pytest.raises(RuntimeError, match="closed"):
        instance.observe(screenshot=False)
    assert chrome.calls == []


def test_paused_targetless_browser_dispatches_nothing(monkeypatch):
    instance, chrome = bootstrap_browser(monkeypatch)
    page = instance.observe(screenshot=False)
    instance.cancel_event = threading.Event()
    instance.cancel_event.set()
    chrome.calls.clear()
    with pytest.raises(module.ActionPaused):
        instance.act(selected(page, "open_url"), page)
    assert not any(method in {"Target.createTarget", "Target.attachToTarget"} for method, _, _ in chrome.calls)


def test_targetless_bootstrap_requires_valid_destinations_before_connection(monkeypatch):
    def forbidden():
        raise AssertionError("Invalid input must not connect to Chrome")

    monkeypatch.setattr(module, "status", forbidden)
    for destinations in (None, [], ["javascript:alert(1)"], ["http://127.0.0.1:8766/"]):
        with pytest.raises(ValueError):
            module.Browser(navigation_urls=destinations)


def test_transient_document_scope_guard_during_history_restore_retries_only_guarded_reads(browser, monkeypatch):
    instance, chrome = browser
    original = chrome.cdp
    rejected = False

    def transient(method, **params):
        nonlocal rejected
        if method == "Runtime.evaluate" and module.READ_STATE in params["expression"] and not rejected:
            rejected = True
            return {"result": {"value": module.OUTSIDE_SCOPE}}
        return original(method, **params)

    monkeypatch.setattr(module, "cdp", transient)
    instance._has_acted = True
    page = instance.observe(screenshot=False)
    assert rejected
    assert page["url"] == "https://example.com/article"
    assert "scope_blocked" not in page
    assert not any(method.startswith("Input.") for method, _, _ in chrome.calls)


def test_persistent_document_scope_rejection_stays_bounded_and_never_reads_outside(browser, monkeypatch):
    instance, chrome = browser
    original = chrome.cdp
    reads = 0

    def rejected(method, **params):
        nonlocal reads
        if method == "Runtime.evaluate" and module.READ_STATE in params["expression"]:
            reads += 1
            return {"result": {"value": module.OUTSIDE_SCOPE}}
        return original(method, **params)

    monkeypatch.setattr(module, "cdp", rejected)
    instance._has_acted = True
    page = instance.observe(screenshot=False)
    assert reads == 10
    assert page["scope_blocked"]
    assert "Page content" not in page["text"]
