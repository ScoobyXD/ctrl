"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from ctrl.browser_connection import cdp, connect, selectable_tab, status
from ctrl.browser_scope import host_allowed, scope_host

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text(encoding="utf-8").rstrip().rstrip(";")
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"
NAVIGATION_READY = "({ready:document.readyState,url:location.href})"
OUTSIDE_SCOPE = {"__ctrl_outside_scope": True}


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class ActionPaused(RuntimeError):
    """Pause stopped undispatched input or an interruptible result observation."""


class OutsideTaskScope(StalePage):
    """The document changed to an origin outside this task before it was read."""


class Browser:
    def __init__(self, url=None, *, target_id=None, navigation_urls=None):
        destinations = validate_navigation_urls(navigation_urls)
        if (url and target_id) or (not url and not target_id and not destinations):
            raise ValueError("Select an existing tab or supply a URL for a new tab.")
        if not status()["connected"] and not connect(wait=2)["connected"]:
            raise RuntimeError(status()["message"])
        self.target = None
        self.session = None
        self._closed = False
        self._consumed = set()
        self.cancel_event = None
        self._pending_navigation = None
        self._selected_start_host = None
        self._has_acted = False
        self.set_navigation_urls(destinations)
        if target_id:
            info = cdp("Target.getTargetInfo", targetId=target_id)["targetInfo"]
            if not selectable_tab(info):
                raise ValueError("Select an ordinary web page, not an internal Chrome or CTRL tab.")
            self.target = target_id
            self._selected_start_host = scope_host(info["url"])
        elif url:
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("Use an http:// or https:// URL for a new tab.")
            if not selectable_tab({"type": "page", "url": url}):
                raise ValueError("The agent cannot control its own CTRL console.")
            self.target = cdp("Target.createTarget", url=url, background=True)["targetId"]
            self._selected_start_host = scope_host(url)
        if self.target:
            self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        if url and self.in_scope(url):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    if self.evaluate("document.readyState") in {"interactive", "complete"}:
                        break
                except StalePage:
                    pass
                time.sleep(0.05)

    def call(self, method, **params):
        if self._closed or self.session is None:
            raise RuntimeError("No browser tab is attached.")
        return cdp(method, session_id=self.session, **params)

    def set_navigation_urls(self, urls):
        self.navigation_urls = validate_navigation_urls(urls)

    @property
    def allowed_hosts(self):
        hosts = {scope_host(url) for url in self.navigation_urls}
        return sorted(hosts or ({self._selected_start_host} if self._selected_start_host else set()))

    def in_scope(self, url):
        return selectable_tab({"type": "page", "url": url}) and host_allowed(url, self.allowed_hosts)

    def target_info(self):
        if self._closed:
            raise RuntimeError("This browser session is closed. Start a new task.")
        if self.target is None:
            return {"targetId": None, "type": "page", "url": "", "title": "Choose a task destination"}
        return cdp("Target.getTargetInfo", targetId=self.target)["targetInfo"]

    def scope_info(self, outside=False):
        return {
            "requested_urls": list(self.navigation_urls),
            "allowed_hosts": self.allowed_hosts,
            "mode": "requested_sites" if self.navigation_urls else "selected_tab",
            "outside_scope": outside,
        }

    def evaluate(self, expression, *, response_timeout=None):
        response = self.call(
            "Runtime.evaluate",
            expression=scoped_expression(expression, self.allowed_hosts),
            returnByValue=True,
            **({"_response_timeout": response_timeout} if response_timeout is not None else {}),
        )
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        value = response.get("result", {}).get("value")
        if value == OUTSIDE_SCOPE:
            raise OutsideTaskScope("The document left this task's allowed sites.")
        return value

    def activate_for_input(self):
        """Restore only the selected minimized window and wait for real rendering."""

        def check_cancel():
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise ActionPaused("Paused during tab activation and before page input.")

        check_cancel()
        window = cdp("Browser.getWindowForTarget", targetId=self.target, _response_timeout=1.0)
        check_cancel()
        if window.get("bounds", {}).get("windowState") == "minimized":
            cdp(
                "Browser.setWindowBounds",
                windowId=window["windowId"],
                bounds={"windowState": "normal"},
                _response_timeout=1.0,
            )
            check_cancel()
        self.call("Page.bringToFront", _response_timeout=1.0)
        check_cancel()
        deadline = time.monotonic() + 2.0
        error_message = (
            "Chrome's selected tab did not become visible, focused, and ready to render. "
            "Restore or uncover that Chrome window on the current desktop, then start the task again. "
            "No page input was sent for this action."
        )
        expression = activation_readiness_expression(uuid.uuid4().hex)
        while True:
            check_cancel()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(error_message)
            try:
                current = self.evaluate(expression, response_timeout=min(0.5, remaining))
            except RuntimeError as error:
                check_cancel()
                raise RuntimeError(error_message) from error
            check_cancel()
            if (
                isinstance(current, dict)
                and current.get("visible")
                and current.get("focused")
                and current.get("frames", 0) >= 2
            ):
                return
            if self.cancel_event is not None:
                self.cancel_event.wait(min(0.05, remaining))
            else:
                time.sleep(min(0.05, remaining))

    def outside_scope_page(self, info):
        self.after_input = None
        self._pending_navigation = None
        blocked = self._has_acted or not self.navigation_urls
        message = (
            "The destination left this task's allowed sites. Inspect Chrome or sign in, then restart the task."
            if blocked
            else "Choose one of the task's requested destinations. The unrelated starting page was not read."
        )
        context = self.navigation_context(info)
        page = {
            "url": "",
            "title": "Task scope boundary" if blocked else "Choose a task destination",
            "text": message,
            "w": 0,
            "h": 0,
            "scroll": {"y": 0, "height": 0},
            "actions": [] if blocked else self.navigation_actions(context),
            "marker": metadata_guard(info),
            "page_key": [],
            "guards": {},
            "omitted_actions": 0,
            "target_id": self.target,
            "browser_context": context,
            "task_browser_scope": self.scope_info(True),
            "bootstrap": not blocked,
            "observation_id": uuid.uuid4().hex,
        }
        if blocked:
            page["scope_blocked"] = message
        page["fingerprint"] = fingerprint(page)
        return page

    def observe(self, screenshot=True):
        if self._has_acted and self.cancel_event is not None and self.cancel_event.is_set():
            raise ActionPaused("Paused before reading the action result.")
        self.wait_for_navigation()
        info = self.target_info()
        if not self.in_scope(info.get("url", "")):
            return self.outside_scope_page(info)
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression=scoped_expression(
                        """(action => new Promise(resolve => {
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
                        self.allowed_hosts,
                    ),
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        for attempt in range(10):
            try:
                page = browser_operation(
                    {
                        "operation": "observe",
                        "session": self.session,
                        "screenshot": screenshot,
                        "allowed_hosts": self.allowed_hosts,
                    }
                )
                info = self.target_info()
                if not self.in_scope(info.get("url", "")):
                    return self.outside_scope_page(info)
                page["target_id"] = self.target
                page["browser_context"] = self.navigation_context(info)
                page["task_browser_scope"] = self.scope_info()
                page["actions"].extend(self.navigation_actions(page["browser_context"]))
                page["fingerprint"] = fingerprint(page)
                page["observation_id"] = uuid.uuid4().hex
                return page
            except OutsideTaskScope:
                info = self.target_info()
                if attempt < 9 and self.in_scope(info.get("url", "")):
                    # A history restore can replace the document between metadata
                    # and evaluation. Retry only guarded reads while metadata stays
                    # in scope; never inspect the rejected document or replay input.
                    if self.cancel_event is not None and self.cancel_event.is_set():
                        raise ActionPaused("Paused while the document changes.")
                    time.sleep(0.02)
                    continue
                return self.outside_scope_page(info)
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        if page.get("target_id", self.target) != self.target:
            return False
        kind = action.get("kind") if action else None
        info = self.target_info()
        if page.get("bootstrap") or page.get("scope_blocked"):
            if self.in_scope(info.get("url", "")) or page["marker"] != metadata_guard(info):
                return False
            if page.get("scope_blocked") or kind not in {None, "open_url", "switch_tab"}:
                return False
            if action is None:
                return self.navigation_context(info) == page["browser_context"]
        elif not self.in_scope(info.get("url", "")):
            return False
        if kind in {"open_url", "switch_tab", "back"}:
            if not page.get("bootstrap"):
                try:
                    if self.evaluate(MARKER) != page["marker"]:
                        return False
                except OutsideTaskScope:
                    return False
            if kind == "open_url":
                return action["url"] in self.navigation_urls
            if kind == "switch_tab":
                try:
                    info = cdp("Target.getTargetInfo", targetId=action["target_id"])["targetInfo"]
                except RuntimeError:
                    return False
                return self.in_scope(info.get("url", "")) and tab_identity(info) == {
                    "target_id": action["target_id"],
                    "url": action["url"],
                    "title": action["title"],
                }
            return self.in_scope(action["url"]) and self.navigation_history() == page["browser_context"]["history"]
        if action is None and "browser_context" in page and self.navigation_context() != page["browser_context"]:
            return False
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            try:
                current = self.evaluate(
                    "(() => { const c=window.__jevFast; "
                    f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
                )
            except OutsideTaskScope:
                return False
            return current == [page["page_key"], page["guards"].get(str(node))]
        try:
            return self.evaluate(MARKER) == page["marker"]
        except OutsideTaskScope:
            return False

    def navigation_history(self):
        history = self.call("Page.getNavigationHistory")
        entries = history.get("entries", [])
        index = history.get("currentIndex", -1)

        def entry_at(position):
            if not 0 <= position < len(entries):
                return None
            entry = entries[position]
            if not self.in_scope(entry.get("url", "")):
                return None
            return {key: entry.get(key) for key in ("id", "url", "title")}

        return {"current_index": index, "current": entry_at(index), "previous": entry_at(index - 1)}

    def navigation_context(self, source=None):
        source = source or self.target_info()
        targets = cdp("Target.getTargets").get("targetInfos", [])
        tabs = sorted(
            [
                tab_identity(target)
                for target in targets
                if selectable_tab(target) and self.in_scope(target.get("url", ""))
            ],
            key=lambda tab: (tab["target_id"] != self.target, tab["target_id"]),
        )
        return {
            "tabs": tabs[:50],
            "omitted_tabs": max(0, len(tabs) - 50),
            "history": self.navigation_history()
            if self.in_scope(source.get("url", ""))
            else {"current_index": -1, "current": None, "previous": None},
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
        if previous and type(previous["id"]) is int and self.in_scope(previous["url"]):
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
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise ActionPaused("Paused while the destination loads.")
            info = self.target_info()
            if not self.in_scope(info.get("url", "")):
                if urlparse(info.get("url", "")).scheme in {"http", "https"}:
                    self._pending_navigation = None
                    return  # observe() publishes only the neutral scope boundary.
                time.sleep(0.05)
                continue
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
                    target = cdp("Target.createTarget", url=action["url"], background=True)["targetId"]
                new_session = cdp("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
                previous_session = self.session
                self.target, self.session = target, new_session
                if previous_session:
                    try:
                        cdp("Target.detachFromTarget", sessionId=previous_session)
                    except RuntimeError:
                        pass  # The previous target is preserved even when detach is not confirmed.
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
        if action["kind"] in {"click", "fill", "select", "scroll"}:
            # Activation is input preflight. Visibility/focus handlers may alter
            # this same observed node, so revalidate identity after activation.
            self.activate_for_input()
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise ActionPaused("Paused after tab activation and before page input.")
            if not self.fresh(page, action):
                raise StalePage("The page changed when activated. Observe and choose again.")
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise ActionPaused("Paused before page input.")
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
                        "allowed_hosts": self.allowed_hosts,
                    }
                )
            )
        except ActionPaused:
            # ActionPaused can only be raised before the first page input, so a new
            # decision after Resume may still use this unchanged observation.
            if observation:
                self._consumed.discard(observation)
            raise
        self.after_input = action if action["kind"] in {"click", "fill", "select", "scroll"} else None
        self._has_acted = self._has_acted or action["kind"] != "wait"
        return result

    def close(self):
        self._closed = True
        session, self.session = self.session, None
        self.target = None
        if session:
            try:
                cdp("Target.detachFromTarget", sessionId=session)
            except RuntimeError:
                pass  # A closed/disconnected tab is already detached.


def activation_readiness_expression(token):
    # Never await rAF: hidden documents may never receive a frame. The host polls
    # with a deadline and cancellation. At most one harmless callback is pending.
    return """(() => {
      const token=TOKEN, visible=document.visibilityState==='visible', focused=document.hasFocus();
      let state=window.__ctrlActivationReadiness;
      if (!state || state.token!==token)
        state=window.__ctrlActivationReadiness={token,frames:0,pending:false};
      if (!visible || !focused) state.frames=0;
      if (visible && focused && state.frames<2 && !state.pending) {
        state.pending=true;
        requestAnimationFrame(()=>{
          if (window.__ctrlActivationReadiness!==state) return;
          state.pending=false;
          state.frames=document.visibilityState==='visible' && document.hasFocus() ? state.frames+1 : 0;
        });
      }
      return {visible,focused,frames:state.frames};
    })()""".replace("TOKEN", json.dumps(token))


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    content.update({key: state[key] for key in ("target_id", "browser_context") if key in state})
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def tab_identity(target):
    return {"target_id": target["targetId"], "url": target.get("url", ""), "title": target.get("title", "")}


def metadata_guard(info):
    # Never copy unrelated target URLs/titles into model-visible bootstrap state.
    return hashlib.sha256(json.dumps(tab_identity(info), sort_keys=True).encode()).hexdigest()


def scoped_expression(expression, allowed_hosts):
    if allowed_hosts is None:
        return expression
    return (
        "(() => { const rawHost=location.hostname.toLowerCase().replace(/^\\[|\\]$/g,'').replace(/\\.$/,''); "
        "const local=rawHost==='localhost' || rawHost.endsWith('.localhost') || rawHost==='::1' || "
        "/^127(?:\\.\\d{1,3}){3}$/.test(rawHost) || /^::ffff:7f[0-9a-f]{2}:[0-9a-f]{1,4}$/.test(rawHost); "
        "const host=local ? rawHost : rawHost.replace(/^www\\./,''); "
        "const port=location.port || (location.protocol==='https:' ? '443' : '80'); "
        "const key=local ? (host.includes(':') ? '['+host+']' : host)+':'+port : host; "
        f"const ownConsole=local && {json.dumps(['8766', '8767', os.environ.get('CTRL_PORT', '8766')])}"
        ".includes(port); "
        f"if (ownConsole || !{json.dumps(allowed_hosts)}.some(base=>local ? key===base : "
        "!base.includes(':') && (host===base || host.endsWith('.'+base)))) "
        f"return {json.dumps(OUTSIDE_SCOPE)}; "
        "return (" + expression + "); })()"
    )


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
        result = call(
            "Runtime.evaluate",
            expression=scoped_expression(expression, request.get("allowed_hosts")),
            returnByValue=True,
        )
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        value = result.get("result", {}).get("value")
        if value == OUTSIDE_SCOPE:
            raise OutsideTaskScope("The document left this task's allowed sites.")
        return value

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
