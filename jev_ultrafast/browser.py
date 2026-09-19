"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from ctrl.browser_connection import cdp, connect, selectable_tab, status

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text(encoding="utf-8").rstrip().rstrip(";")
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"
NAVIGATION_READY = "({ready:document.readyState,url:location.href})"


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class ActionPaused(RuntimeError):
    """Pause arrived before the first browser mutation was dispatched."""


class Browser:
    def __init__(self, url=None, *, target_id=None, navigation_urls=None):
        if bool(url) == bool(target_id):
            raise ValueError("Select an existing tab or supply a URL for a new tab.")
        if not status()["connected"] and not connect(wait=2)["connected"]:
            raise RuntimeError(status()["message"])
        self.target = None
        self.session = None
        self._consumed = set()
        self.cancel_event = None
        self._pending_navigation = None
        self.set_navigation_urls(navigation_urls)
        if target_id:
            info = cdp("Target.getTargetInfo", targetId=target_id)["targetInfo"]
            if not selectable_tab(info):
                raise ValueError("Select an ordinary web page, not an internal Chrome or CTRL tab.")
            self.target = target_id
        else:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("Use an http:// or https:// URL for a new tab.")
            if not selectable_tab({"type": "page", "url": url}):
                raise ValueError("The agent cannot control its own CTRL console.")
            self.target = cdp("Target.createTarget", url=url, background=True)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        if url:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    if self.evaluate("document.readyState") in {"interactive", "complete"}:
                        break
                except StalePage:
                    pass
                time.sleep(0.05)

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def set_navigation_urls(self, urls):
        self.navigation_urls = validate_navigation_urls(urls)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        self.wait_for_navigation()
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))("""
                    + json.dumps(action)
                    + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        for attempt in range(10):
            try:
                page = browser_operation({"operation": "observe", "session": self.session, "screenshot": screenshot})
                page["target_id"] = self.target
                page["browser_context"] = self.navigation_context()
                page["actions"].extend(self.navigation_actions(page["browser_context"]))
                page["fingerprint"] = fingerprint(page)
                page["observation_id"] = uuid.uuid4().hex
                return page
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        if page.get("target_id", self.target) != self.target:
            return False
        kind = action.get("kind") if action else None
        if kind in {"open_url", "switch_tab", "back"}:
            if self.evaluate(MARKER) != page["marker"]:
                return False
            if kind == "open_url":
                return action["url"] in self.navigation_urls
            if kind == "switch_tab":
                try:
                    info = cdp("Target.getTargetInfo", targetId=action["target_id"])["targetInfo"]
                except RuntimeError:
                    return False
                return selectable_tab(info) and tab_identity(info) == {
                    "target_id": action["target_id"],
                    "url": action["url"],
                    "title": action["title"],
                }
            return self.navigation_history() == page["browser_context"]["history"]
        if action is None and "browser_context" in page and self.navigation_context() != page["browser_context"]:
            return False
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def navigation_history(self):
        history = self.call("Page.getNavigationHistory")
        entries = history.get("entries", [])
        index = history.get("currentIndex", -1)

        def entry_at(position):
            if not 0 <= position < len(entries):
                return None
            entry = entries[position]
            return {key: entry.get(key) for key in ("id", "url", "title")}

        return {"current_index": index, "current": entry_at(index), "previous": entry_at(index - 1)}

    def navigation_context(self):
        targets = cdp("Target.getTargets").get("targetInfos", [])
        tabs = sorted(
            [tab_identity(target) for target in targets if selectable_tab(target)],
            key=lambda tab: (tab["target_id"] != self.target, tab["target_id"]),
        )
        return {
            "tabs": tabs[:50],
            "omitted_tabs": max(0, len(tabs) - 50),
            "history": self.navigation_history(),
            "navigation_urls": list(self.navigation_urls),
        }

    def navigation_actions(self, context):
        actions = [
            {"id": f"open_url:{index}", "kind": "open_url", "label": f"Open {url} in a new tab", "url": url}
            for index, url in enumerate(context["navigation_urls"], 1)
        ]
        for tab in context["tabs"]:
            if tab["target_id"] == self.target:
                continue
            actions.append(
                {
                    "id": f"switch_tab:{tab['target_id']}",
                    "kind": "switch_tab",
                    "label": f"Switch to {tab['title'] or tab['url']} — {tab['url']}",
                    **tab,
                }
            )
        previous = context["history"]["previous"]
        if previous and type(previous["id"]) is int and selectable_tab({"type": "page", "url": previous["url"]}):
            actions.append(
                {
                    "id": "back",
                    "kind": "back",
                    "label": f"Go back to {previous['title'] or previous['url']}",
                    "entry_id": previous["id"],
                    "url": previous["url"],
                    "title": previous["title"],
                }
            )
        return actions

    def wait_for_navigation(self):
        pending = self._pending_navigation
        if pending is None:
            return
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                current = self.evaluate(NAVIGATION_READY)
            except StalePage:
                current = None
            if current and current.get("ready") in {"interactive", "complete"}:
                url = current.get("url", "")
                reached = url == pending["url"] or url != pending["source_url"] or pending["target_changed"]
                if reached and urlparse(url).scheme in {"http", "https"}:
                    self._pending_navigation = None
                    return
            time.sleep(0.05)
        raise RuntimeError("The destination is still loading. Navigation was not repeated; inspect the selected tab.")

    def navigate(self, action, page):
        """Execute one observed browser action; preserve every tab and never replay."""
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ActionPaused("Paused before browser navigation.")
        kind = action["kind"]
        try:
            if kind == "back":
                self.call("Page.navigateToHistoryEntry", entryId=action["entry_id"])
            else:
                target = action.get("target_id")
                if kind == "open_url":
                    target = cdp("Target.createTarget", url=action["url"], background=False)["targetId"]
                new_session = cdp("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
                previous_session = self.session
                self.target, self.session = target, new_session
                try:
                    cdp("Target.detachFromTarget", sessionId=previous_session)
                except RuntimeError:
                    pass  # The previous target is preserved even when detach is not confirmed.
                if kind == "switch_tab":
                    cdp("Target.activateTarget", targetId=target)
        except Exception as exc:
            raise RuntimeError("Browser navigation was not confirmed. Inspect the tabs before a new task.") from exc
        self.after_input = None
        self._pending_navigation = {
            "url": action["url"],
            "source_url": page["url"],
            "target_changed": kind != "back",
        }
        return {"executed": action["id"]}

    def act(self, action, page, text=None):
        if action not in page["actions"]:
            raise ValueError("Only an action from the observed page may be executed.")
        observation = page.get("observation_id")
        if observation and observation in self._consumed:
            raise RuntimeError("This observation was already used. Observe again before another action.")
        if action["kind"] == "fill" and not isinstance(text, str):
            raise ValueError("A fill action requires supplied text.")
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ActionPaused("Paused before browser input.")
        # Consume before the first possible mutation, including partial or unconfirmed inputs.
        if observation:
            self._consumed.add(observation)
        if action["kind"] == "wait":
            time.sleep(0.1)
        try:
            result = (
                self.navigate(action, page)
                if action["kind"] in {"open_url", "switch_tab", "back"}
                else browser_operation(
                    {
                        "operation": "act",
                        "session": self.session,
                        "action": action,
                        "text": text,
                        "cancel_event": self.cancel_event,
                    }
                )
            )
        except ActionPaused:
            # ActionPaused can only be raised before the first mutation, so a new
            # decision after Resume may still use this unchanged observation.
            if observation:
                self._consumed.discard(observation)
            raise
        self.after_input = action if action["kind"] in {"click", "fill", "select", "scroll"} else None
        return result

    def close(self):
        session, self.session = self.session, None
        self.target = None
        if session:
            try:
                cdp("Target.detachFromTarget", sessionId=session)
            except RuntimeError:
                pass  # A closed/disconnected tab is already detached.


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    content.update({key: state[key] for key in ("target_id", "browser_context") if key in state})
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def tab_identity(target):
    return {"target_id": target["targetId"], "url": target.get("url", ""), "title": target.get("title", "")}


def validate_navigation_urls(urls):
    if urls is None:
        return []
    if not isinstance(urls, (list, tuple)) or len(urls) > 16:
        raise ValueError("Supply at most 16 exact navigation URLs.")
    result = []
    for url in urls:
        if not isinstance(url, str) or len(url) > 2000 or any(character.isspace() for character in url):
            raise ValueError("Navigation URLs must be exact http:// or https:// destinations.")
        parsed = urlparse(url)
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or "\\" in url
            or not selectable_tab({"type": "page", "url": url})
        ):
            raise ValueError("Navigation URLs must be ordinary web destinations outside the CTRL console.")
        if url not in result:
            result.append(url)
    return result


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def before_mutation():
        cancel = request.get("cancel_event")
        if cancel is not None and cancel.is_set():
            raise ActionPaused("Paused before browser input.")

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def evaluate(expression):
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        before_mutation()
        if kind not in {"scroll", "wait", "click", "fill", "select"}:
            raise ValueError("Unsupported browser operation")
        if kind == "scroll":
            viewport = evaluate("({w:innerWidth,h:innerHeight})")
            if not viewport or viewport["w"] <= 0 or viewport["h"] <= 0:
                raise StalePage("Page viewport is not available")
            before_mutation()
            call(
                "Input.dispatchMouseEvent",
                type="mouseWheel",
                x=viewport["w"] / 2,
                y=viewport["h"] / 2,
                deltaX=0,
                deltaY=action["delta"],
            )
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            if kind == "select":
                before_mutation()  # This evaluation includes the select's first mutation.
            target = evaluate(
                """(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              if (!e.contains(document.elementFromPoint(x,y))) return null;
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              return {x,y};
            })("""
                + json.dumps(action)
                + ")"
            )
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                x, y = target["x"], target["y"]
                before_mutation()
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    try:
                        focused = evaluate(
                            "(() => { const e=window.__jevFast?.nodes.get("
                            + str(action["node"])
                            + "); return !!e && e.isConnected && "
                            "(e===document.activeElement || e.contains(document.activeElement)) && "
                            "!e.readOnly && !e.matches(':disabled') && "
                            "e.getAttribute('aria-readonly')!=='true'; })()"
                        )
                    except StalePage as exc:
                        raise RuntimeError("Field focus changed after clicking; inspect before a new action.") from exc
                    if not focused:
                        raise RuntimeError("The observed field did not receive focus; no text was inserted.")
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                        commands=["selectAll"],
                    )
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                    )
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info
